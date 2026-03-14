# Deploy Financial Dashboard on Mac Mini

## Status: COMPLETE (2026-03-14)

Dashboard is live at `http://dylans-mac-mini:8585/`. Data is empty (Plaid not configured yet). Automated sync to be added later.

---

## What Was Done

### Files Created (dotfiles repo)
- `openclaw/ai.openclaw.financial-dashboard.plist` — LaunchAgent (KeepAlive, venv python, WorkingDirectory = repo root)
- `bin/finance` — CLI wrapper with `dashboard open|start|stop|restart|status`
- `openclaw/FINANCIAL-DASHBOARD.md` — Full deployment documentation

### Files Modified (financial-dashboard repo)
- `serve_dashboard.py` line 799 — bind changed from `127.0.0.1` to `0.0.0.0`

### Commits
- **dotfiles**: `f095dc0` — `financial-dashboard: add LaunchAgent, CLI, and deployment docs`
- **financial-dashboard**: `b1620da` — `bind to 0.0.0.0 for LAN/Tailscale access from Mac Mini`

### Deployment Steps Executed on Mac Mini
1. `cd ~/dotfiles && git stash && git pull && git stash pop` (resolved `.codex/config.toml` merge conflict, kept Mini's local version)
2. `git clone https://github.com/JJJennings/financial-dashboard ~/repos/financial-dashboard`
3. `cd ~/repos/financial-dashboard && /usr/bin/python3 -m venv venv && ./venv/bin/pip install -r requirements.txt`
4. `./venv/bin/python -c "import yaml; from db import init_db; init_db(); print('OK')"` — passed
5. `mkdir -p ~/.openclaw/logs`
6. `plutil -lint` on plist — OK
7. `cp` plist to `~/Library/LaunchAgents/`
8. `ln -sf ~/dotfiles/bin/finance /opt/homebrew/bin/finance`
9. `finance dashboard start` — running on PID 41885, port 8585

### Verified
- `finance dashboard status` — running
- `curl http://localhost:8585/` — returns dashboard HTML
- `curl http://localhost:8585/api/summary` — returns `{"linked_institutions": 0, "accounts": 0, "transactions": 0, "last_sync": null}`

---

## Architecture

Same pattern as nest-dashboard (`:8550`) and usage-dashboard (`:8551`):

| Component | Path on Mini |
|-----------|-------------|
| Repo | `~/repos/financial-dashboard/` |
| Venv | `~/repos/financial-dashboard/venv/` |
| SQLite DB | `~/repos/financial-dashboard/finance.db` |
| LaunchAgent | `~/Library/LaunchAgents/ai.openclaw.financial-dashboard.plist` |
| CLI | `/opt/homebrew/bin/finance` → `~/dotfiles/bin/finance` |
| Logs | `~/.openclaw/logs/financial-dashboard.{log,err.log}` |

## Update Workflow

**App updates**: `cd ~/repos/financial-dashboard && git pull && finance dashboard restart`

**Plist/CLI updates**: `cd ~/dotfiles && git pull && cp ~/dotfiles/openclaw/ai.openclaw.financial-dashboard.plist ~/Library/LaunchAgents/ && finance dashboard restart`

## Deferred

- Plaid API configuration and `.env` secrets
- Automated daily sync LaunchAgent (`ai.openclaw.financial-sync`)
- Data import (utility bills, paystubs, mortgage statements)
