#!/usr/bin/env bash
# opentable-refresh-token.sh - Refresh the OpenTable CLI token from the
# persisted Pinchtab browser profile. Falls back to email verification only
# when the browser session cannot provide a valid token.

set -euo pipefail

PATH="/opt/homebrew/bin:/opt/homebrew/opt/node@22/bin:/usr/local/bin:/usr/bin:/bin"
TOKEN_CACHE="$HOME/.cache/openclaw-gateway/opentable_auth_token"
SECRETS_CACHE="$HOME/.openclaw/.secrets-cache"
RUNTIME_DIR="$HOME/.openclaw/run"
LOCK_DIR="$RUNTIME_DIR/opentable-refresh.lock"
OT_EMAIL="bochmanspam@gmail.com"
GWS_ACCOUNT="bochmanspam@gmail.com"
OT_DASHBOARD_URL="https://www.opentable.com/user/dining-dashboard"
OT_LOGIN_URL="https://www.opentable.com/authenticate/start?isPopup=false"
PINCHTAB_INSTANCE_HELPER="$HOME/.openclaw/bin/pinchtab-headless-instance"
PINCHTAB_INSTANCE_ID=""
PINCHTAB_INSTANCE_STARTED=0
TAB_ID=""
PREVIOUS_TOKEN=""
LOCK_HELD=0

umask 077

log() {
  printf '[%s] %s\n' "$(date '+%H:%M:%S')" "$*" >&2
}

close_tab() {
  if [[ -n "$TAB_ID" ]]; then
    pinchtab close "$TAB_ID" >/dev/null 2>&1 || true
    TAB_ID=""
  fi
}

cleanup() {
  close_tab
  if [[ -n "$PINCHTAB_INSTANCE_ID" ]]; then
    "$PINCHTAB_INSTANCE_HELPER" release "$PINCHTAB_INSTANCE_ID" "$PINCHTAB_INSTANCE_STARTED"
    PINCHTAB_INSTANCE_ID=""
  fi
  if [[ "$LOCK_HELD" == "1" ]]; then
    rm -rf "$LOCK_DIR"
    LOCK_HELD=0
  fi
}

acquire_lock() {
  mkdir -p "$RUNTIME_DIR"
  if mkdir "$LOCK_DIR" 2>/dev/null; then
    printf '%s\n' "$$" > "$LOCK_DIR/pid"
    LOCK_HELD=1
    return
  fi

  local holder=""
  [[ -r "$LOCK_DIR/pid" ]] && holder=$(cat "$LOCK_DIR/pid" 2>/dev/null || true)
  if [[ -n "$holder" ]] && kill -0 "$holder" 2>/dev/null; then
    log "Another OpenTable token refresh is already running"
    exit 0
  fi

  rm -rf "$LOCK_DIR"
  mkdir "$LOCK_DIR"
  printf '%s\n' "$$" > "$LOCK_DIR/pid"
  LOCK_HELD=1
}

trap cleanup EXIT

if [[ -r "$SECRETS_CACHE" ]]; then
  set -a
  . "$SECRETS_CACHE"
  set +a
fi

is_atk() {
  [[ "$1" =~ ^[A-Za-z0-9-]{20,}$ ]]
}

write_token() {
  local token="$1"
  local temporary
  mkdir -p "$(dirname "$TOKEN_CACHE")"
  temporary=$(mktemp "${TOKEN_CACHE}.tmp.XXXXXX")
  printf '%s' "$token" > "$temporary"
  chmod 600 "$temporary"
  mv -f "$temporary" "$TOKEN_CACHE"
}

restore_previous_token() {
  if [[ -n "$PREVIOUS_TOKEN" ]]; then
    write_token "$PREVIOUS_TOKEN"
  else
    rm -f "$TOKEN_CACHE"
  fi
}

validate_cached_token() {
  opentable info 1267699 --json >/dev/null 2>&1
}

install_and_validate_token() {
  local candidate="$1"
  write_token "$candidate"
  if validate_cached_token; then
    return 0
  fi
  restore_previous_token
  return 1
}

acquire_pinchtab_instance() {
  if [[ ! -x "$PINCHTAB_INSTANCE_HELPER" ]]; then
    log "ERROR: Missing managed PinchTab helper: $PINCHTAB_INSTANCE_HELPER"
    return 1
  fi

  if ! IFS=$'\t' read -r PINCHTAB_INSTANCE_ID PINCHTAB_INSTANCE_STARTED \
    < <("$PINCHTAB_INSTANCE_HELPER" acquire opentable); then
    log "ERROR: Could not acquire a managed headless PinchTab instance"
    return 1
  fi

  if [[ -z "$PINCHTAB_INSTANCE_ID" ]]; then
    log "ERROR: Managed PinchTab helper returned no instance id"
    return 1
  fi
}

open_tab() {
  local url="$1"
  TAB_ID=$("$PINCHTAB_INSTANCE_HELPER" open "$PINCHTAB_INSTANCE_ID" "$url") || {
    log "ERROR: Could not create an isolated OpenTable browser tab"
    return 1
  }
  if [[ -z "$TAB_ID" ]]; then
    log "ERROR: Managed PinchTab helper returned no tab id"
    return 1
  fi
  sleep 3
}

find_ref() {
  local role="$1"
  local label="$2"
  local snapshot
  snapshot=$(pinchtab snap --tab "$TAB_ID" -i -c --max-tokens 6000 2>/dev/null || true)
  printf '%s' "$snapshot" | python3 -c '
import re
import sys

role, label = sys.argv[1:]
pattern = re.compile(r"^(e\d+):" + re.escape(role) + " \"" + re.escape(label) + "\"")
for line in sys.stdin.read().splitlines():
    match = pattern.match(line)
    if match:
        print(match.group(1))
        break
' "$role" "$label"
}

wait_for_ref() {
  local role="$1"
  local label="$2"
  local attempts="$3"
  local ref=""
  for _ in $(seq 1 "$attempts"); do
    ref=$(find_ref "$role" "$label")
    if [[ -n "$ref" ]]; then
      printf '%s\n' "$ref"
      return 0
    fi
    sleep 2
  done
  return 1
}

click_button() {
  local label="$1"
  local ref
  ref=$(wait_for_ref button "$label" 15) || {
    log "ERROR: Could not find button '$label'"
    return 1
  }
  pinchtab click "$ref" --tab "$TAB_ID" >/dev/null
}

fill_textbox() {
  local label="$1"
  local value="$2"
  local ref
  ref=$(wait_for_ref textbox "$label" 15) || {
    log "ERROR: Could not find textbox '$label'"
    return 1
  }
  pinchtab fill "$ref" "$value" --tab "$TAB_ID" >/dev/null
}

extract_atk() {
  pinchtab eval '(() => {
    const match = document.cookie.match(/(?:^|;\s*)authCke=([^;]*)/);
    if (!match) return "";
    const value = decodeURIComponent(match[1]);
    const atk = value.match(/(?:^|&)atk=([^&]+)/);
    return atk ? atk[1] : "";
  })()' --tab "$TAB_ID" --json 2>/dev/null | python3 -c '
import json
import sys

try:
    value = json.load(sys.stdin)
except Exception:
    raise SystemExit
result = value.get("result", value.get("data", "")) if isinstance(value, dict) else ""
if isinstance(result, str):
    print(result)
'
}

wait_for_atk() {
  local token=""
  for _ in $(seq 1 15); do
    token=$(extract_atk)
    if is_atk "$token"; then
      printf '%s\n' "$token"
      return 0
    fi
    sleep 2
  done
  return 1
}

extract_email_code() {
  local latest message_id message
  latest=$(gws gmail users messages list --account "$GWS_ACCOUNT" \
    --params '{"userId":"me","q":"from:opentable newer_than:10m","maxResults":1}' 2>/dev/null || true)
  message_id=$(printf '%s' "$latest" | python3 -c '
import json
import sys

try:
    data = json.load(sys.stdin)
except Exception:
    raise SystemExit
messages = data.get("messages") or []
if messages:
    print(messages[0].get("id", ""))
' 2>/dev/null || true)
  [[ -n "$message_id" ]] || return 1

  message=$(gws gmail users messages get --account "$GWS_ACCOUNT" \
    --params "{\"userId\":\"me\",\"id\":\"$message_id\",\"format\":\"full\"}" 2>/dev/null || true)
  printf '%s' "$message" | python3 -c '
import base64
import json
import re
import sys

try:
    message = json.load(sys.stdin)
except Exception:
    raise SystemExit

chunks = []
def visit(part):
    body = part.get("body") or {}
    data = body.get("data")
    if data:
        try:
            padded = data + "=" * (-len(data) % 4)
            chunks.append(base64.urlsafe_b64decode(padded).decode("utf-8", "replace"))
        except Exception:
            pass
    for child in part.get("parts") or []:
        visit(child)

visit(message.get("payload") or {})
text = "\n".join(chunks)
for pattern in (r"(?:code|verification|confirm)[^0-9]{0,80}(\d{4,8})", r"\b(\d{6})\b"):
    match = re.search(pattern, text, re.IGNORECASE)
    if match:
        print(match.group(1))
        break
' 2>/dev/null
}

wait_for_email_code() {
  local code=""
  for _ in $(seq 1 12); do
    code=$(extract_email_code || true)
    if [[ -n "$code" ]]; then
      printf '%s\n' "$code"
      return 0
    fi
    sleep 5
  done
  return 1
}

login_with_email() {
  click_button "Use email instead" || return 1
  fill_textbox "Email" "$OT_EMAIL" || return 1
  click_button "Continue" || return 1
  wait_for_ref textbox "Enter verification code" 20 >/dev/null || {
    log "ERROR: OpenTable did not reach the verification screen"
    return 1
  }

  local code
  code=$(wait_for_email_code) || {
    log "ERROR: Could not retrieve the OpenTable verification email"
    return 1
  }
  fill_textbox "Enter verification code" "$code" || return 1
  click_button "Continue" || return 1
  wait_for_atk
}

refresh_from_persisted_session() {
  local token=""
  open_tab "$OT_DASHBOARD_URL" || return 1
  token=$(wait_for_atk || true)
  if is_atk "$token" && install_and_validate_token "$token"; then
    log "OK: refreshed OpenTable token from the persisted browser session"
    return 0
  fi

  if is_atk "$token"; then
    log "Persisted browser token was not accepted by the OpenTable API"
  else
    log "No usable OpenTable token in the persisted browser session"
  fi
  close_tab
  return 1
}

refresh_with_email_login() {
  local token=""
  open_tab "$OT_LOGIN_URL" || return 1
  token=$(login_with_email || true)
  if is_atk "$token" && install_and_validate_token "$token"; then
    log "OK: refreshed OpenTable token through email verification"
    return 0
  fi
  log "ERROR: OpenTable login did not produce a working CLI token"
  return 1
}

main() {
  acquire_lock
  if [[ -r "$TOKEN_CACHE" ]]; then
    PREVIOUS_TOKEN=$(cat "$TOKEN_CACHE" 2>/dev/null || true)
  fi

  acquire_pinchtab_instance || exit 1
  refresh_from_persisted_session && return
  refresh_with_email_login && return
  exit 1
}

main "$@"
