# OpenClaw iMessage Setup Notes

## Current Setup (Updated 2026-03-06)
- OpenClaw installed at `/opt/homebrew/bin/openclaw` (v2026.3.2)
- iMessage channel via **BlueBubbles** (webhook-only, Private API enabled)
- Gateway runs as LaunchAgent `ai.openclaw.gateway` via .app wrapper
- Model: `anthropic/claude-opus-4-6` (fallback: `anthropic/claude-sonnet-4-6`)

## BlueBubbles Architecture
- BB POSTs webhook events to `http://localhost:18789/bluebubbles-webhook`
- Gateway registers webhook in BB on startup
- Private API at `http://localhost:1234/api/v1` (reactions, typing, edit/unsend)
- SIP disabled on Mac Mini (required for BB Private API)
- BB proxy: `lan-url` (Cloudflare disabled — was causing rate-limit loops)

## The FDA Solution
macOS won't grant FDA to bare binaries via System Settings UI. The fix:
1. Created `~/Applications/OpenClawGateway.app` — minimal .app bundle with bash script
2. Added the .app to FDA in System Settings
3. LaunchAgent plist uses the .app's executable
4. Key detail: bash script must NOT use `exec` — parent must stay alive for FDA context to flow to children

## Key Files
- OpenClaw config: `~/.openclaw/openclaw.json`
- Gateway LaunchAgent: `~/Library/LaunchAgents/ai.openclaw.gateway.plist`
- FDA .app wrapper: `~/Applications/OpenClawGateway.app`
- Gateway wrapper: `~/Applications/OpenClawGateway.app/Contents/MacOS/OpenClawGateway`
- Gateway logs: `~/.openclaw/logs/gateway.log` and `gateway.err.log`
- Secrets cache: `~/.openclaw/.secrets-cache` (sourced by wrapper, cache-only pattern)
- BB server log: `~/Library/Logs/bluebubbles-server/main.log`
- BB watchdog log: `/tmp/bb-watchdog.log`

## Messaging Details
- Dylan DM target: `dylanbochman@gmail.com` (not phone number — phone handle fails on this host)
- Julia DM target: `+15084234853`
- Chat GUIDs: DMs use `any;-;` prefix, groups use `iMessage;+;`
- Reactions: native via `message` tool `action: "react"` (love, like, dislike, laugh, emphasize, question)
- Typing: native via `typingMode: "thinking"` in config
