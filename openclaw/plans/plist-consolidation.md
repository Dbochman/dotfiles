# Plist Consolidation Plan

## Goal
Move 19 LaunchAgent plist files from `openclaw/` root into `openclaw/launchagents/` subdirectory.

## Current State
19 tracked plist files scattered in `openclaw/` root alongside docs, scripts, and other config:
- 9 `ai.openclaw.*.plist` files
- 10 `com.openclaw.*.plist` files

**Out of scope**: `ai.openclaw.dotfiles-pull`, `ai.openclaw.nest-snapshot`, `com.openclaw.bt-connect` — these are either defined inline, on different machines, or disabled. Backup/disabled files (`.plist.disabled`, `.plist.pre-upgrade`) are also out of scope.

## Critical: Symlink-Based Deployment

**`install.sh` creates symlinks**, not copies. On the Mini, `~/Library/LaunchAgents/*.plist` are symlinks pointing back to the dotfiles repo (e.g., `~/dotfiles/openclaw/ai.openclaw.gateway.plist`). Moving files in the repo **breaks these symlinks** after the next `git pull`.

This is the primary risk and the reason the migration requires a relink step on the Mini.

## Target Structure
```
openclaw/
├── launchagents/
│   ├── ai.openclaw.8sleep-snapshot.plist
│   ├── ai.openclaw.financial-dashboard.plist
│   ├── ai.openclaw.gateway.plist
│   ├── ai.openclaw.home-state-snapshot.plist
│   ├── ai.openclaw.nest-dashboard.plist
│   ├── ai.openclaw.ring-listener.plist
│   ├── ai.openclaw.usage-dashboard.plist
│   ├── ai.openclaw.usage-snapshot.plist
│   ├── ai.openclaw.usage-token-push.plist
│   ├── com.openclaw.bb-lag-summary.plist
│   ├── com.openclaw.bb-watchdog.plist
│   ├── com.openclaw.cielo-refresh.plist
│   ├── com.openclaw.gas-scrape.plist
│   ├── com.openclaw.poke-messages.plist
│   ├── com.openclaw.presence-cabin.plist
│   ├── com.openclaw.presence-crosstown.plist
│   ├── com.openclaw.presence-receive.plist
│   ├── com.openclaw.vacancy-actions.plist
│   └── com.openclaw.water-scrape.plist
├── LAUNCHAGENTS.md
├── ...
```

## Files That Need Updates

### Code changes required
| File | Change Needed |
|------|---------------|
| `install.sh` (~lines 552-555) | Update symlink source paths from `openclaw/*.plist` to `openclaw/launchagents/*.plist` |
| `bin/nest` (~line 643) | Update plist source path reference |
| `bin/finance` (~line 20) | Update plist source path reference |

### Documentation updates
| File | Change Needed |
|------|---------------|
| `openclaw/FINANCIAL-DASHBOARD.md` | Update plist file location reference |
| `openclaw/NEST-CLIMATE-DASHBOARD.md` | Update plist file location reference |
| `openclaw/skills/ring-doorbell/IMPLEMENTATION.md` | Update plist file location reference |
| `openclaw/skills/nest-thermostat/DASHBOARD.md` | Update plist file location reference |

### No changes needed (label-only references)
Scripts that reference plists by launchctl label (e.g., `launchctl kickstart gui/.../ai.openclaw.gateway`) are unaffected:
- `openclaw/bin/ccusage-setup.sh`
- `openclaw/bin/home-state-wrapper.sh`
- `openclaw/workspace/scripts/bb-watchdog.sh`
- `openclaw/workspace/scripts/presence-receive.sh`

### Exempt (historical, no update needed)
- `openclaw/plans/archive/*` — historical plans, references remain as-is

## Steps
1. `mkdir openclaw/launchagents/`
2. `git mv openclaw/*.plist openclaw/launchagents/`
3. Update `install.sh` symlink source paths
4. Update `bin/nest` and `bin/finance` plist path references
5. Update documentation files listed above
6. Run `rg 'openclaw/[a-z].*\.plist' --glob '!openclaw/plans/archive/*'` to verify no remaining old-path references
7. Commit and push
8. **On Mini**: relink LaunchAgents by running `install.sh` or manually:
   ```bash
   cd ~/dotfiles && git pull
   for f in openclaw/launchagents/*.plist; do
     name=$(basename "$f")
     ln -sf "$(pwd)/$f" ~/Library/LaunchAgents/"$name"
   done
   ```
9. Verify symlinks: `ls -la ~/Library/LaunchAgents/*.openclaw.* | head -5`
10. Validate plists: `for f in ~/Library/LaunchAgents/*.openclaw.*; do plutil -lint "$f"; done`
11. Spot-check a service: `launchctl print gui/$(id -u)/ai.openclaw.gateway`

## Risk
**Medium** — the main risk is broken symlinks on the Mini between `git pull` (which removes old targets) and relinking (step 8). Running services won't be affected immediately (launchd caches loaded plists), but any `launchctl unload/load` or reboot before relinking would fail. Mitigate by doing steps 7-8 in quick succession, or by SSH'ing into Mini and running both in one command.
