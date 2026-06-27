# OpenClaw Native iMessage Setup Notes

## Current Setup (Updated 2026-06-27)
- OpenClaw installed at `/opt/homebrew/bin/openclaw` (`2026.6.10`)
- Bundled `imessage` channel backed by `/opt/homebrew/bin/imsg` (`0.11.1`)
- Gateway runs as LaunchAgent `ai.openclaw.gateway` via its FDA .app wrapper
- `imsg` basic, advanced, and v2 features are ready with bridge version 2
- BlueBubbles is fully retired and is not a rollback path

## Native Architecture
- OpenClaw launches `imsg rpc` over stdio and reads `~/Library/Messages/chat.db`
- SIP is disabled and library validation relaxed for advanced actions
- Stable routes: `chat_id:171` (Dylan), `chat_id:1` (Julia), `chat_id:170` (group)
- Cron jobs use `delivery.channel: "imessage"` and explicit `chat_id:*` targets
- `vacancy-actions.sh` sends lock notifications directly through `imsg` to `chat_id:171`

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
- Current generated-service gateway log: `~/Library/Logs/openclaw/gateway.log` (the tracked recovery plist uses `~/.openclaw/logs/gateway.{log,err.log}`)
- Secrets cache: `~/.openclaw/.secrets-cache` (sourced by wrapper, cache-only pattern)

## Messaging Details
- Dylan DM target: `chat_id:171`
- Julia DM target: `chat_id:1`
- Dylan and Julia group target: `chat_id:170`
- Reactions: native via `message` tool `action: "react"` (love, like, dislike, laugh, emphasize, question)
- Troubleshooting: run `imsg status --json` and `openclaw channels status --probe --channel imessage`
