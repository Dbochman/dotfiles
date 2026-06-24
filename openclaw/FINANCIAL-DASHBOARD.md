# Financial Dashboard — Deployment Spec

## Status: v1.10 (2026-06-18)

Python HTTP server (threaded) serving 6 HTML dashboard pages with 30 JSON API endpoints backed by SQLite. Runs at port `8585` on the Mac Mini with Tailscale/LAN access via `http://dylans-mac-mini:8585/`. A unified cache-only finance LaunchAgent starts daily at 6:15 AM, syncing production Plaid Items before the Forecast crypto cache without calling `op`. The weekly self-healing scrape pipeline (cron job `financial-scrape-0001`, Sundays 04:05 ET) keeps utility, mortgage, and solar data fresh across 7 providers. BoA has cookie replay, raw-CDP fallback, and a guarded one-attempt re-auth path; the retired interval experiment found a server-side timeout.

`8585` is also the canonical data source for the Forecast Dashboard on `8586`. Its owner-aware forecast baseline lets the forecast begin from the latest reconciled source snapshot rather than a wholly fixed portfolio assumption.

---

## Forecast Baseline Contract

`/api/forecast-baseline` is the source contract consumed by the Forecast Dashboard. It returns safe aggregates only, never account names, identifiers, or Plaid tokens.

- Portfolio inputs use canonical depository balances and classified investment holdings from the latest matching snapshot.
- Credit and loan balances remain liabilities; they are not promoted into investable assets and reduce net worth with Plaid's documented balance convention.
- The contract reports `combined`, `dylan`, `julia`, and `household` scopes. Household accounts are separate so the forecast can count them once in Combined rather than twice across individual views.
- Trailing three complete canonical months supply annualized net cash-flow calibration context. The current partial month is display context, not an annualization input.
- Cash-flow `confidence` explicitly identifies observed recognized Plaid deposits and canonical spending. It does not establish gross pay, withholding and payroll taxes, benefits, or retirement contributions.
- Reconciliation, ownership, holdings, and classification coverage are explicit. A `review`, `partial`, or `unavailable` status must not be promoted as a complete live baseline.
- Cash-flow income is quality-gated: salary/wage PFC deposits are recognized automatically; `TRANSFER_*` movements and tax refunds are excluded; other `INCOME_*` deposits wait for one local source-level review. The contract includes `source_status.income_review` so `8586` can expose any pending classification blocker.
- If the same physical account appears through separate Plaid Items, record a verified `plaid_account_aliases` mapping with `update_data.py reconcile-alias-account`. Alias raw rows remain auditable, but their balances, holdings, and transactions are excluded from every canonical aggregate; ownership labels alone do not prevent double-counting.

The Forecast Dashboard composes these source scopes with any still-unlinked owner profile. For the current deployment, an unavailable owner source remains a model supplement instead of being treated as zero. See [FORECAST-DASHBOARD.md](FORECAST-DASHBOARD.md) for client-side application and override behavior.

---

## Portfolio Allocation, Location, And Geography

`/api/forecast-baseline` version `7` reports both the broad live allocation
(`equity`, `bond`, `cash`) and an `equity_geography` allocation for each owner
scope (`us_equity`, `international_equity`, `unclassified_equity`). It is the
sole source of current allocation inputs for Forecast; the forecast service
does not query `finance.db` directly.

- `cash_breakdown` separately reports depository, taxable brokerage,
  retirement, and restricted cash. `spendable` is only depository plus taxable
  brokerage cash; retirement and restricted cash must not fund tax reserves,
  emergencies, or mortgage decisions. Its `total` must reconcile to the broad
  `cash` allocation before Forecast displays it.
- `asset_location` reconciles each ready scope into depository, taxable
  brokerage, IRA, workplace retirement, stock plan, and restricted balances,
  with a complete `allocation_by_location` equity/bond/cash matrix and
  institution aggregates that repeat the location split. Forecast uses only
  depository and taxable-brokerage rows for funding and taxable rebalancing;
  the remaining rows stay valued but are not spendable. It must not be mixed
  with a static Forecast owner supplement.
- `equity_geography_by_location` uses the same rows with U.S., international,
  and unclassified equity values. Each row reconciles to that location's broad
  equity, and its columns recompose `equity_geography`. Forecast uses only
  depository and taxable-brokerage geography rows for country-level
  implementation; a partial, review, unavailable, or invalid map retains a
  broad equity sleeve and withholds country trade guidance.
- `implementation_holdings` is a deployable-only, cross-account aggregate of
  ticker/name, broad bucket, optional equity geography, value, and
  direct-position flag. It contains no account, institution, or raw holding
  identifiers. The positions must recompose the depository and taxable
  brokerage allocation and geography rows; Forecast withholds the
  instrument-level current mix unless its status is `ok`.
- `concentration` reports up to ten direct equity/stock positions and a 5%
  review threshold with location and institution context. `tracked_positions`
  preserves configured direct risk positions such as NVDA even outside the
  display top ten. Reviewed target-date funds are excluded; this is a review
  signal, not a trade instruction.
- Direct securities are classified through `config.yaml` ticker overrides.
  `SWVXX`, for example, is explicitly cash rather than generic ETF equity.
- Target-date holdings use reviewed broad and within-equity look-throughs in
  `retirement_security_mix_overrides` and
  `retirement_security_equity_geography_overrides`.
- An incomplete geography remains `partial` and must not produce a Buy/Trim
  recommendation in Forecast. A documented, source-dated, user-approved de
  minimis exception may allocate a small disclosed residual pro rata across
  the fund's known U.S./international stock exposure; that is a model policy,
  not provider-reported geography.

The deployed 2026-06-18 baseline is `ok` for Combined, Dylan, and Julia.
`household` has no direct investment holdings, so its geography can be
`unavailable` without affecting the Combined rollup.

---

## Payroll Income Detail

Plaid Payroll Income is an optional, manual detail source for gross pay, taxes,
deductions, and benefits. It is deliberately separate from the daily Plaid
Transactions sync: reconciled bank transactions remain the canonical source
for dashboard income and Forecast cash flow.

- `update_data.py payroll-preflight` creates or verifies the protected Plaid
  Income User state at `~/.openclaw/financial-dashboard/plaid-payroll-income.json`
  (mode `0600`); it never invokes `op`.
- `payroll-link --tailscale-funnel --no-browser` is a one-time ADP-capable
  Link flow. It exposes only a random, signed-webhook route through a temporary
  Tailscale Funnel on `8443`, never the existing OpenClaw `443` service.
- The route verifies Plaid's `Plaid-Verification` JWT and raw-body hash, then
  is removed after the session. `payroll-funnel-cleanup` removes a stale route
  after an interrupted flow.
- `payroll-sync` retrieves already-complete data only. Payroll Income Refresh
  is not scheduled because it is a separately enabled paid feature that may
  require user presence.
- Parsed ADP PDFs retain priority when their pay period overlaps a Payroll
  Income row, preventing duplicate payroll charts while preserving both source
  records for audit.

Production Payroll Income Link requires the `income_verification` product to
be enabled for the Plaid client. A successful local preflight only confirms
credentials and protected user state; if Link reports `INVALID_PRODUCT`, enable
Payroll Income in Plaid's Launch Center or request production access before
retrying.

---

## Income Source Quality

The daily cache-only Transactions sync requests Plaid PFC metadata and the
original institution description, then scans canonical incoming depository
transactions. It does not invoke `op` and does not make Payroll Income calls.

- `INCOME_SALARY` and `INCOME_WAGES` are eligible for dashboard and forecast
  cash-flow totals automatically.
- `TRANSFER_*` rows, including joint-account transfers, are excluded even if a
  display category is incorrect.
- `INCOME_TAX_REFUND` is excluded from recurring payroll projection.
- Other `INCOME_*` rows become candidates and block forecast promotion until
  classified. The main dashboard displays an amber notice while candidates
  remain.

Run these commands on the Mini from `~/repos/financial-dashboard` when a
candidate needs review:

```bash
PLAID_ENV=production ./venv/bin/python update_data.py income-review-status
PLAID_ENV=production ./venv/bin/python update_data.py income-review-confirm TRANSACTION_ID "recurring payroll"
PLAID_ENV=production ./venv/bin/python update_data.py income-review-exclude TRANSACTION_ID "non-recurring transfer or payment"
PLAID_ENV=production ./venv/bin/python update_data.py income-review-reset "SOURCE_KEY"
```

Each decision creates a local source rule in `finance.db`; matching future
deposits follow it automatically. `income-review-status` lists each saved
`SOURCE_KEY`; reset one to return its deposits to pending review. It never
edits the raw transaction. To
refresh historical Plaid descriptions after an upgrade, run
`refresh-plaid-categories` once; regular scheduled `sync` maintains the data.

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
| 7 | `scrape_mortgage.py --lender boa` | **Cookie replay + 2b fallback** | Direct `requests` cookie replay; raw CDP attach to Pinchtab Chrome; one guarded cron re-auth after explicit tab sign-out | `Bank of America` | Crosstown |

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

The regular cron must never run generic `--re-auth` for BoA. It uses the
separate `--boa-re-auth` command only after the normal scrape failed and
`--verify-auth` reports `not_authenticated`. The command uses the existing
Pinchtab tab, submits once, saves fresh cookies on success, and stops on MFA,
any challenge, form failure, rejection, timeout, or CDP failure. Do not
substitute Playwright login or a LaunchAgent credential fetch.

### Retired BoA Session-Durability Experiment

The five-minute keep-alive and one-minute browser heartbeat remained healthy for
at least 10 hours and 13 minutes, repeatedly dismissing the BoA UI warning.
BoA then returned HTTP 403 and the live tab became unauthenticated despite
continuous CDP availability. Both LaunchAgents are persistently disabled on the
Mini. Their logs remain forensic evidence only:

| Agent | Status | Log |
|-------|--------|-----|
| `ai.openclaw.boa-keepalive` | Disabled | `~/Library/Logs/boa-keepalive.log` |
| `ai.openclaw.boa-browser-heartbeat` | Disabled | `~/Library/Logs/boa-browser-heartbeat.log` |

Do not bootstrap or kickstart these labels during normal recovery. See
`BOA-SESSION-DURABILITY-HANDOFF.md` for the measured timeline.

### Current BoA Recovery

1. The normal scrape first replays the protected cookie store and then uses raw
   CDP if the live Pinchtab tab remains authenticated.
2. When that fails, cron runs `--verify-auth`. It may invoke `--boa-re-auth`
   only for `not_authenticated`, with credentials already supplied by the
   authorized cron-agent context.
3. `--boa-re-auth` makes one raw-CDP submission and returns without retrying.
   After `authenticated`, cron retries the normal scrape once. All other
   statuses alert for human recovery in the Pinchtab Chrome window. Do not kill
   a still-authenticated Chrome process.

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
│   ├── payroll_income.py            (Payroll Income Link + signed webhook)
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
├── Combined refresh status: ~/.openclaw/finance-refresh/status.json
├── Plaid component status: ~/.openclaw/financial-dashboard/plaid-sync-status.json
└── Logs: ~/.openclaw/logs/financial-dashboard.{log,err.log}
```

### LaunchAgent

| Label | Type | Command | Logs |
|-------|------|---------|------|
| `ai.openclaw.financial-dashboard` | KeepAlive | `venv/bin/python3 serve_dashboard.py` | `~/.openclaw/logs/financial-dashboard.{log,err.log}` |
| `ai.openclaw.finance-refresh` | StartCalendarInterval, daily 6:15 AM | `finance-refresh.py` → Plaid, then crypto | `~/.openclaw/logs/finance-refresh.{log,err.log}` |
| `ai.openclaw.boa-keepalive` | Disabled | Retired five-minute browser session experiment | `~/Library/Logs/boa-keepalive.log` |
| `ai.openclaw.boa-browser-heartbeat` | Disabled | Retired one-minute UI warning experiment | `~/Library/Logs/boa-browser-heartbeat.log` |

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

31 JSON API endpoints under `/api/` — see `SCHEMA.md` in the financial-dashboard repo for the full catalog, or `serve_dashboard.py` for the canonical source.

The Forecast integration contract is available at `http://dylans-mac-mini:8585/api/forecast-baseline`. Validate its status and owner-scope coverage after source or forecast changes; avoid copying raw payloads into logs or chat.

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
plutil -lint ~/dotfiles/openclaw/launchagents/ai.openclaw.finance-refresh.plist
cp ~/dotfiles/openclaw/launchagents/ai.openclaw.finance-refresh.plist ~/Library/LaunchAgents/
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
ssh dylans-mac-mini 'git -C "$HOME/repos/financial-dashboard" pull --ff-only'
ssh dylans-mac-mini 'launchctl kickstart -k "gui/$(id -u)/ai.openclaw.financial-dashboard"'
```

**Forecast-baseline contract or configuration changes:**
```bash
ssh dylans-mac-mini 'git -C "$HOME/repos/financial-dashboard" pull --ff-only'
ssh dylans-mac-mini 'launchctl kickstart -k "gui/$(id -u)/ai.openclaw.financial-dashboard"'
ssh dylans-mac-mini 'launchctl kickstart -k "gui/$(id -u)/ai.openclaw.forecast-dashboard"'
```

Restart `8585` before `8586`: the forecast service caches its current snapshot
for five minutes and otherwise may retain the prior baseline after a source
policy change.

**Plist or CLI changes:**
```bash
cd ~/dotfiles && git pull
cp ~/dotfiles/openclaw/launchagents/ai.openclaw.financial-dashboard.plist ~/Library/LaunchAgents/
cp ~/dotfiles/openclaw/launchagents/ai.openclaw.finance-refresh.plist ~/Library/LaunchAgents/
finance dashboard restart
```

The CLI symlink auto-follows dotfiles changes. The plist requires re-copy since launchd reads the file at load time.

---

## Files Reference

| File | Location | Purpose |
|------|----------|---------|
| `bin/finance` | Dotfiles repo + `/opt/homebrew/bin/finance` (symlink) | CLI (dashboard management) |
| `openclaw/launchagents/ai.openclaw.financial-dashboard.plist` | `~/Library/LaunchAgents/` on Mini | Dashboard KeepAlive service |
| `openclaw/launchagents/ai.openclaw.finance-refresh.plist` | `~/Library/LaunchAgents/` on Mini | Daily Plaid → crypto source refresh |
| `~/repos/financial-dashboard/` | Cloned repo on Mini | Dashboard server + all source files |

---

## Notes

- **Plaid sync is cache-only and independent of the scraper cron.** `ai.openclaw.finance-refresh` invokes the existing `financial-dashboard-plaid-sync.py` wrapper before the crypto wrapper. The Plaid component runs `update_data.py sync` with `PLAID_ENV=production`; application code reads only the mode-`0600` OpenClaw secrets cache and protected Item token cache. Neither the orchestrator nor its source wrappers invokes `op`. The separately invoked `openclaw-refresh-secrets` command restores `PLAID_CLIENT_ID`, `PLAID_SECRET_PRODUCTION`, and `PLAID_SECRET_SANDBOX` from the managed Plaid item so a general cache refresh cannot silently disable the daily sync.
- **Scheduled does not mean continuously running.** `ai.openclaw.finance-refresh` is expected to show `not running` between its daily 06:15 local executions. Use `~/.openclaw/finance-refresh/status.json`, the component status files, and logs to diagnose a failed run rather than assuming the inactive state is an error.
- **Forecast refresh boundary.** Plaid data becomes a new forecast starting point after a successful source sync and completed income-source review. `8586` refreshes its current snapshot cache every five minutes; restart it after an `8585` baseline or configuration deployment to invalidate that cache. This is a current-day planning baseline, not an intraday balance stream.
- **Cross-Item shared accounts.** When each owner links the same joint account through separate logins, verify the account identity and map the newer record to the existing canonical Plaid account: `./venv/bin/python3 update_data.py reconcile-alias-account ALIAS_ACCOUNT_ID CANONICAL_ACCOUNT_ID "same physical joint account"`. Both inputs must be Plaid accounts from different Items. The alias is intentionally retained for audit and can be restored with `reconcile-clear-alias`; do not solve the duplicate by assigning both copies to `household`.
- **Bind address**: `0.0.0.0:8585` — same pattern as nest-dashboard (`:8550`) and usage-dashboard (`:8551`). Mac Mini is behind NAT, no port forwarding.
- **Venv required**: external deps (`pyyaml`, `plaid-python`, `PyJWT[crypto]`, `playwright`, `beautifulsoup4` for Redfin). The plist points at the venv Python, not `/usr/bin/python3`.
- **WorkingDirectory is critical**: `SimpleHTTPRequestHandler` serves HTML files relative to CWD. The plist sets this to the repo root.
- **Home values via Redfin**: `mortgage_accounts.redfin_url` (set in `<lender>_mortgage_data.json`) triggers `_fetch_redfin_estimate` in `update_data.py` at end-of-import. Currently set for both Crosstown (BoA) and Cabin (PennyMac).
- **Per-property cron job ownership**: bootstrap any new Tier 2 scraper by adding (a) the 1P item to the OpenClaw vault, (b) the LENDERS / config entry in the scraper, (c) the scraper line to the cron prompt, (d) the import line. Tier 2b BoA-pattern adds a `browser.connect_cdp: True` flag.
