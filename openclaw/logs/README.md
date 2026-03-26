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
| `ring-listener.log` | `ai.openclaw.ring-listener` | Ring doorbell events, vision analysis, departure detection |
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

- `bb-watchdog.log` — script rotates daily, keeps 7 days (rotated files: `bb-watchdog.log.YYYY-MM-DD`)
- `nest-cron.log` — truncated by the snapshot plist when over 100KB
- Other logs — no automatic rotation. Monitor size periodically.

## Debugging Commands

```bash
# Live tail a service log
tail -f ~/.openclaw/logs/ring-listener.log

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
