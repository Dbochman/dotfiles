# Fi Collar Presence Integration — Plan

## Goal
Add Potato's Fi Series 3+ collar to the Cabin presence detection system.

## What We Know

- **Collar**: Fi Series 3+, Serial #FC35G072187, Model FC3B, MAC D4:3D:39:A7:4B:6C
- **How it works**: Collar connects to Fi base station via BLE, base connects to WiFi. The collar itself does NOT appear as a WiFi client on Starlink.
- **Fi API**: `api.tryfi.com` — both `dylanbochman@gmail.com` and `juliajoyjennings@gmail.com` return 401 (likely Google SSO accounts, need password reset via Fi "Forgot Password" flow to enable API login)

## Candidate Device on Starlink

| Field | Value |
|-------|-------|
| Name | `1538000-45-B--GF2232900000M0` |
| MAC prefix | `a0:cd:f3` (last 3 octets redacted by Starlink) |
| Band | 2.4 GHz |
| Connected | ~3.5 hours (at time of scan, aligns with arrival) |

**NOT CONFIRMED** as the Fi base — needs verification. To confirm:
1. Unplug the Fi base at Cabin, wait 2 min, re-scan Starlink clients — if `1538000-45-B--GF2232900000M0` disappears, it's the base
2. Or check if `a0:cd:f3` is a known Fi/Kinetic OUI

## Implementation Plan (once device confirmed)

### Step 1: Add to Cabin presence scan
In `presence-detect.sh`, add a device entry to `CABIN_DEVICES`:
```json
{"person":"Potato","match":"name","pattern":"<confirmed-device-name>"}
```

### Step 2: Keep Potato OUT of tracked list
`CABIN_TRACKED` stays `["Dylan","Julia"]` — Potato should NOT gate vacancy decisions. The dog being home alone should still trigger vacancy actions (eco mode, Roombas, etc.).

### Step 3: Potato appears in state.json as informational
After the scan, `state.json` will include:
```json
"people": {
  "Dylan": { "cabin": true, "crosstown": false, "location": "cabin" },
  "Julia": { "cabin": true, "crosstown": false, "location": "cabin" },
  "Potato": { "cabin": true, "crosstown": false, "location": "cabin" }
}
```
The agent can see Potato's location but it doesn't affect vacancy logic.

### Step 4: Crosstown detection (optional)
If there's a Fi base at Crosstown too, add a matching entry to `CROSSTOWN_DEVICES`. If not, Potato will only be detectable at Cabin.

## Alternative: Fi API (if credentials are fixed)
If either account gets a password reset via Fi's "Forgot Password":
- Login: `POST https://api.tryfi.com/auth/login` (email + password, form-encoded)
- GraphQL query returns: GPS location, battery, connection state (base/cellular/user), activity type
- Could provide richer data than WiFi presence (actual GPS, walk tracking, battery alerts)
- Script ready at `openclaw/skills/fi-collar/fi-api.py` (needs working credentials)

## Starlink MAC Redaction Note
Starlink gRPC API redacts the last 3 octets of WiFi client MACs (`XX:XX:XX`). Only wired/mesh devices show full MACs. This means we CANNOT match by full MAC for WiFi clients — must use device name matching.
