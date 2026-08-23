#!/usr/bin/env bash
# Run the exact home-event action worker with a bounded, sanitized log.

set -euo pipefail
umask 077

readonly BIN="$HOME/.openclaw/bin"
readonly LOG_DIR="$HOME/.openclaw/logs"
readonly LOG="$LOG_DIR/home-event-action.log"
readonly MAX_LOG_BYTES=131072
readonly KEEP_LOG_BYTES=65536

/bin/mkdir -p "$LOG_DIR"
[ -d "$LOG_DIR" ] && [ ! -L "$LOG_DIR" ] || exit 1
[ "$(/usr/bin/stat -f '%u' "$LOG_DIR")" = "$(/usr/bin/id -u)" ] || exit 1
[ ! -L "$LOG" ] || exit 1
if [ ! -e "$LOG" ]; then
  (set -o noclobber; : > "$LOG") 2>/dev/null || exit 1
fi
[ -f "$LOG" ] || exit 1
/bin/chmod 600 "$LOG"
if [ "$(/usr/bin/stat -f '%z' "$LOG")" -gt "$MAX_LOG_BYTES" ]; then
  temporary=$(/usr/bin/mktemp "$LOG_DIR/.home-event-action.XXXXXX") || exit 1
  /usr/bin/tail -c "$KEEP_LOG_BYTES" "$LOG" > "$temporary"
  /bin/chmod 600 "$temporary"
  /bin/mv -f "$temporary" "$LOG"
fi

set +e
output=$(/usr/bin/env -i HOME="$HOME" PATH="/opt/homebrew/bin:/usr/bin:/bin" \
  PYTHONDONTWRITEBYTECODE=1 "$BIN/home-event-action" run-once 2>&1)
status=$?
set -e
output=${output//$'\n'/ }
output=${output:0:2048}
printf '%s status=%d %s\n' \
  "$(/bin/date -u '+%Y-%m-%dT%H:%M:%SZ')" "$status" "$output" >> "$LOG"
exit "$status"
