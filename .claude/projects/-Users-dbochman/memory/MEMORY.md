# MEMORY.md

## OpenClaw Setup (Updated 2026-06-27)

**Status:** Fully operational. Native `imsg` for iMessage; BlueBubbles fully retired. GWS for Google Workspace. Runtime secrets are cached (no 1Password calls at startup).

## Key Paths
- OpenClaw config: `~/.openclaw/openclaw.json`
- Gateway LaunchAgent: `~/Library/LaunchAgents/ai.openclaw.gateway.plist`
- FDA .app wrapper: `~/Applications/OpenClawGateway.app`
- Gateway wrapper: `~/Applications/OpenClawGateway.app/Contents/MacOS/OpenClawGateway`
- Current generated-service gateway log: `~/Library/Logs/openclaw/gateway.log` (the tracked recovery plist uses `~/.openclaw/logs/gateway.{log,err.log}`)
- Runtime logs: `/tmp/openclaw/openclaw-YYYY-MM-DD.log` (structured JSON)
- Canonical cron definitions: `~/dotfiles/openclaw/cron/jobs.json`
- Live cron definitions, runtime state, and history: `~/.openclaw/state/openclaw.sqlite` (`cron_jobs`, `cron_run_logs`)
- Secrets cache: `~/.openclaw/.secrets-cache` (chmod 600, KEY=VALUE format)
- Dotfiles repo: `~/dotfiles` (github.com/Dbochman/dotfiles, PUBLIC)
- Workspace repo: `~/.openclaw/workspace` (github.com/Dbochman/openclaw-workspace)

## Config Highlights
- Model: `openai/gpt-5.6-sol` with fast mode (first fallback: `openai/gpt-5.5`)
- Auth: canonical OpenAI OAuth/token profiles plus Anthropic token fallbacks in `~/.openclaw/agents/main/agent/openclaw-agent.sqlite`
- TTS: inbound mode via ElevenLabs
- Session: per-channel-peer isolation, daily reset at 4am
- iMessage: native bundled `imessage` channel via `/opt/homebrew/bin/imsg` JSON-RPC
- Stable targets: `chat_id:171` (Dylan), `chat_id:1` (Julia), `chat_id:170` (group)
- Gateway detects config changes, but channel-scoped runtime snapshots such as `session.typingMode` require an iMessage channel or gateway restart
- Gateway health monitor: enabled (`channelHealthCheckMinutes: 5`)
- Tailscale CLI: `/opt/homebrew/bin/tailscale` is the managed app-bundle wrapper (`TAILSCALE_BE_CLI=1`); the Homebrew formula stays unlinked so CLI and active macOS network-extension builds match
- Heartbeat interval: 12h with no routine transport action
- The native bridge supports typing and advanced actions; `typingMode: "instant"` is enabled while automatic read receipts remain disabled
- Reactions/tapbacks: native `message` tool `action: "react"` (love, like, dislike, laugh, emphasize, question)

## Secrets & Auth
- **Cache-only pattern** (recommended): Gateway wrapper sources `~/.openclaw/.secrets-cache` — never calls `op read` at startup (hangs under launchd, causes TCC popup storms on Tahoe)
- Refresh helper: `~/bin/openclaw-refresh-secrets` (run over SSH after key rotation)
- 1Password service account token: `~/.openclaw/.env-token`
- **CRITICAL:** `op read` hangs indefinitely under launchd — use cache files exclusively

## Native iMessage
- OpenClaw `2026.6.10` uses the bundled `imessage` plugin and `imsg 0.11.1`.
- `imsg status --json` reports basic, advanced, and v2 readiness with bridge version 2.
- SIP remains disabled and library validation relaxed for advanced native actions.
- Cron deliveries and direct notifications use stable `chat_id:*` targets.
- BlueBubbles app, plugin, services, state, credentials, watchdogs, and local caches were removed on 2026-06-27.

## Cron Jobs
- Manual trigger: `openclaw cron run <job-id> --timeout 300000 --expect-final`
- **Do NOT use `openclaw agent --deliver`** — spawns independent async agents with no dedup
- **CRITICAL:** Remove jobs through `openclaw cron rm` and the canonical repo definition; preserve SQLite `cron_run_logs` as history/tombstones
- Current inventory: `openclaw cron list --all --json` (SQLite-backed)
- Julia morning briefing: `gws-julia-morning-briefing-0001` at 7AM ET
- Weekly activity/security/health report: `weekly-report-0001` at 3 PM Sun ET

## npm Upgrade Hazards
- `npm install -g openclaw` may run `openclaw install --service` which overwrites the LaunchAgent plist
- Weekly auto-upgrade is retired; manual upgrades must back up and verify the generated plist/service environment
- After upgrade: check `~/.openclaw/devices/paired.json` for missing scopes
- Missing scopes cause `gateway closed (1008): pairing required` on cron delivery

## GWS (Google Workspace CLI) — sole Google CLI (GOG retired 2026-03-05)
- CLI: `/opt/homebrew/bin/gws` (npm `@googleworkspace/cli` v0.4.4)
- Accounts: dylanbochman (default), julia.joy.jennings, bochmanspam, clawdbotbochman
- Config: `~/.config/gws/` (AES-256-GCM encrypted credentials)
- **DANGER: `gws auth logout` without `--account` flag NUKES ALL accounts**
- Auth requires browser (OAuth) — auth locally, then scp credentials to Mini

## Smart Home / Media
- Hue lights: `hue` CLI at `/opt/homebrew/bin/hue`, 21 lights across 8 rooms
- Nest thermostats: 3x (Solarium, Living Room, Bedroom) via Google SDM API
- `nest` CLI at `~/.openclaw/bin/nest` — status, set temp, mode, eco; fuzzy room matching
- Nest credentials cached at `~/.cache/nest-sdm/` (1-year TTL, access token refreshes every 55min)
- Nest history: `~/.openclaw/nest-history/YYYY-MM-DD.jsonl`; dashboard at `http://0.0.0.0:8550`
- GCP OAuth consent: **In production** (Testing mode expires tokens after 7 days)
- Cielo AC: CLI at `~/repos/cielo-cli/cli.js`, token refresh every 30min via LaunchAgent
- Spotify: `spogo` CLI at `/opt/homebrew/bin/spogo`
- Google Home speakers: `catt` CLI at `~/.local/bin/catt` — Kitchen (192.168.1.66), Bedroom (192.168.1.163)
- Speakers only visible to Spotify Connect when actively playing — use `catt` to wake first
- Roomba: CLI at `~/.openclaw/skills/roomba/roomba` via Google Assistant text API
- Roomba auth: `~/.openclaw/roomba/credentials.json` — `invalid_grant` = must re-auth on Mini screen

## Presence Detection
- Script: `~/.openclaw/workspace/scripts/presence-detect.sh` — deployed to BOTH Mini and MBP
- Sticky/arrival-based model — person stays at location until detected at the other
- Cabin scan: Starlink gRPC API (WiFi client list)
- Crosstown scan: ARP from MacBook Pro, pushed to Mini via Tailscale
- Julia's Crosstown fingerprints: hostname `julias-iphone`, MAC `38:e1:3d:c0:40:63`, IP `192.168.165.248`
- Dylan's Crosstown: MAC `6c:3a:ff:5f:fc:ba`, IP `192.168.165.124`

## Crosstown Network Access
- MacBook Pro at Crosstown: `ssh dylans-macbook-pro` (Tailscale, 1Password SSH agent)
- Subnet: `192.168.165.0/24`

## Lessons Learned
- NEVER run `tccutil reset` without a specific bundle_id — resets ALL permissions
- macOS FDA UI often won't accept bare binaries; use .app bundles
- LaunchAgents don't inherit terminal FDA — spawned binary needs its own FDA grant
- .app wrapper must NOT use `exec` — bash parent must stay alive for FDA to flow to children
- OpenClaw `${ENV_VAR}` syntax in config is resolved at load time — env vars must be set before node starts
- Dotfiles repo is PUBLIC — never commit tokens, API keys, or phone numbers
- Google OAuth "Testing" mode expires refresh tokens after 7 days — use "In production"
- Use `trash` not `rm` — recoverable beats gone forever
- npm path on Mini: `/opt/homebrew/opt/node@22/bin/npm` (not on default PATH)
