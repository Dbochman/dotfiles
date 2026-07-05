---
name: resy
description: Search Resy, inspect availability, and book reservations, including intentionally surprise-driven date cron jobs with standing authorization. Use for Resy discovery, availability, reservations, and booking. Use restaurant-snipe only for bounded cancellation monitoring; never use this skill for unattended cancellation.
allowed-tools: Bash(resy-read:*) Bash(resy:*)
metadata: {"openclaw":{"emoji":"R","requires":{"bins":["resy-read","resy"]}}}
---

# Resy reservations

Use `resy-read` for ordinary discovery and reservation review. It exposes only
search, availability, and reservations, and redacts mutation tokens.

Use raw `resy` only when a booking is authorized. Never reproduce config,
booking, reservation, authentication, or payment tokens in messages, calendar
events, logs, or the final response.

## Authorization model

- A canonical date, double-date, or quarterly-dinner cron whose prompt says to
  book is standing user authorization for one reservation within that prompt's
  cuisine, location, date, time, party-size, and payment-policy constraints.
  The venue choice is intentionally delegated: the surprise is part of the
  experience. Do not pause for exact-venue confirmation.
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

## Authorized booking workflow

1. Run the prompt's idempotency check before searching. If a matching booking
   or calendar event already exists, report it and stop.
2. In an unattended run, export `RESY_CACHE_ONLY=1` for every raw `resy`
   command. Never run `resy auth` or `op`; fail closed if cached authentication
   is unavailable.
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
   never retry it. Reconcile with `resy reservations` and report the outcome as
   unknown if confirmation cannot be established.

## Standing safeguards

- One authorized reservation means one live booking attempt. Never pivot to a
  second booking after an attempted mutation.
- Do not book a deposit, prepayment, nonrefundable reservation, or unfamiliar
  cancellation/no-show fee unless the standing prompt explicitly permits it.
  Venue uncertainty does not imply payment-term authorization.
- Canonical booking one-shots must retain `deleteAfterRun: true`,
  `delivery.mode: none`, a leading idempotency check, cache-only execution, and
  the successful-run tombstone used by cron deployment.
- The CLI enforces dates, party-size limits, and request rate limits. Do not
  bypass those checks.
