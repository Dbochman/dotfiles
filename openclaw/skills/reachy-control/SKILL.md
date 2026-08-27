---
name: reachy-control
description: >-
  Inspect and control the physically secured Reachy Mini at Crosstown through
  ClawBody. Use for requests to check Reachy, look around, express an emotion,
  play any official emotion or dance preset, speak proactively, mute or unmute
  its microphone, stop movement, describe what its camera sees, or capture and
  send an ephemeral camera still.
allowed-tools: Bash(reachyctl:*), message
metadata: {"openclaw":{"emoji":"🤖","requires":{"bins":["reachyctl"]}}}
---

# Reachy control

Control the Crosstown Reachy Mini through ClawBody's owner-only local socket.
Commands from the Mac mini relay through the always-on Crosstown MBP; commands
on the MBP connect directly with its dedicated Reachy SSH identity.

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
reachyctl mute
reachyctl unmute
reachyctl stop
reachyctl idle
```

Use the camera:

```bash
reachyctl see
reachyctl capture
reachyctl cleanup '<cleanupToken>'
```

`see` returns a concise visual description. To send the actual current image:

1. Run `reachyctl capture` and accept only `mediaPath` and `cleanupToken` from
   its success JSON.
2. Send the JPEG through the current authorized route with
   `message(action="send", message="<brief caption>", media="<mediaPath>")`.
   An authenticated owner may explicitly request another admitted owner route;
   follow the normal channel policy for that destination.
3. Treat cleanup as `finally`: after the send succeeds, fails, or times out,
   run `reachyctl cleanup '<cleanupToken>'` exactly once.
4. After a successful current-route send, return `NO_REPLY` so automatic
   delivery does not duplicate the caption.

The captured JPEG is a private temporary file on the machine running
`reachyctl`; never copy it to another path or retain it after the task.

`mute` sets Reachy's daemon-managed microphone volume to zero. `unmute` restores
the last nonzero volume remembered by the running ClawBody process, defaulting to
100 after a restart. Mute also holds a bowed quiet pose with folded antennas and
pauses face tracking. Once that pose settles, Reachy relaxes only the antenna
motors and waits for five stable samples: deliberately move both antennas by
about 14 degrees from that stable baseline for roughly 300 ms to unmute it
physically. Natural relaxation drift is ignored. Unmute returns Reachy to
neutral and restores subtle face tracking. Use `reachyctl status` rather than
the dashboard slider as the authoritative combined state; `reachyctl unmute`
from another trusted OpenClaw channel remains the remote recovery path.

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
  camera, movement, emotion, dance, microphone, and proactive speech commands
  without competing with an ordinary voice turn.

## Safety

- Follow OpenClaw's channel trust and action-authorization rules for remote
  callers; the robot's physical security does not authorize an untrusted sender.
- Execute a single clearly requested movement without extra confirmation. Ask
  before repeated movements, sustained routines, or scheduled physical actions.
- Use `see` only when the user asks what Reachy sees or when visual context is
  necessary for their request. Use `capture` when the task needs the image
  itself. Do not retain or forward camera images or descriptions beyond that
  task; an authenticated owner's explicit request to send the still is part of
  the task and is allowed.
- Use only the enumerated command arguments. Do not SSH to Reachy directly or
  bypass `reachyctl` for robot control.

## Architecture

```text
OpenClaw -> reachyctl (Mac mini) -> dedicated SSH -> clawbody-control
         -> owner-only Unix socket -> ClawBody local tools -> Reachy
         <- one JPEG over the same SSH route <- private ephemeral media
```

If `reachyctl status` reports that control is unavailable, verify ClawBody is
running and the persistent Reachy gateway tunnel is healthy. Do not reboot the
robot automatically.
