# Financial Dashboard — Deployment Spec

## Status: v1.2 (2026-06-18)

Python HTTP server (threaded) serving 6 HTML dashboard pages with 29 JSON API endpoints backed by SQLite. Runs at port 8585 on Mac Mini, LAN access via `http://dylans-mac-mini:8585/`. **Weekly self-healing scrape pipeline** (cron job `financial-scrape-0001`, Sundays 04:05 ET) keeps utility, mortgage, and solar data fresh across 7 providers. BoA has a cookie-replay fast path plus an active browser-session durability experiment; an interactive login is only the recovery path when both have expired.

---

## Scraper Pipeline (Tier 1 / 2 / BoA Fallback)

Seven scrapers run weekly via `financial-scrape-0001`. Each writes its own `<source>_data.json`; `update_data.py import-json-*` upserts into `finance.db`.

| # | Scraper | Tier | Mechanism | 1Password item (OpenClaw vault) | Property |
|---|---------|------|-----------|----------------------------------|----------|
| 1 | `scrape_tesla_solar.py` | 1 (API) | Tesla owners API + cached token | (env var) | Cabin |
| 2 | `scrape_eversource.py` | 2 self-heal | Playwright + `--re-auth` flag | `www.eversource.com` | Crosstown |
| 3 | `scrape_national_grid_electric.py` | 2 self-heal | Playwright + `--re-auth` (shared NG session) | `login.nationalgridus.com` | Cabin |
| 4 | `scrape_national_grid.py` (gas) | 2 self-heal | Playwright + `--re-auth` (shared NG session) | `login.nationalgridus.com` | Crosstown |
| 5 | `scrape_bwsc.py` | 2 self-heal | Playwright + `--re-auth` (Microsoft B2C) | `umaxcustomerportalprod.b2clogin.com` | Crosstown |
| 6 | `scrape_mortgage.py --lender pennymac` | 2 self-heal | Playwright + `--re-auth` + email-MFA via `gws` | `PennyMac` | Cabin |
| 7 | `scrape_mortgage.py --lender boa` | **Cookie replay + 2b fallback** | Direct `requests` cookie replay; raw CDP attach to Pinchtab Chrome on expiry | Manual recovery only | Crosstown |

**Why BoA is different.** BoA's bot detection defeats every Playwright-launched variant tried (headless old + new, channel=chrome, ignore-default-args, navigator.webdriver hidden, and force_visible). The browser fallback must attach to a Chrome that Pinchtab, not Playwright, launched. Chrome 149 also rejects Playwright's `connect_over_cdp` during `Browser.setDownloadBehavior`, so the scraper uses a narrow raw-CDP WebSocket shim instead.

### How Tier 2 self-heal works

When a Tier 2 scraper's session expires, the next `--headless` run exits with "Session expired and running in headless mode". The cron's agent then:
1. Reads creds from `op://OpenClaw/<title>/{username,password}` (the Mini's service-account token only reaches the OpenClaw vault — see [[MEMORY:1Password access]]).
2. Runs `./venv/bin/python3 scrape_<name>.py --re-auth --headless`. This drives the login flow programmatically via Playwright, handles MFA where needed (PennyMac auto-fetches the 6-digit `PM-NNNNNNN` code from Julia's Gmail via `gws`), and saves `storage_state.json` in the scraper's `.NAME_session/` dir.
3. Re-runs the normal scrape, which now finds fresh cookies and pulls data.

The whole loop is documented inline in the cron prompt — `openclaw cron list --json | jq '.[]|select(.id=="financial-scrape-0001").payload.message'` to inspect.

### How BoA works

1. **Cookie-replay fast path.** `scrape_mortgage.py --lender boa` first calls the BoA account API through `requests`, using the mode-`0600` cookie store at `~/.openclaw/.boa_cookies.json`. A warm cookie store needs no browser.
2. **Raw-CDP fallback.** If the API rejects replayed cookies, the scraper discovers Pinchtab's dynamic CDP port and attaches to the existing BoA tab. It uses `Runtime.evaluate` and `Network.getAllCookies`, not `playwright.connect_over_cdp`. A successful fallback atomically replaces the cookie store for the next fast-path scrape.
3. **Auth verification.** The BoA sign-in URL can render either the account dashboard or the login form, and the title can remain "Accounts Overview" after sign-out. The scraper checks visible login controls before using the title heuristic. A JSON API response alone is not proof that the live tab remains authenticated.
4. **Session safety.** Do not call `page.goto`, close the browser context, or kill Pinchtab Chrome after a fresh login. BoA session cookies are process-bound even though the captured cookie store can outlive Chrome for a limited time.

The regular cron must never run `--re-auth` for BoA. A one-time interactive recovery may trigger MFA or device/risk checks; it is not a reliable unattended login mechanism.

### BoA Session Durability Experiment

The current 24-48 hour experiment has no quiet-hours gap and uses two independent interval agents:

| Agent | Cadence | Responsibility | Log |
|-------|---------|----------------|-----|
| `ai.openclaw.boa-keepalive` | 5 minutes | Verifies live-tab authentication before and after a same-origin API ping, sends a trusted mouse move, and atomically saves the current cookie jar. Logs contain metadata only. | `~/Library/Logs/boa-keepalive.log` |
| `ai.openclaw.boa-browser-heartbeat` | 1 minute | Sends no account API traffic. It sends browser-level activity and, if present, dynamically accepts BoA's two-minute inactivity-warning `OK` control. | `~/Library/Logs/boa-browser-heartbeat.log` |

Healthy log statuses are `ok` for the keep-alive and `ok` or `warning_dismissed` for the heartbeat. Any other status is a session-health failure to investigate, not a reason to overwrite or delete the existing cookie store. Logs intentionally contain only status, HTTP/content-type metadata, and cookie counts or expiry metadata; never print cookie values or account response bodies.

Verify the browser independently during the soak:

```bash
ssh dylans-mac-mini 'cd ~/repos/financial-dashboard && ./venv/bin/python3 scrape_mortgage.py --lender boa --verify-auth'
ssh dylans-mac-mini 'tail -n 20 ~/Library/Logs/boa-keepalive.log'
ssh dylans-mac-mini 'tail -n 20 ~/Library/Logs/boa-browser-heartbeat.log'
```

The initial measurement should keep both cadences fixed. Record the first non-healthy status, any 4xx/API rejection, or loss of browser authentication to distinguish an inactivity timeout from a server-side absolute or risk timeout.

### Manual BoA Bootstrap (recovery only)

Tier 2 sessions auto-renew via `--re-auth`. BoA needs a human only after both the replayed cookies and live Pinchtab tab are no longer usable:

1. Screen Share or VNC into `dylans-mac-mini` and ensure the normal Pinchtab Chrome instance is running. If Pinchtab has evicted every target, restore its tab before logging in. Do not kill a still-authenticated Chrome process.
2. In the Pinchtab Chrome window, sign in to BoA, complete any MFA or device-trust prompts, and leave the account overview tab open.
3. Capture a fresh cookie store through the normal scrape:
   ```bash
   ssh dylans-mac-mini 'cd ~/repos/financial-dashboard && ./venv/bin/python3 scrape_mortgage.py --lender boa --headless --merge'
   ```
4. Confirm the two interval agents can see the recovered tab:
   ```bash
   ssh dylans-mac-mini 'launchctl kickstart -p "gui/$(id -u)/ai.openclaw.boa-keepalive"'
   ssh dylans-mac-mini 'launchctl kickstart -p "gui/$(id -u)/ai.openclaw.boa-browser-heartbeat"'
   ```

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
| `ai.openclaw.boa-keepalive` | StartInterval (5 min) | `scrape_mortgage.py --lender boa --keep-alive` | `~/Library/Logs/boa-keepalive.log` |
| `ai.openclaw.boa-browser-heartbeat` | StartInterval (1 min) | `scrape_mortgage.py --lender boa --browser-heartbeat` | `~/Library/Logs/boa-browser-heartbeat.log` |

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
