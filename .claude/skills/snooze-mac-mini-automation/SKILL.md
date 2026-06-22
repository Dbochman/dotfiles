---
name: snooze-mac-mini-automation
description: Temporarily pause, inspect, and restore browser-based BoA and Cielo LaunchAgents on the household Mac Mini without re-enabling jobs that were already disabled. Use when Dylan or Julia is about to watch a show, use full-screen media, present, or otherwise needs the Mac Mini free from browser and authentication pop-ups, and when checking or ending an existing viewing snooze.
---

# Snooze Mac Mini Automation

Use the bundled stateful helper rather than issuing ad hoc `launchctl` commands:

```bash
scripts/launchagent-snooze.sh pause
scripts/launchagent-snooze.sh status
scripts/launchagent-snooze.sh resume
```

The helper connects through the `mac-mini` SSH alias. Set `MAC_MINI_HOST` only when a
different configured SSH alias is required.

## Workflow

1. Run `pause` before viewing starts.
2. Confirm every candidate reports `unloaded` and the snooze reports `active`.
3. Run `status` when asked whether the interruption guard remains active.
4. Run `resume` only after viewing ends. Warn that restoring Cielo may immediately run its
   `RunAtLoad` refresh and briefly open browser automation.

`pause` records only jobs that were loaded when the snooze began, disables their user-domain
overrides, and unloads them. Repeated `pause` calls preserve the original state. `resume`
re-enables and bootstraps only the recorded jobs. The retired BoA jobs remain disabled unless
they were genuinely loaded before the snooze.

Do not delete plist files, kill unrelated browser processes, or enable jobs absent from the
recorded state. Report SSH failures without changing local state.
