---
name: snooze-mac-mini-automation
description: Temporarily pause, time, inspect, and restore browser-based BoA and Cielo LaunchAgents on the household Mac Mini without re-enabling jobs that were already disabled. Use when Dylan or Julia asks to snooze automation for a number of minutes or hours, is about to watch a show, use full-screen media, present, or otherwise needs the Mac Mini free from browser and authentication pop-ups, and when checking, extending, making indefinite, or ending an existing viewing snooze.
---

# Snooze Mac Mini Automation

Use the bundled wrapper around OpenClaw's stateful CLI rather than issuing ad hoc `launchctl`
commands:

```bash
scripts/launchagent-snooze.sh pause
scripts/launchagent-snooze.sh pause --for 2h
scripts/launchagent-snooze.sh snooze 1h30m
scripts/launchagent-snooze.sh pause --manual
scripts/launchagent-snooze.sh status
scripts/launchagent-snooze.sh resume
```

The wrapper connects through the `mac-mini` SSH alias. Set `MAC_MINI_HOST` only when a
different configured SSH alias is required.

## Workflow

1. Run `pause` before viewing starts, or `pause --for <duration>` when the user supplies a
   deadline. Translate natural durations such as "two hours" to `2h` and "90 minutes" to
   `90m`.
2. Confirm every candidate reports `unloaded` and the snooze reports `active`.
3. Use another timed `pause` to move the automatic-resume deadline. Use `pause --manual` to
   cancel a deadline while keeping the snooze active.
4. Run `status` when asked whether the interruption guard remains active.
5. Run `resume` only after viewing ends. Warn that restoring Cielo may immediately run its
   `RunAtLoad` refresh and briefly open browser automation.

`pause` records only jobs that were loaded when the snooze began, disables their user-domain
overrides, and unloads them. Repeated `pause` calls preserve the original state. Timed snoozes
use a transient user LaunchAgent so they survive agent sessions and resume after system sleep.
`resume` re-enables and bootstraps only the recorded jobs. The retired BoA jobs remain disabled
unless they were genuinely loaded before the snooze.

Do not delete plist files, kill unrelated browser processes, or enable jobs absent from the
recorded state. Report SSH failures without changing local state.
