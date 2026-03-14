# Financial Dashboard — Deployment Spec

## Status: v1.0 (2026-03-14)

Python HTTP server serving 6 HTML dashboard pages with 26 JSON API endpoints backed by SQLite. Runs at port 8585 on Mac Mini, LAN access via `http://dylans-mac-mini:8585/`.

---

## Architecture

```
Mac Mini (dylans-mac-mini)
├── CLI: /opt/homebrew/bin/finance → ~/dotfiles/bin/finance
├── Repo: ~/repos/financial-dashboard/
│   ├── serve_dashboard.py          (HTTP server, port 8585)
│   ├── update_data.py              (Plaid sync CLI)
│   ├── db.py                       (SQLite schema + helpers)
│   ├── config.yaml                 (category overrides, FIRE settings)
│   ├── finance.db                  (SQLite database, gitignored)
│   ├── venv/                       (Python virtual environment)
│   ├── dashboard.html              (main financial dashboard)
│   ├── utilities_dashboard.html    (electricity)
│   ├── gas_dashboard.html          (gas)
│   ├── water_dashboard.html        (water)
│   ├── mortgage_dashboard.html     (mortgage)
│   └── expenses_dashboard.html     (expenses)
└── Logs: ~/.openclaw/logs/financial-dashboard.{log,err.log}
```

### LaunchAgent

| Label | Type | Command | Logs |
|-------|------|---------|------|
| `ai.openclaw.financial-dashboard` | KeepAlive | `venv/bin/python3 serve_dashboard.py` | `~/.openclaw/logs/financial-dashboard.{log,err.log}` |

---

## Dashboards & API Endpoints

| URL | Page | Description |
|-----|------|-------------|
| `/` | Main | Spending, income, net worth, savings rate, FIRE metrics |
| `/utilities` | Electricity | Eversource bills, YoY comparison |
| `/gas` | Gas | National Grid bills, YoY comparison |
| `/water` | Water | BWSC bills, YoY comparison |
| `/mortgage` | Mortgage | Amortization, payment history |
| `/expenses` | Expenses | Category breakdown, trends, top merchants |

26 JSON API endpoints under `/api/` — see `serve_dashboard.py` for full reference.

---

## Setup (First Time)

### 1. Clone repo on Mac Mini

```bash
ssh dylans-mac-mini
mkdir -p ~/repos
git clone <url> ~/repos/financial-dashboard
```

### 2. Create venv and install dependencies

```bash
cd ~/repos/financial-dashboard
/usr/bin/python3 -m venv venv
./venv/bin/pip install -r requirements.txt
```

### 3. Verify imports and initialize DB

```bash
./venv/bin/python -c "import yaml; from db import init_db; init_db(); print('OK')"
```

### 4. Ensure log directory exists

```bash
mkdir -p ~/.openclaw/logs
```

### 5. Pull dotfiles and install plist + CLI

```bash
cd ~/dotfiles && git pull
plutil -lint ~/dotfiles/openclaw/ai.openclaw.financial-dashboard.plist
cp ~/dotfiles/openclaw/ai.openclaw.financial-dashboard.plist ~/Library/LaunchAgents/
ln -sf ~/dotfiles/bin/finance /opt/homebrew/bin/finance
```

### 6. Start the dashboard

```bash
finance dashboard start
finance dashboard status
curl -s http://localhost:8585/api/summary | head -c 200
```

---

## CLI Reference

```
finance dashboard                 Open dashboard in browser
finance dashboard start|stop      Start/stop dashboard LaunchAgent
finance dashboard restart|status  Restart or check dashboard status
finance help                      Show help
```

---

## Update Workflow

**Dashboard code changes:**
```bash
cd ~/repos/financial-dashboard && git pull
finance dashboard restart
```

**Plist or CLI changes:**
```bash
cd ~/dotfiles && git pull
cp ~/dotfiles/openclaw/ai.openclaw.financial-dashboard.plist ~/Library/LaunchAgents/
finance dashboard restart
```

The CLI symlink auto-follows dotfiles changes. The plist requires re-copy since launchd reads the file at load time.

---

## Files Reference

| File | Location | Purpose |
|------|----------|---------|
| `bin/finance` | Dotfiles repo + `/opt/homebrew/bin/finance` (symlink) | CLI (dashboard management) |
| `openclaw/ai.openclaw.financial-dashboard.plist` | `~/Library/LaunchAgents/` on Mini | Dashboard KeepAlive service |
| `~/repos/financial-dashboard/` | Cloned repo on Mini | Dashboard server + all source files |

---

## Notes

- **No Plaid sync automation yet** — Plaid is not configured. Data sync will be manual for now; automated daily sync to be added later.
- **Bind address**: `0.0.0.0:8585` — same pattern as nest-dashboard (`:8550`) and usage-dashboard (`:8551`). Mac Mini is behind NAT, no port forwarding.
- **Venv required**: Unlike the single-file nest dashboard, this project has external dependencies (`pyyaml`, `plaid-python`, etc.) so the plist points at the venv Python, not `/usr/bin/python3`.
- **WorkingDirectory is critical**: `SimpleHTTPRequestHandler` serves HTML files relative to CWD. The plist sets this to the repo root.
