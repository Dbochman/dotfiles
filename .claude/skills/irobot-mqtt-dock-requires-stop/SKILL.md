---
name: irobot-mqtt-dock-requires-stop
description: |
  iRobot Roomba MQTT dock command is silently ignored during active cleaning (run phase).
  Use when: (1) dock commands return ok but robot keeps cleaning, (2) repeated dock calls
  have no effect, (3) dorita980 dock not working. Must send stop first, wait 2s, then dock.
author: Claude Code
version: 1.0.0
date: 2026-04-05
---

# iRobot MQTT: Dock Requires Stop First

## Problem
iRobot's local MQTT API (used by dorita980) silently ignores `dock` commands while the robot is in the active `run` phase. The command returns `{"ok":null}` (success) but the robot continues cleaning. This caused a dog walk return monitor to loop infinitely — detecting return, sending dock, robot ignoring it, detecting return again.

## Context / Trigger Conditions
- Roomba status shows `phase: "run"` and `operatingMode: 6` (active cleaning)
- `dock` commands via dorita980 return `{"ok":null}` but robot continues
- Repeated dock commands have no effect
- Only affects local MQTT control (dorita980) — Google Assistant handles stop+dock natively

## Solution
Always send `stop` before `dock`:
1. Send `stop` command
2. Wait 2 seconds (robot needs time to transition from `run` to `stop` phase)
3. Send `dock` command

In the crosstown-roomba wrapper:
```bash
if [[ "$action" == "dock" ]]; then
  run_one "$robot" "stop" >/dev/null 2>&1 || true
  sleep 2
fi
run_one "$robot" "$action"
```

## Verification
After stop+dock, check status — phase should transition from `run` → `stop` → `hmUsrDock` (returning to dock).

## Notes
- The `stop` command itself always works, even during active cleaning
- The 2s delay is important — sending dock immediately after stop can still be ignored
- This is a quirk of iRobot's local MQTT protocol, not a dorita980 bug
- Does NOT affect Cabin Roombas (Google Assistant handles semantics natively)
- Does NOT affect the iRobot mobile app (which presumably handles this internally)
- Even with stop+dock, robots may fail to reach the dock (stuck, lost navigation). Dog walk listener has a post-dock verification thread that checks after 3 minutes and retries up to 2x.
