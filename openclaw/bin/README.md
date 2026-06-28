# OpenClaw Bin Scripts

Helper scripts deployed to `~/.openclaw/bin/` (and `~/bin/`) on the Mac Mini. These support the OpenClaw gateway, dashboards, device integrations, and maintenance automation.

## Deployment

Scripts in this directory are tracked in the dotfiles repo and deployed to Mini via `dotfiles-pull.command` (daily LaunchAgent) or manually via `scp`:

```bash
scp openclaw/bin/<script> dylans-mac-mini:~/.openclaw/bin/<script>
```

## Scripts

### Gateway & Maintenance

| Script | Location on Mini | Description |
|--------|-----------------|-------------|
| `openclaw-refresh-secrets` | `~/bin/` | Refreshes `~/.openclaw/.secrets-cache` from 1Password. Run over SSH after key rotation. |
| `openai-memory-key` | `~/.openclaw/bin/` | Mode-restricted exec secret-provider helper for memory search. Emits the existing `openai:default` token from the agent auth database; never log or call it for diagnostics. |
| `pinchtab-headless-instance` | `~/.openclaw/bin/` | Acquires, scopes, and releases managed headless PinchTab instances without navigating a visible browser. |
| `opentable-refresh-token.sh` | `~/.openclaw/bin/` | Refreshes and validates the OpenTable CLI token in a managed headless PinchTab instance; uses Gmail verification only when reauthentication is required and never logs token material. |
| `openclaw-weekly-report.py` | `~/.openclaw/bin/` | Generates the weekly cron report from durable session/cron records and live service checks; avoids obsolete transient gateway-log parsing. |
| `sag-wrapper` | `~/.openclaw/bin/` | Wraps `sag` (speech audio generator) with 1Password env injection for ElevenLabs API key. |
| `send-audio-briefing` | `~/.openclaw/bin/` | Generates TTS audio via ElevenLabs (`sag-wrapper`) and sends as an iMessage attachment via `imsg`, plus optional summary text. Used by Julia's morning briefing cron job. |

### Dashboards

| Script | Port | Description |
|--------|------|-------------|
| `nest-dashboard.py` | 8550 | Nest climate dashboard — Chart.js UI over JSONL history. Serves thermostat temperatures, humidity, weather, and presence data over the home LAN and Tailscale tailnet. |
| `usage-dashboard.py` | 8551 | OpenClaw usage dashboard — token consumption, utilization, agent activity, cron, and native iMessage health/response latency over the home LAN and Tailscale tailnet. |
| `dog-walk-dashboard.py` | 8552 | Dog walk history, Fi route maps, coverage/heatmaps, and return-signal telemetry over the home LAN and Tailscale tailnet. |
| `roomba-dashboard.py` | 8553 | Crosstown/Cabin Roomba status, command, snooze, and run-history dashboard. |
| `home-dashboard.py` | 8558 | Home Control Plane status and command dashboard across both locations. |
| `finance-refresh.py` | — | Daily 06:15 orchestrator that runs the cache-only Plaid and crypto wrappers sequentially, retries each once, and writes combined protected status without reading source credentials or data. |
| `weekly-financial-scrape.py` | — | Sunday 04:05 deterministic scraper orchestrator. Serializes whole runs with a protected lock, contains timed-out children in private process groups, captures child output privately, retries only recognized auth failures, scopes credentials to re-auth children, targets BoA's dedicated `finance` PinchTab profile, preserves safe raw-CDP recovery statuses, imports only current-run successes, and enforces mortgage run-ID freshness. |
| `financial-dashboard-plaid-sync.py` | — | Daily cache-only Plaid sync wrapper for the separate financial-dashboard repo. Reads protected local caches, never calls `op`, serializes runs with a lock, refreshes local income-source review candidates through `update_data.py sync`, and writes status-only metadata to `~/.openclaw/financial-dashboard/plaid-sync-status.json`. |
| `forecast-crypto-sync.py` | — | Cache-only Coinbase/Etherscan holdings component used by the unified finance refresh; preserves the last known-good holdings cache and writes protected component status. |
| `forecast-ledger-capture.py` | — | Daily post-sync Forecast wrapper. Calls only the loopback aggregate-observation endpoint, retries short service outages, and writes status-only metadata to `~/.openclaw/forecast-dashboard/forecast-ledger-capture-status.json`; never calls `op` or reads Plaid data directly. |

### Nest Integration

| Script | Description |
|--------|-------------|
| `nest` | CLI wrapper for Google Nest SDM API + Open-Meteo weather. Handles OAuth token refresh, 1Password credential caching, thermostat status, camera snapshots, history recording, and dashboard management. |
| `nest-camera-snap.py` | Captures a single JPEG frame from a Nest camera via WebRTC (SDM API). Uses `aiortc` for WebRTC peer connection. Patches Nest's non-standard ICE candidates. |

### Usage Metrics

| Script | Description |
|--------|-------------|
| `usage-snapshot.sh` | Collects OpenClaw usage metrics every 15 minutes via LaunchAgent. Fetches Anthropic utilization, reads runtime logs plus SQLite `cron_run_logs`, counts native iMessage rows, and writes 90-day JSONL history. |

### Bluetooth

| Script | Description |
|--------|-------------|
| `bt_run.sh` | Reads a command from `/tmp/bt_command.txt`, runs `bt_connect` with it, writes result to `/tmp/bt_result.txt`. Used as a GUI-context wrapper for Bluetooth operations. |

> **Note**: `bt_connect` is a compiled Mach-O arm64 binary (not tracked in git). It lives only on the Mini at `~/.openclaw/bin/bt_connect`.

### Dotfiles Sync

| Script | Description |
|--------|-------------|
| `dotfiles-pull.command` | Daily git pull of the dotfiles repo on Mini. Stashes local changes, pulls `--ff-only`, pops stash. Runs cron job sync after pull. Auto-closes Terminal window. Runs as a LaunchAgent via Terminal.app for git credential access. |

### Markdown Search (qmd)

| Script | Description |
|--------|-------------|
| `qmd-setup.sh` | One-time setup for `qmd` (Quick Markdown Search) on Mini. Indexes all OpenClaw markdown (workspace, skills, dotfiles) with BM25 + vector embeddings for hybrid search. |

**Package**: `@tobilu/qmd` (npm), installed at `/opt/homebrew/bin/qmd`

**Collections** (4 — deduplicated to avoid duplicate result slots):
| Name | Path | Contents |
|------|------|----------|
| `workspace` | `~/.openclaw/workspace/` | SOUL.md, TOOLS.md, HEARTBEAT.md |
| `skills` | `~/.openclaw/skills/` | All SKILL.md files |
| `plans` | `~/dotfiles/openclaw/plans/` | Current plans plus archived architecture and migration records |
| `bin-scripts` | `~/dotfiles/openclaw/bin/` | README.md |

**Usage**:
```bash
qmd query "how does native iMessage health work"  # hybrid search (recommended)
qmd search "cart URL"                         # BM25 keyword search
qmd update --pull                             # re-index after dotfiles pull
qmd mcp                                       # start MCP server for AI agents
```

### Device Monitoring

| Script | Description |
|--------|-------------|
| `mysa-status.py` | Queries Mysa baseboard heater API for device status (temp, setpoint, mode). Outputs JSON. Uses Cognito auth cached at `~/.config/mysotherm`; an optional cached `Mysa` vault credential renews expired sessions automatically, or run `mysa --login` interactively. |
