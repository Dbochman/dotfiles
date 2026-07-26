#!/usr/bin/env bash

set -euo pipefail

umask 077

readonly SERVICE="cabin-entry-verifier"
readonly OPENCLAW_ROOT="$HOME/.openclaw"
readonly BUS_ROOT="$OPENCLAW_ROOT/home-events"
readonly BUS_STATE_DIR="$BUS_ROOT/state"
readonly BUS_DATABASE="$BUS_STATE_DIR/events.sqlite3"
readonly STATE_DIR="$OPENCLAW_ROOT/cabin-entry-verifier"
readonly STATE_DATABASE="$STATE_DIR/state.sqlite3"
readonly IMAGE_DIR="$STATE_DIR/images"
readonly LOCK_FILE="$STATE_DIR/service.lock"
readonly PRESENCE_DIR="$OPENCLAW_ROOT/presence"
readonly PRESENCE_STATE="$PRESENCE_DIR/state.json"
readonly CABIN_SCAN="$PRESENCE_DIR/cabin-scan.json"
readonly CROSSTOWN_SCAN="$PRESENCE_DIR/crosstown-scan.json"
readonly SECRETS_CACHE="$OPENCLAW_ROOT/.secrets-cache"
readonly VERIFIER="$OPENCLAW_ROOT/bin/cabin-entry-verifier.py"
readonly REVIEWER="$OPENCLAW_ROOT/bin/nest-activity-reviewer.py"
readonly PYTHON="/usr/bin/python3"
readonly RUNTIME_PATH="/opt/homebrew/opt/node@22/bin:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin"
readonly LOG_DIR="$OPENCLAW_ROOT/logs"
readonly STDOUT_LOG="$LOG_DIR/$SERVICE.log"
readonly STDERR_LOG="$LOG_DIR/$SERVICE.err.log"
readonly LOG_LIMIT_BYTES=262144
readonly LOG_RETAIN_LINES=200
readonly LOG_RETAIN_BYTES=131072
readonly LOG_LINE_BYTES=8192
readonly ENTRY_MODE="${CABIN_ENTRY_MODE:-ring-kitchen-verification}"
readonly ENTRY_BUS_ROOT="${HOME_EVENTS_ROOT:-$BUS_ROOT}"
readonly ENTRY_STATE_DIR="${CABIN_ENTRY_STATE_DIR:-$STATE_DIR}"
readonly ENTRY_DATABASE="${CABIN_ENTRY_DATABASE:-$STATE_DATABASE}"
readonly ENTRY_IMAGE_DIR="${CABIN_ENTRY_IMAGE_DIR:-$IMAGE_DIR}"
readonly ENTRY_LOCK_FILE="${CABIN_ENTRY_LOCK_FILE:-$LOCK_FILE}"
readonly ENTRY_PRESENCE_STATE="${OPENCLAW_PRESENCE_STATE:-$PRESENCE_STATE}"
readonly ENTRY_CABIN_SCAN="${OPENCLAW_PRESENCE_CABIN_SCAN:-$CABIN_SCAN}"
readonly ENTRY_CROSSTOWN_SCAN="${OPENCLAW_PRESENCE_CROSSTOWN_SCAN:-$CROSSTOWN_SCAN}"

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
  [ "$ENTRY_MODE" = "ring-kitchen-verification" ] || fail "mode is invalid"
  [ "$ENTRY_BUS_ROOT" = "$BUS_ROOT" ] || fail "bus root is invalid"
  [ "$ENTRY_STATE_DIR" = "$STATE_DIR" ] || fail "state path is invalid"
  [ "$ENTRY_DATABASE" = "$STATE_DATABASE" ] || fail "database path is invalid"
  [ "$ENTRY_IMAGE_DIR" = "$IMAGE_DIR" ] || fail "image path is invalid"
  [ "$ENTRY_LOCK_FILE" = "$LOCK_FILE" ] || fail "lock path is invalid"
  [ "$ENTRY_PRESENCE_STATE" = "$PRESENCE_STATE" ] || fail "presence state path is invalid"
  [ "$ENTRY_CABIN_SCAN" = "$CABIN_SCAN" ] || fail "Cabin scan path is invalid"
  [ "$ENTRY_CROSSTOWN_SCAN" = "$CROSSTOWN_SCAN" ] || fail "Crosstown scan path is invalid"
}

require_runtime() {
  [ -f "$VERIFIER" ] && [ ! -L "$VERIFIER" ] || fail "verifier is unavailable"
  [ -f "$REVIEWER" ] && [ ! -L "$REVIEWER" ] || fail "reviewer helper is unavailable"
  [ -x "$PYTHON" ] || fail "Python is unavailable"
  for binary in /opt/homebrew/bin/nest /opt/homebrew/bin/openclaw /opt/homebrew/bin/imsg; do
    [ -x "$binary" ] || fail "required verifier command is unavailable"
  done
}

run_child() {
  local chat_target="$1"
  shift
  exec /usr/bin/env -i \
    HOME="$HOME" \
    PATH="$RUNTIME_PATH" \
    CABIN_ENTRY_MODE="ring-kitchen-verification" \
    HOME_EVENTS_ROOT="$BUS_ROOT" \
    CABIN_ENTRY_STATE_DIR="$STATE_DIR" \
    CABIN_ENTRY_DATABASE="$STATE_DATABASE" \
    CABIN_ENTRY_IMAGE_DIR="$IMAGE_DIR" \
    CABIN_ENTRY_LOCK_FILE="$LOCK_FILE" \
    OPENCLAW_PRESENCE_STATE="$PRESENCE_STATE" \
    OPENCLAW_PRESENCE_CABIN_SCAN="$CABIN_SCAN" \
    OPENCLAW_PRESENCE_CROSSTOWN_SCAN="$CROSSTOWN_SCAN" \
    OPENCLAW_DYLAN_IMESSAGE_TARGET="$chat_target" \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    "$PYTHON" "$VERIFIER" "$@"
}

run_service() {
  local chat_target="$1"
  shift
  local run_dir stdout_fifo stderr_fifo
  local stdout_logger_pid="" stderr_logger_pid="" child_pid=""
  local child_status=1 stdout_status=1 stderr_status=1

  prepare_log "$STDOUT_LOG" || exit 1
  prepare_log "$STDERR_LOG" || exit 1
  exec >>"$STDOUT_LOG" 2>>"$STDERR_LOG"

  run_dir=$(/usr/bin/mktemp -d "$STATE_DIR/.run.XXXXXX") \
    || fail "private logging runtime is unavailable"
  /bin/chmod 700 "$run_dir"
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
  /usr/bin/mkfifo -m 600 "$stdout_fifo" "$stderr_fifo"

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
    PATH="$RUNTIME_PATH" \
    CABIN_ENTRY_MODE="ring-kitchen-verification" \
    HOME_EVENTS_ROOT="$BUS_ROOT" \
    CABIN_ENTRY_STATE_DIR="$STATE_DIR" \
    CABIN_ENTRY_DATABASE="$STATE_DATABASE" \
    CABIN_ENTRY_IMAGE_DIR="$IMAGE_DIR" \
    CABIN_ENTRY_LOCK_FILE="$LOCK_FILE" \
    OPENCLAW_PRESENCE_STATE="$PRESENCE_STATE" \
    OPENCLAW_PRESENCE_CABIN_SCAN="$CABIN_SCAN" \
    OPENCLAW_PRESENCE_CROSSTOWN_SCAN="$CROSSTOWN_SCAN" \
    OPENCLAW_DYLAN_IMESSAGE_TARGET="$chat_target" \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    "$PYTHON" "$VERIFIER" "$@" > "$stdout_fifo" 2> "$stderr_fifo" &
  child_pid=$!
  /bin/rm -f "$stdout_fifo" "$stderr_fifo"

  set +e
  wait "$child_pid"
  child_status=$?
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

require_private_dir "$BUS_ROOT"
require_private_dir "$BUS_STATE_DIR"
require_private_file "$BUS_DATABASE"
require_private_file "$SECRETS_CACHE"
require_runtime
validate_environment

if [ "$COMMAND" = "initialize" ]; then
  if [ ! -e "$STATE_DIR" ]; then
    /usr/bin/install -d -m 700 "$STATE_DIR"
  fi
  if [ ! -e "$IMAGE_DIR" ]; then
    /usr/bin/install -d -m 700 "$IMAGE_DIR"
  fi
fi

require_private_dir "$STATE_DIR"
require_private_dir "$IMAGE_DIR"
CHAT_TARGET=$(resolve_chat_target 2>/dev/null) \
  || fail "protected chat target is unavailable"
readonly CHAT_TARGET

case "$COMMAND" in
  run)
    run_service "$CHAT_TARGET" "$@"
    exit $?
    ;;
  initialize|register|disable|status|check-config|arm-canary)
    run_child "$CHAT_TARGET" "$@"
    ;;
  *) fail "command is invalid" ;;
esac
