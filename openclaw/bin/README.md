# OpenClaw Bin Scripts

Helper scripts deployed to `~/.openclaw/bin/` (and `~/bin/`) on the Mac Mini. These support the OpenClaw gateway, dashboards, device integrations, and maintenance automation.

## Deployment

Scripts in this directory are tracked in the dotfiles repo and deployed to Mini via `scp`:

```bash
scp openclaw/bin/<script> dylans-mac-mini:~/.openclaw/bin/<script>
# For ~/bin/ scripts:
scp openclaw/bin/openclaw-weekly-upgrade dylans-mac-mini:~/bin/openclaw-weekly-upgrade
scp openclaw/bin/openclaw-refresh-secrets dylans-mac-mini:~/bin/openclaw-refresh-secrets
```

## Scripts

### Gateway & Maintenance

| Script | Location on Mini | Description |
|--------|-----------------|-------------|
| `openclaw-weekly-upgrade` | `~/bin/` | Weekly npm upgrade with plist backup/restore, BB plugin patch, scope check. Runs Sundays via `ai.openclaw.weekly-upgrade` LaunchAgent. See [WEEKLY-UPGRADE.md](WEEKLY-UPGRADE.md). |
| `openclaw-refresh-secrets` | `~/bin/` | Refreshes `~/.openclaw/.secrets-cache` from 1Password. Run over SSH after key rotation. |
| `sag-wrapper` | `~/.openclaw/bin/` | Wraps `sag` (speech audio generator) with 1Password env injection for ElevenLabs API key. |

### Dashboards

| Script | Port | Description |
|--------|------|-------------|
| `nest-dashboard.py` | 8550 | Nest climate dashboard — Chart.js UI over JSONL history. Serves thermostat temps, humidity, weather, and presence data. Tailscale-only. |
| `usage-dashboard.py` | 8551 | OpenClaw usage dashboard — token consumption, API utilization gauges, agent activity metrics. Chart.js UI over JSONL history. Tailscale-only. |

### Nest Integration

| Script | Description |
|--------|-------------|
| `nest` | CLI wrapper for Google Nest SDM API + Open-Meteo weather. Handles OAuth token refresh, 1Password credential caching, thermostat status, camera snapshots, history recording, and dashboard management. |
| `nest-camera-snap.py` | Captures a single JPEG frame from a Nest camera via WebRTC (SDM API). Uses `aiortc` for WebRTC peer connection. Patches Nest's non-standard ICE candidates. |

### Usage Metrics

| Script | Description |
|--------|-------------|
| `usage-snapshot.sh` | Collects OpenClaw usage metrics every 15min via LaunchAgent. Fetches Anthropic utilization API, parses runtime + cron logs, writes JSONL snapshots to `~/.openclaw/usage-history/`. Auto-prunes data older than 90 days. |

### Bluetooth

| Script | Description |
|--------|-------------|
| `bt_run.sh` | Reads a command from `/tmp/bt_command.txt`, runs `bt_connect` with it, writes result to `/tmp/bt_result.txt`. Used as a GUI-context wrapper for Bluetooth operations. |

> **Note**: `bt_connect` is a compiled Mach-O arm64 binary (not tracked in git). It lives only on the Mini at `~/.openclaw/bin/bt_connect`.

### iMessage Group Sync

| Script | Description |
|--------|-------------|
| `sync-imessage-groups.py` | Queries `~/Library/Messages/chat.db` for group chats (style=43) and adds missing ROWIDs to `channels.imessage.groups` in `openclaw.json`. Preserves existing per-group config and the `*` wildcard. |
| `sync-imessage-groups.command` | GUI-context wrapper for `sync-imessage-groups.py` — needed for Full Disk Access to read `chat.db`. Auto-closes Terminal window after completion. |

### Dotfiles Sync

| Script | Description |
|--------|-------------|
| `dotfiles-pull.command` | Daily git pull of the dotfiles repo on Mini. Stashes local changes, pulls `--ff-only`, pops stash. Runs cron job sync after pull. Auto-closes Terminal window. Runs as a LaunchAgent via Terminal.app for git credential access. |

### Markdown Search (qmd)

| Script | Description |
|--------|-------------|
| `qmd-setup.sh` | One-time setup for `qmd` (Quick Markdown Search) on Mini. Indexes all OpenClaw markdown (workspace, skills, dotfiles) with BM25 + vector embeddings for hybrid search. |

**Package**: `@tobilu/qmd` (npm), installed at `/opt/homebrew/bin/qmd`

**Collections**:
| Name | Path | Contents |
|------|------|----------|
| `workspace` | `~/.openclaw/workspace/` | SOUL.md, TOOLS.md, HEARTBEAT.md |
| `skills` | `~/.openclaw/skills/` | All SKILL.md files |
| `dotfiles-openclaw` | `~/dotfiles/openclaw/` | Bin scripts, plans, workspace copies |

**Usage**:
```bash
qmd query "how does the BB watchdog work"    # hybrid search (recommended)
qmd search "cart URL"                         # BM25 keyword search
qmd update --pull                             # re-index after dotfiles pull
qmd mcp                                       # start MCP server for AI agents
```

### Device Monitoring

| Script | Description |
|--------|-------------|
| `mysa-status.py` | Queries Mysa baseboard heater API for device status (temp, setpoint, mode). Outputs JSON. Uses Cognito auth cached at `~/.config/mysotherm`. |
