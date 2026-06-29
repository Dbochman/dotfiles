---
name: presence
description: Check who is home at the cabin (Philly) or Crosstown (Boston). Use when the user asks "is anyone home", "who's home", "is Julia/Dylan home", "is anyone at the cabin", or presence detection. Cached reporting is read-only; live scans update state and can trigger separate vacancy automation.
allowed-tools: Bash(presence:*)
metadata: {"openclaw":{"emoji":"P"}}
---

# Presence Detection

Cached-state reporting is read-only. A fresh scan rewrites correlated
`state.json`; the separate vacancy WatchPaths automation can react to that
write, so do not run a live scan merely to test presence.

## Quick Check

Read the cached state (updated every 15 min, no scan needed):

```bash
cat ~/.openclaw/presence/state.json
```

Live scans are operational, not read-only: they update `state.json` and may
trigger vacancy actions. Run one only when that side effect is intended:

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
| `possibly_vacant` | Nobody is assigned or freshly observed here, but fresh corroboration at the other location is unavailable |

**Arrival-based (sticky) model:** Once a person is detected at a location, they stay there until positively detected at the other location. Phones going to sleep or missing a scan cycle do NOT cause people to "disappear". Vacancy is only `confirmed_vacant` when everyone has been detected at the other location. When both location snapshots are fresh and both directly report the same person present, that evidence is ambiguous: retain the last unambiguous location and keep both direct-positive locations occupied rather than using scan timestamps or order to infer a relocation. A stale positive neither creates ambiguity nor relocates anyone; only a fresh one-sided positive proves an arrival.

### Per-location tracking

| Location | Tracked people | Vacancy requires |
|----------|---------------|------------------|
| Cabin | Dylan, Julia | Both detected at Crosstown |
| Crosstown | Dylan, Julia | Both detected at Cabin |

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
│     scan.json          │──named cp─▶│ ~/Downloads/              │
└───────────────────────┘            │ com.openclaw.             │
                                     │   presence-receive        │
                                     │ WatchPaths one-shot:      │
                                     │   Validate + atomic move  │
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
| `com.openclaw.presence-receive` | Mac Mini | WatchPaths on `~/Downloads` | Ingest named Crosstown Taildrop state |

### Files on Mac Mini (`~/.openclaw/presence/`)

| File | Contents |
|------|----------|
| `state.json` | Correlated occupancy (the main file to read) |
| `cabin-scan.json` | Raw cabin scan result |
| `crosstown-scan.json` | Raw Crosstown scan result (pushed from MacBook Pro) |
| `events.json` | Rolling log of last 100 transitions |
| `prev-evaluated.json` | Previous evaluation (for transition detection) |

### Logs

- `~/.openclaw/logs/presence-detect.log` (on both machines)
- Receiver ingestion and evaluation summaries share `~/.openclaw/logs/presence-detect.log` on the Mac Mini.

## Detection Methods

### Cabin (Philly)

- **Method**: Starlink gRPC API (`grpcurl` at `192.168.1.1:9000`)
- **Matching**: Device names reported to Starlink router (iPhones use randomized MACs per-network)
- **Dylan**: Device name contains "Dylan" AND "iPhone" or "phone"
- **Julia**: Device name contains "Julia", or unnamed "iPhone" not claimed by Dylan
- **Note**: Mac Mini itself doesn't count as "Dylan present"

### Crosstown (Boston)

- **Method**: ARP scan of `192.168.165.0/24` with stale-entry refresh. After initial ping sweep, tracked IPs' ARP entries are deleted and re-pinged — only devices actually on the network re-populate (ARP is layer 2, works even when iPhones are sleeping and ignoring ICMP).
- **Dylan**: MAC `6c:3a:ff:5f:fc:ba` (private WiFi address off at Crosstown)
- **Julia**: Hostname `julias-iphone`, MAC `38:e1:3d:c0:40:63`, IP `192.168.165.248`
- **Potato** (dog, informational only): Fi collar base station — MAC `d4:3d:39:a7:4b:6c`, hostname `da16200-4b6c`. Does NOT affect vacancy decisions.
- **Note**: Hostname matching (`julias-iphone.lan` from mDNS) is the most durable — survives MAC/IP rotation. MAC and IP are fallbacks.
- **Stale ARP fix**: ARP entries persist 20+ minutes after a device leaves the network. The delete-and-re-ping cycle prevents false presence from cached entries.

## Important Notes

- **Cached reporting is read-only; live scans are not** — `presence-detect.sh cabin` evaluates and writes `state.json`, while a Crosstown scan pushes a file that the Mini receiver evaluates. Either path can trigger the separate vacancy automation.
- **Serialized evaluation** — Cabin scans and Taildrop receipts share one evaluator lock. Each `evaluate` call holds it from the first scan read through the previous-state, event/history, and correlated-state writes; network scanning remains outside the lock.
- **Sticky/arrival-based model** — once detected at a location, a person stays there until detected at the other location. Phone sleep, MAC rotation, or missed ARP scans don't cause flicker.
- **Scan staleness** — only direct positives from scans under 30 minutes old can establish or change a sticky location. Stale or missing observations preserve the previous sticky location; confirmed vacancy still requires a fresh scan at the other location plus sticky assignments of every tracked person there.
- Mac Mini SSHs to MacBook Pro via Tailscale (`ssh dylans-macbook-pro`) using dedicated key `~/.ssh/id_mini_to_mbp` (bypasses 1Password agent which hangs under launchd).
- iOS randomizes MAC addresses per-network — hostname matching is preferred over MAC matching for resilience.

## Skill Boundaries

This skill should normally report the cached state only. Do not invoke a fresh
scanner as a harmless read: its state write can activate the separately managed
vacancy automation.

For related tasks, switch to:
- **cabin-routines** / **crosstown-routines**: Run away/welcome home routines based on presence (user must explicitly request)
- **dog-walk**: Separate Fi GPS-based dog walk automation; no longer uses presence as a departure gate
- **roomba** / **crosstown-roomba**: Start or dock Roombas — presence can inform whether it's safe to vacuum
- Vacancy automation (`com.openclaw.vacancy-actions` LaunchAgent) watches `state.json` and triggers automated actions (lights off, eco mode, Roombas start) when a location becomes `confirmed_vacant` — this is fully automated and does NOT require the presence skill to be invoked
