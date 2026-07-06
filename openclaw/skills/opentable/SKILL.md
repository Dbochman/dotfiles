---
name: opentable
description: Read upcoming OpenTable reservations or handle attended ad-hoc OpenTable availability and exact-approval booking. Use for OpenTable account reads, attended restaurant searches, or a user-confirmed one-off reservation; route canonical recurring date-night, double-date, and quarterly-group jobs to the restaurant-book skill instead.
allowed-tools: Bash(opentable-book:*) Bash(opentable-reservations:*)
metadata: {"openclaw":{"emoji":"O","requires":{"bins":["opentable-book","opentable-reservations","pinchtab","pinchtab-headless-instance"]}}}
---

# OpenTable Reservations

Use `opentable-reservations` for a fail-closed read of upcoming reservations
and the approval-gated `opentable-book` command for attended ad-hoc
availability or booking. Route every canonical recurring date-night,
double-date, or quarterly-group job to `restaurant-book`; its deployed scope
is the standing authorization and its coordinator owns both providers. Direct
OpenTable CLI access is intentionally outside this skill because its mutation
commands do not enforce the one-use approval binding below.

## Upcoming reservations

```bash
opentable-reservations --json
```

This read-only command uses the dedicated authenticated `opentable` PinchTab
profile. It succeeds only when that browser token matches the protected
expected-account binding and the page proves the entire upcoming list is
loaded. Pagination, virtualization, opaque cards, count mismatches, login
overlays, and parsing uncertainty fail closed. A successful response contains
only `provider`, `checked_at`, and normalized platform, restaurant, date, time,
party-size, status, and optional-location facts; reservation IDs remain
internal for duplicate detection. It never invokes `op`, exposes page text or
URLs, or prints browser, identity, or authentication data.

## Approval-gated Pinchtab booking

The `opentable-book.sh` script uses Pinchtab (headless browser automation) for
an attended, approval-gated OpenTable flow. It proceeds only when the search
renderer exposes an inspectable timeslot control. Current OpenTable result
cards can render visually inside an opaque component; when that happens the
wrapper returns `availability_renderer_unavailable` before any mutation. It
does not use coordinate clicks, infer availability from pixels, or silently
switch providers.

### Usage

Preview is the default. It may open OpenTable's booking-details page, but it
cannot click **Complete reservation**. A complete preview creates a protected,
short-lived, one-use approval ID bound to the exact restaurant, location, date,
time, party size, seating, payment, cancellation, no-show, venue, and form
facts.

```bash
# Preview only (same behavior with an explicit --dry-run)
opentable-book \
  [--dry-run] [--max-time-delta MINUTES] \
  "<search_term>" <date> [time] [party_size]

# Commit only the exact approved preview; do not repeat the search arguments
opentable-book \
  --confirm "<approval_id>"
```

The default maximum difference from the requested time is 30 minutes. Override
it during preview with `--max-time-delta MINUTES` or set
`OPENTABLE_MAX_TIME_DELTA_MINUTES`. Accepted values are 0-720 minutes. Approval
IDs expire after five minutes by default and are consumed on the first commit
attempt, including a rejected changed-facts attempt.

### Examples
```bash
# Preview by cuisine + area
opentable-book "Italian brookline newton" 2026-08-15 19:00 2

# Commit the approval ID returned by that preview
opentable-book \
  --confirm "<approval_id>"
```

### Output

Preview and commit each emit exactly one JSON object. Output is restricted to
reservation facts; it never includes page bodies, raw browser responses,
checkout data, booking URLs, cookies, or authentication tokens.

```json
{"success":true,"mode":"preview","status":"ready_to_confirm","approvable":true,"approval_id":"...","restaurant":"Example Restaurant","location":"123 Main St, Boston, MA","seating":"Standard","selected_date":"2026-08-15","selected_time":"7:15 PM","selected_party_size":2,"payment_required":"no","payment_terms":"No payment required at booking","cancellation_policy":"...","no_show_policy":"..."}
{"success":true,"mode":"preview","status":"terms_unknown","approvable":false,"approval_id":"","payment_required":"unknown","cancellation_policy":"unknown","no_show_policy":"unknown"}
{"success":true,"mode":"commit","status":"confirmed","approval_id":"...","confirmation_id":"...","restaurant":"Example Restaurant","confirmed_date":"2026-08-15","confirmed_time":"7:15 PM","party_size":2}
{"success":false,"mode":"commit","status":"unknown","error_code":"reservation_status_unknown","non_retryable":true,"mutation_attempted":true,"reservation_may_exist":true}
{"success":false,"mode":"preview","error_code":"outside_time_window","message":"Closest available time exceeds the configured maximum delta","requested_time":"19:00","closest_time":"8:00 PM","time_delta_minutes":60,"max_time_delta_minutes":30}
{"success":false,"mode":"preview","error_code":"availability_renderer_unavailable","message":"OpenTable result cards could not be safely inspected"}
```

### How it works
1. Acquires the persistent `opentable` profile as a managed headless Pinchtab instance and opens an isolated tab
2. Navigates to OpenTable search with cuisine, date, time, party size
3. Dismisses cookie consent overlay
4. Validates the date, requested time, party size, and maximum time delta
5. Selects the closest inspectable timeslot within the maximum delta, or fails closed when result-card contents are opaque
6. Validates the exact OpenTable HTTPS origin/route and one scoped final button
7. Extracts the complete booking and policy facts; unknown terms are not approvable
8. Stores complete preview facts atomically in a mode-`0700` cache with mode-`0600` state
9. On `--confirm <approval-id>`, consumes the ID and revalidates every bound fact
10. Persists `mutation_attempted` before clicking the single verified final button
11. Verifies the confirmation ID and exact reservation facts
12. Returns a non-retryable unknown/may-exist result after any ambiguous post-click failure

### Tips
- Treat `no_available_times` as an inspectable empty result; never reinterpret `availability_renderer_unavailable` as no availability
- Try multiple dates: e.g. both Fridays and Saturdays in the target week
- Treat every successful preview as a proposal, not a reservation
- Show every preview fact, including payment and policy terms, before asking for approval
- Pass only the returned approval ID to `--confirm`; never construct, reuse, or log it
- Never retry a non-retryable unknown result. Reconcile upcoming OpenTable reservations first
- metroId=7 is hardcoded (Boston metro area)

## Typical Workflow

1. For a canonical recurring date-night, double-date, or quarterly-group job, stop here and use `restaurant-book`; otherwise preview with `opentable-book "cuisine area" DATE TIME PARTY`
2. Show the user every returned restaurant, location, date, time, party, seating, payment, cancellation, and no-show fact
3. Continue only when `approvable` is `true`; if any detail is unsuitable or unknown, change the request and preview again
4. After explicit approval, run `opentable-reservations --json` and stop if the complete account read fails or already contains a conflicting reservation
5. Run `--confirm <approval-id>` with no other arguments
6. If the renderer is unavailable, report that safe inspection is blocked and stop without mutation; do not silently switch providers

For a specific restaurant, use its exact name and location as the search term.
Do not switch to the direct CLI when the search is inconvenient or incomplete.

## Safety Rules

- **Preview is always the default** — omitting mode flags can never complete a reservation
- **Ad-hoc bookings need exact user confirmation** — approve the complete preview, then use its one-use ID
- **A bare `--confirm` is invalid** — commit accepts only `--confirm <approval-id>`
- **Background monitoring belongs to `restaurant-snipe`** — do not bypass this gate with direct CLI or cron booking
- **Unknown terms block booking** — missing payment, cancellation, or no-show facts never produce an approval ID
- **Post-click unknown means may exist** — never retry it; reconcile reservations first
- **No post-attempt fallback** — after an attempted, possibly attempted, or unknown mutation, never try Resy, another restaurant, another date, or another time
- **Reconcile before mutation** — require a fresh successful `opentable-reservations` result; a failed lookup is never an empty account
- **Validate dates** — must be YYYY-MM-DD and not in the past
- **Party size** — must be 1-20
- **Time** — must be 24-hour HH:MM; never accept a selected or confirmed time outside `--max-time-delta`
- **Keep browser data private** — never include raw page text, URLs, cookies, tokens, checkout fields, or browser responses in output or logs

## Background monitoring

Use the `restaurant-snipe` skill for cancellation monitoring, polling, or
pre-authorized background work. Resy scopes may auto-book when explicitly
authorized; the current OpenTable snipe adapter remains notification-only even
though this skill has a standalone reservations reader. Do not create ad-hoc
background loops from this skill.

## Notes

- Pinchtab booking script: `~/.openclaw/workspace/scripts/opentable-book.sh`
- Upcoming-reservations reader: `~/.openclaw/bin/opentable-reservations`
- The attended browser path fails closed when OpenTable exposes only opaque result cards; it has no coordinate-click or provider-fallback mode
