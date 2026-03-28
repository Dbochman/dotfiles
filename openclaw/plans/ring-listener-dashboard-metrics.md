# Ring Listener Dashboard Metrics — Implementation Plan

## Status: PLANNED (not yet implemented)

## Overview

Enrich the ring-listener's state and history JSONL data with structured metadata so that a dashboard can visualize dog walks, Roomba operations, and detection accuracy without parsing logs.

### Goals

1. Every dashboard-relevant data point is in the JSONL history (no log parsing needed)
2. Walk lifecycle is fully reconstructable from JSONL events alone
3. Suppressed departures and detection decisions are visible for tuning

### Non-Goals

- Building the dashboard UI (separate plan, see `dashboard-home-state.md`)
- Changing the log format (logs remain human-readable, JSONL is for machines)
- Altering the existing dashboard API endpoints

## Current State

### What's Captured Today

The JSONL history (`~/.openclaw/ring-listener/history/YYYY-MM-DD.jsonl`) appends a full state snapshot on every `_write_state()` call. Each line contains:

```json
{
  "timestamp": "ISO 8601 UTC",
  "dog_walk": { "active", "location", "departed_at", "returned_at", "people", "dogs" },
  "roombas": { "<location>": { "status", "started_at", "docked_at", "trigger" } },
  "last_vision": { "event_id", "description", "people", "dogs", "people_list", "dogs_list", "analyzed_at" },
  "findmy_polling": { "active", "location", "started_at", "polls", "last_poll_at", "last_result" }
}
```

IMPLEMENTATION.md documents that every JSONL line mirrors `state.json`. This plan preserves that contract — all events (including skips) write full state snapshots.

### What's Missing

| Gap | Impact | Fix |
|-----|--------|-----|
| No `return_signal` on dock events | Can't show *how* return was detected | Add field to `_update_state_dog_walk("dock")` |
| No `walk_duration_minutes` | Must compute from timestamps client-side | Compute at dock time |
| No `walkers` list | Can't show who was on the walk | Populate `dog_walk.walkers` ~2 min after departure via `walkers_detected` event |
| Roomba command success/failure not stored | Can't show reliability metrics | Return result from `run_roomba_command()`, store in state |
| Suppressed departures invisible | Can't measure false-positive prevention | Emit full-state skip events to JSONL |
| Per-person WiFi status not stored | Can't show individual presence timeline | Store scan details in skip events and return-monitor polls |
| No `event_type` field in JSONL | Full-state snapshots are ambiguous — can't tell departure from dock | Add explicit event type to every `_write_state()` call |

---

## Architecture Decisions

### JSONL Format

**Decision:** All JSONL lines remain full state snapshots. Skip events write through `_write_state()` like everything else. This preserves the contract that every JSONL line mirrors `state.json`.

**Decision:** `event_type` is set on every `_write_state()` call. It represents "what triggered this state write." Transient per-event fields (`skip_reason`, `skip_location`, `skip_details`) are stripped by `_write_state()` **only when the current event_type is NOT `departure_skip`**. This ensures skip events include their metadata in the written snapshot, while the very next non-skip write cleans them up — see Change 1 implementation.

### Concurrency

**Existing risk:** `_update_state_dog_walk()`, `_update_state_vision()`, and `_update_state_findmy()` each do unsynchronized read-modify-write via `_read_state()` → mutate → `_write_state()`.

**This plan's approach:** Add a module-level `threading.Lock` (`_state_lock`) and acquire it in each `_update_state_*` function to wrap the **entire read-modify-write transaction** — not just the individual `_read_state()`/`_write_state()` calls. This prevents concurrent updaters from reading stale state and overwriting each other's changes.

```python
_state_lock = threading.Lock()

def _update_state_dog_walk(location, event, ...):
    with _state_lock:
        state = _read_state()
        # ... mutate state ...
        _write_state(state, event_type=event)

def _update_state_vision(vision_data, event_id=0):
    with _state_lock:
        state = _read_state()
        # ... mutate state ...
        _write_state(state, event_type="vision")

def _update_state_findmy(location, event, result=None, network_detail=None):
    with _state_lock:
        state = _read_state()
        # ... mutate state ...
        _write_state(state, event_type=f"findmy_{event}")
```

`_read_state()` and `_write_state()` themselves do **not** acquire the lock (they are called inside it). `threading.Lock` (not `asyncio.Lock`) is correct because the state helpers are synchronous functions; async callers use `asyncio.to_thread()` for blocking work but the state write itself is fast (~1ms file I/O).

All new state writes in this plan go through `_update_state_dog_walk()` or `_update_state_findmy()` — **no new raw `_read_state()`/`_write_state()` paths**. Changes 3 and 5 from the previous revision have been reworked to use the existing `_update_state_*` functions.

### Blocking Subprocess

**Existing issue:** `check_departure()` calls `_check_network_presence()` synchronously via `subprocess.run()`, blocking the async event loop.

**This plan's fix:** Wrap the WiFi check in `asyncio.to_thread()`. Since `check_departure()` is called from async code (`_send_event_recording()`), make `check_departure()` async and `await` the WiFi check. The return-monitor already uses `asyncio.to_thread()` for the same function at line 719 — this follows the same pattern.

**Call chain change:**

| Current | New |
|---------|-----|
| `check_departure(vision_data, doorbot_id)` (sync) | `async def check_departure(vision_data, doorbot_id)` |
| Called at line 1342 in `_send_event_recording()`: `check_departure(vision_data, doorbot_id)` | `await check_departure(vision_data, doorbot_id)` |
| Called at line 1308 in `_send_event_recording()`: `check_departure({"people": ["unknown"], "dogs": ["unknown"]}, doorbot_id)` | `await check_departure(...)` |
| Internal WiFi check: `_check_network_presence(location)` (sync subprocess.run) | `await asyncio.to_thread(_check_network_presence_detailed, location)` |

Both call sites are inside `async def _send_event_recording()` (lines 1276-1355), so adding `await` is safe. Verified: no sync callers of `check_departure()` exist.

---

## Implementation

### Change 1: Add `event_type` to `_write_state()` + state lock + transient field cleanup

**Why:** Every JSONL line needs a self-describing event type. Transient fields must not leak across writes. State access must be serialized.

**Files:** `ring-listener.py`

**New module-level lock** (near line 120, with other globals):

```python
import threading
_state_lock = threading.Lock()
```

**Function:** `_write_state(state, event_type="state_update")` (line 167)

```python
# Transient keys that are only valid on departure_skip events
_SKIP_KEYS = {"skip_reason", "skip_location", "skip_details"}

def _write_state(state: dict, event_type: str = "state_update") -> None:
    """Write state atomically and append to daily history JSONL.

    Called inside _state_lock by _update_state_* functions — do NOT acquire lock here.
    """
    # Strip stale skip metadata from previous writes — but preserve it
    # on departure_skip events (those fields are the payload)
    if event_type != "departure_skip":
        for key in _SKIP_KEYS:
            state.pop(key, None)

    state["event_type"] = event_type
    state["timestamp"] = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")

    # ... existing atomic write + history append (unchanged) ...
```

`_read_state()` and `_write_state()` do **not** acquire the lock — they are always called inside `_state_lock` held by an `_update_state_*` function.

**Event type inventory (complete list):**

| event_type | Emitted by | Meaning |
|------------|-----------|---------|
| `"departure"` | `_update_state_dog_walk(event="departure")` | Walk started, Roombas running |
| `"dock"` | `_update_state_dog_walk(event="dock")` | Walk ended, Roombas docked |
| `"dock_timeout"` | `_update_state_dog_walk(event="dock_timeout")` | 2-hour safety fallback |
| `"walkers_detected"` | `_update_state_dog_walk(event="walkers_detected")` | Who left detected ~2 min after departure |
| `"vision"` | `_update_state_vision()` | Vision analysis completed |
| `"findmy_start"` | `_update_state_findmy(event="start")` | Return monitoring started |
| `"findmy_poll"` | `_update_state_findmy(event="poll")` | FindMy/WiFi check result |
| `"findmy_stop"` | `_update_state_findmy(event="stop")` | Return monitoring stopped |
| `"departure_skip"` | `_update_state_dog_walk(event="departure_skip")` | Departure suppressed |
| `"state_update"` | Default when event_type not specified | Generic state write |

Each `_update_state_*` function passes the event_type to `_write_state()`:

```python
# In _update_state_dog_walk():
_write_state(state, event_type=event)

# In _update_state_vision():
_write_state(state, event_type="vision")

# In _update_state_findmy():
_write_state(state, event_type=f"findmy_{event}")
```

**Migration:** Old JSONL lines without `event_type` are valid — dashboard treats missing field as `"unknown"`.

---

### Change 2: Add `return_signal` and `walk_duration_minutes` to dock events

**Why:** The most important dashboard metric — how long was the walk, and which signal detected the return.

**Files:** `ring-listener.py`

**Function:** `_update_state_dog_walk()` (line 189)

New signature:

```python
def _update_state_dog_walk(
    location: str,
    event: str,
    people: int = 0,
    dogs: int = 0,
    return_signal: str | None = None,
    roomba_result: dict | None = None,
    walkers: list[str] | None = None,
    skip_reason: str | None = None,
    skip_details: dict | None = None,
) -> None:
```

**When `event == "departure"`** (lines 198-212), initialize new fields:

```python
walk = {
    "active": True,
    "location": location,
    "departed_at": now,
    "returned_at": None,
    "people": people,
    "dogs": dogs,
    "walkers": None,
    "return_signal": None,
    "walk_duration_minutes": None,
}
```

**When `event == "dock"` or `event == "dock_timeout"`** (lines 213-223):

```python
walk["active"] = False
walk["returned_at"] = now
walk["return_signal"] = return_signal

departed_at = walk.get("departed_at")
if departed_at:
    try:
        departed = datetime.strptime(departed_at, "%Y-%m-%dT%H:%M:%SZ")
        returned = datetime.strptime(now, "%Y-%m-%dT%H:%M:%SZ")
        walk["walk_duration_minutes"] = round((returned - departed).total_seconds() / 60, 1)
    except ValueError:
        pass

loc_roombas["status"] = "docked"
loc_roombas["docked_at"] = now
```

**When `event == "walkers_detected"`** (new, Change 3):

```python
elif event == "walkers_detected":
    walk["walkers"] = walkers
```

**When `event == "departure_skip"`** (new, Change 5):

```python
elif event == "departure_skip":
    state["skip_reason"] = skip_reason
    state["skip_location"] = location
    if skip_details:
        state["skip_details"] = skip_details
    # Don't modify walk/roombas — just tag the state with skip metadata
```

**Roomba result storage** (for any event):

```python
if roomba_result is not None:
    loc_roombas["last_command_result"] = roomba_result
```

**Return signal values:**

| Value | Trigger | Call site (line) |
|-------|---------|-----------------|
| `"network_wifi"` | Phone rejoined WiFi | `_findmy_poll_loop` line 724 |
| `"ring_motion"` | Person at doorbell during monitoring | `_findmy_poll_loop` line 714 |
| `"findmy"` | FindMy pin near home | `_findmy_poll_loop` line 745 |
| `"timeout"` | 2-hour safety fallback | `_findmy_poll_loop` line 762 |

**All dock call sites updated:**

| Line | Current | New |
|------|---------|-----|
| 714 | `_update_state_dog_walk(location, "dock")` | `_update_state_dog_walk(location, "dock", return_signal="ring_motion", roomba_result=roomba_result)` |
| 724 | `_update_state_dog_walk(location, "dock")` | `_update_state_dog_walk(location, "dock", return_signal="network_wifi", roomba_result=roomba_result)` |
| 745 | `_update_state_dog_walk(location, "dock")` | `_update_state_dog_walk(location, "dock", return_signal="findmy", roomba_result=roomba_result)` |
| 762 | `_update_state_dog_walk(location, "dock_timeout")` | `_update_state_dog_walk(location, "dock_timeout", return_signal="timeout", roomba_result=roomba_result)` |

**Departure call sites updated (for roomba_result):**

| Line | Current | New |
|------|---------|-----|
| 1049 | `_update_state_dog_walk(location, "departure", people=total_people, dogs=total_dogs)` | `_update_state_dog_walk(location, "departure", people=total_people, dogs=total_dogs, roomba_result=roomba_result)` |
| 888 | `_update_state_dog_walk(location, "departure", people=1, dogs=1)` | `_update_state_dog_walk(location, "departure", people=1, dogs=1, roomba_result=roomba_result)` |

---

### Change 3: Store `walkers` list via `_update_state_dog_walk()`

**Why:** Dashboard should show who was on the walk and correlate with return signals.

**Files:** `ring-listener.py`

**Approach:** After `_detect_who_left()` runs (line 698, inside `_findmy_poll_loop()`), update `dog_walk.walkers` through `_update_state_dog_walk()` — **no raw `_read_state()`/`_write_state()` calls**.

```python
# In _findmy_poll_loop(), after line 699:
walkers = await asyncio.to_thread(_detect_who_left, location)
log(f"RETURN MONITOR: Walkers detected: {walkers}")
_update_state_dog_walk(location, "walkers_detected", walkers=walkers)
```

This emits a `walkers_detected` event in JSONL. The departure event has `walkers: null`; the `walkers_detected` event (~2 min later) fills it in. Subsequent events (dock) inherit the `walkers` field from state since `_update_state_dog_walk("dock")` reads the existing `walk` dict and only mutates specific fields.

**Dashboard handling:** When reconstructing a walk, the `walkers` field is populated from the `walkers_detected` event and persists through dock.

---

### Change 4: Return Roomba command results and store in state

**Why:** Dashboard should show Roomba reliability — did the command actually succeed?

**Files:** `ring-listener.py`

**Function:** `run_roomba_command()` (line 454)

Change return type from `None` to `dict`:

```python
def run_roomba_command(location: str, action: str) -> dict:
    """Start or dock Roombas. Returns result dict with per-Roomba outcomes."""
```

**Return schema:**

```python
{
    "success": bool,       # True if all commands returned 0
    "results": [
        {
            "name": str,       # "crosstown-roomba" or "floomba" or "philly"
            "command": str,    # "start all" or "start" or "dock"
            "returncode": int, # 0 = success, -1 = exception
            "output": str,     # stdout first 200 chars
            "error": str | None,  # stderr first 200 chars, or None
        },
    ]
}
```

**Edge cases:**

| Case | Return value |
|------|-------------|
| Cooldown active (line 459-462) | `{"success": False, "results": [], "skipped": "cooldown", "remaining_min": N}` |
| Command not found in ROOMBA_COMMANDS | `{"success": False, "results": [], "skipped": "no_command"}` |
| subprocess.run raises exception | Result entry with `returncode: -1, error: str(e)` |
| Cabin: one Roomba succeeds, one fails | `success: False` (all must succeed), individual results show which failed |

**All 6 `run_roomba_command()` call sites:**

At each site, capture the return value and pass to `_update_state_dog_walk()`:

| Line | Context | Pattern |
|------|---------|---------|
| 713 (Ring motion dock) | `roomba_result = run_roomba_command(location, "dock")` then pass to `_update_state_dog_walk(location, "dock", return_signal="ring_motion", roomba_result=roomba_result)` |
| 723 (network dock) | Same pattern, `return_signal="network_wifi"` |
| 744 (FindMy dock) | Same pattern, `return_signal="findmy"` |
| 761 (timeout dock) | Same pattern, `return_signal="timeout"` |
| 887 (confirmation reply start) | `roomba_result = run_roomba_command(location, "start")` then pass to `_update_state_dog_walk(location, "departure", people=1, dogs=1, roomba_result=roomba_result)` |
| 1048 (auto-start, 2+ dogs) | `roomba_result = run_roomba_command(location, "start")` then pass to `_update_state_dog_walk(location, "departure", ..., roomba_result=roomba_result)` |

**Storage:** `_update_state_dog_walk()` stores the result:

```python
if roomba_result is not None:
    loc_roombas["last_command_result"] = roomba_result
```

**Note on `roombas[location].status`:** Reflects **commanded** state, not confirmed. A failed command still sets `status: "running"` (same as today). `last_command_result.success` lets the dashboard flag discrepancies.

---

### Change 5: Emit skip events via `_update_state_dog_walk()`

**Why:** Dashboard should show detection funnel — how many motion events passed each filter, how many became walks.

**Files:** `ring-listener.py`

**Approach:** Skip events go through `_update_state_dog_walk(event="departure_skip")` — **no raw `_read_state()`/`_write_state()` calls**. This means skip events are full state snapshots (preserving the JSONL contract) and all state writes flow through the same centralized function.

The `departure_skip` event handler inside `_update_state_dog_walk()` sets transient skip fields on the state dict:

```python
elif event == "departure_skip":
    state["skip_reason"] = skip_reason
    state["skip_location"] = location
    if skip_details:
        state["skip_details"] = skip_details
```

These fields are **explicitly stripped** by `_write_state()` on the next write (via `_SKIP_KEYS` — see Change 1). This prevents stale skip metadata from leaking into later JSONL lines or `state.json`.

**Helper function** (convenience wrapper, no state logic):

```python
def _emit_skip_event(location: str, reason: str, details: dict | None = None) -> None:
    """Convenience wrapper to emit a departure_skip via _update_state_dog_walk."""
    _update_state_dog_walk(location, "departure_skip", skip_reason=reason, skip_details=details)
```

**Skip reasons and where to call `_emit_skip_event()`:**

| Reason | Code location | Details |
|--------|--------------|---------|
| `"outside_walk_hours"` | `check_departure()` line 979 | `{"hour": hour}` |
| `"confirmed_vacant"` | `check_departure()` line 984 | `{}` |
| `"wifi_present"` | `check_departure()` line 1031 | `{"wifi": wifi_detail["people"]}` (from Change 6) |
| `"cabin_prompt_suppressed"` | `check_departure()` line 1061 | `{"window": window}` |

**Excluded reasons:**

| Excluded | Why |
|----------|-----|
| `"insufficient_count"` (line 1022) | Fires on every intermediate accumulator check — normal non-event, would flood JSONL |
| `"cooldown_active"` (in `run_roomba_command`) | Not a departure skip — it's a command skip, captured via Change 4's return value |

---

### Change 6: Per-person WiFi detail in network checks

**Why:** Dashboard can show who was home vs away, correlated with walk events.

**Files:** `ring-listener.py`

**Function:** Refactor `_check_network_presence()` (line 608) into a detailed version:

```python
def _check_network_presence_detailed(location: str) -> dict:
    """Check network presence and return per-person details.

    Returns: {"any_present": bool, "people": {"dylan": {"present": bool}, "julia": {"present": bool}}}
    """
    try:
        # ... existing subprocess.run logic (lines 614-627) ...
        scan = json.loads(result.stdout)
        presence = scan.get("presence", {})
        people_detail = {}
        any_present = False
        for person, info in presence.items():
            present = info.get("present", False)
            people_detail[person] = {"present": present}
            if present:
                any_present = True
        return {"any_present": any_present, "people": people_detail}
    except Exception as e:
        log(f"NETWORK CHECK: error: {e}")
        return {"any_present": False, "people": {}}


def _check_network_presence(location: str) -> bool:
    """Boolean wrapper for backward compatibility."""
    return _check_network_presence_detailed(location)["any_present"]
```

**Where per-person detail is used:**

**1. Departure WiFi check** (`check_departure()` line 1030):

Since `check_departure()` is now async (see Architecture Decision: Blocking Subprocess), use `asyncio.to_thread`:

```python
wifi_detail = await asyncio.to_thread(_check_network_presence_detailed, location)
if wifi_detail["any_present"]:
    log(f"DEPARTURE SKIP: phone still on {location} WiFi at decision time")
    _emit_skip_event(location, "wifi_present", {"wifi": wifi_detail["people"]})
    return
```

**2. Return-monitor network polling** (`_findmy_poll_loop()` line 719):

Use the detailed version and store per-person WiFi data in `findmy_polling.last_network_check`:

```python
wifi_detail = await asyncio.to_thread(_check_network_presence_detailed, location)
if wifi_detail["any_present"]:
    elapsed_min = int(elapsed / 60)
    log(f"RETURN MONITOR: Network return after {elapsed_min}min — docking at {location}")
    roomba_result = run_roomba_command(location, "dock")
    _update_state_dog_walk(location, "dock", return_signal="network_wifi", roomba_result=roomba_result)
    _update_state_findmy(location, "stop")
    return
```

**`_update_state_findmy()` enhancement** — add `network_detail` parameter:

```python
def _update_state_findmy(location: str, event: str, result: dict | None = None, network_detail: dict | None = None) -> None:
```

When `event == "poll"`:

```python
state["findmy_polling"]["polls"] += 1
state["findmy_polling"]["last_poll_at"] = now
if result:
    state["findmy_polling"]["last_result"] = { ... }  # existing
if network_detail:
    state["findmy_polling"]["last_network_check"] = network_detail  # new
```

**Where to pass `network_detail`:** On every network poll iteration in `_findmy_poll_loop()`, regardless of whether return is detected:

```python
wifi_detail = await asyncio.to_thread(_check_network_presence_detailed, location)

# Store WiFi detail on every poll (for dashboard timeline)
_update_state_findmy(location, "poll", network_detail=wifi_detail)

if wifi_detail["any_present"]:
    # ... dock logic ...
```

This gives the dashboard a per-minute WiFi presence timeline during walks.

**Note on `findmy_polling.polls` semantics:** The `polls` counter increments once per `_update_state_findmy(event="poll")` call. With this change, it increments on every network poll iteration (~60s), not just FindMy checks (~5min after 20min). This is intentional — the counter now reflects total monitoring iterations, and the dashboard can use `last_result` (FindMy data, present only after 20min) vs `last_network_check` (WiFi data, present every iteration) to distinguish poll types.

---

## Updated JSONL Schema (After Changes)

### All events: full state snapshot + event_type

Every JSONL line is a full state snapshot (same as `state.json`) with `event_type` identifying what triggered the write. Transient fields (`skip_reason`, `skip_location`, `skip_details`) are present only on `departure_skip` events. `_write_state()` preserves them when `event_type == "departure_skip"` and strips them on all other event types, so they never leak into subsequent writes.

### Departure event

```json
{
  "timestamp": "2026-03-28T14:30:00Z",
  "event_type": "departure",
  "dog_walk": {
    "active": true,
    "location": "cabin",
    "departed_at": "2026-03-28T14:30:00Z",
    "returned_at": null,
    "people": 2,
    "dogs": 1,
    "walkers": null,
    "return_signal": null,
    "walk_duration_minutes": null
  },
  "roombas": {
    "cabin": {
      "status": "running",
      "started_at": "2026-03-28T14:30:01Z",
      "docked_at": null,
      "trigger": "dog_walk_departure",
      "last_command_result": {
        "success": true,
        "results": [
          {"name": "floomba", "command": "start", "returncode": 0, "output": "OK", "error": null},
          {"name": "philly", "command": "start", "returncode": 0, "output": "OK", "error": null}
        ]
      }
    }
  },
  "last_vision": { "..." },
  "findmy_polling": { "..." }
}
```

### Walkers detected event (~2 min after departure)

```json
{
  "timestamp": "2026-03-28T14:32:00Z",
  "event_type": "walkers_detected",
  "dog_walk": {
    "active": true,
    "location": "cabin",
    "departed_at": "2026-03-28T14:30:00Z",
    "walkers": ["dylan", "julia"],
    "return_signal": null,
    "walk_duration_minutes": null,
    "people": 2,
    "dogs": 1
  },
  "findmy_polling": {
    "active": true,
    "location": "cabin",
    "last_network_check": {
      "any_present": false,
      "people": {"dylan": {"present": false}, "julia": {"present": false}}
    },
    "..."
  },
  "..."
}
```

### FindMy poll event (during walk, every 60s network + every 5min FindMy)

```json
{
  "timestamp": "2026-03-28T14:45:00Z",
  "event_type": "findmy_poll",
  "findmy_polling": {
    "active": true,
    "polls": 13,
    "last_poll_at": "2026-03-28T14:45:00Z",
    "last_network_check": {
      "any_present": false,
      "people": {"dylan": {"present": false}, "julia": {"present": false}}
    },
    "last_result": {
      "street": "School House Rd",
      "near_home": false,
      "description": "Walking on School House Rd, about 0.5 miles from home"
    }
  },
  "..."
}
```

### Dock event

```json
{
  "timestamp": "2026-03-28T15:05:00Z",
  "event_type": "dock",
  "dog_walk": {
    "active": false,
    "location": "cabin",
    "departed_at": "2026-03-28T14:30:00Z",
    "returned_at": "2026-03-28T15:05:00Z",
    "people": 2,
    "dogs": 1,
    "walkers": ["dylan", "julia"],
    "return_signal": "network_wifi",
    "walk_duration_minutes": 35.0
  },
  "roombas": {
    "cabin": {
      "status": "docked",
      "started_at": "2026-03-28T14:30:01Z",
      "docked_at": "2026-03-28T15:05:01Z",
      "trigger": "dog_walk_departure",
      "last_command_result": {
        "success": true,
        "results": [
          {"name": "floomba", "command": "dock", "returncode": 0, "output": "OK", "error": null},
          {"name": "philly", "command": "dock", "returncode": 0, "output": "OK", "error": null}
        ]
      }
    }
  },
  "..."
}
```

### Skip event (full state snapshot + transient skip metadata)

```json
{
  "timestamp": "2026-03-28T14:25:00Z",
  "event_type": "departure_skip",
  "skip_reason": "wifi_present",
  "skip_location": "cabin",
  "skip_details": {
    "wifi": {
      "dylan": {"present": true},
      "julia": {"present": true}
    }
  },
  "dog_walk": { "..." },
  "roombas": { "..." },
  "last_vision": { "..." },
  "findmy_polling": { "..." }
}
```

**Note:** `skip_reason`, `skip_location`, and `skip_details` are **not present** on any subsequent non-skip JSONL line — `_write_state()` strips them via `_SKIP_KEYS`.

---

## Dashboard Queries Enabled

| Question | Query |
|----------|-------|
| How long are walks on average? | `event_type == "dock"` → mean of `walk_duration_minutes` |
| Which return signal fires most? | `event_type == "dock"` → group by `return_signal` |
| How reliable are Roomba commands? | `event_type in ("departure", "dock")` → `last_command_result.success` rate |
| How many false positives does WiFi prevent? | `event_type == "departure_skip" AND skip_reason == "wifi_present"` count |
| What time of day do walks happen? | `event_type == "departure"` → histogram of `departed_at` hour |
| Who walks the dogs? | `event_type == "walkers_detected"` → `walkers` field frequency |
| How long until return is detected? | `event_type == "dock"` → `walk_duration_minutes` by `return_signal` |
| Detection funnel | Count by `event_type`: departure_skip (by reason) → departure → dock |
| Roomba partial failures | `last_command_result.results` where any `returncode != 0` |
| Per-person WiFi timeline during walk | `event_type == "findmy_poll"` → `last_network_check.people` over time |

---

## Implementation Order

1. **Change 1** (`event_type` + state lock + transient cleanup) — foundation, all changes depend on this
2. **Change 2** (`return_signal` + `walk_duration_minutes` + expanded `_update_state_dog_walk` signature) — high value, pairs with Change 4
3. **Change 4** (`run_roomba_command` results) — pairs with Change 2 (same function signature)
4. **Change 3** (`walkers` list) — small, uses new `walkers_detected` event from expanded signature
5. **Change 5** (`_emit_skip_event`) — uses `departure_skip` event from expanded signature
6. **Change 6** (per-person WiFi + async `check_departure`) — enhances Changes 5 and findmy_poll

Each change is independently deployable. New fields are additive — old dashboard readers ignore them.

---

## Edge Case: Inbox-Started Walks (`dog-walk-start` CLI)

The `dog-walk-start` CLI starts Roombas directly via shell commands (not `run_roomba_command()`) and writes a JSON signal to `~/.openclaw/ring-listener/inbox/`. The listener's `_inbox_poll_loop()` (line 766) picks it up and calls `start_return_monitor(location)` — but **never writes a departure event**. This means:

- `dog_walk` state may be empty or stale when `walkers_detected` / `dock` events fire
- `walk_duration_minutes` can't be computed (no `departed_at`)
- `roomba_result` is not captured (Roombas started outside Python)

**Fix:** When `_inbox_poll_loop()` processes a signal (line 802-804), synthesize a departure event before starting the monitor:

```python
# In _inbox_poll_loop(), after line 802 ("INBOX: starting return monitor"):
_update_state_dog_walk(
    location, "departure",
    people=0, dogs=0,  # unknown — manual trigger
    roomba_result={"success": True, "results": [], "source": "dog-walk-start"},
)
_clear_pending_confirmation("inbox IPC")
start_return_monitor(location)
```

This ensures:
- A `departure` JSONL event is emitted with `departed_at` set
- `walk_duration_minutes` can be computed at dock time
- `people=0, dogs=0` signals "manual trigger" to the dashboard (vs vision-detected `people >= 1`)
- `roomba_result.source = "dog-walk-start"` distinguishes CLI-started Roombas from listener-started ones
- `walkers_detected` and `dock` events find a valid `dog_walk` dict in state

**Dashboard handling:** Filter `event_type == "departure" AND dog_walk.people == 0` for manual walks vs vision-triggered.

---

## Doc Updates Required

After implementation, update `IMPLEMENTATION.md`:

1. **State file schema** (~line 578): Add `walkers`, `return_signal`, `walk_duration_minutes` to `dog_walk`; add `last_command_result` to `roombas[location]`; add `event_type`, `skip_reason`, `skip_location`, `skip_details` as top-level fields; add `last_network_check` to `findmy_polling`
2. **JSONL history description** (~line 627): Document that `event_type` is present on every line, list all event types, note that transient skip fields only appear on `departure_skip` events
3. **Decision logic section** (~line 295): Note that suppressed departures are recorded as `departure_skip` events; note `check_departure()` is now async
4. **Timing constants table**: Note `_DEPARTURE_WINDOW` is 180s (3 minutes)

---

## Testing

### Per-change verification

| Change | Test command | Expected |
|--------|-------------|----------|
| 1 (event_type) | `tail -1 ~/.openclaw/ring-listener/history/$(date +%Y-%m-%d).jsonl \| python3 -c "import sys,json; d=json.load(sys.stdin); assert 'event_type' in d; print(d['event_type'])"` | Prints event type string |
| 1 (transient cleanup) | After a skip then a departure: `python3 -c "import json; s=json.load(open('$HOME/.openclaw/ring-listener/state.json')); assert 'skip_reason' not in s; print('OK: no stale skip fields')"` | `OK: no stale skip fields` |
| 1 (lock) | `grep -n '_state_lock' ~/.openclaw/skills/ring-doorbell/ring-listener.py` | Shows lock defined as `threading.Lock()` and used in each `_update_state_*` function via `with _state_lock:` |
| 2 (return_signal) | After dock: `python3 -c "import json; s=json.load(open('$HOME/.openclaw/ring-listener/state.json')); print(s['dog_walk']['return_signal'], s['dog_walk']['walk_duration_minutes'])"` | e.g. `network_wifi 35.0` |
| 3 (walkers) | During walk (~2 min after departure): `python3 -c "import json; s=json.load(open('$HOME/.openclaw/ring-listener/state.json')); print(s['dog_walk']['walkers'])"` | e.g. `['dylan', 'julia']` |
| 4 (roomba result) | After departure: `python3 -c "import json; s=json.load(open('$HOME/.openclaw/ring-listener/state.json')); r=s['roombas']['cabin']['last_command_result']; print(r['success'], len(r['results']))"` | `True 2` |
| 4 (cooldown) | Trigger roomba during cooldown: check result has `"skipped": "cooldown"` |
| 5 (skip events) | Trigger motion outside walk hours: `grep departure_skip ~/.openclaw/ring-listener/history/$(date +%Y-%m-%d).jsonl \| tail -1 \| python3 -c "import sys,json; d=json.loads(sys.stdin.readline()); print(d['event_type'], d['skip_reason'])"` | `departure_skip outside_walk_hours` |
| 5 (full state) | Same skip line: `python3 -c "import sys,json; d=json.loads(sys.stdin.readline()); assert 'dog_walk' in d; print('OK: full state')"` | `OK: full state` |
| 6 (WiFi detail) | Trigger motion with phone on WiFi: `grep wifi_present ~/.openclaw/ring-listener/history/$(date +%Y-%m-%d).jsonl \| tail -1 \| python3 -c "import sys,json; d=json.loads(sys.stdin.read().strip()); print(json.dumps(d.get('skip_details',{}).get('wifi',{}), indent=2))"` | Per-person presence dict, e.g. `{"dylan": {"present": true}}` |
| 6 (async check_departure) | Verify no event loop warnings in `~/.openclaw/logs/ring-listener.log` during motion events |
| 6 (findmy network detail) | During walk: `python3 -c "import json; s=json.load(open('$HOME/.openclaw/ring-listener/state.json')); print(json.dumps(s['findmy_polling'].get('last_network_check'), indent=2))"` | Per-person WiFi dict |

### Integration test

1. Deploy to Mini
2. Manually trigger via `dog-walk-start cabin`
3. Wait for return detection (or manually connect phone to WiFi)
4. Verify JSONL sequence: `departure` → `walkers_detected` → `findmy_poll` (with `last_network_check`) → `dock` (with `return_signal`, `walk_duration_minutes`)
5. Verify state.json has no stale `skip_reason`/`skip_details` after the dock event
6. Verify existing `/api/dog-walks` endpoint still returns data

### Backward compatibility check

```bash
for f in ~/.openclaw/ring-listener/history/*.jsonl; do
  python3 -c "
import json
for line in open('$f'):
    d = json.loads(line)
    et = d.get('event_type', 'unknown')
" || echo "FAIL: $f"
done
```
