# Forecast Dashboard — Deployment Spec

## Status: v1.3 (2026-06-18)

Python HTTP server serving the Financial Advisor forecast dashboard, preset redirects, a preset index page, current-snapshot APIs, public-market prices, and the monthly operating checklist overlay. Runs at port `8586` on the Mac Mini with Tailscale-only access. A coverage-gated live baseline now seeds eligible forecast inputs from the reconciled financial source on `8585`.

---

## System Overview

The forecast dashboard is the planning surface that sits next to the financial dashboard on `8585`. It keeps the full interactive reallocation model in the Financial Advisor repo, layers in live household facts and mutable checklist state from local runtime storage, and starts eligible scenarios from the latest reconciled source baseline.

Primary URLs:

| URL | Purpose |
|-----|---------|
| `http://dylans-mac-mini:8586/` | Root entrypoint; redirects to the Balanced interactive dashboard |
| `http://dylans-mac-mini:8586/presets` | Preset index page |
| `http://dylans-mac-mini:8586/balanced` | Balanced preset redirect |
| `http://dylans-mac-mini:8586/reallocation-dashboard.html?preset=balanced` | Canonical Balanced dashboard URL |

What it is for:

- Interactive scenario modeling for Dylan and Julia's household allocation plan
- Reconciled live portfolio, cash-flow, and mortgage inputs when source coverage is complete
- Reconciled display context for current net worth, monthly cash flow, and selected market prices
- Current-source health/warning surface for upstream `8585` data gaps
- Monthly operating checklist with mutable dashboard-only state (`done`, `skipped`, `snoozed`) that does not rewrite the planning feed

---

## Architecture

```text
Mac Mini (dylans-mac-mini)
├── Repo: ~/repos/Financial Advisor/
│   ├── dashboard/reallocation-dashboard.html
│   ├── dashboard/reallocation-dashboard.js
│   ├── dashboard/reallocation-dashboard.css
│   ├── dashboard/serve_forecast_dashboard.py
│   └── data/dashboard/monthly-operating-tasks.json
├── Runtime cache: ~/.openclaw/forecast-dashboard/
│   ├── current-snapshot.json
│   └── monthly-operating-task-status.json
├── Upstream finance dashboard: http://127.0.0.1:8585
└── Logs: ~/.openclaw/logs/forecast-dashboard.{log,err.log}
```

### LaunchAgent

| Label | Type | Command | Logs |
|-------|------|---------|------|
| `ai.openclaw.forecast-dashboard` | KeepAlive | `python3 dashboard/serve_forecast_dashboard.py` | `~/.openclaw/logs/forecast-dashboard.{log,err.log}` |

This service runs only on the Mac Mini as user `dbochman`.

---

## Service Shape

The service is intentionally repo-owned and small. It serves only the forecast dashboard assets and APIs, not the full Financial Advisor repository tree.

### Routes

| Route | Purpose |
|-------|---------|
| `/` | Redirect to Balanced preset |
| `/presets` | Preset index page |
| `/balanced` | Redirect to Balanced preset |
| `/tax-reserve` | Redirect to Tax reserve preset |
| `/loss-bank` | Redirect to Loss-bank deployment preset |
| `/liquidity` | Redirect to Liquidity preset |
| `/cabin5` | Redirect to 5-year cabin payoff preset |
| `/nvda-stress` | Redirect to NVDA stress preset |
| `/conservative` | Redirect to Conservative preset |
| `/growth` | Redirect to Growth preset |
| `/reallocation-dashboard.html` | Interactive forecast dashboard shell |
| `/reallocation-dashboard.js` | Forecast model and checklist UI logic |
| `/reallocation-dashboard.css` | Dashboard styles |
| `/api/current-snapshot` | Current household facts derived from local finance APIs |
| `/api/prices` | Supported crypto prices, gold/silver spot quotes, and configured ticker prices |
| `/api/monthly-operating-tasks` | Read-only planning feed enriched with local mutable UI state |
| `/api/monthly-operating-task-status` | Mutable local status overlay write/read endpoint |
| `/api/health` | Service health, source warnings, task-feed availability |

### Presets

The preset index and redirect routes currently expose:

- Balanced
- Tax reserve
- Loss-bank deployment
- Liquidity
- Cabin 5yr
- NVDA stress
- Conservative
- Growth

---

## Data Sources

| Source | Frequency | Data |
|--------|-----------|------|
| Financial dashboard API (`8585`) | Daily source sync + 5 min forecast cache | Mortgage, payroll detail when available, recognized income, spending, net worth, savings rate, and `/api/forecast-baseline`; live projection inputs require reconciliation, income-review, ownership, and coverage readiness |
| Public price APIs | 5 min cache | Supported crypto, USD/troy-ounce gold and silver spot quotes, and tracked tickers such as `NVDA` |
| Financial Advisor repo | Static / git pull | Forecast assumptions, preset logic, dashboard UI, monthly task feed |
| Local runtime overlay | On write | Mutable checklist status state outside source control |

The finance API base probes `http://127.0.0.1:8585` first, then `http://dylans-mac-mini:8585` as fallback.

### Crypto Coverage Fallback

Crypto owner coverage normally requires a fresh synced exchange or wallet source. If a known credential is mismatched or temporarily unavailable, add reviewed, dated `symbol` and `quantity` entries to `~/.openclaw/forecast-dashboard/crypto-manual-values.json` with `model_coverage: true`. Their quantities are live-priced while the dashboard preserves the statement as-of date; static `value_usd` entries remain manual valuations for assets without a live price. Set `coinbase_enabled: false` in the local `crypto-sync-config.json` during the repair so the mismatched account is not counted or allowed to overwrite the statement fallback.

---

### Physical Precious Metals

`~/.openclaw/forecast-dashboard/household-manual-assets.json` remains local and mode `0600`. A physical-metal asset uses documented weights rather than a stale manual dollar value:

```json
{
  "kind": "physical",
  "label": "Physical precious metals",
  "metal_holdings": [
    { "metal": "gold", "grams": 123.45 },
    { "metal": "silver", "grams": 678.9 }
  ],
  "as_of": "YYYY-MM-DD",
  "status": "documented"
}
```

The server converts grams with `31.1034768` grams per troy ounce and uses its five-minute public XAU/XAG USD quote. It never calls `op` and needs no LaunchAgent secret. If a configured quote is unavailable, Household net worth stays partial instead of using zero or a stale price. This is a spot-value estimate: dealer premiums, bid/ask spreads, storage, tax, and liquidation constraints are excluded.

---

## Current Snapshot Model

`/api/current-snapshot` merges current facts used to annotate and seed the forecast:

- Mortgage balances and payment data from `/api/mortgage/summary`
- Latest reconciled net worth from `/api/net-worth`
- Latest reconciled monthly income, expenses, and savings rate from `/api/savings-rate`
- A validated `projection_baseline` from `/api/forecast-baseline`, including source scope readiness, live equity/bond/cash buckets, trailing-full-month recognized cash flow, and source-review blockers
- ETH price from CoinGecko
- Gold and silver spot prices from GoldPrice.org, returned as USD per troy ounce
- Tracked public tickers from Nasdaq where available
- Source warnings when upstream financial APIs are empty or degraded

Payroll data may remain unavailable and surface as a warning. Treat the service as healthy when:

- `/api/health` returns `ok`
- the monthly task feed is available
- mortgage data flows through `/api/current-snapshot`
- reconciled financial totals appear in `/api/current-snapshot` when their upstream APIs are populated

### Live Projection Application

The browser applies only the parts of the model that have a complete, reconciled source contract:

- Source-backed equity, fixed-income, and cash sleeves seed the starting portfolio and its initial allocation.
- Three trailing complete months of recognized source cash flow seed annual savings and annual expenses. The current partial month remains display context. This is net bank cash flow, not gross payroll, withholding, benefits, or compensation-event detail.
- Current mortgage balances seed the Combined scope; the existing individual-scope 50/50 mortgage convention is retained for Dylan and Julia views.
- Crypto/art, unvested equity compensation, salaries, retirement years, home equity, tax assumptions, and other planning inputs remain explicit model assumptions unless separately sourced.

Ownership is intentional:

- Combined composes direct owner source data with the household source once.
- A source-unavailable owner keeps that owner's static model supplement. It is never silently converted to a zero balance or zero cash flow.
- A reconciliation, income-source, or coverage `review` blocks automatic promotion rather than guessing at a portfolio composition. Pending `INCOME_*` deposits are intentionally withheld until a local source rule classifies them.

Manual control remains available. A URL parameter or direct edit to a live-managed field pins that field; **Reset** clears those pins and re-applies the current source. A preset that explicitly defines a managed input takes precedence for that input.

The baseline is refreshed from the `8586` current-snapshot cache every five minutes after `8585` has data. The Plaid source sync is scheduled daily, so this is a current-day planning baseline, not an intraday account-balance feed.

The live net-worth card and current-month cash-flow card remain display context. Only the separate reconciled `projection_baseline` may change eligible starting portfolio, annual cash-flow, and mortgage inputs; it never overwrites salary, compensation, tax, or other unsourced scenario assumptions.

---

## Monthly Checklist Overlay

The planning feed lives in the Financial Advisor repo and remains read-only:

- `~/repos/Financial Advisor/data/dashboard/monthly-operating-tasks.json`

Mutable UI state lives outside the repo:

- `~/.openclaw/forecast-dashboard/monthly-operating-task-status.json`

That overlay stores dashboard-only state such as:

- status
- completion / skipped metadata
- snooze metadata
- note / updated-at / updated-by fields

### Status Cycle

The dashboard cycles each task through:

1. Base planning status (`open` or `blocked`)
2. `done`
3. `skipped`
4. `snoozed`
5. Back to base planning status

The dashboard UI renders explicit state glyphs so the control reads as stateful rather than as an empty square.

### API Contract

| Endpoint | Method | Purpose |
|----------|--------|---------|
| `/api/monthly-operating-tasks` | `GET` | Planning feed with overlay merged in |
| `/api/monthly-operating-task-status` | `GET` | Raw mutable overlay |
| `/api/monthly-operating-task-status` | `POST` | Cycle or set task state |

Expected write shape:

```json
{
  "taskId": "2026-06-combined-source-balance-sheet",
  "action": "cycle"
}
```

---

## Browser / Client Behavior

There are three useful ways to load the dashboard:

1. Load it directly from `8586`
2. Open the static HTML file from the Financial Advisor repo
3. Host the static dashboard elsewhere and point it at the forecast API

Client behavior:

- When hosted from `8586`, it uses same-origin forecast APIs
- When hosted from the financial dashboard on `8585`, it automatically targets `http://<same-host>:8586`
- Other clients can set `?apiBase=http://dylans-mac-mini:8586` or `?forecastApiBase=http://dylans-mac-mini:8586`
- The chosen forecast API base is persisted in browser local storage

Cross-origin writes are allowed only when the origin hostname matches the forecast server hostname, or when the origin is explicitly listed in `FORECAST_ALLOWED_ORIGINS`.

---

## Files

| File | Path | Purpose |
|------|------|---------|
| Server | `~/repos/Financial Advisor/dashboard/serve_forecast_dashboard.py` | HTTP server, redirects, APIs |
| Dashboard HTML | `~/repos/Financial Advisor/dashboard/reallocation-dashboard.html` | Interactive UI shell |
| Dashboard JS | `~/repos/Financial Advisor/dashboard/reallocation-dashboard.js` | Model, rendering, checklist behavior |
| Dashboard CSS | `~/repos/Financial Advisor/dashboard/reallocation-dashboard.css` | Layout and state styling |
| Task feed | `~/repos/Financial Advisor/data/dashboard/monthly-operating-tasks.json` | Read-only planning feed |
| LaunchAgent | `openclaw/launchagents/ai.openclaw.forecast-dashboard.plist` | KeepAlive service |
| Runtime cache | `~/.openclaw/forecast-dashboard/current-snapshot.json` | Cached snapshot payload |
| Runtime overlay | `~/.openclaw/forecast-dashboard/monthly-operating-task-status.json` | Mutable task state |

Related reference:

- `~/repos/Financial Advisor/docs/plans/Forecast_Dashboard_Mac_Mini_Hosting_Plan.md`

---

## Update Workflow

### Dashboard code changes

```bash
ssh dylans-mac-mini 'git -C "$HOME/repos/Financial Advisor" pull --ff-only'
ssh dylans-mac-mini 'launchctl kickstart -k "gui/$(id -u)/ai.openclaw.forecast-dashboard"'
```

### LaunchAgent changes

```bash
cd ~/dotfiles
git pull
plutil -lint ~/dotfiles/openclaw/launchagents/ai.openclaw.forecast-dashboard.plist
cp ~/dotfiles/openclaw/launchagents/ai.openclaw.forecast-dashboard.plist ~/Library/LaunchAgents/
launchctl bootout gui/$(id -u) ~/Library/LaunchAgents/ai.openclaw.forecast-dashboard.plist 2>/dev/null || true
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/ai.openclaw.forecast-dashboard.plist
```

Ownership split:

- Financial Advisor repo owns the dashboard server, assets, and task feed
- Dotfiles owns the LaunchAgent and OpenClaw-side dashboard documentation

---

## Validation

### Service checks

```bash
ssh dylans-mac-mini 'launchctl print gui/$(id -u)/ai.openclaw.forecast-dashboard'
ssh dylans-mac-mini 'tail -50 ~/.openclaw/logs/forecast-dashboard.log'
ssh dylans-mac-mini 'tail -50 ~/.openclaw/logs/forecast-dashboard.err.log'
```

### HTTP checks

```bash
curl -I http://dylans-mac-mini:8586/
curl -I http://dylans-mac-mini:8586/presets
curl -I http://dylans-mac-mini:8586/balanced
curl -fsS -o /dev/null -w 'health HTTP %{http_code}\n' http://dylans-mac-mini:8586/api/health
curl -fsS -o /dev/null -w 'snapshot HTTP %{http_code}\n' http://dylans-mac-mini:8586/api/current-snapshot
curl -fsS -o /dev/null -w 'task feed HTTP %{http_code}\n' http://dylans-mac-mini:8586/api/monthly-operating-tasks
curl -fsS -o /dev/null -w 'task status HTTP %{http_code}\n' http://dylans-mac-mini:8586/api/monthly-operating-task-status
curl -fsS -o /dev/null -w 'forecast baseline HTTP %{http_code}\n' http://dylans-mac-mini:8585/api/forecast-baseline
```

### Browser checks

- Root URL redirects to the Balanced interactive dashboard
- `/presets` loads the preset index page
- Balanced preset loads the full interactive forecast dashboard
- The Projection baseline card reports `Applied` only for source-backed inputs and describes any retained model supplement
- Monthly operating checklist renders for the current month
- Checklist state cycles and persists across refreshes
- Current-snapshot warnings render without blocking the page

---

## Troubleshooting

### Root URL shows preset cards instead of the interactive dashboard

The server route for `/` regressed. Root should redirect to:

```text
/reallocation-dashboard.html?preset=balanced
```

`/presets` is the only route that should render the preset index page directly.

### Checklist toggles do not persist

Check:

- `~/.openclaw/forecast-dashboard/monthly-operating-task-status.json` exists and is writable
- `/api/monthly-operating-task-status` returns `200`
- the browser is loading from `8586` directly or has the correct `apiBase`

### Snapshot looks degraded

Check upstream integrations first:

```bash
curl -fsS -o /dev/null -w 'mortgage HTTP %{http_code}\n' http://127.0.0.1:8585/api/mortgage/summary
curl -fsS -o /dev/null -w 'forecast baseline HTTP %{http_code}\n' http://127.0.0.1:8585/api/forecast-baseline
curl -fsS -o /dev/null -w 'health HTTP %{http_code}\n' http://127.0.0.1:8586/api/health
```

The known-empty financial APIs are expected warnings, not necessarily breakages. If `projection_baseline` is `review` or a scope is `unavailable`, the dashboard should retain its fixed model for that input and state why in the Projection baseline card; do not bypass the gate by copying raw source values into the forecast.

### Browser shows a CSP / eval issue

The forecast dashboard does not intentionally use `eval` or `new Function` in repo-owned code. If DevTools reports a CSP/eval issue while the dashboard still loads, treat it as browser-side noise unless the server is actually sending a CSP header or script execution is failing.

---

## Security Notes

- Tailscale-only access is the intended exposure model
- The dashboard contains sensitive planning assumptions, household balances, allocation targets, and compensation-related planning inputs
- Do not expose `source-documents/`, raw research exports, PDFs, or other repo contents through the forecast server
- Bind-on-all-interfaces is acceptable only because the Mac Mini is not publicly forwarded and this matches the other dashboard services
