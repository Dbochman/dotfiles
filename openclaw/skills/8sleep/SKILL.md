---
name: 8sleep
description: Control the Eight Sleep Pods at Crosstown and the Cabin. Defaults to Crosstown; pass `--location cabin` for the Cabin Pod. Use when asked about bed temperature, sleep tracking, sleep score, Eight Sleep, Pod, bed cooling/heating, or anything about the smart bed. Supports both sides — Dylan (left) and Julia (right). NOT for room thermostat (use nest-thermostat for that).
allowed-tools: Bash(8sleep:*)
metadata: {"openclaw":{"emoji":"S","requires":{"bins":["8sleep"]}}}
---

# Eight Sleep Pod Control

Control both Eight Sleep Pods via the Eight Sleep cloud API.

## Locations

| Location | Pod | Device-ID env var |
|----------|-----|--------------------|
| **crosstown** (default) | Pod 3 (King) at Crosstown | `EIGHTSLEEP_CROSSTOWN_DEVICE_ID` |
| **cabin** | Pod 5 (King) at the Cabin | `EIGHTSLEEP_CABIN_DEVICE_ID` |

Pass `--location <name>` (or `-l <name>`) before any subcommand to target a
specific Pod. If unset, the CLI defaults to `crosstown`. Both device-ID env vars
must remain populated for deterministic multi-Pod routing.

Eight Sleep's per-side write endpoints are user-scoped, and household-set
selection is a semantic relocation rather than a neutral routing mechanism.
Use `home` with an explicit location to make that Pod current for one user; the
other Pod becomes away for that side. Temperature, power, and manual away
commands fail closed unless the requested Pod is already current.

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

### Turn off/on a side
```bash
8sleep off dylan          # turn off Dylan's side (stop thermal unit)
8sleep off julia          # turn off Julia's side
8sleep on dylan           # turn on Dylan's side (resume smart schedule)
8sleep on julia           # turn on Julia's side
```
`off` stops all heating/cooling immediately. `on` resumes the smart schedule.

### Away mode (extended absence)
```bash
8sleep away dylan start   # enable away mode for Dylan
8sleep away julia start   # enable away mode for Julia
8sleep away dylan end     # disable away on Dylan's already-current Pod
8sleep away julia end     # disable away on Julia's already-current Pod
```
Away mode marks the user as absent — the current Pod stops all thermal activity
for that side and adjusts sleep tracking accordingly. It does not relocate the
user between Pods; use `home` for that. Manual away is appropriate for vacations
or extended travel. For short absences, prefer `off`/`on` instead.

### Move a user's home Pod

```bash
8sleep --location crosstown home dylan
8sleep --location cabin home julia
```

`home` requires an explicit location. It selects that household set, clears
away mode there, verifies the side assignment moved, and leaves the selection
in place. The other Pod becomes away for that user. Vacancy automation uses
this command independently for Dylan and Julia, including when they are at
different houses.

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

```text
Pod 3 (Crosstown) ─┐
                   ├─ cloud API ─ 8sleep-api.py (Mac Mini)
Pod 5 (Cabin) ─────┘
```

Cloud-only — requires internet. No local network API. Auth via Dylan's account
and the shared household configuration covers both Pods and both sides.

## Identifying the requester

When Dylan or Julia asks about "my bed temperature" or "my sleep score":
- If the message is from **Dylan** → use `dylan`
- If the message is from **Julia** → use `julia`
- If ambiguous → ask which side

## IMPORTANT: Do NOT use `eightctl`

The `eightctl` Go CLI is **broken** — it sends the wrong `client_id` and uses macOS keyring (which fails headless). **Always use the `8sleep` command** (custom Python wrapper). Never `go install`, `brew install`, or otherwise install `eightctl`.

## Troubleshooting

### "Rate limited by Eight Sleep API"
Too many auth attempts. Wait 5-10 minutes. Token is cached with refresh token support — should rarely need full re-auth.

### "Invalid credentials"
Check the `8sleep` wrapper config on Mac Mini (`~/.openclaw/8sleep/` directory). Password was set via "Forgot Password" reset flow.

### "Could not parse response"
Eight Sleep API may have changed. Use `8sleep raw users/me` for raw response.
