#!/usr/bin/env python3
"""Settle MLB O/U picks and keep public/internal record summaries in sync."""

from __future__ import annotations

import json
import math
import csv
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path

from db import Database
from util import validate_date

SCRIPT_DIR = Path(__file__).parent
PROJECT_DIR = SCRIPT_DIR.parent
DATA_DIR = SCRIPT_DIR / "data"
PERFORMANCE_PATH = DATA_DIR / "performance.json"
README_PATH = PROJECT_DIR / "README.md"

RECORD_START = "<!-- SPORTSBOTV2_RECORD_START -->"
RECORD_END = "<!-- SPORTSBOTV2_RECORD_END -->"
BASELINE_THROUGH = "2026-05-04"

BASELINE_DAILY = [
    {"date": "2026-04-23", "label": "Apr 23", "wins": 1, "losses": 3, "pushes": 0},
    {"date": "2026-04-24", "label": "Apr 24", "wins": 5, "losses": 3, "pushes": 0},
    {"date": "2026-04-25", "label": "Apr 25", "wins": 4, "losses": 2, "pushes": 0},
    {"date": "2026-04-26", "label": "Apr 26", "wins": 6, "losses": 5, "pushes": 0},
    {"date": "2026-04-27", "label": "Apr 27", "wins": 1, "losses": 1, "pushes": 0},
    {"date": "2026-04-28", "label": "Apr 28", "wins": 7, "losses": 2, "pushes": 0},
    {"date": "2026-04-29", "label": "Apr 29", "wins": 4, "losses": 2, "pushes": 1},
    {"date": "2026-04-30", "label": "Apr 30", "wins": 5, "losses": 0, "pushes": 1},
    {"date": "2026-05-01", "label": "May 1", "wins": 8, "losses": 1, "pushes": 0},
    {"date": "2026-05-02", "label": "May 2", "wins": 7, "losses": 4, "pushes": 1},
    {"date": "2026-05-03", "label": "May 3", "wins": 3, "losses": 4, "pushes": 0},
    {"date": "2026-05-04", "label": "May 4", "wins": 2, "losses": 2, "pushes": 0},
]

BASELINE_BY_PICK_TYPE = {
    "OVER": {"wins": 27, "losses": 15, "pushes": 0},
    "UNDER": {"wins": 26, "losses": 14, "pushes": 0},
}

BASELINE_BY_CONFIDENCE = {
    "Low": {"wins": 16, "losses": 9, "pushes": 0},
    "Medium": {"wins": 28, "losses": 14, "pushes": 1},
    "High": {"wins": 9, "losses": 6, "pushes": 2},
}


def empty_counter():
    return {"wins": 0, "losses": 0, "pushes": 0}


def add_counter(target, source):
    target["wins"] += int(source.get("wins", 0))
    target["losses"] += int(source.get("losses", 0))
    target["pushes"] += int(source.get("pushes", 0))


def record_string(counter):
    wins = int(counter.get("wins", 0))
    losses = int(counter.get("losses", 0))
    pushes = int(counter.get("pushes", 0))
    if pushes:
        return f"{wins}-{losses}-{pushes}"
    return f"{wins}-{losses}"


def picks_count(counter):
    return int(counter.get("wins", 0)) + int(counter.get("losses", 0)) + int(counter.get("pushes", 0))


def win_pct(counter):
    wins = int(counter.get("wins", 0))
    losses = int(counter.get("losses", 0))
    decided = wins + losses
    if decided == 0:
        return 0.0
    return wins / decided * 100


def units(counter):
    return round(float(counter.get("wins", 0)) - float(counter.get("losses", 0)) * 1.1, 1)


def roi_pct(counter):
    decided = int(counter.get("wins", 0)) + int(counter.get("losses", 0))
    if decided == 0:
        return 0.0
    return units(counter) / decided * 100


def summary_counter(counter):
    enriched = {
        "wins": int(counter.get("wins", 0)),
        "losses": int(counter.get("losses", 0)),
        "pushes": int(counter.get("pushes", 0)),
    }
    enriched["record"] = record_string(enriched)
    enriched["picks"] = picks_count(enriched)
    enriched["win_pct"] = round(win_pct(enriched), 1)
    return enriched


def empty_performance(baseline=True):
    performance = {
        "version": 1,
        "baseline_through": BASELINE_THROUGH if baseline else None,
        "manual_baseline": {
            "daily": deepcopy(BASELINE_DAILY) if baseline else [],
            "by_pick_type": deepcopy(BASELINE_BY_PICK_TYPE) if baseline else {},
            "by_confidence": deepcopy(BASELINE_BY_CONFIDENCE) if baseline else {},
        },
        "settled_picks": [],
        "last_updated": None,
    }
    return recompute_summary(performance)


def load_performance(path=PERFORMANCE_PATH):
    path = Path(path)
    if not path.exists():
        return empty_performance()
    with path.open() as f:
        performance = json.load(f)
    return recompute_summary(performance)


def save_performance(performance, path=PERFORMANCE_PATH):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    performance = recompute_summary(performance)
    performance["last_updated"] = datetime.now(timezone.utc).isoformat()
    with path.open("w") as f:
        json.dump(performance, f, indent=2)
        f.write("\n")
    return performance


def date_label(date_str):
    validate_date(date_str)
    dt = datetime.strptime(date_str, "%Y-%m-%d")
    return f"{dt.strftime('%b')} {dt.day}"


def normalize_confidence(pick):
    value = str(pick.get("confidence") or pick.get("conf") or "").strip()
    lowered = value.lower()
    if lowered in {"low", "medium", "high"}:
        return lowered.title()
    if "★★★" in value or value.count("★") >= 3:
        return "High"
    if "★★" in value or value.count("★") == 2:
        return "Medium"
    if "★" in value or value.count("★") == 1:
        return "Low"

    edge = pick.get("edge")
    try:
        abs_edge = abs(float(edge))
    except (TypeError, ValueError):
        return "Low"
    if abs_edge >= 1.5:
        return "High"
    if abs_edge >= 0.8:
        return "Medium"
    return "Low"


def is_actionable_pick(pick):
    return str(pick.get("pick", "")).upper() in {"OVER", "UNDER"} and pick.get("odds") is not None


def clean_number(value):
    if value is None:
        return None
    try:
        value = float(value)
    except (TypeError, ValueError):
        return None
    if math.isnan(value):
        return None
    if value.is_integer():
        return int(value)
    return value


def find_game_for_pick(pick, games, used_indexes):
    game_pk = pick.get("game_pk")
    if game_pk is not None:
        for idx, game in enumerate(games):
            if idx in used_indexes:
                continue
            if str(game.get("game_pk")) == str(game_pk):
                return idx, game

    for idx, game in enumerate(games):
        if idx in used_indexes:
            continue
        if game.get("away_team") == pick.get("away") and game.get("home_team") == pick.get("home"):
            return idx, game
    return None, None


def settle_pick(date_str, pick, game):
    line = clean_number(pick.get("odds") if pick.get("odds") is not None else pick.get("line"))
    actual_total = clean_number(game.get("total_runs"))
    if line is None or actual_total is None:
        return None

    direction = str(pick.get("pick", "")).upper()
    if direction == "OVER":
        result = "WIN" if actual_total > line else "LOSS" if actual_total < line else "PUSH"
    elif direction == "UNDER":
        result = "WIN" if actual_total < line else "LOSS" if actual_total > line else "PUSH"
    else:
        return None

    return {
        "date": date_str,
        "game_pk": game.get("game_pk"),
        "away": pick.get("away"),
        "home": pick.get("home"),
        "pick": direction,
        "line": line,
        "model_total": clean_number(pick.get("pred") if pick.get("pred") is not None else pick.get("projected")),
        "edge": clean_number(pick.get("edge")),
        "confidence": normalize_confidence(pick),
        "away_score": clean_number(game.get("away_score")),
        "home_score": clean_number(game.get("home_score")),
        "actual_total": actual_total,
        "result": result,
        "settled_at": datetime.now(timezone.utc).isoformat(),
    }


def settle_picks(date_str, picks, games):
    settled = []
    used_indexes = set()
    for pick in picks:
        if not is_actionable_pick(pick):
            continue
        idx, game = find_game_for_pick(pick, games, used_indexes)
        if game is None:
            continue
        record = settle_pick(date_str, pick, game)
        if record is None:
            continue
        used_indexes.add(idx)
        settled.append(record)
    return settled


def settled_key(record):
    if record.get("game_pk") is not None:
        return f"{record['date']}:{record['game_pk']}:{record['pick']}:{record['line']}"
    return f"{record['date']}:{record['away']}:{record['home']}:{record['pick']}:{record['line']}"


def apply_settled_date(performance, date_str, picks, games):
    performance = deepcopy(performance)
    existing = {settled_key(record): record for record in performance.get("settled_picks", [])}
    for record in settle_picks(date_str, picks, games):
        existing[settled_key(record)] = record
    performance["settled_picks"] = sorted(existing.values(), key=lambda r: (r["date"], str(r.get("game_pk") or ""), r["away"], r["home"], r["pick"]))
    return recompute_summary(performance)


def counter_from_records(records):
    counter = empty_counter()
    for record in records:
        result = record.get("result")
        if result == "WIN":
            counter["wins"] += 1
        elif result == "LOSS":
            counter["losses"] += 1
        elif result == "PUSH":
            counter["pushes"] += 1
    return counter


def recompute_summary(performance):
    manual = performance.get("manual_baseline", {})
    daily = {}
    overall = empty_counter()
    for row in manual.get("daily", []):
        counter = {k: int(row.get(k, 0)) for k in ("wins", "losses", "pushes")}
        daily[row["date"]] = {
            "date": row["date"],
            "label": row.get("label") or date_label(row["date"]),
            **summary_counter(counter),
        }
        add_counter(overall, counter)

    baseline_through = performance.get("baseline_through")
    settled_for_summary = []
    for record in performance.get("settled_picks", []):
        if baseline_through and record.get("date") <= baseline_through:
            continue
        settled_for_summary.append(record)

    grouped_by_date = {}
    for record in settled_for_summary:
        grouped_by_date.setdefault(record["date"], []).append(record)
    for date_str, records in grouped_by_date.items():
        counter = counter_from_records(records)
        daily[date_str] = {
            "date": date_str,
            "label": date_label(date_str),
            **summary_counter(counter),
        }
        add_counter(overall, counter)

    by_pick_type = {key: summary_counter(value) for key, value in manual.get("by_pick_type", {}).items()}
    by_confidence = {key: summary_counter(value) for key, value in manual.get("by_confidence", {}).items()}

    for record in settled_for_summary:
        type_key = record["pick"]
        by_pick_type.setdefault(type_key, summary_counter(empty_counter()))
        conf_key = record.get("confidence", "Low")
        by_confidence.setdefault(conf_key, summary_counter(empty_counter()))

        for bucket in (by_pick_type[type_key], by_confidence[conf_key]):
            if record["result"] == "WIN":
                bucket["wins"] += 1
            elif record["result"] == "LOSS":
                bucket["losses"] += 1
            elif record["result"] == "PUSH":
                bucket["pushes"] += 1

    by_pick_type = {key: summary_counter(value) for key, value in by_pick_type.items()}
    by_confidence = {key: summary_counter(value) for key, value in by_confidence.items()}

    performance["daily"] = [daily[key] for key in sorted(daily)]
    performance["by_pick_type"] = by_pick_type
    performance["by_confidence"] = by_confidence
    performance["overall"] = {
        **summary_counter(overall),
        "units": units(overall),
        "roi_pct": round(roi_pct(overall), 1),
    }
    return performance


def load_picks_for_date(date_str):
    path = DATA_DIR / f"picks_{date_str}.json"
    if not path.exists():
        return []
    with path.open() as f:
        payload = json.load(f)
    return payload.get("picks", [])


def load_final_games_for_date(date_str, db_path=None):
    with Database(db_path=db_path) as db:
        return db.query(
            """
            SELECT game_pk, away_team, home_team, away_score, home_score, total_runs
            FROM games
            WHERE date = ? AND status = 'Final' AND total_runs IS NOT NULL
            ORDER BY game_pk
            """,
            [date_str],
        )


def settle_date(date_str, performance_path=PERFORMANCE_PATH, db_path=None):
    date_str = validate_date(date_str)
    performance = load_performance(performance_path)
    picks = load_picks_for_date(date_str)
    games = load_final_games_for_date(date_str, db_path=db_path)
    performance = apply_settled_date(performance, date_str, picks, games)
    return save_performance(performance, performance_path)


def export_settled_picks_csv(performance_path=PERFORMANCE_PATH, out_path=DATA_DIR / "settled_picks.csv"):
    performance = load_performance(performance_path)
    rows = performance.get("settled_picks", [])
    columns = [
        "date",
        "game_pk",
        "away",
        "home",
        "pick",
        "line",
        "model_total",
        "edge",
        "confidence",
        "away_score",
        "home_score",
        "actual_total",
        "result",
        "settled_at",
    ]
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
    return out_path, len(rows)


def export_daily_csv(performance_path=PERFORMANCE_PATH, out_path=DATA_DIR / "daily_performance.csv"):
    performance = load_performance(performance_path)
    rows = performance.get("daily", [])
    columns = ["date", "label", "record", "wins", "losses", "pushes", "picks", "win_pct"]
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
    return out_path, len(rows)


def render_record_block(performance):
    overall = performance["overall"]
    lines = [
        "## Performance Snapshot",
        "",
        RECORD_START,
        "",
        f"**Overall record:** {overall['record']}  ",
        f"**Win rate:** {overall['win_pct']:.1f}%  ",
        f"**ROI:** {overall['units']:+.1f} units / {overall['roi_pct']:+.1f}% at -110 juice",
        "",
        "## Full Record By Day",
        "",
        "| Date | Record | Win % | Picks |",
        "| --- | --- | ---: | ---: |",
    ]
    for row in performance.get("daily", []):
        lines.append(f"| {row['label']} | {row['record']} | {row['win_pct']:.1f}% | {row['picks']} |")

    lines.extend([
        "",
        "## Record By Pick Type",
        "",
        "| Pick Type | Record | Win % |",
        "| --- | --- | ---: |",
    ])
    for pick_type in ("OVER", "UNDER"):
        row = performance.get("by_pick_type", {}).get(pick_type, summary_counter(empty_counter()))
        label = "Over" if pick_type == "OVER" else "Under"
        lines.append(f"| {label} | {row['record']} | {row['win_pct']:.1f}% |")

    lines.extend([
        "",
        "## Record By Confidence",
        "",
        "| Confidence | Record | Win % |",
        "| --- | --- | ---: |",
    ])
    for confidence in ("Low", "Medium", "High"):
        row = performance.get("by_confidence", {}).get(confidence, summary_counter(empty_counter()))
        lines.append(f"| {confidence} | {row['record']} | {row['win_pct']:.1f}% |")

    lines.extend(["", RECORD_END, ""])
    return "\n".join(lines)


def update_readme_text(text, performance):
    block = render_record_block(performance)
    if RECORD_START in text and RECORD_END in text:
        start = text.index("## Performance Snapshot")
        end = text.index(RECORD_END) + len(RECORD_END)
        return text[:start] + block.rstrip() + text[end:]

    start_marker = "## Performance Snapshot"
    next_marker = "## What The Model Does"
    if start_marker not in text or next_marker not in text:
        raise ValueError("README must contain Performance Snapshot and What The Model Does sections")
    start = text.index(start_marker)
    end = text.index(next_marker)
    return text[:start] + block + "\n" + text[end:]


def update_readme(performance, readme_path=README_PATH):
    path = Path(readme_path)
    updated = update_readme_text(path.read_text(), performance)
    path.write_text(updated)
    return updated


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Settle MLB O/U picks and update README stats")
    parser.add_argument("command_or_date", nargs="?", help="Date to settle, or export-picks/export-daily")
    parser.add_argument("date", nargs="?", help="Date to settle when using the settle command")
    parser.add_argument("--performance-path", default=str(PERFORMANCE_PATH))
    parser.add_argument("--readme-path", default=str(README_PATH))
    parser.add_argument("--out")
    args = parser.parse_args()

    command = args.command_or_date
    if command == "export-picks":
        out = args.out or DATA_DIR / "settled_picks.csv"
        path, count = export_settled_picks_csv(Path(args.performance_path), Path(out))
        print(f"Exported {count} settled picks to {path}")
    elif command == "export-daily":
        out = args.out or DATA_DIR / "daily_performance.csv"
        path, count = export_daily_csv(Path(args.performance_path), Path(out))
        print(f"Exported {count} daily rows to {path}")
    else:
        if command == "settle":
            date_arg = args.date
        else:
            date_arg = command
        if not date_arg:
            parser.error("date is required unless using export-picks or export-daily")
        perf = settle_date(date_arg, Path(args.performance_path))
        update_readme(perf, Path(args.readme_path))
        print(f"Settled {date_arg}: {perf['overall']['record']} ({perf['overall']['win_pct']:.1f}%)")
