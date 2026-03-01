#!/usr/bin/env bash
# cielo-refresh.sh — Cielo token refresh with auto-login fallback
#
# Method 1: API refresh using stored refreshToken (fast, no browser)
# Method 2: Browser CDP capture (pinchtab + persisted cookies)
# Method 3: Headless login with username/password (if cookies expired)
#
# Runs as a LaunchAgent every 30 minutes.

export PATH=/opt/homebrew/bin:/usr/local/bin:/usr/sbin:/usr/bin:/bin
CONFIG_FILE="$HOME/.config/cielo/config.json"
API_HOST="api.smartcielo.com"
API_KEY="3iCWYuBqpY2g7yRq3yyTk1XCS4CMjt1n9ECCjdpd"
GRAB_SCRIPT="$HOME/.openclaw/workspace/scripts/grab-cielo-tokens.py"

# Load credentials for Method 3
if [[ -f "$HOME/.openclaw/.secrets-cache" ]]; then
  set -a; source "$HOME/.openclaw/.secrets-cache"; set +a
fi

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
STARTED_PINCHTAB=false
if ! pgrep -f "pinchtab" >/dev/null 2>&1; then
  /opt/homebrew/bin/pinchtab --headless &
  STARTED_PINCHTAB=true
  for i in $(seq 1 15); do
    if curl -s http://localhost:9867/health >/dev/null 2>&1; then break; fi
    sleep 1
  done
fi

cleanup() {
  [[ "$STARTED_PINCHTAB" == true ]] && pkill -f pinchtab 2>/dev/null
}

# Find Chrome CDP port
CDP_PORT=""
for attempt in $(seq 1 15); do
  CDP_PORT=$(python3 -c "
import subprocess, re
ps = subprocess.check_output(['ps', 'aux'], text=True)
for line in ps.splitlines():
    if 'chrome-profile' in line and 'remote-debugging' in line and 'Google Chrome' in line and '--type=' not in line:
        pid = line.split()[1]
        try:
            lsof = subprocess.check_output(['/usr/sbin/lsof', '-anP', '-p', pid, '-i', 'TCP', '-sTCP:LISTEN'], text=True, stderr=subprocess.DEVNULL)
            for l in lsof.splitlines():
                m = re.search(r':(\d+)\s+\(LISTEN\)', l)
                if m:
                    print(m.group(1))
                    exit(0)
        except: pass
        break
" 2>/dev/null)
  if [[ -n "$CDP_PORT" ]]; then break; fi
  sleep 1
done

if [[ -z "$CDP_PORT" ]]; then
  echo '{"success":false,"error":"Could not find Chrome debug port"}'
  cleanup; exit 1
fi

# ── Navigate to Cielo dashboard ─────────────────────────────────────────────
HAS_CIELO=$(curl -s "http://localhost:$CDP_PORT/json" 2>/dev/null | python3 -c "
import json, sys
tabs = json.load(sys.stdin)
print('yes' if any('cielowigle' in t.get('url','') and 'login' not in t.get('url','') for t in tabs) else 'no')
" 2>/dev/null || echo "no")

if [[ "$HAS_CIELO" != "yes" ]]; then
  /opt/homebrew/bin/pinchtab nav "https://home.cielowigle.com/" 2>/dev/null
  # Wait for Angular SPA to fully load and settle (may redirect to login)
  sleep 12
fi

# ── Check if logged in (poll for URL to settle) ─────────────────────────────
IS_LOGGED_IN="no"
for check in $(seq 1 5); do
  CURRENT_URL=$(/opt/homebrew/bin/pinchtab eval "window.location.href" 2>/dev/null | python3 -c "
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
  if [[ -z "${CIELO_USERNAME:-}" ]] || [[ -z "${CIELO_PASSWORD:-}" ]]; then
    echo '{"success":false,"error":"Cookies expired and no CIELO_USERNAME/CIELO_PASSWORD available"}'
    cleanup; exit 1
  fi

  echo '{"info":"Cookies expired, attempting headless login..."}'

  # Navigate to login page
  /opt/homebrew/bin/pinchtab nav "https://home.cielowigle.com/auth/login" 2>/dev/null
  sleep 8

  # Fill login form and submit
  LOGIN_RESULT=$(/opt/homebrew/bin/pinchtab eval "
    (async () => {
      // Wait for Angular form to render
      await new Promise(r => setTimeout(r, 2000));

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

      setNgValue(emailInput, '${CIELO_USERNAME}');
      setNgValue(passInput, '${CIELO_PASSWORD}');

      await new Promise(r => setTimeout(r, 500));

      // Find and click submit button
      const btns = document.querySelectorAll('button[type=submit], button.login100-form-btn, .container-login100-form-btn button');
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
  " 2>/dev/null | python3 -c "
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
  FINAL_URL=$(/opt/homebrew/bin/pinchtab eval "window.location.href" 2>/dev/null | python3 -c "
import json, sys
try: d = json.loads(sys.stdin.read()); print(d.get('result',''))
except: print('')
" 2>/dev/null)

  if [[ "$FINAL_URL" == *"login"* ]] || [[ "$FINAL_URL" == *"auth"* ]]; then
    # Check if reCAPTCHA is blocking
    HAS_CAPTCHA=$(/opt/homebrew/bin/pinchtab eval "document.querySelector('iframe[src*=recaptcha]')?.src || 'none'" 2>/dev/null | python3 -c "
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
fi

# ── Method 2: Capture tokens via CDP ────────────────────────────────────────
if [[ ! -f "$GRAB_SCRIPT" ]]; then
  echo '{"success":false,"error":"Grab script not found at '"$GRAB_SCRIPT"'"}'
  cleanup; exit 1
fi

GRAB_OUTPUT=$(python3 "$GRAB_SCRIPT" "$CDP_PORT" 2>&1)
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
