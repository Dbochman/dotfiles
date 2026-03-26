# Logs Consolidation Plan

## Goal
Move all service logs from `/tmp/` to `~/.openclaw/logs/` so they survive reboots.

## Current State
7 active log files in `/tmp/` written by LaunchAgents. These are lost on macOS reboot. Meanwhile, `~/.openclaw/logs/` already has 28 files from other services (gateway, nest, usage, financial dashboards).

## Files to Move

| Current `/tmp/` path | Plist | Script `LOG_FILE` | New path |
|----------------------|-------|-------------------|----------|
| `bb-watchdog.log` | `com.openclaw.bb-watchdog` | `bb-watchdog.sh:31` | `~/.openclaw/logs/bb-watchdog.log` |
| `bb-lag-summary.log` | `com.openclaw.bb-lag-summary` | `bb-lag-summary.sh:7` | `~/.openclaw/logs/bb-lag-summary.log` |
| `presence-detect.log` | `com.openclaw.presence-cabin` + `presence-crosstown` | `presence-detect.sh:24`, `presence-receive.sh:11` | `~/.openclaw/logs/presence-detect.log` |
| `presence-receive.log` | `com.openclaw.presence-receive` | — (plist only) | `~/.openclaw/logs/presence-receive.log` |
| `ring-listener.log` | `ai.openclaw.ring-listener` | `ring-listener.py:37` | `~/.openclaw/logs/ring-listener.log` |
| `vacancy-actions.log` | `com.openclaw.vacancy-actions` | `vacancy-actions.sh:22` | `~/.openclaw/logs/vacancy-actions.log` |
| `cielo-refresh.log` | `com.openclaw.cielo-refresh` | — (plist only) | `~/.openclaw/logs/cielo-refresh.log` |

## Changes Required

### Plists (StandardOutPath/StandardErrorPath)
7 plists in `openclaw/launchagents/`:
1. `com.openclaw.bb-watchdog.plist` — lines 24, 26
2. `com.openclaw.bb-lag-summary.plist` — lines 27, 29
3. `com.openclaw.presence-cabin.plist` — lines 25, 27
4. `com.openclaw.presence-crosstown.plist` — lines 25, 27
5. `com.openclaw.presence-receive.plist` — lines 22, 24
6. `ai.openclaw.ring-listener.plist` — lines 22, 24
7. `com.openclaw.vacancy-actions.plist` — lines 24, 26
8. `com.openclaw.cielo-refresh.plist` — lines 24, 26

### Scripts (hardcoded LOG_FILE)
1. `openclaw/workspace/scripts/bb-watchdog.sh:31` — `LOG_FILE="/tmp/bb-watchdog.log"`
2. `openclaw/workspace/scripts/bb-lag-summary.sh:7` — `SUMMARY_LOG="/tmp/bb-lag-summary.log"`
3. `openclaw/workspace/scripts/bb-lag-summary.sh:46` — writes to `/tmp/bb-watchdog.log`
4. `openclaw/workspace/scripts/presence-detect.sh:24` — `LOG_FILE="/tmp/presence-detect.log"`
5. `openclaw/workspace/scripts/presence-receive.sh:11` — `LOG_FILE="/tmp/presence-detect.log"`
6. `openclaw/workspace/scripts/vacancy-actions.sh:22` — `LOG_FILE="/tmp/vacancy-actions.log"`
7. `openclaw/skills/ring-doorbell/ring-listener.py:37` — `LOG_FILE = "/tmp/ring-listener.log"`

### Documentation
1. `openclaw/VACANCY-AUTOMATION.md:82,94` — log path and tail command
2. `openclaw/skills/ring-doorbell/SKILL.md:127` — tail command
3. `openclaw/skills/ring-doorbell/IMPLEMENTATION.md:169,170,539` — plist example + tail
4. `openclaw/skills/presence/SKILL.md:121,122` — log paths
5. `openclaw/skills/cielo-ac/SKILL.md:103` — log path

### Memory (external, not in repo)
- `.claude/projects/-Users-dbochman/memory/openclaw-imessage.md:31` — bb-watchdog log path

## Steps
1. Update all 8 plists: replace `/tmp/` with `/Users/dbochman/.openclaw/logs/`
2. Update all 7 script LOG_FILE paths: replace `/tmp/` with `$HOME/.openclaw/logs/`
3. Update all 5 documentation files
4. Verify with: `rg '/tmp/(bb-|presence|ring-|vacancy|cielo-)' --glob '!openclaw/plans/**'`
5. Commit and push
6. Deploy updated plists + scripts to Mini via scp
7. On Mini: move existing logs from /tmp/ to ~/.openclaw/logs/
8. Restart all 7 affected services: `launchctl kickstart -k gui/$(id -u)/<label>`
9. Verify logs appear in new location: `ls -la ~/.openclaw/logs/{bb-watchdog,ring-listener,presence-detect,vacancy-actions,cielo-refresh}*`

## Note on presence-crosstown
The `com.openclaw.presence-crosstown` plist runs on the **MacBook Pro**, not the Mini. Its log path should use the MacBook Pro's home dir. The plist already uses `/tmp/` — consider whether MacBook Pro has a `~/.openclaw/logs/` dir or if `/tmp/` is acceptable there (MacBook Pro reboots less frequently and presence data is ephemeral).

## Risk
Low — log path changes don't affect service behavior. Services will create new log files at the new path on restart. Old `/tmp/` logs can be moved or left to be cleaned by macOS.
