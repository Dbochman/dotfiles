# Financial Dashboard — Scheduled Scraping Plan

## Status: PLANNED (not yet implemented)

## Overview

Set up automated scraping to keep Julia's financial dashboard data fresh. Currently all data imports are manual.

OpenClaw orchestrates scraping with self-healing auth for utility providers. Bank portals (BoA, PennyMac) remain headless-with-alert due to anti-bot measures. Paystub import remains manual (no email-based PDF delivery from employer).

---

## Repository & File Locations

The financial dashboard lives in a **separate repo** on the Mini (not in dotfiles):

| What | Path on Mini |
|------|-------------|
| Dashboard repo | `~/repos/financial-dashboard/` |
| Server | `~/repos/financial-dashboard/serve_dashboard.py` |
| Scrapers | `~/repos/financial-dashboard/scrape_*.py` |
| Parsers | `~/repos/financial-dashboard/parse_*.py` |
| Importer | `~/repos/financial-dashboard/update_data.py` |
| DB schema | `~/repos/financial-dashboard/db.py` |
| Database | `~/repos/financial-dashboard/finance.db` (gitignored) |
| Config | `~/repos/financial-dashboard/config.yaml` |
| Python venv | `~/repos/financial-dashboard/venv/` |
| LaunchAgent | `~/Library/LaunchAgents/ai.openclaw.financial-dashboard.plist` |

Session directories (Playwright persistent contexts):

| Provider | Session Dir (relative to repo) |
|----------|-------------------------------|
| Eversource | `.eversource_session/` |
| National Grid | `.nationalgrid_session/` |
| BWSC | `.bwsc_session/` |
| BoA | `.boa_session/` |
| PennyMac | `.pennymac_session/` |

---

## Current State

### Database

- SQLite at `~/repos/financial-dashboard/finance.db`
- 83 utility readings, 16 mortgage payments, 0 Plaid transactions, 0 paystubs
- Latest data: utility readings through 2026-03, mortgage through 2026-04

### Session Persistence

All Playwright scrapers use `launch_persistent_context()` with per-provider session dirs:

| Provider | Session Dir | `--cookies` Mode |
|----------|------------|------------------|
| Eversource | `.eversource_session/` | Yes |
| National Grid (gas) | `.nationalgrid_session/` | No (has helper, no CLI flag) |
| National Grid (electric) | `.nationalgrid_session/` (shared) | Yes |
| BWSC | `.bwsc_session/` | Yes |
| BoA Mortgage | `.boa_session/` | Yes |
| PennyMac Mortgage | `.pennymac_session/` | No |

**`--cookies` mode** uses `sweet-cookie` CLI to extract cookies from Chrome's cookie database (Chrome can stay open) and injects them into a fresh Playwright context. This is the session handoff mechanism for self-healing: Pinchtab logs into Chrome → `sweet-cookie` reads fresh cookies → scraper runs with `--cookies --headless`.

**`sweet-cookie`:** NOT installed on Mini. Must build from SweetCookieKit.

### Auth Infrastructure

- **1Password:** `OP_SERVICE_ACCOUNT_TOKEN` via `~/.openclaw/.secrets-cache`
- **Pinchtab:** CDP browser automation available on Mini
- **Plaid:** NOT configured (no `.env`, no linked accounts)
- **Tesla:** No `.env`. TeslaPy handles OAuth token refresh from `~/.config/teslapy/`

---

## Provider Classification

### Tier 1: API-Only (Fully Autonomous)

No browser needed. Run as simple commands with retry on transient errors.

| Script | Command | Schedule | Auth |
|--------|---------|----------|------|
| `scrape_tesla_solar.py` | `python3 scrape_tesla_solar.py` | Weekly (via scrape job) | TeslaPy OAuth (auto-refresh) |
| `update_data.py sync` | `python3 update_data.py sync` | Weekly (via scrape job; upgrade to daily if needed) | Plaid API tokens |

### Tier 2: Self-Healing via Cookie Injection (Utility Providers)

These support `--cookies` mode. Self-healing flow:

1. Run `scrape_X.py --cookies --headless`
2. If session expired → Claude opens Chrome via Pinchtab to the login URL
3. Reads credentials from 1Password via `op read` (env var, not shell arg)
4. Fills login form via CDP `fill` tool
5. No MFA expected (utility providers don't use MFA as of 2026-03)
6. After login succeeds, `sweet-cookie` extracts fresh Chrome cookies
7. Retry `scrape_X.py --cookies --headless` with fresh cookies
8. Alert Dylan only if retry also fails

| Script | Login URL | 1Password Item | MFA |
|--------|----------|----------------|-----|
| `scrape_eversource.py --cookies --headless` | `eversource.com/security/account/login` | `Eversource` | None |
| `scrape_national_grid_electric.py --cookies --headless` | `myaccount.nationalgrid.com` | `National Grid` | None |
| `scrape_bwsc.py --cookies --headless` | Azure B2C → `bwsc.org` | `BWSC` | None |

**National Grid Gas exception:** `scrape_national_grid.py` lacks `--cookies` CLI flag but shares `nationalgrid_helpers.py` which has `get_chrome_cookies()`. Fix required before Tier 2: add `--cookies` flag to the gas scraper (Phase 2a). Until then, NG gas runs as **Tier 3** (headless with alert, no cookie injection fallback).

### Tier 3: Headless with Alert (Bank Portals)

BoA and PennyMac use sophisticated anti-bot measures (Akamai Bot Manager, device fingerprinting, step-up auth). Automated re-auth is not reliable. Run headless with persistent sessions; alert on failure for manual re-auth.

| Script | Login Flow | Why Not Self-Healing |
|--------|-----------|---------------------|
| `scrape_mortgage.py --lender boa --headless` | Username + password → account token | Akamai bot detection, SMS MFA (not email), device fingerprinting |
| `scrape_mortgage.py --lender pennymac --headless` | Username + password → bearer token from identity redirect | Step-up auth, token in redirect URL requires browser state |

**BoA-specific:** Requires `account_token` extracted from authenticated page. Even if login succeeds, token capture requires specific page navigation that's fragile with CDP.

**PennyMac-specific:** Bearer token is generated from identity server redirect. Captured from `ids_token` URL parameter. Session-based, regenerated each login.

**Failure handling:** On auth error, send iMessage alert with error details. Dylan re-auths interactively via screen share.

### Manual: Paystubs

Julia's employer does not email paystub PDFs — they must be downloaded manually from the employer portal. Dylan or Julia downloads the PDF and runs `parse_paystubs.py`.

**TODO:** Write a `scrape_paystubs.py` script (Playwright + headless) to automate downloading paystub PDFs from Julia's employer portal (ADP/Workday). Needs: portal URL, Julia's login credentials in 1Password, and a test session to map the navigation flow. Once built, this moves from Manual to Tier 2 (self-healing) or Tier 3 (headless with alert) depending on the portal's anti-bot posture.

---

## Self-Healing Auth Flow (Tier 2 Detail)

### Session Handoff Mechanism

The scrapers already support a `--cookies` flag that:
1. Calls `sweet-cookie --domains <domain> --browser chrome --format json`
2. Parses Chrome's cookie database (doesn't need Chrome closed)
3. Injects cookies into a fresh Playwright context via `context.add_cookies()`

This means **Pinchtab can log into Chrome, and the scraper reads the resulting cookies**. No need to merge Playwright session state.

### Concrete Flow

```
OpenClaw cron job starts
    │
    ├── Run: scrape_eversource.py --cookies --headless
    │   ├── sweet-cookie extracts Chrome cookies for eversource.com
    │   ├── Playwright opens with injected cookies
    │   ├── Navigates to account page
    │   │
    │   ├── SUCCESS → scrape data → write utilities_data.json → done
    │   │
    │   └── FAIL (redirected to login page)
    │       │
    │       ├── Claude reads: op read "op://Personal/Eversource/username"
    │       │   (via env var export, never as shell arg)
    │       ├── Claude opens Pinchtab: navigate to eversource.com/security/account/login
    │       ├── Claude fills: username field, password field via CDP fill tool
    │       ├── Claude waits for redirect to account page (~10s)
    │       ├── Claude verifies: URL contains /cg/customer/ (authenticated)
    │       │
    │       ├── Retry: scrape_eversource.py --cookies --headless
    │       │   (sweet-cookie now gets fresh cookies from Chrome)
    │       │
    │       ├── SUCCESS → done
    │       └── FAIL → alert Dylan
    │
    ├── Run: scrape_national_grid_electric.py --cookies --headless
    │   └── (same pattern, domain: nationalgrid.com)
    │
    ├── Run: scrape_bwsc.py --cookies --headless
    │   └── (same pattern, domain: bwsc.org, Azure B2C login)
    │
    ... (continue for each provider)
```

### Pinchtab Isolation

**Risk:** Cielo refresh (`com.openclaw.cielo-refresh`) uses Pinchtab/CDP every 30 min via `StartInterval` (runs from load time, not clock-aligned — cannot reliably avoid by scheduling offset).

**Mitigation — disable Cielo during scrape:**

The financial scrape cron prompt should disable the Cielo LaunchAgent before starting browser-based re-auth, then re-enable after:

```bash
# Before Pinchtab re-auth
launchctl unload ~/Library/LaunchAgents/com.openclaw.cielo-refresh.plist

# ... perform Pinchtab login(s) ...

# After all re-auths complete
launchctl load ~/Library/LaunchAgents/com.openclaw.cielo-refresh.plist
```

This is only needed when self-healing is triggered (Tier 2 auth failure). Normal headless runs don't use Pinchtab at all.

**Additional safeguard:** `sessionTarget: "isolated"` on the cron job (standard OpenClaw pattern for independent agent sessions).

### Secret Handling Rules

1. **Never pass credentials as shell arguments** — use `op read` output captured in env vars, then `unset` immediately after use
2. **Never log credentials** — agent exec context already redacts env vars matching `PASSWORD=`, `SECRET=`, `TOKEN=` patterns
3. **Pinchtab `fill` tool sends values via CDP protocol** — values appear in CDP messages but not in shell history or process listing. CDP operates over localhost WebSocket (not logged by default)
4. **Delete transient artifacts** — downloaded PDFs cleaned up after use
5. **Credential lifecycle:**
   ```bash
   export PROVIDER_PW=$(op read "op://Personal/Eversource/password")
   # ... use in Pinchtab fill ...
   unset PROVIDER_PW  # clean up immediately after use
   ```

---

## Paystub Automation (Tier 4 Detail)

### DB Schema (from `db.py`)

```sql
CREATE TABLE paystubs (
    id TEXT PRIMARY KEY,               -- e.g., "adp-2026-03-28-2026-03-27"
    pay_date TEXT NOT NULL,            -- YYYY-MM-DD
    period_start TEXT,                 -- YYYY-MM-DD
    period_end TEXT,                   -- YYYY-MM-DD
    pay_type TEXT DEFAULT 'regular',   -- "regular" or "bonus"
    gross_pay REAL,
    net_pay REAL,
    regular_pay REAL,
    bonus REAL DEFAULT 0,
    deductions_total REAL,
    federal_tax REAL,
    state_tax REAL,
    social_security_tax REAL,
    medicare_tax REAL,
    retirement_401k REAL,
    retirement_401k_er REAL,           -- employer match
    hsa_employee REAL,
    hsa_employer REAL,
    benefits_pretax REAL,
    other_deductions REAL
);
```

### JSON Contract (`paystubs_data.json`)

The import function (`update_data.py import-json-paystubs`) reads a JSON file with this structure:

```json
{
  "paystubs": [
    {
      "id": "adp-2026-03-28-2026-03-27",
      "pay_date": "2026-03-28",
      "period_start": "2026-03-14",
      "period_end": "2026-03-27",
      "pay_type": "regular",
      "gross_pay": 5000.00,
      "net_pay": 3500.00,
      "regular_pay": 5000.00,
      "bonus": 0,
      "deductions_total": 1500.00,
      "federal_tax": 600.00,
      "state_tax": 250.00,
      "social_security_tax": 310.00,
      "medicare_tax": 72.50,
      "retirement_401k": 200.00,
      "retirement_401k_er": 100.00,
      "hsa_employee": 50.00,
      "hsa_employer": 25.00,
      "benefits_pretax": 150.00,
      "other_deductions": 42.50
    }
  ]
}
```

**Duplicate detection:** `id` is PRIMARY KEY — reimporting the same paystub is idempotent (INSERT OR REPLACE). ID format: `adp-<pay_date>-<period_end>` (e.g., `adp-2026-03-28-2026-03-27`) to handle multiple stubs on the same pay date.

### Claude Vision Extraction Flow

```
1. Search Julia's Gmail:
   gws gmail users messages list --account julia.joy.jennings@gmail.com \
     --params '{"userId":"me","q":"subject:(pay stub OR earnings statement) newer_than:16d has:attachment","maxResults":3}'

2. For each unprocessed message:
   a. Get message details + attachment IDs
   b. Download PDF attachment to /tmp/paystub-<msg_id>.pdf
   c. Read PDF with Claude vision (Read tool handles PDFs)
   d. Extract structured data matching the schema above
   e. Generate unique ID: "adp-<pay_date>-<period_end>" (collision-safe)
   f. Check if ID already in DB (skip if exists)

3. Write paystubs_data.json with new entries only

4. Run: update_data.py import-json-paystubs

5. Clean up: delete /tmp/paystub-*.pdf

6. Send Julia a summary:
   "Imported paystub for 3/14-3/27: gross $5,000, net $3,500"
```

**Edge cases:**
- Multi-page PDFs: Claude vision handles up to 20 pages per request
- Password-protected PDFs: ADP paystubs are not password-protected
- Rasterized PDFs: Claude vision reads images — works for scanned stubs
- YTD values: Extract only current-period values (not YTD) per the schema

### Paystub Search Refinement

Julia's employer uses ADP. Search query refinements to try (in order):
1. `from:noreply@adp.com subject:earnings newer_than:16d has:attachment`
2. `subject:(pay stub OR earnings statement OR direct deposit) newer_than:16d has:attachment`
3. Fall back to broader search and let Claude filter by content

**Prerequisite:** Identify Julia's actual paystub email pattern from her Gmail. Run the search once manually to calibrate.

---

## OpenClaw Cron Job Schema

### Weekly Scrape Job

```json
{
  "id": "financial-scrape-0001",
  "agentId": "main",
  "name": "Weekly Financial Data Scrape",
  "enabled": true,
  "createdAtMs": 0,
  "updatedAtMs": 0,
  "schedule": {
    "kind": "cron",
    "expr": "5 4 * * 0",
    "tz": "America/New_York"
  },
  "sessionTarget": "isolated",
  "wakeMode": "next-heartbeat",
  "payload": {
    "kind": "agentTurn",
    "message": "Run the financial dashboard scrapers. Working directory: ~/repos/financial-dashboard. Venv Python: ./venv/bin/python3.\n\nFor each scraper below, run it and check the exit code. If a Tier 2 scraper fails with a session/auth error, attempt self-healing re-auth via Pinchtab (see re-auth instructions below). Tier 3 scrapers: just alert on failure.\n\n--- Tier 1 (API only) ---\n1. ./venv/bin/python3 scrape_tesla_solar.py\n\n--- Tier 2 (self-healing via --cookies) ---\n2. ./venv/bin/python3 scrape_eversource.py --cookies --headless\n3. ./venv/bin/python3 scrape_national_grid_electric.py --cookies --headless\n4. ./venv/bin/python3 scrape_bwsc.py --cookies --headless\n\n--- Tier 3 (headless, alert on failure) ---\n5. ./venv/bin/python3 scrape_national_grid.py --headless (no --cookies until Phase 2a)\n6. ./venv/bin/python3 scrape_mortgage.py --lender boa --headless\n7. ./venv/bin/python3 scrape_mortgage.py --lender pennymac --headless\n\n--- Import results to SQLite ---\n./venv/bin/python3 update_data.py import-json-utilities\n./venv/bin/python3 update_data.py import-json-gas\n./venv/bin/python3 update_data.py import-json-electric-cabin\n./venv/bin/python3 update_data.py import-json-solar-cabin\n./venv/bin/python3 update_data.py import-json-water\n./venv/bin/python3 update_data.py import-json-boa-mortgage\n./venv/bin/python3 update_data.py import-json-pennymac-mortgage\n\nIf .env has PLAID_CLIENT_ID set:\n./venv/bin/python3 update_data.py sync\n\n--- Re-auth instructions (Tier 2 only) ---\nIf a Tier 2 scraper fails with 'session expired' or 'login' in the output:\n1. Disable Cielo refresh to avoid Pinchtab conflicts: launchctl unload ~/Library/LaunchAgents/com.openclaw.cielo-refresh.plist\n2. Export credentials: export PROVIDER_USER=$(op read 'op://Personal/<provider>/username') && export PROVIDER_PW=$(op read 'op://Personal/<provider>/password')\n3. Open Chrome to provider login URL via pinchtab navigate_page\n4. Fill username and password fields via CDP fill tool\n5. Wait for redirect to authenticated page (~10s)\n6. Unset credentials immediately: unset PROVIDER_USER PROVIDER_PW\n7. Re-enable Cielo refresh: launchctl load ~/Library/LaunchAgents/com.openclaw.cielo-refresh.plist\n8. Retry the scraper command\n\nOnly message Dylan if there were failures (include which scrapers and the error summary). Imports are idempotent — safe to re-run."
  },
  "delivery": {
    "mode": "silent"
  }
}
```

### Biweekly Paystub Job

```json
{
  "id": "paystub-import-0001",
  "agentId": "main",
  "name": "Julia Paystub Import",
  "enabled": false,
  "createdAtMs": 0,
  "updatedAtMs": 0,
  "schedule": {
    "kind": "cron",
    "expr": "0 10 1,15 * *",
    "tz": "America/New_York"
  },
  "sessionTarget": "isolated",
  "wakeMode": "next-heartbeat",
  "payload": {
    "kind": "agentTurn",
    "message": "Check Julia's Gmail for recent paystub PDFs and import them to the financial dashboard.\n\n1. Search: gws gmail users messages list --account julia.joy.jennings@gmail.com --params '{\"userId\":\"me\",\"q\":\"subject:(pay stub OR earnings statement) newer_than:16d has:attachment\",\"maxResults\":3}'\n2. For each message with a PDF attachment:\n   a. Download the PDF to /tmp/paystub-<message_id>.pdf\n   b. Read the PDF and extract ALL these fields (current period only, not YTD): pay_date, period_start, period_end, pay_type ('regular' or 'bonus'), gross_pay, net_pay, regular_pay, bonus, deductions_total, federal_tax, state_tax, social_security_tax, medicare_tax, retirement_401k, retirement_401k_er, hsa_employee, hsa_employer, benefits_pretax, other_deductions\n   c. Generate id as 'adp-<pay_date>-<period_end>' (collision-safe)\n   d. Write JSON: {\"paystubs\": [{\"id\": \"...\", ...all fields...}]}\n3. Write to ~/repos/financial-dashboard/paystubs_data.json\n4. Run: cd ~/repos/financial-dashboard && ./venv/bin/python3 update_data.py import-json-paystubs\n5. Clean up: rm /tmp/paystub-*.pdf\n6. Send Julia a summary of what was imported.\n\nIf no new paystubs found, do nothing (no message needed)."
  },
  "delivery": {
    "mode": "announce",
    "channel": "bluebubbles",
    "to": "+15084234853"
  }
}
```

**Note:** `enabled: false` until Julia's paystub email pattern is confirmed. Schedule `0 10 1,15 * *` = 10 AM on 1st and 15th of each month (biweekly approximation via cron).

---

## Idempotency & Duplicate Safety

| Import Command | Dedup Mechanism |
|----------------|-----------------|
| `import-json-utilities` | Readings keyed by `account_id + month` (INSERT OR REPLACE) |
| `import-json-gas` | Same |
| `import-json-water` | Same |
| `import-json-electric-cabin` | Same |
| `import-json-solar-cabin` | Same |
| `import-json-boa-mortgage` | Payments keyed by `mortgage_id + month` |
| `import-json-pennymac-mortgage` | Same |
| `import-json-paystubs` | Keyed by `id` (PRIMARY KEY, INSERT OR REPLACE) |
| `update_data.py sync` | Plaid cursor-based sync (tracks last position) |

All imports are idempotent. Re-running on unchanged data produces no duplicates.

---

## Schedule Summary

| Job | Schedule | Providers | Delivery |
|-----|----------|-----------|----------|
| `financial-scrape-0001` | Weekly, Sun 4:05 AM ET | Tesla + utilities + mortgages + Plaid | Silent (alert on failure only) |
| `paystub-import-0001` | 1st & 15th, 10 AM ET | Julia's Gmail → vision → DB | Announce to Julia |

---

## Failure Modes

| Failure | Tier | Detection | Auto-Fix | Fallback |
|---------|------|-----------|----------|----------|
| Session expired (utility) | 2 | Scraper exits with "login" in output | Pinchtab re-auth + cookie injection + retry | Alert Dylan |
| Session expired (bank) | 3 | Scraper exits with auth error | None | Alert Dylan for manual re-auth |
| Site redesigned | 2, 3 | Unexpected error/timeout | None | Alert Dylan with error details |
| Account locked | 2, 3 | Multiple failed logins | Stop retrying immediately | Alert Dylan |
| `sweet-cookie` fails | 2 | Exit code from sweet-cookie | Fall back to persistent session (`--headless` without `--cookies`) | Alert Dylan |
| Plaid ITEM_LOGIN_REQUIRED | 1 | Sync returns error | None (needs Plaid Link UI) | Alert Dylan |
| Tesla token expired | 1 | TeslaPy error | TeslaPy auto-refreshes | Re-run interactively |
| Network timeout | All | HTTP/DNS timeout | Retry up to 3x with backoff | Alert Dylan |
| 1Password unavailable | 2 | `op read` fails | Skip re-auth, run with stale session | Alert Dylan |
| Pinchtab/Cielo conflict | 2 | N/A (prevented by disabling Cielo) | Unload Cielo LaunchAgent during re-auth, reload after | N/A |
| No new paystub email | 4 | Gmail search returns empty | Do nothing (expected) | N/A |
| Paystub PDF unreadable | 4 | Vision extraction fails | None | Alert Dylan |

---

## Prerequisites & Implementation Order

### Phase 1: One-Time Setup (Needs Mini Screen Share)

| Step | What | Time |
|------|------|------|
| 1a | Build sweet-cookie: `cd /tmp && git clone SweetCookieKit && swift build` → `~/bin/sweet-cookie` | 5 min |
| 1b | Create `.env` with `TESLA_EMAIL` | 1 min |
| 1c | Verify 1Password items exist for: Eversource, National Grid, BWSC, BoA, PennyMac | 5 min |
| 1d | Interactive auth for each scraper (6 logins) | 15 min |
| 1e | Test `--cookies --headless` mode for Tier 2 scrapers | 10 min |
| 1f | Test `--headless` mode for Tier 3 scrapers (BoA, PennyMac) | 5 min |

### Phase 2: Cron Jobs (Remote)

| Step | What |
|------|------|
| 2a | Add `--cookies` flag to `scrape_national_grid.py` (copy from electric scraper) |
| 2b | Add `financial-scrape-0001` to `~/.openclaw/cron/jobs.json` |
| 2c | Test by running `openclaw cron run financial-scrape-0001 --timeout 600000 --expect-final` |
| 2d | Monitor first automated Sunday run |

### Phase 3: Paystub Automation (Remote)

| Step | What |
|------|------|
| 3a | Identify Julia's paystub email pattern (search her Gmail) |
| 3b | Test vision extraction on one paystub PDF |
| 3c | Add `paystub-import-0001` to jobs.json (enabled: false initially) |
| 3d | Test manually, then enable |

### Phase 4: Plaid Setup (Separate Effort)

| Step | What |
|------|------|
| 4a | Create Plaid account at dashboard.plaid.com |
| 4b | Obtain `PLAID_CLIENT_ID` and `PLAID_SECRET` |
| 4c | Add to `.env` |
| 4d | Run `update_data.py link` interactively per bank account (screen share) |
| 4e | Test `update_data.py sync` |
| 4f | Add daily sync to scrape job (or separate daily cron job) |
