#!/usr/bin/env bash
# cielo-refresh.sh — Self-contained Cielo token refresh
#
# Starts pinchtab if needed, navigates to Cielo (auto-login via persisted cookies),
# captures fresh access token via CDP, verifies it works, saves to config.
#
# No persistent browser process needed — cookies persist in ~/.pinchtab/chrome-profile/
# Only requires manual re-login when Cielo session cookies expire (weeks/months).
#
# Runs as a LaunchAgent every 30 minutes.

export PATH=/opt/homebrew/bin:/usr/local/bin:/usr/sbin:/usr/bin:/bin
CONFIG_FILE="$HOME/.config/cielo/config.json"
API_HOST="api.smartcielo.com"
API_KEY="3iCWYuBqpY2g7yRq3yyTk1XCS4CMjt1n9ECCjdpd"
GRAB_SCRIPT="$HOME/.openclaw/workspace/scripts/grab-cielo-tokens.py"

# --- Method 1: API refresh (if we have a refresh token) ---
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

# --- Method 2: Browser-based token capture via CDP ---
STARTED_PINCHTAB=false
if ! pgrep -f "pinchtab" >/dev/null 2>&1; then
  /opt/homebrew/bin/pinchtab --headless &
  STARTED_PINCHTAB=true
  for i in $(seq 1 15); do
    if curl -s http://localhost:9867/health >/dev/null 2>&1; then break; fi
    sleep 1
  done
fi

# Find Chrome CDP port (retry loop for startup timing)
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
  [[ "$STARTED_PINCHTAB" == true ]] && pkill -f pinchtab 2>/dev/null
  exit 1
fi

# Navigate to Cielo if not already there (auto-login via persisted cookies)
HAS_CIELO=$(curl -s "http://localhost:$CDP_PORT/json" 2>/dev/null | python3 -c "
import json, sys
tabs = json.load(sys.stdin)
print('yes' if any('cielowigle' in t.get('url','') for t in tabs) else 'no')
" 2>/dev/null || echo "no")

if [[ "$HAS_CIELO" != "yes" ]]; then
  /opt/homebrew/bin/pinchtab nav "https://home.cielowigle.com/" 2>/dev/null
  sleep 8
fi

# Check if logged in (not redirected to login page)
IS_LOGGED_IN=$(/opt/homebrew/bin/pinchtab eval "!window.location.href.includes('login')" 2>/dev/null | python3 -c "
import json, sys
try:
    d = json.loads(sys.stdin.read())
    print('yes' if d.get('result') == True else 'no')
except:
    print('no')
" 2>/dev/null)

if [[ "$IS_LOGGED_IN" != "yes" ]]; then
  echo '{"success":false,"error":"Cielo session expired. Manual re-login required."}'
  [[ "$STARTED_PINCHTAB" == true ]] && pkill -f pinchtab 2>/dev/null
  exit 1
fi

# Capture tokens via CDP network monitoring
if [[ ! -f "$GRAB_SCRIPT" ]]; then
  echo '{"success":false,"error":"Grab script not found at '"$GRAB_SCRIPT"'"}'
  [[ "$STARTED_PINCHTAB" == true ]] && pkill -f pinchtab 2>/dev/null
  exit 1
fi

GRAB_OUTPUT=$(python3 "$GRAB_SCRIPT" "$CDP_PORT" 2>&1)
GRAB_EXIT=$?

# Clean up pinchtab if we started it
[[ "$STARTED_PINCHTAB" == true ]] && pkill -f pinchtab 2>/dev/null

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
