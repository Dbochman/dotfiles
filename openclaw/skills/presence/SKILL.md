---
name: presence
description: Check who is home at the cabin (Philly) or Crosstown (Boston). Use when the user asks "is anyone home", "who's home", "is Julia/Dylan home", "is anyone at the cabin", presence detection, or when routines need to know occupancy.
allowed-tools: Bash(presence:*)
metadata: {"openclaw":{"emoji":"P"}}
---

# Presence Detection

Detect who is home at each location by querying local network devices.

## Usage

**Cabin** (run on Mac Mini — local Starlink API):
```bash
~/.openclaw/workspace/scripts/presence-detect.sh cabin
```

**Crosstown** (run on MacBook Pro via crosstown-network skill):
```bash
ssh dylans-macbook-pro "~/.openclaw/workspace/scripts/presence-detect.sh crosstown"
```

**Both** (run cabin locally, Crosstown requires separate SSH call):
```bash
~/.openclaw/workspace/scripts/presence-detect.sh cabin
ssh dylans-macbook-pro "~/.openclaw/workspace/scripts/presence-detect.sh crosstown"
```

**Important**: Crosstown scan must run ON the MacBook Pro (it does a local ARP scan of 192.168.165.0/24). The Mac Mini cannot SSH to the MacBook Pro directly.

## Correlated State (on Mac Mini)

The Mac Mini maintains a correlated view of both locations at `~/.openclaw/presence/state.json`:

```json
{
  "timestamp": "2026-03-01T22:16:06.997Z",
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
- **`occupied`**: At least one tracked person is present
- **`confirmed_vacant`**: ALL tracked people are absent AND confirmed present at the other location
- **`possibly_vacant`**: Nobody detected, but can't confirm they're at the other location (phones may be sleeping)

**Vacancy is only `confirmed_vacant` when everyone has left AND arrived at the other location.** This prevents false vacants from sleeping phones or network glitches.

### Transitions
- `{"location":"cabin","from":"occupied","to":"confirmed_vacant"}` — cabin emptied, everyone at Crosstown
- `{"person":"Dylan","event":"relocated","from":"cabin","to":"crosstown"}` — Dylan moved

## Reading State (No Scan Needed)

LaunchAgents update every 15 minutes. Read cached state instantly:

```bash
cat ~/.openclaw/presence/state.json
```

**Recent events** (last 100 transitions):
```bash
cat ~/.openclaw/presence/events.json
```

## Architecture

1. **Cabin scan** (`com.openclaw.presence-cabin`): Runs every 15 min on Mac Mini, queries Starlink gRPC API, then evaluates correlated state
2. **Crosstown scan** (`com.openclaw.presence-crosstown`): Runs every 15 min on MacBook Pro, ARP scans the LAN, pushes results to Mac Mini via `tailscale file cp`
3. **Receiver** (`com.openclaw.presence-receive`): KeepAlive daemon on Mac Mini, accepts Tailscale file transfers and triggers re-evaluation
4. **Evaluator**: Reads both scan files, correlates presence across locations, writes `state.json`

## Automation Use Cases

Use `occupancy` and `transitions` to trigger routines:
- **Away mode**: When occupancy is `confirmed_vacant` → eco thermostats, lights off, start Roombas
- **Welcome home**: When occupancy transitions from `confirmed_vacant` to `occupied` → lights on, comfortable temp
- **Eco heating**: Set thermostats to eco when `confirmed_vacant` (safe — everyone is confirmed elsewhere)
- **Ignore `possibly_vacant`**: Don't turn off heat just because phones went to sleep

## Detection Methods

### Cabin (Philly)
- **Method**: Starlink gRPC API (`grpcurl` at `192.168.1.1:9000`)
- **Matching**: Device names reported to Starlink router
- **Dylan**: Matches "Dylan" + "iPhone"/"phone" in device name
- **Julia**: Matches "Julia" in device name, or unnamed "iPhone" not claimed by Dylan

### Crosstown (Boston)
- **Method**: ARP scan via SSH to MacBook Pro (`192.168.165.0/24`)
- **Dylan**: MAC `6c:3a:ff:5f:fc:ba` (private WiFi address off at Crosstown)
- **Julia**: Not yet identified (TBD)

## Important Notes

- iPhones in sleep mode may not respond immediately — results are best-effort
- Cabin uses randomized MACs per-network, so matching is by device name, not MAC
- The Mac Mini itself is always on the cabin network — it doesn't count as "Dylan present"
- State is saved to `~/.openclaw/presence/state.json`
- Logs: `/tmp/presence-detect.log`

## Examples

Check if anyone is at the cabin:
```bash
~/.openclaw/workspace/scripts/presence-detect.sh cabin
```

Check Crosstown (via crosstown-network skill):
```bash
ssh dylans-macbook-pro "~/.openclaw/workspace/scripts/presence-detect.sh crosstown"
```
