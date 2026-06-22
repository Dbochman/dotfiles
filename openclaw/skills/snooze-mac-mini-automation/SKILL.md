---
name: snooze-mac-mini-automation
description: Temporarily pause, time, check, extend, or restore the Mac Mini's browser-based BoA and Cielo LaunchAgents. Use when Dylan or Julia asks to "snooze for X hours/minutes," prevent authentication windows, browser automation, or pop-ups while watching a show, movie, or other full-screen media on the Mac Mini, make a timed snooze indefinite, check its deadline, or resume automation afterward. NOT for stopping dashboards, OpenClaw, media playback, or unrelated background services.
allowed-tools: Bash(launchagent-snooze:*)
metadata: {"openclaw":{"emoji":"S","requires":{"bins":["launchagent-snooze"]}}}
---

# Snooze Mac Mini Automation

Use the stateful CLI for every operation. Do not issue ad hoc `launchctl` commands.

## Commands

Pause browser-facing automation before viewing:

```bash
launchagent-snooze pause
```

Pause and automatically restore after a duration:

```bash
launchagent-snooze pause --for 2h
launchagent-snooze snooze 90m
launchagent-snooze snooze 1h30m
```

Translate natural-language durations to `m`, `h`, or `hNm`. A new timed command moves the
deadline without changing the original restore set. To cancel the deadline while keeping the
snooze active:

```bash
launchagent-snooze pause --manual
```

Check whether the snooze remains active:

```bash
launchagent-snooze status
```

Restore only the jobs that were loaded before the snooze:

```bash
launchagent-snooze resume
```

Do not resume until the user explicitly says viewing is finished. Restoring Cielo bootstraps
its `RunAtLoad` LaunchAgent, so browser automation may start immediately.

## Managed Jobs

| Label | Expected normal state | Snooze behavior |
|---|---|---|
| `ai.openclaw.boa-keepalive` | Retired and disabled | Leave disabled unless it was genuinely loaded |
| `ai.openclaw.boa-browser-heartbeat` | Retired and disabled | Leave disabled unless it was genuinely loaded |
| `com.openclaw.cielo-refresh` | Loaded on a 30-minute interval | Disable and unload, then restore after viewing |

The CLI records the original loaded set and optional automatic-resume deadline in
`~/.openclaw/state/mac-mini-automation-snooze.tsv`. Repeated `pause` calls preserve that
original set. Timed snoozes use the transient
`com.openclaw.launchagent-snooze-resume` user LaunchAgent and coalesce after system sleep. A
failed restore retains the state file and keeps the failed job disabled.

## Verification

After `pause`, require `snooze: active` and `unloaded, disabled` for every managed job. For a
timed snooze, also require `automatic resume: <timestamp> (scheduled)`.
After `resume`, require `snooze: inactive`; Cielo should report `loaded, enabled`, while the
retired BoA jobs should remain `unloaded, disabled` unless their prior state was different.
