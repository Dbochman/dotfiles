#!/usr/bin/env bash
# cielo-refresh.sh — Cielo token refresh with auto-login fallback
#
# Method 1: API refresh using stored refreshToken (fast, no browser)
# Method 2: Browser CDP capture (pinchtab + persisted cookies)
# Method 3: Explicitly opted-in headless login with username/password
#
# Runs as a LaunchAgent every 30 minutes.

export PATH=/opt/homebrew/bin:/usr/local/bin:/usr/sbin:/usr/bin:/bin

# Load credentials (must come before variable expansion)
if [[ -f "$HOME/.openclaw/.secrets-cache" ]]; then
  set -a; source "$HOME/.openclaw/.secrets-cache"; set +a
fi

CONFIG_FILE="$HOME/.config/cielo/config.json"
API_HOST="api.smartcielo.com"
API_KEY="${CIELO_API_KEY:?CIELO_API_KEY not set}"
GRAB_SCRIPT="$HOME/.openclaw/workspace/scripts/grab-cielo-tokens.py"
PINCHTAB="/opt/homebrew/bin/pinchtab"
PINCHTAB_PROFILE="${CIELO_PINCHTAB_PROFILE:-default}"

# ── Method 1: API refresh token ─────────────────────────────────────────────
if [[ -f "$CONFIG_FILE" ]]; then
  REFRESH_TOKEN=$(python3 -c "import json; print(json.load(open('$CONFIG_FILE')).get('refreshToken',''))" 2>/dev/null)

  if [[ -n "$REFRESH_TOKEN" ]]; then
    ACCESS_TOKEN=$(python3 -c "import json; print(json.load(open('$CONFIG_FILE')).get('accessToken',''))" 2>/dev/null)
    RESPONSE=$(curl -s -X POST "https://$API_HOST/web/token/refresh" \
      -H "Content-Type: application/json; charset=UTF-8" \
      -H "x-api-key: $API_KEY" \
      -H "authorization: $ACCESS_TOKEN" \
      -H "Origin: https://home.cielowigle.com" \
      -d "{\"local\":\"en\",\"refreshToken\":\"$REFRESH_TOKEN\"}" 2>/dev/null)

    STATUS=$(echo "$RESPONSE" | python3 -c "import json,sys; print(json.loads(sys.stdin.read()).get('status',''))" 2>/dev/null)

    if [[ "$STATUS" == "200" ]]; then
      python3 -c "
import json, time, os
response = json.loads('''$RESPONSE''')
data = response['data']
config = json.load(open('$CONFIG_FILE'))
config['accessToken'] = data['accessToken']
config['refreshToken'] = data.get('refreshToken', config.get('refreshToken', ''))
config['expiresIn'] = data.get('expiresIn', '')
config['lastRefresh'] = int(time.time() * 1000)
with open('$CONFIG_FILE', 'w') as f:
    json.dump(config, f, indent=2)
os.chmod('$CONFIG_FILE', 0o600)
print(json.dumps({'success': True, 'method': 'api-refresh'}))
"
      exit 0
    fi
  fi
fi

# ── Start pinchtab ──────────────────────────────────────────────────────────
STARTED_PINCHTAB_INSTANCE=false
PINCHTAB_INSTANCE_ID=""
PINCHTAB_PROFILE_PATH=""
CIELO_TAB_ID=""
PASSIVE_GRAB_PID=""

cleanup() {
  if [[ -n "$PASSIVE_GRAB_PID" ]] && kill -0 "$PASSIVE_GRAB_PID" 2>/dev/null; then
    kill "$PASSIVE_GRAB_PID" 2>/dev/null || true
    wait "$PASSIVE_GRAB_PID" 2>/dev/null || true
  fi
  if [[ -n "$CIELO_TAB_ID" ]]; then
    "$PINCHTAB" close "$CIELO_TAB_ID" >/dev/null 2>&1 || true
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

# Open an isolated Cielo tab through the managed instance API.
NAV_OUTPUT=$("$PINCHTAB" instance navigate "$PINCHTAB_INSTANCE_ID" "https://home.cielowigle.com/" 2>/dev/null)
CIELO_TAB_ID=$(printf '%s' "$NAV_OUTPUT" | python3 -c "
import re, sys
match = re.search(r'\"tabId\"\s*:\s*\"([^\"]+)\"', sys.stdin.read())
print(match.group(1) if match else '')
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
  CURRENT_URL=$("$PINCHTAB" eval "window.location.href" --tab "$CIELO_TAB_ID" --json 2>/dev/null | python3 -c "
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

  echo '{"info":"Cookies expired, attempting headless login..."}'

  # Navigate to login page
  "$PINCHTAB" eval "window.location.assign('https://home.cielowigle.com/auth/login'); 'navigating'" --tab "$CIELO_TAB_ID" >/dev/null 2>&1
  sleep 8

  # Start passive CDP listener BEFORE login to capture the auth response (refreshToken)
  if [[ -n "$CDP_PORT" ]] && [[ -f "$GRAB_SCRIPT" ]]; then
    CIELO_TAB_ID="$CIELO_TAB_ID" python3 "$GRAB_SCRIPT" "$CDP_PORT" --passive > /tmp/cielo-passive-grab.log 2>&1 &
    PASSIVE_GRAB_PID=$!
  fi

  # Fill login form and submit
  CIELO_USERNAME_JS=$(python3 -c 'import json, os; print(json.dumps(os.environ["CIELO_USERNAME"]))')
  CIELO_PASSWORD_JS=$(python3 -c 'import json, os; print(json.dumps(os.environ["CIELO_PASSWORD"]))')
  LOGIN_RESULT=$("$PINCHTAB" eval "
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
    echo "{\"success\":false,\"error\":\"Login form fill failed: $LOGIN_RESULT\"}"
    cleanup; exit 1
  fi

  # Wait for login to complete and redirect
  sleep 10

  # Check if we landed on dashboard
  FINAL_URL=$("$PINCHTAB" eval "window.location.href" --tab "$CIELO_TAB_ID" --json 2>/dev/null | python3 -c "
import json, sys
try: d = json.loads(sys.stdin.read()); print(d.get('result',''))
except: print('')
" 2>/dev/null)

  if [[ "$FINAL_URL" == *"login"* ]] || [[ "$FINAL_URL" == *"auth"* ]]; then
    # Check if reCAPTCHA is blocking
    HAS_CAPTCHA=$("$PINCHTAB" eval "document.querySelector('iframe[src*=recaptcha]')?.src || 'none'" --tab "$CIELO_TAB_ID" --json 2>/dev/null | python3 -c "
import json, sys
try: d = json.loads(sys.stdin.read()); print(d.get('result','none'))
except: print('none')
" 2>/dev/null)

    if [[ "$HAS_CAPTCHA" != "none" ]]; then
      echo '{"success":false,"error":"Login blocked by reCAPTCHA. Manual login required."}'
    else
      echo '{"success":false,"error":"Login failed (still on login page after submit)"}'
    fi
    cleanup; exit 1
  fi

  IS_LOGGED_IN="yes"
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
    PASSIVE_REFRESH=$(python3 -c "import json; print(json.load(open('$CONFIG_FILE')).get('refreshToken',''))" 2>/dev/null)
    if [[ -n "$PASSIVE_REFRESH" ]]; then
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
  echo '{"success":true,"method":"cdp-browser"}'
else
  echo '{"success":false,"error":"Token captured but API verification failed"}'
  exit 1
fi
