# Event Bus Promotion Plan

## Status

IN PROGRESS — Cabin and Crosstown local-presence shadow enrichment are
established, and the Ring/August concurrent shadow evidence inventory is
current through August 3. User-facing delivery, automatic camera work, and
physical mutation remain disabled.

The starting production snapshot at `2026-07-26T19:45:02Z` was:

- SQLite schema 2 in `shadow` mode with health `ok`;
- zero pending or leased deliveries, dead letters, or ready spool files;
- Ring and August installed flags enabled, loaded, healthy, and independently
  reversible;
- Cabin local-presence enrichment enabled;
- Crosstown local-presence enrichment disabled;
- the deployed local-presence adapter byte-identical to tracked source;
- the strict Crosstown scanner activated and approved separately.

## Objective

Promote the event bus from a private observer into a narrowly scoped household
advisory layer without making it the authority for presence, access, cameras,
or existing automations.

The first active policy should turn only selected high-confidence shadow
decisions into one combined message. It must not replace or delay canonical
presence, Ring FCM and dog-walk processing, direct Ring ding behavior, the Nest
listener or reviewer, vacancy actions, the Cabin entry verifier, or August's
separately approved mutation commands.

Promotion is not a runtime flag flip. The current database and correlator are
structurally shadow-only. Limited delivery requires a reviewed schema
migration, a protected policy file, a separate sender, and an attended rollout.

## Household experience

### Julia

Julia should see no routine presence or movement reporting. If she explicitly
opts in, her route should contain only high-confidence household exceptions:

- unexplained person or doorbell activity while a site is confidently vacant;
- an unexplained unlock or door opening;
- optionally, one escalation when a door or lock remains unresolved beyond an
  agreed threshold.

Occupied-site activity, local network departures and returns, source health,
adapter failures, battery recovery, and routine resolutions remain silent.
Operational telemetry is not a household notification. Images remain disabled
unless a separate site-and-camera policy is explicitly approved.

### Dylan

Dylan may receive the same concise household exception plus a separate
operator-only route for degraded source or delivery health. OpenClaw remains
the read-only explanation surface for recent events, incident membership,
presence context, suppression, rate limiting, and subsystem health.

The notification should state evidence and uncertainty without inventing
identity or intent. A representative message is:

> Cabin is marked vacant. The driveway and front entry detected activity, and
> the door was opened. No resident arrival was detected during the following
> 90 seconds. Do you recognize this activity?

## Policy invariants

- Canonical presence remains outside the bus and is re-read before every active
  decision.
- Only `confirmed_vacant` permits a delivery candidate.
- `occupied`, `possibly_vacant`, stale, malformed, or unknown presence produces
  no delivery or camera work.
- Ring, August, and Nest evidence never establish occupancy.
- Generic Ring or camera motion remains journal-only and non-actionable.
- A fresh resident arrival resolves recent activity silently.
- Ring bursts and related August/Nest evidence collapse into one site incident.
- No model participates in validation or hard security decisions.
- No automatic lock, unlock, light, thermostat, camera-control, or other
  physical action is authorized.
- No image is stored in the event journal. Any current-image request is a
  separately authorized, exact-camera, short-lived workflow.
- Informational delivery is capped at one reserved attempt per site per hour.
- Reserve-before-send favors a rare missed notification over a duplicate
  alarming message.
- Every producer, consumer, site route, recipient route, camera policy, and
  sender remains independently reversible.

## Promotion sequence

### Stage 1 — Crosstown local-presence shadow enrichment

Goal: enable exact-device Crosstown local arrival/departure and household
excursion context now that the strict scanner has passed its separate approval
gate.

Activation gate:

1. Require the strict deployed scanner, exact approval, protected binding,
   fresh canonical state, and source/runtime adapter parity.
2. Require the local adapter to have no pending recovery batch.
3. Snapshot canonical state, event/incident counts, adapter state, bus health,
   queue depth, and outbound-attempt counters.
4. Atomically set only the installed Crosstown local-presence flag to `1`;
   preserve the Cabin flag and tracked disabled-by-default plist.
5. Restart only `ai.openclaw.presence-local-event-adapter`.
6. Require a silent Crosstown baseline: zero normalized events and no change to
   canonical presence, incidents, decisions, camera state, or physical state.
7. Require a duplicate-scan no-op and zero bus backlog/dead letters.

Rollback: set only the installed Crosstown flag to `0`, restart the same
adapter, and preserve its protected baseline and bus state for diagnosis.

### Stage 2 — Ring/August attended evidence and concurrent soak

Goal: prove the two independently reversible producers preserve legacy
behavior, normalize exact events once, and create no delivery attempts.

Ring gate:

- at least 48 hours of concurrent shadow operation;
- attended ding and person-motion evidence at both configured sites;
- exact alias/site binding, source-time precision, and restart/reconnect dedupe;
- unknown-device quarantine;
- identical direct-ding and dog-walk behavior with the bus enabled, disabled,
  or unavailable;
- no queue growth, duplicate/cross-site incident, or additional message.

August gate:

- seven days of shadow operation;
- attended manual lock, unlock, door-open, and door-close observations across
  poll boundaries;
- restart/dedupe, sleeping MacBook Pro, SSH outage, expired-auth, malformed
  response, contradictory state, timeout, and rate-limit evidence;
- provider-unsupported DoorSense may remain `unknown` without inventing an
  event;
- no automated unlock in any test;
- no queue growth, mutation-path regression, or delivery attempt.

Concurrent gate:

- treat Ring and August health and rollback independently;
- allow their evidence to join only same-site incidents;
- require deterministic suppression for occupied/uncertain presence;
- require unexplained vacant activity decisions within 120 seconds;
- require zero user-facing sends and zero automatic camera captures throughout
  the soak.

#### Evidence checkpoint — `2026-07-26T19:51:17Z`

Ring:

- The enabled concurrent shadow window began on July 23, so the 48-hour
  duration requirement is met.
- The safe producer projection reports health `ok`, 98 accepted and 98
  published records, zero failures, and zero drops.
- The newest 100 matching person events contain 74 Cabin `front_door`, 13 Cabin
  `driveway`, and 10 Crosstown `front_door` source-time records. This covers
  person evidence from all three configured aliases.
- No `entry.doorbell_rang` event is present in the seven-day query window, so
  attended ding evidence remains open.
- The producer retains eight historical quarantine outcomes from the
  previously unknown Sliding Door camera. Exact `driveway` binding recovery is
  live and current producer health is `ok`; the history is intentionally not
  erased.
- Live restart/reconnect dedupe and attended direct-ding/dog-walk parity remain
  open. The focused publisher tests cover exact binding, queue bounds,
  quarantine containment/recovery, duplicate handling, disabled/bus-failure
  isolation, and legacy-path preservation.

August:

- The adapter is healthy at this checkpoint with a current good observation,
  zero consecutive failures, no pending recovery file, and one normalized
  `lock.unlocked` transition.
- The current safe observation is `unlocked`; DoorSense remains `unknown`, and
  no battery zone is available.
- Three `source.unavailable` and three matching recovery events exercised the
  safe failure/recovery path. The unavailable reasons include one transport,
  one remote-observation, and one earlier generic observation failure.
- No `lock.locked`, `door.opened`, `door.closed`, or battery threshold
  transition is present. The manual physical cycle remains open, and unsupported
  DoorSense must not be treated as a failed or invented transition.
- The seven-day duration gate is not met; the concurrent shadow window is
  approximately day 3.

Concurrent:

- Both source health projections are `ok` with zero queued or failed source
  work. The bus has zero pending/leased deliveries, dead letters, ready spool
  files, or non-shadow notification rows.
- There are zero duplicate event UIDs and zero cross-site incident links.
- Forty-nine activity decisions containing Ring or August evidence completed
  with a maximum 95.5-second decision latency and zero decisions over 120
  seconds.
- Two older decision-latency outliers occurred on July 23 and July 24, but both
  were Nest-only Cabin incidents during rollout windows. July 25 and July 26
  decisions are again within approximately 96 seconds. Attribute the two
  historical pauses before active delivery even though they do not count
  against the Ring/August evidence slice.
- Thirty focused Ring/August tests pass under the declared Homebrew production
  Python. The first Ring test attempt under macOS system Python failed at import
  because that older interpreter cannot parse the production type syntax; it
  was not a source behavior failure.

#### August attended checkpoint — `2026-07-27T13:07:22Z`

- The normalized journal records `unlocked` to `locked` at
  `2026-07-26T22:06:22Z`, bounded by successful observations at `22:01:17Z`
  and `22:06:22Z`.
- It then records `locked` to `unlocked` at `2026-07-27T01:00:57Z`, bounded by
  successful observations at `00:54:50Z` and `01:00:57Z`. The manual
  lock/unlock cycle therefore crossed distinct poll boundaries and is
  complete.
- The latest safe observation remains `unlocked` with DoorSense `unknown` and
  no battery zone. There are no normalized `door.opened` or `door.closed`
  events. Door evidence remains conditional on the provider returning a known
  state and is neither failed nor inferred from `unknown`.
- The adapter has a current good observation, zero consecutive failures, no
  current error, no pending recovery file, and continued clean scheduled
  observations. Bus health remains `ok` with zero queued August work, backlog,
  or dead letters.

### Stage 3 — Limited-delivery foundation

Implement, but do not activate:

1. A transactional schema migration from hard-coded `shadow` to explicit
   `shadow|limited_delivery` mode.
2. A protected owner-only policy containing exact sites, incident classes,
   recipients, escalation threshold, and camera-disabled defaults.
3. A separate delivery consumer using durable leases and reserve-before-send.
4. Fixed safe message templates derived from normalized incident facts; no
   free-form model-authored security claims.
5. Delivery health, cooldown, burned-slot, unknown-outcome, and dead-letter
   status.
6. Independent rollback to shadow while ingestion and the journal continue.

### Stage 4 — Dylan-only limited canary

- Start with unexplained person, unlock, and door-open incidents while
  confidently vacant.
- Keep camera work disabled.
- Preserve existing direct Ring routes and explicitly audit overlap.
- Compare every delivered message with its shadow explanation.
- Stop for any duplicate, wrong-site route, occupied/uncertain delivery,
  unexplained delay, queue growth, or privacy regression.

### Stage 5 — Julia opt-in household route

- Obtain explicit recipient authorization.
- Route only the agreed high-confidence household exception classes.
- Keep operator/source-health messages Dylan-only.
- Verify no message exposes routine movement, raw identifiers, provider
  payloads, or unsupported certainty.

### Stage 6 — Optional camera evidence

This is a separate rollout, not a consequence of delivery activation. Decide
per site and exact camera whether an unexplained, confidently vacant incident
may request one current short-lived image and return a bounded description.
Never retain media in the bus or enable Crosstown camera delivery implicitly.

## Progress ledger

### `2026-07-26`

- [x] Documented household experience, policy invariants, promotion stages,
  rollback, and unresolved decisions.
- [x] Verified the starting bus is schema 2, mode `shadow`, health `ok`, with
  zero pending/leased deliveries, dead letters, or ready spool files.
- [x] Verified installed Ring and August flags are `1`, both jobs are loaded,
  both bus-observed sources are healthy, and neither has queued or failed
  source work.
- [x] Verified Cabin local enrichment is `1`, Crosstown is `0`, the local
  adapter job is loaded, and deployed adapter bytes match tracked source.
- [x] Completed Stage 1 activation preflight: protected adapter state and
  no-pending-batch checks; strict deployed scanner, exact approval, and binding
  validation; canonical-state/fresh-scan checks; tracked/runtime adapter parity;
  source and installed plist validation; 41 local-adapter, deployment, and
  correlator unit tests; and the presence-home-event integration test.
- [x] Enabled only the installed Crosstown local-presence flag while preserving
  the tracked disabled-by-default plist and the installed Cabin flag.
- [x] Verified the first Crosstown baseline and the immediate duplicate pass
  each produced zero events. The first guarded verifier expected stdout from
  the log-owning wrapper and therefore restored the flag after the already-safe
  zero-event baseline; the corrected direct-adapter verification then enabled
  the flag atomically and returned `no_new_observation`.
- [x] Verified two residents baselined present at Crosstown, no excursion was
  opened, canonical presence bytes were unchanged, the protected pending file
  remained absent, and the scheduled job returned another zero-event no-op.
- [x] Verified zero recent local-presence events, zero non-shadow notification
  rows, bus health `ok`, and zero backlog or dead letters after activation.
- [x] Recorded the Ring/August normalized-event, producer-health, incident,
  latency, quarantine, and failure/recovery evidence inventory.
- [x] Started the dated concurrent soak ledger. Ring has met its 48-hour
  duration and all-alias person-evidence gates; August is approximately day 3
  of 7.
- [ ] Capture an attended Ring ding at both sites and explicitly verify direct
  ding plus dog-walk parity.
- [ ] Capture a live Ring restart/reconnect dedupe boundary.

### `2026-07-27`

- [x] Completed a manual August lock/unlock cycle across distinct poll
  boundaries.
- [ ] Attempt door open/close evidence only if DoorSense begins returning a
  known state; never invent it from `unknown`.
- [ ] Complete the remaining August soak and failure-mode evidence.
- [ ] Attribute the two historical Nest-only correlator latency pauses before
  any delivery implementation.

### `2026-08-03`

- [x] Verified 306 normalized events and 113 incident decisions in the prior
  seven days with zero pending/leased work, dead letters, duplicate event IDs,
  cross-site joins, or open incidents. Every decision completed within 95
  seconds and no outbound attempt occurred.
- [x] Established local-presence shadow enrichment at both sites from organic
  departure/return intervals. Cabin transitioned to confirmed vacant when
  Julia relocated to Crosstown on August 2 instead of remaining partially
  occupied.
- [x] Household evaluation identified the three July 27–28 vacant-Cabin
  shadow windows as Julia doing yard work or expected excursions such as dog
  walks. They are expected resident activity, not household security
  exceptions.
- [x] Separated historical unresolved-access attention from current bus
  health and added an audited operator review action that preserves the
  incident record.
- [x] Added durable August successful/failed poll counts and latest/maximum
  successful-observation gaps so a quiet soak can be evaluated without
  publishing heartbeat events.
- [ ] Complete Ring doorbell-ding and restart/reconnect parity evidence before
  limited delivery.

## Decisions required before limited delivery

1. Whether Julia opts into household exception messages.
2. Which sites and incident classes route to Dylan, Julia, or both.
3. The unresolved door/unlocked escalation threshold.
4. Whether the existing direct Ring ding should coexist with, suppress, or
   eventually yield to a combined bus follow-up.
5. Whether any exact camera at either site may participate in a later,
   separately approved rollout.

No decision above blocks continued shadow evidence collection.
