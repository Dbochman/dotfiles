---
name: dog-walk
description: Automated dog walk detection and Roomba control. Detects departures via Fi GPS collar, starts Roombas, and monitors return via Ring motion + WiFi + Fi GPS. Use when asked about dog walks, Roomba automation during walks, or walk tracking.
allowed-tools: Bash(dog-walk:*)
metadata: {"openclaw":{"emoji":"D","requires":{"bins":["fi-collar","crosstown-roomba","roomba"]}}}
---

# Dog Walk Automation

Detects dog walks via **Fi GPS collar** (departure) and manages Roomba automation with **multi-signal return detection** (Ring motion + WiFi + Fi GPS).

## Current Model

- Departure decisions remain **Fi-owned**. A recent front-door Ring person signal
  may accelerate Fi confirmation, but Ring alone never starts a walk.
- `ai.openclaw.ring-event-listener` owns the sole Ring FCM connection. It sends
  only a short-lived local `person_motion` hint to
  `ai.openclaw.dog-walk-automation`; the dog-walk service owns Fi, network,
  route, collar, and Roomba policy.
- Ring ingress reconciles bounded read-only provider history every five minutes.
  Events missed by FCM are accepted only within 15 minutes, marked as backfill,
  and cannot ring the direct-notification or dog-walk automation paths. Finding
  a missed event marks live ingress degraded and restarts the push listener;
  only a later live callback restores ingress health. The publisher retries one
  failed idempotent event-bus spool commit before counting terminal failure.
- The runtime applies a bounded compatibility normalization for the first
  encoded Web Push key and salt parameters before `firebase-messaging` decrypts
  them; credentials and message content are not logged or rewritten.
- The listener uses Potato's Fi GPS/geofence result to choose the home, and stores the last confirmed in-geofence home as `home_location`.
- Before any automatic Fi-triggered Roomba start, a complete fresh read-only
  network observation must show both residents absent. If either resident is
  still home—or the observation is unavailable or omits a resident—the collar
  departure is recorded as suppressed, no Roomba command is sent, LOST_DOG
  mode is not enabled, and the same outside trip remains suppressed until the
  collar reaches a home geofence. Manual starts remain explicit.
- Walks now get immutable `walk_id` and `origin_location` fields at departure.
- Route files are persisted atomically during return monitoring at `~/.openclaw/dog-walk/routes/<location>/<YYYY-MM-DD>/<walk_id>.json`; per-route locking keeps concurrent polling, finalization, car marking, and delayed Fi enrichment from dropping one another's fields.
- Route files include `distance_m`, `point_count`, inferred `end_location`, and `is_interhome_transit`.
- Inter-home transits are filtered out of the dashboard route-summary API, so future map views only show same-home walks.
- Walk hours now cover the full day in three contiguous sections: `7 AM-12 PM`, `12 PM-5 PM`, `5 PM-9 PM`.

## How It Works

### Departure Detection (combo triggers + GPS fallback)

Departure uses **combo triggers** for fast detection (~1 min), with a GPS geofence fallback.

#### Combo Trigger 1: Ring + Fi Base Disconnect

1. Ring doorbell detects human motion → the Ring ingress sends a protected
   local signal containing only the safe site alias
2. Dog-walk automation stores the short-lived timestamp per location
3. Polling immediately switches from 3min to 30s intervals
4. When Fi collar disconnects from base station AND recent Ring motion exists (within 5min) → **departure confirmed immediately**

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
- **Resident-presence guard**: all automatic Fi paths require a complete fresh
  observation showing Dylan and Julia both absent before any Roomba command.
  This prevents a collar-only car trip from starting cleaners around someone
  who remained home.

**Per-location Roomba commands:**

| Location | Start | Dock |
|----------|-------|------|
| Crosstown | `crosstown-roomba start all` | `crosstown-roomba dock all` |
| Cabin | `roomba start floomba` + `roomba start philly` | `roomba dock floomba` + `roomba dock philly` |

**Fi GPS geofences:**

| Location | Radius |
|----------|--------|
| Crosstown (Crosstown residence, West Roxbury) | 30m |
| Cabin (Cabin, Phillipston) | 75m |

### Return Detection (multi-signal)

After departure, the return monitor uses three signals — any one triggers Roomba docking:

| Signal | Interval | How it works |
|--------|----------|-------------|
| **Ring motion** | Event-driven | Person detected at doorbell during monitoring |
| **WiFi / network presence** | Every 30s (after 10min) | Read-only `presence-detect.sh observe <location>` via the MBP (Crosstown) or locally (Cabin). Detects phone reconnecting without writing correlated presence state, pushing Taildrop, or triggering vacancy automation. **Ignored for first 10 minutes** — phones linger on WiFi at the front door. |
| **Fi GPS** | Every 30s | Polls Potato's Fi collar GPS. Docks when Potato re-enters home geofence. Base-station echo detection prevents false "at home" readings. |
| **Fi GPS (inter-home)** | Every 30s | If Potato enters the *other* home's geofence during monitoring, the walk is auto-finalized as an inter-home transit. Roombas dock at origin, home anchor updates to the new location. |

- Departure GPS point is seeded as the first route point for dashboard maps
- 2 minutes after departure, the same read-only network observation identifies
  **who left**. It reports only residents proven absent after being recently
  present; an empty result stays empty instead of falling back to everyone who
  had been at the house. Missing, stale, malformed, or incomplete observations
  fail closed: they neither dock Roombas nor infer walkers.
- WiFi return signals are suppressed for the first 10 minutes (phones stay connected at front door)
- On return, the full Fi `OngoingWalk` path is fetched (dense polyline) and merged into the route file
- **Fi walk enrichment** queries `activityFeed` for authoritative timestamps and distance, then **merges all Walk segments that overlap our outing window** (`[our_started_at - 5min, our_ended_at + 5min]`). Fi splits a single outing into multiple Walks when the dog pauses for Play/Rest (sniffing, yard time); the merge takes the earliest start, latest end, sum of distances, and records `fi_walk_count` for transparency. A background thread retries at 5 / 10 / 20 min after return to catch Walks Fi finalizes late — retries are idempotent and always scheduled so late segments can be merged in.
- A final Fi GPS point is captured before docking (for route completeness)
- Safety fallback: auto-docks after 2 hours if no return detected (sends iMessage)
- **Resilient finalization:** once a return signal is confirmed, the loop always exits. Walk path capture, dock, and state updates are each wrapped in individual try/except blocks so a failure in any step cannot cause the monitor to loop back and re-trigger
- **Dock sends stop first:** the `crosstown-roomba dock` command sends `stop` before `dock` because iRobot's MQTT `dock` is silently ignored during active cleaning. The CLI now skips stop+dock for any robot whose `phase` is `charge` (already on dock), so the verify-retry path doesn't re-stop a docked robot when only one needs a re-dock.
- **Post-dock verification:** 3 minutes after the dock command, a background thread checks if roombas are actually on the dock (`Charging (on dock)` in status). If not, it retries the dock command up to 2 times (3min between each). If still not docked after all retries, sends an iMessage warning. State is updated with `dock_verified: true/false` and `dock_retry_count`.
- Dog-walk operational warnings use one bounded send through OpenClaw's
  already-supervised native iMessage channel. Delivery requires the protected
  exact `chat_id` target, accepts matching successful gateway or native-direct
  channel receipts, and never retries an ambiguous timeout. Direct Ring dings
  belong to the independent Ring ingress service.

### GPS Tracking Mode (Lost Dog)

On departure, the collar switches to **LOST_DOG mode** for high-frequency GPS (~15-30s updates vs ~3-7min in NORMAL). This produces dense route data for dashboard mapping.

**Battery protection:** If consecutive GPS readings show car speeds (>30mph) for 6+ minutes, the collar switches back to NORMAL to avoid unnecessary drain during inter-home car trips. Speed resets if Potato slows to walking pace.

The collar always resets to NORMAL when the walk ends (via the return monitor's `finally` block). On listener startup, the collar mode is checked and reset to NORMAL if stuck in LOST_DOG (safety net for crashes/power outages).

### Roomba Snooze

Roomba automation can be temporarily disabled per-location via the **Roomba Dashboard** (port 8553). When snoozed:
- **Automatic start commands are skipped** — both dog-walk departures and
  vacancy automation honor the same per-location policy
- **Dock commands still execute** — Roombas should never be left running
- **Walk tracking continues** — GPS, return detection, and route data are unaffected

Manual CLI/dashboard start commands remain explicit operator actions and do
not consult the automation snooze.

Snooze state is stored at `~/.openclaw/dog-walk/snooze.json` and expires automatically.

Dashboard UI: Snooze bar on the Roomba Dashboard (port 8553) with 1h / 3h / 8h / Indef presets per location, plus a Clear button.

API: `POST http://localhost:8553/api/snooze` with `{"location": "crosstown", "minutes": 60}` (or `"all"`, `0` to clear).

### Roomba Cooldown

Start commands have a 2-hour cooldown to prevent re-triggering. Dock commands always execute immediately — Roombas should never be left running because of a cooldown.

### Manual Trigger

```bash
dog-walk-start <location>    # "cabin" or "crosstown"
```

Starts Roombas and signals dog-walk automation to begin return monitoring via inbox IPC.

## State Tracking

- Current state: `~/.openclaw/dog-walk/state.json`
- Daily history: `~/.openclaw/dog-walk/history/YYYY-MM-DD.jsonl`
- Per-walk routes: `~/.openclaw/dog-walk/routes/<location>/<YYYY-MM-DD>/<walk_id>.json`
- Inbox (IPC): `~/.openclaw/dog-walk/inbox/`

## LaunchAgents

Two persistent `KeepAlive` services have explicit ownership:

- `ai.openclaw.dog-walk-automation`: Fi departure and return policy, network
  observation, routes, collar modes, and Roomba lifecycle.
- `ai.openclaw.ring-event-listener`: sole Ring FCM ingress, event-bus
  publication, direct dings, and the narrow local dog-walk signal.

To check:
```bash
launchctl list | grep -E 'dog-walk-automation|ring-event-listener'
tail -f ~/.openclaw/logs/dog-walk-automation.log
tail -f ~/.openclaw/logs/ring-event-listener.log
```

To restart:
```bash
launchctl kickstart -k gui/$(id -u)/ai.openclaw.dog-walk-automation
launchctl kickstart -k gui/$(id -u)/ai.openclaw.ring-event-listener
```

## Deploy Notes

Recent dog-walk changes touched these paths:

- `openclaw/skills/dog-walk/dog-walk-automation.py`
- `openclaw/skills/dog-walk/ring-event-listener.py`
- `openclaw/skills/dog-walk/service-runtime.py` (shared implementation; never a service entry point)
- `openclaw/bin/dog-walk-dashboard.py`
- `openclaw/bin/roomba-dashboard.py`
- `openclaw/skills/fi-collar/fi-api.py`

If deploying to the Mac Mini, make sure the updated files are present under `~/.openclaw/`, then restart:

```bash
launchctl kickstart -k gui/$(id -u)/ai.openclaw.dog-walk-automation
launchctl kickstart -k gui/$(id -u)/ai.openclaw.ring-event-listener
launchctl kickstart -k gui/$(id -u)/ai.openclaw.dog-walk-dashboard
launchctl kickstart -k gui/$(id -u)/ai.openclaw.roomba-dashboard
```

Quick verification:

```bash
tail -20 ~/.openclaw/logs/dog-walk-automation.log
tail -20 ~/.openclaw/logs/ring-event-listener.log
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
