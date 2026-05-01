# Fi Collar Presence Integration — Plan

## Status: COMPLETE (2026-05-01)

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

## Base Station Presence Tracking — REMOVED (2026-04-02)
Fi GPS polling (every 3 min in ring-listener) provides real coordinates and geofence status, making base station network detection redundant. Removed Potato entries from both CABIN_DEVICES and CROSSTOWN_DEVICES in presence-detect.sh. A second base station was added at the cabin but not tracked in presence — Fi GPS covers both locations.

## Fi API — IMPLEMENTED (2026-04-01)
Password reset completed for `dylanbochman@gmail.com`. Fi API is fully operational.
- CLI: `fi-collar location` / `fi-collar status` (wrapper at `~/.openclaw/bin/fi-collar`)
- API script: `openclaw/skills/fi-collar/fi-api.py`
- Auth: `TRYFI_EMAIL` + `TRYFI_PASSWORD` in `~/.openclaw/.secrets-cache`
- Session cached at `~/.config/fi-collar/session.json` (12hr TTL, auto-re-login on 401)
- GPS coordinates available at both Crosstown and Cabin (collar has built-in GNSS + cellular)
- Integrated into ring-listener return monitor as Phase 1 of [fi-gps-dog-walk-integration](archive/fi-gps-dog-walk-integration.md)

## Potato re-added to presence scan via Fi GPS — 2026-05-01

Reversed the 2026-04-02 removal: Potato is back in `state.json.people`, but
this time via Fi cellular GPS instead of base-station WiFi detection. Works
at both locations (cellular fallback when out of base range).

### Implementation
- New `scan_potato()` function in `presence-detect.sh` calls
  `/opt/homebrew/bin/fi-collar location` in a subshell with
  `~/.openclaw/.secrets-cache` sourced (so `TRYFI_*`, `CROSSTOWN_LAT/LON`,
  `CABIN_LAT/LON` are available without leaking into the parent env).
- `fi-api.py`'s existing `nearest_location()` does the cabin/crosstown
  classification (150m / 300m radii via haversine).
- Result cached at `~/.openclaw/presence/potato-scan.json`; the evaluator
  reads it and merges Potato into `people['Potato']` with `location`,
  `activity`, `distance_m`, and `source: "fi-collar"`.
- Runs on every `cabin` cron tick (Fi API call ~1-2s); also exposed as
  `presence-detect.sh potato` for manual testing.

### Vacancy gating unchanged
Potato is NOT in `CABIN_TRACKED` / `CROSSTOWN_TRACKED`. Cabin and crosstown
occupancy still gates on humans only — dog home alone still triggers eco /
Roombas / lights-off, as before.

### `location` values
- `"cabin"` — within 300m of `CABIN_LAT/LON` and `at_location: true`
- `"crosstown"` — within 150m of `CROSSTOWN_LAT/LON`
- `"traveling"` — has GPS but outside both radii (walks, car trips, etc.)
- key omitted entirely when Fi returns an error or no GPS fix

### Coexistence with dog-walk listener
The dog-walk listener still polls Fi GPS independently (every 3 min) for
active walk detection; that path is unchanged. The presence scan provides
the resting-state location signal that downstream consumers (home control
plane dashboard, `state.json` readers) want without each having to call the
Fi API themselves.
