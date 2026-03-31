# Fi Collar Presence Integration — Plan

## Status: IMPLEMENTED (2026-03-30)

## Goal
Add Potato's Fi Series 3+ collar to the Crosstown presence detection system.

## What We Know

- **Collar**: Fi Series 3+, Serial #FC35G072187, Model FC3B, MAC D4:3D:39:A7:4B:6C
- **How it works**: Collar connects to Fi base station via BLE, base connects to WiFi. The collar itself does NOT appear as a WiFi client.
- **Fi base location**: Crosstown (19 Crosstown Ave) — NOT Cabin
- **Fi base on network**: hostname `da16200-4b6c.lan`, IP `192.168.165.187`, MAC `d4:3d:39:a7:4b:6c`
- The `da16200` prefix is a Dialog Semiconductor DA16200 WiFi SoC used in Fi base stations
- **Fi API**: `api.tryfi.com` — both `dylanbochman@gmail.com` and `juliajoyjennings@gmail.com` return 401 (likely Google SSO accounts, need password reset via Fi "Forgot Password" flow to enable API login)

## Implementation (Completed)

### Added to Crosstown devices in `presence-detect.sh`
```json
{"person":"Potato","match":"mac","pattern":"d4:3d:39:a7:4b:6c"},
{"person":"Potato","match":"hostname","pattern":"da16200-4b6c"}
```

### Potato is informational only
`CROSSTOWN_TRACKED` stays `["Dylan","Julia"]` — Potato does NOT gate vacancy decisions. The dog being home alone still triggers vacancy actions (eco mode, Roombas, etc.).

### Potato appears in state.json
```json
"people": {
  "Dylan": { "cabin": false, "crosstown": true, "location": "crosstown" },
  "Julia": { "cabin": false, "crosstown": true, "location": "crosstown" },
  "Potato": { "cabin": false, "crosstown": true, "location": "crosstown" }
}
```

## Detection method
The Fi base station is a persistent WiFi client — unlike phones, it doesn't rotate MACs or sleep. When the base is connected and powered on, Potato is detected as present. When Potato travels (e.g., to Cabin), the base stays at Crosstown but the collar disconnects from it. However, the base itself remains on the network, so this method only confirms "Potato's home base is at Crosstown" — not necessarily that Potato is physically there.

For true location tracking (away from base), the Fi API would be needed.

### Dog walk dashboard — no changes needed
The ring-listener already calls `presence-detect.sh` for network checks, so Potato flows through automatically to `last_network_check` in JSONL events. Vision analysis already knows Potato by name. The Fi base stays on the network during walks (plugged in), so Potato won't appear in `walkers` — that's correct since vision detects the dog leaving via camera frames.

## Cabin Base (pending)
A second Fi base station is being ordered for the Cabin. Placeholder entry added to `CABIN_DEVICES` matching by `da16200` name prefix. Once plugged in:
1. SSH to Mini: `ssh dylans-mac-mini`
2. Scan Starlink clients: `grpcurl -plaintext -d '{"wifiGetClients":{}}' 192.168.1.1:9000 SpaceX.API.Device.Device/Handle`
3. Look for a `da16200-XXXX` device name
4. Tighten the pattern in `presence-detect.sh` if needed

## Future: Fi API (if credentials are fixed)
If either account gets a password reset via Fi's "Forgot Password":
- Login: `POST https://api.tryfi.com/auth/login` (email + password, form-encoded)
- GraphQL query returns: GPS location, battery, connection state (base/cellular/user), activity type
- Could provide richer data than WiFi presence (actual GPS, walk tracking, battery alerts)
- Script ready at `openclaw/skills/fi-collar/fi-api.py` (needs working credentials)
