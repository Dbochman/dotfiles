#!/bin/bash
# Start the sole Ring FCM ingress service with cache-only secrets.

set -euo pipefail

if [[ -f "$HOME/.openclaw/.secrets-cache" ]]; then
  set -a
  source "$HOME/.openclaw/.secrets-cache"
  set +a
fi

LOG="$HOME/.openclaw/logs/ring-event-listener.log"
MAX_BYTES=$((100 * 1024 * 1024))
KEEP=3

if [[ -f "$LOG" ]]; then
  SIZE=$(stat -f%z "$LOG" 2>/dev/null || echo 0)
  if [[ $SIZE -gt $MAX_BYTES ]]; then
    i=$KEEP
    while [[ $i -gt 1 ]]; do
      prev=$((i - 1))
      [[ -f "$LOG.$prev" ]] && mv -f "$LOG.$prev" "$LOG.$i"
      i=$prev
    done
    mv -f "$LOG" "$LOG.1"
    : > "$LOG"
    exec 1>>"$LOG" 2>&1
    echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) wrapper: rotated log at ${SIZE} bytes (kept $KEEP)"
  fi
fi

exec "$HOME/.openclaw/ring/venv/bin/python3" \
  "$HOME/.openclaw/skills/dog-walk/ring-event-listener.py"
