---
name: 8sleep
description: Control the Eight Sleep Pod 3 at Crosstown (West Roxbury). Use when asked about bed temperature, sleep tracking, sleep score, Eight Sleep, Pod, bed cooling/heating, or anything about the smart bed. Supports both sides — Dylan (left) and Julia (right). NOT for room thermostat (use nest-thermostat for that).
allowed-tools: Bash(8sleep:*)
metadata: {"openclaw":{"emoji":"S","requires":{"bins":["8sleep"]}}}
---

# Eight Sleep Pod Control (Crosstown)

Control the **Eight Sleep Pod 3** (King) at Crosstown via the Eight Sleep cloud API.

## Pod Sides

| Side | User | Position |
|------|------|----------|
| **dylan** | Dylan | Left |
| **julia** | Julia | Right |

All temperature and sleep commands require specifying the side: `dylan` or `julia`.

## Commands

### Check status (both sides at once)
```bash
8sleep status
```
Shows current temperature level, heating/cooling state, and water status for both sides.

### Set temperature for a side
```bash
8sleep temp dylan -30     # cool Dylan's side (~64F)
8sleep temp julia 20      # warm Julia's side (~87F)
8sleep temp dylan 0       # neutral (~81F)
```

### Temperature scale
| Level | Temp | Feeling |
|-------|------|---------|
| -100 | ~55F | Very cold |
| -50 | ~70F | Cool |
| -30 | ~64F | Slightly cool |
| 0 | ~81F | Neutral |
| +30 | ~87F | Warm |
| +50 | ~97F | Very warm |
| +100 | ~111F | Very hot |

Dylan prefers cool temperatures. Julia prefers warm.

### Sleep data
```bash
8sleep sleep dylan              # Dylan's last night
8sleep sleep julia              # Julia's last night
8sleep sleep dylan 2026-03-21   # specific date
```

### Device info
```bash
8sleep device
```
Shows model, serial, water level, connectivity, priming status.

## Architecture

```
Pod 3 ←─cloud─→ Eight Sleep API ←─HTTPS─→ 8sleep-api.py (Mac Mini)
```

Cloud-only — requires internet. No local network API. Auth via Dylan's account (household access covers both sides).

## Identifying the requester

When Dylan or Julia asks about "my bed temperature" or "my sleep score":
- If the message is from **Dylan** → use `dylan`
- If the message is from **Julia** → use `julia`
- If ambiguous → ask which side

## Troubleshooting

### "Rate limited by Eight Sleep API"
Too many auth attempts. Wait 5-10 minutes. Token is cached after first successful auth.

### "Invalid credentials"
Check `~/.config/eightctl/config.yaml` on Mac Mini. Password was set via "Forgot Password" reset flow.

### "Could not parse response"
Eight Sleep API may have changed. Use `8sleep raw users/me` for raw response.
