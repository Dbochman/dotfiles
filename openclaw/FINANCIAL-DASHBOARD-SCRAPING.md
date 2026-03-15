# Financial Dashboard: Web Scraping Pattern

## Status: IMPLEMENTED for Water + Gas (2026-03-14) — Template for reuse

## Context

The financial dashboard has 7 data pipelines that all follow the same pattern: **Source → Parser → JSON → SQLite Import**. Originally all pipelines used PDF parsers requiring manual file downloads. The BWSC water scraper introduced a new automated approach using Playwright to scrape web portals directly, eliminating manual downloads and enabling unattended monthly syncs. The National Grid gas scraper was the second implementation, validating the pattern's reusability.

This document captures the scraping pattern so it can be replicated for the remaining 5 data sources.

---

## The Pattern

### Architecture

```
Web Portal (Angular/React SPA)
    ↓ Playwright (headless Chrome)
Scraper Script (scrape_*.py)
    ↓ writes
Intermediate JSON (*_data.json)
    ↓ python update_data.py import-json-*
SQLite Database (finance.db)
    ↓ scp to Mac Mini
Dashboard Server (serve_dashboard.py)
```

### Key Design Decisions

1. **Persistent browser session** — Login once interactively, reuse `.session/` dir for all future headless runs
2. **Same JSON output** as the PDF parser — drop-in replacement, no DB schema changes needed
3. **Merge mode** — `--merge` flag layers new data on top of existing historical PDF-parsed data
4. **Month-tracking** — `.last-run` file prevents redundant runs within the same billing cycle
5. **Runs on MacBook, not Mini** — Browser sessions are tied to the machine where login happened; data is scp'd to the Mini after import
6. **Headless fail-fast** — In `--headless` mode, expired sessions exit immediately with a clear error instead of waiting for interactive login
7. **Budget billing awareness** — Portals may show budget plan payments instead of actual charges; merge logic preserves richer PDF-parsed data for overlapping months

### Scraper Script Template

Each scraper follows this structure (see `scrape_bwsc.py` as reference):

```python
# scrape_<provider>.py

# 1. Configuration block
PORTAL_URL = "https://..."
SESSION_DIR = ".session_dir/"
UTILITY_ACCOUNT_ID = "provider-type-property"

# 2. Browser helpers
#    - get_chrome_profile_dir()      # fallback to Chrome profile
#    - wait_for_login(page)          # wait for auth redirect
#    - ensure_authenticated(page)    # navigate + verify

# 3. Data extraction
#    - extract via Angular: ng.getComponent(el).exportData
#    - OR extract via DOM: page.evaluate("document.querySelector(...).textContent")
#    - OR intercept API: page.on("response", ...) for REST endpoints

# 4. Data merging
#    - build_*_data()                # combine sources into JSON format
#    - merge_with_existing()         # overlay new on old

# 5. CLI interface
#    --dry-run     # preview without writing
#    --merge       # combine with existing data
#    --headless    # unattended mode
#    --force       # override month-skip logic
```

### Sync Wrapper Script Template

Each sync wrapper follows this structure (see `water-scrape-sync.sh` as reference):

```bash
#!/bin/bash
# 1. Configuration
REPO_DIR="${HOME}/repos/financial-dashboard"
MINI_HOST="dylans-mac-mini"
SSH_OPTS="-o BatchMode=yes -o ConnectTimeout=10"
LAST_RUN_FILE="${REPO_DIR}/.<type>-scrape-last-run"

# 2. Month-skip check (idempotent — safe to run daily)
# 3. Run scraper: python3 scrape_<provider>.py --headless --merge
# 4. Import: python3 update_data.py import-json-<type>
# 5. Sync: scp finance.db to Mini + restart dashboard
# 6. Write month marker on success
```

### LaunchAgent Template

Monthly schedule with 6-day retry window (5th–10th). Stagger hours across scrapers to avoid overlapping runs:

```xml
<key>StartCalendarInterval</key>
<array>
    <dict><key>Day</key><integer>5</integer><key>Hour</key><integer>10</integer>...</dict>
    <dict><key>Day</key><integer>6</integer>...</dict>
    ...through day 10...
</array>
```

| Scraper | Hour | Label |
|---------|------|-------|
| Water | 10 AM | `com.openclaw.water-scrape` |
| Gas | 11 AM | `com.openclaw.gas-scrape` |

Script-level month tracking prevents duplicate runs when the retry window fires after a successful sync.

---

## Current Pipeline Status

| Dashboard | Source | Current Method | Scraper Candidate? | Portal |
|-----------|--------|---------------|-------------------|--------|
| Water | BWSC | **Web scraper** | DONE | customerportal.bwsc.org |
| Electricity | Eversource | PDF parser | Yes | eversource.com account portal |
| Gas | National Grid | **Web scraper** | DONE | myaccount.nationalgrid.com |
| Payroll | ADP | PDF parser | Maybe | ADP portal (complex auth) |
| Mortgage | BofA / PennyMac | PDF parser | Maybe | Bank portals (complex auth) |
| Checking | USAA | PDF parser | Maybe | USAA portal (MFA-heavy) |
| Credit Card | Chase | PDF parser | Maybe | Chase portal (MFA-heavy) |

**Best candidate for next scraper**: Eversource — utility portals tend to have simpler auth (username/password) and structured billing data similar to BWSC and National Grid.

---

## Reference Implementations

### Files (Water/BWSC)

| File | Location | Purpose |
|------|----------|---------|
| `scrape_bwsc.py` | `~/repos/financial-dashboard/` | Playwright scraper |
| `water-scrape-sync.sh` | `dotfiles/openclaw/bin/` | Sync wrapper (scrape + import + scp) |
| `com.openclaw.water-scrape.plist` | `dotfiles/openclaw/` | Monthly LaunchAgent (MacBook, 10 AM) |
| `.bwsc_session/` | `~/repos/financial-dashboard/` | Persistent browser session |
| `.water-scrape-last-run` | `~/repos/financial-dashboard/` | Month tracker |
| `water_data.json` | `~/repos/financial-dashboard/` | Intermediate JSON output |

### Files (Gas/National Grid)

| File | Location | Purpose |
|------|----------|---------|
| `scrape_national_grid.py` | `~/repos/financial-dashboard/` | Playwright scraper |
| `gas-scrape-sync.sh` | `dotfiles/openclaw/bin/` | Sync wrapper (scrape + import + scp) |
| `com.openclaw.gas-scrape.plist` | `dotfiles/openclaw/` | Monthly LaunchAgent (MacBook, 11 AM) |
| `.nationalgrid_session/` | `~/repos/financial-dashboard/` | Persistent browser session |
| `.gas-scrape-last-run` | `~/repos/financial-dashboard/` | Month tracker |
| `gas_data.json` | `~/repos/financial-dashboard/` | Intermediate JSON output |

### Data Extraction Techniques Used

**Water (BWSC — Angular SPA)**
1. **Angular component injection** — `ng.getComponent(el).exportData` reads chart data directly from the framework
2. **Service dropdown switching** — Playwright clicks dropdown options to load different data views (Water → Sewer)
3. **DOM text scraping** — Regex on rendered `textContent` for bill totals
4. **Derived fields** — Stormwater charge = total - water - sewer (not shown in chart)
5. **Unit conversion** — Gallons = cubic_feet × 7.48052

**Gas (National Grid — Next.js SPA)**
1. **Structured page scraping** — Bill History page renders issue dates, billing periods, and amounts in a consistent list format
2. **Budget billing awareness** — Portal shows budget plan payments, not actual charges; merge preserves PDF-parsed actual charges
3. **Headless fail-fast** — Expired sessions exit immediately in headless mode with actionable error message
4. **Optional bill detail extraction** — `--with-details` flag clicks into individual bill PDFs for therms and cost breakdowns

### Import Commands Reference

| Type | Command | JSON File |
|------|---------|-----------|
| Water | `import-json-water` | `water_data.json` |
| Electricity | `import-json-utilities` | `utilities_data.json` |
| Gas | `import-json-gas` | `gas_data.json` |
| Payroll | `import-json-paystubs` | `paystubs_data.json` |
| Mortgage | `import-json-mortgage` | `mortgage_data.json` |
| USAA | `import-json-usaa` | `usaa_data.json` |
| Chase | `import-json-chase` | `chase_data.json` |
