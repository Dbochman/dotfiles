# OpenClaw LaunchAgents

Reference for all LaunchAgents on the Mac Mini (`~/Library/LaunchAgents/`).

## Long-Running Services (KeepAlive)

| Label | Program | Port | Description |
|-------|---------|------|-------------|
| `ai.openclaw.gateway` | `OpenClawGateway.app` wrapper | — | OpenClaw gateway (core agent runtime, cron scheduler, BB channel) |
| `ai.openclaw.nest-dashboard` | `nest-dashboard.py` | 8550 | Nest thermostat history dashboard |
| `ai.openclaw.usage-dashboard` | `usage-dashboard.py` | 8551 | Anthropic usage tracking dashboard |
| `ai.openclaw.financial-dashboard` | `serve_dashboard.py` | — | Financial dashboard |
| `ai.openclaw.ring-listener` | `ring-listener-wrapper.sh` | — | Ring doorbell FCM push listener (motion/doorbell events) |
| `com.openclaw.presence-receive` | `presence-receive.sh` | — | Receives Tailscale file pushes from Crosstown presence scans |

## Interval-Based (StartInterval)

| Label | Interval | Program | Description |
|-------|----------|---------|-------------|
| `com.openclaw.bb-watchdog` | 60s | `bb-watchdog.sh` | Monitors BlueBubbles health, cross-checks gateway BB plugin activity |
| `com.openclaw.poke-messages` | 60s | `poke-messages.scpt` | AppleScript to keep Messages.app responsive for BB Private API |
| `com.openclaw.presence-cabin` | 15min | `presence-detect.sh cabin` | Cabin network presence scan (ARP/mDNS) |
| `ai.openclaw.usage-snapshot` | 15min | `usage-snapshot.sh` | Snapshots Anthropic API usage to JSONL history |
| `ai.openclaw.nest-snapshot` | 30min | Inline bash | Nest thermostat snapshot to JSONL (shows `-` PID — normal, runs and exits) |
| `com.openclaw.cielo-refresh` | 30min | `cielo-refresh.sh` | Refreshes Cielo AC API token |

## Calendar-Based (StartCalendarInterval)

| Label | Schedule | Program | Description |
|-------|----------|---------|-------------|
| `ai.openclaw.dotfiles-pull` | Daily 6:00 AM | `dotfiles-pull.command` | Pulls dotfiles repo, deploys skills/wrappers to Mini |
| `ai.openclaw.home-state-snapshot` | Daily 9:00 AM | `home-state-wrapper.sh` | Daily home state snapshot (cat weights, sleep scores, doorbell battery) |
| `com.openclaw.bb-lag-summary` | Daily 8:05 AM | `bb-lag-summary.sh` | BlueBubbles message lag summary |

## Event-Driven (WatchPaths)

| Label | Watches | Program | Description |
|-------|---------|---------|-------------|
| `com.openclaw.vacancy-actions` | `~/.openclaw/presence/state.json` | `vacancy-actions.sh` | On vacancy: lights off, thermostat eco, Cielos off, Eight Sleep off, Roombas start. On return: Eight Sleep restored. See [VACANCY-AUTOMATION.md](VACANCY-AUTOMATION.md) |

## Run-Once (RunAtLoad only)

| Label | Program | Description |
|-------|---------|-------------|
| `com.openclaw.bt-connect` | `/bin/ln -sf bt_op.command` | Creates symlink for Bluetooth operations script |

## Disabled

| Label | File | Reason |
|-------|------|--------|
| `ai.openclaw.weekly-upgrade` | `.plist.disabled` | Weekly auto-upgrade removed 2026-03-12; upgrades now manual |

## Notes

- **Logs**: Most services log to `~/.openclaw/logs/` or `/tmp/`. Check `StandardErrorPath`/`StandardOutPath` in plists.
- **Gateway wrapper**: Uses cache-only secrets pattern (`~/.openclaw/.secrets-cache`), no `op read` at startup (hangs under launchd).
- **Pre-upgrade backup**: `ai.openclaw.gateway.plist.pre-upgrade` exists as safety backup — `npm install -g openclaw` may overwrite the plist via post-install hook.
- **Prefix convention**: Newer agents use `ai.openclaw.*`, older ones use `com.openclaw.*`. Both are functionally equivalent.
