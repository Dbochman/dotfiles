# OpenClaw Logs

All service logs live at `~/.openclaw/logs/` on the Mac Mini (and MacBook Pro for crosstown presence). This directory survives reboots — previously logs were in `/tmp/` and lost on restart.

## Logging Model

Each service uses one of two patterns:

| Pattern | How it works | Plist `StandardOutPath` |
|---------|-------------|------------------------|
| **Script-owned** | Script has a `log()` function that appends to `$HOME/.openclaw/logs/<name>.log` | `/dev/null` |
| **Plist-owned** | Script writes to stdout, launchd captures it | `~/.openclaw/logs/<name>.log` |

## Active Logs

### Script-Owned (log() appends directly)

| Log file | Service | Description |
|----------|---------|-------------|
| `bb-watchdog.log` | `com.openclaw.bb-watchdog` | BlueBubbles health checks, restart actions |
| `bb-ingest-lag.log` | `com.openclaw.bb-watchdog` | Ingest lag metrics (written by watchdog) |
| `bb-lag-summary.log` | `com.openclaw.bb-lag-summary` | Daily lag summaries |
| `presence-detect.log` | `com.openclaw.presence-cabin` / `presence-crosstown` | Presence scan results and evaluations |
| `vacancy-actions.log` | `com.openclaw.vacancy-actions` | Vacancy trigger actions (lights, thermostat, 8sleep, Roombas) |

### Plist-Owned (stdout capture)

| Log file | Service | Description |
|----------|---------|-------------|
| `dog-walk-listener.log` | `ai.openclaw.dog-walk-listener` | Dog walk detection (Fi GPS departure, multi-signal return) |
| `cielo-refresh.log` | `com.openclaw.cielo-refresh` | Cielo AC token refresh via pinchtab |
| `gateway.log` | `ai.openclaw.gateway` | OpenClaw gateway stdout |
| `gateway.err.log` | `ai.openclaw.gateway` | OpenClaw gateway stderr |
| `nest-cron.log` | `ai.openclaw.nest-snapshot` | Nest thermostat snapshot cron |
| `nest-dashboard.log` / `.err.log` | `ai.openclaw.nest-dashboard` | Nest dashboard HTTP server |
| `usage-dashboard.log` / `.err.log` | `ai.openclaw.usage-dashboard` | Usage dashboard HTTP server |
| `usage-snapshot.log` / `.err.log` | `ai.openclaw.usage-snapshot` | Usage metrics snapshot |
| `financial-dashboard.log` / `.err.log` | `ai.openclaw.financial-dashboard` | Financial dashboard HTTP server |
| `dotfiles-pull.log` | `ai.openclaw.dotfiles-pull` | Daily dotfiles sync |
| `home-state-snapshot.log` | `ai.openclaw.home-state-snapshot` | Daily home state snapshot (cat weights, sleep, doorbell) |
| `poke-messages.log` | `com.openclaw.poke-messages` | AppleScript Messages.app keepalive |

## Log Rotation

| Log | Strategy | Trigger |
|-----|----------|---------|
| `gateway.log` / `gateway.err.log` | Truncate to last 1000 lines when >5MB | Gateway wrapper on startup/restart |
| `bb-watchdog.log` | Daily rotation, keep 7 days (`bb-watchdog.log.YYYY-MM-DD`) | Script checks on each run |
| `nest-cron.log` | Truncate to last 50 lines when >100KB | Nest snapshot plist inline bash |
| All others | No automatic rotation | Monitor size periodically |

To check log sizes: `ls -lhS ~/.openclaw/logs/*.log`

## Debugging Commands

```bash
# Live tail a service log
tail -f ~/.openclaw/logs/dog-walk-listener.log

# Check recent vacancy actions
tail -50 ~/.openclaw/logs/vacancy-actions.log

# Search for errors across all logs
grep -i error ~/.openclaw/logs/*.log | tail -20

# Check log sizes
ls -lhS ~/.openclaw/logs/*.log
```

## MacBook Pro

The MacBook Pro at Crosstown also writes to `~/.openclaw/logs/`:
- `presence-detect.log` — Crosstown LAN presence scan results

## Historical / Stale

These may exist from previous configurations and can be safely removed:
- `*.log.old` — pre-migration logs moved from `/tmp/`
- `weekly-upgrade.*` — weekly auto-upgrade was removed 2026-03-12
- `op-test-*` — 1Password testing artifacts
- `group-sync*` — iMessage group sync (superseded by `sync-imessage-groups`)
- `gateway-wrapper.log` — old gateway wrapper output
