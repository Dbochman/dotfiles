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

## Output

JSON with presence state per person:

```json
{
  "location": "cabin",
  "timestamp": "2026-03-01T21:57:42.231Z",
  "totalClients": 19,
  "presence": {
    "Dylan": { "present": true, "device": "Dylan's iPhone", "ip": "192.168.1.x", ... },
    "Julia": { "present": true, "device": "iPhone", "ip": "192.168.1.92", ... }
  }
}
```

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
