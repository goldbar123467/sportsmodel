#!/bin/bash
# Install/update the SportsBotv2 daily cron block without deleting other jobs.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LOCAL_TZ="${LOCAL_TZ:-America/New_York}"
SCHEDULE="${SCHEDULE:-55 9 * * *}"
BEGIN_MARKER="# BEGIN SportsBotv2 daily odds scrape"
END_MARKER="# END SportsBotv2 daily odds scrape"

current="$(mktemp)"
next="$(mktemp)"
cleanup() {
  rm -f "$current" "$next"
}
trap cleanup EXIT

crontab -l > "$current" 2>/dev/null || true
sed "/$BEGIN_MARKER/,/$END_MARKER/d" "$current" > "$next"

cat >> "$next" <<CRON
$BEGIN_MARKER
SHELL=/bin/bash
CRON_TZ=$LOCAL_TZ
$SCHEDULE cd "$SCRIPT_DIR" && ./auto_cycle.sh >> "$SCRIPT_DIR/logs/auto_cycle.log" 2>&1
$END_MARKER
CRON

crontab "$next"
echo "Installed SportsBotv2 cron: $SCHEDULE $LOCAL_TZ"
