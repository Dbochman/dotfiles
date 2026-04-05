---
name: openclaw-ssrf-localhost-plugins
description: |
  Fix OpenClaw SSRF guard blocking channel plugins (BlueBubbles, etc.) from reaching
  localhost or private/LAN IP servers. Use when: (1) gateway.err.log shows "blocked URL
  fetch (bluebubbles-api) reason=Blocked hostname or private/internal/special-use IP
  address", (2) `openclaw health` reports BlueBubbles as "failed (unknown)", (3) after
  upgrading OpenClaw to 2026.3.28+ or 2026.4.x. The SSRF guard doesn't distinguish
  between agent-initiated web_fetch and plugin internal HTTP calls.
author: Claude Code
version: 1.0.0
date: 2026-04-05
---

# OpenClaw SSRF Guard Blocks Localhost Channel Plugins

## Problem
OpenClaw 2026.3.28 introduced SSRF protection that blocks all HTTP requests to
localhost, 127.0.0.1, private IPs (10.x, 172.16-31.x, 192.168.x), and other
special-use addresses. This affects channel plugin internal API calls (BlueBubbles,
Home Assistant, local LLM servers, etc.) even though those URLs are admin-configured
in openclaw.json, not user-controllable.

## Context / Trigger Conditions
- Upgraded OpenClaw to 2026.3.28 or later
- Channel plugin (e.g., BlueBubbles) uses a localhost or LAN server URL
- `gateway.err.log` shows:
  ```
  [security] blocked URL fetch (bluebubbles-api) target=http://localhost:1234/api/v1/ping
    reason=Blocked hostname or private/internal/special-use IP address
  ```
- `openclaw health` reports the channel as failed
- Actual messaging may still partially work (webhooks inbound work, but outbound
  pings/health checks and some API calls are blocked)

## Solution
Add `"allowPrivateNetwork": true` to the channel config in `openclaw.json`:

```json
{
  "channels": {
    "bluebubbles": {
      "enabled": true,
      "serverUrl": "http://localhost:1234",
      "allowPrivateNetwork": true,
      ...
    }
  }
}
```

This is a per-channel setting that exempts that channel's internal HTTP client from
the SSRF guard. It does NOT affect agent-initiated `web_fetch` or browser tool calls.

## Verification
1. Restart gateway: `launchctl kickstart -k gui/$(id -u)/ai.openclaw.gateway`
2. Check `gateway.log` — should show `BlueBubbles server macOS X.X` and
   `BlueBubbles Private API enabled` (successful connection)
3. Check `gateway.err.log` — no more "blocked URL fetch" lines after restart
4. Run `openclaw health` — should show `BlueBubbles: ok`

## Notes
- This affects ANY self-hosted integration on localhost/LAN, not just BlueBubbles
- The config key is `allowPrivateNetwork` (camelCase), validated in the channel schema
- See openclaw/openclaw#57181 and openclaw/openclaw#60715 for upstream discussion
- A broader fix (exempting all plugin internal HTTP from SSRF) is tracked upstream
  but not yet shipped as of 2026.4.2

## References
- openclaw/openclaw#57181 — SSRF guard blocks BlueBubbles plugin internal API calls
- openclaw/openclaw#60715 — BlueBubbles health check fails on LAN/private serverUrl
