---
name: opentable
description: Check restaurant availability and book reservations on OpenTable. Use when asked about OpenTable reservations, restaurant availability on OpenTable, booking a table via OpenTable, or sniping hard-to-get OpenTable reservations.
allowed-tools: Bash(opentable:*)
metadata: {"openclaw":{"emoji":"O","requires":{"bins":["opentable"]}}}
---

# OpenTable Reservations

Check availability, get restaurant info, and book reservations via the `opentable` CLI. Auth uses a bearer token extracted from browser cookies and stored in 1Password.

## Available Commands

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
Always use `--dry-run` first to preview. The CLI will look up availability, find the matching slot, and prompt for confirmation.

### Snipe mode (auto-book when slot appears)
```bash
# Dry-run: poll but don't book
opentable snipe <restaurant_id> 2026-03-20 2 --time 19:00

# With auto-booking enabled
opentable snipe <restaurant_id> 2026-03-20 2 --time 19:00 --confirm

# Custom poll duration (seconds)
opentable snipe <restaurant_id> 2026-03-20 2 --time 20:00 --duration 3600 --confirm
```

## Global Flags

- `--json` — Machine-readable JSON output
- `--dry-run` — Stop before booking

## Typical Workflow

1. Find the restaurant ID from its OpenTable URL (e.g. `?rid=8033`)
2. Verify the restaurant: `opentable info 8033`
3. Check availability: `opentable availability 8033 2026-03-20 2`
4. Preview booking: `opentable book 8033 2026-03-20 19:00 2 --dry-run`
5. Confirm booking: `opentable book 8033 2026-03-20 19:00 2`

## Safety Rules

- **Never book without user confirmation** — always use `--dry-run` first and show the user what will be booked
- **Snipe mode requires `--confirm`** — without it, snipe only reports matches
- **Never exceed rate limits** — the CLI enforces 3s between requests and max 20/minute
- **Validate dates** — must be YYYY-MM-DD and not in the past
- **Party size** — must be 1-20

## Limitations

- **No search** — restaurant IDs must be found from OpenTable URLs
- **No cancel/list reservations** — manage existing reservations at opentable.com directly
- **No programmatic login** — requires auth token from browser cookies
- **Fragile** — uses undocumented mobile API that may break when OpenTable changes it

## Auth Token Refresh

If auth errors occur, the token needs refreshing:

1. Open Chrome -> navigate to opentable.com
2. Log in (if not already)
3. Open DevTools (Cmd+Opt+I) -> Application -> Cookies
4. Find the cookie named "authCke"
5. Copy the "atk" value (UUID after `atk=` and before `&rtk=`)
6. Update 1Password: `op item edit "OpenTable" --vault "OpenClaw" "auth_token=<atk_value>"`
7. Clear cache: `rm ~/.cache/openclaw-gateway/opentable_auth_token`

Auth tokens typically last ~14 days.

## Notes

- Logs are written to `~/.openclaw/logs/opentable.log`
- Rate limiting is conservative (3s intervals) — OpenTable blocks aggressively
- Uses undocumented mobile API (`mobile-api.opentable.com`) — may break without notice
