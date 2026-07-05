---
name: crosstown-roomba
description: Control the iRobot Roombas at Crosstown (Boston — Crosstown residence). Two robots available. Use when asked to vacuum, mop, start/stop/dock the Roomba at Crosstown, clean the house, or anything about the Roombas at Crosstown. NOT for Cabin Roombas (use roomba skill for Floomba/Philly).
allowed-tools: Bash(crosstown-roomba:*)
metadata: {"openclaw":{"emoji":"🤖","requires":{"bins":["crosstown-roomba"]}}}
---

# Crosstown Roomba Control (Boston)

Control two iRobot Roombas at **Crosstown (Boston — Crosstown residence)** via local MQTT through the MacBook Pro.

## Robots

| Name | Aliases | Model | Notes |
|------|---------|-------|-------|
| **roomba** | 10max, combo, max | Roomba Combo 10 Max | Vacuums + mops, has dock with auto-empty and tank |
| **scoomba** | j5 | Roomba J5 | Vacuum only |

Use `all` to target both robots.

Robot names and aliases are exact (case-insensitive). Never guess from a partial
name: if a requested name is not listed above, ask the user to clarify instead
of substituting a robot.

## Commands

### Check status (battery, mission, bin)
```bash
crosstown-roomba status           # both robots
crosstown-roomba status roomba    # just 10 Max
crosstown-roomba status scoomba   # just J5
```

### Start cleaning
```bash
crosstown-roomba start all        # start both
crosstown-roomba start roomba     # just 10 Max
```

### Stop / Pause / Resume
```bash
crosstown-roomba stop roomba
crosstown-roomba pause scoomba
crosstown-roomba resume scoomba
```

### Return to dock
```bash
crosstown-roomba dock all
```
The `dock` command automatically sends `stop` first, then `dock`. This is necessary because iRobot's MQTT `dock` command alone does not interrupt an active cleaning cycle — the robot ignores it while in `run` phase.

Before docking, the CLI must successfully read mission status. If the robot is
already charging (`cleanMissionStatus.phase == "charge"`), it safely skips that
robot. Otherwise it requires both the pre-dock `stop` command and stop-state
verification to succeed before sending `dock`; a failed stop is never ignored.
With `dock all`, the other robot is still processed normally.

### Locate (play sound)
```bash
crosstown-roomba find roomba
```

### WiFi info
```bash
crosstown-roomba wifi roomba
```

### Full robot state (JSON)
```bash
crosstown-roomba state roomba     # requires specific robot name
```

### List robots
```bash
crosstown-roomba list
```

## Results and verification

Action commands (`start`, `stop`, `pause`, `resume`, `dock`, and `find`) print
one JSON summary with a `results` entry for every targeted robot. Check both the
process exit status and each result's `ok`, `verification`, `phase`, and `error`
fields. When targeting `all`, the CLI attempts both robots and exits nonzero if
either one fails; a successful first robot never hides a failed second robot.

The CLI polls mission status a bounded number of times after stateful actions:

- `start` / `resume` must reach `run`
- `pause` must reach `pause`
- `stop` must reach `stop` or `charge`
- `dock` must report a return-to-dock phase or `charge`
- `find` has no persistent mission state, so only its command response is checked

A timeout or mismatched phase is a failure even when MQTT initially accepted
the action. `dock` success confirms that return-to-dock began (or the robot is
already charging); it does not wait indefinitely for physical docking.

## Architecture

```
Roomba 10 Max ←─MQTT:8883─→ roomba-cmd.js (MacBook Pro) ←─SSH─→ crosstown-roomba CLI (Mac Mini)
Roomba J5     ←─MQTT:8883─→ roomba-cmd.js (MacBook Pro) ←─SSH─→      (same CLI)
```

- `roomba-cmd.js` on the MacBook Pro connects to the robot via dorita980 MQTT, runs the command, disconnects
- The CLI SSHs into the MacBook Pro for each command (connect-per-request, no persistent MQTT)
- Each command takes ~5-10s due to SSH + MQTT connect/disconnect
- SSH, invalid JSON, robot-reported errors, and verification failures all produce a nonzero CLI exit

## Dog Walk Mode

**IMPORTANT: During walk hours (8-10 AM, 11 AM-1 PM, 5-8 PM), ALWAYS use `dog-walk-start` instead of `crosstown-roomba start`.** This starts Roombas AND activates return monitoring (Fi GPS + WiFi + Ring motion), so they auto-dock when Dylan/Julia return. Without this, Roombas run until battery dies.

```bash
dog-walk-start crosstown   # Start Roombas + return monitoring
```

Use bare `crosstown-roomba start` ONLY for non-walk cleaning (cleaning day, routine, etc.) where you don't need auto-dock on return.

## Disambiguation

- "vacuum", "roomba", "clean" at **Crosstown** → this skill
- "crosstown roomba", "boston roomba", "house roomba" → this skill
- "cabin roomba", "floomba", "philly" → `roomba` skill (Google Assistant)
- If location is ambiguous, ask which location

## Skill Boundaries

This skill controls Roombas at **Crosstown only** (Combo 10 Max + J5 via MQTT through MacBook Pro).

For related tasks, switch to:
- **roomba**: Roomba control at the Cabin (Floomba + Philly via Google Assistant)
- **crosstown-routines**: Full Crosstown routines (away, welcome home, goodnight) that include Roomba start/dock alongside lights, thermostats, and audio
- **dog-walk**: Automated dog walk detection auto-starts/docks Roombas at Crosstown via Fi GPS departure
- **presence**: Check if anyone is home at Crosstown before starting Roombas
- Vacancy automation (`com.openclaw.vacancy-actions` LaunchAgent) also starts Roombas when Crosstown becomes `confirmed_vacant` — this is independent of the dog walk system

## Troubleshooting

### "Connection refused" or SSH timeout
MacBook Pro at Crosstown must be reachable via Tailscale:
```bash
ssh dylans-macbook-pro "echo ok"
```

### "Robot did not respond within 20s"
- Robot may be off WiFi or powered down
- Check WiFi: `crosstown-roomba wifi <name>`
- Ensure robot is on the 192.168.165.x network

### Action returns JSON with `"ok": false`

- Read the failed robot's `error.code`, `error.message`, and last observed `phase`
- With target `all`, inspect both entries; one robot may have succeeded
- Do not immediately retry `dock` if `pre_dock_stop_failed` or
  `pre_dock_stop_verification_failed` is reported; check status first

### Command takes too long
Each command needs ~5-10s for SSH + MQTT handshake. This is normal for the connect-per-request architecture.
