#!/usr/bin/env bash
# Resolve the protected owner route and run one bounded delivery pass.

set -euo pipefail
umask 077

readonly SERVICE="home-event-delivery"
readonly ROOT="$HOME/.openclaw/home-events"
readonly STATE="$ROOT/state"
readonly DELIVERY="$HOME/.openclaw/bin/home-event-delivery.py"
readonly PYTHON="/opt/homebrew/bin/python3"
readonly SECRETS_CACHE="$HOME/.openclaw/.secrets-cache"
readonly LOG_DIR="$HOME/.openclaw/logs"
readonly LOG="$LOG_DIR/home-event-delivery.log"
readonly LOG_LIMIT=262144
readonly LOG_KEEP=131072

fail() {
  printf '%s: protected runtime is unavailable\n' "$SERVICE" >&2
  exit 1
}

owner_mode() {
  /usr/bin/stat -f '%u %Lp' "$1" 2>/dev/null
}

require_private_dir() {
  [ -d "$1" ] && [ ! -L "$1" ] \
    && [ "$(owner_mode "$1")" = "$(/usr/bin/id -u) 700" ] || fail
}

require_private_file() {
  [ -f "$1" ] && [ ! -L "$1" ] \
    && [ "$(owner_mode "$1")" = "$(/usr/bin/id -u) 600" ] || fail
}

prepare_log() {
  [ -d "$LOG_DIR" ] && [ ! -L "$LOG_DIR" ] || fail
  [ "$(/usr/bin/stat -f '%u' "$LOG_DIR" 2>/dev/null)" = "$(/usr/bin/id -u)" ] \
    || fail
  [ ! -L "$LOG" ] || fail
  if [ ! -e "$LOG" ]; then
    (set -o noclobber; : > "$LOG") 2>/dev/null || fail
  fi
  [ -f "$LOG" ] || fail
  /bin/chmod 600 "$LOG"
  if [ "$(/usr/bin/stat -f '%z' "$LOG")" -gt "$LOG_LIMIT" ]; then
    local temporary
    temporary=$(/usr/bin/mktemp "$LOG_DIR/.home-event-delivery.XXXXXX") || fail
    /usr/bin/tail -c "$LOG_KEEP" "$LOG" > "$temporary" \
      && /bin/chmod 600 "$temporary" \
      && /bin/mv -f "$temporary" "$LOG" || fail
  fi
}

run_delivery() (
  local target=""
  local -a child_env
  set +a
  set +x
  unset OPENCLAW_DYLAN_IMESSAGE_TARGET DYLAN_CHAT_ID
  unset OPENCLAW_GATEWAY_TOKEN OPENCLAW_GATEWAY_PASSWORD
  # shellcheck disable=SC1090
  . "$SECRETS_CACHE" >/dev/null 2>&1 || exit 1
  set +x
  target="${OPENCLAW_DYLAN_IMESSAGE_TARGET:-}"
  if [ -z "$target" ] && [[ "${DYLAN_CHAT_ID:-}" =~ ^[1-9][0-9]{0,17}$ ]]; then
    target="chat_id:${DYLAN_CHAT_ID}"
  fi
  if [ -n "$target" ] && [[ ! "$target" =~ ^chat_id:[1-9][0-9]{0,17}$ ]]; then
    exit 1
  fi
  child_env=(
    "HOME=$HOME"
    "PATH=/opt/homebrew/opt/node@22/bin:/opt/homebrew/bin:/usr/bin:/bin"
    "HOME_EVENTS_ROOT=$ROOT"
    "HOME_EVENTS_PRESENCE_STATE=$HOME/.openclaw/presence/state.json"
    "OPENCLAW_DYLAN_IMESSAGE_TARGET=$target"
    "PYTHONDONTWRITEBYTECODE=1"
  )
  if [ -n "${OPENCLAW_GATEWAY_TOKEN:-}" ]; then
    child_env+=("OPENCLAW_GATEWAY_TOKEN=$OPENCLAW_GATEWAY_TOKEN")
  fi
  if [ -n "${OPENCLAW_GATEWAY_PASSWORD:-}" ]; then
    child_env+=("OPENCLAW_GATEWAY_PASSWORD=$OPENCLAW_GATEWAY_PASSWORD")
  fi
  /usr/bin/env -i "${child_env[@]}" "$PYTHON" -I "$DELIVERY"
)

require_private_dir "$ROOT"
require_private_dir "$ROOT/config"
require_private_dir "$ROOT/spool"
require_private_dir "$STATE"
require_private_file "$ROOT/state/events.sqlite3"
require_private_file "$ROOT/state/delivery.lock"
require_private_file "$SECRETS_CACHE"
[ -f "$DELIVERY" ] && [ ! -L "$DELIVERY" ] || fail
[ -x "$PYTHON" ] || fail
prepare_log

set +e
output=$(run_delivery 2>&1)
status=$?
set -e
output=${output//$'\n'/ }
output=${output:0:4096}
printf '%s status=%d %s\n' \
  "$(/bin/date -u '+%Y-%m-%dT%H:%M:%SZ')" "$status" "$output" >> "$LOG"
exit "$status"
