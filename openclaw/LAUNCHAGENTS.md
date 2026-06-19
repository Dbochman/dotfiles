# LaunchAgents

Reference for all LaunchAgents across machines. Plist source files live in two locations:

- **OpenClaw agents** (`ai.openclaw.*` / `com.openclaw.*` prefix): [`openclaw/launchagents/`](launchagents/)
- **Personal agents** (`com.dylanbochman.*` prefix): [`launchagents/`](../launchagents/) at the dotfiles repo root

## Mac Mini — Long-Running Services (KeepAlive)

| Label | Program | Port | Description |
|-------|---------|------|-------------|
| `ai.openclaw.gateway` | `OpenClawGateway.app` wrapper | — | OpenClaw gateway (core agent runtime, cron scheduler, BB channel) |
| `ai.openclaw.nest-dashboard` | `nest-dashboard.py` | 8550 | Nest thermostat history dashboard |
| `ai.openclaw.usage-dashboard` | `usage-dashboard.py` | 8551 | Anthropic usage tracking dashboard |
| `ai.openclaw.dog-walk-dashboard` | `dog-walk-dashboard.py` | 8552 | Dog walk & Roomba dashboard (walk history, Fi GPS, return signals) |
| `ai.openclaw.financial-dashboard` | `serve_dashboard.py` | 8585 | Canonical financial dashboard and owner-aware forecast baseline source |
| `ai.openclaw.forecast-dashboard` | `serve_forecast_dashboard.py` | 8586 | Forecast dashboard and five-minute live projection snapshot |
| `ai.openclaw.forecast-crypto-sync` | `forecast-crypto-sync.py` | — | Daily cache-only crypto holdings refresh for Forecast |
| `ai.openclaw.forecast-ledger-capture` | `forecast-ledger-capture.py` | — | Daily aggregate observation capture after source refreshes |
| `ai.openclaw.dog-walk-listener` | `dog-walk-listener-wrapper.sh` | — | Dog walk automation (Fi GPS departure, Ring/WiFi/Fi return monitoring) |
| `com.openclaw.presence-receive` | `presence-receive.sh` | — | Receives Tailscale file pushes from Crosstown presence scans |

### Financial Dashboard LaunchAgents

The financial dashboard services are Mac Mini services, not laptop services. Before installing, bootstrapping, stopping, or starting these plists, confirm the target host:

```bash
ssh dylans-mac-mini 'hostname -s; whoami; echo HOME=$HOME'
```

Expected target context is the Mac Mini user `dbochman` with `HOME=/Users/dbochman`. Do not bootstrap `ai.openclaw.financial-dashboard`, `ai.openclaw.financial-dashboard-plaid-sync`, `ai.openclaw.forecast-dashboard`, `ai.openclaw.forecast-crypto-sync`, or `ai.openclaw.forecast-ledger-capture` on Dylan's laptop just because the dotfiles checkout is local there.

`ai.openclaw.financial-dashboard` depends on the Python environment at:

```bash
/Users/dbochman/repos/financial-dashboard/venv/bin/python3
```

The venv should be built from Homebrew Python, not the Command Line Tools Python shim. The current Mini baseline as of 2026-06-18 is Python 3.13.12 with OpenSSL 3.x and the dependencies from `~/repos/financial-dashboard/requirements.txt` installed. Verify it on the Mini with:

```bash
ssh dylans-mac-mini 'cd ~/repos/financial-dashboard && ./venv/bin/python3 -c "import sys, ssl, yaml, requests; print(sys.version.split()[0]); print(ssl.OPENSSL_VERSION); print(yaml.__version__); print(requests.__version__)"'
```

The paired forecast dashboard service runs from `~/repos/Financial Advisor` with `/usr/bin/python3` and reads the financial dashboard through `http://127.0.0.1:8585` first, with `http://dylans-mac-mini:8585` as a fallback.

The data flow is intentionally one-way:

```text
daily cache-only Plaid sync + income-source scan -> finance.db -> 8585 /api/forecast-baseline
                                      -> 8586 current snapshot (5 min cache)
                                      -> browser projection inputs

daily cache-only crypto holdings sync -> local non-secret holdings cache -> 8586 public-price valuation
                                                                       -> browser projection inputs

daily post-sync aggregate capture -> 8586 forecast ledger -> annual forecast/actual checkpoints
```

`8586` never reads `finance.db`, Plaid tokens, or the OpenClaw secrets cache directly. It promotes only the reconciled, owner-aware aggregate contract returned by `8585`.

That contract includes the broad portfolio allocation and a separately gated
U.S./international equity-geography map. If geography is incomplete, Forecast
must expose a review state rather than infer a country split or issue an equity
trade instruction.

`ai.openclaw.financial-dashboard-plaid-sync` runs daily at 7:15 AM local time. It uses the protected Plaid credential and Item-token caches directly, never invokes `op`, exits nonzero when any Item fails, and writes only result metadata to `~/.openclaw/financial-dashboard/plaid-sync-status.json`. Each successful run also refreshes local `INCOME_*` source candidates; candidates are not sync failures, but they keep the forecast baseline in `review` until resolved. `not running` is normal between scheduled executions.

`ai.openclaw.forecast-crypto-sync` runs daily at 7:25 AM local time, after Plaid. It uses a dedicated Python 3.11+ venv and protected local Coinbase/Etherscan credentials to refresh `~/.openclaw/forecast-dashboard/crypto-holdings.json`; the cache and status file are mode `0600`. It never invokes `op`, never writes credentials to the cache, and preserves the last known-good cache when a source fails. A local `~/.openclaw/forecast-dashboard/crypto-sync-config.json` may set `coinbase_enabled: false` while a mismatched Coinbase key is being replaced; the wallet refresh continues. The forecast server accepts a reviewed manual statement as owner coverage only when the manual entry explicitly sets `model_coverage: true`; a statement quantity next to an active source must also declare `replaces_source_id`, or `independent: true` for a separate asset. The separate local `household-manual-assets.json` is not scheduled because property and physical-asset values require explicit review; documented `gold`/`silver` gram holdings are live-valued by `8586` with public XAU/XAG prices and require no secret or `op` access.

`ai.openclaw.forecast-ledger-capture` runs daily at 7:35 AM local time after the source jobs. It calls only `http://127.0.0.1:8586/api/forecast-ledger/observations`, captures aggregate facts, retries short service outages, and writes result metadata to `~/.openclaw/forecast-dashboard/forecast-ledger-capture-status.json`. It never calls `op`, touches Plaid secrets, or copies raw account/transaction data. Identical same-day source facts are idempotent; changed facts are retained as immutable revisions in the local Forecast ledger.

### Dashboard Health Semantics

The `financial-dashboard` and `forecast-dashboard` agents are `KeepAlive` services and should report `running`. The three daily agents should normally report `not running` between their calendar triggers; use their last exit code and status files instead:

```text
~/.openclaw/financial-dashboard/plaid-sync-status.json
~/.openclaw/forecast-dashboard/crypto-sync-status.json
~/.openclaw/forecast-dashboard/forecast-ledger-capture-status.json
```

`launchctl kickstart -k` intentionally terminates and replaces a running `KeepAlive` process. A historical `Killed: 9` entry immediately after that operation is not a failure by itself; require the replacement process to return to `running` and its health endpoint to succeed. The Financial Dashboard writes normal HTTP access records to its stderr log, so `HTTP 200` lines in `financial-dashboard.err.log` are request records, not application errors. Assess older log lines against the installed plist, current status file, and current endpoint health before treating them as an incident.

Minimum post-change verification:

```bash
ssh dylans-mac-mini 'launchctl list | grep -E "ai.openclaw.(financial|forecast)-dashboard"'
ssh dylans-mac-mini 'launchctl print gui/$(id -u)/ai.openclaw.financial-dashboard-plaid-sync'
ssh dylans-mac-mini 'launchctl print gui/$(id -u)/ai.openclaw.forecast-crypto-sync'
ssh dylans-mac-mini 'launchctl print gui/$(id -u)/ai.openclaw.forecast-ledger-capture'
ssh dylans-mac-mini 'lsof -nP -iTCP:8585 -sTCP:LISTEN; lsof -nP -iTCP:8586 -sTCP:LISTEN'
curl -fsS -o /dev/null -w 'mortgage HTTP %{http_code}\n' http://dylans-mac-mini:8585/api/mortgage/summary
curl -fsS -o /dev/null -w 'forecast baseline HTTP %{http_code}\n' http://dylans-mac-mini:8585/api/forecast-baseline
curl -fsS -o /dev/null -w 'forecast health HTTP %{http_code}\n' http://dylans-mac-mini:8586/api/health
curl -fsS -o /dev/null -w 'forecast snapshot HTTP %{http_code}\n' http://dylans-mac-mini:8586/api/current-snapshot
curl -fsS -o /dev/null -w 'forecast crypto HTTP %{http_code}\n' http://dylans-mac-mini:8586/api/crypto/positions
curl -fsS -o /dev/null -w 'forecast household net worth HTTP %{http_code}\n' http://dylans-mac-mini:8586/api/household-net-worth
curl -fsS -o /dev/null -w 'forecast ledger summary HTTP %{http_code}\n' http://dylans-mac-mini:8586/api/forecast-ledger/summary
```

Restart `8585` before `8586` after a paired deployment, including an `8585`
holding-classification or allocation-policy change, so Forecast builds a fresh
snapshot instead of serving its prior five-minute cache:

```bash
ssh dylans-mac-mini 'launchctl kickstart -k "gui/$(id -u)/ai.openclaw.financial-dashboard"'
ssh dylans-mac-mini 'launchctl kickstart -k "gui/$(id -u)/ai.openclaw.forecast-dashboard"'
```

Payroll data may still be unavailable, but the linked Plaid sources should populate canonical recognized income, spending, net-worth, savings-rate, and forecast-baseline APIs after a successful daily sync. Source reconciliation, income review, and portfolio coverage must be ready before Forecast promotes a portfolio, crypto, or mortgage value into the model; observed net cash flow remains calibration context. See [FINANCIAL-DASHBOARD.md](FINANCIAL-DASHBOARD.md#income-source-quality) for the local review workflow.

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
| `ai.openclaw.boa-keepalive` | 5min | `scrape_mortgage.py --lender boa --keep-alive` | Temporary BoA browser-session durability experiment; verifies the live tab and atomically captures rotated cookies |
| `ai.openclaw.boa-browser-heartbeat` | 1min | `scrape_mortgage.py --lender boa --browser-heartbeat` | Temporary BoA UI heartbeat; sends no API traffic and dynamically accepts the two-minute session warning when present |

### BoA Session Durability Agents

These agents are a paired 24-48 hour experiment, not a credential-login system. They require an already-authenticated BoA tab in Pinchtab Chrome. The normal weekly scrape uses the cookie-replay fast path and only attaches to Chrome if those cookies are rejected.

- `ai.openclaw.boa-keepalive` runs every 5 minutes. It verifies the tab before and after a same-origin BoA API request, sends a trusted no-click mouse move, and atomically persists the current cookie jar at `~/.openclaw/.boa_cookies.json`; its logs contain metadata only.
- `ai.openclaw.boa-browser-heartbeat` runs every minute. It does not call a BoA account API. It sends browser-level activity and uses the accessibility tree to accept the two-minute inactivity-warning dialog's `OK` button when it appears.
- Neither agent logs cookie values, credentials, or account response bodies. A real browser-auth check is required because a successful API response alone is insufficient.

Source plists are:

```text
openclaw/launchagents/ai.openclaw.boa-keepalive.plist
openclaw/launchagents/ai.openclaw.boa-browser-heartbeat.plist
```

Check the installed jobs and force one run without changing their schedule:

```bash
ssh dylans-mac-mini 'launchctl print "gui/$(id -u)/ai.openclaw.boa-keepalive"'
ssh dylans-mac-mini 'launchctl print "gui/$(id -u)/ai.openclaw.boa-browser-heartbeat"'
ssh dylans-mac-mini 'launchctl kickstart -p "gui/$(id -u)/ai.openclaw.boa-keepalive"'
ssh dylans-mac-mini 'launchctl kickstart -p "gui/$(id -u)/ai.openclaw.boa-browser-heartbeat"'
```

Healthy status is `ok` for the keep-alive and `ok` or `warning_dismissed` for the heartbeat. Treat `cdp_unavailable`, `not_authenticated`, `api_rejected`, `tab_lost_auth`, `warning_unhandled`, or any other status as a failure to investigate. Preserve the existing cookie file and inspect the logs first:

```bash
ssh dylans-mac-mini 'tail -n 40 ~/Library/Logs/boa-keepalive.log'
ssh dylans-mac-mini 'tail -n 40 ~/Library/Logs/boa-browser-heartbeat.log'
ssh dylans-mac-mini 'cd ~/repos/financial-dashboard && ./venv/bin/python3 scrape_mortgage.py --lender boa --verify-auth'
```

Do not add quiet hours or jitter the cadence while the initial soak is being measured. Do not run `--re-auth` for BoA from a LaunchAgent or the weekly cron. If the live tab and cookie replay both expire, recover with one interactive login in the Pinchtab Chrome window, then run the normal BoA scrape to recapture cookies.

## Mac Mini — Calendar-Based (StartCalendarInterval)

| Label | Schedule | Program | Description |
|-------|----------|---------|-------------|
| `ai.openclaw.dotfiles-pull` | Daily 6:00 AM | `dotfiles-pull.command` | Pulls dotfiles repo, deploys skills/wrappers to Mini |
| `ai.openclaw.financial-dashboard-plaid-sync` | Daily 7:15 AM | `financial-dashboard-plaid-sync.py` | Cache-only production Plaid sync; no `op` invocation |
| `ai.openclaw.forecast-crypto-sync` | Daily 7:25 AM | `forecast-crypto-sync.py` | Cache-only Coinbase/Etherscan holdings sync for Forecast; no `op` invocation |
| `ai.openclaw.forecast-ledger-capture` | Daily 7:35 AM | `forecast-ledger-capture.py` | Aggregate post-sync Forecast observation; no `op` invocation |
| `ai.openclaw.home-state-snapshot` | Daily 9:00 AM | `home-state-wrapper.sh` | Daily home state snapshot (cat weights, sleep scores, doorbell battery) |
| `com.openclaw.bb-lag-summary` | Daily 8:05 AM | `bb-lag-summary.sh` | BlueBubbles message lag summary |
| `ai.openclaw.opentable-refresh` | Weekly Wed 4:00 AM | `opentable-refresh-token.sh` | Refreshes OpenTable CLI auth token (~14d TTL) via Pinchtab + GWS Gmail 2FA. Scheduled at 4 AM to avoid Pinchtab collision with 8/10 AM booking jobs. |

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
| `com.openclaw.presence-crosstown` | 15min | `presence-detect.sh crosstown` | Crosstown LAN presence scan (ARP), pushes to Mini via Tailscale. **Must NOT be loaded on Mini** — the script ARP-scans `192.168.165.x`, which the Mini isn't on, so the scan returns nothing and Tailscale `file cp` to self fails. A misplaced copy ran on Mini until 2026-05-10. |

## Local Mac (Dylan's MacBook)

| Label | Interval | Program | Description |
|-------|----------|---------|-------------|
| `ai.openclaw.ccusage-push` | 30min | `ccusage-push.sh` | Collects Claude Code token usage and pushes daily JSON to Mini |
| `com.dylanbochman.claude-session-backup` | Weekly Sun 5:00 AM | `bin/claude-session-backup` | Rsyncs `~/.claude/projects/` (335MB) to Mini at `~/Backups/claude-sessions/<hostname>/projects/` via Tailscale SSH. Preserves Claude session transcripts past the default ~30-day local retention. Stays on Tailscale (no cloud); namespaces by `hostname -s` so other machines can co-exist. |

## Disabled

| Label | File | Reason |
|-------|------|--------|
| `ai.openclaw.weekly-upgrade` | `.plist.disabled` | Weekly auto-upgrade removed 2026-03-12; upgrades now manual |
| `ai.openclaw.usage-token-push` | `usage-token-push.plist.disabled` | Replaced by `oauth-refresh` — was pushing OAuth cache from laptop keychain to Mini, fragile (required laptop open + keychain readable). Lingered as `.plist` on Mini for weeks firing self-loop SSH every 30 min and exit-255'ing; renamed to `.disabled` and bootout'd on 2026-05-10 post-Tahoe-26.4.1 reboot. |

## New LaunchAgent Checklist

Every new LaunchAgent script MUST follow these rules:

1. **Set `HOME` and `PATH`** in the plist `EnvironmentVariables` — LaunchAgents inherit a minimal environment (`/usr/bin:/bin:/usr/sbin:/sbin`)
2. **Set `OP_SERVICE_ACCOUNT_TOKEN`** in any script that calls `op` (directly or via a CLI that calls `op` internally like `opentable`, `resy`, `nest`). Without it, `op` tries the 1Password desktop app's Mach bootstrap service, which triggers a GUI permission popup on every run — impossible to approve without VNC. Use: `export OP_SERVICE_ACCOUNT_TOKEN=$(cat "$HOME/.openclaw/.env-token")`
3. **Source `.secrets-cache`** for environment secrets: `set -a && source ~/.openclaw/.secrets-cache && set +a`
4. **Use `IdentityAgent none`** for any SSH/scp to known hosts — the default 1Password SSH agent also triggers GUI popups under launchd
5. **Set `StandardOutPath` and `StandardErrorPath`** in the plist to `~/.openclaw/logs/`
6. **Track the plist** in `openclaw/launchagents/` in the dotfiles repo (deploy via scp)

## Notes

- **Plist source**: OpenClaw-prefix plists in `openclaw/launchagents/`; personal `com.dylanbochman.*` plists in top-level `launchagents/`.
- **Deployment**: Most plists are deployed via `scp` to `~/Library/LaunchAgents/` on the target machine. Only `ai.openclaw.gateway` is symlinked via `install.sh`. Personal plists are typically copied to `~/Library/LaunchAgents/` once and registered via `launchctl bootstrap gui/$(id -u) <plist>`.
- **Logs**: Most services log to `~/.openclaw/logs/` or `/tmp/`. Check `StandardErrorPath`/`StandardOutPath` in plists.
- **Gateway wrapper**: Uses cache-only secrets pattern (`~/.openclaw/.secrets-cache`), no `op read` at startup (hangs under launchd).
- **OAuth refresh**: `oauth-refresh.sh` hides `/usr/bin` from PATH during `claude auth login` so that `security` (macOS keychain CLI) is not found. This forces Claude Code to write credentials to `~/.claude/.credentials.json` instead of the keychain, which is unreadable over SSH. The refresh token rotates on each login, so the flow is self-sustaining. If the refresh token chain breaks (e.g., manual `claude auth login` rotates it outside the script), re-seed by pushing a fresh token from a machine with keychain access: `security find-generic-password -s "Claude Code-credentials" -w | ssh dylans-mac-mini 'cat > ~/.openclaw/.anthropic-oauth-cache'`.
- **Pre-upgrade backup**: `ai.openclaw.gateway.plist.pre-upgrade` exists as safety backup — `npm install -g openclaw` may overwrite the plist via post-install hook.
- **Prefix convention**: Newer agents use `ai.openclaw.*`, older ones use `com.openclaw.*`. Both are functionally equivalent.
