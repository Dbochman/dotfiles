#!/usr/bin/env bash
# cielo-refresh.sh — Cielo token refresh with auto-login fallback
#
# Method 1: API refresh using stored refreshToken (fast, no browser)
# Method 2: Browser CDP capture (pinchtab + persisted cookies)
# Method 3: Explicitly opted-in headless login with username/password
#
# Runs as a LaunchAgent every 30 minutes.

# Load credentials (must come before variable expansion)
if [[ -f "$HOME/.openclaw/.secrets-cache" ]]; then
  set -a; source "$HOME/.openclaw/.secrets-cache"; set +a
fi
export PATH=/opt/homebrew/bin:/usr/local/bin:/usr/sbin:/usr/bin:/bin

CONFIG_FILE="$HOME/.config/cielo/config.json"
API_HOST="api.smartcielo.com"
API_KEY="${CIELO_API_KEY:?CIELO_API_KEY not set}"
AUTH_HELPER="$HOME/.openclaw/bin/cielo-auth.py"
GRAB_SCRIPT="$HOME/.openclaw/workspace/scripts/grab-cielo-tokens.py"
PINCHTAB="/opt/homebrew/bin/pinchtab"
PINCHTAB_PROFILE="${CIELO_PINCHTAB_PROFILE:-cielo}"
BACKOFF_FILE="$HOME/.openclaw/state/cielo-headless-login-backoff.json"
HEADLESS_LOGIN_BACKOFF_SECONDS="${CIELO_HEADLESS_LOGIN_BACKOFF_SECONDS:-21600}"
RUN_STARTED_AT_MS=$(python3 -c 'import time; print(int(time.time() * 1000))')

clear_headless_login_backoff() {
  python3 - "$BACKOFF_FILE" <<'PY'
from pathlib import Path
import sys

path = Path(sys.argv[1])
try:
    if path.is_file() and not path.is_symlink():
        path.unlink()
except OSError:
    pass
PY
}

headless_login_backoff_status() {
  python3 - "$BACKOFF_FILE" <<'PY'
import json
from pathlib import Path
import stat
import sys
import time

path = Path(sys.argv[1])
try:
info = path.lstat()
if not stat.S_ISREG(info.st_mode) or path.is_symlink() or info.st_uid != __import__('os').getuid():
        raise ValueError
    if stat.S_IMODE(info.st_mode) & 0o077:
        raise ValueError
    payload = json.loads(path.read_text(encoding='utf-8'))
    next_attempt = int(payload.get('nextAttemptAt', 0))
except (FileNotFoundError, OSError, ValueError, TypeError, json.JSONDecodeError):
    raise SystemExit(1)
if next_attempt <= int(time.time()):
    raise SystemExit(1)
print(json.dumps({
    'success': False,
    'error': 'Headless login is in bounded backoff; attended Cielo recovery is required.',
    'error_kind': 'attended_reauthentication_required',
    'nextAttemptAt': next_attempt,
}, separators=(',', ':')))
PY
}

record_headless_login_backoff() {
  local reason="$1"
  BACKOFF_REASON="$reason" python3 - "$BACKOFF_FILE" "$HEADLESS_LOGIN_BACKOFF_SECONDS" <<'PY'
import json
import os
from pathlib import Path
import tempfile
import time
import sys

path = Path(sys.argv[1])
try:
    seconds = int(sys.argv[2])
except ValueError:
    seconds = 21600
if seconds < 300 or seconds > 86400:
    seconds = 21600
path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
payload = {
    'reason': os.environ.get('BACKOFF_REASON', 'login_not_completed'),
    'recordedAt': int(time.time()),
    'nextAttemptAt': int(time.time()) + seconds,
}
fd, temporary = tempfile.mkstemp(prefix=f'.{path.name}.', dir=path.parent)
temporary_path = Path(temporary)
try:
    os.fchmod(fd, 0o600)
    with os.fdopen(fd, 'w', encoding='utf-8') as handle:
        json.dump(payload, handle, separators=(',', ':'), sort_keys=True)
        handle.write('\n')
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary_path, path)
    os.chmod(path, 0o600)
finally:
    try:
        temporary_path.unlink()
    except FileNotFoundError:
        pass
PY
}

# ── Method 1: API refresh token ─────────────────────────────────────────────
if [[ ! -x "$AUTH_HELPER" ]]; then
  echo '{"success":false,"error":"Cielo auth helper is unavailable","error_kind":"configuration_error"}'
  exit 1
fi

AUTH_RESULT=$("$AUTH_HELPER" refresh --force 2>&1)
AUTH_EXIT=$?
if [[ -n "$AUTH_RESULT" ]]; then
  printf '%s\n' "$AUTH_RESULT"
fi
if [[ $AUTH_EXIT -eq 0 ]]; then
  clear_headless_login_backoff
  exit 0
fi

AUTH_CATEGORY=$(printf '%s' "$AUTH_RESULT" | python3 -c "
import json, sys
try:
    print(json.loads(sys.stdin.read()).get('category', 'unknown'))
except Exception:
    print('unknown')
" 2>/dev/null)
case "$AUTH_CATEGORY" in
  network_error|rate_limited|server_error|lock_timeout|lock_unavailable)
    echo '{"success":false,"error":"Cielo API refresh had a retryable failure; browser fallback was skipped.","error_kind":"retryable_refresh_failure"}'
    exit 1
    ;;
esac

# ── Start pinchtab ──────────────────────────────────────────────────────────
STARTED_PINCHTAB_INSTANCE=false
PINCHTAB_INSTANCE_ID=""
PINCHTAB_INSTANCE_URL=""
PINCHTAB_PROFILE_PATH=""
CIELO_TAB_ID=""
PASSIVE_GRAB_PID=""

cleanup() {
  if [[ -n "$PASSIVE_GRAB_PID" ]] && kill -0 "$PASSIVE_GRAB_PID" 2>/dev/null; then
    kill "$PASSIVE_GRAB_PID" 2>/dev/null || true
    wait "$PASSIVE_GRAB_PID" 2>/dev/null || true
  fi
  if [[ -n "$CIELO_TAB_ID" && -n "$PINCHTAB_INSTANCE_URL" ]]; then
    "$PINCHTAB" --server "$PINCHTAB_INSTANCE_URL" \
      close "$CIELO_TAB_ID" >/dev/null 2>&1 || true
  fi
  if [[ "$STARTED_PINCHTAB_INSTANCE" == true ]] && [[ -n "$PINCHTAB_INSTANCE_ID" ]]; then
    "$PINCHTAB" instance stop "$PINCHTAB_INSTANCE_ID" >/dev/null 2>&1 || true
  fi
}

if ! "$PINCHTAB" health >/dev/null 2>&1; then
  echo '{"success":false,"error":"PinchTab server is unavailable"}'
  exit 1
fi

PINCHTAB_PROFILE_PATH=$("$PINCHTAB" profiles --json 2>/dev/null | python3 -c "
import json, sys
try:
    profiles = json.load(sys.stdin)
    print(next((p.get('path', '') for p in profiles if p.get('name') == '$PINCHTAB_PROFILE'), ''))
except Exception:
    print('')
" 2>/dev/null)

if [[ -z "$PINCHTAB_PROFILE_PATH" ]]; then
  echo '{"success":false,"error":"PinchTab profile not found: '"$PINCHTAB_PROFILE"'"}'
  exit 1
fi

read -r PINCHTAB_INSTANCE_ID PINCHTAB_INSTANCE_MODE < <("$PINCHTAB" instances --json 2>/dev/null | python3 -c "
import json, sys
try:
    instances = json.load(sys.stdin)
    match = next((i for i in instances if i.get('profileName') == '$PINCHTAB_PROFILE' and i.get('status') in ('starting', 'running')), {})
    print(match.get('id', ''), match.get('mode', ''))
except Exception:
    print('', '')
" 2>/dev/null)

if [[ -n "$PINCHTAB_INSTANCE_ID" && "$PINCHTAB_INSTANCE_MODE" != "headless" ]]; then
  echo '{"success":false,"error":"Refusing Cielo browser fallback while the PinchTab profile is visible"}'
  exit 1
fi

if [[ -z "$PINCHTAB_INSTANCE_ID" ]]; then
  START_OUTPUT=$("$PINCHTAB" instance start --profile "$PINCHTAB_PROFILE" --mode headless 2>/dev/null)
  PINCHTAB_INSTANCE_ID=$(printf '%s' "$START_OUTPUT" | python3 -c "
import json, sys
try:
    print(json.load(sys.stdin).get('id', ''))
except Exception:
    print('')
" 2>/dev/null)
  if [[ -z "$PINCHTAB_INSTANCE_ID" ]]; then
    echo '{"success":false,"error":"Could not start headless PinchTab instance"}'
    exit 1
  fi
  STARTED_PINCHTAB_INSTANCE=true
fi

for _ in $(seq 1 15); do
  INSTANCE_STATUS=$("$PINCHTAB" instances --json 2>/dev/null | python3 -c "
import json, sys
try:
    instances = json.load(sys.stdin)
    print(next((i.get('status', '') for i in instances if i.get('id') == '$PINCHTAB_INSTANCE_ID'), ''))
except Exception:
    print('')
" 2>/dev/null)
  [[ "$INSTANCE_STATUS" == "running" ]] && break
  sleep 1
done

if [[ "${INSTANCE_STATUS:-}" != "running" ]]; then
  echo '{"success":false,"error":"Headless PinchTab instance did not become ready"}'
  cleanup; exit 1
fi

PINCHTAB_INSTANCE_URL=$("$PINCHTAB" instances --json 2>/dev/null | python3 -c "
import json, sys
try:
    instances = json.load(sys.stdin)
    print(next((i.get('url', '') for i in instances if i.get('id') == '$PINCHTAB_INSTANCE_ID'), ''))
except Exception:
    print('')
" 2>/dev/null)
case "$PINCHTAB_INSTANCE_URL" in
  http://127.0.0.1:*|http://localhost:*) ;;
  *)
    echo '{"success":false,"error":"PinchTab instance endpoint is unavailable"}'
    cleanup; exit 1
    ;;
esac

# Open an isolated Cielo tab through the acquired instance's exact endpoint.
# The control-plane instance-navigate command can report a tab ID before the
# headed/headless instance has registered the tab, which leaves CDP capture
# pointed at a target that never becomes usable.
NAV_OUTPUT=$("$PINCHTAB" --server "$PINCHTAB_INSTANCE_URL" \
  nav "https://home.cielowigle.com/" --new-tab --print-tab-id 2>/dev/null)
CIELO_TAB_ID=$(printf '%s' "$NAV_OUTPUT" | python3 -c "
import json, re, sys
text = sys.stdin.read().strip()
tab_id = ''
try:
    payload = json.loads(text)
except Exception:
    payload = None
if isinstance(payload, dict):
    tab_id = payload.get('tabId') or payload.get('id') or ''
if not tab_id and re.fullmatch(r'[A-Za-z0-9_.:-]{4,160}', text):
    tab_id = text
if not tab_id:
    match = re.search(r'\"tabId\"\s*:\s*\"([^\"]+)\"', text)
    tab_id = match.group(1) if match else ''
print(tab_id if isinstance(tab_id, str) else '')
" 2>/dev/null)

if [[ -z "$CIELO_TAB_ID" ]]; then
  echo '{"success":false,"error":"Could not open an isolated Cielo browser tab"}'
  cleanup; exit 1
fi

# Wait for the Angular SPA to load and settle (it may redirect to login).
sleep 12

# Find Chrome CDP port
CDP_PORT=""
for _ in $(seq 1 15); do
  CDP_PORT=$(python3 - "$PINCHTAB_PROFILE_PATH" <<'PY'
import re
import subprocess
import sys

profile_path = sys.argv[1]
ps = subprocess.check_output(['ps', 'aux'], text=True)
for line in ps.splitlines():
    if '--remote-debugging-port=' not in line or '--type=' in line:
        continue
    if f'--user-data-dir={profile_path}' not in line:
        continue
    pid = line.split()[1]
    command_port = re.search(r'--remote-debugging-port=(\d+)', line)
    if command_port and command_port.group(1) != '0':
        print(command_port.group(1))
        raise SystemExit
    try:
        sockets = subprocess.check_output(
            ['/usr/sbin/lsof', '-anP', '-p', pid, '-i', 'TCP', '-sTCP:LISTEN'],
            text=True,
            stderr=subprocess.DEVNULL,
        )
    except subprocess.CalledProcessError:
        continue
    for socket in sockets.splitlines():
        match = re.search(r':(\d+)\s+\(LISTEN\)', socket)
        if match:
            print(match.group(1))
            raise SystemExit
PY
)
  if [[ -n "$CDP_PORT" ]]; then break; fi
  sleep 1
done

if [[ -z "$CDP_PORT" ]]; then
  echo '{"success":false,"error":"Could not find Chrome debug port"}'
  cleanup; exit 1
fi

# ── Navigate to Cielo dashboard in an isolated tab ──────────────────────────
# ── Check if logged in (poll for URL to settle) ─────────────────────────────
IS_LOGGED_IN="no"
for check in $(seq 1 5); do
  CURRENT_URL=$("$PINCHTAB" --server "$PINCHTAB_INSTANCE_URL" \
    eval "window.location.href" --tab "$CIELO_TAB_ID" --json 2>/dev/null | python3 -c "
import json, sys
try:
    d = json.loads(sys.stdin.read())
    print(d.get('result', ''))
except:
    print('')
" 2>/dev/null)

  if [[ -n "$CURRENT_URL" ]] && [[ "$CURRENT_URL" != *"login"* ]] && [[ "$CURRENT_URL" != *"auth"* ]]; then
    IS_LOGGED_IN="yes"
    break
  fi
  sleep 3
done

# ── Method 3: Headless login with credentials ───────────────────────────────
if [[ "$IS_LOGGED_IN" != "yes" ]]; then
  if [[ "${CIELO_ALLOW_HEADLESS_LOGIN:-false}" != "true" ]]; then
    echo '{"success":false,"error":"Cielo browser session expired; manual reauthentication required."}'
    cleanup; exit 1
  fi
  if [[ -z "${CIELO_USERNAME:-}" ]] || [[ -z "${CIELO_PASSWORD:-}" ]]; then
    echo '{"success":false,"error":"Cookies expired and no CIELO_USERNAME/CIELO_PASSWORD available"}'
    cleanup; exit 1
  fi

  if BACKOFF_STATUS=$(headless_login_backoff_status 2>/dev/null); then
    printf '%s\n' "$BACKOFF_STATUS"
    cleanup; exit 1
  fi

  echo '{"info":"Cookies expired, attempting headless login..."}'

  # Navigate to login page
  "$PINCHTAB" --server "$PINCHTAB_INSTANCE_URL" \
    eval "window.location.assign('https://home.cielowigle.com/auth/login'); 'navigating'" \
    --tab "$CIELO_TAB_ID" >/dev/null 2>&1
  sleep 8

  # Start passive CDP listener BEFORE login to capture the auth response (refreshToken)
  if [[ -n "$CDP_PORT" ]] && [[ -f "$GRAB_SCRIPT" ]]; then
    PASSIVE_LOG="$HOME/.openclaw/logs/cielo-passive-grab.log"
    : > "$PASSIVE_LOG"
    chmod 600 "$PASSIVE_LOG"
    CIELO_TAB_ID="$CIELO_TAB_ID" python3 "$GRAB_SCRIPT" "$CDP_PORT" --passive > "$PASSIVE_LOG" 2>&1 &
    PASSIVE_GRAB_PID=$!
  fi

  # Fill login form and submit
  CIELO_USERNAME_JS=$(python3 -c 'import json, os; print(json.dumps(os.environ["CIELO_USERNAME"]))')
  CIELO_PASSWORD_JS=$(python3 -c 'import json, os; print(json.dumps(os.environ["CIELO_PASSWORD"]))')
  LOGIN_RESULT=$("$PINCHTAB" --server "$PINCHTAB_INSTANCE_URL" eval "
    (() => {
      // Find form inputs — Cielo uses .input100 class
      const inputs = document.querySelectorAll('input');
      let emailInput = null;
      let passInput = null;
      for (const inp of inputs) {
        if (inp.type === 'email' || inp.type === 'text' || inp.name === 'user' || inp.getAttribute('formcontrolname') === 'user') {
          emailInput = inp;
        }
        if (inp.type === 'password' || inp.name === 'password' || inp.getAttribute('formcontrolname') === 'password') {
          passInput = inp;
        }
      }

      if (!emailInput || !passInput) {
        return 'NO_FORM_FIELDS (found ' + inputs.length + ' inputs)';
      }

      // Set values using Angular-compatible method
      function setNgValue(el, value) {
        el.focus();
        el.value = value;
        el.dispatchEvent(new Event('input', { bubbles: true }));
        el.dispatchEvent(new Event('change', { bubbles: true }));
        el.blur();
      }

      setNgValue(emailInput, $CIELO_USERNAME_JS);
      setNgValue(passInput, $CIELO_PASSWORD_JS);

      // Find and click submit button
      const btns = document.querySelectorAll('button[type=submit], input[type=submit], button.login100-form-btn, .container-login100-form-btn button');
      let submitBtn = null;
      for (const btn of btns) {
        if (!btn.disabled) { submitBtn = btn; break; }
      }
      if (!submitBtn) {
        // Fallback: find any button with Sign In text
        for (const btn of document.querySelectorAll('button')) {
          if (btn.textContent.includes('Sign In') || btn.textContent.includes('Login')) {
            submitBtn = btn; break;
          }
        }
      }

      if (!submitBtn) {
        return 'NO_SUBMIT_BUTTON';
      }

      submitBtn.click();
      return 'SUBMITTED';
    })()
  " --tab "$CIELO_TAB_ID" --json 2>/dev/null | python3 -c "
import json, sys
try:
    d = json.loads(sys.stdin.read())
    print(d.get('result', 'ERROR'))
except:
    print('PARSE_ERROR')
" 2>/dev/null)

  if [[ "$LOGIN_RESULT" != "SUBMITTED" ]]; then
    record_headless_login_backoff "form_submission_failed"
    echo '{"success":false,"error":"Cielo headless login form submission failed; attended recovery is required.","error_kind":"attended_reauthentication_required"}'
    cleanup; exit 1
  fi

  # Wait for login to complete and redirect
  sleep 10

  # Check if we landed on dashboard
  FINAL_URL=$("$PINCHTAB" --server "$PINCHTAB_INSTANCE_URL" \
    eval "window.location.href" --tab "$CIELO_TAB_ID" --json 2>/dev/null | python3 -c "
import json, sys
try: d = json.loads(sys.stdin.read()); print(d.get('result',''))
except: print('')
" 2>/dev/null)

  if [[ "$FINAL_URL" == *"login"* ]] || [[ "$FINAL_URL" == *"auth"* ]]; then
    # A reCAPTCHA iframe is present on the untouched Cielo login page, so its
    # presence alone cannot prove that a challenge blocked the submission.
    # Record only a bounded, generic attended-recovery classification.
    LOGIN_BLOCK_KIND=$("$PINCHTAB" --server "$PINCHTAB_INSTANCE_URL" \
      eval "(() => {
        const captcha = document.querySelector('textarea[name=g-recaptcha-response]');
        if (captcha && !(captcha.value || '').trim()) return 'challenge_or_interaction_required';
        const visibleError = Array.from(document.querySelectorAll('.alert-danger,.invalid-feedback,.text-danger'))
          .some(el => el.offsetParent !== null && (el.textContent || '').trim());
        return visibleError ? 'credentials_or_validation_rejected' : 'login_not_completed';
      })()" \
      --tab "$CIELO_TAB_ID" --json 2>/dev/null | python3 -c "
import json, sys
try: d = json.loads(sys.stdin.read()); print(d.get('result','login_not_completed'))
except: print('login_not_completed')
" 2>/dev/null)

    record_headless_login_backoff "$LOGIN_BLOCK_KIND"
    echo '{"success":false,"error":"Cielo headless login did not complete; attended recovery is required.","error_kind":"attended_reauthentication_required"}'
    cleanup; exit 1
  fi

  IS_LOGGED_IN="yes"
  clear_headless_login_backoff
  echo '{"info":"Headless login successful"}'

  # Wait for passive grabber to capture the login response (refreshToken)
  if [[ -n "${PASSIVE_GRAB_PID:-}" ]]; then
    # Give the grabber time to see the login response and post-login API calls
    sleep 5
    # Check if it's still running (may have captured and exited already)
    if kill -0 "$PASSIVE_GRAB_PID" 2>/dev/null; then
      # Wait up to 15 more seconds
      for i in $(seq 1 15); do
        if ! kill -0 "$PASSIVE_GRAB_PID" 2>/dev/null; then break; fi
        sleep 1
      done
      # Kill if still running (timed out)
      kill "$PASSIVE_GRAB_PID" 2>/dev/null
      wait "$PASSIVE_GRAB_PID" 2>/dev/null
    fi
    echo '{"info":"Passive Cielo token capture completed"}'

    # If passive grab captured tokens, we may be able to skip the normal Method 2 grab
    PASSIVE_REFRESH_CAPTURED=$(python3 -c "
import json
try:
    config = json.load(open('$CONFIG_FILE'))
    print('yes' if int(config.get('refreshTokenCapturedAt', 0)) >= int('$RUN_STARTED_AT_MS') else 'no')
except Exception:
    print('no')
" 2>/dev/null)
    if [[ "$PASSIVE_REFRESH_CAPTURED" == "yes" ]]; then
      echo '{"info":"refreshToken captured during login"}'
    fi
  fi
fi

# ── Method 2: Capture tokens via CDP ────────────────────────────────────────
if [[ ! -f "$GRAB_SCRIPT" ]]; then
  echo '{"success":false,"error":"Grab script not found at '"$GRAB_SCRIPT"'"}'
  cleanup; exit 1
fi

GRAB_OUTPUT=$(CIELO_TAB_ID="$CIELO_TAB_ID" python3 "$GRAB_SCRIPT" "$CDP_PORT" 2>&1)
GRAB_EXIT=$?

cleanup

if [[ $GRAB_EXIT -ne 0 ]]; then
  echo '{"success":false,"error":"CDP token capture failed"}'
  exit 1
fi

# Verify the new token works
NEW_TOKEN=$(python3 -c "import json; print(json.load(open('$CONFIG_FILE')).get('accessToken',''))" 2>/dev/null)
TEST_RESULT=$(curl -s "https://$API_HOST/web/devices?limit=1" \
  -H "x-api-key: $API_KEY" \
  -H "authorization: $NEW_TOKEN" \
  -H "Origin: https://home.cielowigle.com" 2>/dev/null | python3 -c "
import json, sys
try:
    d = json.loads(sys.stdin.read())
    print('ok' if d.get('status') == 200 else 'fail')
except:
    print('fail')
" 2>/dev/null)

if [[ "$TEST_RESULT" == "ok" ]]; then
  DURABLE_RESULT=$("$AUTH_HELPER" refresh --force 2>&1)
  DURABLE_EXIT=$?
  if [[ -n "$DURABLE_RESULT" ]]; then
    printf '%s\n' "$DURABLE_RESULT"
  fi
  if [[ $DURABLE_EXIT -eq 0 ]]; then
    clear_headless_login_backoff
    echo '{"success":true,"method":"cdp-browser-reseed","durable":true}'
    exit 0
  fi
  echo '{"success":true,"method":"cdp-browser","durable":false,"warning":"Refresh-token chain remains unavailable; attended recovery is required."}'
  exit 2
else
  echo '{"success":false,"error":"Token captured but API verification failed"}'
  exit 1
fi
