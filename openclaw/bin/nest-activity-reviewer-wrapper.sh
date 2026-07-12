#!/usr/bin/env bash

set -euo pipefail

umask 077

readonly SERVICE="nest-activity-reviewer"
readonly BASE_DIR="$HOME/.openclaw/nest-events"
readonly STATE_DIR="$BASE_DIR/state"
readonly DATABASE_FILE="$STATE_DIR/events.sqlite3"
readonly REVIEW_STATE_FILE="$STATE_DIR/activity-reviewer.json"
readonly IMAGE_DIR="$STATE_DIR/activity-images"
readonly LOCK_FILE="$STATE_DIR/activity-reviewer.lock"
readonly PRESENCE_DIR="$HOME/.openclaw/presence"
readonly PRESENCE_STATE_FILE="$PRESENCE_DIR/state.json"
readonly CABIN_SCAN_FILE="$PRESENCE_DIR/cabin-scan.json"
readonly CROSSTOWN_SCAN_FILE="$PRESENCE_DIR/crosstown-scan.json"
readonly PRESENCE_OBSERVER="$HOME/.openclaw/workspace/scripts/presence-detect.sh"
readonly SECRETS_CACHE="$HOME/.openclaw/.secrets-cache"
readonly REVIEWER="$HOME/.openclaw/bin/nest-activity-reviewer.py"
readonly PYTHON="/usr/bin/python3"
readonly LOG_DIR="$HOME/.openclaw/logs"
readonly STDOUT_LOG="$LOG_DIR/$SERVICE.log"
readonly STDERR_LOG="$LOG_DIR/$SERVICE.err.log"
readonly LOG_LIMIT_BYTES=262144
readonly LOG_RETAIN_LINES=200
readonly LOG_RETAIN_BYTES=131072
readonly LOG_LINE_BYTES=8192
readonly ACTIVITY_MODE="${NEST_ACTIVITY_MODE:-cabin-commentary}"
readonly ACTIVITY_STATE_DIR="${NEST_EVENT_STATE_DIR:-$STATE_DIR}"
readonly ACTIVITY_DATABASE="${NEST_EVENT_DATABASE:-$DATABASE_FILE}"
readonly ACTIVITY_STATE_FILE="${NEST_ACTIVITY_STATE_FILE:-$REVIEW_STATE_FILE}"
readonly ACTIVITY_IMAGE_DIR="${NEST_ACTIVITY_IMAGE_DIR:-$IMAGE_DIR}"
readonly ACTIVITY_LOCK_FILE="${NEST_ACTIVITY_LOCK_FILE:-$LOCK_FILE}"
readonly ACTIVITY_PRESENCE_STATE="${OPENCLAW_PRESENCE_STATE:-$PRESENCE_STATE_FILE}"
readonly ACTIVITY_CABIN_SCAN="${OPENCLAW_PRESENCE_CABIN_SCAN:-$CABIN_SCAN_FILE}"
readonly ACTIVITY_CROSSTOWN_SCAN="${OPENCLAW_PRESENCE_CROSSTOWN_SCAN:-$CROSSTOWN_SCAN_FILE}"
readonly ACTIVITY_PRESENCE_OBSERVER="${OPENCLAW_PRESENCE_OBSERVER:-$PRESENCE_OBSERVER}"

if [ "$#" -eq 0 ]; then
  set -- run
fi
readonly COMMAND="$1"

fail() {
  printf '%s: %s\n' "$SERVICE" "$1" >&2
  exit 1
}

owner_mode() {
  /usr/bin/stat -f '%u %Lp' "$1" 2>/dev/null
}

require_private_dir() {
  local path="$1"

  [ -d "$path" ] && [ ! -L "$path" ] \
    || fail "required private directory is unavailable"
  [ "$(owner_mode "$path")" = "$(/usr/bin/id -u) 700" ] \
    || fail "private directory ownership or mode is invalid"
}

require_private_file() {
  local path="$1"

  [ -f "$path" ] && [ ! -L "$path" ] \
    || fail "required private file is unavailable"
  [ "$(owner_mode "$path")" = "$(/usr/bin/id -u) 600" ] \
    || fail "private file ownership or mode is invalid"
}

resolve_chat_target() (
  local target=""

  # The generated owner-only cache contains shell assignments.  Source it in
  # this short-lived subshell and emit only the one validated routing value to
  # the parent; the review process never inherits the rest of the cache.
  set +a
  set +x
  unset OPENCLAW_DYLAN_IMESSAGE_TARGET DYLAN_CHAT_ID
  # shellcheck disable=SC1090
  . "$SECRETS_CACHE" >/dev/null 2>&1 || exit 1
  set +x
  target="${OPENCLAW_DYLAN_IMESSAGE_TARGET:-}"
  if [ -z "$target" ] && [[ "${DYLAN_CHAT_ID:-}" =~ ^[1-9][0-9]{0,17}$ ]]; then
    target="chat_id:${DYLAN_CHAT_ID}"
  fi
  [[ "$target" =~ ^chat_id:[1-9][0-9]{0,17}$ ]] || exit 1
  printf '%s' "$target"
)

compact_log() {
  local path="$1"
  local temporary

  temporary=$(/usr/bin/mktemp "$LOG_DIR/.$SERVICE.log.XXXXXX") || return 1
  if /usr/bin/tail -c "$LOG_RETAIN_BYTES" "$path" \
    | /usr/bin/tail -n "$LOG_RETAIN_LINES" > "$temporary" \
    && /bin/chmod 600 "$temporary" \
    && /bin/mv -f "$temporary" "$path"; then
    return 0
  fi
  /bin/rm -f "$temporary"
  return 1
}

prepare_log() {
  local path="$1"
  local size

  [ -d "$LOG_DIR" ] && [ ! -L "$LOG_DIR" ] || return 1
  [ "$(/usr/bin/stat -f '%u' "$LOG_DIR" 2>/dev/null || true)" = "$(/usr/bin/id -u)" ] \
    || return 1
  [ ! -L "$path" ] || return 1
  if [ -e "$path" ]; then
    [ -f "$path" ] || return 1
    [ "$(/usr/bin/stat -f '%u' "$path" 2>/dev/null || true)" = "$(/usr/bin/id -u)" ] \
      || return 1
  else
    (set -o noclobber; : > "$path") 2>/dev/null || return 1
  fi
  /bin/chmod 600 "$path" || return 1
  size=$(/usr/bin/stat -f '%z' "$path" 2>/dev/null || return 1)
  if [ "$size" -gt "$LOG_LIMIT_BYTES" ]; then
    compact_log "$path" || return 1
  fi
}

bounded_log_stream() {
  local path="$1"
  local line
  local line_bytes
  local size
  local LC_ALL=C

  while IFS= read -r line || [ -n "$line" ]; do
    line_bytes=${#line}
    if [ "$line_bytes" -gt "$LOG_LINE_BYTES" ]; then
      line="${line:0:$((LOG_LINE_BYTES - 18))}[line truncated]"
      line_bytes=${#line}
    fi
    size=$(/usr/bin/stat -f '%z' "$path" 2>/dev/null) || return 1
    if [ $((size + line_bytes + 1)) -gt "$LOG_LIMIT_BYTES" ]; then
      compact_log "$path" || return 1
    fi
    printf '%s\n' "$line" >> "$path" || return 1
  done
}

validate_environment() {
  [ "$ACTIVITY_MODE" = "cabin-commentary" ] || fail "activity mode is invalid"
  [ "$ACTIVITY_STATE_DIR" = "$STATE_DIR" ] || fail "state path is invalid"
  [ "$ACTIVITY_DATABASE" = "$DATABASE_FILE" ] || fail "database path is invalid"
  [ "$ACTIVITY_STATE_FILE" = "$REVIEW_STATE_FILE" ] || fail "review state path is invalid"
  [ "$ACTIVITY_IMAGE_DIR" = "$IMAGE_DIR" ] || fail "image path is invalid"
  [ "$ACTIVITY_LOCK_FILE" = "$LOCK_FILE" ] || fail "lock path is invalid"
  [ "$ACTIVITY_PRESENCE_STATE" = "$PRESENCE_STATE_FILE" ] \
    || fail "presence state path is invalid"
  [ "$ACTIVITY_CABIN_SCAN" = "$CABIN_SCAN_FILE" ] \
    || fail "Cabin presence path is invalid"
  [ "$ACTIVITY_CROSSTOWN_SCAN" = "$CROSSTOWN_SCAN_FILE" ] \
    || fail "Crosstown presence path is invalid"
  [ "$ACTIVITY_PRESENCE_OBSERVER" = "$PRESENCE_OBSERVER" ] \
    || fail "presence observer path is invalid"
}

require_runtime() {
  [ -f "$REVIEWER" ] && [ ! -L "$REVIEWER" ] \
    || fail "deployed reviewer is unavailable"
  [ -x "$PYTHON" ] || fail "reviewer interpreter is unavailable"
  [ -f "$PRESENCE_OBSERVER" ] && [ ! -L "$PRESENCE_OBSERVER" ] \
    && [ -x "$PRESENCE_OBSERVER" ] \
    || fail "presence observer is unavailable"
  for binary in /opt/homebrew/bin/nest /opt/homebrew/bin/openclaw /opt/homebrew/bin/imsg; do
    [ -x "$binary" ] || fail "required reviewer command is unavailable"
  done
}

run_child() {
  local chat_target="$1"
  shift

  exec /usr/bin/env -i \
    HOME="$HOME" \
    PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin" \
    NEST_ACTIVITY_MODE="cabin-commentary" \
    NEST_EVENT_STATE_DIR="$STATE_DIR" \
    NEST_EVENT_DATABASE="$DATABASE_FILE" \
    NEST_ACTIVITY_STATE_FILE="$REVIEW_STATE_FILE" \
    NEST_ACTIVITY_IMAGE_DIR="$IMAGE_DIR" \
    NEST_ACTIVITY_LOCK_FILE="$LOCK_FILE" \
    OPENCLAW_PRESENCE_STATE="$PRESENCE_STATE_FILE" \
    OPENCLAW_PRESENCE_CABIN_SCAN="$CABIN_SCAN_FILE" \
    OPENCLAW_PRESENCE_CROSSTOWN_SCAN="$CROSSTOWN_SCAN_FILE" \
    OPENCLAW_PRESENCE_OBSERVER="$PRESENCE_OBSERVER" \
    OPENCLAW_DYLAN_IMESSAGE_TARGET="$chat_target" \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    "$PYTHON" "$REVIEWER" "$@"
}

run_service() {
  local chat_target="$1"
  shift
  local run_dir
  local stdout_fifo
  local stderr_fifo
  local stdout_logger_pid=""
  local stderr_logger_pid=""
  local child_pid=""
  local child_status=1
  local stdout_status=1
  local stderr_status=1

  prepare_log "$STDOUT_LOG" || exit 1
  prepare_log "$STDERR_LOG" || exit 1
  exec >>"$STDOUT_LOG" 2>>"$STDERR_LOG"

  run_dir=$(/usr/bin/mktemp -d "$STATE_DIR/.activity-run.XXXXXX") \
    || fail "private logging runtime is unavailable"
  stdout_fifo="$run_dir/stdout"
  stderr_fifo="$run_dir/stderr"

  cleanup_run() {
    /bin/rm -f "$stdout_fifo" "$stderr_fifo"
    for pid in "$stdout_logger_pid" "$stderr_logger_pid"; do
      if [ -n "$pid" ] && /bin/kill -0 "$pid" 2>/dev/null; then
        /bin/kill -TERM "$pid" 2>/dev/null || true
      fi
    done
    /bin/rmdir "$run_dir" 2>/dev/null || true
  }

  trap cleanup_run EXIT
  /bin/chmod 700 "$run_dir" || fail "private logging runtime is unavailable"
  /usr/bin/mkfifo -m 600 "$stdout_fifo" "$stderr_fifo" \
    || fail "private logging runtime is unavailable"

  forward_signal() {
    local signal_name="$1"
    if [ -n "$child_pid" ] && /bin/kill -0 "$child_pid" 2>/dev/null; then
      /bin/kill -"$signal_name" "$child_pid" 2>/dev/null || true
    fi
  }

  trap 'forward_signal TERM' TERM
  trap 'forward_signal INT' INT
  trap 'forward_signal HUP' HUP

  bounded_log_stream "$STDOUT_LOG" < "$stdout_fifo" &
  stdout_logger_pid=$!
  bounded_log_stream "$STDERR_LOG" < "$stderr_fifo" &
  stderr_logger_pid=$!

  /usr/bin/env -i \
    HOME="$HOME" \
    PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin" \
    NEST_ACTIVITY_MODE="cabin-commentary" \
    NEST_EVENT_STATE_DIR="$STATE_DIR" \
    NEST_EVENT_DATABASE="$DATABASE_FILE" \
    NEST_ACTIVITY_STATE_FILE="$REVIEW_STATE_FILE" \
    NEST_ACTIVITY_IMAGE_DIR="$IMAGE_DIR" \
    NEST_ACTIVITY_LOCK_FILE="$LOCK_FILE" \
    OPENCLAW_PRESENCE_STATE="$PRESENCE_STATE_FILE" \
    OPENCLAW_PRESENCE_CABIN_SCAN="$CABIN_SCAN_FILE" \
    OPENCLAW_PRESENCE_CROSSTOWN_SCAN="$CROSSTOWN_SCAN_FILE" \
    OPENCLAW_PRESENCE_OBSERVER="$PRESENCE_OBSERVER" \
    OPENCLAW_DYLAN_IMESSAGE_TARGET="$chat_target" \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    "$PYTHON" "$REVIEWER" "$@" > "$stdout_fifo" 2> "$stderr_fifo" &
  child_pid=$!
  /bin/rm -f "$stdout_fifo" "$stderr_fifo"

  set +e
  wait "$child_pid"
  child_status=$?
  while /bin/kill -0 "$child_pid" 2>/dev/null; do
    wait "$child_pid"
    child_status=$?
  done
  wait "$stdout_logger_pid"
  stdout_status=$?
  wait "$stderr_logger_pid"
  stderr_status=$?
  set -e

  trap - EXIT TERM INT HUP
  cleanup_run
  if [ "$stdout_status" -ne 0 ] || [ "$stderr_status" -ne 0 ]; then
    return 1
  fi
  return "$child_status"
}

require_private_dir "$BASE_DIR"
require_private_dir "$STATE_DIR"
require_private_file "$DATABASE_FILE"
require_private_file "$SECRETS_CACHE"
require_runtime
validate_environment
CHAT_TARGET=$(resolve_chat_target 2>/dev/null) \
  || fail "protected chat target is unavailable"
readonly CHAT_TARGET

case "$COMMAND" in
  run)
    run_service "$CHAT_TARGET" "$@"
    exit $?
    ;;
  initialize|status|check-config)
    run_child "$CHAT_TARGET" "$@"
    ;;
  *) fail "command is invalid" ;;
esac
