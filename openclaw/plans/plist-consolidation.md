# Plist Consolidation Plan

## Goal
Move 19 LaunchAgent plist files from `openclaw/` root into `openclaw/launchagents/` subdirectory.

## Current State
19 tracked plist files in `openclaw/` root:
- 9 `ai.openclaw.*.plist` files
- 10 `com.openclaw.*.plist` files

**Out of scope**: `ai.openclaw.dotfiles-pull`, `ai.openclaw.nest-snapshot`, `com.openclaw.bt-connect` (defined elsewhere or not in repo root). Backup/disabled files (`.plist.disabled`, `.plist.pre-upgrade`) are also out of scope.

## Deployment Reality (from Mini audit 2026-03-25)

**Only 1 plist is symlinked** on Mini. The rest are **regular files** (manually copied via `scp` or `cp`).

| Plist | Mini State | Deployed By |
|-------|-----------|-------------|
| `ai.openclaw.gateway` | **Symlink** → `~/dotfiles/openclaw/...` | `install.sh:552` |
| `com.openclaw.bb-watchdog` | Regular file (not symlink despite `install.sh:553`) | `install.sh` or manual `scp` |
| `com.openclaw.poke-messages` | Regular file | `install.sh` or manual `scp` |
| `com.openclaw.bb-lag-summary` | Regular file | `install.sh` or manual `scp` |
| `ai.openclaw.financial-dashboard` | Regular file | Manual `cp` per FINANCIAL-DASHBOARD.md |
| `ai.openclaw.home-state-snapshot` | Regular file | Manual `scp` |
| `ai.openclaw.nest-dashboard` | Regular file | Manual `cp` per NEST-CLIMATE-DASHBOARD.md |
| `ai.openclaw.ring-listener` | Regular file | Manual `scp` |
| `ai.openclaw.usage-dashboard` | Regular file | Manual `scp` |
| `ai.openclaw.usage-snapshot` | Regular file | Manual `scp` |
| `com.openclaw.cielo-refresh` | Regular file | Manual `scp` |
| `com.openclaw.presence-cabin` | Regular file | Manual `scp` |
| `com.openclaw.presence-receive` | Regular file | Manual `scp` |
| `com.openclaw.vacancy-actions` | Regular file | Manual `scp` |

**Not on Mini** (different machine or not deployed):
- `ai.openclaw.8sleep-snapshot` — unclear
- `ai.openclaw.usage-token-push` — runs on local Mac (pushes OAuth cache to Mini)
- `com.openclaw.gas-scrape` — unclear
- `com.openclaw.presence-crosstown` — on MacBook Pro
- `com.openclaw.water-scrape` — unclear

**Note**: `install.sh` has `link_file` calls for 4 plists (lines 552-555), but on Mini only `ai.openclaw.gateway` is currently an active symlink. The other 3 (`bb-watchdog`, `poke-messages`, `bb-lag-summary`) were likely overwritten by manual `scp` at some point and are now regular files.

## Impact: Only the gateway symlink breaks

Since only `ai.openclaw.gateway.plist` is a live symlink, the migration risk is contained to that one file. All other plists on Mini are regular copies and won't be affected by the repo move.

## Files That Need Updates

### Code changes required (evidence-based)
| File | Line | Change |
|------|------|--------|
| `install.sh` | 552 | `openclaw/ai.openclaw.gateway.plist` → `openclaw/launchagents/ai.openclaw.gateway.plist` |
| `install.sh` | 553 | `openclaw/com.openclaw.bb-watchdog.plist` → `openclaw/launchagents/...` |
| `install.sh` | 554 | `openclaw/com.openclaw.poke-messages.plist` → `openclaw/launchagents/...` |
| `install.sh` | 555 | `openclaw/com.openclaw.bb-lag-summary.plist` → `openclaw/launchagents/...` |
| `bin/nest` | 643 | Update error message: `openclaw/ai.openclaw.nest-dashboard.plist` → `openclaw/launchagents/...` |
| `bin/finance` | 20 | Update error message: `openclaw/ai.openclaw.financial-dashboard.plist` → `openclaw/launchagents/...` |
| `openclaw/bin/nest` | 572 | Update error message: `openclaw/ai.openclaw.nest-dashboard.plist` → `openclaw/launchagents/...` |

### Documentation updates
| File | Lines | Change |
|------|-------|--------|
| `openclaw/FINANCIAL-DASHBOARD.md` | 87, 88, 124, 137 | Update plist source paths |
| `openclaw/NEST-CLIMATE-DASHBOARD.md` | 398, 399 | Update plist file location table |
| `openclaw/skills/nest-thermostat/DASHBOARD.md` | 151, 162 | Update plist path + scp example |
| `openclaw/skills/ring-doorbell/IMPLEMENTATION.md` | 82 | Update plist file location |

### No changes needed
- Scripts using launchctl labels only (no source paths)
- `openclaw/plans/archive/*` — historical, exempt
- `openclaw/LAUNCHAGENTS.md` — references labels/programs, not repo source paths

## Steps
1. `mkdir openclaw/launchagents/`
2. `git mv openclaw/*.plist openclaw/launchagents/`
3. Update `install.sh` lines 552-555
4. Update `bin/nest:643`, `bin/finance:20`, `openclaw/bin/nest:572`
5. Update 4 documentation files listed above
6. Verify no remaining old-path references:
   ```bash
   rg 'openclaw/[a-z][^/]*\.plist' --glob '!openclaw/plans/**' --glob '!openclaw/launchagents/**'
   ```
7. Commit and push
8. **On Mini**: fix the one symlink:
   ```bash
   cd ~/dotfiles && git pull
   ln -sf "$(pwd)/openclaw/launchagents/ai.openclaw.gateway.plist" ~/Library/LaunchAgents/ai.openclaw.gateway.plist
   ```
9. Verify the symlink:
   ```bash
   readlink ~/Library/LaunchAgents/ai.openclaw.gateway.plist
   # Expected: /Users/dbochman/dotfiles/openclaw/launchagents/ai.openclaw.gateway.plist
   ```
10. Validate plist:
    ```bash
    plutil -lint ~/Library/LaunchAgents/ai.openclaw.gateway.plist
    ```
11. Verify gateway still running:
    ```bash
    launchctl print gui/$(id -u)/ai.openclaw.gateway | head -5
    ```

## Risk
**Low** — only 1 symlink (`ai.openclaw.gateway`) needs relinking on Mini. All other plists are regular copies unaffected by the repo move. The gateway symlink will break between `git pull` and the `ln -sf` fix, but the running gateway process won't be affected (launchd caches loaded plists). Risk window is seconds if steps 7-8 run in quick succession.
