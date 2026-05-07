#!/usr/bin/env python3
"""Operational preflight for the SportsBotv2 XGBoost path."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from db import Database, DB_PATH
import recordkeeping

SCRIPT_DIR = Path(__file__).parent
MODEL_META = SCRIPT_DIR / "model" / "model_metadata.json"
MODEL_FILE = SCRIPT_DIR / "model" / "ou_xgb.json"


def iso_mtime(path: Path):
    if not path.exists():
        return None
    return datetime.fromtimestamp(path.stat().st_mtime, timezone.utc).isoformat()


def as_text(value):
    return None if value is None else str(value)


def run_preflight(db_path=DB_PATH, performance_path=recordkeeping.PERFORMANCE_PATH):
    with Database(db_path=db_path) as db:
        summary = db.get_stats_summary()
        latest_odds = db.query("SELECT MAX(date) as latest FROM odds")[0]["latest"]
        latest_weather = db.query(
            "SELECT MAX(date) as latest FROM games WHERE weather_source IS NOT NULL"
        )[0]["latest"]
        missing_weather = db.query(
            """
            SELECT COUNT(*) as n
            FROM games
            WHERE stadium_roof = false AND weather_source IS NULL
            """
        )[0]["n"]

    performance = recordkeeping.load_performance(performance_path)
    meta = {}
    if MODEL_META.exists():
        with MODEL_META.open() as f:
            meta = json.load(f)
    settled_keys = {
        (r.get("date"), r.get("game_pk"), r.get("pick"), r.get("line"))
        for r in performance.get("settled_picks", [])
    }
    baseline_through = performance.get("baseline_through")
    pending = 0
    for path in sorted((SCRIPT_DIR / "data").glob("picks_*.json")):
        try:
            payload = json.loads(path.read_text())
        except json.JSONDecodeError:
            continue
        date = payload.get("date")
        if baseline_through and date and date <= baseline_through:
            continue
        for pick in payload.get("picks", []):
            if str(pick.get("pick", "")).upper() not in {"OVER", "UNDER"}:
                continue
            line = pick.get("odds") if pick.get("odds") is not None else pick.get("line")
            key = (date, pick.get("game_pk"), str(pick.get("pick", "")).upper(), line)
            if key not in settled_keys:
                pending += 1

    return {
        "database": str(db_path),
        "date_range": {
            "min_date": as_text(summary["date_range"].get("min_date")),
            "max_date": as_text(summary["date_range"].get("max_date")),
        },
        "tables": summary["tables"],
        "latest_odds_date": as_text(latest_odds),
        "latest_weather_date": as_text(latest_weather),
        "outdoor_games_missing_weather": missing_weather,
        "model_file": str(MODEL_FILE),
        "model_updated_at": iso_mtime(MODEL_FILE),
        "model_metadata_updated_at": iso_mtime(MODEL_META),
        "model_train_games": meta.get("train_games"),
        "model_target_mode": meta.get("target_mode"),
        "pending_settlements": pending,
        "last_performance_update": performance.get("last_updated"),
    }


def main():
    import argparse

    parser = argparse.ArgumentParser(description="Check SportsBotv2 XGBoost operational readiness")
    parser.add_argument("--db-path", default=str(DB_PATH))
    parser.add_argument("--performance-path", default=str(recordkeeping.PERFORMANCE_PATH))
    args = parser.parse_args()

    result = run_preflight(Path(args.db_path), Path(args.performance_path))
    print(json.dumps(result, indent=2, default=str))


if __name__ == "__main__":
    main()
