---
name: restaurant-snipe
description: Stage tightly scoped background reservation monitoring for OpenTable or Resy after the user explicitly preauthorizes the bounded scope. OpenTable is read-only until its adapter gains a live reservations lookup; Resy may auto-book after approval. Use when the user asks to watch for cancellations or auto-book a specific venue over a bounded period. Not for one-shot restaurant search, availability checks, attended bookings, or cancellations; use the platform skill for those.
allowed-tools: Bash(restaurant-snipe:*)
metadata: {"openclaw":{"emoji":"🎯","requires":{"bins":["python3","opentable","resy","restaurant-snipe"]}}}
---

# Restaurant Snipe

Stage a reviewable authorization and per-user LaunchAgent for a bounded,
cache-only reservation monitor. Staging never deploys, loads, books, or changes
a live LaunchAgent.

## Safety contract

- Default to read-only availability monitoring. Auto-book only after the user
  explicitly approves the complete scope below in the current conversation.
- OpenTable monitoring currently executes read-only because its adapter cannot
  perform the required live existing-reservation check. Even a separately
  approved, digest-bound `auto-book` scope must not create an attempt marker or call
  the booking endpoint while that capability is absent.
- Treat “watch,” “monitor,” or “snipe” alone as insufficient authorization to
  book. Do not infer missing values or reuse approval from another job.
- Never put a 1Password token in a job, source `.env-token` or
  `.secrets-cache`, or let a LaunchAgent invoke `op`. Refresh provider caches
  interactively before staging; a cache miss at runtime fails closed.
- Never print or persist availability/config/book/reservation/auth/payment
  tokens or raw provider responses. Logs and notifications contain safe facts
  only.
- Never deploy directly from this skill. Generate artifacts in a staging
  directory, show the user the safe summary, and wait for a separate explicit
  instruction to copy and bootstrap the reviewed files.
- Never retry an ambiguous booking attempt. The runner records a protected
  attempt marker and requires manual account review.

## Required preauthorization

Record every field explicitly:

1. Approval ID, approver, and timestamp
2. Declared, digest-bound booking mode: `read-only` or `auto-book`
3. Platform plus verified venue ID and venue name
4. Exact candidate date or dates
5. Party size
6. Target time, earliest/latest time window, and maximum time delta
7. Monitoring duration, absolute expiry, and polling interval
8. Notification target supplied by the user
9. A fresh read-only existing-reservation check: timestamp, source, and a
   normalized token-free snapshot

The absolute expiry must equal the authorization timestamp plus the approved
duration. A reservation already inside the requested time window blocks
staging.

## Attended staging workflow

1. Load the appropriate platform skill, then resolve and verify the venue with
   its attended read-only `info` or `search` command.
2. Review the booking account for existing reservations. Resy supports
   `resy reservations --json`; for OpenTable, inspect My Reservations in an
   attended authenticated session because the CLI has no reservations command.
3. Save only normalized, non-secret facts to a temporary JSON file:

```json
{
  "reservations": [
    {
      "platform": "resy",
      "venue_id": "example-id",
      "date": "YYYY-MM-DD",
      "time": "19:00",
      "party_size": 2,
      "status": "confirmed"
    }
  ]
}
```

Use an empty `reservations` array only after the attended check confirms none.

4. After the user approves every required field, stage artifacts outside
   `~/.openclaw` and `~/Library/LaunchAgents`:

```bash
restaurant-snipe stage \
  --output-dir /tmp/restaurant-snipe-review/<job> \
  --slug <job> \
  --authorization-id <approval-reference> \
  --approved-by <approver> \
  --authorized-at <ISO-8601-with-timezone> \
  --platform <opentable-or-resy> \
  --booking-mode <read-only-or-auto-book> \
  --venue-id <verified-id> \
  --venue-name <verified-name> \
  --date <YYYY-MM-DD> \
  --party-size <number> \
  --target-time <HH:MM> \
  --window-start <HH:MM> \
  --window-end <HH:MM> \
  --max-delta-minutes <minutes> \
  --duration-seconds <seconds> \
  --expires-at <ISO-8601-with-timezone> \
  --poll-interval-seconds <300-to-3600> \
  --notification-target <explicit-chat-id> \
  --existing-reservations-json /tmp/<job>-existing.json \
  --existing-reservation-checked-at <ISO-8601-with-timezone> \
  --existing-reservation-source <account-view-used>
```

OpenTable may use digest-bound `auto-book` only after the user explicitly approves
that mode, but its current adapter still downgrades matches to notified
read-only results because it cannot perform the live account-reservations check
required for mutation. The helper emits only `authorization.json` and a plist in the staging
directory. Inspect both and summarize the approved venue, dates, party, time
policy, declared booking mode, duration, expiry, and whether an existing conflict
was found. Do not display the notification target.

## Runtime guarantees

The tracked runner performs one poll per LaunchAgent invocation:

- Recompute the authorization fingerprint and reject edits or expired scope.
- Require schema v2 with a declared booking mode in both the fingerprint and
  authorization digest. Schema-v1 jobs fail closed and must be restaged; a
  newly available provider capability cannot upgrade a read-only job.
- Acquire a per-scope lock and check retained completion/attempt markers.
- Check local receipts and, for Resy, query existing reservations before
  availability. Reject malformed or error-bearing reservation responses rather
  than treating them as an empty account.
- Select only a structured slot inside every approved date/time constraint.
- Load the existing provider module with its `_op_read` fallback disabled and
  a PATH that excludes Homebrew `op`/`timeout`.
- Immediately before any mutation, repeat the live reservations check. Missing,
  malformed, or failed lookup capability is never equivalent to an empty list.
  OpenTable therefore reports a safe read-only match and keeps monitoring;
  only an adapter with an explicit normalized live lookup may proceed.
- Serialize all booking mutations through one account-wide lock and recheck
  cross-job receipts and unresolved attempt markers while holding it. An
  ambiguous attempt in any overlapping scope blocks other jobs pending review.
- Persist a token-free booking-attempt marker before one exact booking call.
- Require the tracked provider adapter's pre-mutation deadline contract. The
  adapter first reserves rate-limit capacity for one guard GET and one booking
  POST, repeats the live reservations check with the first slot, rebuilds fresh
  auth headers, and consumes the second slot without an intervening wait before
  rechecking expiry and sending the POST. Booking redirects are disabled, and
  transport failures, redirects, and rate-limit responses are never retried.
- For OpenTable, accept success only when a structured confirmation ID and an
  affirmative success/confirmed signal are both present and no false status or
  nested error contradicts them. Exit status, an ID alone, `success: true`
  alone, or matching human text is insufficient. Resy requires an exact
  reservation read-back after every non-exceptional booking call.
- On a confirmed booking or confirmed existing reservation, atomically retain
  a token-free receipt, send the preauthorized notification, then unload the
  exact job and remove only its deployed config/plist. If unload fails, retain
  the artifacts. Notification failure never re-arms booking.
- On an ambiguous result, retain the job and marker for attended review; later
  runs make no booking call. On expiry, make no booking call and leave cleanup
  for an attended action.
- For digest-bound read-only jobs, send one token-free “availability found, not
  booked” notification per continuously available date/time and retain the job.
  Clear the notification marker after a no-match poll so a later reappearance
  can notify again.

## Helper

`restaurant-snipe` is the skill-local attended wrapper for staging. It rejects
the internal `run` subcommand; staged plists invoke the tracked Python runner
directly. Do not call the runner against a live authorization merely to test
the skill.
