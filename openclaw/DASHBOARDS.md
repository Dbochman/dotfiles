# OpenClaw Dashboards

All dashboards run on Mac Mini (`dylans-mac-mini`) as KeepAlive LaunchAgents. They bind to all local interfaces and are reachable from the home LAN and the Tailscale tailnet; no public-internet exposure or router port forwarding is intended.

## Quick Reference

| Port | Dashboard | URL | Data Refresh |
|------|-----------|-----|-------------|
| 8550 | [Nest Climate](#nest-climate-dashboard) | http://dylans-mac-mini:8550 | 5 min (UI/Airthings) · 30 min (HVAC) |
| 8551 | [OpenClaw Usage](#openclaw-usage-dashboard) | http://dylans-mac-mini:8551 | 5 min (UI) · 15 min (snapshots) |
| 8552 | [Dog Walk](#dog-walk-dashboard) | http://dylans-mac-mini:8552 | 5 min (UI) · event-driven (JSONL) |
| 8553 | [Roomba](#roomba-dashboard) | http://dylans-mac-mini:8553 | 5 min (UI) · 15 min (Cabin Assistant) · event-driven (JSONL) |
| 8554 | [Cat Care](#cat-care-dashboard) | http://dylans-mac-mini:8554 | 60s cache · Whisker/Petlibro/event bus on demand |
| 8558 | [Home Control Plane](#home-control-plane-dashboard) | http://dylans-mac-mini:8558 | 60s cache · 5 min background refresh |
| 8585 | [Financial](#financial-dashboard) | http://dylans-mac-mini:8585 | Daily unified finance refresh at 06:15 + weekly scrapes · API on demand |
| 8586 | [Forecast](#forecast-dashboard) | http://dylans-mac-mini:8586 | 5 min snapshot and market prices · crypto in 06:15 finance refresh · aggregate ledger capture at 07:35 |

---

## Nest Climate Dashboard

**Port 8550** · [Full spec](NEST-CLIMATE-DASHBOARD.md)

Monitors thermostats, air conditioners, indoor air quality, and weather across
two locations via four heating/cooling systems and one local Wave Enhance.

### What It Shows

- **Vacancy cards** — canonical occupancy per location (Occupied / Confirmed Vacant / Possibly Vacant), with only Dylan and Julia shown
- **Temperature cards** — current temp, setpoint, HVAC mode, humidity per room
- **Mixed-cadence current state** — the newest five-minute Airthings reading
  overlays the latest complete 30-minute HVAC snapshot without hiding any
  thermostat or air-conditioner cards
- **Temperature chart** — line graph with room temps + setpoints + outdoor weather
- **Humidity chart** — per-room humidity over time
- **CO2 and VOC charts** — Cabin Living Room Wave Enhance trends in ppm and ppb
- **HVAC Duty Cycle** — hourly Active % bar chart using real Mysa duty,
  Midea fan percentage while active, and binary activity for Nest/Cielo;
  Airthings is excluded

### Data Sources

| Source | Frequency | Data |
|--------|-----------|------|
| Nest SDM API | 30 min | Thermostat temps, setpoints, HVAC mode |
| Cielo CLI | 30 min | Minisplit AC status (Crosstown) |
| Mysa API | 30 min | Baseboard heater temps + duty cycle (Crosstown) |
| Midea local LAN | 30 min | Portable/window AC status, setpoints, and energy telemetry (Cabin) |
| Airthings local BLE + attended CSV history | 5 min dedicated sampler | Wave Enhance CO2, VOC, temperature, humidity, pressure, noise, light, and battery (Cabin); idempotent UTC CSV backfills share the same room series |
| Open-Meteo | 30 min | Outdoor weather (no API key needed) |
| Presence scanner | Continuous | WiFi-based occupancy per location |

### Locations & Rooms

| Location | System | Rooms |
|----------|--------|-------|
| Cabin (Philly) | Nest central HVAC | Solarium, Living Room, Bedroom |
| Crosstown (19Crosstown) | Cielo minisplit | Living Room, Basement, Dylan's Office, Bedroom |
| Crosstown (19Crosstown) | Mysa baseboard | Cat Room, Basement door, Movie room |
| Cabin (Philly) | Midea AC | Air Conditioner, Lil Air Conditioner |
| Cabin (Philly) | Airthings Wave Enhance | Living Room |

### Files

| File | Path |
|------|------|
| Server | `openclaw/bin/nest-dashboard.py` → `~/.openclaw/bin/nest-dashboard.py` |
| LaunchAgent | `openclaw/launchagents/ai.openclaw.nest-dashboard.plist` |
| Data | `~/.openclaw/nest-history/YYYY-MM-DD.jsonl` |
| Airthings sampler health | `~/.openclaw/airthings/snapshot-status.json` |
| Presence | `~/.openclaw/presence/state.json` + `history/` |
| Logs | `~/.openclaw/logs/nest-dashboard.{log,err.log}` |

---

## OpenClaw Usage Dashboard

**Port 8551** · [Full spec](USAGE-DASHBOARD.md)

Tracks OpenClaw session activity, token consumption and costs alongside Anthropic utilization and Codex CLI usage.

### What It Shows

- **Utilization gauges** — 5-hour and 7-day token usage rings (green/amber/red thresholds)
- **Stat cards** — total cost, all-session total tokens, cron runs, messages sent/received, sessions, errors, gateway restarts
- **Native iMessage Health** — live OpenClaw channel and `imsg` bridge readiness, configured typing/read-receipt behavior, latest outbound delivery, and privacy-safe seven-day direct-response latency
- **Token Usage Over Time** — stacked daily OpenClaw all-session/Codex CLI bars
- **Activity chart** — sent/received/cron messages over time
- **Cost Over Time** — aggregate daily cost, with a component breakdown when supplied
- **Model Split** — doughnut chart of date-scoped per-model token usage
- **Tool Usage** — horizontal bar of most-used tools
- **Recent Cron Runs** — table with status badges, duration, token counts

### Data Sources

| Source | Frequency | Data |
|--------|-----------|------|
| Anthropic Usage API | 15 min | 5h/7d utilization percentages |
| Usage snapshots | 15 min | Per-model token counts, costs, cache stats |
| Gateway RPC | 5 min cache/UI refresh | Session data (tool calls, costs, latency) |
| SQLite `cron_run_logs` | 15 min cursor | Job ID, status, duration, model, delivery, and tokens |
| Local Messages database | 15 min | Native iMessage send/receive counts |
| Native iMessage health probes | 60 sec | Gateway health, configured typing/read-receipt behavior, attached `imsg rpc` worker, basic/advanced/v2 readiness, latest outbound delivery, and direct-response latest/median/p95 plus slow/open counts |
| ccusage push | 30 min | Codex CLI daily token usage (from Mini and MacBook) |

### Files

| File | Path |
|------|------|
| Server | `openclaw/bin/usage-dashboard.py` → `~/.openclaw/bin/usage-dashboard.py` |
| LaunchAgent | `openclaw/launchagents/ai.openclaw.usage-dashboard.plist` |
| Data | `~/.openclaw/usage-history/YYYY-MM-DD.jsonl` |
| Codex CLI data | `~/.openclaw/usage-history/ccusage-codex-<hostname>.json` |
| Logs | `~/.openclaw/logs/usage-dashboard.{log,err.log}` |

---

## Dog Walk Dashboard

**Port 8552** · [Full spec](DOG-WALK-DASHBOARD.md)

Visualizes dog walk departures, return signal detection, route maps, and the Fi departure pipeline.

### What It Shows

- **Status cards** — today's distance/duration, departure candidate, return monitor, and selected-route summary
- **Potato (Fi collar) cards** — battery %, activity (Rest/Walk), GPS location, connection type, Fi base station status
- **Walk map** — three layer modes: Routes (single-walk selection), Coverage (all walks at full weight with date range picker), Heatmap (density)
- **Recent Walks table** — date, location, distance, duration, return signal badge, walkers
- **Walk Duration** — scatter chart by location over time
- **Return Signal Distribution** — doughnut (WiFi / Ring Motion / Fi GPS / Timeout)
- **Departure Pipeline** — horizontal bar showing first outside reads, candidate resets, Fi departures, manual starts, and completed walks
- **Walks per Day** — daily bar chart for trend analysis

### Data Sources

| Source | Frequency | Data |
|--------|-----------|------|
| Dog Walk Listener | Event-driven | Fi GPS departure detection, return monitoring, dock lifecycle |
| Fi GPS Collar | 2 min cache | Potato GPS, battery, activity, connection, geofence |
| Network presence | Per walk + 30s return polling | WiFi scans (Starlink gRPC for cabin, ARP for crosstown) |
| `dog-walk-start` CLI | Manual trigger | Inbox IPC to dog-walk listener |

### Files

| File | Path |
|------|------|
| Server | `openclaw/bin/dog-walk-dashboard.py` → `~/.openclaw/bin/dog-walk-dashboard.py` |
| Fi collar API | `openclaw/skills/fi-collar/fi-api.py` → `~/.openclaw/skills/fi-collar/fi-api.py` |
| LaunchAgent | `openclaw/launchagents/ai.openclaw.dog-walk-dashboard.plist` |
| Event history | `~/.openclaw/dog-walk/history/YYYY-MM-DD.jsonl` |
| Current state | `~/.openclaw/dog-walk/state.json` |
| Logs | `~/.openclaw/logs/dog-walk-dashboard.{log,err.log}` |

---

## Roomba Dashboard

**Port 8553** · [Full spec](ROOMBA-DASHBOARD.md)

Two-home Roomba status and automation view with explicit telemetry provenance.

### What It Shows

- **Both/Crosstown/Cabin selector** — side-by-side comparison or a focused
  single-home view
- **Crosstown live-local cards** — battery, phase, bin, tank, and guarded
  vacancy-start readiness through the persistent MBP rest980 services
- **Cabin Assistant-status cards** — current cleaning/stopped state from exact
  read-only Google Assistant queries; ambiguous replies remain unverified
- **Home automation summaries** — verified occupancy, schedule, latest
  protected decision, and any safety hold
- **Automation Pause** — temporarily disable automatic Roomba starts per
  location (1h/3h/8h/Indef)
- **Occupied-home protection** — automatic Fi departure starts fail closed
  unless a complete fresh network observation shows both residents absent
- **Cleaning & Decision History** — monthly dog-walk activity plus protected
  Crosstown 6 AM and vacancy-transition outcomes

### Data Sources

| Source | Frequency | Data |
|--------|-----------|------|
| Crosstown guarded Roomba CLI | 5 min cache | Live local battery, phase, bin, and tank through authenticated rest980 on the MBP |
| Cabin guarded Roomba CLI | 15 min cache | Read-only Google Assistant running/stopped response; no physical command. The longer cache keeps routine status traffic below the daily request quota. |
| Protected canonical presence | On demand | Hash-verified, freshness-bounded occupancy summary without people or raw evidence |
| Crosstown vacancy decisions | On demand | Latest evaluation and owner-only per-day controller outcomes |
| Dog Walk History JSONL | On demand | Roomba start/dock events per walk |
| Automation pause state | Real-time | Per-location pause expiry |

### Locations

| Location | Roombas |
|----------|---------|
| Cabin (Phillipston) | Floomba + Philly (Google Assistant) |
| Crosstown (West Roxbury) | Roomba Combo 10 Max + J5 (persistent rest980 MQTT) |

### Files

| File | Path |
|------|------|
| Server | `openclaw/bin/roomba-dashboard.py` → `~/.openclaw/bin/roomba-dashboard.py` |
| Cabin Roomba skill | `openclaw/skills/roomba/` → `~/.openclaw/skills/roomba/` |
| LaunchAgent | `openclaw/launchagents/ai.openclaw.roomba-dashboard.plist` |
| Snooze state | `~/.openclaw/dog-walk/snooze.json` |
| Run history | `~/.openclaw/dog-walk/history/YYYY-MM-DD.jsonl` |
| Daily decisions | `~/.openclaw/vacant-roomba/crosstown/runs/YYYY-MM-DD.json` |
| Latest decision | `~/.openclaw/vacant-roomba/crosstown/latest-status.json` |
| Logs | `~/.openclaw/logs/roomba-dashboard.{log,err.log}` |

---

## Cat Care Dashboard

**Port 8554** · [Full spec](CAT-DASHBOARD.md)

A cat-specific care view across both homes, combining Whisker Litter-Robots
with Petlibro feeders and fountains.

### What It Shows

- **Cat profiles** — current Whisker weight, recent direction, and a compact
  weight-over-time chart
- **Feeding between homes** — a plain-English current state (`Cats are at
  Cabin`, `Both homes are ready`, or an explicit checking/review state), exact
  schedule readback for each home, litter-box freshness, and waiting changes
- **Whisker cards** — one exact enrolled robot per home, including online
  state, waste, litter, cycle count, and a guarded clean action
- **Petlibro cards** — live feeder and fountain telemetry, exact provider-
  verified master schedule state plus effective active-meal count and
  observation time, guarded pause/resume, and guarded 1–3 portion manual
  feeding when an enrolled device is reporting; OpenClaw-owned vacancy pauses
  are marked for automatic resume and block conflicting manual toggles
- **Cat activity** — one combined, location-filterable timeline of named and
  weighted litter visits, provider-confirmed scheduled feedings with actual
  portions, and confirmed vacancy-driven moves between homes; low-level
  Litter-Robot sensor and cleaning records are collapsed out
- **Attention state** — integration errors, offline robots, and waste drawers
  that are full or approaching full, plus stale transfer evidence or unknown
  feeder outcomes

### Files

| File | Path |
|------|------|
| Server | `openclaw/bin/cat-dashboard.py` → `~/.openclaw/bin/cat-dashboard.py` |
| LaunchAgent | `openclaw/launchagents/ai.openclaw.cat-dashboard.plist` |
| Whisker skill | `openclaw/skills/litter-robot/` |
| Petlibro skill | `openclaw/skills/petlibro/` |
| Logs | `~/.openclaw/logs/cat-dashboard.{log,err.log}` |

---

## Financial Dashboard

**Port 8585** · [Full spec](FINANCIAL-DASHBOARD.md)

Household financial dashboard tracking spending, income, net worth, utilities, reconciled Plaid sources, and the canonical source baseline consumed by Forecast.

### What It Shows

- **Main dashboard** — spending trends, recognized income streams, net worth, savings rate, FIRE progress
- **Utilities — Electricity** — Eversource bills, year-over-year comparison
- **Utilities — Gas** — National Grid bills, year-over-year comparison
- **Utilities — Water** — BWSC bills, year-over-year comparison
- **Mortgage** — amortization schedule, payment history
- **Expenses** — category breakdown, trends, top merchants
- **Forecast baseline** — owner-aware canonical portfolio, spendable-versus-retirement/restricted cash split, account location/institution/direct-position concentration, live equity geography, and trailing-full-month cash-flow confidence for `8586`

### Data Sources

| Source | Frequency | Data |
|--------|-----------|------|
| SQLite database | On demand | Canonical transactions, categories, balances, holdings, and reconciliation state |
| Plaid API | Daily 06:15 cache-only finance refresh | Bank/credit-card transactions, depository balances, investment holdings, PFC income review, and source status |
| Weekly HTTP-first scraper cron | Sundays 04:05 ET | Contract-v2 guarded utilities, mortgage, solar, and other statement-shaped data; browser fallback is degraded |
| Config YAML | Static | Category overrides, FIRE settings, utility accounts |

### Files

| File | Path |
|------|------|
| Server | `~/repos/financial-dashboard/serve_dashboard.py` (separate repo) |
| LaunchAgent | `openclaw/launchagents/ai.openclaw.financial-dashboard.plist` |
| Runtime | `~/repos/financial-dashboard/venv/bin/python3` |
| Database | `~/repos/financial-dashboard/finance.db` (gitignored) |
| Config | `~/repos/financial-dashboard/config.yaml` |
| Weekly scrape status | `~/.openclaw/financial-dashboard/weekly-scrape-status.json` (owner-only mode `0600`); attended `weekly-financial-scrape.py --recover-source boa` replaces a resolved stale BoA result |
| Weekly alert outbox | `~/.openclaw/financial-dashboard/weekly-scrape-alerts/` (directory `0700`, atomic records `0600`) |
| Weekly alert quarantine | `~/.openclaw/financial-dashboard/weekly-scrape-alerts-quarantine/` (owner-only directory `0700`) |
| Weekly alert notifier health | `~/.openclaw/financial-dashboard/weekly-scrape-alert-notifier-status.json` (owner-only mode `0600`) |
| Logs | `~/.openclaw/logs/financial-dashboard.{log,err.log}` |

### Runtime Notes

This service runs only on the Mac Mini as user `dbochman`. The venv must be built from an installed Homebrew Python, not the Command Line Tools Python shim. Because Homebrew removes versioned formula paths during upgrades, verify that `~/repos/financial-dashboard/venv/bin/python3` resolves before restarting the LaunchAgent; rebuild the venv from `/opt/homebrew/bin/python3` if its interpreter symlink is stale. The environment was rebuilt and verified on 2026-06-27 with Python 3.14.3, OpenSSL 3.6.2, declared Playwright support, and a working Chromium runtime.

The Sunday wrapper verifies `FINANCE_SCRAPER_CONTRACT 2` plus the exact compact seven-source capability manifest before credentials, browser startup, or data work. It pins every merge to `--wrapper-contract 2`, gives one run ID to every normal scraper and guarded import, and requires one exact allowlisted `FINANCE_SCRAPER_STATUS` marker from every successful source. Invalid markers skip import. A validated browser fallback can import but produces a durable `degraded` status and nonzero exit. Every nonhealthy final status attempts a protected per-run alert handoff and records whether it persisted or failed; healthy runs do not enqueue alerts. The weekly wrapper and separate 15-minute notifier are exact-argv command cron jobs whose nonzero/timeout/output-bound failures reach job-level alerts. The notifier uses one bounded, bridge-required `imsg rpc` send, marks confirmed records sent before cleanup, quarantines invalid entries without blocking valid alerts, and retains failed sends with bounded backoff without rerunning financial work. A rare send-before-sent-state crash remains explicitly at-least-once.

The forecast-baseline API is the primary integration check for downstream forecast work. It reports source readiness, aggregate owner scopes, broad allocation, a cash split, account location/institution totals, direct-position concentration, location-scoped U.S./international equity geography, a safe deployable-only instrument aggregate, and cash-flow confidence without requiring the forecast service to query SQLite directly. `cash_breakdown.spendable` is depository plus taxable brokerage cash; retirement and restricted cash stay visible but must not be treated as tax, emergency, or mortgage liquidity. `implementation_holdings` contains only grouped ticker/name, bucket, geography, value, and direct-position fields; it excludes account and institution details and is withheld unless it exactly reconciles to the deployable matrices. Location and concentration are withheld if they do not reconcile to the covered scope. A pending income-source candidate is a readiness blocker: it is held out of cash-flow totals until reviewed, rather than silently changing the Forecast input. A partial, invalid, or direct-position-excluded deployable geography withholds country equity trade guidance. The detailed policy and local review commands live in [FINANCIAL-DASHBOARD.md](FINANCIAL-DASHBOARD.md#income-source-quality).

```bash
ssh dylans-mac-mini 'curl -fsS -o /dev/null -w "forecast-baseline HTTP %{http_code}\n" http://127.0.0.1:8585/api/forecast-baseline'
```

---

## Forecast Dashboard

**Port 8586** · [Full spec](FORECAST-DASHBOARD.md)

Financial Advisor forecast dashboard for Dylan and Julia's household reallocation scenarios. Root now redirects to the Balanced interactive dashboard, while `/presets` serves the preset index page.

### What It Shows

- **Interactive forecast model** — full reallocation dashboard with presets, controls, and projections
- **Live projection baseline** — source-backed starting portfolio, allocation, mortgage balances, and trailing-full-month cash-flow calibration context when coverage is ready
- **Target Mix Details** — current broad mix plus live spendable-cash, retirement/restricted-cash, deployable U.S./international geography, and fungible/manual crypto-art categories; country or manual-value trade guidance is visibly review-only when gated
- **Asset Location & Concentration** — reconciled tax/access location, institution exposure, and direct-stock review signals; Combined withholds mixed live/static scope data
- **Rebalance Execution** — material live sleeve drift prioritized against the scenario target; direct-position signals are review items, not automatic orders
- **Input provenance** — each control distinguishes live data, a manual override, or a planning assumption; cash-flow confidence keeps net Plaid flow separate from payroll-detail assumptions
- **Forecast history** — aggregate daily observations plus explicit saved browser scenarios; annual checkpoint comparisons avoid invented daily forecast precision
- **Current snapshot overlay** — compact live cards for household net worth, income, spendable cash, mortgage debt, and crypto; supporting source detail appears on hover or keyboard focus
- **Monthly operating checklist** — current-month planning tasks with mutable `done` / `skipped` / `snoozed` dashboard state
- **Stale-data warnings** — source status for current snapshot inputs

### Runtime Notes

This service runs only on the Mac Mini as user `dbochman`. It reads current household data from the financial dashboard API through `http://127.0.0.1:8585` first, with `http://dylans-mac-mini:8585` as fallback. It refreshes its snapshot and public market prices every five minutes; the underlying Plaid source sync is daily, so the projection is current-day planning data rather than an intraday balance stream.

The unified `ai.openclaw.finance-refresh` LaunchAgent refreshes the non-secret crypto holdings cache after Plaid in the same daily 06:15 run. It writes `~/.openclaw/forecast-dashboard/crypto-holdings.json` from protected local Coinbase and Etherscan credentials, then the forecast service values those quantities with public market prices. The forecast applies the crypto input only when both owners are covered, all tracked positions are priceable, and every synced source is no more than 36 hours old. A dated, reviewed manual statement can explicitly provide temporary owner coverage with `model_coverage: true` while a credential is repaired. If a synced source for that owner remains present, the manual token quantity must declare `replaces_source_id`; use `independent: true` only for a separate asset. Manual `symbol` and `quantity` entries are live-priced, with the statement date retained as the quantity source; static `value_usd` entries remain manual valuations. Otherwise the dashboard retains the fixed model baseline. The scheduled job never calls `op`.

`ai.openclaw.forecast-ledger-capture` runs daily at 07:35 after the Plaid and crypto jobs. It asks `8586` to persist one immutable, aggregate observation in `~/.openclaw/forecast-dashboard/forecast-ledger.sqlite`; it never reads `finance.db`, calls `op`, or writes raw account/transaction data. Browser `Save forecast` actions persist the current annual model output, scenario state, provenance, seed, and model revision against that observation. The History view compares only due annual checkpoints: real-dollar investable value uses the saved inflation assumption, mortgage comparison is direct, and Household net worth remains display-only because manual physical assets are outside the model.

`Household net worth` is a Forecast-owned aggregate, not a replacement for the canonical Plaid `8585 /api/net-worth` contract. It adds eligible live crypto, provenance-marked Redfin values less current mortgage balances, and documented manual physical assets from `~/.openclaw/forecast-dashboard/household-manual-assets.json`. The protected manual property values remain fallbacks; an overdue fallback review retains the known value but makes the aggregate partial. Update a fallback with `dashboard/update_manual_property_value.py` in the Financial Advisor repo. The compact hover ledger shows an exact USD value and source for cash, investments, any financial reconciliation adjustment, crypto, property equity, and Precious metals. Documented gold and silver grams are live-valued from a five-minute USD/troy-ounce quote and intentionally collapse into the Precious metals total. Only present balance-sheet assets appear in the ledger. A pending manual entry or unavailable required metal quote produces a `+` known subtotal rather than silently adding zero. The metal value is spot only and excludes dealer premiums and sale spreads.

Combined uses each household source once. An owner with incomplete or unavailable source coverage retains the existing model supplement rather than being silently zeroed. Account location, institution, concentration, and execution guidance are withheld if that supplement would make the live source total non-reconcilable. The detailed target mix uses only the `8585` deployable geography rows for country allocation, retains a broad equity sleeve when they are unavailable, and exposes live fungible versus manual crypto/art without manufacturing a category target split. See [FORECAST-DASHBOARD.md](FORECAST-DASHBOARD.md) for input, coverage, and override rules.

```bash
ssh dylans-mac-mini 'curl -fsS http://127.0.0.1:8586/api/health'
ssh dylans-mac-mini 'curl -fsS http://127.0.0.1:8586/api/current-snapshot'
ssh dylans-mac-mini 'curl -fsS http://127.0.0.1:8586/api/crypto/positions'
ssh dylans-mac-mini 'curl -fsS http://127.0.0.1:8586/api/household-net-worth'
ssh dylans-mac-mini 'curl -fsS http://127.0.0.1:8586/api/forecast-ledger/summary'
ssh dylans-mac-mini 'curl -fsS http://127.0.0.1:8586/api/monthly-operating-tasks'
```

---

## Home Control Plane Dashboard

**Port 8558** · [Full spec](HOME-CONTROL-PLANE-DASHBOARD.md)

Unified control plane for smart home devices across both locations. Eighteen status collectors provide a single pane of glass for monitoring and control, organized into five collapsible sections.

### Layout

Cards are grouped into collapsible sections (all open by default, click header to collapse):

1. **Lighting** — Hue Crosstown, Hue Cabin
2. **Temperature** — Nest, Midea AC, Cielo, Mysa, Eight Sleep at both homes
3. **Security** — August Lock, Ring Doorbell, Nest Camera
4. **Pets** — Litter-Robot, Petlibro, Dog Walk
5. **Misc** — TV, Speakers, Cabin Speakers, Roombas (Crosstown + Cabin)

Command feedback (Running/Success/Error) appears inline below the section header of the clicked card, not at the top of the page. Success/error auto-dismiss after 4s.

### What It Shows

- **Hue Lights** — room chip cards (ON/OFF indicator, brightness%, color temp label e.g. "Warm White") with on/off, brightness, and color controls, plus standing Hue automation status and exact enable/disable controls (Crosstown: 9 rooms, 7 routines; Cabin: 8 rooms, currently no routines)
- **Nest Thermostat** — per-room temp, setpoint, HVAC mode with set temp / set mode / eco controls (Cabin: 3 rooms)
- **Midea AC** — per-unit temperature, setpoint, power, mode, fan, eco, and live wattage with exact-device on/off, temp, mode, fan, and eco controls (Cabin: 2 units)
- **Cielo AC** — per-unit temp, mode, fan speed with on/off, temp, and mode controls (Crosstown: 4 units)
- **Mysa Heaters** — per-heater temp, setpoint, humidity, duty cycle (read-only; Crosstown: 3 units)
- **Eight Sleep** — separate Crosstown and Cabin Pod cards with connection,
  water, per-side temperature and thermal state, and authoritative Home/Away
  routing for Dylan and Julia. On/off/set-temperature controls carry an exact
  location, disable Away users in the selector, and fail closed when that Pod
  is not current for the selected person.
- **August Lock** — lock state, door state, battery with lock/unlock controls
- **Ring Doorbell** — chip cards per doorbell (battery, last event with relative time) + snapshot capture with Crosstown/Cabin selector
- **Nest Cameras** — live snapshot capture via WebRTC, one card per camera with inline image + relative timestamp. Three cameras across two locations: Kitchen (Cabin/Philly), Laundry (Crosstown — physically in the Garage room in Nest), Living Room (Crosstown). Camera discovery in the `nest` CLI matches against `customName` as a fallback to `parentRelations.displayName` so dashboard labels can diverge from Google Home room names. New devices require `nest reauth` (one-time OAuth re-consent) to become visible to SDM
- **Litter-Robot** — one card per house with status, waste/litter levels, cycle count, and cat weights; exact-device clean and robot-reset controls
- **Petlibro** — chip cards per device (fountain: water level, battery, filter alert; feeder: food level, next feed) with manual feed
- **Dog Walk** — active/inactive status, last walk details (read-only)
- **Samsung TV** — power state with on/off controls; shows friendly "TV is likely off" when unreachable
- **Google Speakers** — volume with set volume / mute / unmute; shows friendly "Speakers are likely asleep" when unreachable
- **Cabin Speakers** — chip cards per speaker (online/asleep status) with set volume / stop (via catt by IP)
- **Roombas** — chip cards per robot (status, battery, bin state) with start/stop/dock (Crosstown: 2 MQTT, Cabin: 2 Google). Cabin status is projected from the dedicated Roomba dashboard's bounded local API, so Assistant failures appear as concise degraded-state cards rather than raw provider output.

### Architecture

```
Browser → home-dashboard.py (port 8558)
            ├── GET /                        → embedded HTML dashboard
            ├── GET /api/status              → cached results (instant, non-blocking)
            ├── GET /api/status?refresh=true → force re-poll all collectors
            ├── GET /api/status/<device>     → refresh single device
            ├── POST /api/command            → execute device command
            ├── GET /api/camera-snap/<name>  → serve JPEG snapshot (nest/ring)
            └── GET /api/presence            → presence state
```

- **Progressive loading** — `GET /api/status` returns cached data instantly (no blocking). Uncached devices listed in `meta.pending`; frontend polls them individually in background. Cards render as data arrives.
- **Precache on startup** — all 18 collectors run in parallel via `ThreadPoolExecutor` at boot
- **Background refresh** — every 5 minutes, most collectors re-run in background; speakers/cabin_speakers are excluded to avoid Cast connections that cause chimes on idle Google Home devices (polled on page load only)
- **Per-device refresh** — `GET /api/status/<device_name>` refreshes one collector and updates cache
- **60s cache TTL** — CLI results cached to avoid hammering APIs
- **30s command timeout** — accommodates slower SSH-based collectors (crosstown roombas, speakers)
- **Secrets loading** — sources `~/.openclaw/.secrets-cache` at startup for CLI env vars (Petlibro, 8sleep, etc.)
- **Custom renderers** — all device categories have dedicated JS renderers with room-chip card layout; TV and Speakers show friendly messages when devices are off/asleep; Hue shows human-readable color temp (Warm White, Daylight, etc.) only when lights are on
- **Camera snapshots** — Nest (WebRTC) and Ring snapshots saved to `~/.openclaw/camera-snaps/`, served via `/api/camera-snap/<name>` with timestamp header; loaded on page refresh
- **Inline feedback** — command status messages appear below the section header of the clicked card, auto-dismiss success/error after 4s
- **Midea verified refresh** — successful Midea controls update the device card and server cache from the CLI's verified readback, avoiding a redundant immediate LAN session

### Controls

All controls use dropdown selectors (not text inputs) with pre-populated room/device lists:

| Device | Room/Device Selector | Extra Controls |
|--------|---------------------|----------------|
| Hue Crosstown | 9 rooms + **All Lights** dropdown; 7 exact standing routines | Brightness, Color, routine Enable/Disable. In **All Lights** mode, brightness/color inputs are disabled; use On/Off for global toggle. The card marks vacancy-managed suspension. |
| Hue Cabin | 8 rooms + **All Lights** dropdown; currently no standing routines | Brightness, Color, and exact routine controls when routines exist. In **All Lights** mode, brightness/color inputs are disabled; use On/Off for global toggle. |
| Nest | 3 rooms dropdown | Temp °F, Mode (HEAT/OFF), Eco on/off |
| Midea AC | 2 exact-device aliases | Temp °F (60–86), Mode (auto/cool/dry/heat/fan), Fan (auto/silent/low/medium/high/full), Eco on/off |
| Cielo | 4 devices dropdown | Temp °F, Mode (cool/heat/auto/dry/fan) |
| Eight Sleep | Location-specific card + side (Dylan/Julia) | Level (-100 to +100), On / Off; writes require the selected person to be Home on that exact Pod |
| August | — | Lock / Unlock |
| Ring Doorbell | Crosstown/Cabin dropdown | Take Snapshot |
| Nest Cameras | One card per camera (Kitchen @ Cabin; Laundry + Living Room @ Crosstown) | Take Snapshot per card |
| Litter-Robot | Crosstown/Cabin cards | Clean / Reset Robot per card |
| Petlibro | — | Feed (portions) |
| Samsung TV | — | Power On / Off |
| Speakers | Speaker selector | Volume, Mute / Unmute |
| Crosstown Roomba | Robot selector | Start / Stop / Dock |
| Cabin Roomba | Robot selector | Start / Stop / Dock |

### Data Sources

| Source | Type | Data |
|--------|------|------|
| `~/.openclaw/presence/state.json` | File | Occupancy per location |
| `hue --crosstown/--cabin status` | CLI | Room-by-room light status |
| `~/.openclaw/nest-history/*.jsonl` | File | Latest Nest snapshot |
| `midea-ac status --json` | CLI | Locally enrolled Cabin AC status and energy telemetry |
| `cielo status --json` | CLI | Minisplit status (JSON) |
| `mysa` | CLI | Baseboard heater status (JSON) |
| `august status` | CLI | Lock state (JSON, via SSH to MBP) |
| `crosstown-roomba status` | CLI | Roomba status via SSH to the MBP's authenticated rest980 services |
| `http://127.0.0.1:8553/api/cabin-roombas` | Local API | Bounded, 15-minute-cached Cabin Roomba Assistant status; Home Control Plane controls still use the guarded `roomba` CLI |
| `samsung-tv status` | CLI | TV power state |
| `speaker status` | CLI | Speaker volume/reachability (page load only; excluded from bg refresh to prevent Cast chimes) |
| `litter-robot --json status` | CLI | Both enrolled LR4 units through protected site bindings |
| `petlibro status` | CLI | Feeder + fountain |
| `8sleep overview` | CLI | Bounded status for both Pods plus exact per-person Home/Away routing |
| `ring status` | CLI | Doorbell battery, motion |
| `ring snapshot <path> [id]` | CLI | Doorbell camera snapshot (JPEG) |
| `nest camera snap-config <alias> <path>` | CLI | Exact-resource Nest camera snapshot via WebRTC (JPEG); generic display-name lookup is attended-only |
| `~/.openclaw/camera-snaps/*.jpg` | File | Cached camera/doorbell snapshots |
| `~/.openclaw/dog-walk/state.json` | File | Walk state |

### Device → Location Mapping

| Device | Crosstown | Cabin |
|--------|-----------|-------|
| Hue Lights | Entryway, Kitchen, Bedroom, Movie, Living, Office, Upstairs, Downstairs, Master | Kitchen, Living, Bathroom, Hallway, Bedroom, Office, Solarium, Staircase |
| Nest | — | Solarium, Living Room, Bedroom |
| Midea AC | — | Air Conditioner, Lil Air Conditioner |
| Cielo AC | Basement, Living Room, Dylan's Office, Bedroom | — |
| Mysa Heaters | Cat Room, Basement door, Movie room | — |
| August Lock | Front Door | — |
| Roombas | 10 Max + J5 (rest980 via MBP) | Floomba + Philly (Google) |
| Samsung TV | Frame 65 | — |
| Google Speakers | Bedroom + Living Room | Kitchen + Bedroom |
| Litter-Robot | LR4 | LR4 |
| Petlibro | Feeder + Fountain | (seasonal, unplugged) |
| Eight Sleep | Pod 3 status + controls | Pod 5 status + controls |
| Ring Doorbell | Front Door (snap + status) | Front Door (snap + status) |
| Nest Camera | Laundry + Living Room (snap) | Kitchen (snap) |
| Dog Walk | Yes | Yes |
| Presence | Yes | Yes |

### Files

| File | Path |
|------|------|
| Server | `openclaw/bin/home-dashboard.py` → `~/.openclaw/bin/home-dashboard.py` |
| LaunchAgent | `openclaw/launchagents/ai.openclaw.home-dashboard.plist` |
| Camera snaps | `~/.openclaw/camera-snaps/*.jpg` |
| Logs | `~/.openclaw/logs/home-dashboard.{log,err.log}` |

### Known Limitations

- **Mysa is read-only** — the Mysa API doesn't expose setpoint changes or on/off; use the Mysa app or physical thermostat
- **Midea is LAN-local** — status and controls require the Mac mini to be on the same Cabin network as the enrolled units; no cloud fallback is retained
- **Cabin Roombas use Google Assistant** — the dedicated Roomba service parses natural-language responses into a bounded local status contract. Failed or quota-limited checks remain unknown rather than exposing provider diagnostics or inferring physical state.
- **Petlibro/8sleep** require env vars from `~/.openclaw/.secrets-cache` — if secrets are stale, these collectors will error
- **Crosstown Roombas and Speakers** route through SSH to MBP — if MBP is
  offline, these time out. Roomba reads and actions then use exact authenticated
  loopback rest980 bindings; they do not open competing MQTT connections.
- **Nest Camera snapshot** takes ~10-15s (WebRTC negotiation + first frame); SDM API exposes no battery/online status for cameras
- **Ring snapshot** may fail on battery doorbells if the doorbell is asleep; requires Ring Protect subscription
- **Samsung TV status is REST-only** — `samsung-tv status` deliberately skips the WebSocket/art-mode probe because opening the WS wakes the Frame's panel and shows a connection notification every poll. Use `samsung-tv art frame` explicitly when art-mode info is needed.

---

## Common Architecture

All seven dashboards share a small operational baseline:

- **Server:** threaded Python HTTP service on the Mac Mini
- **Binding:** `0.0.0.0:<port>`, reachable on the home LAN and through Tailscale MagicDNS
- **Remote access:** Tailscale is the supported away-from-home path; these ports are not intentionally published to the public internet
- **Process:** KeepAlive LaunchAgent that restarts after a crash
- **Theme:** dark-first browser UI with system fonts and responsive layouts

The implementations intentionally differ by workload. Nest, Usage, Dog Walk, Roomba, and Home Control Plane are dotfiles-owned single-file servers. Financial serves six repo-owned HTML pages backed by SQLite and a Homebrew-Python virtual environment. Forecast is also repo-owned and combines static assets, JSON caches, and a local SQLite forecast ledger. Chart libraries, storage formats, cache TTLs, and browser refresh intervals are documented per dashboard rather than assumed to be universal.

Operational changes to dashboard LaunchAgents should be made on the Mac Mini, not on Dylan's laptop:

```bash
ssh dylans-mac-mini 'hostname -s; whoami; echo HOME=$HOME'
```

Expected target context is user `dbochman` with `HOME=/Users/dbochman`.

---

## Troubleshooting

### Check if a dashboard is running

```bash
ssh dbochman@dylans-mac-mini "launchctl list | grep dashboard"
```

### Restart a dashboard

```bash
ssh dbochman@dylans-mac-mini "launchctl stop ai.openclaw.<name>-dashboard"
# KeepAlive auto-restarts it
```

### Check logs

```bash
ssh dbochman@dylans-mac-mini "tail -20 ~/.openclaw/logs/<name>-dashboard.log"
ssh dbochman@dylans-mac-mini "tail -20 ~/.openclaw/logs/<name>-dashboard.err.log"
```

For the Financial Dashboard, normal HTTP access records are written to the
stderr log. Treat a `200` line as an access record, not an error. The daily
Plaid and crypto sync agents are expected to be idle between runs; check their
last exit code and status files rather than expecting `launchctl` to show them
as running:

```bash
ssh dbochman@dylans-mac-mini 'cat ~/.openclaw/financial-dashboard/plaid-sync-status.json'
ssh dbochman@dylans-mac-mini 'cat ~/.openclaw/financial-dashboard/weekly-scrape-status.json'
ssh dbochman@dylans-mac-mini 'cat ~/.openclaw/forecast-dashboard/crypto-sync-status.json'
```

### Verify from local machine

```bash
curl -s http://dylans-mac-mini:8550/ | head -5   # Nest
curl -s http://dylans-mac-mini:8551/ | head -5   # Usage
curl -s http://dylans-mac-mini:8552/ | head -5   # Dog Walk
curl -s http://dylans-mac-mini:8553/ | head -5   # Roomba
curl -s http://dylans-mac-mini:8558/ | head -5   # Home Control Plane
curl -s http://dylans-mac-mini:8585/ | head -5   # Financial
curl -s http://dylans-mac-mini:8586/ | head -5   # Forecast
```

### Verify financial and forecast integration

```bash
curl -fsS http://dylans-mac-mini:8585/api/mortgage/summary
curl -fsS http://dylans-mac-mini:8586/api/health
curl -fsS http://dylans-mac-mini:8586/api/current-snapshot
curl -fsS http://dylans-mac-mini:8586/api/monthly-operating-tasks
```
