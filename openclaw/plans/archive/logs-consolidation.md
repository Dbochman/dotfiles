# Logs Consolidation Plan

## Goal
Move all service logs from `/tmp/` to `~/.openclaw/logs/` so they survive reboots.

## Current State
7+ active log files in `/tmp/` written by LaunchAgents. These are lost on macOS reboot. Meanwhile, `~/.openclaw/logs/` already has 28 files from other services (gateway, nest, usage, financial dashboards).

## Critical: Double-Write Pattern

Several services write to the same log file via **two independent mechanisms**:
1. **Plist `StandardOutPath`** — launchd redirects stdout/stderr to the log file
2. **Script `log()` function** — script appends to the same file via `>> $LOG_FILE`

When both point to the same path, this works but is redundant and fragile. When migrating, **both must be updated together**.

### Per-Service Logging Analysis

| Service | Plist stdout → file | Script log() → file | Owner to use |
|---------|--------------------|--------------------|-------------|
| `bb-watchdog` | Yes (plist:24,26) | Yes (`bb-watchdog.sh:54`) | **Script** — script does rotation + structured logging. Set plist stdout to `/dev/null` |
| `bb-lag-summary` | Yes (plist:27,29) | Yes (`bb-lag-summary.sh:46` writes to bb-watchdog.log) | **Script** — writes to its own `SUMMARY_LOG`. Set plist stdout to `/dev/null` |
| `presence-detect` | Yes (plist:25,27) | Yes (`presence-detect.sh:34`) | **Script** — script has structured `log()`. Set plist stdout to `/dev/null` |
| `presence-receive` | Yes (plist:22,24) | Yes (`presence-receive.sh:33` writes to presence-detect.log) | **Script** — shares log with presence-detect. Set plist stdout to `/dev/null` |
| `vacancy-actions` | Yes (plist:24,26) | Yes (`vacancy-actions.sh:27`) | **Script** — script has structured `log()`. Set plist stdout to `/dev/null` |
| `cielo-refresh` | Yes (plist:24,26) | No (plist only) | **Plist** — keep plist stdout redirect |
| `ring-listener` | Yes (plist:22,24) | **Stdout only** (`ring-listener.py:137` writes to `sys.stdout`) | **Plist** — script logs via stdout, plist captures it. `LOG_FILE` at line 37 is a dead variable (unused) |

**Decision**: For services with script `log()` functions, the script owns logging and plist stdout goes to `/dev/null`. For services that log via stdout (ring-listener) or have no script logging (cielo-refresh), the plist owns logging.

## Files to Update

### Plists — change StandardOutPath/StandardErrorPath

**To `~/.openclaw/logs/` (plist-owned logging):**
| Plist | Lines | New path |
|-------|-------|----------|
| `ai.openclaw.ring-listener` | 22, 24 | `~/.openclaw/logs/ring-listener.log` |
| `com.openclaw.cielo-refresh` | 24, 26 | `~/.openclaw/logs/cielo-refresh.log` |

**To `/dev/null` (script-owned logging):**
| Plist | Lines |
|-------|-------|
| `com.openclaw.bb-watchdog` | 24, 26 |
| `com.openclaw.bb-lag-summary` | 27, 29 |
| `com.openclaw.presence-cabin` | 25, 27 |
| `com.openclaw.presence-crosstown` | 25, 27 |
| `com.openclaw.presence-receive` | 22, 24 |
| `com.openclaw.vacancy-actions` | 24, 26 |

### Scripts — update LOG_FILE paths
| Script | Line | Old | New |
|--------|------|-----|-----|
| `bb-watchdog.sh` | 31 | `/tmp/bb-watchdog.log` | `$HOME/.openclaw/logs/bb-watchdog.log` |
| `bb-watchdog.sh` | 32 | `/tmp/bb-ingest-lag.log` | `$HOME/.openclaw/logs/bb-ingest-lag.log` |
| `bb-watchdog.sh` | 44 | `find /tmp -name 'bb-watchdog.log.*'` | `find $HOME/.openclaw/logs -name 'bb-watchdog.log.*'` |
| `bb-lag-summary.sh` | 6 | `/tmp/bb-ingest-lag.log` | `$HOME/.openclaw/logs/bb-ingest-lag.log` |
| `bb-lag-summary.sh` | 7 | `/tmp/bb-lag-summary.log` | `$HOME/.openclaw/logs/bb-lag-summary.log` |
| `bb-lag-summary.sh` | 46 | `/tmp/bb-watchdog.log` | `$HOME/.openclaw/logs/bb-watchdog.log` |
| `presence-detect.sh` | 24 | `/tmp/presence-detect.log` | `$HOME/.openclaw/logs/presence-detect.log` |
| `presence-receive.sh` | 11 | `/tmp/presence-detect.log` | `$HOME/.openclaw/logs/presence-detect.log` |
| `vacancy-actions.sh` | 22 | `/tmp/vacancy-actions.log` | `$HOME/.openclaw/logs/vacancy-actions.log` |
| `ring-listener.py` | 37 | Remove dead `LOG_FILE` variable (unused, logging goes to stdout) |

### Documentation updates
| File | Lines | Change |
|------|-------|--------|
| `openclaw/VACANCY-AUTOMATION.md` | 82, 94 | Update log path + tail command |
| `openclaw/skills/ring-doorbell/SKILL.md` | 127 | Update tail command |
| `openclaw/skills/ring-doorbell/IMPLEMENTATION.md` | 169, 170, 539 | Update plist example + tail |
| `openclaw/skills/presence/SKILL.md` | 121, 122 | Update log paths |
| `openclaw/skills/cielo-ac/SKILL.md` | 103 | Update log path |

## Scope Exclusions

### `presence-crosstown` (MacBook Pro)
The `com.openclaw.presence-crosstown` plist runs on the **MacBook Pro**, not the Mini. It writes to `/tmp/presence-detect.log` on that machine. The `~/.openclaw/logs/` directory has been created on the MacBook Pro (2026-03-25). The script change to `presence-detect.sh` will use `$HOME/.openclaw/logs/` which will work on both machines. The crosstown plist also needs its `StandardOutPath`/`StandardErrorPath` updated to `/dev/null` (script-owned logging) and deployed to the MacBook Pro.

**MacBook Pro deployment (step 13):**
```bash
ssh -i ~/.ssh/id_mini_to_mbp -o IdentityAgent=none dylans-macbook-pro \
  'cp ~/dotfiles/openclaw/launchagents/com.openclaw.presence-crosstown.plist ~/Library/LaunchAgents/ && \
   launchctl bootout gui/$(id -u)/com.openclaw.presence-crosstown 2>/dev/null; \
   launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.openclaw.presence-crosstown.plist'
```

### `ring-listener.py:37` (dead variable)
`LOG_FILE = "/tmp/ring-listener.log"` is never used — the script logs via `sys.stdout.write()` at line 137, which launchd captures via `StandardOutPath`. Remove the dead variable rather than updating it.

## Steps
1. Update 8 plists (2 to new log path, 6 to `/dev/null`)
2. Update 6 scripts (LOG_FILE paths + rotation path)
3. Clean dead `LOG_FILE` variable from `ring-listener.py`
4. Update 5 documentation files
5. Verify no remaining /tmp/ references for in-scope logs:
   ```bash
   rg '/tmp/(bb-watchdog|bb-lag-summary|bb-ingest-lag|presence-detect|presence-receive|ring-listener|vacancy-actions|cielo-refresh)' --glob '!openclaw/plans/**'
   ```
   Expected: no matches. If any remain, fix before proceeding.
7. Commit and push
8. Deploy updated plists to Mini: `scp openclaw/launchagents/*.plist dylans-mac-mini:~/Library/LaunchAgents/`
9. Deploy updated scripts to Mini
10. **Reload services FIRST** (before moving logs — avoids race where running scripts recreate `/tmp/` logs):
    ```bash
    for label in com.openclaw.bb-watchdog com.openclaw.bb-lag-summary \
      com.openclaw.presence-cabin com.openclaw.presence-receive \
      com.openclaw.vacancy-actions com.openclaw.cielo-refresh \
      ai.openclaw.ring-listener; do
      launchctl bootout gui/$(id -u)/$label 2>/dev/null
      launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/$label.plist
    done
    ```
11. Move old logs from /tmp/ to new location (services are already writing to new paths):
    ```bash
    for f in bb-watchdog bb-lag-summary bb-ingest-lag presence-detect presence-receive vacancy-actions cielo-refresh ring-listener; do
      mv /tmp/${f}.log ~/.openclaw/logs/${f}.log.old 2>/dev/null
    done
    ```
12. Verify logs appear in new location:
    ```bash
    ls -la ~/.openclaw/logs/{bb-watchdog,ring-listener,presence-detect,vacancy-actions,cielo-refresh,bb-lag-summary,bb-ingest-lag}.log
    ```
13. **Deploy to MacBook Pro** (presence-crosstown plist + script). SSH key `~/.ssh/id_mini_to_mbp` exists on Mini (confirmed 2026-03-25). Each step validates before proceeding:
    ```bash
    MBP_SSH="ssh -i ~/.ssh/id_mini_to_mbp -o IdentityAgent=none dylans-macbook-pro"
    $MBP_SSH 'cd ~/dotfiles && git pull --ff-only' && \
    $MBP_SSH 'cp ~/dotfiles/openclaw/launchagents/com.openclaw.presence-crosstown.plist ~/Library/LaunchAgents/' && \
    $MBP_SSH 'cp ~/dotfiles/openclaw/workspace/scripts/presence-detect.sh ~/.openclaw/workspace/scripts/' && \
    $MBP_SSH 'launchctl bootout gui/$(id -u)/com.openclaw.presence-crosstown 2>/dev/null; launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.openclaw.presence-crosstown.plist'
    ```
14. Verify MacBook Pro log:
    ```bash
    $MBP_SSH 'ls -la ~/.openclaw/logs/presence-detect.log'
    ```

## Risk
Low — log path changes don't affect service behavior. The `bootout`/`bootstrap` cycle will briefly stop and restart each service (~1s gap). Gateway is NOT affected by this plan.
