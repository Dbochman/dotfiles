# Plans

Implementation plans, specs, and proposals for OpenClaw integrations.

## Active

| Plan | Summary |
|------|---------|
| [dashboard-home-state](dashboard-home-state.md) | Home state dashboard API — aggregated IoT data endpoint |
| [dog-walk-listener-hardening](dog-walk-listener-hardening.md) | Thread-safety and async hot-path hardening for `dog-walk-listener.py` |
| [dog-walk-route-visualization](dog-walk-route-visualization.md) | Approximate Fi-based walk maps, per-house filtering, and split `Both` view |
| [fi-collar-presence](fi-collar-presence.md) | Add Potato's Fi collar to Cabin presence detection |
| [grocery-auth-improvement](grocery-auth-improvement.md) | Improve Star Market grocery reorder auth flow |

## Archive

Completed or historical plans in [`archive/`](archive/).

| Plan | Summary | Status |
|------|---------|--------|
| [bluebubbles-implementation-current-state](archive/bluebubbles-implementation-current-state.md) | BB integration state snapshot (Mar 2026) | Reference |
| [bluebubbles-private-api](archive/bluebubbles-private-api.md) | Enable BB Private API for reactions/typing | Complete |
| [cli-path-standardization](archive/cli-path-standardization.md) | Standardize CLI wrappers in `~/.openclaw/bin/` | Complete |
| [crosstown-8sleep](archive/crosstown-8sleep.md) | Eight Sleep Pod skill implementation | Complete |
| [crosstown-roomba-10-max](archive/crosstown-roomba-10-max.md) | Crosstown Roomba skill implementation | Complete |
| [financial-dashboard-scraping](archive/financial-dashboard-scraping.md) | Web scraping pattern for utility bills | Complete |
| [gog-cutover](archive/gog-cutover.md) | GOG → GWS CLI migration | Complete |
| [mysa](archive/mysa.md) | Mysa thermostat integration | Complete |
| [openclaw-workspace-state](archive/openclaw-workspace-state.md) | Workspace state snapshot (Mar 2026) | Reference |
| [petlibro](archive/petlibro.md) | Petlibro feeder/fountain skill implementation | Complete |
| [skills-symlink-fix](archive/skills-symlink-fix.md) | Why symlinks broke skills deployment | Post-mortem |
| [weekly-upgrade](archive/weekly-upgrade.md) | Automated weekly OpenClaw npm upgrade script | Removed (Mar 12) |
| [plist-consolidation](archive/plist-consolidation.md) | Move 22 plists from openclaw/ root to launchagents/ | Complete |
| [logs-consolidation](archive/logs-consolidation.md) | Move service logs from /tmp/ to ~/.openclaw/logs/ | Complete |
