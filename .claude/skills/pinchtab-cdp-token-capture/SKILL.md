---
name: pinchtab-cdp-token-capture
description: |
  Capture authentication tokens from reCAPTCHA-protected websites using pinchtab + Chrome DevTools Protocol (CDP).
  Use when: (1) need to automate token refresh for sites with reCAPTCHA login, (2) need to find Chrome's CDP port
  when pinchtab uses --remote-debugging-port=0, (3) lsof not working in LaunchAgent context on macOS,
  (4) need to click inside cross-origin iframes via CDP Input.dispatchMouseEvent,
  (5) Chrome cookies not persisting between pinchtab restarts, (6) Network.getResponseBody returns empty
  for fetch() API responses (use Fetch.enable + Fetch.getResponseBody instead), (7) need to capture tokens
  from login responses by monitoring CDP network traffic passively during browser form submission,
  (8) API response body structure nests tokens in unexpected locations (e.g., data.user.X not data.X).
author: Claude Code
version: 1.2.0
date: 2026-03-01
---

# Pinchtab CDP Token Capture

## Problem
Automating login to websites with reCAPTCHA v2 protection to capture authentication tokens for API use.
reCAPTCHA can't be reliably solved programmatically, so you need a hybrid manual+automated approach.

## Context / Trigger Conditions
- Website login requires reCAPTCHA (e.g., Cielo Home, many Angular/React SPAs)
- Need to periodically refresh API tokens that expire (~1 hour)
- Running pinchtab browser automation on a Mac Mini or similar headless server
- `lsof` commands fail or return wrong data in LaunchAgent context

## Solution

### Key Insight: Pinchtab Cookie Persistence
Cookies persist in `~/.pinchtab/chrome-profile/`. Even after killing and restarting pinchtab,
Chrome loads the same profile. Authenticated sessions survive restarts indefinitely (until
the site's session cookies expire, typically weeks/months).

### One-Time Manual Login
```bash
# Start pinchtab with VISIBLE browser window
BRIDGE_HEADLESS=false pinchtab &
sleep 5
pinchtab nav "https://example.com/login"
pinchtab fill 'input[name=email]' 'user@example.com'
# User solves CAPTCHA manually, clicks Sign In
```

### Automated Token Refresh (every 30 min via LaunchAgent)
1. Start pinchtab headless (auto-loads persisted cookies)
2. Navigate to site (auto-login via cookies)
3. Find Chrome's CDP port
4. Capture fresh access token via CDP Network monitoring
5. Clean up pinchtab

### Finding Chrome's CDP Port
Pinchtab uses `--remote-debugging-port=0` (random port). To find it:

```python
import subprocess, re

# Get Chrome main process PID (exclude --type= worker processes)
ps = subprocess.check_output(['ps', 'aux'], text=True)
for line in ps.splitlines():
    if 'chrome-profile' in line and 'remote-debugging' in line and '--type=' not in line:
        pid = line.split()[1]
        # CRITICAL: use /usr/sbin/lsof with -anP flags on macOS
        lsof = subprocess.check_output(
            ['/usr/sbin/lsof', '-anP', '-p', pid, '-i', 'TCP', '-sTCP:LISTEN'],
            text=True, stderr=subprocess.DEVNULL
        )
        for l in lsof.splitlines():
            m = re.search(r':(\d+)\s+\(LISTEN\)', l)
            if m:
                print(m.group(1))  # This is the CDP port
```

**Critical macOS `lsof` flags:**
- `-a` = AND mode (without this, returns ALL system sockets, not just the target PID's)
- `-n` = numeric IPs (skip DNS)
- `-P` = numeric ports (skip service name resolution)
- `-sTCP:LISTEN` = only LISTEN state sockets
- Use `/usr/sbin/lsof` (not in default LaunchAgent PATH!)

### CDP Token Capture via Python
```python
import json, asyncio, websockets

async def capture_token(cdp_port):
    tabs = json.loads(subprocess.check_output(
        ["curl", "-s", f"http://localhost:{cdp_port}/json"], text=True))
    tab = next(t for t in tabs if "example.com" in t.get("url", ""))

    async with websockets.connect(tab["webSocketDebuggerUrl"]) as ws:
        await ws.send(json.dumps({"id": 1, "method": "Network.enable"}))
        await ws.recv()
        await ws.send(json.dumps({"id": 2, "method": "Page.reload"}))

        while True:
            msg = json.loads(await ws.recv())
            if msg.get("method") == "Network.requestWillBeSent":
                headers = msg["params"]["request"].get("headers", {})
                auth = headers.get("authorization", "")
                if auth and len(auth) > 50:
                    return auth  # Got the token!
```

### CDP Click for Cross-Origin Iframes (reCAPTCHA)
Regular JS `dispatchEvent` can't cross iframe origin boundaries. Use CDP:
```python
# Input.dispatchMouseEvent works at absolute page coordinates
await ws.send(json.dumps({
    "id": 1, "method": "Input.dispatchMouseEvent",
    "params": {"type": "mousePressed", "x": 681, "y": 540, "button": "left", "clickCount": 1}
}))
await ws.send(json.dumps({
    "id": 2, "method": "Input.dispatchMouseEvent",
    "params": {"type": "mouseReleased", "x": 681, "y": 540, "button": "left", "clickCount": 1}
}))
```
Note: This triggers the CAPTCHA but doesn't solve it — it escalates to image challenges.

## Verification
```bash
# Test token works
curl -s "https://api.example.com/endpoint" \
  -H "authorization: $TOKEN" | python3 -c "
import json,sys; d=json.loads(sys.stdin.read()); print('ok' if d.get('status')==200 else 'fail')"
```

## LaunchAgent Setup
```xml
<key>EnvironmentVariables</key>
<dict>
    <key>PATH</key>
    <!-- MUST include /usr/sbin for lsof -->
    <string>/opt/homebrew/bin:/usr/local/bin:/usr/sbin:/usr/bin:/bin</string>
    <key>HOME</key>
    <string>/Users/username</string>
</dict>
<key>StartInterval</key>
<integer>1800</integer>
```

## Critical: Network.getResponseBody Returns Empty for fetch() Responses

**Problem**: `Network.getResponseBody` returns empty body for responses consumed by the page's
`fetch()` API. Chrome doesn't cache the response body for CDP when JavaScript reads it as a stream.
Even waiting for `Network.loadingFinished` doesn't help — the body is simply not available.

**Fix**: Use CDP's **Fetch domain** instead, which intercepts responses at the network layer
*before* the page consumes them:

```python
# Enable Fetch to intercept responses before page consumes them
await ws.send(json.dumps({
    "id": 2,
    "method": "Fetch.enable",
    "params": {
        "patterns": [
            {"urlPattern": "*api.example.com*", "requestStage": "Response"}
        ]
    }
}))

# On Fetch.requestPaused (response ready but paused):
if method == "Fetch.requestPaused":
    fetch_rid = params["requestId"]
    # Read body while paused — this ALWAYS works
    await ws.send(json.dumps({
        "id": next_id, "method": "Fetch.getResponseBody",
        "params": {"requestId": fetch_rid}
    }))
    # ... handle response, decode base64 if base64Encoded ...
    # MUST resume the request or page hangs:
    await ws.send(json.dumps({
        "id": next_id, "method": "Fetch.continueRequest",
        "params": {"requestId": fetch_rid}
    }))
```

**Important**: Always call `Fetch.continueRequest` after reading the body, or the page will hang.
Disable Fetch when done: `Fetch.disable`.

## Passive Mode: Capturing Tokens During Login

When you need to capture tokens from a login API response (e.g., `refreshToken`), you can't
just reload the dashboard — authenticated sessions only hit data endpoints, not auth endpoints.

**Pattern**: Start CDP Fetch interception BEFORE the login form is submitted, then capture
the auth response as it flows through.

```bash
# 1. Start passive CDP listener in background
python3 grab-tokens.py $CDP_PORT --passive &
GRAB_PID=$!

# 2. Submit login form (via pinchtab eval or other method)
pinchtab eval "document.querySelector('form').submit()"

# 3. Wait for grabber to capture auth response
sleep 5
wait $GRAB_PID 2>/dev/null || kill $GRAB_PID 2>/dev/null
```

**Gotcha**: Check nested response structures. Cielo's `/auth/login` returns tokens at
`data.user.accessToken` and `data.user.refreshToken`, NOT directly under `data`.

## Obfuscated SPA API Endpoint Discovery

When an Angular/React SPA has obfuscated JS, you can still find API paths:
```bash
# Download the compiled bundle
curl -s 'https://example.com/main.HASH.js' > /tmp/bundle.js
# Extract all API paths (works even on obfuscated code — paths are string literals)
grep -oE '/web/[a-zA-Z/]+' /tmp/bundle.js | sort -u
# Find auth-related URLs
grep -oE '(https?://[a-zA-Z0-9./-]+|/[a-zA-Z]+/[a-zA-Z/]+)' /tmp/bundle.js | grep -iE '(auth|login|password|token)' | sort -u
```

## Notes
- Chrome CDP port changes every time pinchtab restarts — always discover dynamically
- Need retry loop (up to 15s) for Chrome to open its debug port after pinchtab starts
- `pinchtab health` shows `"cdp":""` — it doesn't expose the CDP port, must use lsof
- `pgrep -f "Google Chrome.*chrome-profile.*remote-debugging"` may match worker processes;
  filter with `--type= not in line` to get main process only
- For WebSocket sessionIds: watch `Network.webSocketCreated` events and parse URL params
- Cielo's Angular SPA stores tokens in encrypted localStorage blobs (`uIn`, `dtd`, `ad`) — can't extract plaintext tokens from storage
- Direct API login (`/authenticate`) is CORS-blocked — must capture via browser CDP during actual login flow
- Dashboard reloads only hit data endpoints (`/web/devices`, `/web/default-threshold`), never auth endpoints
