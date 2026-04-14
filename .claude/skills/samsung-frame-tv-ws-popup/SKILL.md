---
name: samsung-frame-tv-ws-popup
description: |
  Fix Samsung Frame TV showing a connection notification on the panel every
  time a samsungtvws WebSocket is opened, even with a saved token. Use when:
  (1) polling a Samsung Frame TV for status from a dashboard/cron and the
  panel briefly wakes or shows "[App] is connecting" each poll, (2) using
  samsungtvws / SamsungTVWS.open() against a Frame TV, (3) art-mode or
  app_list calls cause a visible popup. Solution: do REST-only polling
  (rest_device_info, rest_app_status) and reserve WebSocket calls for
  user-initiated commands.
author: Claude Code
version: 1.0.0
date: 2026-04-13
---

# Samsung Frame TV WebSocket Connection Popup

## Problem

Samsung Frame TVs show a connection notification on the panel every time the
samsungtvws WebSocket (port 8002) is opened, even when a valid token is saved
on disk. On a Frame in art mode this also wakes the panel briefly, which is
disruptive when polling on a schedule (e.g. a home dashboard refreshing every
few minutes).

## Context / Trigger Conditions

- Using `samsungtvws.SamsungTVWS` (NickWaterton fork or upstream) against a
  Samsung Frame TV
- Code calls `tv.open()` or any method that internally opens the WS:
  `tv.art()`, `tv.send_key()`, `tv.app_list()`, etc.
- Token is saved (`token_file=...`) and reused — but popup still appears
- Symptom: TV panel briefly lights / shows a small "connecting" notification
  every poll interval, often reported as "the dashboard is causing a popup
  on the TV"

The popup is **not** a token/auth problem — it's the WS handshake itself.
It does not happen with the REST endpoints on port 8001.

## Solution

Split TV operations into two tiers:

1. **Status / polling (REST-only, silent):**
   - `tv.rest_device_info()` — power state, model, name
   - `tv.rest_app_status(app_id)` — running apps
   - `tv.rest_app_run(app_id)` / `tv.rest_app_close(app_id)` — app launch/close
2. **Interactive commands (WS, popup acceptable):**
   - `tv.send_key(...)` — volume, power off, navigation
   - `tv.art()` — art mode read/write
   - Any method requiring `tv.open()`

For dashboard-style polling, only call the REST methods. Move art-mode info
behind an explicit user action (e.g. a separate `samsung-tv art frame`
command), not the periodic status collector.

### Example fix

```python
def cmd_status(_args):
    # REST-only: opening the WS connection wakes the Frame's panel and shows
    # a connection notification, even with a saved token.
    tv = SamsungTVWS(host=ip, port=8002, token_file=tp, timeout=5, name="OpenClaw")
    info = tv.rest_device_info()
    dev = info.get("device", info)
    print(f"Power: {dev.get('PowerState', 'Unknown')}")
    # NO tv.open(), NO tv.art() here
```

## Verification

1. Stop the dashboard / poller.
2. Run the status command once manually — confirm no popup appears on the TV.
3. Restart the poller and watch the TV through one full poll cycle (or two)
   — the panel should stay quiet.
4. Run an explicit interactive command (`samsung-tv power frame off`) and
   confirm the WS path still works.

## Notes

- Saving the token avoids the **approval prompt** (the "Allow this device?"
  dialog on first connection), but it does not suppress the **connection
  notification** that Frame TVs show on every WS open.
- Non-Frame Samsung TVs generally don't show this notification, so the same
  code may work fine on a regular Samsung TV and only misbehave on a Frame.
- WoL on port 9 is silent and unrelated to this issue.
- If you genuinely need art-mode info on a schedule, accept the popup and
  poll less often (e.g. hourly) rather than every few minutes.

## References

- samsungtvws (NickWaterton fork): https://github.com/NickWaterton/samsung-tv-ws-api
- Samsung Frame TV behavior is not documented in the library README; this
  was discovered empirically while debugging a 5-minute polling loop in a
  home control dashboard.
