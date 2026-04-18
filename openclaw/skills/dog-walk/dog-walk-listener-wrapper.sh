#!/bin/bash
# dog-walk-listener-wrapper.sh — Start dog walk listener with secrets loaded
# Runs as a persistent LaunchAgent (ai.openclaw.dog-walk-listener)

set -euo pipefail

# Load secrets (cache-only pattern — no op read, avoids launchd hang)
if [[ -f "$HOME/.openclaw/.secrets-cache" ]]; then
  set -a
  source "$HOME/.openclaw/.secrets-cache"
  set +a
fi

# Log rotation — prevent runaway logs from filling the disk.
# The plist's StandardOutPath/StandardErrorPath open the log file before exec,
# so we rotate on every launchd (re)start. The Python listener forces exit if
# stderr spam exceeds a threshold, letting launchd (KeepAlive=true) restart us
# here, at which point we rotate.
LOG="$HOME/.openclaw/logs/dog-walk-listener.log"
MAX_BYTES=$((100 * 1024 * 1024))  # 100MB
KEEP=3                              # .1 .2 .3 (oldest dropped)

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
    # Redirect our own FDs to the new inode — launchd opened the old one at
    # spawn time and child processes would inherit that stale FD otherwise.
    exec 1>>"$LOG" 2>&1
    echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) wrapper: rotated log at ${SIZE} bytes (kept $KEEP)"
  fi
fi

PYTHON="$HOME/.openclaw/ring/venv/bin/python3"
LISTENER="$HOME/.openclaw/skills/dog-walk/dog-walk-listener.py"

exec "$PYTHON" "$LISTENER"
