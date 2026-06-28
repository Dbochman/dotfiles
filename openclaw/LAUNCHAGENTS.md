# LaunchAgents

Reference for all LaunchAgents across machines. Plist source files live in two locations:

- **OpenClaw agents** (`ai.openclaw.*` / `com.openclaw.*` prefix): [`openclaw/launchagents/`](launchagents/)
- **Personal agents** (`com.dylanbochman.*` prefix): [`launchagents/`](../launchagents/) at the dotfiles repo root

## Mac Mini — Long-Running Services (KeepAlive)

| Label | Program | Port | Description |
|-------|---------|------|-------------|
| `ai.openclaw.gateway` | `OpenClawGateway.app` wrapper | — | OpenClaw gateway (core agent runtime, SQLite cron scheduler, native `imsg` channel) |
| `ai.openclaw.nest-dashboard` | `nest-dashboard.py` | 8550 | Nest thermostat history dashboard |
| `ai.openclaw.usage-dashboard` | `usage-dashboard.py` | 8551 | AI usage, gateway, and native iMessage health dashboard |
| `ai.openclaw.dog-walk-dashboard` | `dog-walk-dashboard.py` | 8552 | Dog walk dashboard (walk history, Fi GPS, maps, presence, and return signals) |
| `ai.openclaw.roomba-dashboard` | `roomba-dashboard.py` | 8553 | Roomba state and command dashboard |
| `ai.openclaw.home-dashboard` | `home-dashboard.py` | 8558 | Home Control Plane dashboard and provider status API |
| `ai.openclaw.financial-dashboard` | `serve_dashboard.py` | 8585 | Canonical financial dashboard and owner-aware forecast baseline source |
| `ai.openclaw.forecast-dashboard` | `serve_forecast_dashboard.py` | 8586 | Forecast dashboard and five-minute live projection snapshot |
| `ai.openclaw.dog-walk-listener` | `dog-walk-listener-wrapper.sh` | — | Dog walk automation (Fi GPS departure, Ring/WiFi/Fi return monitoring) |

### Financial Dashboard LaunchAgents

The financial dashboard services are Mac Mini services, not laptop services. Before installing, bootstrapping, stopping, or starting these plists, confirm the target host:

```bash
ssh dylans-mac-mini 'hostname -s; whoami; echo HOME=$HOME'
```

Expected target context is the Mac Mini user `dbochman` with `HOME=/Users/dbochman`. Do not bootstrap `ai.openclaw.financial-dashboard`, `ai.openclaw.finance-refresh`, `ai.openclaw.forecast-dashboard`, or `ai.openclaw.forecast-ledger-capture` on Dylan's laptop just because the dotfiles checkout is local there.

`ai.openclaw.financial-dashboard` depends on the Python environment at:

```bash
/Users/dbochman/repos/financial-dashboard/venv/bin/python3
```

The venv should be built from the currently installed Homebrew Python, not the Command Line Tools Python shim. The Mini baseline as of 2026-06-27 is Python 3.14.3 with OpenSSL 3.x and the dependencies from `~/repos/financial-dashboard/requirements.txt` installed. A Homebrew minor-version removal can leave `venv/bin/python3` as a broken symlink even while an already-running dashboard appears healthy; rebuild the venv before restarting the service when that happens. Verify it on the Mini with:

```bash
ssh dylans-mac-mini 'cd ~/repos/financial-dashboard && ./venv/bin/python3 -c "import sys, ssl, yaml, requests; from playwright.sync_api import sync_playwright; print(sys.version.split()[0]); print(ssl.OPENSSL_VERSION); print(yaml.__version__); print(requests.__version__)"'
```

The paired forecast dashboard service runs from `~/repos/Financial Advisor` with `/usr/bin/python3` and reads the financial dashboard through `http://127.0.0.1:8585` first, with `http://dylans-mac-mini:8585` as a fallback.

The data flow is intentionally one-way:

```text
daily 06:15 finance refresh -> Plaid sync + income-source scan -> finance.db -> 8585 /api/forecast-baseline
                           -> crypto holdings sync -> local non-secret cache -> 8586 public-price valuation
                           -> 8586 current snapshot (5 min cache) -> browser projection inputs

daily post-sync aggregate capture -> 8586 forecast ledger -> annual forecast/actual checkpoints
```

`8586` never reads `finance.db`, Plaid tokens, or the OpenClaw secrets cache directly. It promotes only the reconciled, owner-aware aggregate contract returned by `8585`.
Version 7 adds `implementation_holdings`: a safe, account-free instrument aggregate that exactly reconciles to the deployable depository and taxable-brokerage allocation and geography rows. Forecast can show it above implementation candidates only when it is `ok`; country-level instructions still use `equity_geography_by_location` and remain withheld when reconciliation or direct-position treatment is incomplete.

That contract includes the broad portfolio allocation and a separately gated
U.S./international equity-geography map. If geography is incomplete, Forecast
must expose a review state rather than infer a country split or issue an equity
trade instruction.

`ai.openclaw.finance-refresh` runs daily at 6:15 AM local time and invokes the existing cache-only source wrappers sequentially: Plaid first, then crypto. Each component gets one retry, retains its own lock and protected status file, and contributes only operational metadata to the combined mode-`0600` status at `~/.openclaw/finance-refresh/status.json`. The LaunchAgent never invokes `op`; a partial source failure does not prevent the other source from refreshing, but the combined job exits nonzero so monitoring sees the degradation.

The Plaid component uses the protected credential and Item-token caches directly, exits nonzero when any Item fails, and writes `~/.openclaw/financial-dashboard/plaid-sync-status.json`. Each successful run also refreshes local `INCOME_*` source candidates; candidates are not sync failures, but they keep the forecast baseline in `review` until resolved. The crypto component uses a dedicated Python 3.11+ venv and protected local Coinbase/Etherscan credentials to refresh `~/.openclaw/forecast-dashboard/crypto-holdings.json`; its status file and cache are mode `0600`. It preserves the last known-good cache when a source fails. A local `~/.openclaw/forecast-dashboard/crypto-sync-config.json` may set `coinbase_enabled: false` while a mismatched Coinbase key is being replaced; the wallet refresh continues. Property values are not part of this daily LaunchAgent: the weekly mortgage imports refresh authorized Redfin estimates. The separate local `household-manual-assets.json` provides property fallbacks and physical-asset inventory; an overdue property-fallback `review_after_days` date makes household net worth partial only when Redfin is unavailable. Documented `gold`/`silver` gram holdings are live-valued by `8586` with public XAU/XAG prices and require no secret or `op` access.

`ai.openclaw.forecast-ledger-capture` runs daily at 7:35 AM local time after the source jobs. It calls only `http://127.0.0.1:8586/api/forecast-ledger/observations`, captures aggregate facts, retries short service outages, and writes result metadata to `~/.openclaw/forecast-dashboard/forecast-ledger-capture-status.json`. It never calls `op`, touches Plaid secrets, or copies raw account/transaction data. Identical same-day source facts are idempotent; changed facts are retained as immutable revisions in the local Forecast ledger.

### Dashboard Health Semantics

The `financial-dashboard` and `forecast-dashboard` agents are `KeepAlive` services and should report `running`. The two daily agents should normally report `not running` between their calendar triggers; use their last exit code and status files instead:

```text
~/.openclaw/finance-refresh/status.json
~/.openclaw/financial-dashboard/plaid-sync-status.json
~/.openclaw/forecast-dashboard/crypto-sync-status.json
~/.openclaw/forecast-dashboard/forecast-ledger-capture-status.json
```

`launchctl kickstart -k` intentionally terminates and replaces a running `KeepAlive` process. A historical `Killed: 9` entry immediately after that operation is not a failure by itself; require the replacement process to return to `running` and its health endpoint to succeed. The Financial Dashboard writes normal HTTP access records to its stderr log, so `HTTP 200` lines in `financial-dashboard.err.log` are request records, not application errors. Assess older log lines against the installed plist, current status file, and current endpoint health before treating them as an incident.

Minimum post-change verification:

```bash
ssh dylans-mac-mini 'launchctl list | grep -E "ai.openclaw.(financial|forecast)-dashboard"'
ssh dylans-mac-mini 'launchctl print gui/$(id -u)/ai.openclaw.finance-refresh'
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
| `com.openclaw.presence-cabin` | 15min | `presence-detect.sh cabin` | Cabin network presence scan (Starlink gRPC) |
| `ai.openclaw.usage-snapshot` | 15min | `usage-snapshot.sh` | Snapshots Anthropic API usage to JSONL history |
| `ai.openclaw.nest-snapshot` | 30min | Inline bash | Nest thermostat snapshot to JSONL (shows `-` PID — normal, runs and exits) |
| `com.openclaw.cielo-refresh` | 30min | `cielo-refresh.sh` | Refreshes Cielo AC API token; browser fallback uses an isolated managed headless PinchTab instance |
| `ai.openclaw.oauth-refresh` | 6hr | `oauth-refresh.sh` | Self-contained Anthropic OAuth token refresh (uses `claude auth login` with refresh token, no keychain/laptop needed) |

### Headless Browser Policy

PinchTab defaults to headless mode on the Mini. Cielo fallback, OpenTable token
refresh and booking, and Star Market grocery automation acquire isolated tabs
through `~/.openclaw/bin/pinchtab-headless-instance`. OpenTable uses the
`opentable` profile, grocery uses `grocery`, and Cielo retains `default` for its
attended reauthentication path. The helper refuses to navigate a visible
PinchTab instance and stops only instances that it created.
The former viewing snooze is retired because scheduled browser work no longer
needs the display. A visible browser is allowed only for an explicit,
user-attended authentication flow such as Cielo reCAPTCHA recovery; see the
service skill for that recovery sequence.

### Retired BoA Session-Durability Agents

The paired five-minute keep-alive and one-minute UI heartbeat were tested on
2026-06-18. They reliably dismissed the browser inactivity warning, but BoA
still invalidated the server session after at least 10 hours and 13 minutes.
Both labels are booted out and persistently disabled on the Mini:

```bash
ssh dylans-mac-mini 'launchctl print-disabled "gui/$(id -u)" | grep -E "ai\.openclaw\.boa-(keepalive|browser-heartbeat)"'
```

Do not bootstrap or kickstart them during normal recovery. Their source plists
are retained for incident history only:

```text
openclaw/launchagents/ai.openclaw.boa-keepalive.plist
openclaw/launchagents/ai.openclaw.boa-browser-heartbeat.plist
```

The interval agents are not replaced by another LaunchAgent. The weekly
OpenClaw cron now invokes one guarded raw-CDP re-auth command only after cookie
replay fails and `--verify-auth` reports `not_authenticated`. It uses the
existing Pinchtab tab, stops on MFA or any challenge, and never invokes `op`
from a LaunchAgent. See `BOA-SESSION-DURABILITY-HANDOFF.md`.

## Mac Mini — Calendar-Based (StartCalendarInterval)

| Label | Schedule | Program | Description |
|-------|----------|---------|-------------|
| `ai.openclaw.dotfiles-pull` | Daily 6:00 AM | `dotfiles-pull.command` | Pulls dotfiles repo, deploys skills/wrappers to Mini |
| `ai.openclaw.8sleep-snapshot` | Daily 6:50 AM | `8sleep-snapshot.sh` | Pre-captures last-night summaries to `/tmp/8sleep-{dylan,julia}-latest.txt` before morning briefings |
| `ai.openclaw.finance-refresh` | Daily 6:15 AM | `finance-refresh.py` | Sequential cache-only Plaid and crypto refresh with retries and combined health; no `op` invocation |
| `ai.openclaw.forecast-ledger-capture` | Daily 7:35 AM | `forecast-ledger-capture.py` | Aggregate post-sync Forecast observation; no `op` invocation |
| `ai.openclaw.home-state-snapshot` | Daily 9:00 AM | `home-state-wrapper.sh` | Daily home state snapshot (cat weights, sleep scores, doorbell battery) |
| `ai.openclaw.opentable-refresh` | Weekly Wed 4:00 AM | `opentable-refresh-token.sh` | Refreshes the OpenTable CLI token in a managed headless PinchTab instance, with GWS email 2FA only as fallback. Uses the cache-only secret environment and does not touch visible browser tabs. |

## Mac Mini — Event-Driven (WatchPaths)

| Label | Watches | Program | Description |
|-------|---------|---------|-------------|
| `com.openclaw.presence-receive` | `~/Downloads` | `presence-receive.sh` | Validates the newest named Crosstown Taildrop file, atomically promotes it to presence state, and evaluates occupancy |
| `com.openclaw.vacancy-actions` | `~/.openclaw/presence/state.json` | `vacancy-actions.sh` | On vacancy: lights off, thermostat eco, Cielos off where applicable, lock Crosstown, and start Roombas. Independently reconciles each person's detected location with Eight Sleep `home`, which leaves their other Pod side away. See [VACANCY-AUTOMATION.md](VACANCY-AUTOMATION.md) |

## Mac Mini — Run-Once (RunAtLoad only)

| Label | Program | Description |
|-------|---------|-------------|
| `com.openclaw.bt-connect` | `/bin/ln -sf bt_op.command` | Creates symlink for Bluetooth operations script |

## MacBook Pro (Crosstown)

| Label | Interval | Program | Description |
|-------|----------|---------|-------------|
| `com.openclaw.presence-crosstown` | 15min | `presence-detect.sh crosstown` | Crosstown LAN presence scan (ARP), pushes to Mini via Tailscale. **Must NOT be loaded on Mini** — the script ARP-scans `192.168.165.x`, which the Mini isn't on, so the scan returns nothing and Tailscale `file cp` to self fails. A misplaced copy ran on Mini until 2026-05-10. |

The Crosstown source plist is deployed as a regular file on the MacBook rather
than shared live with the Mini checkout. After changing it, copy the source,
bootstrap or kickstart it on the MacBook, and compare the deployed file with the
source. A single nonzero run immediately after login can be a transient ARP
warm-up failure; investigate repeated failures rather than treating one sample
as a persistent outage.

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
| `ai.openclaw.ring-listener` | `ring-listener.plist.disabled` | Retired listener retained only as a disabled tombstone; current Ring state is collected through the home-control paths. |
| `ai.openclaw.boa-keepalive` | `ai.openclaw.boa-keepalive.plist` | Disabled 2026-06-18: UI heartbeat did not prevent BoA's server-side session cutoff. |
| `ai.openclaw.boa-browser-heartbeat` | `ai.openclaw.boa-browser-heartbeat.plist` | Disabled 2026-06-18 with the keep-alive experiment; source retained for history only. |

## New LaunchAgent Checklist

Every new LaunchAgent script MUST follow these rules:

1. **Set `HOME` and `PATH`** in the plist `EnvironmentVariables` — LaunchAgents inherit a minimal environment (`/usr/bin:/bin:/usr/sbin:/sbin`)
2. **Set `OP_SERVICE_ACCOUNT_TOKEN`** in any script that calls `op` (directly or via a CLI that calls `op` internally like `opentable`, `resy`, `nest`). Without it, `op` tries the 1Password desktop app's Mach bootstrap service, which triggers a GUI permission popup on every run — impossible to approve without VNC. Use: `export OP_SERVICE_ACCOUNT_TOKEN=$(cat "$HOME/.openclaw/.env-token")`
3. **Source `.secrets-cache`** for environment secrets: `set -a && source ~/.openclaw/.secrets-cache && set +a`
4. **Use `IdentityAgent none`** for any SSH/scp to known hosts — the default 1Password SSH agent also triggers GUI popups under launchd
5. **Choose one log owner**: plist-owned services write stdout/stderr to `~/.openclaw/logs/`; scripts with their own bounded `log()` function send plist stdout/stderr to `/dev/null` so third-party CLI noise cannot create an unbounded duplicate log
6. **Track the plist** in `openclaw/launchagents/` in the dotfiles repo (deploy via scp)

## Notes

- **Plist source**: OpenClaw-prefix plists in `openclaw/launchagents/`; personal `com.dylanbochman.*` plists in top-level `launchagents/`.
- **Deployment**: Most plists are deployed as regular files via `scp` to `~/Library/LaunchAgents/` on the target machine. `install.sh` can initially symlink the gateway plist, but `openclaw gateway install` may replace it with a generated regular plist that invokes `~/.openclaw/service-env/ai.openclaw.gateway-env-wrapper.sh`. The Mini currently uses that generated contract, so do not assume the live gateway plist is a symlink or byte-identical to the recovery source. Verify its `ProgramArguments`, wrapper, environment file, and loaded job after upgrades. Personal plists are typically copied once and registered with `launchctl bootstrap gui/$(id -u) <plist>`.
- **Logs**: Most services log to `~/.openclaw/logs/` or `/tmp/`. The current generated gateway job writes to `~/Library/Logs/openclaw/gateway.log`; the tracked recovery plist still names `~/.openclaw/logs/gateway.{log,err.log}`. Check the live plist's `StandardErrorPath` and `StandardOutPath` instead of assuming either layout.
- **Gateway wrapper**: Uses cache-only secrets pattern (`~/.openclaw/.secrets-cache`), no `op read` at startup (hangs under launchd).
- **Anthropic OAuth refresh**: `oauth-refresh.sh` hides `/usr/bin` from PATH during `claude auth login` so that `security` (macOS keychain CLI) is not found. This forces Claude Code to write credentials to `~/.claude/.credentials.json` instead of the keychain, which is unreadable over SSH. The refresh token rotates on each login, so the flow is self-sustaining. If the refresh token chain breaks (e.g., manual `claude auth login` rotates it outside the script), re-seed by pushing a fresh token from a machine with keychain access: `security find-generic-password -s "Claude Code-credentials" -w | ssh dylans-mac-mini 'cat > ~/.openclaw/.anthropic-oauth-cache'`.
- **Anthropic OAuth verification**: after enabling or repairing `ai.openclaw.oauth-refresh`, require exit 0, mode `0600` on `~/.openclaw/.anthropic-oauth-cache`, and non-null `utilization` from `http://127.0.0.1:8551/api/current`.
- **OpenAI authentication**: `ai.openclaw.oauth-refresh` manages Anthropic only. OpenClaw `2026.6.10` uses the canonical `openai` provider and stores its usable OAuth/token profiles in `~/.openclaw/agents/main/agent/openclaw-agent.sqlite`; the configured default is `openai/gpt-5.6-sol`. Repair an invalidated profile with `openclaw models auth login --provider openai`, then confirm the intended order with `openclaw models status --json` and `openclaw models auth order`. Source `~/.openclaw/.secrets-cache` and include Homebrew Node in `PATH` before running these commands over SSH.
- **OpenAI verification**: `openclaw models status --json` checks profile structure and routing but does not prove the provider still accepts the token. Require a live no-delivery smoke request and verify `agentMeta.provider` is `openai`, `agentMeta.model` is `gpt-5.6-sol`, and `executionTrace.fallbackUsed` is `false`:

  ```bash
  ssh dylans-mac-mini 'set -a; source ~/.openclaw/.secrets-cache; set +a; \
    PATH=/opt/homebrew/bin:/opt/homebrew/opt/node@22/bin:$PATH \
    openclaw agent --agent main --session-id "primary-auth-smoke-$(date +%s)" \
      --thinking minimal --timeout 60 --json \
      --message "Reply with exactly AUTH_OK and nothing else."'
  ```
- **Cielo verification**: a zero LaunchAgent exit is necessary but not sufficient. Refresh `http://127.0.0.1:8558/api/status?refresh=true` and require the `cielo` object to have no `error`.
- **Pre-upgrade backup**: `ai.openclaw.gateway.plist.pre-upgrade` exists as safety backup — `npm install -g openclaw` may overwrite the plist via post-install hook.
- **Prefix convention**: Newer agents use `ai.openclaw.*`, older ones use `com.openclaw.*`. Both are functionally equivalent.
