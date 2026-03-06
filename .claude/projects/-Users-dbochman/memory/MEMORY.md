# MEMORY.md

## OpenClaw Setup (Updated 2026-03-06)

**Status:** Fully operational. BlueBubbles for iMessage. GWS for Google Workspace. All secrets cached (no 1Password at runtime).

## Key Paths
- OpenClaw config: `~/.openclaw/openclaw.json`
- Gateway LaunchAgent: `~/Library/LaunchAgents/ai.openclaw.gateway.plist`
- FDA .app wrapper: `~/Applications/OpenClawGateway.app`
- Gateway wrapper: `~/Applications/OpenClawGateway.app/Contents/MacOS/OpenClawGateway`
- Gateway logs: `~/.openclaw/logs/gateway.log` and `gateway.err.log`
- Runtime logs: `/tmp/openclaw/openclaw-YYYY-MM-DD.log` (structured JSON)
- Cron jobs: `~/.openclaw/cron/jobs.json`
- Cron run state: `~/.openclaw/cron/runs/<job-id>.jsonl`
- Secrets cache: `~/.openclaw/.secrets-cache` (chmod 600, KEY=VALUE format)
- Dotfiles repo: `~/dotfiles` (github.com/Dbochman/dotfiles, PUBLIC)
- Workspace repo: `~/.openclaw/workspace` (github.com/Dbochman/openclaw-workspace)

## Config Highlights
- Model: `anthropic/claude-sonnet-4-6`
- Auth: OAuth tokens (`sk-ant-oat01-*`) from Dylan's Claude Max subscription
- TTS: inbound mode via ElevenLabs
- Session: per-channel-peer isolation, daily reset at 4am
- iMessage: via BlueBubbles (webhook-only, Private API enabled)
- Gateway entry point: `dist/entry.js` (changed from `dist/index.js` in v2026.3.2)
- Gateway hot-reloads config changes without restart
- Gateway health monitor: DISABLED (`channelHealthCheckMinutes: 0`) — BB watchdog handles stale detection
- Heartbeat interval: 12h, minimal BB ping only
- Typing indicators: native via `typingMode: "thinking"`
- Reactions/tapbacks: native `message` tool `action: "react"` (love, like, dislike, laugh, emphasize, question)

## Secrets & Auth
- **Cache-only pattern** (recommended): Gateway wrapper sources `~/.openclaw/.secrets-cache` — never calls `op read` at startup (hangs under launchd, causes TCC popup storms on Tahoe)
- Refresh helper: `~/bin/openclaw-refresh-secrets` (run over SSH after key rotation)
- 1Password service account token: `~/.openclaw/.env-token`
- **CRITICAL:** `op read` hangs indefinitely under launchd — use cache files exclusively

## BlueBubbles
- Webhook-only architecture (no socket.io) — BB POSTs to `http://localhost:18789/bluebubbles-webhook`
- Private API: `http://localhost:1234/api/v1` with `?password=${BLUEBUBBLES_PASSWORD}`
- BB proxy: `lan-url` (Cloudflare disabled)
- SIP: disabled on Mac Mini (required for BB Private API)
- Watchdog: `com.openclaw.bb-watchdog` runs every 60s, detects stale webhooks, coordinates BB+gateway restarts
- Chat GUIDs: DMs use `any;-;` prefix, groups use `iMessage;+;`
- `ackReactionScope` config is a no-op for iMessage (only wired for Slack/Discord/Telegram)
- BB restart: full app restart needed for dead webhooks (soft restart doesn't fix them)

## Cron Jobs
- Manual trigger: `openclaw cron run <job-id> --timeout 300000 --expect-final`
- **Do NOT use `openclaw agent --deliver`** — spawns independent async agents with no dedup
- **CRITICAL:** When removing jobs from `jobs.json`, also delete `~/.openclaw/cron/runs/<job-id>.jsonl` — orphan run files cause ghost executions
- Health check: job `128c4ed0` at 9AM/9PM ET
- Julia morning briefing: `gws-julia-morning-briefing-0001` at 7AM ET
- Weekly upgrade verify: `weekly-upgrade-verify-0001` at 9:15AM Sun ET

## npm Upgrade Hazards
- `npm install -g openclaw` may run `openclaw install --service` which overwrites the LaunchAgent plist
- Weekly upgrade script backs up/restores the plist around npm install
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
