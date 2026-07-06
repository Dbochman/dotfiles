---
name: resy
description: Search Resy, inspect availability, and handle attended ad-hoc Resy booking. Route canonical recurring date-night, double-date, and quarterly-dinner jobs to restaurant-book, and use restaurant-snipe only for bounded cancellation monitoring. Never use this skill for unattended cancellation.
allowed-tools: Bash(resy-read:*) Bash(resy:*)
metadata: {"openclaw":{"emoji":"R","requires":{"bins":["resy-read","resy"]}}}
---

# Resy reservations

Use `resy-read` for ordinary discovery and reservation review. It exposes only
search, availability, and reservations, and redacts mutation tokens.

Use raw `resy` only when a booking is authorized. Never reproduce config,
booking, reservation, authentication, or payment tokens in messages, calendar
events, logs, or the final response.

For a tracked canonical date-night, double-date, or quarterly-dinner job, stop
and use `restaurant-book`. Its deployed scope is the standing authorization
and its coordinator owns both providers; never reproduce that workflow with
provider-specific Resy commands.

## Authorization model

- A canonical date-night, double-date, or quarterly-dinner scope is standing
  authorization only through `restaurant-book`; this skill must not execute
  or reimplement that cron booking.
- A direct user request to book may also delegate venue choice through broad
  constraints. A request only to search or check availability is not booking
  authorization.
- Cancellation always requires a fresh, explicit user request. Cron jobs must
  never cancel an existing reservation to make room for another.
- Use `restaurant-snipe` for bounded background monitoring. Do not create an
  ad-hoc raw `resy snipe` loop from this skill.

## Read-only commands

```bash
resy-read search "Japanese Brookline"
resy-read availability <venue-id> 2026-08-15 2
resy-read reservations
```

Venue IDs must be numeric. Dates use `YYYY-MM-DD`; party size is 1–20.

## Attended ad-hoc booking workflow

1. Read current reservations before searching. If the authorized request would
   duplicate an existing booking, report it and stop.
2. In any OpenClaw or gateway child, export `RESY_CACHE_ONLY=1` for every raw
   `resy` command. Never run `resy auth` or `op`; fail closed if cached
   authentication is unavailable.
3. Search and inspect availability within the authorized constraints:

   ```bash
   RESY_CACHE_ONLY=1 resy search "Japanese Brookline"
   RESY_CACHE_ONLY=1 resy availability <venue-id> 2026-08-15 2
   ```

4. Select a slot within scope. A different restaurant is welcome; a different
   date, time window, party size, location, fee policy, or cuisine is not.
5. Preview the selected slot, then make at most one live booking call:

   ```bash
   RESY_CACHE_ONLY=1 resy book '<config-token>' 2026-08-15 2 --dry-run
   printf 'yes\n' | RESY_CACHE_ONLY=1 resy book '<config-token>' 2026-08-15 2
   ```

6. Read reservations back once and report only the human-readable booking
   facts. If the live call returns a transport error or any ambiguous result,
   never retry it. Reconcile with `resy-read reservations` and report the
   outcome as unknown if confirmation cannot be established.

## Standing safeguards

- One authorized reservation means one live booking attempt. Never pivot to a
  second booking after an attempted mutation.
- Do not book a deposit, prepayment, nonrefundable reservation, or unfamiliar
  cancellation/no-show fee unless the fresh user authorization explicitly
  permits it. Venue uncertainty does not imply payment-term authorization.
- Canonical booking one-shots are outside this skill. They must use
  `restaurant-book` and retain its paired job/scope contract,
  `deleteAfterRun: true`, `delivery.mode: none`, and successful-run tombstone.
- The CLI enforces dates, party-size limits, and request rate limits. Do not
  bypass those checks.
