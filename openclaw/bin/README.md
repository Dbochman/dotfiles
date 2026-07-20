# OpenClaw Bin Scripts

Helper scripts deployed to `~/.openclaw/bin/` (and `~/bin/`) on the Mac Mini. These support the OpenClaw gateway, dashboards, device integrations, and maintenance automation.

## Deployment

Scripts in this directory are tracked in the dotfiles repo and deployed to Mini via `dotfiles-pull.command` (daily LaunchAgent) or manually via `scp`:

```bash
scp openclaw/bin/<script> dylans-mac-mini:~/.openclaw/bin/<script>
```

Per-file `scp` is only for an isolated helper with no paired artifacts. Never
use it for the restaurant coordinator: that rollout includes wrappers, Python,
provider readers, copied skills, the owner-only scope registry, cron
definitions, and the gateway `PATH` contract. Use `dotfiles-pull.command` for
the ordered, mode-correct rollout and its live gateway/cron verification. The
initial installer can seed the coordinator files, copied skills, and protected
scope on a new host, but it neither reconciles live cron nor runs the same
active post-deploy checks.

## Scripts

### Gateway & Maintenance

| Script | Location on Mini | Description |
|--------|-----------------|-------------|
| `openclaw-refresh-secrets` | `~/bin/` | Attended exact-field refresh of `~/.openclaw/.secrets-cache` plus the optional complete dedicated finance cache; run with `--interactive` after key rotation, never from launchd/cron. |
| `openai-memory-key` | `~/.openclaw/bin/` | Mode-restricted exec secret-provider helper for memory search. Emits the existing `openai:default` token from the agent auth database; never log or call it for diagnostics. |
| `pinchtab-headless-instance` | `~/.openclaw/bin/` | Acquires, inventories, scopes, and releases managed headless PinchTab instances without navigating a visible browser. |
| `opentable-reservations` | `~/.openclaw/bin/` | Reads the authenticated OpenTable account's upcoming reservations through its dedicated managed profile. It requires an exact protected account/token binding plus affirmative full-list evidence, rejects pagination, virtualization, opaque cards, and document-level login overlays, and emits only normalized token-free facts without reservation IDs. |
| `opentable-refresh-token.sh` | `~/.openclaw/bin/` | Refreshes the OpenTable CLI token in a managed headless PinchTab instance. It matches the dashboard's hash-only account identity to the protected expected account, validates the exact token through a read-only API call, then writes token and attestation with atomic file replacements. A matching first-run session bootstraps without OTP; Gmail OTP is used only when browser identity cannot be proven. It never logs identity, fingerprints, or token material. |
| `restaurant-book` / `restaurant-book.py` | `~/.openclaw/bin/` | Runs a tracked standing-authorized dinner scope across both Resy and OpenTable with dual-account idempotency guards, deterministic selection, one total mutation boundary, and token-free receipts. The protected scope registry is deployed to `~/.openclaw/restaurant-bookings/scopes.json`. |
| `openclaw-weekly-report.py` | `~/.openclaw/bin/` | Generates the weekly cron report from durable session/cron records and live service checks; avoids obsolete transient gateway-log parsing. |
| `dylan-morning-briefing-data.py` | repo path (cron invokes it directly) | Collects a bounded seven-day Calendar agenda and 24-hour Gmail metadata for Dylan's briefing. Owns GWS account routing, token-race retry, local header filtering, and privacy-safe partial-failure output. |
| `julia-morning-briefing-data.py` | repo path (cron invokes it directly) | Collects Julia's validated same-day triage handoff, current Calendar, cached sleep, aggregate finances, and bounded post-triage unread metadata. Owns raw GWS account routing, pagination, retries, deadlines, and section-level failure handling. |
| `sag-wrapper` | `~/.openclaw/bin/` | Wraps `sag` (speech audio generator) with 1Password env injection for ElevenLabs API key. |
| `send-audio-briefing` | `~/.openclaw/bin/` | Generates TTS audio via ElevenLabs (`sag-wrapper`) and sends as an iMessage attachment via `imsg`, plus optional summary text. Used by Julia's morning briefing cron job. |
| `reachyctl` | `~/.openclaw/bin/` and `/opt/homebrew/bin/` | Sends constrained status, movement, proactive speech, microphone mute/unmute, and camera commands to ClawBody through the always-on Crosstown MBP relay. On that MBP it uses the dedicated Reachy SSH identity and owner-only Unix control socket directly. Face tracking is automatic while the user speaks. |

### Dashboards

| Script | Port | Description |
|--------|------|-------------|
| `nest-dashboard.py` | 8550 | Nest climate dashboard — Chart.js UI over JSONL history. Serves thermostat temperatures, humidity, weather, and presence data over the home LAN and Tailscale tailnet. |
| `usage-dashboard.py` | 8551 | OpenClaw usage dashboard — token consumption, utilization, agent activity, cron, and native iMessage health/response latency over the home LAN and Tailscale tailnet. |
| `dog-walk-dashboard.py` | 8552 | Dog walk history, Fi route maps, coverage/heatmaps, and return-signal telemetry over the home LAN and Tailscale tailnet. |
| `roomba-dashboard.py` | 8553 | Crosstown/Cabin Roomba status, command, snooze, and run-history dashboard. |
| `home-dashboard.py` | 8558 | Home Control Plane status and command dashboard across both locations. |
| `finance-refresh.py` | — | Daily 06:15 orchestrator that runs the cache-only Plaid and crypto wrappers sequentially, retries each once, and writes combined protected status without reading source credentials or data. |
| `weekly-financial-scrape.py` | — | Sunday 04:05 deterministic cache-only HTTP-first scraper orchestrator. Before credentials, browsers, or data work it reads the verified bounded canonical owner-only repo `.env` through one file descriptor and retains only `TESLA_EMAIL`, requires the repo child to emit the exact `FINANCE_SCRAPER_CONTRACT 2` line and exact compact seven-source capability manifest, validates provider modes, then validates the dedicated credential cache. It pins every normal merge to `--wrapper-contract 2`, assigns one run ID to every normal scraper and guarded import, and accepts a successful artifact only with one compact `FINANCE_SCRAPER_STATUS` object whose exact `contract`/`source`/`path` fields match the closed per-source allowlist. Missing, duplicate, malformed, mismatched, or unknown markers skip import; validated browser fallback may import but makes the final status degraded/nonzero. Exact provider-owned auth lines gate one scoped re-auth child for Eversource, National Grid, BWSC, or PennyMac; Tesla has no standard re-auth and BoA keeps its exact-profile raw-CDP state machine. Every child receives a closed runtime allowlist, every Python child has dotenv loading disabled, only Tesla receives its identity, and only one guarded re-auth child receives a selected credential pair. The helper never reads `.env-token` or invokes `op`; it captures aggregate child stdout/stderr only in memory under a 64 KiB ceiling, requires strict UTF-8, and rejects/discards invalid output before auth recovery or import. It always fully drains the complete child process group before returning from each child attempt, binds BoA to the acquired headless `finance` profile, and atomically writes safe owner-only final metadata to `~/.openclaw/financial-dashboard/weekly-scrape-status.json`. Every nonhealthy final status attempts one idempotent, strict, owner-only per-run alert handoff and records `alert_handoff` as persisted or failed; healthy runs create none. The exact-argv command cron propagates helper failure directly to its bounded job-level alert. `--preflight` performs the same value-free Tesla identity, contract, provider-mode, and credential checks without browser or data mutation. |
| `financial-scrape-alert-notifier.py` | — | Delivery-only consumer for `~/.openclaw/financial-dashboard/weekly-scrape-alerts/`. It validates owner/mode/schema/bounds, reads only an exact numeric Dylan chat assignment from a scoped value or one verified cache fd, and runs fixed `/opt/homebrew/bin/imsg` with bounded process-group capture. Records transition `pending` → `inflight` → `sent`; confirmed sent state precedes cleanup so delete failure cannot resend, while failed sends retain 15-minute-to-six-hour backoff. Invalid/orphan entries move to a private sibling quarantine, safe health is persisted separately, and the command cron propagates nonzero/timeout/output failure to a cooldown-bounded job alert. The remaining send-before-sent-state crash window is explicitly at-least-once. It has no scraper/import/browser entry point; `--canary` sends one fixed attended test message without touching the queue or financial work. |
| `financial-dashboard-plaid-sync.py` | — | Daily cache-only Plaid sync wrapper for the separate financial-dashboard repo. Reads protected local caches, never calls `op`, serializes runs with a lock, refreshes local income-source review candidates through `update_data.py sync`, and writes status-only metadata to `~/.openclaw/financial-dashboard/plaid-sync-status.json`. |
| `forecast-crypto-sync.py` | — | Cache-only Coinbase/Etherscan holdings component used by the unified finance refresh; preserves the last known-good holdings cache and writes protected component status. |
| `forecast-ledger-capture.py` | — | Daily post-sync Forecast wrapper. Calls only the loopback aggregate-observation endpoint, retries short service outages, and writes status-only metadata to `~/.openclaw/forecast-dashboard/forecast-ledger-capture-status.json`; never calls `op` or reads Plaid data directly. |

### Nest Integration

| Script | Description |
|--------|-------------|
| `nest` | CLI wrapper for Google Nest SDM API + Open-Meteo weather. Handles OAuth token refresh, protected exact-resource still/video capture, thermostat status, history recording, and dashboard management. |
| `nest-camera-snap.py` | Captures a JPEG frame or bounded H.264 MP4 from a Nest camera via WebRTC (SDM API). Uses `aiortc`, pins the working H.264 profile, and patches Nest's non-standard ICE candidates. |
| `nest-camera-image` | Creates exact-resource, owner-only, short-lived Nest request stills or 1-30 second videos for native OpenClaw delivery; validates JPEG/MP4 structure and exposes token-scoped cleanup without arbitrary paths. |

### Home Events

| Script | Description |
|--------|-------------|
| `home_event_bus.py` | Durable shadow-only household event journal: strict source validation, HMAC-minimized atomic spools, single-writer SQLite ingestion, retention, safe status, and bounded read queries. |
| `home-eventctl` | Operator-only wrapper for `init`, `check-config`, producer `enqueue`, `ingest-once`, `status`, and `prune`; producers send strict JSON on stdin. |
| `home-events` | Fixed-root, read-only JSON CLI exposed to the OpenClaw `home-events` skill for status, recent activity, incidents, and explanations. |
| `home-event-correlator.py` | Persistent shadow-only correlator that claims durable consumer rows, applies fail-closed canonical presence context, groups site incidents, and records rate-limited shadow decisions without delivery or camera work. |
| `home-event-service-wrapper.sh` | LaunchAgent boundary for ingestion, correlation, and August polling; sanitizes the environment and writes one bounded owner-only operational log. |
| `august-event-adapter.py` | Disabled-by-default read-only August transition poller. Uses the exact protected MBP observe binding, baselines silently, and publishes through `home-eventctl` without a mutation path. |

See [`../HOME-EVENTS.md`](../HOME-EVENTS.md) for the protected runtime,
producer contracts, shadow rollout, and rollback procedure. The subsystem does
not require an OpenClaw gateway restart.

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
| `dotfiles-pull.command` | Daily git pull and deployment on Mini. Stashes local changes, pulls `--ff-only`, restores the stash, copies skills/wrappers/scripts, publishes required skill wrappers such as `reachyctl` into Homebrew's PATH, atomically installs the protected restaurant scope registry, reconciles cron, activates a changed gateway wrapper, and verifies restaurant skills through the active gateway before succeeding. Auto-closes its Terminal window and runs via Terminal.app for git credential access. |

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
