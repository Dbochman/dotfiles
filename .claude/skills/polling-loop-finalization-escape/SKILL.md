---
name: polling-loop-finalization-escape
description: |
  Fix polling loops that repeat finalization actions instead of exiting after a trigger.
  Use when: (1) a return/completion signal is detected but the loop keeps running and
  re-triggering the same action, (2) "resilient" try/except blocks inside a polling
  loop cause the loop to continue after a finalization error, (3) repeated dock/stop/
  cleanup commands are sent when only one was expected. The pattern: inner finalization
  steps are individually try/excepted for resilience, but an outer except catches
  unexpected errors and doesn't exit, causing the loop to re-detect the trigger.
author: Claude Code
version: 1.0.0
date: 2026-04-05
---

# Polling Loop Finalization Escape

## Problem
A polling loop detects a trigger condition (e.g., "return home detected"), executes finalization steps (dock Roombas, send notification, update state), then should exit. But if any exception occurs during finalization that isn't caught by the inner try/except blocks, the outer loop's generic `except Exception` handler catches it, logs the error, and **continues the loop** — causing the trigger to be re-detected and finalization to re-execute on every poll cycle.

## Context / Trigger Conditions
- A polling loop with an `if trigger_condition: ... return` finalization block
- Individual finalization steps wrapped in their own try/except for resilience
- An outer `except Exception` around the entire poll iteration that logs but doesn't return
- Symptoms: the same action (dock, notify, cleanup) executes repeatedly at poll intervals
- Log pattern: alternating "trigger detected" and "Error: ..." messages in a loop

## Solution
Ensure the outer exception handler checks whether the trigger was already detected, and if so, exits instead of looping:

```python
# BEFORE (broken): exception during finalization causes loop to continue
while polling:
    try:
        if detect_trigger():
            try:
                finalize_step_1()
            except Exception:
                log("step 1 failed (non-fatal)")
            try:
                finalize_step_2()  # <-- unexpected error here
            except Exception:
                log("step 2 failed (non-fatal)")
            return  # never reached if step_2 throws something uncaught
    except Exception as e:
        log(f"Error: {e}")  # catches it, but doesn't exit!
    await sleep(interval)   # loops back and re-triggers

# AFTER (fixed): outer handler respects the trigger state
while polling:
    trigger = None
    try:
        trigger = detect_trigger()
        if trigger:
            # ... finalization steps ...
            return
    except Exception as e:
        log(f"Error: {e}")
        if trigger:
            log("Exiting despite error (trigger was already detected)")
            return  # don't loop back and re-trigger
    await sleep(interval)
```

## Verification
After fix, logs should show exactly one "trigger detected" + "docking" sequence, then the monitor ends. No repeated dock commands at 30s intervals.

## Notes
- This is especially dangerous when finalization has side effects (sending commands to devices, sending notifications) — each re-trigger executes all side effects again
- In the Roomba case, repeated stop+dock cycles interrupted the robots mid-dock-return, leaving them stopped in the middle of the floor
- The root cause exception may itself be subtle (e.g., Python 3.14 `UnboundLocalError` on a variable that *appears* unreachable) — fixing the exception alone isn't sufficient because other future exceptions could cause the same loop behavior
- Always set a flag/variable BEFORE entering the finalization block so the outer handler can check it
