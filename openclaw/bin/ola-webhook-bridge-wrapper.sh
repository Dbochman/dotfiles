#!/usr/bin/env bash
# Cache-only LaunchAgent boundary for the Ola HMAC webhook bridge.

set -euo pipefail
umask 077

readonly CACHE="${OPENCLAW_SECRETS_CACHE:-$HOME/.openclaw/.secrets-cache}"
readonly BRIDGE="$HOME/.openclaw/bin/ola-webhook-bridge.py"

fail() {
  printf 'ola-webhook-bridge: %s\n' "$1" >&2
  exit 1
}

owner_mode() {
  /usr/bin/stat -f '%u %Lp' "$1" 2>/dev/null
}

[ -f "$CACHE" ] && [ ! -L "$CACHE" ] \
  || fail "protected secrets cache is unavailable"
[ "$(owner_mode "$CACHE")" = "$(/usr/bin/id -u) 600" ] \
  || fail "protected secrets cache ownership or mode is invalid"
[ -f "$BRIDGE" ] && [ ! -L "$BRIDGE" ] \
  || fail "deployed bridge is unavailable"

set -a
# shellcheck disable=SC1090
source "$CACHE"
set +a

[ -n "${OLA_WEBHOOK_SECRET:-}" ] \
  || fail "OLA_WEBHOOK_SECRET is unavailable"
[ -n "${OLA_HOOK_TOKEN:-}" ] \
  || fail "OLA_HOOK_TOKEN is unavailable"

exec /usr/bin/env -i \
  HOME="$HOME" \
  PATH="/usr/bin:/bin" \
  PYTHONDONTWRITEBYTECODE=1 \
  PYTHONUNBUFFERED=1 \
  OLA_WEBHOOK_SECRET="$OLA_WEBHOOK_SECRET" \
  OPENCLAW_HOOK_TOKEN="$OLA_HOOK_TOKEN" \
  OLA_BRIDGE_LISTEN_HOST="127.0.0.1" \
  OLA_BRIDGE_LISTEN_PORT="18790" \
  OLA_BRIDGE_CALLBACK_PATH="/hooks/wake" \
  OLA_BRIDGE_UPSTREAM_PORT="18789" \
  /usr/bin/python3 -I "$BRIDGE"
