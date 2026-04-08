# Dog Walk Listener Hardening — Claude Implementation Plan

## Overview

Harden `openclaw/skills/dog-walk/dog-walk-listener.py` so the listener is safer under real production conditions:

- Ring callbacks must be truly thread-safe
- The asyncio event loop must not be blocked by synchronous subprocess/network calls
- Car-trip detection must use real elapsed time between Fi readings
- Poll-path state writes should be reduced to a sane minimum

This plan is intentionally implementation-oriented. Claude should be able to execute it directly without rediscovering the problem statement.

## Status

Implemented (2026-04-07). All 5 phases deployed to Mini.

## Goals

1. Preserve the existing behavior and signal fusion model.
2. Eliminate the known threading bug in the Ring callback path.
3. Remove the most important event-loop blocking calls from async code paths.
4. Fix the Fi speed heuristic so car-trip detection is based on real timestamps.
5. Reduce poll-path write amplification without changing the external state schema.
6. Move the most frequent synchronous route/state file writes off the asyncio thread.

## Non-Goals

- Rewriting the listener into a fully async architecture
- Changing the walk detection product behavior in a major way
- Replacing JSON state/history persistence
- Introducing a test framework for this file
- Refactoring unrelated route/dashboard behavior

## Primary Issues

### 1. Ring ding scheduling is not actually thread-safe

`on_event()` is invoked by the Ring listener callback layer and currently calls `asyncio.get_event_loop()` before `asyncio.run_coroutine_threadsafe(...)`.

That is unsafe if the callback is running on a non-event-loop thread. On modern Python, this can raise `RuntimeError` or target the wrong loop. Result: ding notifications may be dropped.

### 2. Ring mutable state crosses threads unsafely

The current code mutates and reads these globals from different threads:

- `_recent_events`
- `_ring_departure_motion`
- `_ring_motion_during_walk`

The current comment about "GIL-atomic" is not sufficient. Iterating a dict in one thread while mutating it in another can still fail or observe inconsistent state.

### 3. Async flows still contain blocking sync calls

These synchronous functions are called directly from async code paths:

- `send_imessage()`
- `run_roomba_command()`
- `_set_fi_collar_mode()`
- `_check_fi_gps()` in `main()`

Those perform network I/O and subprocess calls. They can block the event loop for seconds or longer, especially in dock flows.

### 4. Car-trip speed calculation is using the wrong time basis

The code currently derives speed from the difference between successive `age_s` values. `age_s` is "how stale this reading is right now", not "elapsed time between reading A and reading B". That can inflate speed and incorrectly classify a normal walk as a car trip.

### 5. Return-monitor poll writes are unnecessarily chatty

Each 30-second poll can write return-monitor state twice:

- once for WiFi
- once for Fi GPS

Each write updates `state.json` and appends a JSONL history line. That is more churn than the dashboard needs.

## Desired End State

After the implementation:

- `on_event()` becomes a thin bridge that hands work to the main asyncio loop
- all listener-owned mutable state is mutated on the asyncio thread, not on the callback thread
- async code uses `asyncio.to_thread()` for blocking helpers
- car-trip detection uses parsed Fi report timestamps
- return-monitor state emits at most one `"poll"` write per loop iteration
- route/state persistence no longer blocks the event loop in the return-monitor hot path
- the file still compiles with `python3 -m py_compile`

## Implementation Strategy

Implement in five phases, in order.

## Phase 1: Make Ring event handling single-thread-owned

### Objective

Move all meaningful Ring event processing onto the asyncio loop thread.

### Design

Introduce a module-level loop reference:

```python
_main_loop: asyncio.AbstractEventLoop | None = None
```

Set it in `main()` after the event loop is running:

```python
global _main_loop
_main_loop = asyncio.get_running_loop()
```

Do not call `asyncio.get_event_loop()` inside `on_event()`.

### Required code shape

Keep `on_event()` very small:

1. Read minimal event fields from the callback
2. If `_main_loop` is missing, log and return
3. Use `_main_loop.call_soon_threadsafe(...)` to hand off to a new sync helper on the loop thread

Recommended helper:

```python
def _process_ring_event_on_loop(
    event_id: int,
    kind: str,
    device: str,
    doorbot_id: int,
    state: str,
    is_update: bool,
) -> None:
    ...
```

### What `_process_ring_event_on_loop()` should own

- `_recent_events` cleanup and dedup
- logging of Ring event metadata
- motion handling
- task creation for ding notifications

This keeps `_recent_events`, `_ring_departure_motion`, and `_ring_motion_during_walk` on a single owner thread.

### Ding path

Inside `_process_ring_event_on_loop()`, when `kind == "ding"`:

```python
asyncio.create_task(_handle_ding(device, doorbot_id, event_id))
```

Do not use `run_coroutine_threadsafe()` anymore.

### Motion path

The current `_handle_motion_sync()` can remain synchronous, but it should now run only on the loop thread via `_process_ring_event_on_loop()`.

Update its docstring/comments accordingly. Remove the current claim that GIL-atomic mutation makes cross-thread access safe.

### Acceptance criteria

- No use of `asyncio.get_event_loop()` remains inside `on_event()`
- No direct mutation of `_recent_events` or `_ring_departure_motion` happens on the callback thread
- Ding notifications are scheduled through the main loop only

## Phase 2: Remove blocking calls from async hot paths

### Objective

Prevent the event loop from stalling on subprocess and HTTP work.

### Rule

If a helper does blocking subprocess or network I/O and is called from `async def`, call it through `await asyncio.to_thread(...)`.

### Must-change call sites

#### In `_handle_ding()`

Current:

```python
send_imessage(msg)
```

Change to:

```python
await asyncio.to_thread(send_imessage, msg)
```

#### In `_return_poll_loop()`

Wrap these:

- initial "tracking your walk" iMessage
- `run_roomba_command(location, "dock")`
- timeout iMessage
- `_set_fi_collar_mode("NORMAL")` in `finally`
- `_set_fi_collar_mode("NORMAL")` in the car-trip branch
- welcome-back iMessage

#### In `_fi_departure_poll_loop()`

Wrap these:

- `_set_fi_collar_mode("LOST_DOG")`
- departure iMessages
- `run_roomba_command(location, "start")`

#### In `main()`

The startup collar status check currently calls `_check_fi_gps()` synchronously. Change to:

```python
fi_result = await asyncio.to_thread(_check_fi_gps)
```

Also wrap the startup recovery call to `_set_fi_collar_mode("NORMAL")`.

### Optional but good cleanup

Create tiny async wrappers near the helpers:

```python
async def _send_imessage_async(text: str) -> bool:
    return await asyncio.to_thread(send_imessage, text)

async def _run_roomba_command_async(location: str, action: str) -> dict:
    return await asyncio.to_thread(run_roomba_command, location, action)

async def _set_fi_collar_mode_async(mode: str) -> bool:
    return await asyncio.to_thread(_set_fi_collar_mode, mode)
```

This is preferable if it keeps the call sites readable.

### Important constraint

Do not convert the low-level helpers themselves to `async def`. Keep them synchronous and wrap them at the call sites or with thin async wrappers. That minimizes blast radius.

### Acceptance criteria

- No direct `send_imessage(...)` calls remain inside `async def`
- No direct `run_roomba_command(...)` calls remain inside `async def`
- No direct `_set_fi_collar_mode(...)` calls remain inside `async def`
- `main()` no longer calls `_check_fi_gps()` synchronously

## Phase 3: Fix car-trip speed detection

### Objective

Base speed on actual elapsed time between two Fi readings.

### Design

Add a helper that extracts a timezone-aware reading timestamp from a Fi result:

```python
def _fi_reported_at(fi_result: dict) -> datetime | None:
    raw = fi_result.get("lastReport") or fi_result.get("connectionDate")
    if not raw:
        return None
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
```

### Replace this logic

Current logic in `_return_poll_loop()`:

```python
time_between = fi_result.get("age_s", 30)
prev_age = prev_gps.get("age_s", 30)
time_gap = max(abs(time_between - prev_age), 10)
speed_mps = dist_between / time_gap if time_gap > 0 else 0
```

Replace with real timestamp math:

```python
prev_ts = _fi_reported_at(prev_gps)
cur_ts = _fi_reported_at(fi_result)

if prev_ts and cur_ts:
    time_gap = (cur_ts - prev_ts).total_seconds()
else:
    time_gap = 0

if time_gap > 0:
    speed_mps = dist_between / time_gap
else:
    speed_mps = 0
```

### Guardrails

Add conservative sanity checks before using the speed:

- ignore zero or negative gaps
- ignore extremely large gaps, for example `> 900s`
- do not force a `max(..., 10)` fallback, because that can fabricate speed

Recommended:

```python
if 0 < time_gap <= 900:
    speed_mps = dist_between / time_gap
else:
    speed_mps = 0
```

### Acceptance criteria

- No speed calculation uses `age_s` delta
- Car-trip detection uses parsed Fi timestamps
- Duplicate or stale timestamps do not trigger false car speed

## Phase 4: Reduce poll-path state churn

### Objective

Ensure the return monitor emits at most one `"poll"` state write per loop iteration.

### Minimal safe change

Do not redesign the state format. Keep the same fields. Just consolidate writes.

### Current behavior

In a poll iteration, `_return_poll_loop()` can call:

- `_update_state_return_monitor(..., network_detail=wifi_detail)`
- `_update_state_return_monitor(..., fi_result=fi_result)`

That produces two history writes.

### Required change

Refactor the loop so it gathers poll data first:

```python
wifi_detail = None
fi_result = None
```

Then at the end of the iteration, if either exists, call `_update_state_return_monitor()` once:

```python
if wifi_detail or fi_result:
    _update_state_return_monitor(
        location,
        "poll",
        fi_result=fi_result,
        network_detail=wifi_detail,
    )
```

### Additional constraint

Do not change the semantics of return detection just to reduce writes. Detection should still happen as soon as any signal is available.

### Optional follow-up

Only if the above is completed cleanly and the file still feels too chatty, add lightweight throttling to `"poll"` history writes. Do not do this in the first pass unless the simpler consolidation is already done.

Possible future throttle rule:

- always write transitions (`start`, `stop`, `dock`, `dock_timeout`)
- for `"poll"`, write only when data changed materially or every N polls

That is explicitly optional. The first implementation pass should stop at one write per poll.

### Acceptance criteria

- `_return_poll_loop()` emits at most one `_update_state_return_monitor(..., "poll", ...)` call per loop iteration
- Existing state keys are preserved

## Phase 5: Move synchronous file writes off the asyncio thread

### Objective

Remove the remaining synchronous route/state persistence from the return-monitor hot path so the event loop stays responsive while GPS polling and Ring callbacks continue.

### Why this matters

These calls still do local file I/O directly on the asyncio thread:

- `_append_active_walk_route_point(fi_result)` during Fi polling
- `_update_state_return_monitor(location, "poll", ...)` once per poll iteration
- `_update_state_dog_walk(location, "dock", ...)` during finalization
- `_update_state_return_monitor(location, "stop")` during finalization

Those are not network-slow, but they are still blocking. `_write_state()` performs:

- JSON serialization
- temp-file write
- `fsync`
- atomic `os.replace`
- history append

That is enough to add jitter under disk pressure and delay queued Ring callbacks.

### Rule

If a helper does synchronous file persistence and is called from `async def`, move that call behind `await asyncio.to_thread(...)` unless it is guaranteed to be trivial and cold-path only.

### Required call-site changes

#### In `_return_poll_loop()`

Current:

```python
_append_active_walk_route_point(fi_result)
```

Change to:

```python
await asyncio.to_thread(_append_active_walk_route_point, fi_result)
```

Current:

```python
_update_state_return_monitor(
    location,
    "poll",
    fi_result=poll_fi_result,
    network_detail=poll_wifi_detail,
)
```

Change to:

```python
await asyncio.to_thread(
    _update_state_return_monitor,
    location,
    "poll",
    poll_fi_result,
    poll_wifi_detail,
)
```

or an equivalent wrapper that preserves keyword readability.

#### In return finalization inside `_return_poll_loop()`

Current:

```python
_append_active_walk_route_point(return_fi)
_update_state_dog_walk(location, "dock", return_signal=return_signal, roomba_result=roomba_result)
_update_state_return_monitor(location, "stop")
```

Change each of those to run via `asyncio.to_thread(...)`.

#### In timeout finalization inside `_return_poll_loop()`

Current:

```python
_update_state_dog_walk(location, "dock_timeout", return_signal="timeout", roomba_result=roomba_result)
_update_state_return_monitor(location, "stop")
```

Change both to run via `asyncio.to_thread(...)`.

### Optional wrapper cleanup

If the call sites get noisy, add async wrappers such as:

```python
async def _append_active_walk_route_point_async(fi_result: dict | None) -> dict | None:
    return await asyncio.to_thread(_append_active_walk_route_point, fi_result)

async def _update_state_return_monitor_async(
    location: str,
    event: str,
    fi_result: dict | None = None,
    network_detail: dict | None = None,
) -> None:
    await asyncio.to_thread(
        _update_state_return_monitor,
        location,
        event,
        fi_result,
        network_detail,
    )

async def _update_state_dog_walk_async(
    location: str,
    event: str,
    **kwargs,
) -> None:
    await asyncio.to_thread(_update_state_dog_walk, location, event, **kwargs)
```

If using wrappers, keep the underlying persistence helpers synchronous.

### State lock note

`_update_state_dog_walk()` and `_update_state_return_monitor()` already use `_state_lock`. Running them in `to_thread()` is fine; the lock still serializes file writes, but the waiting now happens off the event loop thread.

Do not replace `_state_lock` with `asyncio.Lock`. The persistence helpers are synchronous and also used by non-async code paths.

### Constraint

Do not scatter independent background writes with `create_task()` or fire-and-forget threads. These writes still represent ordered state transitions. Offload them to a worker thread with `await asyncio.to_thread(...)` so ordering is preserved.

### Acceptance criteria

- No direct calls to `_append_active_walk_route_point(...)` remain inside `async def`
- No direct calls to `_update_state_return_monitor(...)` remain inside `_return_poll_loop()`
- No direct calls to `_update_state_dog_walk(...)` remain inside `_return_poll_loop()`
- Return-monitor state and route persistence still happen in the same logical order as before
- The file still compiles cleanly

## Suggested Edit Order

1. Add `_main_loop`
2. Add `_process_ring_event_on_loop()`
3. Rewrite `on_event()` to bridge into `_main_loop.call_soon_threadsafe(...)`
4. Update `_handle_motion_sync()` comments/docstring
5. Convert blocking async call sites to `asyncio.to_thread(...)`
6. Add `_fi_reported_at()` and fix car-trip timing
7. Consolidate return-monitor poll writes
8. Move route/state write call sites in async code behind `asyncio.to_thread(...)`
9. Run compile verification

## Verification

Run:

```bash
python3 -m py_compile openclaw/skills/dog-walk/dog-walk-listener.py
```

Then do a targeted local grep pass to confirm the async cleanup:

```bash
rg -n "send_imessage\\(|run_roomba_command\\(|_set_fi_collar_mode\\(|_append_active_walk_route_point\\(|_update_state_return_monitor\\(|_update_state_dog_walk\\(" openclaw/skills/dog-walk/dog-walk-listener.py
```

Expected result after implementation:

- remaining matches in synchronous helpers are fine
- async call sites should use wrappers or `asyncio.to_thread(...)`
- `_return_poll_loop()` should not directly invoke synchronous persistence helpers

If deploying to the Mini, do a runtime smoke check:

```bash
tail -f ~/.openclaw/logs/dog-walk-listener.log
launchctl kickstart -k gui/$(id -u)/ai.openclaw.dog-walk-listener
```

Watch for:

- listener startup succeeds
- no `RuntimeError` about missing event loop in callback thread
- ding and motion events still log normally
- no obvious regressions in departure/return flow

## Risks

### Risk: event ordering changes

Moving Ring handling onto the loop thread slightly changes timing. That is acceptable and is the point of the fix. The work is tiny and should still be effectively immediate.

### Risk: hidden blocking helpers remain

Claude should review every `async def` in this file after the main edits and catch any remaining direct synchronous network/subprocess calls.

### Risk: over-refactor

Do not rewrite the overall listener architecture. This plan is a hardening pass, not a redesign.

## Out of Scope Follow-Ups

These are reasonable later, but not part of this patch:

- add structured tests around speed calculation and Ring event bridging
- move dock verification threads to asyncio tasks
- reduce or batch history fsync behavior after the async-thread offload is complete
- extract Fi, Roomba, and Ring integration layers into separate modules

## Claude Execution Notes

Implement the code changes only in:

- `openclaw/skills/dog-walk/dog-walk-listener.py`

Do not change behavior outside the four phases above unless required for correctness. Keep the diff focused.
