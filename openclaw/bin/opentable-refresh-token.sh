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
STATUS_FILE="$RUNTIME_DIR/opentable-refresh-status.json"
SCHEDULE_WINDOW_SECONDS=21600
MAX_SCHEDULED_ATTEMPTS=2
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
ATTEMPT_KIND="manual"
ATTEMPT_NUMBER=1
ATTEMPT_STARTED=0
STATUS_FINALIZED=0
STARTED_AT=""
CURRENT_STAGE="initialization"
FAILURE_CODE="unexpected_failure"

umask 077

log() {
  printf '[%s] %s\n' "$(date '+%H:%M:%S')" "$*" >&2
}

set_failure_context() {
  CURRENT_STAGE="$1"
  FAILURE_CODE="$2"
}

scheduled_decision() {
  python3 - "$STATUS_FILE" "$SCHEDULE_WINDOW_SECONDS" "$MAX_SCHEDULED_ATTEMPTS" <<'PY'
import datetime
import json
import os
import pathlib
import re
import stat
import sys

path = pathlib.Path(sys.argv[1])
window_seconds = int(sys.argv[2])
maximum_attempts = int(sys.argv[3])
try:
    metadata = path.lstat()
    if (
        not stat.S_ISREG(metadata.st_mode)
        or metadata.st_uid != os.getuid()
        or stat.S_IMODE(metadata.st_mode) != 0o600
        or metadata.st_nlink != 1
        or metadata.st_size <= 0
        or metadata.st_size > 4096
    ):
        raise ValueError
    value = json.loads(path.read_text(encoding="utf-8"))
    if set(value) != {
        "schema_version", "attempt_kind", "attempt_number", "outcome", "stage",
        "reason_code", "started_at", "completed_at", "last_success_at",
    } or value["schema_version"] != 1:
        raise ValueError
    attempt_kind = value["attempt_kind"]
    attempt_number = value["attempt_number"]
    outcome = value["outcome"]
    if attempt_kind not in {"manual", "scheduled"}:
        raise ValueError
    if (
        not isinstance(attempt_number, int)
        or isinstance(attempt_number, bool)
        or attempt_number not in range(1, maximum_attempts + 1)
    ):
        raise ValueError
    if outcome not in {"success", "failed"}:
        raise ValueError
    code = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
    if code.fullmatch(value["stage"]) is None or code.fullmatch(value["reason_code"]) is None:
        raise ValueError
    timestamps = (value["started_at"], value["completed_at"])
    if value["last_success_at"] is not None:
        timestamps += (value["last_success_at"],)
    parsed_timestamps = []
    for timestamp in timestamps:
        if not isinstance(timestamp, str):
            raise ValueError
        parsed = datetime.datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            raise ValueError
        parsed_timestamps.append(parsed.astimezone(datetime.timezone.utc))
    completed = parsed_timestamps[1]
except (OSError, UnicodeError, KeyError, TypeError, ValueError, json.JSONDecodeError):
    print("attempt:1")
    raise SystemExit

age = (datetime.datetime.now(datetime.timezone.utc) - completed).total_seconds()
if age < -300 or age > window_seconds:
    print("attempt:1")
elif outcome == "success":
    print("skip")
elif attempt_kind == "scheduled" and attempt_number >= maximum_attempts:
    print("exhausted")
elif attempt_kind == "scheduled":
    print(f"attempt:{attempt_number + 1}")
else:
    print("attempt:1")
PY
}

write_status() {
  local outcome="$1" stage="$2" reason_code="$3" completed_at
  completed_at=$(date -u +%Y-%m-%dT%H:%M:%SZ)
  python3 - "$STATUS_FILE" "$outcome" "$stage" "$reason_code" \
    "$STARTED_AT" "$completed_at" "$ATTEMPT_KIND" "$ATTEMPT_NUMBER" <<'PY'
import datetime
import json
import os
import pathlib
import re
import stat
import sys
import tempfile

path = pathlib.Path(sys.argv[1])
outcome, stage, reason, started_at, completed_at, attempt_kind = sys.argv[2:8]
attempt_number = int(sys.argv[8])
code = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
if (
    outcome not in {"success", "failed"}
    or attempt_kind not in {"manual", "scheduled"}
    or attempt_number not in {1, 2}
    or code.fullmatch(stage) is None
    or code.fullmatch(reason) is None
):
    raise SystemExit(1)
for timestamp in (started_at, completed_at):
    parsed = datetime.datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise SystemExit(1)

last_success = None
try:
    metadata = path.lstat()
    if (
        stat.S_ISREG(metadata.st_mode)
        and metadata.st_uid == os.getuid()
        and stat.S_IMODE(metadata.st_mode) == 0o600
        and metadata.st_nlink == 1
        and 0 < metadata.st_size <= 4096
    ):
        previous = json.loads(path.read_text(encoding="utf-8"))
        candidate = previous.get("last_success_at")
        if candidate is not None:
            parsed = datetime.datetime.fromisoformat(candidate.replace("Z", "+00:00"))
            if parsed.tzinfo is not None:
                last_success = candidate
except (
    AttributeError,
    OSError,
    UnicodeError,
    TypeError,
    ValueError,
    json.JSONDecodeError,
):
    pass
if outcome == "success":
    last_success = completed_at

payload = {
    "schema_version": 1,
    "attempt_kind": attempt_kind,
    "attempt_number": attempt_number,
    "outcome": outcome,
    "stage": stage,
    "reason_code": reason,
    "started_at": started_at,
    "completed_at": completed_at,
    "last_success_at": last_success,
}
path.parent.mkdir(parents=True, exist_ok=True)
fd, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
temporary = pathlib.Path(temporary_name)
try:
    os.fchmod(fd, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8", closefd=True) as handle:
        json.dump(payload, handle, separators=(",", ":"), sort_keys=True)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)
    os.chmod(path, 0o600, follow_symlinks=False)
finally:
    try:
        temporary.unlink()
    except FileNotFoundError:
        pass
PY
}

begin_attempt() {
  ATTEMPT_STARTED=1
  STARTED_AT=$(date -u +%Y-%m-%dT%H:%M:%SZ)
  set_failure_context initialization unexpected_failure
}

finish_status() {
  local outcome="$1" stage="$2" reason_code="$3"
  if ! write_status "$outcome" "$stage" "$reason_code"; then
    set_failure_context status_write status_write_failed
    log "ERROR: Could not persist safe OpenTable refresh status"
    return 1
  fi
  STATUS_FINALIZED=1
}

prepare_runtime_dir() {
  mkdir -p "$RUNTIME_DIR"
  if [[ ! -d "$RUNTIME_DIR" || -L "$RUNTIME_DIR" ]]; then
    log "ERROR: OpenTable runtime directory must be a non-symlink directory"
    return 1
  fi
  local runtime_owner
  runtime_owner=$(stat -f '%u' "$RUNTIME_DIR") || return 1
  if [[ "$runtime_owner" != "$(id -u)" ]]; then
    log "ERROR: OpenTable runtime directory must be owned by the current user"
    return 1
  fi
  chmod 700 "$RUNTIME_DIR"
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

on_exit() {
  local exit_code="$1"
  trap - EXIT
  set +e
  if [[ "$ATTEMPT_STARTED" == "1" && "$STATUS_FINALIZED" != "1" ]]; then
    write_status failed "$CURRENT_STAGE" "$FAILURE_CODE" || true
  fi
  cleanup
  exit "$exit_code"
}

acquire_lock() {
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

trap 'on_exit $?' EXIT

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
  click_button "Use email instead" || return 11
  fill_textbox "Email" "$OT_EMAIL" || return 12
  click_button "Continue" || return 13
  wait_for_ref textbox "Enter verification code" 20 >/dev/null || {
    log "ERROR: OpenTable did not reach the verification screen"
    return 14
  }

  local code
  code=$(wait_for_email_code) || {
    log "ERROR: Could not retrieve the OpenTable verification email"
    return 15
  }
  fill_textbox "Enter verification code" "$code" || return 16
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
    return 17
  }
  wait_for_atk || return 17
}

set_login_failure_context() {
  case "$1" in
    11) set_failure_context email_method email_method_unavailable ;;
    12) set_failure_context email_address email_address_unavailable ;;
    13) set_failure_context email_continue email_continue_unavailable ;;
    14) set_failure_context email_verification_screen verification_screen_unavailable ;;
    15) set_failure_context email_verification_email verification_email_unavailable ;;
    16) set_failure_context email_verification_submit verification_submit_failed ;;
    17) set_failure_context email_token token_unavailable ;;
    *) set_failure_context email_login email_login_failed ;;
  esac
}

refresh_from_persisted_session() {
  local token=""
  local proof="browser_email_hash"
  set_failure_context persisted_open browser_open_failed
  open_tab "$OT_DASHBOARD_URL" || return 1
  set_failure_context persisted_session persisted_session_unusable
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
  local token="" login_status=0
  set_failure_context email_open browser_open_failed
  open_tab "$OT_LOGIN_URL" || return 1
  if token=$(login_with_email); then
    login_status=0
  else
    login_status=$?
  fi
  if [[ "$login_status" -ne 0 ]]; then
    set_login_failure_context "$login_status"
    log "ERROR: OpenTable login did not produce a working CLI token"
    return 1
  fi
  if ! is_atk "$token"; then
    set_failure_context email_token token_invalid
    return 1
  fi
  set_failure_context dashboard_navigation dashboard_navigation_failed
  if ! "$PINCHTAB_INSTANCE_HELPER" navigate "$PINCHTAB_INSTANCE_ID" "$TAB_ID" "$OT_DASHBOARD_URL" >/dev/null 2>&1; then
    return 1
  fi
  set_failure_context account_identity account_identity_unverified
  if ! wait_for_expected_browser_identity; then
    return 1
  fi
  set_failure_context token_install token_rejected
  if ! install_and_validate_token "$token" email_otp; then
    return 1
  fi
  log "OK: refreshed OpenTable token through email verification"
}

main() {
  local scheduled=0 decision
  if [[ "${1:-}" == "--scheduled" && $# -eq 1 ]]; then
    scheduled=1
    ATTEMPT_KIND="scheduled"
  elif [[ $# -ne 0 ]]; then
    log "Usage: opentable-refresh-token.sh [--scheduled]"
    return 2
  fi
  prepare_runtime_dir || return 1
  acquire_lock
  mkdir -p "$(dirname "$BINDING_FILE")"
  if [[ "$scheduled" == "1" ]]; then
    decision=$(scheduled_decision)
    case "$decision" in
      skip)
        log "Recent successful OpenTable refresh; scheduled retry is not needed"
        return 0
        ;;
      exhausted)
        log "ERROR: OpenTable scheduled retry budget is exhausted"
        return 1
        ;;
      attempt:1) ATTEMPT_NUMBER=1 ;;
      attempt:2) ATTEMPT_NUMBER=2 ;;
      *)
        log "ERROR: OpenTable retry state is invalid"
        return 1
        ;;
    esac
  fi
  begin_attempt
  if [[ -r "$TOKEN_CACHE" ]]; then
    PREVIOUS_TOKEN=$(cat "$TOKEN_CACHE" 2>/dev/null || true)
  fi
  snapshot_previous_binding
  set_failure_context protected_identity protected_identity_failed
  if ! write_expected_identity; then
    log "ERROR: Could not establish protected OpenTable account identity"
    return 1
  fi

  set_failure_context browser_acquire browser_acquire_failed
  acquire_pinchtab_instance || return 1
  if refresh_from_persisted_session; then
    finish_status success persisted_session completed || return 1
    return 0
  fi
  if refresh_with_email_login; then
    finish_status success email_verification completed || return 1
    return 0
  fi
  log "ERROR: OpenTable refresh failed stage=$CURRENT_STAGE reason=$FAILURE_CODE attempt=$ATTEMPT_NUMBER/$MAX_SCHEDULED_ATTEMPTS"
  finish_status failed "$CURRENT_STAGE" "$FAILURE_CODE" || return 1
  return 1
}

if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
  main "$@"
fi
