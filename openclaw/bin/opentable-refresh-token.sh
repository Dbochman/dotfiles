#!/usr/bin/env bash
# opentable-refresh-token.sh - Refresh the OpenTable CLI token from the
# persisted Pinchtab browser profile. Falls back to email verification only
# when the browser session cannot provide a valid token.

set -euo pipefail

PATH="/opt/homebrew/bin:/opt/homebrew/opt/node@22/bin:/usr/local/bin:/usr/bin:/bin"
TOKEN_CACHE="$HOME/.cache/openclaw-gateway/opentable_auth_token"
BINDING_FILE="$HOME/.cache/openclaw-gateway/opentable_account_binding.json"
EXPECTED_ACCOUNT_FILE="$HOME/.cache/openclaw-gateway/opentable_expected_account.sha256"
EXPECTED_EMAIL_CACHE="$HOME/.cache/openclaw-gateway/opentable_email"
SECRETS_CACHE="$HOME/.openclaw/.secrets-cache"
RUNTIME_DIR="$HOME/.openclaw/run"
LOCK_DIR="$RUNTIME_DIR/opentable-refresh.lock"
OT_EMAIL=""
GWS_ACCOUNT=""
OT_DASHBOARD_URL="https://www.opentable.com/user/dining-dashboard"
OT_LOGIN_URL="https://www.opentable.com/authenticate/start?isPopup=false"
PINCHTAB_INSTANCE_HELPER="$HOME/.openclaw/bin/pinchtab-headless-instance"
PINCHTAB_INSTANCE_ID=""
PINCHTAB_INSTANCE_STARTED=0
TAB_ID=""
PREVIOUS_TOKEN=""
PREVIOUS_BINDING_BACKUP=""
LOCK_HELD=0

umask 077

log() {
  printf '[%s] %s\n' "$(date '+%H:%M:%S')" "$*" >&2
}

close_tab() {
  if [[ -n "$PINCHTAB_INSTANCE_ID" && -n "$TAB_ID" ]]; then
    "$PINCHTAB_INSTANCE_HELPER" close "$PINCHTAB_INSTANCE_ID" "$TAB_ID" >/dev/null 2>&1 || true
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
  if [[ -n "$PREVIOUS_BINDING_BACKUP" ]]; then
    rm -f "$PREVIOUS_BINDING_BACKUP"
    PREVIOUS_BINDING_BACKUP=""
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

if [[ ! -f "$SECRETS_CACHE" || -L "$SECRETS_CACHE" ]]; then
  log "Protected cache must be a regular non-symlink file"
  exit 1
fi
read -r cache_owner cache_mode < <(stat -f '%u %Lp' "$SECRETS_CACHE")
if [[ "$cache_owner" != "$(id -u)" || "$cache_mode" != "600" ]]; then
  log "Protected cache must be owned by the current user with mode 0600"
  exit 1
fi
set -a
if ! . "$SECRETS_CACHE" >/dev/null 2>&1; then
  set +a
  log "Protected cache could not be loaded"
  exit 1
fi
set +a
: "${OPENTABLE_EMAIL:?OPENTABLE_EMAIL is missing from the protected cache}"
OT_EMAIL="$OPENTABLE_EMAIL"
GWS_ACCOUNT="$OPENTABLE_EMAIL"

is_atk() {
  [[ "$1" =~ ^[A-Za-z0-9_-]{20,}$ && ${#1} -le 512 ]]
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

write_expected_identity() {
  mkdir -p "$(dirname "$EXPECTED_ACCOUNT_FILE")"
  printf '%s' "$OT_EMAIL" | python3 -c '
import hashlib
import os
import pathlib
import re
import sys
import tempfile

path = pathlib.Path(sys.argv[1])
email_path = pathlib.Path(sys.argv[2])
identity = sys.stdin.read().strip().casefold()
if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", identity):
    raise SystemExit(1)
def write_atomic(target, value):
    fd, temporary = tempfile.mkstemp(prefix=f".{target.name}.", dir=target.parent)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(value)
        os.replace(temporary, target)
    except BaseException:
        try:
            os.close(fd)
        except OSError:
            pass
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise
write_atomic(path, hashlib.sha256(identity.encode()).hexdigest() + "\n")
write_atomic(email_path, identity)
' "$EXPECTED_ACCOUNT_FILE" "$EXPECTED_EMAIL_CACHE"
}

snapshot_previous_binding() {
  if [[ -f "$BINDING_FILE" && ! -L "$BINDING_FILE" ]] \
    && [[ "$(stat -f '%u %Lp' "$BINDING_FILE" 2>/dev/null || true)" == "$(id -u) 600" ]]; then
    PREVIOUS_BINDING_BACKUP=$(mktemp "${RUNTIME_DIR}/opentable-binding.backup.XXXXXX")
    cp "$BINDING_FILE" "$PREVIOUS_BINDING_BACKUP"
    chmod 600 "$PREVIOUS_BINDING_BACKUP"
  fi
}

restore_previous_binding() {
  if [[ -n "$PREVIOUS_BINDING_BACKUP" && -f "$PREVIOUS_BINDING_BACKUP" ]]; then
    local temporary
    temporary=$(mktemp "${BINDING_FILE}.tmp.XXXXXX")
    cp "$PREVIOUS_BINDING_BACKUP" "$temporary"
    chmod 600 "$temporary"
    mv -f "$temporary" "$BINDING_FILE"
  else
    rm -f "$BINDING_FILE"
  fi
}

write_account_binding() {
  local token="$1"
  local proof="$2"
  printf '%s' "$token" | python3 -c '
import datetime
import hashlib
import json
import os
import pathlib
import re
import stat
import sys
import tempfile

binding_path = pathlib.Path(sys.argv[1])
expected_path = pathlib.Path(sys.argv[2])
proof = sys.argv[3]
token = sys.stdin.read().strip()
metadata = expected_path.lstat()
if not stat.S_ISREG(metadata.st_mode) or metadata.st_uid != os.getuid() or stat.S_IMODE(metadata.st_mode) != 0o600:
    raise SystemExit(1)
expected_hash = expected_path.read_text(encoding="utf-8").strip()
if proof not in {"email_otp", "browser_email_hash"}:
    raise SystemExit(1)
if not re.fullmatch(r"[a-f0-9]{64}", expected_hash) or not re.fullmatch(r"[A-Za-z0-9_-]{20,512}", token):
    raise SystemExit(1)
payload = {
    "schema_version": 1,
    "profile": "opentable",
    "verified_via": proof,
    "account_sha256": expected_hash,
    "token_sha256": hashlib.sha256(token.encode()).hexdigest(),
    "verified_at": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
}
fd, temporary = tempfile.mkstemp(prefix=f".{binding_path.name}.", dir=binding_path.parent)
try:
    os.fchmod(fd, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, separators=(",", ":"), sort_keys=True)
        handle.write("\n")
    os.replace(temporary, binding_path)
except BaseException:
    try:
        os.close(fd)
    except OSError:
        pass
    try:
        os.unlink(temporary)
    except OSError:
        pass
    raise
' "$BINDING_FILE" "$EXPECTED_ACCOUNT_FILE" "$proof"
}

binding_matches() {
  local token="$1"
  printf '%s' "$token" | python3 -c '
import hashlib
import hmac
import json
import os
import pathlib
import re
import stat
import sys

binding_path = pathlib.Path(sys.argv[1])
expected_path = pathlib.Path(sys.argv[2])
token = sys.stdin.read().strip()
for path in (binding_path, expected_path):
    metadata = path.lstat()
    if not stat.S_ISREG(metadata.st_mode) or metadata.st_uid != os.getuid() or stat.S_IMODE(metadata.st_mode) != 0o600:
        raise SystemExit(1)
expected_hash = expected_path.read_text(encoding="utf-8").strip()
binding = json.loads(binding_path.read_text(encoding="utf-8"))
token_hash = hashlib.sha256(token.encode()).hexdigest()
valid = (
    re.fullmatch(r"[a-f0-9]{64}", expected_hash)
    and re.fullmatch(r"[A-Za-z0-9_-]{20,512}", token)
    and isinstance(binding, dict)
    and binding.get("schema_version") == 1
    and binding.get("profile") == "opentable"
    and binding.get("verified_via") in {"email_otp", "browser_email_hash"}
    and hmac.compare_digest(str(binding.get("account_sha256", "")), expected_hash)
    and hmac.compare_digest(str(binding.get("token_sha256", "")), token_hash)
)
raise SystemExit(0 if valid else 1)
' "$BINDING_FILE" "$EXPECTED_ACCOUNT_FILE" 2>/dev/null
}

restore_previous_token() {
  if [[ -n "$PREVIOUS_TOKEN" ]]; then
    write_token "$PREVIOUS_TOKEN"
  else
    rm -f "$TOKEN_CACHE"
  fi
  restore_previous_binding
}

validate_cached_token() {
  local candidate="$1"
  printf '%s' "$candidate" | opentable _validate-refresh-candidate >/dev/null 2>&1
}

install_and_validate_token() {
  local candidate="$1"
  local proof="$2"
  if [[ "$proof" != "persisted" && "$proof" != "email_otp" && "$proof" != "browser_email_hash" ]]; then
    return 1
  fi
  if [[ "$proof" == "persisted" ]] && ! binding_matches "$candidate"; then
    return 1
  fi
  write_token "$candidate"
  if validate_cached_token "$candidate"; then
    if [[ "$proof" != "persisted" ]] && ! write_account_binding "$candidate" "$proof"; then
      restore_previous_token
      return 1
    fi
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
  snapshot=$("$PINCHTAB_INSTANCE_HELPER" snap "$PINCHTAB_INSTANCE_ID" "$TAB_ID" 2>/dev/null || true)
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
  "$PINCHTAB_INSTANCE_HELPER" click "$PINCHTAB_INSTANCE_ID" "$TAB_ID" "$ref" >/dev/null
}

fill_textbox() {
  local label="$1"
  local value="$2"
  local ref
  ref=$(wait_for_ref textbox "$label" 15) || {
    log "ERROR: Could not find textbox '$label'"
    return 1
  }
  "$PINCHTAB_INSTANCE_HELPER" fill "$PINCHTAB_INSTANCE_ID" "$TAB_ID" "$ref" "$value" >/dev/null
}

extract_atk() {
  "$PINCHTAB_INSTANCE_HELPER" eval "$PINCHTAB_INSTANCE_ID" "$TAB_ID" '(() => {
    const match = document.cookie.match(/(?:^|;\s*)authCke=([^;]*)/);
    if (!match) return "";
    const value = decodeURIComponent(match[1]);
    const atk = value.match(/(?:^|&)atk=([^&]+)/);
    return atk ? atk[1] : "";
  })()' 2>/dev/null | python3 -c '
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
  local attempts="${1:-15}"
  local token=""
  for _ in $(seq 1 "$attempts"); do
    token=$(extract_atk)
    if is_atk "$token"; then
      printf '%s\n' "$token"
      return 0
    fi
    sleep 2
  done
  return 1
}

extract_browser_identity_hash() {
  "$PINCHTAB_INSTANCE_HELPER" eval "$PINCHTAB_INSTANCE_ID" "$TAB_ID" '(() => {
    const value=String(window.__INITIAL_STATE__?.header?.userProfile?.emailHash || "").trim().toLowerCase();
    return /^[a-f0-9]{64}$/.test(value) ? value : "";
  })()' 2>/dev/null | python3 -c '
import json
import re
import sys

try:
    value = json.load(sys.stdin)
except Exception:
    raise SystemExit(1)
result = value.get("result", value.get("data", "")) if isinstance(value, dict) else ""
if isinstance(result, str) and re.fullmatch(r"[a-f0-9]{64}", result):
    print(result)
'
}

browser_identity_matches_expected() {
  local browser_hash="$1"
  printf '%s' "$browser_hash" | python3 -c '
import hmac
import os
import pathlib
import re
import stat
import sys

path = pathlib.Path(sys.argv[1])
candidate = sys.stdin.read().strip()
metadata = path.lstat()
if not stat.S_ISREG(metadata.st_mode) or metadata.st_uid != os.getuid() or stat.S_IMODE(metadata.st_mode) != 0o600:
    raise SystemExit(1)
expected = path.read_text(encoding="utf-8").strip()
valid = re.fullmatch(r"[a-f0-9]{64}", candidate) and re.fullmatch(r"[a-f0-9]{64}", expected)
raise SystemExit(0 if valid and hmac.compare_digest(candidate, expected) else 1)
' "$EXPECTED_ACCOUNT_FILE" 2>/dev/null
}

wait_for_expected_browser_identity() {
  local browser_hash=""
  for _ in $(seq 1 15); do
    browser_hash=$(extract_browser_identity_hash || true)
    if [[ -n "$browser_hash" ]] && browser_identity_matches_expected "$browser_hash"; then
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
  # OpenTable may auto-submit once the final code digit is filled. Treat the
  # resulting auth token as success instead of requiring a button that has
  # already disappeared during the authenticated redirect.
  local token
  token=$(wait_for_atk 5 || true)
  if is_atk "$token"; then
    printf '%s\n' "$token"
    return 0
  fi
  click_button "Continue" || {
    token=$(wait_for_atk 3 || true)
    if is_atk "$token"; then
      printf '%s\n' "$token"
      return 0
    fi
    return 1
  }
  wait_for_atk
}

refresh_from_persisted_session() {
  local token=""
  local proof="browser_email_hash"
  open_tab "$OT_DASHBOARD_URL" || return 1
  token=$(wait_for_atk || true)
  if is_atk "$token" && wait_for_expected_browser_identity; then
    if binding_matches "$token"; then
      proof="persisted"
    fi
  else
    token=""
  fi
  if is_atk "$token" && install_and_validate_token "$token" "$proof"; then
    log "OK: refreshed OpenTable token from the persisted browser session"
    return 0
  fi

  if is_atk "$token"; then
    log "Persisted browser token was not bound to the expected account or was not accepted"
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
  if is_atk "$token" \
    && "$PINCHTAB_INSTANCE_HELPER" navigate "$PINCHTAB_INSTANCE_ID" "$TAB_ID" "$OT_DASHBOARD_URL" >/dev/null 2>&1 \
    && wait_for_expected_browser_identity \
    && install_and_validate_token "$token" email_otp; then
    log "OK: refreshed OpenTable token through email verification"
    return 0
  fi
  log "ERROR: OpenTable login did not produce a working CLI token"
  return 1
}

main() {
  acquire_lock
  mkdir -p "$RUNTIME_DIR" "$(dirname "$BINDING_FILE")"
  if [[ -r "$TOKEN_CACHE" ]]; then
    PREVIOUS_TOKEN=$(cat "$TOKEN_CACHE" 2>/dev/null || true)
  fi
  snapshot_previous_binding
  if ! write_expected_identity; then
    log "ERROR: Could not establish protected OpenTable account identity"
    exit 1
  fi

  acquire_pinchtab_instance || exit 1
  refresh_from_persisted_session && return
  refresh_with_email_login && return
  exit 1
}

if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
  main "$@"
fi
