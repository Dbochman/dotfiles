---
name: polling-loop-phantom-transition-on-pause-exit
description: |
  Fix phantom event detection that fires moments after a legitimate trigger in
  polling loops that use transition tracking + conditional observation pauses.
  Use when: (1) a duplicate event (departure/arrival/alert/state-change) fires
  seconds after the real one legitimately finalized, (2) the loop tracks state
  transitions via local vars like `last_X` and does `if busy: continue` BEFORE
  fetching fresh observations during a "busy/paused" phase, (3) logs show a
  transition was "detected" immediately after the pause cleared, (4) the
  upstream API has lag in reflecting the new state after the underlying
  condition actually changed. Covers reseed-on-pause-exit pattern and
  physical-state sanity guards on transition-triggered actions.
author: Claude Code
version: 1.0.0
date: 2026-04-21
---

# Polling Loop Phantom Transition on Pause-Exit

## Problem

A polling loop that (a) detects events by comparing current observation to a
tracker of the previous observation (`last_connection`, `last_activity`,
`last_state`, etc.) and (b) skips observation during a "busy" or "paused"
phase via an early `continue`/`return` will fire phantom transitions the
instant the pause clears. The bug produces a characteristic signature:
**a duplicate event fires within seconds of the real event finalizing**.

## Context / Trigger Conditions

Look for this bug when:

- A duplicate event (walk departure, session start, alert, alarm, trigger)
  appears in history/logs within **seconds to tens of seconds** of a
  legitimate event ending. The suspiciously small gap is the smoking gun — real
  events don't repeat that fast.
- The loop looks like:
  ```python
  while True:
      await asyncio.sleep(interval)
      if paused_or_busy:
          continue              # ← skips the observation + tracker update
      obs = fetch_observation()
      if last_tracker != obs.value:
          fire_event()
      last_tracker = obs.value
  ```
- Logs show a state-transition detection message immediately after a "pause
  cleared" / "monitor ended" / "finished" message. Look for pairs like:
  - `Monitor ended` → seconds later → `Activity transition detected`
  - `Session closed` → seconds later → `New session started`
- The upstream data source (API, device, feed) has known lag reflecting the
  post-event state. E.g., Fi collar activity feed stays `Walk` for minutes
  after the dog is physically back inside.

## Root Cause

The tracker variable (`last_connection`, `last_activity`, etc.) stores the
"previous observation" used for transition detection. During the paused
phase, the loop's `continue` skips `fetch_observation()` — so the tracker
**stays frozen at its pre-pause value** for however long the pause lasts.

When the pause clears:
1. Next iteration fetches a fresh observation
2. Upstream still reflects the just-ended event's state (API lag)
3. Tracker compares fresh-but-lagging observation to the frozen pre-pause
   baseline → sees a "transition" that already happened
4. Fires a phantom event

## Solution

Apply **both** fixes together:

### Fix 1: Reseed trackers on pause-exit

Add a module-level flag that the pause-exit sets, and have the next poll
reseed trackers from current observed state without firing transitions:

```python
_reseed_trackers_after_pause: bool = False  # module-level

# In the pause's finally block:
try:
    await run_paused_phase(...)
finally:
    _paused = False
    _reseed_trackers_after_pause = True

# In the poll loop, AFTER fetching observation but BEFORE transition detection:
obs = fetch_observation()
if not obs:
    continue

global _reseed_trackers_after_pause
if _reseed_trackers_after_pause:
    last_connection = obs.get("connection", "") or last_connection
    last_activity = obs.get("activity", "") or last_activity
    _reseed_trackers_after_pause = False
    log("Reseeded trackers after pause — skipping transition detection this iteration")
    continue  # don't fire on this iteration

# ... normal transition detection continues
```

This ensures the next "real" transition is compared against the post-pause
baseline, not the pre-pause one.

### Fix 2: Physical-state sanity guard on the triggered action

Before acting on a detected transition, verify the underlying physical
precondition actually holds. For a "dog left home" detection, require the
dog's GPS to actually be outside the geofence. For a "session started"
detection, require the session handle to actually be valid. Etc.

```python
if activity_transitioned_to_walk and base_disconnected:
    # Physical-state guard: don't trust the transition alone
    distance = _distance_to(fi_result, home)
    radius = GEOFENCE_RADII[home]
    if distance is not None and distance <= radius:
        log(f"COMBO SUPPRESSED — dog still at home ({distance}m inside {radius}m)")
        continue

    fire_departure_event(...)
```

The guard catches residual cases the tracker reseed doesn't cover: e.g., the
upstream API flickers during normal operation and produces a transition while
the physical state hasn't changed.

## Verification

After the fix:
1. Manually trigger the paused phase to completion (e.g., simulate a walk
   return). Observe logs for `Reseeded trackers after pause` on the next poll.
2. Confirm no phantom event fires.
3. Check that a real new transition (some time later) still triggers
   correctly — the guard must not suppress legitimate events.

If a phantom was already recorded historically, clean up:
- Remove/rename the ghost entry's output artifact (route file, report, etc.)
- Restore any state file that got overwritten by the phantom (keep a backup)
- Leave immutable audit logs (JSONL event streams) alone — they correctly
  record what the code did

## Example (the motivating bug)

Dog walk listener in an OpenClaw dotfiles repo:

**Symptom**: Dashboard showed a 7:37 AM 45min walk immediately followed by
an 8:22 AM 3min walk. The two were actually one walk + a phantom.

**Timeline (UTC)**:
- `12:22:05` — Walk 1 finalized via Fi GPS return signal
- `12:22:11` — Phantom departure fires: `COMBO TRIGGER — activity Rest→Walk + base disconnected`
- `12:24:50` — "Return" detected 2.6min later (dog was home the whole time)

**Why**: For the full 45min walk, the departure poll loop's `last_connection`
and `last_activity` variables were frozen at `"Base"` and `"Rest"` (pre-walk
defaults), because the loop was doing `if _return_monitor_active: continue`
before fetching Fi. The instant return monitoring cleared, the next poll saw
Fi still reporting `activity=Walk` / `connection=User` (lag), compared to the
stale `"Rest"` / `"Base"` trackers, and fired.

**Fix**: Reseed trackers on return-monitor exit + require dog actually outside
the geofence (30m Crosstown / 75m Cabin) before accepting combo triggers.
See commit `6cf761a` in Dbochman/dotfiles.

## Investigation Technique

When you see a duplicate event with a suspiciously short gap:

1. Pull the event log / history file for the day.
2. Find the finalize-event timestamp and the start-of-duplicate timestamp.
   Compute the gap.
3. **If the gap is suspiciously short** (seconds to tens of seconds for an
   event that shouldn't recur that fast), it's almost certainly a detection
   bug, not two real events.
4. Grep the component's log for those two timestamps and read what happened
   in between.
5. Look specifically for: "transition detected" / "state change detected"
   / "trigger fired" messages immediately after a "ended" / "cleared" /
   "finalized" message.
6. Find the tracker variables (`last_X`) in source. Check whether the loop
   updates them unconditionally or only on a code path that may be skipped
   during a busy phase.

## Notes

- This pattern is **not** the same as `polling-loop-finalization-escape`
  (which fixes loops that fail to exit after a legit trigger). Here the
  loop exits fine — it re-enters and re-fires on the next cycle.
- The bug tends to hide during testing because test runs rarely exercise
  the "legitimate event immediately follows pause exit" timing window.
- If the "paused phase" is itself a separate coroutine/task (not inline in
  the loop), the module-level flag pattern is necessary — you can't pass
  a nonlocal into an unrelated coroutine.
- Consider adding a log line that records tracker reseed events. Makes this
  class of bug much easier to spot next time.
- Physical-state sanity guards are cheap and composable. When adding a new
  transition-triggered action, always ask: "what physical condition
  implicitly must be true for this trigger to be real?" and assert it
  before acting.

## Related Patterns

- State-machine trackers that span multiple coroutines: consider a single
  shared observer object rather than ad-hoc module flags.
- Upstream API lag after a known state change: prefer explicit "observation
  taken at timestamp T" metadata over implicit "whatever value it last
  returned" tracking.
- Duplicate event suppression via cooldowns: an alternative mitigation if
  reseed + guard aren't applicable. Rejects any event within N seconds of
  the last finalized one for the same subject.
