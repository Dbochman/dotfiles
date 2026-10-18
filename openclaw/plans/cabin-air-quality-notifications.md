# Cabin ventilation notifications

**October 12 deployment:** Live after the approved one-time test
`AIR-20261012T141319Z` was machine-receipted and Dylan personally confirmed
receipt. The protected test record stores that confirmation separately from
transport submission. Three deployed files matched source hashes; source and
sampler integration passed 71 focused regressions. No service restart, extra
sensor read, schedule change, or synthetic production episode was used.
The next ordinary sample at 10:15:52 Eastern confirmed collection success and
`notifications.mode=live`, with `reason=observing` and no automatic message.
The climate dashboard's loopback current-state API also returned valid JSON.
Dylan confirmed on October 16 that the organic Cabin CO2 automation worked.

**October 16 follow-up:** Add a paired recovery text after sustained good CO2
and VOC readings. No test message or historical recovery replay is authorized
for this change. The next organic recovery remains the delivery acceptance check.
Deployed the helper and sampler only, with matching source hashes, after 93
focused tests, Python compilation, repository validation, and diff checks passed.
Under the notification lock, preserved an owner-only schema-1 state backup and
migrated state without changing live mode or prior alert receipts. No episode
was active at deployment. The ordinary 19:24:37 Eastern sample verified schema 2,
successful collection and `notifications.mode=live`, `reason=observing`, with
no message sent. The sampler retained its five-minute schedule and exited zero;
the climate current-state API returned HTTP 200 and valid JSON. No restart,
extra sensor read, or historical replay occurred.

**October 18 receipt/recovery repair:** The October 17 initial texts were locally
delivered but recorded as unconfirmed after ambiguous CLI outcomes. Schema 3
adds exact local receipt reconciliation without resends and tracks qualifying
episode pollutants. The deployed helper and the shared receipt matcher passed
115 focused tests. Under the notification lock, retained an owner-only schema-2
backup and confirmed each existing initial message against its exact recipient,
text, and bounded attempt window before correcting the two receipt entries.
Scoped sampler history established that only CO2 qualified in the active
episode; its trigger set was migrated accordingly and its good timer reset.
Live mode, sampler schedule, and sensor state were preserved. No test message,
historical recovery replay, extra sensor read, or service restart was performed.
The next ordinary 09:56:47 Eastern sample verified live schema-3 processing,
successful collection, both preserved confirmed receipts, and
`episode_already_notified`. CO2 remained elevated, so no recovery timer or
follow-up was started. The climate current-state API returned HTTP 200.

## Scope and rollout

Owner-approved October 12, 2026: implement an occupant-specific ventilation
nudge using the existing five-minute Cabin Living Room Airthings sampler.
Start in shadow mode, validate decisions, then send one explicitly approved
test to Dylan. Wait for human receipt confirmation before live activation.
No additional model cron, BLE scan, home-event source, camera access, weather
API, HVAC action, or window control is added.

## Policy

- Only `airthings_ble_sampler_v1` samples for Cabin/Philly Living Room count.
  Historical CSV imports and merged HVAC records cannot trigger notifications.
- CO2 at least 1,000 ppm continuously for 10 minutes, or VOC at least 2,000 ppb
  continuously for 15 minutes. Humidity-only poor status does not qualify.
- Samples must be no more than 10 minutes old, at most 60 seconds future,
  strictly increasing and no more than 7.5 minutes apart. Duplicates and older
  observations never advance timers. Gaps restart evidence, not delivery flags.
- Recover and rearm after the pollutants that qualified during the episode
  remain in their good bands for 15 minutes with continuous fresh samples:
  CO2 below 800 ppm and/or VOC below 250 ppb. A CO2-only episode does not wait
  for untriggered VOCs (and vice versa). If another pollutant qualifies before
  recovery, add it to the episode and restart the good timer. These match the sensor's
  good bands; they are not medical limits or a substitute for smoke/CO alarms.
- Deliver 08:00–22:00 America/New_York. Reevaluate fresh conditions after quiet
  hours; do not queue a stale alert for the morning.
- A recipient must be Dylan or Julia, canonically at the Cabin with fresh
  occupied state and positive evidence in the Cabin scan, each at most 30
  minutes old. Fresh contradictory Crosstown evidence suppresses that person.
  A retained last-known location alone never qualifies. Guests are not inferred.
- At most one combined alert per resident per episode. A newly arrived resident
  can receive one while someone already alerted is not notified again.
- At recovery, send at most one follow-up only to recipients whose initial alert
  has a validated transport submission or an exact local delivered receipt and
  who still pass fresh Cabin presence. Unreconciled reserved/unconfirmed or shadow-only initial deliveries cannot authorize a
  live recovery text. The same quiet hours and immediate delivery rechecks apply.
- Recovery is evaluated only on the new sample that completes the good interval.
  If quiet hours, absent/unknown presence, or unavailable delivery suppress it,
  skip it rather than queue stale close-window advice for morning or a return.
  A crash after a recovery reservation also skips remaining recipients on restart.
  Clearing the episode still permits a future elevated episode to notify normally.
- Explain the triggering measurement and suggest opening a window briefly
  **if weather and outdoor air quality permit**. For VOCs, also suggest checking
  nearby sources. Do not claim the automation checked outdoor air or weather.
- Recovery text names only the episode's recovered pollutants and includes
  current CO2/VOC measurements. Conditional close-window advice is included only
  when both current measurements are good. Otherwise it explicitly notes the
  other pollutant is above its good band and this is not an all-clear to stop
  ventilating. It does not establish whether cooking has stopped, windows are
  open, or unmeasured pollutants are safe.

## Implementation and failure boundaries

`bin/airthings-notify.py` consumes the already appended sampler record on stdin.
It never refreshes the sensor or writes presence. The existing sampler invokes
it in a bounded subprocess after history append; notification failures remain
separate from successful collection in `snapshot-status.json.notifications`.
Health includes the allowlisted `kind` (`elevated` or `recovery`) when a sample
qualifies for either decision, without message text or transport details.

Private runtime: `~/.openclaw/airthings/notifications/` (0700), with 0600
`config.json`, `state.json`, and a lock. Initialize explicitly with
`/usr/bin/python3 ~/.openclaw/bin/airthings-notify.py init`; this refuses to
overwrite an existing runtime. Missing, corrupt, unsafe, or unsupported state
fails closed rather than losing deduplication history.

State schema 3 retains the active episode's qualifying pollutants, the most
recent recovery, and bounded per-recipient attempt evidence (timestamp, sample,
template triggers, and a recipient-route hash; no credentials, chat IDs, or
arbitrary message text). A changed recipient route cannot confirm an old attempt.
Exact schemas 1 and 2 migrate while preserving original receipts and resetting
the recovery timer. Unknown legacy episode triggers conservatively require both
pollutants unless an attended migration establishes them from scoped history.
Already closed episodes are not rebuilt or replayed. Configuration remains schema 1. Deploy under the notification lock,
preserve an owner-only pre-migration state copy, and verify the next ordinary
sample without forcing a BLE read or evaluating historical records.

Configuration is exactly `{"schema":1,"mode":"shadow"}`. Supported modes are
`off`, `shadow`, and `live`. Mode changes are attended operator changes, never
model/skill actions. Deployment preserves runtime mode and state. Shadow mode
does not load credentials or call a sender, and its decisions are not receipts.

Live delivery resolves only distinct protected Dylan/Julia chat routes and
gateway authentication from the existing owner-only cache. No shell evaluation,
`op`, arbitrary recipients, or configurable message text is allowed. It reuses
`home-event-delivery.py`'s sender and receipt validator without creating bus
events or modifying the security incident policy/database.

Reserve each recipient and exact attempt evidence durably before sending, then
record `submitted` after the transport validates its machine receipt. On a send
exception, also check the local Messages database read-only for exactly one
delivered outgoing iMessage matching the recipient, generated text, and bounded
attempt window. A timeout alone is not evidence that the message was lost.
Unresolved attempts remain `unconfirmed`; a crash may leave `reserved`. Subsequent
fresh live samples reconcile these receipts for up to seven days, without
resending. Missing, ambiguous, pending, failed, or unavailable evidence cannot
promote a receipt. Shadow/off mode does no reconciliation. A late recovery
receipt never replays a skipped follow-up or changes who was eligible. A valid transport
receipt is not proof the recipient read or received the message. Recheck mode,
quiet hours, sample freshness, and recipient presence immediately before send.

Inspect without actuation:

```bash
/usr/bin/python3 ~/.openclaw/bin/airthings-notify.py status
```

To pause, set the protected config's mode to `off` atomically; do not delete
state or edit historical delivery flags. No gateway restart or schedule change
is required. Sampler scheduling and the climate-history writer are unchanged.

## Acceptance

- Unit tests: threshold duration, duplicate/stale/gapped samples, humidity-only
  poor status, recovery hysteresis, both owners, stale/contradictory presence,
  quiet hours/DST, persistence, corruption, permissions, locks, ambiguous sends,
  crash-after-reservation, policy changes, and sampler failure isolation.
- Deploy helper and sampler only; initialize shadow state and verify live
  cache/history decisions without extra BLE calls or messages.
- Send exactly one labeled test to Dylan after approval; persist its attempt
  before sending and never retry an ambiguous outcome. Confirm receipt with
  Dylan before changing shadow to live. No synthetic sensor event goes live.
- Confirm a subsequent ordinary sample reports the intended mode and keep the
  next organic recovery notification as the remaining end-to-end acceptance check.
