# Financial Dashboard — Deployment Spec

## Status: v1.1 (2026-05-24)

Python HTTP server (threaded) serving 6 HTML dashboard pages with 29 JSON API endpoints backed by SQLite. Runs at port 8585 on Mac Mini, LAN access via `http://dylans-mac-mini:8585/`. **Weekly self-healing scrape pipeline** (cron job `financial-scrape-0001`, Sundays 04:05 ET) keeps utility / mortgage / solar data fresh across 7 providers without human intervention except for a few-times-a-year manual relogin.

---

## Scraper Pipeline (Tier 1 / 2 / 2b)

Seven scrapers run weekly via `financial-scrape-0001`. Each writes its own `<source>_data.json`; `update_data.py import-json-*` upserts into `finance.db`.

| # | Scraper | Tier | Mechanism | 1Password item (OpenClaw vault) | Property |
|---|---------|------|-----------|----------------------------------|----------|
| 1 | `scrape_tesla_solar.py` | 1 (API) | Tesla owners API + cached token | (env var) | Cabin |
| 2 | `scrape_eversource.py` | 2 self-heal | Playwright + `--re-auth` flag | `www.eversource.com` | Crosstown |
| 3 | `scrape_national_grid_electric.py` | 2 self-heal | Playwright + `--re-auth` (shared NG session) | `login.nationalgridus.com` | Cabin |
| 4 | `scrape_national_grid.py` (gas) | 2 self-heal | Playwright + `--re-auth` (shared NG session) | `login.nationalgridus.com` | Crosstown |
| 5 | `scrape_bwsc.py` | 2 self-heal | Playwright + `--re-auth` (Microsoft B2C) | `umaxcustomerportalprod.b2clogin.com` | Crosstown |
| 6 | `scrape_mortgage.py --lender pennymac` | 2 self-heal | Playwright + `--re-auth` + email-MFA via `gws` | `PennyMac` | Cabin |
| 7 | `scrape_mortgage.py --lender boa` | **2b — Pinchtab CDP attach** | Playwright `connect_over_cdp` to Pinchtab-managed Chrome | `Bank of America` | Crosstown |

**Why BoA is different.** BoA's bot detection defeats every Playwright-launched variant we tried (headless old + new, channel=chrome, ignore-default-args, navigator.webdriver hidden, force_visible). The working pattern is to attach via CDP to a Chrome that Pinchtab — not Playwright — launched, so no automation flags are injected at process start. See skill `playwright-device-trust-bootstrap`.

### How Tier 2 self-heal works

When a Tier 2 scraper's session expires, the next `--headless` run exits with "Session expired and running in headless mode". The cron's agent then:
1. Reads creds from `op://OpenClaw/<title>/{username,password}` (the Mini's service-account token only reaches the OpenClaw vault — see [[MEMORY:1Password access]]).
2. Runs `./venv/bin/python3 scrape_<name>.py --re-auth --headless`. This drives the login flow programmatically via Playwright, handles MFA where needed (PennyMac auto-fetches the 6-digit `PM-NNNNNNN` code from Julia's Gmail via `gws`), and saves `storage_state.json` in the scraper's `.NAME_session/` dir.
3. Re-runs the normal scrape, which now finds fresh cookies and pulls data.

The whole loop is documented inline in the cron prompt — `openclaw cron list --json | jq '.[]|select(.id=="financial-scrape-0001").payload.message'` to inspect.

### How Tier 2b (BoA) works

- Pinchtab daemon runs as a LaunchAgent (`com.pinchtab.pinchtab`), managing a real Chrome on a dynamic `--remote-debugging-port` with `--user-data-dir=~/.pinchtab/profiles/default`.
- The scraper discovers the CDP port at runtime by `ps`-grepping for the marker `.pinchtab/profiles`, then `playwright.chromium.connect_over_cdp(http://127.0.0.1:N)`.
- Uses the **existing** Pinchtab tab (no `page.goto`, no `context.close()` — those would invalidate the session or close Pinchtab's tabs).
- Authentication detection uses `page.title()` not URL substring (BoA's `/myaccounts/signin/signIn.go` serves the dashboard when authenticated AND the login form when not — same URL, different page). See skill `web-auth-check-by-title-not-url`.

### Manual bootstrap (rare, when a session expires)

Tier 2 sessions auto-renew via `--re-auth`. Tier 2b (BoA) cannot — needs a human:

1. Screen Share into the Mini via Tailscale (the Mini is `dylans-mac-mini`).
2. Open Terminal on the Mini.
3. Make sure Pinchtab Chrome is running with a BoA tab. If not:
   ```bash
   pinchtab daemon                  # confirm running
   curl -X PUT "http://127.0.0.1:$(ps auxww | grep -oE 'remote-debugging-port=\d+' | head -1 | cut -d= -f2)/json/new?https://www.bankofamerica.com/"
   ```
4. In the Pinchtab Chrome window: log in, MFA, tick "Yes, remember this device", land on the dashboard. Leave the tab open.
5. Re-run the cron manually to confirm: `openclaw cron run financial-scrape-0001 --timeout 1500000 --expect-final`.

For Tier 2 manual debugging (e.g. PennyMac credentials change): just run `--re-auth` with creds exported from `op read` — same as the cron does.

---

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

29 JSON API endpoints under `/api/` — see `SCHEMA.md` in the financial-dashboard repo for the full catalog, or `serve_dashboard.py` for the canonical source.

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
plutil -lint ~/dotfiles/openclaw/launchagents/ai.openclaw.financial-dashboard.plist
cp ~/dotfiles/openclaw/launchagents/ai.openclaw.financial-dashboard.plist ~/Library/LaunchAgents/
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
cp ~/dotfiles/openclaw/launchagents/ai.openclaw.financial-dashboard.plist ~/Library/LaunchAgents/
finance dashboard restart
```

The CLI symlink auto-follows dotfiles changes. The plist requires re-copy since launchd reads the file at load time.

---

## Files Reference

| File | Location | Purpose |
|------|----------|---------|
| `bin/finance` | Dotfiles repo + `/opt/homebrew/bin/finance` (symlink) | CLI (dashboard management) |
| `openclaw/launchagents/ai.openclaw.financial-dashboard.plist` | `~/Library/LaunchAgents/` on Mini | Dashboard KeepAlive service |
| `~/repos/financial-dashboard/` | Cloned repo on Mini | Dashboard server + all source files |

---

## Notes

- **Plaid sync is not configured.** Personal-account aggregation goes via the per-provider scrapers above; no automated daily Plaid pull. If a `PLAID_CLIENT_ID` ever appears in `.env`, the cron's `update_data.py sync` step will start running.
- **Bind address**: `0.0.0.0:8585` — same pattern as nest-dashboard (`:8550`) and usage-dashboard (`:8551`). Mac Mini is behind NAT, no port forwarding.
- **Venv required**: external deps (`pyyaml`, `plaid-python`, `playwright`, `beautifulsoup4` for Redfin). The plist points at the venv Python, not `/usr/bin/python3`.
- **WorkingDirectory is critical**: `SimpleHTTPRequestHandler` serves HTML files relative to CWD. The plist sets this to the repo root.
- **Home values via Redfin**: `mortgage_accounts.redfin_url` (set in `<lender>_mortgage_data.json`) triggers `_fetch_redfin_estimate` in `update_data.py` at end-of-import. Currently set for both Crosstown (BoA) and Cabin (PennyMac).
- **Per-property cron job ownership**: bootstrap any new Tier 2 scraper by adding (a) the 1P item to the OpenClaw vault, (b) the LENDERS / config entry in the scraper, (c) the scraper line to the cron prompt, (d) the import line. Tier 2b BoA-pattern adds a `browser.connect_cdp: True` flag.
