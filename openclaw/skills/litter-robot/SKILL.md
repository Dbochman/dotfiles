---
name: litter-robot
description: Read and control the exact Litter-Robot at Cabin or Crosstown through protected Whisker bindings. Use for litter-box status, waste or litter levels, cleaning cycles, activity history, night lights, robot resets, and cat weight history. Do not use for Petlibro feeders or fountains.
allowed-tools: Bash(litter-robot:*)
metadata: {"openclaw":{"emoji":"🐈","requires":{"bins":["litter-robot"]}}}
---

# Litter-Robot control

Use the guarded `litter-robot` CLI. It resolves protected device serials from
`~/.config/litter-robot/bindings.json`; never discover or target a robot by
account order, fuzzy name, or a serial supplied in chat.

## Exact aliases

- `crosstown-litter-robot`
- `cabin-litter-robot`

## Read commands

```bash
litter-robot status
litter-robot status cabin-litter-robot
litter-robot --json overview
litter-robot pets
litter-robot history crosstown-litter-robot
litter-robot history cabin-litter-robot 25
```

Use `--json` before the command when structured output is needed. `status`
returns both enrolled robots by default and never emits protected identifiers.
`overview` adds recent per-robot activity and recent cat weights for dashboards.

## Control commands

Run a control only when the user asks for that action and the exact house is
known. Ask which house when it is ambiguous.

```bash
litter-robot clean cabin-litter-robot
litter-robot nightlight crosstown-litter-robot on
litter-robot reset cabin-litter-robot
```

`reset` is a remote robot reset that clears errors and may trigger a cycle. It
does not reset the LR4 waste gauge. Never retry a physical action automatically
when the result says its outcome is unknown.

## Status interpretation

- `READY`: idle and ready.
- `CLEAN_CYCLE`: currently cycling.
- `CAT_DETECTED`: a cat is inside.
- `PAUSED`: a cycle was interrupted.
- `DRAWER_FULL`: empty the waste drawer.
- `OFF`, `OFFLINE`, or `NOT_FOUND`: unavailable; do not issue controls.

## Architecture and recovery

```text
Litter-Robot 4 ← cloud → Whisker API ← HTTPS → pylitterbot on Mac mini
```

The locked runtime is `~/.openclaw/venvs/litter-robot`. Credentials and tokens
remain owner-only under `~/.config/litter-robot`. If bindings are missing or a
device is replaced, use the attended `litter-robot-enroll` operator helper;
OpenClaw should not enroll devices autonomously.
