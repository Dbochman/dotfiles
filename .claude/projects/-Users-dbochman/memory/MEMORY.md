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
- Model: claude-sonnet-4-5-20250929 (fallback: haiku-4-5)
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
- SDM credentials (client ID, secret, refresh token, project ID) in 1Password "Google Nest SDM API"
- Access tokens cached at `~/.cache/nest-sdm/` (55min TTL)
- Refresh token expires after 6 months of non-use — must be renewed
- Hue lights: 21 lights across 8 rooms via Hue Bridge (BSB002, 192.168.1.195)
- `hue` CLI at `dotfiles/bin/hue` — status, on/off, brightness, color, scenes; fuzzy room matching
- Hue Bridge credentials (API key, bridge IP) in 1Password "Philips Hue Bridge"
- Credentials cached at `~/.cache/hue/` (24hr TTL)
- OpenClaw skills: `nest-thermostat` and `hue-lights` at `~/.openclaw/skills/`

## Lessons Learned
- NEVER run `tccutil reset` without a specific bundle_id — it resets ALL permissions for that service
- macOS FDA UI often won't accept bare binaries; use .app bundles
- LaunchAgents don't inherit terminal FDA — the spawned binary itself needs FDA
- .app wrapper must NOT use `exec` — bash parent must stay alive for FDA to flow to children
- 1Password CLI in LaunchAgents needs `OP_BIOMETRIC_UNLOCK_ENABLED=true` and explicit `HOME`
- OpenClaw `${ENV_VAR}` syntax in config is resolved at load time — env vars must be set before node starts
- Dotfiles repo is PUBLIC — never commit tokens, API keys, or phone numbers
