---
name: restaurant-book
description: Safely plan or execute a canonical standing-authorized restaurant cron scope across both Resy and OpenTable. Use only for the tracked date-night, double-date, and quarterly-dinner jobs; use the provider skills for attended ad-hoc work and restaurant-snipe for bounded monitoring.
allowed-tools: Bash(restaurant-book:*)
metadata: {"openclaw":{"emoji":"🍽️","requires":{"bins":["restaurant-book","opentable","opentable-reservations","pinchtab-headless-instance","resy"]}}}
---

# Restaurant Book

Use this coordinator for canonical recurring dinner one-shots. The deployed
job-scope registry is the standing authorization. It intentionally delegates
the exact venue while fixing the cuisine, locality, candidate dates, time
window, party size, fee policy, execution window, and one-attempt limit.

```bash
# Read-only rehearsal. This never books and never creates an attempt marker.
restaurant-book plan --job-id <canonical-job-id>

# Canonical cron execution. This may make at most one total mutation attempt.
restaurant-book run --job-id <canonical-job-id>
```

Do not pass arbitrary JSON, paths, provider tokens, approval IDs, or booking
details. The public CLI accepts only a tracked job ID; it has no arbitrary
venue, payment, cancellation, or provider override.

## Runtime guarantees

- Reads upcoming reservations from both Resy and OpenTable before search and
  again while holding the shared booking-mutation lock. A missing, stale,
  malformed, unauthenticated, or ambiguous provider read blocks all booking.
- Searches both providers within a strict total-call budget, rejects unknown
  or disallowed payment terms, and selects one candidate deterministically.
  A partial or unavailable search from either provider blocks mutation.
- Discovers OpenTable venue metadata through its managed browser profile, but
  uses the same-account-bound cache-only API for exact availability and
  mutation. Opaque result-card controls are never clicked or inferred from
  pixels.
- Makes at most one provider mutation. There is no retry or cross-provider,
  cross-date, or cross-venue fallback after the durable mutation boundary.
- Writes a protected attempt marker before mutation and retains it after every
  ambiguous result. A later run stops for attended review instead of booking.
- Requires an exact provider reservation read-back before writing a durable
  token-free receipt. OpenTable additionally requires a strictly correlated,
  contradiction-free API confirmation.
- Never cancels. Never invokes `op`. Never prints provider tokens, raw provider
  responses, booking URLs, browser content, cookies, or approval IDs.

The current canonical scopes authorize `$0` due now, deposit, prepayment,
cancellation fee, and no-show fee. A card-required or HOLD-style OpenTable
slot is therefore rejected when its complete monetary exposure is not
affirmatively reported as zero.

Scopes may also require a structured minimum price tier. The December upscale
job requires tier 3 or higher; missing or malformed price metadata is rejected.

The `plan` result is a proposal only. A canonical cron should call `run`
directly after any required calendar idempotency check, then send exactly one
status message based on its single JSON result.
