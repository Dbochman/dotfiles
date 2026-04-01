---
name: opentable
description: Search restaurants, check availability, and book reservations on OpenTable. Use when asked about OpenTable reservations, restaurant availability on OpenTable, booking a table via OpenTable, or searching for restaurants by cuisine/location.
allowed-tools: Bash(opentable:*),Bash(pinchtab:*),Bash(bash:*)
metadata: {"openclaw":{"emoji":"O","requires":{"bins":["pinchtab","opentable"]}}}
---

# OpenTable Reservations

Two methods are available for OpenTable bookings. **Prefer Pinchtab** (browser automation) for search-based bookings. Fall back to the `opentable` CLI when you have a specific restaurant ID.

## Method 1: Pinchtab Booking Script (Primary)

The `opentable-book.sh` script uses Pinchtab (headless browser automation) to search OpenTable, select a timeslot, and complete the reservation using the card on file.

### Usage
```bash
bash ~/.openclaw/workspace/scripts/opentable-book.sh "<search_term>" <date> [time] [party_size]
```

### Examples
```bash
# Search by cuisine + area
bash ~/.openclaw/workspace/scripts/opentable-book.sh "Italian brookline newton" 2026-04-11 19:00 2

# Broader search
bash ~/.openclaw/workspace/scripts/opentable-book.sh "sushi south end boston" 2026-05-15 20:00 2
```

### Output
```json
{"success": true, "restaurant": "Carbone", "date": "Fri, Apr 11", "time": "7:00 PM", "url": "..."}
{"success": false, "error": "No available timeslots found for search: ..."}
```

### How it works
1. Starts a Pinchtab browser session
2. Navigates to OpenTable search with cuisine, date, time, party size
3. Dismisses cookie consent overlay
4. Finds available timeslots, picks the closest to requested time
5. Clicks through to booking details page
6. Clicks "Complete reservation" (uses card on file)
7. Verifies confirmation page
8. Cleans up Pinchtab process

### Tips
- If no slots found, retry with a different search term (just the cuisine, or a different neighborhood)
- Try multiple dates: e.g. both Fridays and Saturdays in the target week
- The script handles the full booking flow — no manual steps needed
- metroId=7 is hardcoded (Boston metro area)

## Method 2: OpenTable CLI (Fallback)

Use the `opentable` CLI when you already have a specific restaurant ID (from an OpenTable URL). Auth uses a bearer token from browser cookies stored in 1Password.

### Get restaurant info by ID
```bash
opentable info 8033
opentable info 8033 --json
```
Restaurant IDs come from OpenTable URLs: `opentable.com/r/carbone-new-york?rid=8033` -> ID is `8033`.

### Check availability for a restaurant
```bash
opentable availability <restaurant_id> 2026-03-20 2
opentable availability <restaurant_id> 2026-03-20 2 --time 20:00
```
Date format is YYYY-MM-DD, last positional argument is party size. Default search time is 19:00.

### Book a reservation
```bash
opentable book <restaurant_id> 2026-03-20 19:00 2
opentable book <restaurant_id> 2026-03-20 19:00 2 --dry-run
```
Always use `--dry-run` first to preview.

### Snipe mode (auto-book when slot appears)
```bash
opentable snipe <restaurant_id> 2026-03-20 2 --time 19:00
opentable snipe <restaurant_id> 2026-03-20 2 --time 19:00 --confirm
opentable snipe <restaurant_id> 2026-03-20 2 --time 20:00 --duration 3600 --confirm
```

### CLI Global Flags
- `--json` — Machine-readable JSON output
- `--dry-run` — Stop before booking

## Typical Workflow

### For search-based bookings (most common)
1. Use the Pinchtab script: `bash ~/.openclaw/workspace/scripts/opentable-book.sh "cuisine area" DATE TIME PARTY`
2. If it fails, try different search terms or dates
3. If Pinchtab keeps failing, fall back to Resy

### For specific restaurant bookings
1. Find the restaurant ID from its OpenTable URL (e.g. `?rid=8033`)
2. Check availability: `opentable availability 8033 2026-03-20 2`
3. Preview: `opentable book 8033 2026-03-20 19:00 2 --dry-run`
4. Confirm: `opentable book 8033 2026-03-20 19:00 2`

## Safety Rules

- **Cron jobs can book directly** — datenight and group dinner jobs are pre-approved
- **Ad-hoc bookings need user confirmation** — use `--dry-run` first
- **Snipe mode requires `--confirm`** — without it, snipe only reports matches
- **Validate dates** — must be YYYY-MM-DD and not in the past
- **Party size** — must be 1-20

## CLI Auth Token Refresh

Auth tokens (~14 days) are stored at `~/.cache/openclaw-gateway/opentable_auth_token`.

### Automated refresh (preferred)
```bash
bash ~/.openclaw/bin/opentable-refresh-token.sh
```
Fully automated: Pinchtab navigates to OT login, enters `bochmanspam@gmail.com`, reads the 2FA code from Gmail via GWS, enters it, extracts the `authCke` cookie, and updates the CLI token cache. No manual steps.

**How it works:**
1. Pinchtab opens `opentable.com/authenticate/start` (headless)
2. Clicks "Use email instead", enters `bochmanspam@gmail.com`
3. Reads verification code from Gmail (`gws gmail users messages list --account bochmanspam@gmail.com`)
4. Enters code — auto-redirected to logged-in state
5. Extracts `authCke` cookie `atk` value from `document.cookie`
6. Writes token to `~/.cache/openclaw-gateway/opentable_auth_token`

**Requirements:** Pinchtab, GWS with `bochmanspam@gmail.com` authenticated, secrets-cache sourced.

### Manual refresh (fallback)
1. Open Chrome on Mini -> navigate to opentable.com
2. Log in via phone or email (bochmanspam@gmail.com)
3. Open DevTools (Cmd+Opt+I) -> Application -> Cookies
4. Find "authCke" cookie, copy the `atk` UUID value
5. Write to cache: `echo "<atk_value>" > ~/.cache/openclaw-gateway/opentable_auth_token`

### Alternative: extract from CDP
If Chrome is already logged in, you can extract the cookie programmatically:
1. Kill Chrome, relaunch with `--remote-debugging-port=19222 --user-data-dir=/tmp/chrome-ot` (copy Default profile first — Chrome rejects CDP on the real data dir)
2. Use CDP `Network.getCookies` via WebSocket to read `authCke`
3. Note: Chrome's Cookies DB is encrypted, so direct SQLite reads don't work

## Snipe Mode

When target timeslots are fully booked, use snipe mode to monitor for cancellations:

```bash
# Monitor one date (reports only)
opentable snipe <restaurant_id> 2026-04-16 4 --time 19:00

# Auto-book when slot opens
opentable snipe <restaurant_id> 2026-04-16 4 --time 19:00 --duration 86400 --confirm

# Monitor multiple dates (run in background)
for DATE in 2026-04-16 2026-04-17 2026-04-18; do
  nohup opentable snipe <id> "$DATE" 4 --time 19:00 --duration 86400 --confirm \
    > "/tmp/snipe-$DATE.log" 2>&1 &
done
```

- Polls every 30 seconds
- `--duration 86400` = monitor for 24 hours
- `--confirm` = auto-book if a matching slot appears (without it, only reports)
- Check status: `tail /tmp/snipe-*.log`
- Check running: `pgrep -af "opentable snipe"`
- Kill all: `pkill -f "opentable snipe"`

### Snipe monitor with iMessage notification

To get notified when a snipe succeeds or expires, run a monitor script that checks logs and sends an iMessage via BlueBubbles:

```bash
# Checks every 60s, sends iMessage on success or expiry
nohup bash /tmp/snipe-monitor.sh > /tmp/snipe-monitor.log 2>&1 &
```

## Notes

- Pinchtab booking script: `~/.openclaw/workspace/scripts/opentable-book.sh`
- Automated auth refresh: `~/.openclaw/bin/opentable-refresh-token.sh`
- CLI token cache: `~/.cache/openclaw-gateway/opentable_auth_token`
- CLI logs: `~/.openclaw/logs/opentable.log`
- CLI uses undocumented mobile API (`mobile-api.opentable.com`) — may break without notice
- Pinchtab uses the real OpenTable website — more resilient to API changes
- OpenTable account: `bochmanspam@gmail.com` (2FA via email, readable by GWS)
- Mahaniyom (Brookline Thai, Michelin Bib Gourmand): restaurant ID `1267699`
