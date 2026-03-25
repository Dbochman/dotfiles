# Plist Consolidation Plan

## Goal
Move 20 LaunchAgent plist files from `openclaw/` root into `openclaw/launchagents/` subdirectory.

## Current State
20 plist files scattered in `openclaw/` root alongside docs, scripts, and other config:
- 9 `ai.openclaw.*.plist` files
- 11 `com.openclaw.*.plist` files

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
├── LAUNCHAGENTS.md (stays at root — reference doc)
├── CRON-JOBS.md
├── ...
```

## Scripts That Reference Plist Paths
These will need path updates after the move:

| Script | Reference Type |
|--------|---------------|
| `openclaw/bin/ccusage-setup.sh` | Plist path for launchctl |
| `openclaw/bin/home-state-wrapper.sh` | Label reference |
| `openclaw/workspace/scripts/bb-watchdog.sh` | Label reference |
| `openclaw/workspace/scripts/presence-receive.sh` | Label reference |

Most scripts reference plists by **label** (e.g., `launchctl kickstart gui/.../ai.openclaw.gateway`) not by file path, so they won't need changes. Only `dotfiles-pull.command` (which deploys plists to Mini) needs the source path updated.

## Deployment Impact
`dotfiles-pull.command` copies plists from the dotfiles repo to `~/Library/LaunchAgents/` on the Mini. After the move:
- Update the glob/copy pattern from `openclaw/*.plist` to `openclaw/launchagents/*.plist`
- The destination (`~/Library/LaunchAgents/`) stays the same

## Steps
1. `mkdir openclaw/launchagents/`
2. `git mv openclaw/*.plist openclaw/launchagents/`
3. Update `dotfiles-pull.command` plist source path
4. Update any scripts that reference `openclaw/*.plist` source paths
5. Update `LAUNCHAGENTS.md` if it references file locations
6. Test deployment to Mini
7. Commit and push

## Risk
Low — plists on Mini at `~/Library/LaunchAgents/` are not affected. Only the dotfiles repo source layout changes. The `dotfiles-pull.command` deploy script is the only thing that needs a path update.
