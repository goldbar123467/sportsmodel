#!/usr/bin/env python3
"""Run the full daily MLB O/U bot cycle and send the Telegram card."""

from __future__ import annotations

import argparse
import json
import os
import shlex
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import recordkeeping

SCRIPT_DIR = Path(__file__).parent
PROJECT_DIR = SCRIPT_DIR.parent
DEFAULT_TZ = "America/New_York"
DEFAULT_OPENCLAW = Path("/home/clark/code/openclaw/dist/index.js")


def load_env(path=PROJECT_DIR / ".env"):
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip())


def local_dates(date_str=None, local_tz=DEFAULT_TZ):
    if date_str:
        today = datetime.strptime(date_str, "%Y-%m-%d").date()
    else:
        today = datetime.now(ZoneInfo(local_tz)).date()
    return today.isoformat(), (today - timedelta(days=1)).isoformat()


def run_step(label, cmd, cwd=SCRIPT_DIR):
    print(f"{datetime.now().isoformat(timespec='seconds')}: {label}")
    subprocess.run(cmd, cwd=cwd, check=True)


def read_json(path, default):
    path = Path(path)
    if not path.exists():
        return default
    with path.open() as f:
        return json.load(f)


def current_openclaw_target():
    if os.environ.get("OPENCLAW_TELEGRAM_TARGET"):
        return os.environ["OPENCLAW_TELEGRAM_TARGET"]

    config_path = Path(os.environ.get("OPENCLAW_CONFIG_PATH", Path.home() / ".openclaw/openclaw.json"))
    try:
        config = read_json(config_path, {})
    except json.JSONDecodeError:
        return None
    for target in config.get("commands", {}).get("ownerAllowFrom", []):
        if str(target).startswith("telegram:"):
            return str(target).split(":", 1)[1]
    return None


def openclaw_command():
    if os.environ.get("OPENCLAW_CMD"):
        return shlex.split(os.environ["OPENCLAW_CMD"])
    if os.environ.get("OPENCLAW_BIN"):
        return [os.environ["OPENCLAW_BIN"]]
    return [os.environ.get("NODE_BIN", "node"), str(DEFAULT_OPENCLAW)]


def send_telegram(message, dry_run=False):
    target = current_openclaw_target()
    if not target:
        print("No OPENCLAW_TELEGRAM_TARGET or ownerAllowFrom Telegram target configured; skipping Telegram send")
        return False

    cmd = openclaw_command() + [
        "message",
        "send",
        "--channel",
        "telegram",
        "--target",
        target,
        "--message",
        message,
    ]
    if dry_run:
        cmd.extend(["--dry-run", "--json"])
    subprocess.run(cmd, cwd=PROJECT_DIR, check=True)
    return True


def actionable_picks(picks):
    return [p for p in picks if str(p.get("pick", "")).upper() in {"OVER", "UNDER"}]


def format_pick_line(pick):
    pick_type = str(pick.get("pick", "")).upper()
    odds = pick.get("odds")
    pred = pick.get("pred")
    edge = pick.get("edge")
    confidence = recordkeeping.normalize_confidence(pick)

    line = f"{pick.get('away')} @ {pick.get('home')} {pick_type}"
    if odds is not None:
        line += f" {float(odds):.1f}"
    details = []
    if pred is not None:
        details.append(f"model {float(pred):.1f}")
    if edge is not None:
        details.append(f"edge {float(edge):+.2f}")
    details.append(confidence)
    return f"{line} | " + " | ".join(details)


def daily_row(performance, date_str):
    for row in performance.get("daily", []):
        if row.get("date") == date_str:
            return row
    return None


def format_telegram_message(today, settle_date, performance, picks):
    overall = performance.get("overall", {})
    settled = daily_row(performance, settle_date)
    plays = actionable_picks(picks)

    lines = [
        "SportsBotv2 MLB O/U Daily Card",
        f"Date: {recordkeeping.date_label(today)}",
        "",
    ]

    if settled:
        lines.append(
            f"Settled {settled['label']}: {settled['record']} "
            f"({settled['win_pct']:.1f}%)"
        )
    else:
        lines.append(f"Settled {recordkeeping.date_label(settle_date)}: no resolved picks")

    lines.append(
        f"Overall: {overall.get('record', '0-0')} "
        f"({overall.get('win_pct', 0.0):.1f}%) | "
        f"ROI {overall.get('units', 0.0):+.1f}u "
        f"({overall.get('roi_pct', 0.0):+.1f}%)"
    )
    lines.append("")

    if plays:
        lines.append("Today's plays:")
        lines.extend(format_pick_line(pick) for pick in plays)
    else:
        lines.append("Today's plays: no qualifying edges at the current threshold.")

    return "\n".join(lines)


def run_daily_cycle(args):
    load_env()
    today, default_settle_date = local_dates(args.date, args.local_tz)
    settle_date = args.settle_date or default_settle_date
    season = str(args.season or today[:4])
    python = sys.executable

    print(f"{datetime.now().isoformat(timespec='seconds')}: === SportsBotv2 daily cycle for {today} ({args.local_tz}) ===")
    print(f"{datetime.now().isoformat(timespec='seconds')}: Settling previous card: {settle_date}")

    if not args.skip_scrape:
        run_step("Scraping previous day finals", [python, "scrape.py", "--date", settle_date, "--season", season])

    performance = recordkeeping.settle_date(settle_date)
    recordkeeping.update_readme(performance)

    if not args.settle_only:
        if not args.skip_scrape:
            run_step("Scraping today's schedule and odds", [python, "scrape.py", "--date", today, "--season", season])
        if not args.skip_train:
            run_step("Retraining XGBoost model", [python, "train.py"])
        run_step("Generating today's picks", [python, "pick_today.py", today])

    picks_payload = read_json(recordkeeping.DATA_DIR / f"picks_{today}.json", {"date": today, "picks": []})
    message = format_telegram_message(today, settle_date, performance, picks_payload.get("picks", []))

    print("\n" + message + "\n")
    if not args.no_telegram:
        send_telegram(message, dry_run=args.dry_run_telegram)

    print(f"{datetime.now().isoformat(timespec='seconds')}: === SportsBotv2 daily cycle complete ===")
    return 0


def main():
    parser = argparse.ArgumentParser(description="Settle yesterday, generate today's MLB O/U picks, and send Telegram")
    parser.add_argument("--date", help="Today's local MLB date (YYYY-MM-DD)")
    parser.add_argument("--settle-date", help="Previous local MLB date to settle (YYYY-MM-DD)")
    parser.add_argument("--season", type=int, help="MLB season year")
    parser.add_argument("--local-tz", default=DEFAULT_TZ)
    parser.add_argument("--skip-scrape", action="store_true")
    parser.add_argument("--skip-train", action="store_true")
    parser.add_argument("--settle-only", action="store_true")
    parser.add_argument("--no-telegram", action="store_true")
    parser.add_argument("--dry-run-telegram", action="store_true")
    args = parser.parse_args()
    raise SystemExit(run_daily_cycle(args))


if __name__ == "__main__":
    main()
