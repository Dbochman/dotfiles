---
name: resy
description: Search restaurants, check availability, and book reservations on Resy. Use when asked about restaurant reservations, dinner plans, booking a table, finding available restaurants, or sniping hard-to-get reservations.
allowed-tools: Bash(resy:*)
metadata: {"openclaw":{"emoji":"R","requires":{"bins":["resy"]}}}
---

# Resy Reservations

Search restaurants, check availability, and manage reservations via the `resy` CLI. Credentials are managed via 1Password.

## Available Commands

### Authenticate
```bash
resy auth
```

### Check auth status
```bash
resy status
```

### Search restaurants by name
```bash
resy search "Carbone"
resy search "sushi boston"
```

### Check availability for a venue
```bash
resy availability <venue_id> 2026-03-20 2
```
Use the venue ID from search results. Date format is YYYY-MM-DD, last argument is party size.

### Book a reservation
```bash
resy book <config_token> 2026-03-20 2
resy book <config_token> 2026-03-20 2 --dry-run
```
The config_token comes from the availability output. Always use `--dry-run` first to preview.

### Cancel a reservation
```bash
resy cancel <resy_token>
```
The resy_token comes from the reservations list.

### List upcoming reservations
```bash
resy reservations
```

### Snipe mode (auto-book when slot appears)
```bash
# Dry-run: poll but don't book
resy snipe <venue_id> 2026-03-20 2 --time 19:00

# With auto-booking enabled
resy snipe <venue_id> 2026-03-20 2 --time 19:00 --confirm

# Custom poll duration (seconds)
resy snipe <venue_id> 2026-03-20 2 --time 20:00 --duration 3600 --confirm
```

## Global Flags

- `--json` — Machine-readable JSON output
- `--dry-run` — Stop before booking or cancelling

## Typical Workflow

1. Search for a restaurant: `resy search "name"`
2. Note the venue ID from results (e.g. `[12345]`)
3. Check availability: `resy availability 12345 2026-03-20 2`
4. Preview booking: `resy book <config_token> 2026-03-20 2 --dry-run`
5. Confirm booking: `resy book <config_token> 2026-03-20 2`

## Safety Rules

- **Never book without user confirmation** — always use `--dry-run` first and show the user what will be booked
- **Snipe mode requires `--confirm`** — without it, snipe only reports matches
- **Never exceed rate limits** — the CLI enforces 2s between requests and max 30/minute
- **Validate dates** — must be YYYY-MM-DD and not in the past
- **Party size** — must be 1-20

## Notes

- Venue IDs are shown in brackets in search results (e.g. `[12345]`)
- Config tokens come from availability output
- Resy tokens come from the reservations list
- Auth tokens are cached for 12 hours; credentials for 1 year
- Logs are written to `~/.openclaw/logs/resy.log`
