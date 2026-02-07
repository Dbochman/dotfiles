# MEMORY.md

## OpenClaw iMessage Setup (WORKING - 2026-02-07)
See [openclaw-imessage.md](openclaw-imessage.md) for full details.

**Status:** Gateway running with FDA via .app wrapper. Model: claude-sonnet-4-5-20250929.

## Key Paths
- OpenClaw config: `~/.openclaw/openclaw.json`
- Gateway LaunchAgent: `~/Library/LaunchAgents/ai.openclaw.gateway.plist`
- FDA .app wrapper: `~/Applications/OpenClawGateway.app`
- Gateway logs: `~/.openclaw/logs/gateway.log`

## Lessons Learned
- NEVER run `tccutil reset` without a specific bundle_id — it resets ALL permissions for that service
- macOS FDA UI often won't accept bare binaries via drag-and-drop; may need .app bundles
- LaunchAgents don't inherit terminal FDA — the spawned binary itself needs FDA
