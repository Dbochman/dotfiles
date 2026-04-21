# Dog Walk Detection — Implementation Spec

Implementation details for the departure and return detection logic in `dog-walk-listener.py`. This document covers signal sources, timing, state machines, and known edge cases.

For operational docs (deploy, snooze, dashboard), see `skills/dog-walk/SKILL.md`.

---

## Architecture Overview

A single Python asyncio process (`ai.openclaw.dog-walk-listener` LaunchAgent) runs three concurrent loops plus an event-driven callback:

| Component | Type | Purpose |
|-----------|------|---------|
| `_fi_departure_poll_loop` | asyncio task | Polls Fi GPS API to detect departures |
| `_return_poll_loop` | asyncio task | Polls Fi GPS + WiFi to detect returns (spawned per walk) |
| `_inbox_poll_loop` | asyncio task | Watches filesystem inbox for manual trigger IPC |
| `on_event` (FCM callback) | Sync callback on FCM thread | Receives Ring doorbell events in real-time |

All loops share state via module-level globals. The FCM callback runs on a **background thread** (not the asyncio event loop) and bridges events to the main loop via `call_soon_threadsafe`. See [Thread Safety](#thread-safety-fcm-callback).

---

## Signal Sources

### Fi GPS Collar (Potato)

- **API**: GraphQL at `https://api.tryfi.com/graphql`, session cookie auth
- **CLI wrapper**: `fi-collar status` (calls `fi-api.py`)
- **Update frequency**:
  - NORMAL mode (resting): ~7 min between reports (measured: 420s)
  - NORMAL mode (walking): faster, collar enters `OngoingWalk` with position trail
  - LOST_DOG mode: ~15-30s (drains battery, used only during active walks)
- **Connection states**: `Base` (on charger/nearby), `User` (BLE to phone), `Cellular`, `Unknown`
- **Key field**: `ongoingActivity.__typename` — `OngoingRest` or `OngoingWalk`
- **Historical walks**: `activityFeed(limit: N) { activities { __typename start end ... on Walk { distance presentUser { firstName } } } }` — returns recent activities (Walk, Play, Rest, Travel) with Fi's own start/end timestamps. A single outing often spans multiple `Walk` entries interleaved with `Play`/`Rest` when the dog pauses — see the enrichment merge logic below.

### Ring Doorbell (FCM Push)

- **Delivery**: Firebase Cloud Messaging push → `ring_doorbell` Python library → `on_event` callback
- **Event types**: `motion` (with `state`: `human` / `other`), `ding` (doorbell press)
- **Latency**: Near-instant (~1-3s from physical motion to callback)
- **Coverage**: Front door only (no back door coverage at either location)

### WiFi Network Presence

- **Crosstown**: ARP scan via SSH to MacBook Pro (`presence-detect.sh crosstown`)
- **Cabin**: ARP scan locally (`presence-detect.sh cabin`)
- **Detects**: Phone reconnecting to home WiFi network
- **Limitation**: Phones linger on WiFi at the front door for several minutes after departure (see [WiFi Departure Unreliability](#wifi-departure-unreliability))

---

## Departure Detection

### State Machine

```
                ┌──────────────────────────────────────────────────────┐
                │                    IDLE                               │
                │  Polling Fi API every 3 min (FI_POLL_INTERVAL=180s)   │
                └────────┬────────────────────────┬────────────────────┘
                         │                        │
              Ring motion fires             Fi GPS outside geofence
              (stores timestamp)            (first reading)
                         │                        │
                         ▼                        ▼
                ┌─────────────────┐     ┌──────────────────────────┐
                │ RING_MOTION_SET │     │ CANDIDATE                │
                │                 │     │ (need confirmation)      │
                │ Fast poll: 30s  │     │ Poll: 30s if disconnected│
                │ if has_recent_  │     │       180s otherwise     │
                │ ring = true     │     │                          │
                └────────┬────────┘     └────────┬─────────────────┘
                         │                        │
                    Fi base disconnect       Wait for threshold:
                    detected on next poll    - 60s if base disconnected
                         │                   - 180s if still on base
                         │                        │
                         ▼                        ▼
                ┌─────────────────┐     ┌──────────────────────────┐
                │ COMBO TRIGGER   │     │ GPS CONFIRMED            │
                │ (immediate)     │     │ (2nd reading outside)    │
                └────────┬────────┘     └────────┬─────────────────┘
                         │                        │
                         └──────────┬─────────────┘
                                    ▼
                         ┌──────────────────────┐
                         │ DEPARTURE CONFIRMED  │
                         │ → LOST_DOG mode      │
                         │ → Start Roombas      │
                         │ → iMessage notify    │
                         │ → Start return mon.  │
                         └──────────────────────┘
```

### Path 1: Ring + Fi Combo Trigger (Primary)

The fastest departure path. Two independent signals confirm departure without needing GPS geofence confirmation.

**Sequence:**
1. Person walks out front door → Ring doorbell detects human motion
2. `on_event` callback fires on FCM thread → `_handle_motion_sync` sets `_ring_departure_motion[location] = time.monotonic()`
3. On next Fi poll iteration, `has_recent_ring` evaluates True → poll interval drops to 30s (`FI_FAST_POLL_INTERVAL`)
4. Fi API response shows `connection` transitioned from `"Base"` to `"User"` or `"Cellular"` → `base_just_disconnected = True`
5. Combo check: `home_anchor in _ring_departure_motion` AND `ring_age <= 300s` → **departure confirmed immediately**
6. No GPS geofence confirmation needed

**Timing budget:**
| Step | Duration | Cumulative |
|------|----------|------------|
| Ring motion → FCM callback | ~1-3s | ~2s |
| Waiting for next Fi poll | 0-30s (fast poll) or 0-180s (normal) | ~15-90s |
| Fi API HTTP call | ~1-2s | ~16-92s |
| Base disconnect detection | 0s (same poll) | ~16-92s |
| **Total** | | **~15-90s typical** |

**Why the poll interval matters**: The `has_recent_ring` check at the top of the loop uses the Ring motion timestamp to decide whether to fast-poll (30s) or normal-poll (180s). If Ring motion fires WHILE the loop is sleeping (which it usually does), the CURRENT sleep completes at normal pace, but the NEXT iteration sleeps only 30s. So the first poll after Ring motion could be anywhere from 0-180s, but subsequent polls are 30s.

**Key constants:**
```python
_RING_DEPARTURE_WINDOW = 300   # Ring motion must be within 5 min of Fi disconnect
FI_FAST_POLL_INTERVAL = 30     # Fast polling when Ring motion detected
```

### Path 2: GPS-Only Fallback

Used when the combo trigger doesn't fire (back door exit, Ring offline, etc.).

**Sequence:**
1. Fi GPS poll shows Potato outside the home geofence (150m Crosstown, 300m Cabin)
2. Record as first candidate reading with timestamp
3. Continue polling (30s if base disconnected, 180s otherwise)
4. Second reading also outside geofence AND time since first exceeds threshold:
   - `CONFIRM_BASE_DISCONNECT = 60s` if collar disconnected from base
   - `CONFIRM_NORMAL = 180s` if still showing base connection
5. **Departure confirmed**

**Timing budget:**
| Scenario | First reading delay | Confirmation | Total |
|----------|-------------------|--------------|-------|
| Base disconnected, fast poll | 0-30s | 60s | ~1.5-2 min |
| Base disconnected, normal poll | 0-180s | 60s | ~1-4 min |
| No base disconnect | 0-180s | 180s | ~3-6 min |

**Key constants:**
```python
FI_POLL_INTERVAL = 180         # Normal polling: 3 minutes
FI_FAST_POLL_INTERVAL = 30     # Fast polling after base disconnect or Ring motion
CONFIRM_NORMAL = 180           # GPS-only confirmation: 3 min apart
CONFIRM_BASE_DISCONNECT = 60   # GPS + base disconnect: 1 min apart
```

### Candidate Reset Conditions

A departure candidate is reset (detection starts over) when:

| Condition | Reason |
|-----------|--------|
| `inside_geofence` | Dog came back inside geofence radius |
| `outside_walk_hours` | Walk hours ended (before 7 AM or after 9 PM) |
| `return_monitor_active` | A walk is already in progress |
| `location_changed` | Dog appeared near a different home |
| `no_occupied_location` | No home anchor established |
| `combo_trigger` | Combo fired, so candidate tracking resets |

### Home Anchor

The listener tracks `home_location` — the last Fi geofence Potato was confirmed inside. This is the reference point for departure detection (not the nearest location at any given moment).

- **Bootstrapped** from `state.json` on startup
- **Updated** when Fi GPS confirms Potato inside a geofence (`fi_at_location = True`)
- **Persists** across restarts via state file

### Pre-checks (Departure Skips)

Before confirming departure, the system validates:

1. **Walk hours**: 7 AM - 9 PM local time (three windows: 7-12, 12-17, 17-21)
2. **No active walk**: `_return_monitor_active` must be False
3. **GPS freshness**: `lastReport` must be < 10 minutes old
4. **Base-station echo filter**: If pet coordinates match a home location within 5m AND connection is not `"Base"`, the reading is discarded. This handles the Fi API lag during Rest→Walk transitions where it returns base station coords as pet position.
5. **Geofence guard on combo triggers** (2026-04-21): Both combo triggers (Ring+disconnect and activity+disconnect) require Potato's Fi GPS to be outside the home geofence radius before firing. Fi's activity feed and base-station connection can lag after a real return, so without this guard a Rest→Walk flip or User→Base→User blip while the dog is home reads as a new departure.
6. **Post-return tracker reseed** (2026-04-21): When return monitor exits, `_reset_departure_trackers` is set; the next Fi poll reseeds `last_connection` / `last_activity` from current Fi state and skips transition detection for that iteration. Prevents a phantom `Rest→Walk + base-disconnect` combo when the just-ended walk's values haven't cleared yet.
7. **Snooze**: Roomba start is skipped if snoozed (but walk tracking still happens)
8. **Cooldown**: Roomba start has a 2-hour cooldown per location

---

## Return Detection

Starts immediately after departure is confirmed. Uses three independent signals — **any one** triggers return.

### Signal Priority & Behavior

| Signal | Check method | Interval | Suppression |
|--------|-------------|----------|-------------|
| Ring motion | Event-driven flag (`_ring_motion_during_walk`) | Instant | None |
| WiFi presence | Network scan (`_check_network_presence`) | Every 30s | **First 10 min suppressed** |
| Fi GPS | Poll (`_check_fi_gps` with monitored location) | Every 30s | None |

**WiFi suppression rationale**: Phones stay connected to home WiFi for several minutes after walking out the front door. The 10-minute `MIN_WALK_FOR_WIFI` delay avoids false returns from lingering WiFi connections.

### Return Flow

```
DEPARTURE CONFIRMED
        │
        ├─ Wait 2 min → detect walkers (network scan for who's absent)
        │
        ▼
┌───────────────────────────────────────────────┐
│               RETURN MONITOR LOOP              │
│  Every 30s, check:                             │
│  1. Ring motion flag (event-driven)            │
│  2. WiFi presence (after 10min)                │
│  3. Fi GPS geofence (always)                   │
│                                                │
│  Also: route point appended each poll          │
│  Also: car speed check (>30mph for 6min →      │
│         switch collar to NORMAL)               │
└───────────┬───────────────────────────────────┘
            │ Any signal triggers return
            ▼
┌───────────────────────────────────────────────┐
│           RETURN FINALIZATION                  │
│  1. Fetch Fi OngoingWalk path (dense polyline) │
│  2. Merge into route file                      │
│  3. Capture final GPS point                    │
│  4. Dock Roombas (sends stop first, then dock) │
│  5. Start dock verification thread (3min)      │
│  6. Send iMessage with walk summary            │
│  7. Update state + history                     │
│  8. Reset collar to NORMAL                     │
│                                                │
│  Each step is try/except wrapped so failures   │
│  cannot cause the loop to repeat finalization. │
│  Once a return signal is set, the loop ALWAYS  │
│  exits.                                        │
└───────────────────────────────────────────────┘
```

### Car Speed Detection

During return monitoring, consecutive GPS readings are compared to detect car travel. Speed is calculated using real Fi report timestamps (`lastReport` / `connectionDate` fields) — not staleness deltas — to avoid inflation from variable poll timing.

- Threshold: >30 mph (`CAR_SPEED_MPS = 13.4 m/s`) sustained for 6+ minutes (`CAR_DURATION_S = 360`)
- Speed formula: `haversine(prev, cur) / (cur_report_ts - prev_report_ts).total_seconds()`, clamped to 0 if the time gap exceeds 15 minutes or is non-positive
- Actions:
  1. Switch collar from LOST_DOG to NORMAL to save battery
  2. Set `is_car_trip = True` on the route file during finalization
- Resets: If speed drops below threshold between any two readings
- Purpose: Prevents car trips (errands, inter-home transit) from appearing as walks in the dashboard
- The return monitor continues running — when the car returns home, it docks Roombas normally
- Dashboard filters `is_car_trip: true` routes from the walk table and today's totals

### Dock Verification

After sending the dock command, a background daemon thread verifies:
1. Wait 3 minutes
2. Check Roomba status (is it actually charging/on dock?)
3. If not docked: retry dock command (up to 2 retries, 3 min between each)
4. If still not docked after retries: send iMessage warning
5. State updated with `dock_verified: true/false` and `dock_retry_count`

### Safety Timeout

If no return signal is detected within 2 hours (`MAX_DURATION = 7200`), the monitor auto-docks Roombas and sends a timeout notification.

---

## GPS Tracking (Route Recording)

### LOST_DOG Mode

On departure, the collar switches to LOST_DOG mode for ~15-30s GPS updates (vs ~7 min in NORMAL). This produces dense route data.

- **Set on**: departure confirmed
- **Reset on**: return monitor exits (in `finally` block, guaranteed)
- **Startup safety**: if listener starts and collar is in LOST_DOG, it resets to NORMAL

### Route Files

Each walk produces a route file at:
```
~/.openclaw/dog-walk/routes/<location>/<YYYY-MM-DD>/<walk_id>.json
```

**Structure:**
```json
{
  "walk_id": "20260405T202347Z-crosstown-9030538a",
  "origin_location": "crosstown",
  "started_at": "2026-04-05T20:23:47Z",
  "ended_at": "2026-04-05T20:47:08Z",
  "return_signal": "fi_gps",
  "distance_m": 2184,
  "point_count": 133,
  "end_location": "crosstown",
  "is_interhome_transit": false,
  "points": [
    {"ts": "2026-04-05T20:15:11.248Z", "lat": 42.262500, "lon": -71.164310},
    ...
  ]
}
```

**Point sources:**
1. **Departure GPS seed**: first point from the Fi API response at departure
2. **30s polling**: each return monitor poll appends the current Fi GPS reading
3. **Fi OngoingWalk path merge**: on return, the full dense polyline from Fi's API is fetched and merged (deduplicated by lat/lon within 5m)

**Distance calculation:** Uses the route file's `distance_m`, which prefers Fi's `walkDistance_m` (from `OngoingWalk.distance`) when available, falling back to haversine sum of consecutive points.

### Fi Walk Summary Enrichment (Post-Walk)

After return finalization, the listener queries Fi's `activityFeed` (limit 15) and **merges all Walk segments that overlap our outing window** into a single enrichment. Fi frequently splits a single outing into multiple `Walk` activities separated by `Play`/`Rest` periods (dog sitting, sniffing, running around the yard between trail segments) — the merge stitches them back together.

**Merge rule:** select every Walk segment whose `start` falls within `[our_started_at - 5min, our_ended_at + 5min]`, then:

| Merged field | Value |
|--------------|-------|
| `fi_walk_start` | earliest Walk `start` |
| `fi_walk_end` | latest Walk `end` |
| `fi_distance_m` | sum of segment distances |
| `fi_walker` | unique non-null `presentUser.firstName` values, joined |
| `fi_walk_count` | number of Walk segments merged (transparency) |
| `detection_latency_s` | `our started_at - fi_walk_start` |

**Example (Apr 18, 2026 cabin outing):**
- Our detection: `13:27:23Z → 14:22:21Z` (55 min, 810m GPS)
- Fi activityFeed:
  - Walk 1: `13:25:32 → 13:33:15` (531m, Dylan)
  - Play: `13:33:15 → 14:17:37` (44 min, no distance — yard time)
  - Walk 2: `14:17:37 → 14:22:31` (400m)
- Merged: `fi_walk_start=13:25:32`, `fi_walk_end=14:22:31`, `fi_distance_m=931`, `fi_walker=Dylan`, `fi_walk_count=2`

**Retry strategy:** A background thread re-runs enrichment at 5 / 10 / 20 min after return. Retries are always scheduled (not gated on first-attempt failure) because Fi often emits additional Walk segments after the dog returns — the last stretch of walking back to the door can land minutes later. Retries are idempotent: if the merge output matches what's already stored, no write occurs.

**Safety guard:** Only Walk segments whose `start` falls inside our outing window (±5min) are considered. Walks from other outings in the same feed window (e.g. a short walk Fi finalizes from this morning when we're checking last night's) are ignored.

**Note:** Fi's `activityFeed` returns only summary data (start, end, distance, walker) — not GPS track points. Route visualization still relies on our own GPS polling. `Play` activities don't carry distance, so their movement is not counted in `fi_distance_m` — but because a Play between two Walks is captured in `[fi_walk_start, fi_walk_end]`, the duration is correct.

### Walker Detection

On departure, the listener waits 2 minutes then runs a fresh ARP/WiFi network scan to identify who left. Three sources are cross-referenced:

1. **Sticky presence state** (`~/.openclaw/presence/state.json`): who was at this location → candidates
2. **Fresh network scan** (`presence-detect.sh <location>`): who is currently absent from the network
3. **Last periodic scan** (`~/.openclaw/presence/<location>-scan.json`): who was recently present on the network (within 1 hour)

A person is only flagged as a walker if **all three** conditions are met:
- Presence state says they were at this location (candidate)
- Fresh scan shows them absent from the network
- Last periodic scan (< 1 hour old) showed them present

This prevents two classes of false positives:
- Someone who left for work hours ago (absent from ARP, but not recently present → excluded)
- Someone at the other location whose sticky presence is stale (not a candidate → excluded)

If nobody qualifies after all three checks, the system falls back to all candidates at the location.

**Note**: Fi's `fi_walker` BLE field (who was near the collar) is stored in the route file but not used for dashboard display — it only detects one phone, so multi-walker walks would undercount.

---

## Dashboard Display

### Status Cards

The top-level status cards show:

| Card | Source | Notes |
|------|--------|-------|
| **Today's Distance** | Sum of `fi_distance_m` (or `distance_m`) for walks 7 AM – 11 PM local | Walk count and active indicator |
| **Today's Duration** | Sum of Fi-derived or fallback durations for same window | Active walk elapsed time as sub-label |
| **Departure Candidate** | Live `state.json` | Shows when GPS-only confirmation is pending |
| **Return Monitor** | Live `state.json` | Shows poll count and Potato distance |

### Fi Data Preference

The dashboard prefers Fi authoritative data when available:

| Field | Primary source | Fallback |
|-------|---------------|----------|
| Distance | `fi_distance_m` (sum across merged Walk segments) | `distance_m` (GPS point sum) |
| Duration | `fi_walk_end - fi_walk_start` (spans all merged segments, including any Play/Rest between them) | `walk_duration_minutes` from JSONL or `ended_at - started_at` |
| Walkers | JSONL walkers (ARP detection) | — (`fi_walker` stored but not used for display; Fi BLE only detects one phone) |
| Start time | `started_at` (our detection time) | — |

### Junk Walk Filtering

GPS jitter can cause false-positive departures — the collar briefly reports being outside the geofence by a few meters, triggering a walk that docks within minutes with negligible distance. The dashboard filters these from the walk table:

- **Filter**: walks with <50m distance AND <5 min duration are hidden
- **Active walks** always display (they haven't finished yet)
- **The listener still triggers** for junk walks (better to dock roombas on a false positive than miss a real walk)

---

## Thread Safety: FCM Callback

The Ring doorbell library's FCM push receiver runs on a **background thread**, not the asyncio event loop. The `on_event` callback is invoked on this thread.

### The Problem (Fixed 2026-04-07)

Previously, `on_event` directly mutated shared dicts and bools from the FCM thread, relying on CPython's GIL for atomicity. While technically safe for simple assignments, this was fragile — any future refactoring that added compound operations would silently introduce race conditions. An earlier iteration also used `loop.create_task()` to schedule async handlers from the FCM thread, which is **not thread-safe** and could silently drop coroutines.

### The Fix: `call_soon_threadsafe` Bridge

`on_event` is now a **thin bridge** that copies event fields into plain values and schedules all processing on the asyncio loop thread:

```python
def on_event(event: RingEvent) -> None:
    if event.is_update:
        return
    _main_loop.call_soon_threadsafe(
        _process_ring_event_on_loop,
        event.id, event.kind, event.device_name, event.doorbot_id, event.state or "",
    )
```

`_process_ring_event_on_loop` runs on the main asyncio thread and owns all shared state writes (`_ring_departure_motion`, `_ring_motion_during_walk`, `_recent_events`). No shared mutable state is touched from the FCM thread.

The `_main_loop` reference is set once in `main()` via `asyncio.get_running_loop()` before the Ring listener starts. Events that arrive before this (startup race) are logged and dropped.

### Blocking I/O Off the Event Loop

All subprocess calls (`fi-collar`, `roomba`, `imsg`), network I/O (`send_imessage`), and file writes (state/route JSON persistence) are wrapped in `asyncio.to_thread()` so they don't block the event loop:

```python
await _send_imessage_async(text)        # → asyncio.to_thread(send_imessage, text)
await _run_roomba_command_async(loc, a)  # → asyncio.to_thread(run_roomba_command, loc, a)
await _update_state_dog_walk_async(...)  # → asyncio.to_thread(_update_state_dog_walk, ...)
```

File persistence functions use a `threading.Lock` (`_state_lock`) to serialize concurrent writes from worker threads.

### Shared State Model (Post-Hardening)

All shared state is now accessed exclusively from the asyncio loop thread:

| Variable | Written by | Read by | Notes |
|----------|-----------|---------|-------|
| `_ring_departure_motion` | loop thread (via bridge) | loop thread (Fi poll) | Single-thread access |
| `_ring_motion_during_walk` | loop thread (via bridge) | loop thread (return poll) | Single-thread access |
| `_return_monitor_active` | loop thread | loop thread (bridge reads it) | Single-thread access |
| `_recent_events` | loop thread (via bridge) | loop thread (via bridge) | Single-thread access |

---

## Geofences

Geofence coordinates are loaded from environment variables at startup (not hardcoded).

| Location | Env vars | Radius | Notes |
|----------|----------|--------|-------|
| Crosstown | `CROSSTOWN_LAT`, `CROSSTOWN_LON` | 150m | Uses custom coords, NOT Fi base station coords (those report wrong location) |
| Cabin | `CABIN_LAT`, `CABIN_LON` | 300m | Larger radius for rural GPS accuracy |

---

## Timing Constants Reference

| Constant | Value | Context |
|----------|-------|---------|
| `FI_POLL_INTERVAL` | 180s (3 min) | Normal departure polling |
| `FI_FAST_POLL_INTERVAL` | 30s | Polling after base disconnect or Ring motion |
| `CONFIRM_NORMAL` | 180s (3 min) | GPS-only: time between 2 outside readings |
| `CONFIRM_BASE_DISCONNECT` | 60s (1 min) | GPS + disconnect: time between 2 outside readings |
| `_RING_DEPARTURE_WINDOW` | 300s (5 min) | Max age of Ring motion for combo trigger |
| `POLL_INTERVAL` (return) | 30s | Return monitor check interval |
| `MIN_WALK_FOR_WIFI` | 600s (10 min) | WiFi suppression window after departure |
| `MAX_DURATION` (return) | 7200s (2 hr) | Safety timeout for return monitor |
| `_ROOMBA_COOLDOWN` | 7200s (2 hr) | Prevent re-triggering Roomba start |
| `_DEDUP_WINDOW` | 300s (5 min) | Ring event deduplication window |
| `_INBOX_MAX_AGE` | 7200s (2 hr) | Max age for manual trigger requests |
| `CAR_SPEED_MPS` | 13.4 m/s (~30 mph) | Car detection threshold |
| `CAR_DURATION_S` | 360s (6 min) | Sustained car speed before collar reset |

---

## Walk Hours

```python
_WALK_HOURS = [(7, 12), (12, 17), (17, 21)]  # 7 AM - 9 PM local time
```

Outside these hours, the departure poll loop skips Fi checks entirely and resets any active candidate. The return monitor has no time restriction — if a walk departs at 8:55 PM, it will track the return regardless of when that occurs.

---

## Known Edge Cases

### Back Door Departure
The Ring doorbell only covers the front door. Back door exits bypass the combo trigger entirely and fall through to GPS-only detection (~5-7 min latency).

### WiFi Departure Unreliability
Phones stay connected to home WiFi well past the front door. WiFi is intentionally NOT used for departure detection — only for return. Even for return, WiFi signals are suppressed for the first 10 minutes.

### Base-Station Echo
When the Fi API transitions from `OngoingRest` to `OngoingWalk`, there's a brief window where it returns base station coordinates as the pet's position. The echo filter discards readings within 5m of any home location when the connection type is not `"Base"`.

### Ring Motion Cleared on At-Home Poll
When the Fi poll confirms the dog is inside the geofence (`fi_at_location = True`), it clears `_ring_departure_motion` for that location (line 1489). This is intentional — it prevents stale Ring motion from triggering a false combo later. But it means Ring motion that fires DURING an at-home Fi poll can be immediately cleared. The fix for the thread safety issue (synchronous writes) reduces but doesn't eliminate this window.

### Inter-Home Transit
If a walk ends at a different location than it started (Crosstown → Cabin or vice versa), the route file is marked with `is_interhome_transit: true`. The dashboard API filters these out of walk history views.

### GPS Jitter False Positives
The Fi collar can briefly report coordinates just outside the geofence radius due to GPS accuracy limitations (~5-15m error). This triggers the GPS-only departure path when two jittery readings happen within the confirmation window. The resulting "walk" has near-zero distance (e.g., 4m) and docks within minutes. The dashboard filters these out (<50m AND <5 min), but the listener still processes them to avoid missing real walks.

### Car Trips (Round-Trip)
A departure to run errands (Crosstown → store → Crosstown) triggers departure detection like any walk. The car speed check switches the collar to NORMAL after 6 minutes at >30 mph, and marks the route with `is_car_trip: true` on finalization. The `is_interhome_transit` flag only catches walks that end at a *different* known location, so round-trip car errands need this separate flag. The dashboard filters both `is_car_trip` and `is_interhome_transit` from the walk list.

### Listener Restarts
The listener process may restart (KeepAlive LaunchAgent). On restart:
- Home anchor is bootstrapped from `state.json`
- Collar mode is checked and reset to NORMAL if stuck in LOST_DOG
- All in-memory state (`_ring_departure_motion`, candidates, etc.) is lost
- FCM credentials are reloaded for Ring event listener

---

## File Layout

```
~/.openclaw/dog-walk/
├── state.json                  # Current state (walk active, roombas, candidate, etc.)
├── history/
│   └── YYYY-MM-DD.jsonl        # All state changes as append-only log
├── routes/
│   └── <location>/
│       └── YYYY-MM-DD/
│           └── <walk_id>.json  # GPS route + metadata per walk
├── inbox/                      # IPC for manual trigger (dog-walk-start)
├── snooze.json                 # Roomba snooze state per location
└── fcm-credentials.json        # Ring FCM push credentials

~/.openclaw/logs/
└── dog-walk-listener.log       # Operational log (local timestamps)
```

---

## Manual Trigger

External processes can request return monitoring via filesystem IPC:

```bash
dog-walk-start <location>
```

This writes a JSON file to `~/.openclaw/dog-walk/inbox/` with `location` and `requested_at`. The inbox poll loop (5s interval) picks it up, starts Roombas, and begins return monitoring. Stale requests (>2 hours old) are ignored.
