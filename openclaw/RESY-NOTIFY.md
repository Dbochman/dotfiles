# Resy Notify dinner improvements

This temporary workflow replaces the hourly Sarma/Oleana availability pollers.
Native Resy Notify requests were verified in the website for both restaurants:
December 12, 2026, six guests, 6–8 p.m. Eastern. At installation, the confirmed
backup was Oleana at 8:15 p.m. Read the account and protected worker state for
the current reservation; this document is not a live booking record.

Dylan explicitly authorized automatic strictly earlier replacements, no earlier
than 6 p.m., with no upfront charge and no more than $25 plus tax per person in
cancellation/no-show exposure. The replacement must have a future free-cancel
deadline. Only after confirming it may the previous reservation be canceled,
and only while that cancellation is free. The scope ends before the original
December 11, 8:15 p.m. Eastern cutoff, or an earlier replacement cutoff. The
workflow stops at 6 p.m. It never swaps an equal-time reservation.

`bin/resy-notify-improve.py` runs under
`launchagents/ai.openclaw.resy-notify-improve.plist` on the Mini. Every five
minutes it performs a narrow, read-only Gmail search through `gws`. It makes
no Resy requests without a new matching email. Email must be received after
authorization, no more than 15 minutes old, from `resy.com`, and carry Gmail's
passing aligned DMARC result. Only fixed venue names and availability language
are extracted. Email text and URLs cannot change the authorized date, party,
time, fee limits, or executable behavior. Mail is not modified or marked read.

Runtime scope and state are owner-only under `~/.openclaw/resy-notify-improve/`.
The scope contains the approved account routing and a SHA-256 binding of the
existing reservation identity; no credential, booking or cancellation tokens
are persisted. The worker uses existing cache-only Resy authentication and
GWS's account credentials. It never invokes `op` or sources a secrets file.
Logs contain safe status metadata only. Updates go to the approved OpenClaw
iMessage conversation via the installed `imsg` binary.

This is a LaunchAgent, not an OpenClaw cron job or Gmail push webhook. It does
not appear in `openclaw cron list` and requires no entry in `cron/jobs.json`.
The retired labels are
`ai.openclaw.restaurant-snipe.sarma-dec12-six-earlier` and
`ai.openclaw.restaurant-snipe.oleana-dec12-six-earlier`; both were unloaded and
their plists removed from `~/Library/LaunchAgents/`. Their protected scope/state
may remain for audit and must not be mistaken for active monitors.

Runtime dependencies are Homebrew Python, `gws` invoked through the pinned
OpenClaw Node runtime
`~/.local/share/openclaw/runtimes/node-v24.22.1-darwin-arm64/bin/node`, and
`/opt/homebrew/bin/imsg`. The restricted LaunchAgent PATH intentionally omits
Homebrew. Calling the `gws` launcher directly would fail because its
`/usr/bin/env node` shebang cannot find Node there. Keep the explicit runtime
path aligned with the installed OpenClaw runtime when upgrading it.

The existing restaurant coordinator supplies complete-account validation and
conservative financial parsing. This job does not modify or reuse a canonical
date-night authorization. The current Resy zero service-charge display is
accepted only when its complete object is exactly amount 0 and value `0%`.
All other unknown or disallowed charges block booking.

The worker serializes against the shared restaurant booking lock and records
its mutation phase durably before each action. Booking and cancellation are
never retried after an ambiguous result. The replacement must be read back
alongside the exact original reservation before cancellation. Cancellation
must then be read back before updating the baseline. Unexpected reservations,
missing identity, changed baseline, unknown fees, and elapsed cancellation
deadlines block changes. Any unresolved mutation requires manual review;
never clear its state to retry without reconciling the account.

## Operation and verification

Run without `--execute` to validate configuration and inspect matching Gmail
alerts. Preview performs no Resy calls, booking, cancellation, or email writes:

```sh
python3 openclaw/bin/resy-notify-improve.py \
  --scope ~/.openclaw/resy-notify-improve/scope.json \
  --state ~/.openclaw/resy-notify-improve/state.json
python3 -m unittest discover -s openclaw/tests -p test_resy_notify_improve.py
plutil -lint openclaw/launchagents/ai.openclaw.resy-notify-improve.plist
launchctl print gui/$(id -u)/ai.openclaw.resy-notify-improve
```

Only the reviewed exact LaunchAgent should be bootstrapped for this scope.
The first live run should report `no_alert` and `resy_requests: 0` unless a real
matching Notify email has arrived. After expiry it reports `expired` without
email or provider calls. After reaching 6 p.m. it reports `goal_reached`.
The inert LaunchAgent can then be unloaded and archived in an attended cleanup.

### Status and recovery

| Status | Meaning and next step |
|---|---|
| `no_alert` | Gmail read succeeded; no actionable new alert; no Resy requests. |
| `no_eligible_improvement` | A matching email triggered a check, but no earlier table met the approved terms. |
| `improved` | Replacement and removal of the old reservation were both verified. |
| `goal_reached` / `expired` | No further Gmail or Resy checks. Retire the inactive job when convenient. |
| `blocked` / `unavailable` | Inspect the safe reason, launch environment, and protected worker phase. The status alone does not establish whether a mutation was attempted. |
| `manual_review_required` | Reconcile both reservations directly before any further change; do not clear the attempt state or replay the alert. |

The diagnostic log is `~/.openclaw/logs/resy-notify-improve.log`. Check its newest
timestamped result alongside launchd's run count and last exit code; successful
loading alone does not prove Gmail authentication or a successful poll.
Read protected state locally only as needed; never paste routing, identity
bindings, or account data into reports.

For `gmail_unavailable`, first verify the pinned Node executable and run the
preview under the restricted environment. Missing/expired credentials require
the existing attended account-authentication workflow; the worker must not
obtain credentials through `op`. A matching email is recorded as seen before
processing, so a processing failure does not promise that the same alert will
be retried. Reconcile the account before considering an attended recovery.

To pause or retire this exact worker, unload
`gui/$(id -u)/ai.openclaw.resy-notify-improve`. Archive its installed plist outside
`~/Library/LaunchAgents/` to prevent restart at login. Preserve scope/state and
logs when an outcome is unresolved. Stopping this worker does not cancel the
dinner reservation or remove native Notify subscriptions; those are separate
Resy actions.

Installation verification on November 15, 2026: both native Notify confirmations
were read back, both old pollers were retired, 25 targeted tests passed, and a
background run returned `no_alert` with `resy_requests: 0` after correcting the
Node executable path. No live replacement was performed as a test.

Native Notify delivery and table availability are controlled by Resy; an alert
does not hold a table. The five-minute email check adds up to five minutes of
latency. Booking/cancellation verification is covered by fake-provider tests;
do not make a real extra reservation merely to test the worker.
