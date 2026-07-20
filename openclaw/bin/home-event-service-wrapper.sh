#!/usr/bin/env bash
# Run one protected home-events maintenance component with bounded safe logs.

set -euo pipefail
umask 077

readonly COMPONENT="${1:-}"
readonly ROOT="$HOME/.openclaw/home-events"
readonly BIN="$HOME/.openclaw/bin"
readonly PYTHON="/opt/homebrew/bin/python3"
readonly LOG_DIR="$HOME/.openclaw/logs"
readonly LOG="$LOG_DIR/home-events.log"
readonly MAX_LOG_BYTES=262144
readonly KEEP_LOG_BYTES=131072

fail() {
  printf 'home-events: %s\n' "$1" >&2
  exit 1
}

owner_mode() {
  /usr/bin/stat -f '%u %Lp' "$1" 2>/dev/null
}

require_private_dir() {
  [ -d "$1" ] && [ ! -L "$1" ] \
    && [ "$(owner_mode "$1")" = "$(/usr/bin/id -u) 700" ] \
    || fail "protected runtime is unavailable"
}

prepare_log() {
  /bin/mkdir -p "$LOG_DIR"
  [ -d "$LOG_DIR" ] && [ ! -L "$LOG_DIR" ] || fail "log directory is unsafe"
  [ "$(/usr/bin/stat -f '%u' "$LOG_DIR")" = "$(/usr/bin/id -u)" ] \
    || fail "log directory is unsafe"
  [ ! -L "$LOG" ] || fail "log file is unsafe"
  if [ ! -e "$LOG" ]; then
    (set -o noclobber; : > "$LOG") 2>/dev/null || fail "log file is unavailable"
  fi
  [ -f "$LOG" ] || fail "log file is unsafe"
  /bin/chmod 600 "$LOG"
  if [ "$(/usr/bin/stat -f '%z' "$LOG")" -gt "$MAX_LOG_BYTES" ]; then
    local temporary
    temporary=$(/usr/bin/mktemp "$LOG_DIR/.home-events.XXXXXX") \
      || fail "log compaction failed"
    /usr/bin/tail -c "$KEEP_LOG_BYTES" "$LOG" > "$temporary" \
      && /bin/chmod 600 "$temporary" \
      && /bin/mv -f "$temporary" "$LOG" \
      || fail "log compaction failed"
  fi
}

run_component() {
  case "$COMPONENT" in
    ingest)
      /usr/bin/env -i HOME="$HOME" PATH="/opt/homebrew/bin:/usr/bin:/bin" \
        PYTHONDONTWRITEBYTECODE=1 "$BIN/home-eventctl" ingest-once
      ;;
    correlate)
      /usr/bin/env -i HOME="$HOME" PATH="/opt/homebrew/bin:/usr/bin:/bin" \
        HOME_EVENTS_ROOT="$ROOT" \
        HOME_EVENTS_PRESENCE_STATE="$HOME/.openclaw/presence/state.json" \
        PYTHONDONTWRITEBYTECODE=1 "$PYTHON" -I \
        "$BIN/home-event-correlator.py"
      ;;
    august)
      /usr/bin/env -i HOME="$HOME" PATH="/opt/homebrew/bin:/usr/bin:/bin" \
        HOME_EVENTS_ROOT="$ROOT" \
        HOME_EVENTS_AUGUST_ENABLED="${HOME_EVENTS_AUGUST_ENABLED:-0}" \
        AUGUST_BIN="$BIN/august" HOME_EVENTCTL="$BIN/home-eventctl" \
        PYTHONDONTWRITEBYTECODE=1 "$PYTHON" -I \
        "$BIN/august-event-adapter.py"
      ;;
    nest)
      /usr/bin/env -i HOME="$HOME" PATH="/opt/homebrew/bin:/usr/bin:/bin" \
        HOME_EVENTS_ROOT="$ROOT" \
        HOME_EVENTS_NEST_ENABLED="${HOME_EVENTS_NEST_ENABLED:-0}" \
        NEST_EVENT_DATABASE="$HOME/.openclaw/nest-events/state/events.sqlite3" \
        HOME_EVENTCTL="$BIN/home-eventctl" \
        PYTHONDONTWRITEBYTECODE=1 "$PYTHON" -I \
        "$BIN/nest-home-event-bridge.py"
      ;;
    *) fail "unsupported component" ;;
  esac
}

require_private_dir "$ROOT"
require_private_dir "$ROOT/config"
require_private_dir "$ROOT/spool"
require_private_dir "$ROOT/state"
prepare_log

set +e
output=$(run_component 2>&1)
status=$?
set -e
output=${output//$'\n'/ }
output=${output:0:4096}
printf '%s component=%s status=%d %s\n' \
  "$(/bin/date -u '+%Y-%m-%dT%H:%M:%SZ')" "$COMPONENT" "$status" "$output" \
  >> "$LOG"
exit "$status"
