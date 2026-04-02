# Fi GPS Dog Walk Integration — Plan

## Status: PROPOSED (2026-04-01)

## Goal

Integrate Potato's Fi collar GPS into the ring-listener's dog walk detection and return monitoring. Fi GPS provides actual coordinates independent of cameras, WiFi, or FindMy — making both departure detection and return monitoring simpler and more reliable.

## Current Architecture

### Departure Detection (ring-listener)
1. Ring doorbell fires FCM push on motion
2. Download video → 5-frame extraction → Claude Haiku vision analysis
3. Count people + dogs in frames
4. Crosstown: 1+ people + 1+ dogs → auto-start Roombas
5. Cabin: 1+ people + 1+ dogs → confirmation prompt (no Ring Protect, assumed 1 dog)

**Problems:**
- Vision often misses dogs (fisheye distortion, occlusion)
- No Ring Protect at Cabin = no video analysis, all motion assumed as person + 1 dog
- False positives from delivery drivers, neighbors, animals
- Requires camera, video download, API call to Haiku — complex pipeline with multiple failure points (expired OAuth, ffmpeg, etc.)

### Return Monitoring (ring-listener)
1. **WiFi/ARP scan** every 60s (Crosstown via MBP SSH, Cabin via Starlink gRPC)
2. **Ring motion** during walk → dock immediately
3. **FindMy polling** every 5min after 20min (Peekaboo screenshot → Haiku vision)
4. Safety timeout: 2 hours

**Problems:**
- WiFi scan requires SSH to MBP at Crosstown (SSH key issues, MBP going to sleep)
- FindMy requires: Peekaboo installed, Screen Recording TCC grant, FindMy app open, People tab visible, keyboard navigation to select person, Haiku API call — extremely fragile
- FindMy can't work at Cabin (no Peekaboo/screen access)

## Fi GPS Capabilities

### What the API provides
- **GPS coordinates** (lat/lng) with error radius — real position, not base station location
- **Activity type**: `OngoingRest` (stationary) vs `OngoingWalk` (moving, with path/distance)
- **Place detection**: Fi knows named places (e.g., "Philly", "Home") with geofence radius
- **Connection state**: `ConnectedToBase` / `ConnectedToUser` (BLE to phone) / `ConnectedToCellular` / `UnknownConnectivity`
- **Battery level**: percent + voltage
- **Next update ETA**: tells us when the next GPS fix is expected

### Update frequency
- **Resting (NORMAL mode)**: ~7 minutes between reports
- **Walking**: more frequent (collar enters `OngoingWalk` activity type with position trail)
- **Lost Dog mode**: every few seconds (drains battery fast, don't use for routine tracking)

### Auth
- Email + password login → session cookie → GraphQL queries
- Session cached at `~/.config/fi-collar/session.json` (12hr TTL)
- Credentials: `TRYFI_EMAIL` + `TRYFI_PASSWORD` in `~/.openclaw/.secrets-cache`

## Proposed Changes

### Phase 1: Fi GPS as supplementary signal in return monitor

**Scope**: Add Fi GPS polling to the existing return monitor loop as a 4th signal alongside WiFi, Ring motion, and FindMy.

**Implementation**:
- Import `fi-api.py` location check into ring-listener
- In the return monitor loop, poll Fi GPS every 60s (same interval as WiFi)
- If Potato is within the home geofence (150m Crosstown, 300m Cabin) → dock Roombas
- Fi GPS works at both locations — no location-specific infrastructure needed

**Why start here**: Lowest risk. Return monitoring already has multiple signals; adding one more is additive. If Fi GPS fails, WiFi/Ring/FindMy still work.

**Ring-listener changes**:
```python
# In _return_monitor_loop, after network check:
fi_location = await asyncio.to_thread(_check_fi_gps, location)
if fi_location and fi_location.get("at_location"):
    log(f"RETURN MONITOR: Fi GPS shows Potato at {location} — docking")
    roomba_result = run_roomba_command(location, "dock")
    _update_state_dog_walk(location, "dock", return_signal="fi_gps", roomba_result=roomba_result)
    return
```

### Phase 2: Fi GPS as supplementary departure signal

**Scope**: Add Fi GPS departure detection as a complement to Ring vision.

**Implementation**:
- Track Potato's last known position in the ring-listener state
- When a Ring motion event fires AND Potato's GPS shows movement away from the home geofence → stronger confidence in departure
- When Ring vision sees person + dog, AND Fi GPS confirms Potato left the geofence → auto-trigger even at Cabin (currently requires confirmation)
- When Ring vision sees person but NO dog, but Fi GPS shows Potato left geofence → still trigger (dog was occluded in video)

**Why not standalone**: Fi GPS updates every ~7 min at rest, so there's a latency gap. Ring fires immediately on motion. Combining both gives fast detection (Ring) + high confidence (Fi GPS). A standalone Fi poller would miss the first few minutes of a walk.

### Phase 3: Replace FindMy with Fi GPS in return monitor

**Scope**: Remove the fragile FindMy/Peekaboo integration and rely on Fi GPS for "near home" detection.

**Implementation**:
- Remove FindMy polling code from return monitor
- Fi GPS already runs every 60s in the return loop (from Phase 1)
- Remove Peekaboo dependency for dog walk tracking
- FindMy polling code, `findmy-locate.sh`, and Peekaboo keyboard navigation can be removed

**Why**: FindMy polling is the most fragile part of the system — requires 5+ components to align (app open, correct tab, Peekaboo permissions, keyboard nav, Haiku API). Fi GPS replaces all of this with a single API call.

### Phase 4 (Future): Standalone Fi GPS departure detection

**Scope**: Detect walks without Ring doorbell at all — useful at Cabin (no Ring Protect) or if Ring goes offline.

**Implementation**:
- Background poller (LaunchAgent or integrated into ring-listener) checks Fi GPS every 2-3 min
- Detect transition from `OngoingRest` → `OngoingWalk` activity type
- OR detect Potato's position moving outside home geofence
- Auto-start Roombas when departure confirmed (2+ consecutive out-of-geofence readings)
- Latency: ~4-7 min to detect departure (vs Ring's ~1 min) — acceptable for Roomba automation

**Why later**: Requires more testing of Fi GPS update frequency during walks, and need to validate the `OngoingWalk` activity detection reliability.

## Geofence Configuration

| Location | Center Lat | Center Lon | Radius | Source |
|----------|-----------|-----------|--------|--------|
| Crosstown | 42.26233696 | -71.16434947 | 150m | Fi base station GPS |
| Cabin | 42.60211154 | -72.15119056 | 300m | Fi collar GPS at cabin |

Cabin has a larger radius due to the rural setting and larger property.

## Files to Modify

| File | Changes |
|------|---------|
| `ring-listener.py` | Add Fi GPS polling to return monitor (Phase 1), departure enhancement (Phase 2), remove FindMy (Phase 3) |
| `fi-api.py` | Already implemented — `location` command returns lat/lng + nearest location + `at_location` boolean |
| `fi-collar` (bin wrapper) | Already implemented |
| `SKILL.md` (ring-doorbell) | Update return monitoring docs |
| `IMPLEMENTATION.md` (ring-doorbell) | Update architecture diagrams |
| `fi-collar-presence.md` (plan) | Update status to reference this plan |

## Risks

- **Fi API rate limits**: Unknown. Polling every 60s = 1440 requests/day. Monitor for 429s.
- **Fi API downtime**: API unavailable = fall back to WiFi/Ring (Phases 1-2 are additive, not replacement)
- **Session expiry**: 12hr cached session, auto-re-login on 401. May fail if password is rotated.
- **GPS accuracy in buildings**: Fi collar GPS may be inaccurate indoors. Use generous geofence radius.
- **Battery drain**: Frequent GPS fixes drain collar battery faster. Monitor battery trend after integration.

## Success Criteria

- Phase 1: Return from walk detected via Fi GPS within 2 minutes of arriving home
- Phase 2: Departure confirmed at Cabin without user confirmation (Fi GPS + Ring motion together)
- Phase 3: FindMy code removed, no Peekaboo dependency for dog walks
