---
name: presence
description: Check who is home at the cabin (Philly) or Crosstown (Boston). Use when the user asks "is anyone home", "who's home", "is Julia/Dylan home", "is anyone at the cabin", or presence detection. This skill is read-only — it reports occupancy status but does NOT trigger any actions.
allowed-tools: Bash(presence:*)
metadata: {"openclaw":{"emoji":"P"}}
---

# Presence Detection

Detect who is home at each location by querying local network devices. This is a **detection-only** system — it tracks and reports occupancy but takes no automated actions (no lights, thermostats, or routines are triggered).

## Quick Check

Read the cached state (updated every 15 min, no scan needed):

```bash
cat ~/.openclaw/presence/state.json
```

Or run a fresh scan:

```bash
# Cabin (on Mac Mini)
~/.openclaw/workspace/scripts/presence-detect.sh cabin

# Crosstown (on MacBook Pro via crosstown-network skill)
ssh dylans-macbook-pro "~/.openclaw/workspace/scripts/presence-detect.sh crosstown"
```

## Correlated State

The Mac Mini maintains a correlated view of both locations at `~/.openclaw/presence/state.json`:

```json
{
  "timestamp": "2026-03-01T22:20:14.043Z",
  "people": {
    "Dylan": { "cabin": false, "crosstown": true, "location": "crosstown" },
    "Julia": { "cabin": true, "crosstown": false, "location": "cabin" }
  },
  "cabin": { "occupancy": "occupied", "scanAge": "0min", "fresh": true },
  "crosstown": { "occupancy": "occupied", "scanAge": "0min", "fresh": true },
  "transitions": []
}
```

### Occupancy values

| Value | Meaning |
|-------|---------|
| `occupied` | At least one tracked person is present |
| `confirmed_vacant` | ALL tracked people absent AND confirmed present at the other location |
| `possibly_vacant` | Nobody detected, but can't confirm they're elsewhere (phones may be sleeping) |

**Vacancy is only `confirmed_vacant` when everyone has left AND arrived at the other location.** This prevents false vacants from sleeping phones or network glitches.

### Per-location tracking

| Location | Tracked people | Vacancy requires |
|----------|---------------|------------------|
| Cabin | Dylan, Julia | Both absent at cabin AND both at Crosstown |
| Crosstown | Dylan (Julia TBD) | Dylan absent at Crosstown AND at cabin |

When Julia's Crosstown MAC is identified, add to `CROSSTOWN_TRACKED` and `CROSSTOWN_DEVICES` in the script.

### Transitions

Logged when occupancy or person location changes between evaluations:

```json
{"location": "cabin", "from": "occupied", "to": "confirmed_vacant", "timestamp": "..."}
{"person": "Dylan", "event": "relocated", "from": "cabin", "to": "crosstown", "timestamp": "..."}
```

Recent events (last 100):
```bash
cat ~/.openclaw/presence/events.json
```

## Architecture

```
MacBook Pro (Crosstown)              Mac Mini (Cabin)
┌───────────────────────┐            ┌──────────────────────────┐
│ com.openclaw.          │            │ com.openclaw.             │
│   presence-crosstown   │            │   presence-cabin          │
│ Every 15 min:          │            │ Every 15 min:             │
│   ARP scan 192.168.165 │            │   Starlink gRPC API       │
│   Write crosstown-     │──tailscale │   Write cabin-scan.json   │
│     scan.json          │──file cp──▶│                           │
└───────────────────────┘            │ com.openclaw.             │
                                     │   presence-receive        │
                                     │ KeepAlive daemon:         │
                                     │   Receive crosstown scan  │
                                     │   Trigger evaluate        │
                                     │                           │
                                     │ Evaluator:                │
                                     │   Correlate both scans    │
                                     │   Write state.json        │
                                     └──────────────────────────┘
```

### LaunchAgents

| Agent | Host | Schedule | Purpose |
|-------|------|----------|---------|
| `com.openclaw.presence-cabin` | Mac Mini | Every 15 min | Scan cabin WiFi, evaluate |
| `com.openclaw.presence-crosstown` | MacBook Pro | Every 15 min | Scan Crosstown LAN, push to Mac Mini |
| `com.openclaw.presence-receive` | Mac Mini | KeepAlive | Receive Crosstown state via Tailscale |

### Files on Mac Mini (`~/.openclaw/presence/`)

| File | Contents |
|------|----------|
| `state.json` | Correlated occupancy (the main file to read) |
| `cabin-scan.json` | Raw cabin scan result |
| `crosstown-scan.json` | Raw Crosstown scan result (pushed from MacBook Pro) |
| `events.json` | Rolling log of last 100 transitions |
| `prev-evaluated.json` | Previous evaluation (for transition detection) |

### Logs

- `/tmp/presence-detect.log` (on both machines)
- `/tmp/presence-receive.log` (on Mac Mini)

## Detection Methods

### Cabin (Philly)

- **Method**: Starlink gRPC API (`grpcurl` at `192.168.1.1:9000`)
- **Matching**: Device names reported to Starlink router (iPhones use randomized MACs per-network)
- **Dylan**: Device name contains "Dylan" AND "iPhone" or "phone"
- **Julia**: Device name contains "Julia", or unnamed "iPhone" not claimed by Dylan
- **Note**: Mac Mini itself doesn't count as "Dylan present"

### Crosstown (Boston)

- **Method**: ARP scan of `192.168.165.0/24` with targeted ping for known IPs
- **Dylan**: MAC `6c:3a:ff:5f:fc:ba` (private WiFi address off at Crosstown)
- **Julia**: Not yet identified (TBD — needs MAC from Crosstown WiFi)
- **Note**: Sleeping iPhones get a targeted `ping -c3 -W2` for reliability

## Important Notes

- **No automated actions** — this system only detects and reports. Routines (away mode, welcome home, etc.) must be explicitly requested by the user.
- iPhones in sleep mode may not respond to the first ping — the script uses targeted pings with longer timeouts for known devices.
- Scan staleness: if either location's scan is >30 min old, the evaluator won't use it for cross-location vacancy confirmation (stays `possibly_vacant` instead of `confirmed_vacant`).
- Crosstown scan must run ON the MacBook Pro (Mac Mini can't SSH to it — 1Password agent needs GUI approval).
