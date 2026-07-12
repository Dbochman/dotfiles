#!/usr/bin/env bash

set -euo pipefail

umask 077

readonly SERVICE="nest-event-listener"
readonly BASE_DIR="$HOME/.openclaw/nest-events"
readonly CREDENTIAL_DIR="$BASE_DIR/credentials"
readonly CONFIG_DIR="$BASE_DIR/config"
readonly STATE_DIR="$BASE_DIR/state"
readonly CREDENTIAL_FILE="$CREDENTIAL_DIR/subscriber-service-account.json"
readonly CONFIG_FILE="$CONFIG_DIR/cameras.json"
readonly LISTENER="$HOME/.openclaw/bin/nest-event-listener.py"
readonly PYTHON="$HOME/.openclaw/venvs/nest-events/bin/python"
readonly LOG_DIR="$HOME/.openclaw/logs"
readonly STDOUT_LOG="$LOG_DIR/$SERVICE.log"
readonly STDERR_LOG="$LOG_DIR/$SERVICE.err.log"
readonly LOG_LIMIT_BYTES=262144
readonly LOG_RETAIN_LINES=200
readonly LOG_RETAIN_BYTES=131072
readonly LOG_LINE_BYTES=8192
readonly EVENT_MODE="${NEST_EVENT_MODE:-shadow}"
readonly EVENT_SUBSCRIPTION="${NEST_EVENT_SUBSCRIPTION:-openclaw-nest-events}"
readonly EVENT_CONFIG="${NEST_EVENT_CONFIG:-$CONFIG_FILE}"
readonly EVENT_STATE_DIR="${NEST_EVENT_STATE_DIR:-$STATE_DIR}"

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

require_listener_runtime() {
  [ -f "$LISTENER" ] && [ ! -L "$LISTENER" ] \
    || fail "deployed listener is unavailable"
  [ -x "$PYTHON" ] && [ ! -L "$HOME/.openclaw/venvs/nest-events" ] \
    || fail "dedicated listener interpreter is unavailable"
}

validate_config_environment() {
  [ "$EVENT_MODE" = "shadow" ] || fail "event mode is invalid"
  [ "$EVENT_SUBSCRIPTION" = "openclaw-nest-events" ] \
    || fail "subscription name is invalid"
  [ "$EVENT_CONFIG" = "$CONFIG_FILE" ] || fail "config path is invalid"
}

validate_state_environment() {
  [ "$EVENT_STATE_DIR" = "$STATE_DIR" ] || fail "state path is invalid"
}

run_service() {
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

  require_private_dir "$BASE_DIR"
  require_private_dir "$CREDENTIAL_DIR"
  require_private_dir "$CONFIG_DIR"
  require_private_dir "$STATE_DIR"
  require_private_file "$CREDENTIAL_FILE"
  require_private_file "$CONFIG_FILE"
  require_listener_runtime
  validate_config_environment
  validate_state_environment

  run_dir=$(/usr/bin/mktemp -d "$STATE_DIR/.listener-run.XXXXXX") \
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
    PATH="/usr/bin:/bin" \
    GOOGLE_APPLICATION_CREDENTIALS="$CREDENTIAL_FILE" \
    NEST_EVENT_CONFIG="$CONFIG_FILE" \
    NEST_EVENT_STATE_DIR="$STATE_DIR" \
    NEST_EVENT_MODE="shadow" \
    NEST_EVENT_SUBSCRIPTION="openclaw-nest-events" \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    "$PYTHON" "$LISTENER" "$@" > "$stdout_fifo" 2> "$stderr_fifo" &
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

case "$COMMAND" in
  run)
    run_service "$@"
    exit $?
    ;;
  status)
    require_private_dir "$BASE_DIR"
    require_private_dir "$STATE_DIR"
    require_listener_runtime
    validate_state_environment
    ;;
  check-config)
    require_private_dir "$BASE_DIR"
    require_private_dir "$CONFIG_DIR"
    require_private_file "$CONFIG_FILE"
    require_listener_runtime
    validate_config_environment
    ;;
  *) fail "command is invalid" ;;
esac

exec /usr/bin/env -i \
  HOME="$HOME" \
  PATH="/usr/bin:/bin" \
  GOOGLE_APPLICATION_CREDENTIALS="$CREDENTIAL_FILE" \
  NEST_EVENT_CONFIG="$CONFIG_FILE" \
  NEST_EVENT_STATE_DIR="$STATE_DIR" \
  NEST_EVENT_MODE="shadow" \
  NEST_EVENT_SUBSCRIPTION="openclaw-nest-events" \
  PYTHONDONTWRITEBYTECODE=1 \
  PYTHONUNBUFFERED=1 \
  "$PYTHON" "$LISTENER" "$@"
