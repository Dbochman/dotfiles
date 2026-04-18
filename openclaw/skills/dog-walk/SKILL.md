---
name: dog-walk
description: Automated dog walk detection and Roomba control. Detects departures via Fi GPS collar, starts Roombas, and monitors return via Ring motion + WiFi + Fi GPS. Use when asked about dog walks, Roomba automation during walks, or walk tracking.
allowed-tools: Bash(dog-walk:*)
metadata: {"openclaw":{"emoji":"D","requires":{"bins":["fi-collar","crosstown-roomba","roomba"]}}}
---

# Dog Walk Automation

Detects dog walks via **Fi GPS collar** (departure) and manages Roomba automation with **multi-signal return detection** (Ring motion + WiFi + Fi GPS).

## Current Model

- Departure detection is **Fi-only**. Ring and presence no longer decide whether a walk started.
- The listener uses Potato's Fi GPS/geofence result to choose the home, and stores the last confirmed in-geofence home as `home_location`.
- Walks now get immutable `walk_id` and `origin_location` fields at departure.
- Route files are persisted during return monitoring at `~/.openclaw/dog-walk/routes/<location>/<YYYY-MM-DD>/<walk_id>.json`.
- Route files include `distance_m`, `point_count`, inferred `end_location`, and `is_interhome_transit`.
- Inter-home transits are filtered out of the dashboard route-summary API, so future map views only show same-home walks.
- Walk hours now cover the full day in three contiguous sections: `7 AM-12 PM`, `12 PM-5 PM`, `5 PM-9 PM`.

## How It Works

### Departure Detection (combo triggers + GPS fallback)

Departure uses **combo triggers** for fast detection (~1 min), with a GPS geofence fallback.

#### Combo Trigger 1: Ring + Fi Base Disconnect

1. Ring doorbell detects human motion → timestamp stored per location
2. Polling immediately switches from 3min to 30s intervals
3. When Fi collar disconnects from base station AND recent Ring motion exists (within 5min) → **departure confirmed immediately**

**Typical latency:** ~1 minute

#### Combo Trigger 2: Fi Activity Rest→Walk + Base Disconnect

1. Fi collar activity transitions from Rest to Walk (Fi detects the dog is moving)
2. Base station is already disconnected (dog left BLE range)
3. Both signals together → **departure confirmed immediately**

This trigger works without Ring (e.g., leaving through back door at cabin) and fires as soon as Fi recognizes the walk activity.

**Typical latency:** ~1-2 minutes

#### Fallback: GPS Geofence

If no combo trigger fires, the GPS-only path still works:

- 2 consecutive Fi GPS readings outside geofence, confirmed after a time threshold
- Both readings must be < 10 min old (not stale)
- Only during walk hours, using the last home geofence Potato was inside as the departure anchor
- Only when no walk is already active

**Typical latency:** ~5-7 minutes (normal), ~2 minutes (accelerated)

**Acceleration:** When the base station is disconnected OR Fi activity is Walk, polling switches from 3min to 30s and the confirmation threshold drops from 3min to 60s. Backyard time does not trigger departure — the 30m geofence (Crosstown) / 75m geofence (Cabin) is large enough that GPS still shows Potato at home even with base station BLE out of range (~30-50m).

**Walk hours:** 7 AM-12 PM, 12-5 PM, 5-9 PM

**Pre-checks:**
- **Time-of-day filter**: only active during walk hours
- **Base-station echo filter**: when Fi API is slow to transition from Rest to Walk, it returns base station coords as pet position. If pet coords match a home location within 5m and connection is not "Base", the reading is discarded as stale.

**Per-location Roomba commands:**

| Location | Start | Dock |
|----------|-------|------|
| Crosstown | `crosstown-roomba start all` | `crosstown-roomba dock all` |
| Cabin | `roomba start floomba` + `roomba start philly` | `roomba dock floomba` + `roomba dock philly` |

**Fi GPS geofences:**

| Location | Radius |
|----------|--------|
| Crosstown (19 Crosstown Ave, West Roxbury) | 30m |
| Cabin (95 School House Rd, Phillipston) | 75m |

### Return Detection (multi-signal)

After departure, the return monitor uses three signals — any one triggers Roomba docking:

| Signal | Interval | How it works |
|--------|----------|-------------|
| **Ring motion** | Event-driven | Person detected at doorbell during monitoring |
| **WiFi / network presence** | Every 30s (after 10min) | ARP scan (Crosstown via MBP) or Starlink gRPC (Cabin). Detects phone reconnecting to WiFi. **Ignored for first 10 minutes** — phones linger on WiFi at the front door. |
| **Fi GPS** | Every 30s | Polls Potato's Fi collar GPS. Docks when Potato re-enters home geofence. Base-station echo detection prevents false "at home" readings. |
| **Fi GPS (inter-home)** | Every 30s | If Potato enters the *other* home's geofence during monitoring, the walk is auto-finalized as an inter-home transit. Roombas dock at origin, home anchor updates to the new location. |

- Departure GPS point is seeded as the first route point for dashboard maps
- 2 minutes after departure, a network scan identifies **who left**
- WiFi return signals are suppressed for the first 10 minutes (phones stay connected at front door)
- On return, the full Fi `OngoingWalk` path is fetched (dense polyline) and merged into the route file
- **Fi walk enrichment** queries `activityFeed` for authoritative timestamps and distance, then **merges all Walk segments that overlap our outing window** (`[our_started_at - 5min, our_ended_at + 5min]`). Fi splits a single outing into multiple Walks when the dog pauses for Play/Rest (sniffing, yard time); the merge takes the earliest start, latest end, sum of distances, and records `fi_walk_count` for transparency. A background thread retries at 5 / 10 / 20 min after return to catch Walks Fi finalizes late — retries are idempotent and always scheduled so late segments can be merged in.
- A final Fi GPS point is captured before docking (for route completeness)
- An iMessage notification is sent on return with walk duration and which signal triggered it
- Safety fallback: auto-docks after 2 hours if no return detected
- **Resilient finalization:** once a return signal is confirmed, the loop always exits. Walk path capture, dock, iMessage, and state updates are each wrapped in individual try/except blocks so a failure in any step cannot cause the monitor to loop back and re-trigger
- **Dock sends stop first:** the `crosstown-roomba dock` command sends `stop` before `dock` because iRobot's MQTT `dock` is silently ignored during active cleaning
- **Post-dock verification:** 3 minutes after the dock command, a background thread checks if roombas are actually on the dock (`Charging (on dock)` in status). If not, it retries the dock command up to 2 times (3min between each). If still not docked after all retries, sends an iMessage warning. State is updated with `dock_verified: true/false` and `dock_retry_count`.

### GPS Tracking Mode (Lost Dog)

On departure, the collar switches to **LOST_DOG mode** for high-frequency GPS (~15-30s updates vs ~3-7min in NORMAL). This produces dense route data for dashboard mapping.

**Battery protection:** If consecutive GPS readings show car speeds (>30mph) for 6+ minutes, the collar switches back to NORMAL to avoid unnecessary drain during inter-home car trips. Speed resets if Potato slows to walking pace.

The collar always resets to NORMAL when the walk ends (via the return monitor's `finally` block). On listener startup, the collar mode is checked and reset to NORMAL if stuck in LOST_DOG (safety net for crashes/power outages).

### Roomba Snooze

Roomba automation can be temporarily disabled per-location via the **Roomba Dashboard** (port 8553). When snoozed:
- **Start commands are skipped** — Roombas won't start on departure
- **Dock commands still execute** — Roombas should never be left running
- **Walk tracking continues** — GPS, return detection, and route data are unaffected

Snooze state is stored at `~/.openclaw/dog-walk/snooze.json` and expires automatically.

Dashboard UI: Snooze bar on the Roomba Dashboard (port 8553) with 1h / 3h / 8h / Indef presets per location, plus a Clear button.

API: `POST http://localhost:8553/api/snooze` with `{"location": "crosstown", "minutes": 60}` (or `"all"`, `0` to clear).

### Roomba Cooldown

Start commands have a 2-hour cooldown to prevent re-triggering. Dock commands always execute immediately — Roombas should never be left running because of a cooldown.

### Manual Trigger

```bash
dog-walk-start <location>    # "cabin" or "crosstown"
```

Starts Roombas and signals the listener to begin return monitoring via inbox IPC.

## State Tracking

- Current state: `~/.openclaw/dog-walk/state.json`
- Daily history: `~/.openclaw/dog-walk/history/YYYY-MM-DD.jsonl`
- Per-walk routes: `~/.openclaw/dog-walk/routes/<location>/<YYYY-MM-DD>/<walk_id>.json`
- Inbox (IPC): `~/.openclaw/dog-walk/inbox/`

## LaunchAgent

The listener runs as a persistent `KeepAlive` LaunchAgent (`ai.openclaw.dog-walk-listener`).

To check:
```bash
launchctl list | grep dog-walk-listener    # should show PID
tail -f ~/.openclaw/logs/dog-walk-listener.log
```

To restart:
```bash
launchctl unload ~/Library/LaunchAgents/ai.openclaw.dog-walk-listener.plist
launchctl load ~/Library/LaunchAgents/ai.openclaw.dog-walk-listener.plist
```

## Deploy Notes

Recent dog-walk changes touched these paths:

- `openclaw/skills/dog-walk/dog-walk-listener.py`
- `openclaw/bin/dog-walk-dashboard.py`
- `openclaw/bin/roomba-dashboard.py`
- `openclaw/skills/fi-collar/fi-api.py`

If deploying to the Mac Mini, make sure the updated files are present under `~/.openclaw/`, then restart:

```bash
launchctl kickstart -k gui/$(id -u)/ai.openclaw.dog-walk-listener
launchctl kickstart -k gui/$(id -u)/ai.openclaw.dog-walk-dashboard
launchctl kickstart -k gui/$(id -u)/ai.openclaw.roomba-dashboard
```

Quick verification:

```bash
tail -20 ~/.openclaw/logs/dog-walk-listener.log
tail -20 ~/.openclaw/logs/dog-walk-dashboard.log
tail -20 ~/.openclaw/logs/roomba-dashboard.log
curl -s http://localhost:8552/api/routes?days=30 | jq '.meta'
curl -s http://localhost:8553/api/roombas | jq '.'
```

## Skill Boundaries

This skill handles dog walk detection and Roomba automation triggered by Fi GPS departure.

For related tasks, switch to:
- **ring-doorbell**: Check Ring doorbell status, events, video, health (CLI only)
- **fi-collar**: Direct Fi collar GPS/battery queries
- **roomba**: Direct Roomba control at the Cabin
- **crosstown-roomba**: Direct Roomba control at Crosstown
- **cabin-routines** / **crosstown-routines**: Full home routines
- Vacancy automation (`com.openclaw.vacancy-actions`) is separate — starts Roombas on vacancy
