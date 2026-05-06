#!/bin/bash
# MLB O/U Bot daily cycle.
# Scrapes MLB schedule/data plus The Odds API totals, retrains, then writes picks.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
PYTHON="${PYTHON:-$PROJECT_DIR/.venv/bin/python}"
if [ ! -x "$PYTHON" ]; then
  PYTHON="python3"
fi

LOCAL_TZ="${LOCAL_TZ:-America/New_York}"
DATE_STR="${1:-$(TZ="$LOCAL_TZ" date +%F)}"
SEASON="${SEASON:-${DATE_STR:0:4}}"
LOG_DIR="$SCRIPT_DIR/logs"
mkdir -p "$LOG_DIR"

cd "$SCRIPT_DIR"

if [ -f "$PROJECT_DIR/.env" ]; then
  set -a
  # shellcheck disable=SC1091
  source "$PROJECT_DIR/.env"
  set +a
fi

if [ -z "${ODDS_API_KEY:-}" ]; then
  echo "$(date -Is): ODDS_API_KEY is not set in the environment or $PROJECT_DIR/.env" >&2
  exit 2
fi

echo "$(date -Is): === Starting MLB O/U daily cycle for $DATE_STR ($LOCAL_TZ) ==="

echo "$(date -Is): Scraping MLB data and Odds API totals..."
"$PYTHON" scrape.py --date "$DATE_STR" --season "$SEASON"

echo "$(date -Is): Retraining XGBoost model..."
"$PYTHON" train.py

echo "$(date -Is): Generating picks..."
"$PYTHON" pick_today.py "$DATE_STR"

echo "$(date -Is): === Daily cycle complete ==="
