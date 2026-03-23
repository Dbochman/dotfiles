---
name: litter-robot
description: Control the Litter-Robot 4 at Crosstown (West Roxbury). Use when asked about the litter box, cat litter, waste level, cleaning the litter box, cat weight, Sopaipilla, Burrito, or anything about the Litter-Robot. NOT for Petlibro feeder/fountain (use petlibro skill for those).
allowed-tools: Bash(litter-robot:*)
metadata: {"openclaw":{"emoji":"🐈","requires":{"bins":["litter-robot"]}}}
---

# Litter-Robot 4 Control (Crosstown)

Control the **Litter-Robot 4** at Crosstown via the Whisker cloud API (pylitterbot).

## Device

| Name | Model | Serial | Location |
|------|-------|--------|----------|
| Litter-Robot 4 | LR4 | LR4C293473 | Crosstown (West Roxbury) |

## Cats

| Name | Weight |
|------|--------|
| Sopaipilla | ~10.3 lbs |
| Burrito | ~11.2 lbs |

## Commands

### Check status
```bash
litter-robot status
```
Shows waste level, cycle status, online state, night light, cats and weights.

### Start cleaning cycle
```bash
litter-robot clean
```

### Activity history
```bash
litter-robot history        # last 10 entries
litter-robot history 25     # last 25 entries
```

### Cat info and weight tracking
```bash
litter-robot pets
```

### Night light
```bash
litter-robot nightlight on
litter-robot nightlight off
```

### Reset waste drawer gauge
```bash
litter-robot reset
```
Run after emptying the waste drawer to reset the fill level gauge.

## Status Values

| Status | Meaning |
|--------|---------|
| READY | Idle, ready for use |
| CLEAN_CYCLE | Currently cycling |
| CLEAN_CYCLE_COMPLETE | Just finished cycling |
| CAT_DETECTED | Cat is inside |
| PAUSED | Cycle paused (interrupted) |
| DRAWER_FULL | Waste drawer needs emptying |
| OFF / OFFLINE | Powered off or disconnected |

## Architecture

```
Litter-Robot 4 ←─cloud─→ Whisker API ←─HTTPS─→ pylitterbot (Mac Mini venv)
```

Cloud-only. Auth via AWS Cognito (email+password). Tokens auto-refresh.

## Troubleshooting

### "auth_failed"
Check `~/.config/litter-robot/config.yaml` on Mac Mini. Uses Whisker/Litter-Robot account credentials.

### Waste level stuck
Run `litter-robot reset` after physically emptying the drawer.

## Disambiguation

- "litter box", "litter robot", "waste level", "cat weight" → this skill
- "cat food", "feeder", "fountain", "water" → `petlibro` skill
