# OpenClaw iMessage Setup Notes

## The Setup
- OpenClaw installed at `/opt/homebrew/bin/openclaw` (v2026.2.3-1)
- iMessage channel enabled via `imsg` CLI (v0.4.0)
- Gateway runs as LaunchAgent `ai.openclaw.gateway` using node
- User: Dylan Bochman, chat ID 64
- Julia (fiancee): chat ID 1

## Status: WORKING (as of 2026-02-07)
- Gateway LaunchAgent uses `.app` wrapper for FDA: `~/Applications/OpenClawGateway.app`
- Model: `anthropic/claude-sonnet-4-5-20250929`
- imsg rpc successfully reading chat.db

## The FDA Solution
macOS won't grant FDA to bare binaries via System Settings UI. The fix:
1. Created `~/Applications/OpenClawGateway.app` -- a minimal .app bundle with a bash script that runs `node ... gateway`
2. Added the .app to FDA in System Settings
3. Updated the LaunchAgent plist to use the .app's executable instead of node directly
4. Key detail: the bash script must NOT use `exec` -- it needs to stay alive as the parent process so FDA context flows to child processes (node -> imsg)

## Key Files
- OpenClaw config: `~/.openclaw/openclaw.json`
- Gateway LaunchAgent: `~/Library/LaunchAgents/ai.openclaw.gateway.plist`
- FDA .app wrapper: `~/Applications/OpenClawGateway.app`
- Gateway logs: `~/.openclaw/logs/gateway.log` and `gateway.err.log`
