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

If the `opentable` CLI gives auth errors, the token needs refreshing:

1. Open Chrome -> navigate to opentable.com
2. Log in (if not already)
3. Open DevTools (Cmd+Opt+I) -> Application -> Cookies
4. Find the cookie named "authCke"
5. Copy the "atk" value (UUID after `atk=` and before `&rtk=`)
6. Update 1Password: `op item edit "OpenTable" --vault "OpenClaw" "auth_token=<atk_value>"`

Auth tokens typically last ~14 days. Pinchtab does not need auth tokens (uses browser session).

## Notes

- Pinchtab booking script: `~/.openclaw/workspace/scripts/opentable-book.sh`
- CLI logs: `~/.openclaw/logs/opentable.log`
- CLI uses undocumented mobile API (`mobile-api.opentable.com`) — may break without notice
- Pinchtab uses the real OpenTable website — more resilient to API changes
