# MEMORY.md

## OpenClaw iMessage Setup (WORKING - 2026-02-07)
See [openclaw-imessage.md](openclaw-imessage.md) for full details.

**Status:** Fully operational. All secrets in 1Password. Remote access via Tailscale Serve.

## Key Paths
- OpenClaw config: `~/.openclaw/openclaw.json`
- Gateway LaunchAgent: `~/Library/LaunchAgents/ai.openclaw.gateway.plist`
- FDA .app wrapper: `~/Applications/OpenClawGateway.app`
- Gateway logs: `~/.openclaw/logs/gateway.log`
- Dotfiles repo: `~/dotfiles` (github.com/Dbochman/dotfiles, PUBLIC)
- Tailscale URL: `https://dylans-mac-mini.tail3e55f9.ts.net/`

## Config Highlights
- Model: claude-haiku-4-5-20251001 (primary)
- TTS: inbound mode via ElevenLabs
- Session: per-channel-peer isolation, daily reset at 4am
- iMessage: allowlist policy, Dylan + Julia approved
- All secrets (OpenAI, ElevenLabs, gateway token) from 1Password
- Remote client config at `dotfiles/openclaw/openclaw-remote.json`
- install.sh auto-detects gateway host vs remote client by hostname

## Smart Home / Media
- Spotify: controlled via `spogo` CLI (brew steipete/tap/spogo), needs Premium
- Google Home speakers: controlled via `catt` CLI (pipx install catt)
- Cabin speakers: Kitchen speaker (Nest Audio, 192.168.1.66), Bedroom speaker (Nest Mini, 192.168.1.163)
- Home speakers at 19 Crosstown Ave: not reachable yet (no Tailscale device on that network)
- Google Home speakers only show as Spotify Connect targets when actively playing Spotify
- Nest thermostats: 3x (Solarium, Living Room, Bedroom) via Google SDM API
- `nest` CLI at `dotfiles/bin/nest` — status, set temp, mode, eco; fuzzy room matching
- SDM credentials (clientID, client_secret, refresh_token, project_id) in 1Password vault "OpenClaw", item "Google Nest"
- Device Access project ID: `22c97547-9e70-4b24-b9dd-7111d2d2d5c0` (name: "HomeAutomation")
- OAuth client ID: `606088757009-3tnr...` (type: Web application)
- GCP project publishing status: **In production** (switched from Testing on 2026-02-16)
- Access tokens cached at `~/.cache/nest-sdm/access_token` (55min TTL)
- All credentials cached at `~/.cache/nest-sdm/` (clientid, client_secret, refresh_token, project_id) — 1yr TTL
- **CRITICAL:** If GCP OAuth consent screen is in "Testing" mode, refresh tokens expire after 7 days. Must be "In production" for persistent tokens.
- Re-auth flow: nestservices.google.com partner connections → get auth code → exchange via googleapis.com/oauth2/v4/token → update 1Password + cache
- Hue lights: 21 lights across 8 rooms via Hue Bridge (BSB002, 192.168.1.195)
- `hue` CLI at `dotfiles/bin/hue` — status, on/off, brightness, color, scenes; fuzzy room matching
- Hue Bridge credentials (API key, bridge IP) in 1Password "Philips Hue Bridge"
- Credentials cached at `~/.cache/hue/` (24hr TTL)
- Roomba: controlled via `roomba` CLI at `~/.openclaw/skills/roomba/roomba` (symlinked to `~/.local/bin/roomba`)
- Roomba uses Google Assistant text API (`gassist-text`) to send voice-style commands
- Roomba Python venv at `~/.openclaw/roomba/venv/` (gassist-text + google-auth-oauthlib)
- Roomba OAuth credentials at `~/.openclaw/roomba/credentials.json` (separate from Nest — uses assistant-sdk-prototype scope)
- Known Roombas: Floomba, Philly (both at Cabin)
- **CRITICAL:** macOS Homebrew Python (PEP 668) blocks system-wide pip installs. Roomba script uses venv shebang.
- OpenClaw skills: `nest-thermostat`, `hue-lights`, `roomba`, and others at `~/.openclaw/skills/`

## Lessons Learned
- NEVER run `tccutil reset` without a specific bundle_id — it resets ALL permissions for that service
- macOS FDA UI often won't accept bare binaries; use .app bundles
- LaunchAgents don't inherit terminal FDA — the spawned binary itself needs FDA
- .app wrapper must NOT use `exec` — bash parent must stay alive for FDA to flow to children
- 1Password CLI in LaunchAgents needs `OP_BIOMETRIC_UNLOCK_ENABLED=true` and explicit `HOME`
- OpenClaw `${ENV_VAR}` syntax in config is resolved at load time — env vars must be set before node starts
- Dotfiles repo is PUBLIC — never commit tokens, API keys, or phone numbers
- Google OAuth "Testing" mode expires refresh tokens after 7 days — switch to "In production" for personal projects
- Mac Mini 1Password requires biometric unlock — can't be triggered via SSH. Workaround: write credentials directly to cache files
- Mac Mini reachable via Tailscale at `100.93.66.71` (hostname: dylans-mac-mini)
- `nest` CLI on Mac Mini is at `~/.openclaw/bin/nest` (not in PATH by default over SSH)
