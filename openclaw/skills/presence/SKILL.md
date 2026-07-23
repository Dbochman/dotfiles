---
name: presence
description: Check who is home at the cabin (Philly) or Crosstown (Boston). Use when the user asks "is anyone home", "who's home", "is Julia/Dylan home", "is anyone at the cabin", or presence detection. Cached reporting is read-only; strict network observation is allowed only after the deployed scanner contract check passes, and scheduled state-refresh scans can trigger separate vacancy automation.
allowed-tools: Bash(presence:*)
metadata: {"openclaw":{"emoji":"P"}}
---

# Presence Detection

Cached-state reporting is read-only. A fresh `observe` is permitted only when
the deployed runtime advertises the strict binding contract; this prevents a
preserved legacy observer from returning private device or network fields. The
scheduled scan modes rewrite presence files, and the separate vacancy
WatchPaths automation can react to those writes.

## Quick Check

Read the cached state (updated every 15 min, no scan needed):

```bash
cat ~/.openclaw/presence/state.json
```

For a fresh, side-effect-free network observation, fail closed unless the
deployed runtime has the exact strict contract marker:

```bash
# Cabin (on Mac Mini)
/usr/bin/grep -Fqx \
  'PRESENCE_SCANNER_DEPLOYMENT_CONTRACT="strict-site-bindings-v1"' \
  ~/.openclaw/workspace/scripts/presence-detect.sh && \
  ~/.openclaw/workspace/scripts/presence-detect.sh observe cabin

# Crosstown (on MacBook Pro)
ssh dylans-macbook-pro \
  "/usr/bin/grep -Fqx \
    'PRESENCE_SCANNER_DEPLOYMENT_CONTRACT=\"strict-site-bindings-v1\"' \
    ~/.openclaw/workspace/scripts/presence-detect.sh && \
   ~/.openclaw/workspace/scripts/presence-detect.sh observe crosstown"
```

After that gate, `observe` prints only a validated, sanitized fresh scan and
does not write raw/correlated presence state, evaluate occupancy, push
Taildrop, or activate vacancy actions. Before strict activation, use only the
cached state; do not bypass the marker check. Observation exits nonzero for an
unavailable, stale, malformed, or legacy runtime.

Scheduled/state-refresh scans are operational: they update state and may
trigger vacancy actions. Run one manually only when that side effect is intended:

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

The tracked strict scanner requires a separate, site-local
`~/.openclaw/presence-devices.json`. That protected identity file is not part
of the presence state directory, must never be printed, and is described
below. Until the candidate's exact source bytes have passed a canary and that
hash is approved on the target host, deployment preserves that host's prior
runtime scanner instead. Cabin and Crosstown approvals are independent.

### Logs

- `~/.openclaw/logs/presence-detect.log` (on both machines)
- Receiver ingestion and evaluation summaries share `~/.openclaw/logs/presence-detect.log` on the Mac Mini.

## Detection Methods

### Cabin (Philly)

- **Method**: Starlink gRPC API (`grpcurl` at `192.168.1.1:9000`). The scanner
  queries the primary controller and every configured mesh node; a mesh request
  carries only that source's exact protected `target_id`.
- **Identity and compatibility**: Cabin schema v1 remains accepted only as the
  original controller-only compatibility format. Schema v2 has exactly one
  `starlink_controller` source and one or more `starlink_mesh` sources, each
  with exact, source-local `captiveClientId` bindings for both residents. A
  resident is present when any successfully validated source has its exact
  positive binding. Display names, substrings, device types, and generic
  iPhone fallbacks are never identity evidence.
- **Controller liveness**: A selected controller row must have boolean
  `dhcpLeaseFound` and `dhcpLeaseActive`, finite
  `secondsUntilDhcpLeaseExpires`, and a non-negative integer `noDataIdleS`.
  It counts present only when both DHCP flags are true, the lease has positive
  remaining time, and idle time is no greater than 300 seconds.
- **Mesh liveness**: A selected mesh row must match the exact node-local
  `captiveClientId`, have `role: "CLIENT"`, finite non-negative
  `associatedTimeS`, finite `signalStrength`, and both `rxStatsValid` and
  `txStatsValid` set to true. Starlink's `active` field is diagnostic-only: it
  may be false or absent for a connected mesh client and never decides
  presence.
- **Whole-observation validity**: Every configured source must return a valid
  clients array and every selected match must be unique. A query failure,
  malformed source response, or duplicate exact match invalidates the entire
  Cabin observation. Incomplete selected-row liveness is per-person unknown:
  another source's strict positive wins the union for that resident, but no
  positive plus unknown fails closed. Absence is authoritative only after all
  sources succeed and none has either a strict positive or unknown evidence
  for that resident.
- **Sanitized output**: Strict observations, state, events, and logs expose
  resident booleans and safe aggregate evidence only. They never emit
  controller or mesh client IDs, mesh target IDs, names, addresses, or raw
  Starlink rows.
- **Activation status and future guard**: Cabin schema v2 is active with exact
  controller and Kitchen-mesh bindings, and the installed scanner hash has a
  site-local mode-`0600` approval. The July 23 activation used an explicit
  operator waiver after two clean scheduled ticks plus 12/12 live
  observations and the full test suite. Any future scanner byte change still
  requires a fresh Cabin canary and exact-hash approval before routine
  deployment can replace the runtime.

### Crosstown (Boston)

- **Method**: Unprivileged ARP reachability scan of `192.168.165.0/24`. After active probes, `arp -anl` supplies receive-side reachability; only a matching device with a live inbound timer on the gateway's interface counts. This works when an iPhone answers ARP but ignores ICMP, without trusting a complete but expired cache row.
- **Identity**: Each resident is bound to one exact site-private MAC from the
  protected Crosstown config. There is no hostname, display-name, or IP
  fallback.
- **Rollout guard**: dotfiles pull preserves the prior MBP scanner instead of
  deploying this version until the protected config validates and the
  candidate's exact source hash has a site-local mode-`0600` canary approval.
- **Stale ARP defense**: Complete ARP entries can persist after a device leaves.
  Presence therefore requires live receive-side reachability plus the exact
  MAC; send-side freshness, `(none)`, `expired`, and `(incomplete)` rows do not
  count. A fresh gateway row is required so LAN failure cannot become a valid
  all-absent scan.

### Protected device bindings

Each host stores only its own site's identities at the same local path. The
file must be a regular, single-link, owner-owned mode-`0600` file in an
owner-only, non-symlink directory. Cabin schema v1 is retained strictly for
controller-only compatibility:

```json
{
  "schema_version": 1,
  "site": "cabin",
  "people": {
    "Dylan": {
      "kind": "starlink_captive_client_id",
      "value": "REDACTED"
    },
    "Julia": {
      "kind": "starlink_captive_client_id",
      "value": "REDACTED"
    }
  }
}
```

As soon as any mesh source is monitored, Cabin requires schema v2. Its
closed-schema shape is:

```json
{
  "schema_version": 2,
  "site": "cabin",
  "sources": [
    {
      "kind": "starlink_controller",
      "bindings": {
        "Dylan": {
          "kind": "starlink_captive_client_id",
          "value": "REDACTED"
        },
        "Julia": {
          "kind": "starlink_captive_client_id",
          "value": "REDACTED"
        }
      }
    },
    {
      "kind": "starlink_mesh",
      "target_id": "REDACTED",
      "bindings": {
        "Dylan": {
          "kind": "starlink_captive_client_id",
          "value": "REDACTED"
        },
        "Julia": {
          "kind": "starlink_captive_client_id",
          "value": "REDACTED"
        }
      }
    }
  ]
}
```

There must be exactly one controller and at least one uniquely targeted mesh
source, and every source must bind both people. The same phone can have a
different exact node-local ID on each source; presence is the union of those
source-specific positives only after every configured source validates.

The MacBook Pro continues to use schema v1 with `"site": "crosstown"` and
`"kind": "mac"` for both people. Values must be distinct within a source, and
one exact identity cannot be assigned to different people across Cabin
sources. Missing, symlinked, insecure, wrong-site, extra-key, malformed, or
duplicate bindings fail the scan closed; there is no heuristic fallback. The
tracked strict scanner's output, state, events, and logs contain only resident
booleans and safe aggregate evidence, never the private bindings, mesh target
IDs, client names, addresses, or raw Starlink records.

Validate a site's protected file without scanning or writing presence state:

```bash
~/.openclaw/workspace/scripts/presence-detect.sh validate-config cabin
ssh dylans-macbook-pro \
  '~/.openclaw/workspace/scripts/presence-detect.sh validate-config crosstown'
```

## Important Notes

- **Cached reporting is read-only; live scans are not** — `presence-detect.sh cabin` evaluates and writes `state.json`, while a Crosstown scan pushes a file that the Mini receiver evaluates. Either path can trigger the separate vacancy automation.
- **Serialized evaluation** — Cabin scans and Taildrop receipts share one evaluator lock. Each `evaluate` call holds it from the first scan read through the previous-state, event/history, and correlated-state writes; network scanning remains outside the lock.
- **Sticky/arrival-based model** — once detected at a location, a person stays there until detected at the other location. Phone sleep, MAC rotation, or missed ARP scans don't cause flicker.
- **Scan staleness** — only direct positives from scans under 30 minutes old can establish or change a sticky location. Stale or missing observations preserve the previous sticky location; confirmed vacancy still requires a fresh scan at the other location plus sticky assignments of every tracked person there.
- **Strict identity is a deployment boundary** — do not activate a new scanner
  merely because its source changed. Validate the protected site-scoped config,
  canary the exact source bytes, and approve that hash on the target host first.
  Any later scanner byte change produces a new hash and requires a new
  per-host canary approval before routine deployment. Approval on the Cabin
  Mac Mini never approves the Crosstown MacBook Pro, or vice versa. Cabin
  provisioning remains an attended prerequisite.
- Mac Mini SSHs to MacBook Pro via Tailscale (`ssh dylans-macbook-pro`) using dedicated key `~/.ssh/id_mini_to_mbp` (bypasses 1Password agent which hangs under launchd).
- iOS private addresses are network-specific. Bind the exact current
  Crosstown MAC address for each phone; never compensate for rotation with a
  hostname or generic-device fallback.

## Skill Boundaries

This skill should normally report cached state. If a fresh network-only reading
is necessary, use `observe`; do not invoke the state-refresh modes as harmless
reads because their writes can activate separately managed vacancy automation.

For related tasks, switch to:
- **cabin-routines** / **crosstown-routines**: Run away/welcome home routines based on presence (user must explicitly request)
- **dog-walk**: Separate Fi GPS-based dog walk automation; no longer uses presence as a departure gate
- **roomba** / **crosstown-roomba**: Start or dock Roombas — presence can inform whether it's safe to vacuum
- Vacancy automation (`com.openclaw.vacancy-actions` LaunchAgent) watches `state.json` and triggers automated actions (lights off, eco mode, Roombas start) when a location becomes `confirmed_vacant` — this is fully automated and does NOT require the presence skill to be invoked
