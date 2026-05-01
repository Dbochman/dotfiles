#!/bin/bash
# opentable-refresh-token.sh — Automated OpenTable CLI token refresh.
# Logs into OpenTable via Pinchtab browser automation + GWS Gmail 2FA,
# extracts the authCke cookie, and updates the CLI token cache.
#
# Requirements: pinchtab, gws (with bochmanspam@gmail.com auth), secrets-cache
# Runs on Mac Mini (dbochman)

set -euo pipefail

PATH="/opt/homebrew/bin:/opt/homebrew/opt/node@22/bin:/usr/local/bin:/usr/bin:/bin"
TOKEN_CACHE="$HOME/.cache/openclaw-gateway/opentable_auth_token"
OT_EMAIL="bochmanspam@gmail.com"
GWS_ACCOUNT="bochmanspam@gmail.com"

# Source secrets for GWS
set -a
source "$HOME/.openclaw/.secrets-cache" 2>/dev/null || true
set +a

log() { echo "[$(date +%H:%M:%S)] $*" >&2; }

cleanup() {
  pkill -f "pinchtab" 2>/dev/null || true
  sleep 1
}
trap cleanup EXIT

# Step 1: Start headless Pinchtab
log "Starting Pinchtab..."
pkill -f pinchtab 2>/dev/null || true
sleep 1
pinchtab --headless=true &>/tmp/pinchtab-ot-refresh.log &
sleep 4

if ! curl -s http://127.0.0.1:9867/health | grep -q '"status":"ok"'; then
  log "ERROR: Pinchtab failed to start"
  exit 1
fi

# Helper: run JS in Pinchtab
pt_eval() {
  pinchtab eval "$1" 2>&1 | python3 -c "import sys,json; d=json.loads(sys.stdin.read()); print(d.get('result',''))" 2>/dev/null
}

# Step 2: Navigate to login page
log "Navigating to OpenTable login..."
pinchtab navigate "https://www.opentable.com/authenticate/start?isPopup=false" &>/dev/null

# Wait for login form to render (Akamai bot check can delay it, esp. on rapid retries)
for wait_attempt in $(seq 1 12); do
  sleep 5
  FOUND=$(pt_eval '(() => {
    const btn = Array.from(document.querySelectorAll("button")).find(el => el.textContent.trim().toLowerCase() === "use email instead");
    return btn ? "found" : "not_yet";
  })()')
  [[ "$FOUND" == "found" ]] && break
  log "Waiting for login form... (attempt $wait_attempt/12)"
done

# Step 3: Click "Use email instead"
log "Switching to email login..."
CLICK_RESULT=$(pt_eval '(() => {
  const btn = Array.from(document.querySelectorAll("button")).find(el => el.textContent.trim().toLowerCase() === "use email instead");
  if (btn) { btn.dispatchEvent(new MouseEvent("click", {bubbles: true, cancelable: true})); return "ok"; }
  return "not_found";
})()')

if [[ "$CLICK_RESULT" != "ok" ]]; then
  log "ERROR: Could not find 'Use email instead' button"
  exit 1
fi
sleep 2

# Step 4: Enter email
log "Entering email: $OT_EMAIL"
RESULT=$(pt_eval "(() => {
  const input = document.querySelector(\"input[type=email], input[name=email]\");
  if (!input) return \"no_email_input\";
  const setter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, \"value\").set;
  setter.call(input, \"$OT_EMAIL\");
  input.dispatchEvent(new Event(\"input\", {bubbles: true}));
  input.dispatchEvent(new Event(\"change\", {bubbles: true}));
  return \"ok\";
})()")

if [[ "$RESULT" != "ok" ]]; then
  log "ERROR: Could not find email input: $RESULT"
  exit 1
fi

# Step 5: Click Continue
log "Submitting email..."
pt_eval '(() => {
  const btn = Array.from(document.querySelectorAll("button")).find(el => el.textContent.trim().toLowerCase() === "continue");
  if (btn) { btn.click(); return "ok"; }
  return "no_button";
})()'
sleep 5

# Step 6: Read verification code from Gmail
log "Waiting for verification email..."
CODE=""
for attempt in 1 2 3 4 5; do
  LATEST=$(gws gmail users messages list --account "$GWS_ACCOUNT" \
    --params "{\"userId\":\"me\",\"q\":\"from:opentable subject:Confirm newer_than:5m\",\"maxResults\":1}" 2>/dev/null)

  MSG_ID=$(echo "$LATEST" | python3 -c "import sys,json; msgs=json.loads(sys.stdin.read()).get('messages',[]); print(msgs[0]['id'] if msgs else '')" 2>/dev/null)

  if [[ -n "$MSG_ID" ]]; then
    CODE=$(gws gmail users messages get --account "$GWS_ACCOUNT" \
      --params "{\"userId\":\"me\",\"id\":\"$MSG_ID\",\"format\":\"full\"}" 2>/dev/null | python3 -c "
import sys, json, base64, re
msg = json.loads(sys.stdin.read())
payload = msg.get('payload', {})
body_data = payload.get('body', {}).get('data', '')
if not body_data and payload.get('parts'):
    for part in payload['parts']:
        bd = part.get('body', {}).get('data', '')
        if bd:
            body_data = bd
            break
if body_data:
    text = base64.urlsafe_b64decode(body_data).decode('utf-8', errors='replace')
    codes = re.findall(r'(?:code|verify|confirmation)[^0-9]*(\d{4,6})', text, re.IGNORECASE)
    if codes:
        print(codes[0])
    else:
        all_codes = [c for c in re.findall(r'\b(\d{6})\b', text) if not c.startswith('0000')]
        if all_codes:
            print(all_codes[0])
" 2>/dev/null)

    if [[ -n "$CODE" ]]; then
      log "Got verification code: $CODE"
      break
    fi
  fi

  log "Waiting for email... (attempt $attempt/5)"
  sleep 5
done

if [[ -z "$CODE" ]]; then
  log "ERROR: Could not get verification code from Gmail"
  exit 1
fi

# Step 7: Enter verification code
log "Entering verification code..."
pt_eval "(() => {
  const inputs = document.querySelectorAll('input');
  const codeInput = Array.from(inputs).find(i => i.type === 'text' || i.type === 'number' || i.type === 'tel' || i.inputMode === 'numeric');
  if (!codeInput) return 'no_input';
  const setter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value').set;
  setter.call(codeInput, '$CODE');
  codeInput.dispatchEvent(new Event('input', {bubbles: true}));
  codeInput.dispatchEvent(new Event('change', {bubbles: true}));
  return 'ok';
})()"
sleep 5

# Step 8: Verify we're logged in (should redirect away from auth page)
URL=$(pt_eval '(() => { return window.location.href; })()')
if echo "$URL" | grep -q "authenticate"; then
  # Try clicking Continue if code didn't auto-submit
  pt_eval '(() => {
    const btn = Array.from(document.querySelectorAll("button")).find(el => el.textContent.trim().toLowerCase() === "continue");
    if (btn) { btn.click(); return "ok"; }
    return "no_button";
  })()'
  sleep 5
  URL=$(pt_eval '(() => { return window.location.href; })()')
fi

if echo "$URL" | grep -q "authenticate"; then
  log "ERROR: Still on auth page after entering code. URL: $URL"
  exit 1
fi
log "Logged in. Redirected to: $URL"

# Step 9: Extract authCke cookie
log "Extracting auth token..."
pinchtab navigate "https://www.opentable.com" &>/dev/null
sleep 2
ATK=$(pt_eval '(() => {
  const c = document.cookie;
  const m = c.match(/authCke=[^;]*/);
  if (m) {
    const atk = m[0].match(/atk=([^&]+)/);
    return atk ? atk[1] : "";
  }
  return "";
})()')

if [[ -z "$ATK" ]]; then
  log "ERROR: Could not extract authCke token"
  exit 1
fi

# Step 10: Update CLI token cache
mkdir -p "$(dirname "$TOKEN_CACHE")"
echo "$ATK" > "$TOKEN_CACHE"
chmod 600 "$TOKEN_CACHE"

log "OK: OpenTable token refreshed: ${ATK:0:8}..."
echo "$ATK"
