# OpenClaw LaunchAgents

Reference for all LaunchAgents across machines. Plist source files live in [`openclaw/launchagents/`](launchagents/).

## Mac Mini — Long-Running Services (KeepAlive)

| Label | Program | Port | Description |
|-------|---------|------|-------------|
| `ai.openclaw.gateway` | `OpenClawGateway.app` wrapper | — | OpenClaw gateway (core agent runtime, cron scheduler, BB channel) |
| `ai.openclaw.nest-dashboard` | `nest-dashboard.py` | 8550 | Nest thermostat history dashboard |
| `ai.openclaw.usage-dashboard` | `usage-dashboard.py` | 8551 | Anthropic usage tracking dashboard |
| `ai.openclaw.financial-dashboard` | `serve_dashboard.py` | 8585 | Financial dashboard |
| `ai.openclaw.ring-listener` | `ring-listener-wrapper.sh` | — | Ring doorbell FCM push listener (motion/doorbell events) |
| `com.openclaw.presence-receive` | `presence-receive.sh` | — | Receives Tailscale file pushes from Crosstown presence scans |

## Mac Mini — Interval-Based (StartInterval)

| Label | Interval | Program | Description |
|-------|----------|---------|-------------|
| `com.openclaw.bb-watchdog` | 60s | `bb-watchdog.sh` | Monitors BlueBubbles health, cross-checks gateway BB plugin activity |
| `com.openclaw.poke-messages` | 60s | `poke-messages.scpt` | AppleScript to keep Messages.app responsive for BB Private API |
| `com.openclaw.presence-cabin` | 15min | `presence-detect.sh cabin` | Cabin network presence scan (Starlink gRPC) |
| `ai.openclaw.usage-snapshot` | 15min | `usage-snapshot.sh` | Snapshots Anthropic API usage to JSONL history |
| `ai.openclaw.8sleep-snapshot` | 15min | `8sleep-snapshot.sh` | Eight Sleep status snapshot to JSONL history |
| `ai.openclaw.nest-snapshot` | 30min | Inline bash | Nest thermostat snapshot to JSONL (shows `-` PID — normal, runs and exits) |
| `com.openclaw.cielo-refresh` | 30min | `cielo-refresh.sh` | Refreshes Cielo AC API token |
| `ai.openclaw.oauth-refresh` | 6hr | `oauth-refresh.sh` | Self-contained Anthropic OAuth token refresh (uses `claude auth login` with refresh token, no keychain/laptop needed) |

## Mac Mini — Calendar-Based (StartCalendarInterval)

| Label | Schedule | Program | Description |
|-------|----------|---------|-------------|
| `ai.openclaw.dotfiles-pull` | Daily 6:00 AM | `dotfiles-pull.command` | Pulls dotfiles repo, deploys skills/wrappers to Mini |
| `ai.openclaw.home-state-snapshot` | Daily 9:00 AM | `home-state-wrapper.sh` | Daily home state snapshot (cat weights, sleep scores, doorbell battery) |
| `com.openclaw.bb-lag-summary` | Daily 8:05 AM | `bb-lag-summary.sh` | BlueBubbles message lag summary |
| `com.openclaw.gas-scrape` | Daily | `gas-scrape-sync.sh` | Scrapes gas utility data for financial dashboard |
| `com.openclaw.water-scrape` | Daily | `water-scrape-sync.sh` | Scrapes water utility data for financial dashboard |

## Mac Mini — Event-Driven (WatchPaths)

| Label | Watches | Program | Description |
|-------|---------|---------|-------------|
| `com.openclaw.vacancy-actions` | `~/.openclaw/presence/state.json` | `vacancy-actions.sh` | On vacancy: lights off, thermostat eco, Cielos off, Eight Sleep off, Roombas start. On return: Eight Sleep restored. See [VACANCY-AUTOMATION.md](VACANCY-AUTOMATION.md) |

## Mac Mini — Run-Once (RunAtLoad only)

| Label | Program | Description |
|-------|---------|-------------|
| `com.openclaw.bt-connect` | `/bin/ln -sf bt_op.command` | Creates symlink for Bluetooth operations script |

## MacBook Pro (Crosstown)

| Label | Interval | Program | Description |
|-------|----------|---------|-------------|
| `com.openclaw.presence-crosstown` | 15min | `presence-detect.sh crosstown` | Crosstown LAN presence scan (ARP), pushes to Mini via Tailscale |

## Local Mac (Dylan's MacBook)

| Label | Interval | Program | Description |
|-------|----------|---------|-------------|
| `ai.openclaw.ccusage-push` | 30min | `ccusage-push.sh` | Collects Claude Code token usage and pushes daily JSON to Mini |

## Disabled

| Label | File | Reason |
|-------|------|--------|
| `ai.openclaw.weekly-upgrade` | `.plist.disabled` | Weekly auto-upgrade removed 2026-03-12; upgrades now manual |
| `ai.openclaw.usage-token-push` | `usage-token-push.plist` | Replaced by `oauth-refresh` — was pushing OAuth cache from laptop keychain to Mini, fragile (required laptop open + keychain readable) |

## Notes

- **Plist source**: All plist files tracked in `openclaw/launchagents/` in the dotfiles repo.
- **Deployment**: Most plists are deployed via `scp` to `~/Library/LaunchAgents/` on the target machine. Only `ai.openclaw.gateway` is symlinked via `install.sh`.
- **Logs**: Most services log to `~/.openclaw/logs/` or `/tmp/`. Check `StandardErrorPath`/`StandardOutPath` in plists.
- **Gateway wrapper**: Uses cache-only secrets pattern (`~/.openclaw/.secrets-cache`), no `op read` at startup (hangs under launchd).
- **Pre-upgrade backup**: `ai.openclaw.gateway.plist.pre-upgrade` exists as safety backup — `npm install -g openclaw` may overwrite the plist via post-install hook.
- **Prefix convention**: Newer agents use `ai.openclaw.*`, older ones use `com.openclaw.*`. Both are functionally equivalent.
