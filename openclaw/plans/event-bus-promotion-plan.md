# Event Bus Promotion Plan

## Status

IN PROGRESS — Cabin and Crosstown local-presence enrichment are established,
the Ring/August concurrent shadow evidence inventory is closed, and the
Dylan-only limited-delivery canary is active. Its separately approved camera
evidence layer uses short-lived exact +30/+60 stills and retains only a bounded
structured result. Julia routing and physical mutation remain disabled.

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
Operational telemetry is not a household notification. Images are captured
only under the separately approved exact site-and-camera policy.

### Dylan

Dylan may receive the same concise household exception plus a separate
operator-only route for degraded source or delivery health. OpenClaw remains
the read-only explanation surface for recent events, incident membership,
presence context, suppression, rate limiting, and subsystem health.

The notification should state evidence and uncertainty without inventing
identity or intent. A representative message is:

> Cabin is marked vacant. The driveway and front entry detected activity, and
> the door was opened. No resident arrival was detected during the following
> 15 minutes. Do you recognize this activity?

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
- require unexplained vacant activity decisions within 12 minutes;
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

#### Stage 2 closure — `2026-08-05`

- The operator explicitly waived the remaining attended Cabin doorbell press
  because it would delay the rollout for several days. The waiver applies only
  to that evidence item; exact Cabin bindings, person-event publication,
  quarantine recovery, disabled/failure isolation, dedupe, and legacy-path
  preservation remain covered by live history and focused tests.
- Crosstown attended ding, supervised direct-message delivery, and two
  restart/reconnect boundaries passed. Ring remained healthy at 169 accepted
  and 169 published records with zero failures or drops.
- August completed the seven-day soak with 492 successful and 25 failed polls,
  zero consecutive failures, no current error, a 313-second latest successful
  observation gap, and a 579-second maximum gap. Three organic unavailable /
  recovered pairs cover the read-only adapter's health projection.
- Manual August lock/relock transitions crossed polling boundaries. DoorSense
  remains provider-unknown, so door open/close evidence is closed as
  not-applicable unless the provider later exposes known data. No automated
  mutation or synthetic failure was introduced.
- The bus was healthy with zero pending/leased work, dead letters, ready spool
  files, or delivery attempts. Stage 2 is closed under this recorded waiver.

### Stage 3 — Limited-delivery foundation

Implemented and initially deployed in shadow mode before the separate Stage 4
operator action:

1. [x] A transactional schema migration from hard-coded `shadow` to explicit
   `shadow|limited_delivery` mode.
2. [x] A protected owner-only policy containing exact sites, incident classes,
   recipients, escalation threshold, and camera-disabled defaults.
3. [x] A separate delivery worker using durable reserve-before-send and a
   one-attempt crash boundary.
4. [x] Fixed safe message templates derived from normalized incident facts; no
   free-form model-authored security claims.
5. [x] Delivery health, cooldown, burned-slot, unknown-outcome, and dead-letter
   status.
6. [x] Independent rollback to shadow while ingestion and the journal continue.

The tracked delivery policy is active for the Stage 4 canary. Its scope is both
residences, Dylan only, the
`person_activity`, `access_activity`, and `person_and_access` classes, a fixed
15-minute arrival grace, a one-hour per-site cooldown, a five-minute
reservation TTL, a recorded 30-minute unresolved-access threshold, and exact
Cabin Ring `driveway`/`front_door` plus Nest `Kitchen` and Crosstown Ring
`front_door` plus Nest `Living Room Wired` camera evidence.

### Stage 4 — Dylan-only limited canary

- Start with unexplained person, unlock, and door-open incidents while
  confidently vacant.
- Begin with camera work disabled, then enable it only through the separate
  Stage 6 exact-camera gate.
- Preserve existing direct Ring routes and explicitly audit overlap.
- Compare every delivered message with its shadow explanation.
- Stop for any duplicate, wrong-site route, occupied/uncertain delivery,
  unexplained delay, queue growth, or privacy regression.

#### Activation checkpoint — `2026-08-05T15:42:14Z`

- Commit `4d2b77b` passed the combined 181-test suite and was pushed to
  `origin/main` before activation.
- The protected runtime policy was installed with `active=true`, retaining the
  exact Dylan-only, both-site, three-class scope and camera-disabled boundary.
- Runtime mode changed atomically from `shadow` to `limited_delivery` with zero
  burned or unknown reservations.
- The first post-activation projection reports bus and delivery health `ok`,
  all sources healthy, zero backlog/dead letters, and zero reserved, sent,
  burned, unknown, or delivery dead-letter rows.
- The existing occupied Crosstown activity incident already has a terminal
  suppression decision and is not eligible for replay.

### Stage 5 — Julia opt-in household route

- Obtain explicit recipient authorization.
- Route only the agreed high-confidence household exception classes.
- Keep operator/source-health messages Dylan-only.
- Verify no message exposes routine movement, raw identifiers, provider
  payloads, or unsupported certainty.

### Stage 6 — Active exact camera evidence

This remains a separate rollout rather than a consequence of delivery
activation. The approved schema-v3 implementation binds Cabin Ring `driveway`
and `front_door` plus Nest `Kitchen`, and Crosstown Ring `front_door` plus Nest
`Living Room Wired`. A fresh person, unlock, or door-open event may schedule
evidence at +30 and +60 seconds only while the site is canonically confirmed
vacant and the camera policy is active. An attached Ring event selects its
exact camera; access- and Nest-only triggers fall back to the site's Ring front
door. Backfill, generic motion, stale events, uncertain/occupied presence,
source health, and local-presence inference never capture.

Each still is owner-only, validated, classified locally as only visible person
or no visible person with bounded uncertainty, and deleted immediately. The
bus retains only `person_visible`, `no_person_visible`, `uncertain`, or
`unavailable`; no image path, prose, identity, or frame enters the journal or
message. This evidence may add one fixed clause to an independently eligible
Dylan notification. It never creates, suppresses, accelerates, or delays one.
Rollback to `shadow` cancels pending evaluations under the same lock used by
capture and delivery.

Each +30/+60 slot aggregates its exact Ring and Nest targets. Any
medium-or-high-confidence visible person produces `person_visible`; every
target must return a clear result for `no_person_visible`. A partial provider
failure produces bounded uncertainty and degrades camera health without
discarding successful evidence from the other provider.

#### Camera activation checkpoint — `2026-08-05T16:34:03Z`

- The focused 89-test home-event suite and complete OpenClaw Python test
  discovery passed before deployment; Python compilation, shell syntax, plist
  validation, and diff hygiene also passed.
- The canary returned to `shadow` with zero burned or unknown reservations.
  All six existing jobs were stopped before the attended schema 3 to schema 4
  migration, and a mode-`0600` SQLite backup passed `quick_check`.
- Fresh attended probes confirmed exact Cabin `Kitchen` and Crosstown
  `Living Room Wired` still capture. The non-wired `Living Room` alias rejected
  live capture, so it was not selected. Every probe frame was removed before
  deployment.
- The protected schema-v2 policy was installed with `active=true` and
  `camera_enabled=true`. The tracked schema-v3 policy retains those exact Nest
  bindings and adds the reviewed exact Ring `driveway`/`front_door` bindings.
- All seven jobs loaded in shadow before the atomic return to
  `limited_delivery`. The first active projection reports schema 4, bus,
  delivery, and camera health `ok`; all sources are healthy; queues, ready
  spools, pending evaluations, reservations, outcomes, and dead letters are
  zero; and the protected camera image directory is empty.
- No synthetic household event or capture was injected. The first organic
  eligible event remains the live camera-evidence evaluation point.

#### Arrival and delivery correction checkpoint — `2026-08-07T22:40:59Z`

- The first organic camera-backed Cabin reservation ended in an ambiguous
  sender timeout. Dylan confirmed that no message arrived. Schema 5 retains
  the `unknown` outcome, records the explicit `not_received` review, and keeps
  delivery attention degraded until that review is present; it never retries
  the send.
- The arrival grace is now fifteen minutes. The correlator also creates a new
  vacancy-scoped decision boundary when an occupied incident still records an
  unlocked lock or open door as the site becomes confirmed vacant. A matching
  lock/close during that new grace resolves the carried state silently.
- The Crosstown manual lock was observed at `2026-08-07T22:23:02Z` and resolved
  the live incident four seconds later. Its history retained the exact repaired
  shape: an occupied terminal decision, a later unlock, then the household
  vacancy transition.
- A mode-`0600` schema-4 backup passed `quick_check`; all seven jobs then passed
  schema-5 shadow smoke runs with exit code 0. The active projection reports
  the fixed 900-second grace, exact camera bindings, healthy bus/delivery/camera
  state, zero pending or leased work, zero dead letters, no unreviewed delivery
  outcomes, and an empty camera-image directory.

#### Ring-plus-Nest camera expansion checkpoint — `2026-08-08T02:27:42Z`

- Delivery policy schema 3 now names exact provider bundles. Cabin allows Ring
  `driveway`/`front_door` plus Nest `Kitchen`; Crosstown allows Ring
  `front_door` plus Nest `Living Room Wired`. Provider identifiers remain
  behind the Ring safe-binding boundary and never enter policy, status, the
  journal, or an image-analysis prompt.
- The worker selects attached Ring trigger aliases, falling back to the site's
  Ring front door for access- and Nest-only incidents, and combines that view
  with the site's Nest interior camera. Every target must be clear for a
  `no_person_visible` result; a partial provider failure stays uncertain and
  degrades camera health without discarding the other provider's evidence.
- Python compilation, Ring shell syntax, JSON validation, diff hygiene, and
  the combined 127-test event-bus/Ring/deployment suite passed. The Ring
  capture binding is also checked against the listener's exact device map.
- A mode-`0600` database backup passed `quick_check`, and the prior protected
  policy was backed up. The canary moved to shadow with zero burned/unknown
  reservations and zero pending evaluations before the compatible runtime and
  protected policy were installed.
- A non-viewing exact Cabin `front_door` probe returned a private 720x720 JPEG
  and was deleted. Dormant Cabin `driveway` and Crosstown `front_door` probes
  returned sanitized Ring snapshot failures; this remains an organic-event
  validation item because an actual motion event may wake a battery camera.
  No probe image remains on disk.
- All seven shadow jobs completed cleanly before returning to
  `limited_delivery`. The active projection is healthy with the exact nested
  bindings, zero pending/leased work, zero dead letters, no unreviewed delivery
  outcome, and an empty camera-image directory.

#### Ingress, receipt, and camera diagnostics repair — `2026-08-11`

- A live audit found the queues drained and the core bus healthy, but Ring FCM
  publication had stopped on August 7 while the listener continued emitting
  hourly heartbeats and current Ring history contained newer events. The SDK's
  `started` flag therefore was not a sufficient liveness signal.
- Ring ingress now reconciles at most 20 recent history records per bound
  device every five minutes. It accepts only records no more than 15 minutes
  old, forces every recovered record into the inert backfill path, and restarts
  FCM when a previously unseen record proves a push gap. Backfill cannot send a
  direct ding or feed dog-walk automation, and the existing event identity
  remains the dedupe boundary.
- OpenClaw 2026.7.1 routes iMessage through its native-direct outbound path.
  The delivery and Ring-notification validators now accept that receipt only
  with the exact expected target, matching message identity, and
  `deliveryStatus=sent`; the older gateway receipt remains supported. Ambiguous
  outcomes are still never retried.
- An exact Crosstown probe confirmed Nest capture succeeds while the idle
  battery Ring snapshot endpoint returns `snapshot_failed`. Future evaluations
  now preserve `ring_*` or `nest_*` failure codes instead of collapsing a
  single-provider failure into `camera_target_partial`; uncertainty and image
  deletion rules are unchanged.
- The dog-walk skill validator, 95 focused event-bus/Ring/deployment tests, and
  the complete 1,043-test OpenClaw suite passed before deployment. The Ring
  listener and both interval workers were restarted from byte-identical
  deployed copies; the replacement listener started with all three bound
  devices.

#### Cabin arrival cancellation and Ring ingress health — `2026-08-15`

- Julia's organic Cabin arrival first produced a Kitchen Nest person event,
  followed about 25 seconds later by a canonical `confirmed_vacant` to
  `occupied` transition and exact Crosstown-to-Cabin relocation. The activity
  incident was suppressed and resolved as `resident_arrival_silent` with no
  reservation or delivery.
- The initial vacant-state event had already scheduled camera evidence. The
  worker continued its +30/+60 attempts after occupancy changed, produced no
  retained image or message, and finished uncertain when the Ring snapshot
  failed. Camera claims now re-read fresh canonical presence before each slot
  and atomically complete pending work as `presence_not_vacant` without a
  capture; a safe cancellation has its own aggregate counter and does not
  degrade camera health.
- Ring provider history later recovered two driveway and two front-door person
  records as inert backfill and restarted FCM. Live ingress had therefore been
  stale even though the SDK's outer `started` flag remained true. The watchdog
  now validates the underlying receiver tasks, restores the library's bounded
  sequential-error abort, and keeps ingress health degraded after a recovered
  gap until a genuine live callback proves recovery.
- That restart exposed `firebase-messaging` passing a Web Push parameter tail
  plus an unpadded key/salt value into its strict decoder before Ring could
  deliver callbacks. The runtime now selects and pads only the first bounded
  public header parameters before invoking the unchanged upstream decryptor,
  with regression coverage for valid and malformed input.
- The decrypted startup stream exposed a separate contract mismatch: history
  recovery correctly marked a seconds-old record as inert backfill, while the
  bus required backfill to be more than 60 seconds old. The bus now accepts
  explicit history provenance at any age within five-second clock tolerance
  and the existing 15-minute bound; unmarked events older than 60 seconds
  remain rejected.
- Ring's dedicated publisher now retries one failed idempotent spool commit
  before counting terminal failure. The callback remains nonblocking and
  backfill remains unable to trigger direct dings, dog-walk automation, camera
  evidence, incidents, or delivery.

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
- [x] Classified the July 30 Cabin arrival as a grace-period false positive:
  the shadow decision preceded canonical arrival by roughly seven minutes.
  Extended the shadow arrival grace from 90 seconds to 10 minutes while the
  existing direct Ring path remains immediate.
- [x] Closed the remaining Cabin doorbell-ding evidence item by explicit
  operator waiver on August 5. Crosstown ding, direct-message transport, and
  restart/reconnect dedupe passed the same day.

### `2026-08-05`

- [x] Captured two attended Crosstown doorbell presses as exactly one
  `entry.doorbell_rang` event each, with source-time precision, the exact
  `front_door` binding, zero duplicate IDs, and no Ring publisher failure,
  drop, backlog, or dead letter.
- [x] Restarted and reconnected the Ring listener twice while preserving the
  cumulative publisher counters. Neither restart replayed either press or
  changed the healthy bus projection.
- [x] Repaired the legacy direct Ring notification path. The old standalone
  `imsg send` command and a competing direct RPC worker both timed out beside
  OpenClaw's persistent native channel. The listener now makes one bounded
  send through that already-supervised channel, requires a matching receipt,
  and never retries an ambiguous outcome. A labeled deployed transport canary
  produced a local outgoing Messages row and Dylan confirmed receipt.
- [x] Split the former combined dog-walk listener into the sole
  `ai.openclaw.ring-event-listener` FCM ingress and independent
  `ai.openclaw.dog-walk-automation` policy service. Their protected local
  contract contains only a fresh `person_motion` signal and safe site alias.
  The attended cutover retained Ring publication at 169/169 with zero failures
  or drops, started each service exactly once, and left the event bus healthy.
- [x] Waived the equivalent attended Cabin doorbell press because it would
  block progress for several days. Existing focused tests continue to prove
  that Ring publication failure or disablement cannot change legacy ding or
  dog-walk handling.
- [x] Closed Stage 2 with the operator's explicit Cabin-press waiver and the
  live Ring/August evidence recorded above.
- [x] Built Stage 3 schema v3, protected inactive Dylan policy, durable
  reservation/sender state machine, fixed templates, safe health projection,
  rollback controls, tests, and attended shadow deployment.
- [x] Began Stage 4 at `2026-08-05T15:42:14Z` through the separate explicit
  activation: installed the same protected policy with `active=true`, then set
  runtime mode to `limited_delivery`.

## Decisions required before limited delivery

1. Julia opt-in remains deferred; Stage 4 is Dylan-only.
2. Stage 4 scope is both sites and only person, access, or combined
   person-and-access incidents after confirmed vacancy and the arrival grace.
3. The protected policy records a 30-minute unresolved-access threshold;
   escalation remains inactive until separately implemented and approved.
4. Existing direct Ring ding delivery coexists during Stage 4 and is audited
   for overlap; it is not suppressed by this rollout.
5. Camera participation remains disabled and requires a later, separately
   approved rollout.

No remaining decision blocks the Dylan-only canary activation review.
