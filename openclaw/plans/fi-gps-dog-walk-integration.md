# Fi GPS Dog Walk Integration — Plan

## Status: PROPOSED (2026-04-01) — Revised after Plan Review

## Goal

Integrate Potato's Fi collar GPS into the ring-listener's dog walk detection and return monitoring. Fi GPS provides actual coordinates independent of cameras, WiFi, or FindMy — making both departure detection and return monitoring simpler and more reliable.

## Current Architecture

### Departure Detection (`check_departure()` at ring-listener.py:1064)
1. Ring doorbell fires FCM push on motion → `on_event()` at line 1297
2. Download video → 5-frame extraction → Claude Haiku vision analysis
3. Count people + dogs via accumulation window (`_departure_sightings`)
4. Crosstown: 1+ people + 1+ dogs → auto-start via `run_roomba_command(location, "start")` at line 1152
5. Cabin: any motion → confirmation prompt (no Ring Protect, assumed 1 dog)

**Problems:**
- Vision often misses dogs (fisheye distortion, occlusion)
- No Ring Protect at Cabin = no video analysis, all motion assumed as person + 1 dog
- False positives from delivery drivers, neighbors, animals
- Complex pipeline with multiple failure points (expired OAuth, ffmpeg, etc.)

### Return Monitoring (`_findmy_poll_loop()` at ring-listener.py:769)
Started by `start_return_monitor()` at line 1008. Runs as async task.

**Three signals checked in loop (lines 801-854):**
1. **Ring motion** (event-driven via `_ring_motion_during_walk` flag) → dock with `return_signal="ring_motion"` (line 810)
2. **WiFi/ARP scan** every 60s → dock with `return_signal="network_wifi"` (line 821)
3. **FindMy polling** every 5min after 20min delay → dock with `return_signal="findmy"` (line 842)
4. Safety timeout: 2 hours → dock with `return_signal="timeout"` (line 859)

**State tracking:** `_update_state_findmy()` at line 294 manages `findmy_polling` state in `state.json` with events: `start`, `poll`, `stop`.

**Problems:**
- WiFi scan requires SSH to MBP at Crosstown (SSH key issues, MBP going to sleep)
- FindMy requires: Peekaboo, Screen Recording TCC, FindMy app open, People tab visible, keyboard navigation, Haiku API — extremely fragile (`_check_person_near_home()` at line 678)
- FindMy can't work at Cabin (no Peekaboo/screen access)

## Fi GPS Capabilities

### What the API provides (`fi-api.py`)
- **GPS coordinates** (lat/lng) — real position from collar's GNSS receiver
- **Activity type**: `OngoingRest` (stationary) vs `OngoingWalk` (moving, with path/distance)
- **Place detection**: Fi knows named places (e.g., "Philly", "Home") with geofence
- **Connection state**: `ConnectedToBase` / `ConnectedToUser` (BLE) / `ConnectedToCellular` / `UnknownConnectivity`
- **Battery level**: percent + voltage (from `device.info` JSON)

### What `cmd_location()` returns (fi-api.py:134)
```json
{
  "name": "Potato",
  "petId": "4WbrzFllED1YxCLqdT5SC4",
  "activity": "Rest",           // "Rest" or "Walk"
  "areaName": null,
  "lastReport": "2026-04-02T02:06:03.357Z",
  "latitude": 42.602,
  "longitude": -72.151,
  "location": "cabin",          // nearest configured location
  "label": "Cabin (95 School House Rd)",
  "distance_m": 26,             // meters from location center
  "at_location": true,          // within geofence radius
  "place": "Philly",            // Fi's named place (if any)
  "address": "95 School House Rd"
}
```

**Not currently exposed** (available in API, needs adding to `fi-api.py`):
- `errorRadius` — GPS accuracy in meters (from `positions[].errorRadius` in GraphQL)
- `nextLocationUpdateExpectedBy` — when next GPS fix is expected (from `device` in GraphQL)
- `lastReportTimestamp` vs current time — staleness indicator

### Update frequency
- **Resting (NORMAL mode)**: ~7 minutes between reports (measured: 420s interval)
- **Walking**: more frequent (collar enters `OngoingWalk` with position trail)
- **Lost Dog mode**: every few seconds (drains battery, not for routine use)

### Auth
- Login: `POST https://api.tryfi.com/auth/login` (email + password, form-encoded)
- GraphQL: `POST https://api.tryfi.com/graphql` (session cookie auth)
- Session cached at `~/.config/fi-collar/session.json` (12hr TTL, auto-re-login on 401)
- Credentials: `TRYFI_EMAIL` + `TRYFI_PASSWORD` in `~/.openclaw/.secrets-cache`

## Fi Signal Validity Rules

### When to trust Fi GPS
- `lastReport` is < 10 minutes old (GPS data is fresh)
- `activity` is not null
- API returned valid lat/lng (not 0, not null)

### When to ignore Fi GPS
- `lastReport` is > 10 minutes old → treat as stale, do not use for decisions
- API returns error (401, 429, network timeout) → skip Fi check, rely on other signals
- `at_location` compares to the **monitored location** specifically (not just nearest) — if monitoring Crosstown return, only check distance to Crosstown, not Cabin
- Battery < 5% → collar may stop reporting soon, log warning but don't change behavior
- Connection state is `UnknownConnectivity` → GPS data may be stale, check `lastReport` age

### Geofence matching for return detection
- Return is confirmed when Potato is within the **monitored location's** geofence
- Must match `location` parameter passed to `_findmy_poll_loop()`, not just nearest location
- One reading within geofence is sufficient for return (same as WiFi — one ARP hit docks)
- Implementation: `haversine(potato_lat, potato_lon, home_lat, home_lon) <= radius`

### Geofence matching for departure detection
- Departure via Fi GPS requires **2 consecutive readings** outside the geofence with `lastReport` timestamps at least 3 minutes apart
- Prevents false departures from indoor GPS drift or brief backyard trips
- Single reading outside geofence is logged but not acted on

## Proposed Changes

### Phase 1: Fi GPS as supplementary signal in return monitor

**Scope**: Add Fi GPS polling to the existing `_findmy_poll_loop()` as a 4th return signal.

**Code changes in `ring-listener.py`:**

1. Add Fi GPS check function:
```python
def _check_fi_gps(location: str) -> dict | None:
    """Check Potato's Fi GPS location. Returns location dict or None on error."""
    try:
        env = os.environ.copy()
        env["PATH"] = f"{OPENCLAW_BIN}:{env.get('PATH', '')}"
        r = subprocess.run(
            [f"{OPENCLAW_BIN}/fi-collar", "location"],
            capture_output=True, timeout=15, env=env
        )
        if r.returncode != 0:
            log(f"FI GPS: command failed: {r.stderr.decode()[:100]}")
            return None
        result = json.loads(r.stdout.decode())
        # Check staleness
        last_report = result.get("lastReport")
        if last_report:
            from datetime import datetime, timezone
            report_time = datetime.fromisoformat(last_report.replace("Z", "+00:00"))
            age_s = (datetime.now(timezone.utc) - report_time).total_seconds()
            if age_s > 600:  # > 10 minutes
                log(f"FI GPS: stale data ({int(age_s)}s old), ignoring")
                return None
            result["age_s"] = int(age_s)
        # Check against monitored location specifically
        loc = LOCATIONS.get(location)  # reuse the HOME_ADDRESSES or add LOCATIONS dict
        if loc:
            dist = _haversine(result["latitude"], result["longitude"], loc["lat"], loc["lon"])
            result["distance_to_monitored"] = round(dist)
            result["at_monitored_location"] = dist <= loc["radius"]
        return result
    except Exception as e:
        log(f"FI GPS: error: {e}")
        return None
```

2. Add to `_findmy_poll_loop()` after the network check (after line 823), before FindMy:
```python
# Fi GPS check (every poll cycle, same as WiFi)
fi_result = await asyncio.to_thread(_check_fi_gps, location)
if fi_result and fi_result.get("at_monitored_location"):
    elapsed_min = int(elapsed / 60)
    log(f"RETURN MONITOR: Fi GPS shows Potato {fi_result['distance_to_monitored']}m from {location} after {elapsed_min}min — docking")
    roomba_result = run_roomba_command(location, "dock")
    _update_state_dog_walk(location, "dock", return_signal="fi_gps", roomba_result=roomba_result)
    _update_state_findmy(location, "stop")
    return
```

3. Add `"fi_gps"` as a valid return signal type (no code change needed — just a new string value)

**Integration approach**: Subprocess call to `fi-collar location` (not importable module). Reasons:
- `fi-api.py` uses `urllib` which conflicts with ring-listener's `aiohttp` async model
- Subprocess isolates Fi API failures from the ring-listener process
- 15s timeout prevents Fi API hangs from blocking the return monitor loop
- Same pattern used for `crosstown-roomba` and `roomba` CLI calls

**Verification:**
1. Start a dog walk (or use `dog-walk-start crosstown`)
2. Check logs: `grep "FI GPS" ~/.openclaw/logs/ring-listener.log`
3. Expected log on return: `RETURN MONITOR: Fi GPS shows Potato 45m from crosstown after 25min — docking`
4. Expected state.json: `"return_signal": "fi_gps"` in dock event
5. If Fi API is down: log shows `FI GPS: command failed` and WiFi/Ring/FindMy still work (no regression)

### Phase 2: Fi GPS as supplementary departure signal

**Scope**: Enhance departure confidence by cross-referencing Ring vision with Fi GPS.

**Code changes in `ring-listener.py`:**

1. In `check_departure()` (line 1064), after accumulation and before triggering:
```python
# Cross-reference with Fi GPS for higher confidence
fi_result = await asyncio.to_thread(_check_fi_gps, location)
fi_departed = False
if fi_result and not fi_result.get("at_monitored_location"):
    fi_departed = True
    log(f"FI GPS: Potato is {fi_result['distance_to_monitored']}m from {location} (outside geofence)")
```

2. For Cabin departures: if Ring sees motion AND Fi GPS confirms departure → auto-trigger without confirmation:
```python
if location == "cabin" and fi_departed:
    log(f"DEPARTURE DETECTED at {location}: Ring motion + Fi GPS confirms departure!")
    send_imessage(f"🧹 Starting Roombas at {location} — Potato left (confirmed by GPS)!")
    roomba_result = run_roomba_command(location, "start")
    # ...
```

3. For any location: if Ring sees person but NO dog, but Fi GPS shows Potato outside geofence → trigger anyway:
```python
if max_people >= 1 and max_dogs == 0 and fi_departed:
    log(f"DEPARTURE DETECTED at {location}: person seen + Fi GPS confirms Potato left (dog occluded)")
    # ... same trigger logic
```

**Verification:**
1. Walk from Cabin during walk hours → should auto-trigger without confirmation prompt
2. Walk where camera misses the dog → Fi GPS should still trigger departure
3. Delivery driver at door → Fi GPS shows Potato still inside, no false trigger

### Phase 3: Replace FindMy with Fi GPS in return monitor

**Scope**: Remove FindMy/Peekaboo code, rely on Fi GPS (from Phase 1) + WiFi + Ring for return detection.

**Prerequisites**: Phase 1 must be running for at least 2 weeks with Fi GPS successfully detecting ≥5 returns across both locations with zero false docks.

**Code removals in `ring-listener.py`:**
- Remove `_check_person_near_home()` (line 678) and helper functions
- Remove FindMy polling section in `_findmy_poll_loop()` (lines 826-846)
- Remove `FINDMY_INTERVAL`, `FINDMY_START` constants
- Remove `findmy-locate.sh` references
- Rename `_findmy_poll_loop()` → `_return_poll_loop()` (reflects that it's no longer FindMy-specific)

**State/event migration:**
- Rename `findmy_polling` → `return_monitoring` in state.json (backward-compatible: keep reading old key if present)
- Rename events: `findmy_start` → `return_start`, `findmy_poll` → `return_poll`, `findmy_stop` → `return_stop`
- Update `_update_state_findmy()` → `_update_state_return_monitor()` (rename function)

**Dashboard updates:**
- `DOG-WALK-DASHBOARD.md` (lines 60-71, 158-177): update field names from `findmy_*` to `return_*`
- `ring-dashboard.py` (lines 261, 267, 342-343, 383): update color mapping, labels, state paths
  - Add `'fi_gps': '#10b981'` (green) to color mapping at line 261
  - Add `'fi_gps': 'Fi GPS'` to signal labels at line 267
  - Update `const fm = state.findmy_polling || {}` → `const rm = state.return_monitoring || state.findmy_polling || {}` at line 342
- `SKILL.md` (ring-doorbell): update return monitoring signal table
- `IMPLEMENTATION.md` (ring-doorbell): rewrite Component 4 (lines 359-440) — remove FindMy/Peekaboo, document Fi GPS

**Verification:**
1. Confirm no Peekaboo calls in ring-listener after removal
2. Walk + return at both Crosstown and Cabin → docked via Fi GPS or WiFi
3. Dashboard renders correctly with new field names
4. Old JSONL history entries with `findmy_*` fields still display correctly

### Phase 4 (Future): Standalone Fi GPS departure detection

**Scope**: Detect walks without Ring doorbell — useful at Cabin or if Ring goes offline.

**Architecture decision**: Integrate into ring-listener as a secondary polling loop (not a separate LaunchAgent). Reasons:
- Shares Roomba command infrastructure, state management, and return monitoring
- Single process to manage, no IPC needed
- Ring listener already has the async event loop

**Implementation:**
- New async task `_fi_departure_poll_loop()` running every 3 minutes
- Track last 3 Fi GPS readings in memory
- Detect departure: 2 consecutive readings outside geofence, ≥3 min apart, both with `lastReport` < 10 min old
- Detect `OngoingWalk` activity type as corroborating signal
- On confirmed departure: start Roombas + start return monitor (same as Ring-triggered departure)
- Only active during walk hours (reuse `_is_walk_hour()`)
- Only active when location is occupied (reuse `_is_location_occupied()`)

**Verification:**
- Walk without triggering Ring motion → Fi GPS detects departure within 7 min
- Backyard visit (within geofence) → no false trigger
- Indoor GPS drift → no false trigger (requires 2 consecutive out-of-geofence)

## Geofence Configuration

| Location | Center Lat | Center Lon | Radius | Source |
|----------|-----------|-----------|--------|--------|
| Crosstown | 42.26233696 | -71.16434947 | 150m | Fi base station GPS |
| Cabin | 42.60211154 | -72.15119056 | 300m | Fi collar GPS at cabin |

Cabin has a larger radius due to the rural setting and larger property.

These coordinates are already defined in `fi-api.py` `LOCATIONS` dict (line 17). The ring-listener will need its own copy or import.

## Required `fi-api.py` Enhancements

Before Phase 1, add to `cmd_location()` output:
- `errorRadius` — from `positions[].errorRadius` in the GraphQL `OngoingWalk` response
- `lastReport` age validation — already present as `lastReport` timestamp, consumer calculates age
- `nextLocationUpdateExpectedBy` — from `device.nextLocationUpdateExpectedBy` in GraphQL

Add these fields to the GraphQL query in `cmd_location()` and include in JSON output.

## Files to Modify

| Phase | File | Changes |
|-------|------|---------|
| 1 | `ring-listener.py` | Add `_check_fi_gps()`, add Fi GPS check to `_findmy_poll_loop()` after WiFi check |
| 1 | `fi-api.py` | Add `errorRadius` and `nextLocationUpdateExpectedBy` to output |
| 2 | `ring-listener.py` | Add Fi GPS cross-reference in `check_departure()`, Cabin auto-trigger |
| 3 | `ring-listener.py` | Remove `_check_person_near_home()`, FindMy polling, rename functions |
| 3 | `ring-dashboard.py` | Add `fi_gps` color/label, update state path from `findmy_polling` |
| 3 | `DOG-WALK-DASHBOARD.md` | Update field names from `findmy_*` to `return_*` |
| 3 | `SKILL.md` (ring-doorbell) | Update return monitoring signal table |
| 3 | `IMPLEMENTATION.md` (ring-doorbell) | Rewrite Component 4 |
| 4 | `ring-listener.py` | Add `_fi_departure_poll_loop()` |

## Risks & Mitigations

| Risk | Impact | Mitigation |
|------|--------|------------|
| Fi API rate limits (unknown) | Polling blocked | Monitor for 429s. Fall back to longer interval (5min). 60s polling = 1440 req/day. |
| Fi API downtime | No Fi signal | Phases 1-2 are additive — WiFi/Ring/FindMy still work. Log and skip. |
| Session expiry (12hr) | 401 errors | Auto-re-login on 401 (already implemented in `graphql()` function). |
| GPS inaccuracy indoors | False return/departure | 150m/300m geofence radius is generous. Departure requires 2 consecutive out-of-geofence readings. |
| Collar battery dies | No GPS data | Log warning when battery < 10%. `_check_fi_gps()` returns None, other signals still work. |
| Stale GPS data | False decisions | 10-minute staleness threshold. Data older than 10 min is ignored. |
| Fi password rotated | Login fails | Credentials in `~/.openclaw/.secrets-cache`. Update there if password changes. |

## Success Criteria

| Phase | Criteria | How to verify |
|-------|----------|---------------|
| 1 | Return detected via Fi GPS within 2 min of arriving home | Check logs: `grep "Fi GPS" ~/.openclaw/logs/ring-listener.log` shows `at_monitored_location: true` with dock event |
| 1 | No regression when Fi API is unavailable | Kill `fi-collar` process, walk and return — WiFi/Ring still dock |
| 2 | Cabin departure auto-triggers without confirmation | Walk from Cabin during walk hours — no "Reply start roombas" text, just "Starting Roombas" |
| 2 | Occluded dog still triggers | Walk where Ring sees person but misses dog — Fi GPS triggers departure |
| 3 | FindMy code removed, no Peekaboo dependency | `grep -c "peekaboo\|findmy" ring-listener.py` returns 0 |
| 3 | Dashboard renders new field names | Load ring-dashboard at port 8550, verify return signal chart shows "Fi GPS" entries |
| 3 | Gate: ≥5 successful Fi GPS returns across both locations, 0 false docks | Review JSONL history before removing FindMy |
