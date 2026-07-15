---
name: reachy-control
description: >-
  Inspect and control the physically secured Reachy Mini at Crosstown through
  ClawBody. Use for requests to check Reachy, look around, express an emotion,
  play any official emotion or dance preset, speak proactively, stop movement,
  or describe what its camera sees.
allowed-tools: Bash(reachyctl:*)
metadata: {"openclaw":{"emoji":"🤖","requires":{"bins":["reachyctl"]}}}
---

# Reachy control

Control the Crosstown Reachy Mini through ClawBody's owner-only local socket and
the dedicated Mac-mini-to-Reachy SSH identity.

## Commands

Check availability before an action if the robot's current state is unknown:

```bash
reachyctl status
```

Move or express:

```bash
reachyctl look left|right|up|down|front
reachyctl presets
reachyctl emotion <preset>
reachyctl dance <preset>
reachyctl speak "Text to say aloud"
reachyctl stop
reachyctl idle
```

Use the camera:

```bash
reachyctl see
```

Treat JSON with `"status":"success"` as confirmation. Report an `error`
verbatim and do not claim the robot moved or saw something when the command
failed.

Read [references/presets.md](references/presets.md) when choosing a movement.
`emotion` plays the official recorded motion and its bundled vocalization when
present. `dance dance1|dance2|dance3` selects the three vocalized dance presets
from the emotion library. The official dance-library presets are motion-only;
the six legacy built-ins are also available. Use `reachyctl presets` if the live
catalog may be newer than the reference.

Face tracking is automatic: it strengthens while the in-person user is actively
speaking, then returns to its subtle idle blend as soon as speech stops. Do not
toggle tracking manually.

In the direct Reachy voice session (`agent:main:reachy`), do not call
`reachyctl speak`; ClawBody automatically vocalizes OpenClaw's final response.
Use `speak` for proactive announcements initiated by other OpenClaw sessions,
cron jobs, or explicit remote requests.

Every command automatically acquires ClawBody's exclusive OpenClaw control lease.
The direct Realtime voice pauses while the command runs, so it is safe to combine
camera, movement, emotion, dance, and proactive speech commands without competing
with an ordinary voice turn.

## Safety

- Follow OpenClaw's channel trust and action-authorization rules for remote
  callers; the robot's physical security does not authorize an untrusted sender.
- Execute a single clearly requested movement without extra confirmation. Ask
  before repeated movements, sustained routines, or scheduled physical actions.
- Use `see` only when the user asks what Reachy sees or when visual context is
  necessary for their request. Do not retain or forward camera descriptions
  beyond that task.
- Use only the enumerated command arguments. Do not SSH to Reachy directly or
  bypass `reachyctl` for robot control.

## Architecture

```text
OpenClaw -> reachyctl (Mac mini) -> dedicated SSH -> clawbody-control
         -> owner-only Unix socket -> ClawBody movement/camera tools -> Reachy
```

If `reachyctl status` reports that control is unavailable, verify ClawBody is
running and the persistent Reachy gateway tunnel is healthy. Do not reboot the
robot automatically.
