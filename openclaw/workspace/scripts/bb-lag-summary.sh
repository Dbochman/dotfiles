#!/bin/bash
# bb-lag-summary.sh — Daily summary for BlueBubbles ingest lag alerts.

set -euo pipefail

LAG_FILE="$HOME/.openclaw/logs/bb-ingest-lag.log"
SUMMARY_LOG="$HOME/.openclaw/logs/bb-lag-summary.log"
STATE_DIR="${HOME}/.openclaw/bb-watchdog"
STATE_FILE="${STATE_DIR}/lag-summary-state"

mkdir -p "$STATE_DIR"

target_date=$(date -v-1d '+%Y-%m-%d')
last_done=""
if [[ -f "$STATE_FILE" ]]; then
  last_done=$(cat "$STATE_FILE" 2>/dev/null || true)
fi

if [[ "$last_done" == "$target_date" ]]; then
  exit 0
fi

count=0
max_sec=0
avg_sec=0

if [[ -f "$LAG_FILE" ]]; then
  read -r count avg_sec max_sec <<< "$(awk -F',' -v d="$target_date" '
    $1 ~ "^" d {
      c++
      sum += $2
      if ($2 > max) max = $2
    }
    END {
      if (c > 0) {
        printf "%d %d %d", c, int(sum / c), max
      } else {
        printf "0 0 0"
      }
    }
  ' "$LAG_FILE")"
fi

line="[$(date '+%Y-%m-%d %H:%M:%S')] DAILY LAG SUMMARY (${target_date}): count=${count} avg_s=${avg_sec} max_s=${max_sec}"
echo "$line" >> "$SUMMARY_LOG"
echo "$line" >> "$HOME/.openclaw/logs/bb-watchdog.log"
echo "$target_date" > "$STATE_FILE"
