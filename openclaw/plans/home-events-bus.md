# Durable Home Events — Bus, Presence, Nest, Ring, August, and Agent Skill

## Status: DEPLOYED IN SHADOW MODE

Canonical presence normalization, the metadata-only Nest bridge, the Ring tee,
the read-only August observer, and Cabin plus Crosstown local
arrival/departure enrichment are enabled in the installed shadow runtime.
Cabin presence uses the approved schema-v2 exact controller and Kitchen-mesh
bindings; Crosstown uses its separately approved strict scanner and exact
site-local bindings. Ring and August were promoted concurrently on July 23
after explicit operator approval; their tracked defaults remain disabled.
Both sources still need their remaining attended physical evidence, and August
has not completed its seven-day soak. There is no user-facing delivery or
physical-mutation path in this bus.

## Overview

Operate a private SQLite-backed event journal on the Mac Mini. Canonical
presence, Cabin's sanitized local scan observations, and already-committed Nest
person/motion metadata feed it today in shadow mode. Ring and the read-only
August observer now soak concurrently with independent health and rollback
boundaries. The Nest listener/reviewer, Ring dog-walk and direct ding behavior,
vacancy actions, camera policy, and August's guarded mutation workflow remain
independent.

```text
Ring FCM tee (enabled) -----------------\
Canonical presence normalization (enabled) ----+--> protected atomic spools
August read-only observer (enabled) ------------/              |
Nest listener outbox -> metadata bridge (enabled) -------------/
Sanitized Cabin scans -> local presence adapter (enabled) -----/
                                                               v
                                                     SQLite event journal
                                                               |
                                              durable queues and leases
                                                               |
                                              shadow incidents/decisions
                                                               |
                                               read-only agent queries

Nest SDM -> listener/reviewer acknowledgement and camera path stay independent
```

This design does not introduce MQTT, NATS, another cloud Pub/Sub topic, or a
network listener. Household event volume is low, and SQLite already provides
the durability pattern proven by the existing Nest event store.

## Goals

1. Durably normalize canonical presence, local arrival/departure observations,
   Nest metadata, Ring, and August without changing their existing paths.
2. Correlate multiple sources into site-scoped activity incidents instead of
   sending one message per raw event.
3. Operate August as the first new, read-only provider adapter with an
   independently reversible gate beside the deployed Ring tee.
4. Preserve presence as the hard policy gate for active versus shadow camera
   monitoring.
5. Make every producer, consumer, and outbound policy independently reversible.
6. Give OpenClaw a narrowly read-only skill for answering questions about
   recent activity, incidents, decisions, and subsystem health.

## Non-goals

- Republishing into Google's SDM topic.
- Adding an internet-facing or LAN-facing broker.
- Migrating or placing the bus in front of the existing Nest subscriber or
  Cabin camera reviewer; the bridge is downstream and metadata-only.
- Replacing canonical presence state or vacancy actions.
- Replacing Ring dog-walk behavior or its direct ding notification in the
  initial build.
- Auto-unlock or any other event-authorized physical mutation.
- Enabling Crosstown camera delivery as a side effect of adding August.
- Retaining historical images or raw provider payloads.
- Adding Ring Alarm, HomeKit, Hue, or new hardware in this build.
- Granting the agent skill access to raw provider history, adapter controls,
  policy mutation, event replay, acknowledgements, or lock operations.

## Safety and policy invariants

- The Mac Mini is the normalized bus authority.
- Presence remains canonical in its existing state files. Bus events never
  rewrite presence.
- The existing Ring listener remains the only Ring FCM connection.
- August is observation-only. The adapter cannot lock or unlock.
- Lock and Ring activity are evidence, never occupancy authority.
- Stale, missing, ambiguous, or `possibly_vacant` presence fails to shadow
  mode.
- No raw provider payloads, IDs, image paths, coordinates, credentials,
  recipients, or message bodies enter the database.
- Nest bridging is downstream-only: bus failure cannot delay Pub/Sub
  acknowledgement or the existing reviewer, and no media or model output
  enters the journal.

## Normalized event contract

Persist a small, versioned, vendor-neutral envelope:

```json
{
  "schema_version": 1,
  "event_uid": "opaque-local-id",
  "source": "ring",
  "event_type": "entry.person_detected",
  "site": "crosstown",
  "entity_kind": "doorbell",
  "entity_alias": "front_door",
  "occurred_at": "2026-07-12T20:00:00Z",
  "observed_at": "2026-07-12T20:00:01Z",
  "time_precision": "source",
  "dedupe_key": "opaque-keyed-digest",
  "attributes": {
    "classification": "person"
  }
}
```

Raw provider event IDs may enter the publisher through stdin or process memory,
but the publisher must generate a keyed digest and discard the original before
spooling. Unknown devices are quarantined rather than assigned a guessed site.

### Initial event taxonomy

Ring:

- `entry.doorbell_rang`
- `entry.person_detected`
- `entry.motion_detected` — stored and shadowed, not initially actionable

Presence:

- `presence.occupancy_changed`
- `presence.person_relocated`
- `presence.local_departure_inferred` — named site-local network inference;
  journal-only
- `presence.local_arrival_observed` — named exact-device reappearance;
  journal-only
- `presence.household_excursion_started` — every assigned resident locally
  away; journal-only
- `presence.household_excursion_ended` — first same-site return or canonical
  relocation outcome; journal-only

Nest:

- `camera.person_detected` — site-scoped incident evidence
- `camera.motion_detected` — queryable metadata only; non-actionable

August:

- `lock.locked`
- `lock.unlocked`
- `door.opened`
- `door.closed`
- `device.battery_low`
- `device.battery_recovered`
- `source.unavailable`
- `source.recovered`

Derived later:

- `incident.opened`
- `incident.updated`
- `incident.resolved`

## Runtime layout

```text
~/.openclaw/home-events/           0700
├── config/                        0700
│   └── dedupe.key                0600
├── spool/                         0700
│   ├── ring/
│   ├── presence/
│   ├── august/
│   └── nest/
└── state/                         0700
    ├── events.sqlite3            0600
    ├── events.sqlite3-wal/-shm   0600 while SQLite is open
    ├── ingest.lock               0600
    ├── ring-producer.json        0600 safe worker health/counters
    ├── status.json               0600
    ├── august-adapter.json       0600 after first observation
    ├── august-adapter.pending.json 0600 only during retry/recovery
    ├── august-adapter.lock       0600
    ├── nest-bridge.json          0600 outbox cursor + DB identity
    ├── nest-bridge.lock          0600
    ├── presence-local-adapter.json 0600 safe site/person debounce state
    ├── presence-local-adapter.pending.json 0600 only during retry/recovery
    └── presence-local-adapter.lock 0600
```

## Work package 0: freeze the contracts — complete

Before runtime coding:

1. Document schema, durability, privacy, recovery, retention, aliases, and
   rollout in this plan and the eventual subsystem runbook.
2. Define strict source-specific JSON schemas and sanitized fixtures.
3. Reuse Ring's existing exact device-to-site mapping, and keep the new exact
   August observe lock and alias binding only in its protected MBP config.
4. Fix initial retention:
   - Accepted and acknowledged event metadata: 30 days.
   - Dead-letter metadata: 90 days.
   - Pending or leased work: never pruned.
5. Define independent, disabled-by-default producer flags:
   - `HOME_EVENTS_RING_ENABLED`
   - `HOME_EVENTS_PRESENCE_ENABLED`
   - `HOME_EVENTS_AUGUST_ENABLED`
   - `HOME_EVENTS_NEST_ENABLED`
   - `HOME_EVENTS_LOCAL_PRESENCE_CABIN_ENABLED`
   - `HOME_EVENTS_LOCAL_PRESENCE_CROSSTOWN_ENABLED`
6. Distinguish SQLite schema version 2 from normalized event-envelope schema
   version 1.
7. Record that the current phase has no delivery path and all correlation
   remains shadow-only.

Exit gate: schema, privacy rules, aliases, and failure semantics can be reviewed
without running anything.

## Work package 1: durable bus core — deployed

### Tracked components

- `openclaw/bin/home_event_bus.py`
- `openclaw/bin/home-eventctl`
- `openclaw/bin/home-events`
- `openclaw/bin/home-event-correlator.py`
- `openclaw/bin/august-event-adapter.py`
- `openclaw/bin/nest-home-event-bridge.py`
- `openclaw/bin/presence-local-event-adapter.py`
- `openclaw/bin/home-event-service-wrapper.sh`
- attended-install LaunchAgents for ingestion, correlation, August polling,
  Nest bridging, and local-presence enrichment
- `openclaw/tests/test_home_event_bus.py`
- `openclaw/tests/test_home_event_correlator.py`
- `openclaw/tests/test_august_event_adapter.py`
- `openclaw/tests/test_nest_home_event_bridge.py`
- `openclaw/tests/test_presence_local_event_adapter.py`
- `openclaw/tests/test_home_event_deployment.py`
- `openclaw/HOME-EVENTS.md`

### Publisher semantics

1. Read bounded JSON from stdin.
2. Validate exact fields, enum values, timestamp skew, size, alias binding, and
   allowed attributes.
3. Convert any source ID to an opaque keyed digest.
4. Atomically create a `0600` spool file, fsync it, rename it into ready state,
   and fsync the parent directory.
5. Return success only after that durable boundary.

### Ingest semantics

1. Revalidate the ready file.
2. Start a `BEGIN IMMEDIATE` transaction.
3. Insert the producer receipt or recognize a duplicate.
4. Insert normalized events and update the producer checkpoint.
5. Add pending rows for enabled consumers.
6. Commit.
7. Remove the spool file and fsync the directory.
8. Update safe `status.json` best-effort.
9. Opportunistically prune retained metadata only when a durable SQLite marker
   is at least 24 hours old. The due check, deletes, marker update, and
   `prune_runs` increment share one `BEGIN IMMEDIATE` transaction, so restarts
   and concurrent five-second workers cannot multiply maintenance writes.

A crash after database commit but before spool deletion replays the file and
hits the unique dedupe key.

SQLite settings should follow the Nest precedent: `foreign_keys=ON`,
`busy_timeout=15000`, `synchronous=FULL`, plus WAL for low-contention
producer/consumer concurrency. Use `AUTOINCREMENT` so pruning cannot reuse
event IDs. WAL permits concurrent readers; it does not create concurrent
writers. One ingester must serially drain every producer spool and own all
normal ingestion writes under `BEGIN IMMEDIATE`.

An automatic prune does not checkpoint the WAL. The explicit operator
`home-eventctl prune` command is always forced, resets the daily maintenance
gate, writes status, and requests a truncating checkpoint. Missing, invalid,
or future maintenance markers fail toward one immediate prune; the internal
marker is excluded from public status counters.

### Core tables

- `schema_migrations`
- `producer_inbox`
- `events`
- `producer_state`
- `consumers`
- `consumer_deliveries`
- `incidents`, `incident_events`, and durable `incident_decisions`
- `notification_outbox`
- `service_counters`
- `runtime_status`

The implemented status projection also reports explicit bus-observed source
health, safe failure counts/codes, per-consumer unfinished depth and oldest
timestamp, retention settings, and database size. An unobserved source remains
`unknown`; process liveness alone is never promoted to healthy.

Consumers claim work with a lease token and expiry, work outside the
transaction, and acknowledge using the same token. External sends use
reserve-before-send; if the send result becomes unknown after a crash, burn the
delivery slot rather than risk duplicate household messages.

Exit gate met: concurrency, crash-boundary, restart-dedupe, permissions,
schema-corruption, pruning, and privacy-sentinel tests passed before the live
presence and Nest producers were enabled.

## Work package 2: Ring durable tee — enabled, shadow evidence active

The tee and its tests are installed and the attended production flag is `1`.
The listener has exact bindings for both front doors and the Cabin driveway
camera. The bus has observed post-promotion Ring activity at both sites; the
existing FCM, direct-ding, and dog-walk paths remain authoritative.

The implementation modifies the existing Ring callback path and does not add
another Ring service or FCM registration.

Implementation:

1. When `HOME_EVENTS_RING_ENABLED=1`, map each exact known Ring device binding
   to a fixed site and alias. The default `0` does no bus publication.
2. Keep the FCM callback free of disk I/O. It submits a bounded normalized
   record to a dedicated publisher worker; the worker performs HMAC,
   validation, spool creation, fsync, and ready rename.
3. Keep the current five-minute in-memory dedupe temporarily for legacy
   behavior.
4. Preserve existing direct ding and dog-walk behavior unchanged.
5. If bus publication fails completely, record a safe health error and continue
   the legacy path.
6. Logs use the alias, canonical event type, opaque key, and result rather than
   raw IDs or display names.
7. Backfill is limited to approximately 15 minutes, marked as backfill, and
   forbidden from generating doorbell messages or satisfying stale dog-walk
   motion windows.

The worker queue is bounded to 256 records. Queue overflow or worker failure
increments a safe health counter but never blocks or changes the legacy Ring
path. Ring bus durability begins only when the worker commits the spool file;
the small callback-to-worker crash window is an explicit tradeoff to preserve
FCM and dog-walk latency.

Dedupe identity:

```text
HMAC(source + bound device alias + provider event ID + kind + classification)
```

### Remaining Ring shadow gate

- At least 48 hours.
- One attended ding and person-motion test at each configured site.
- Reconnect and restart duplicate tests.
- Unknown device quarantine test.
- Existing dog-walk outputs are identical with the bus enabled, disabled, or
  unavailable.
- No additional messages are sent.

## Work package 3: canonical presence normalization — deployed

Canonical normalization is enabled and has produced live transition events.
The deployed Cabin scan uses the approved schema-v2 exact controller and
Kitchen-mesh bindings for both residents. The attended enrollment completed
with two clean scheduled ticks, 12/12 live observations, the full test suite,
and explicit operator acceptance of the exact installed scanner hash.

Only the Mini publishes canonical presence. The Crosstown MacBook continues
supplying observations through the existing validated Taildrop receiver. Its
strict exact-MAC scanner passed the separate
[exact-source canary and approval gate](crosstown-strict-presence-canary.md)
and is active with an exact legacy rollback bundle retained.

Preserve all current semantics:

- `occupied`
- `confirmed_vacant`
- `possibly_vacant`
- sticky resident location
- fresh-positive vacancy veto
- no absent event merely because a phone disappeared
- serialized evaluation under `lockf`

Use a protected source outbox rather than treating `events.json` or WatchPaths
as the durability boundary. Recovery must complete before the evaluator is
allowed to calculate another state:

1. Compute the result and transitions while holding the existing evaluation
   lock.
2. Write and fsync a pending observation batch with a deterministic evidence
   ID plus the prior and target canonical-state hashes.
3. Atomically replace `state.json`, which is the canonical commit marker.
4. Finish the compatibility projections (`prev-evaluated.json`, rolling events,
   and JSONL history), then atomically mark the batch ready.
5. Import it idempotently into the bus and remove it only after bus commit.
6. On recovery, compare canonical state to the pending batch. If it matches the
   target hash, finish projections and promote the batch. If it matches the
   prior hash, discard the uncommitted batch and reevaluate. Any third hash is
   left untouched and fails health closed for operator review.

The observation ID should derive from Cabin and Crosstown scan timestamps and
normalized state, not the evaluation wall clock. First deployment establishes
a baseline and emits nothing.

Existing JSON projections and vacancy actions remain in place. The bus is
observational and cannot trigger a fresh scan or roll back canonical presence
if publication fails.

### Remaining presence evidence

- Verify the first true post-enrollment transition normalizes exactly once and
  repeated identical evaluations remain silent.
- Correlate a later organic or attended Nest person event against the corrected
  canonical state without changing listener or reviewer behavior.
- Continue natural strict Crosstown transition evidence after its completed
  activation.

### Local arrival and departure enrichment

The deployed Mini-only adapter reads the already-sanitized per-site scan files.
It does not change `presence-detect.sh`, its approved Cabin hash, canonical
occupancy, sticky resident locations, compatibility transitions, or vacancy
actions.

The adapter baselines each enabled site silently and processes only strictly
advancing, successful, fresh scan timestamps. A recent exact positive followed
by three distinct negatives spanning at least 30 minutes infers one named
departure; the first later exact positive records one named arrival. Unknown,
stale, malformed, failed, replayed, or duplicate observations do not advance
state, and a gap longer than 25 minutes breaks a negative sequence. Split
households consider only residents canonically assigned to that site.

When all assigned residents are independently locally away, create one
site-scoped household excursion. End it on the first same-site return or with
the separate `residence_relocated` outcome if canonical residence changes.
Store bounded observation intervals and safe aliases only—never IP, MAC, SSID,
controller/mesh identifiers, raw client metadata, or provider rows.

All four local event types are queryable context only. The correlator must
acknowledge them without changing incidents, decisions, delivery, camera,
lock, or canonical presence state. Cabin and Crosstown retain independent
disabled-by-default tracked flags. Both installed site flags are now enabled
after their separate scanner approvals; the July 26 Crosstown baseline and
duplicate pass each produced zero events and no canonical or incident side
effects.

## Work package 4: shadow correlation — deployed, multi-source soak pending

The durable correlator is deployed and consumes presence and Nest evidence in
shadow mode. Ring and August are now enabled for concurrent participation; live
multi-source parity evidence remains pending. No delivery or camera-capture
path exists in the correlator.

The original sequential Ring-before-August gate was intentionally accelerated
by the operator on July 23. Their shadow soaks remain independently observable
and reversible while the Nest bridge stays active and shadow-only.

Policy:

- Incidents are strictly site-scoped.
- Presence changes choose active versus shadow mode, but consumers re-read
  canonical presence before any decision.
- `confirmed_vacant` may open an activity episode.
- `occupied` suppresses routine commentary.
- `possibly_vacant`, stale, malformed, or unknown presence permits no camera
  work or physical action.
- A resident arrival can explain recent activity and close the episode
  silently.
- Ring bursts collapse into one incident.
- No model participates in event validation or hard security decisions.
- Shadow mode records durable incidents, decisions, and safe counters only—no
  proposed message text, outbound delivery, or extra image capture.
- The first suppressed, rate-limited, or shadowed decision is terminal for its
  incident; later presence changes or rate-limit expiry cannot create a second
  classification or outbox row.
- Routine incidents resolve after 15 quiet minutes. Door or lock incidents
  persist until resolved, or become `expired_unresolved` after 24 hours and
  degrade health without inventing a resolution.
- Incidents, consumer cursors, delivery cooldowns, and correlation windows are
  durable across process restarts.
- If canonical presence becomes stale or uncertain during an open incident,
  stop active evaluation and retain the incident in shadow until a fresh state
  permits a deterministic decision.

Exit gate: no unexplained parity gaps, unbounded backlog, cross-site
correlation, duplicate incidents, or outbound delivery attempts.

## Work package 5: Nest downstream metadata bridge — deployed

`nest-home-event-bridge.py` reads only committed rows from the existing Nest
listener's SQLite outbox. It runs after Pub/Sub acknowledgement and outside the
Cabin reviewer, so a bridge or home-event failure cannot delay either path.

Implementation:

1. `HOME_EVENTS_NEST_ENABLED=0` is the install default; the attended production
   flag is independently preserved across routine pulls.
2. First enable records the current listener outbox watermark without replaying
   historical camera activity.
3. Exact configured resources map only to `kitchen`, `living_room`, and
   `living_room_wired` plus their fixed sites. Unknown resources fail closed.
4. Committed person and motion rows normalize to `camera.person_detected` and
   `camera.motion_detected`. Person evidence may open or extend a shadow
   incident; motion remains queryable metadata and is non-actionable.
5. The cursor advances only after the bus durably accepts the normalized
   event. Listener database replacement, schema mismatch, or rewind fails
   closed instead of silently rebasing an established cursor.
6. Camera resources, raw SDM identifiers, images, model output, messages, and
   provider payloads never enter the bridge state, spool, or journal.

Completed operational evidence:

- The first enabled run baselined the committed outbox without replaying
  historical camera rows.
- Later `living_room_wired` person metadata normalized to the fixed Crosstown
  site while listener acknowledgement and reviewer behavior remained
  independent.

Remaining operational evidence:

- After Cabin enrollment, correlate a later organic or attended person event
  against the corrected canonical presence state.
- Exercise the other configured aliases as organic or attended events arrive,
  and verify cursor recovery during the next safe affected-service restart.
- Keep Nest bridging independently reversible; disabling it must leave the
  listener, reviewer, and explicit on-demand camera skill untouched.

## Work package 6: August as the first new adapter — enabled, shadow evidence pending

The read-only observe command, adapter, protected state contract, and tests are
implemented. The protected exact lock binding was installed and verified
without printing the identifier, `HOME_EVENTS_AUGUST_ENABLED=1` in the
attended runtime, and the first good observation established a silent baseline
with zero events. The next unattended poll also completed cleanly.
Bus-observed source health remains `unknown` until a real transition is
published; process liveness alone does not promote it.

Given the current local integration and lack of a documented personal consumer
webhook in August's public developer materials, the first adapter polls
read-only status through the existing MBP boundary.

### Implemented MBP contract

1. `august observe` is a separate sanitized command beside `august-cmd.js`.
2. It requires one exact configured lock rather than selecting the first lock
   on the account.
3. It returns only:
   - safe alias
   - `locked|unlocked|unknown`
   - `open|closed|unknown`
   - optional validated battery percentage
   - observation timestamp
4. It never returns lock/account IDs or the raw API response.
5. Existing lock/unlock approval code remains unchanged.

### Implemented Mini contract

1. The adapter polls through the existing protected August wrapper.
2. Its separate read-only code path cannot invoke mutation commands.
3. The first good observation is baseline-only.
4. Each successful safe observation is stored, compared, and checkpointed
   transactionally during ingestion.
5. `unknown` is checkpointed silently. A lock or door transition is published
   only when its immediately previous and current values are both known and
   different.
6. Simultaneous unlock and door-open state becomes two events in one
   observation without inventing an ordering.
7. Because polling cannot know the physical event time, it records:
   - `time_precision=observed_interval`
   - `not_before=previous_good_poll`
   - `not_after=current_poll`

### Initial cadence

- Start shadow testing at five minutes with jitter.
- Keep 60-second vacant polling and 15-second post-event bursts behind separate
  disabled feature flags until observed rate-limit behavior supports them.
- On failure, back off through 1, 2, 5, 10, and 15 minutes.
- For relay transport failures, emit one `source.unavailable` only after at
  least three failures spanning 30 minutes. This threshold incorporates the
  observed routine 14- to 23-minute Crosstown transport gaps.
- For remote-command, missing, oversized, malformed, or contract-invalid
  output, retain the stricter threshold of three consecutive failures or ten
  minutes without a good status.
- Emit one recovery event on the first successful poll.
- Battery low at `≤20%`; recovered at `≥25%`.

### August shadow gate

- Seven-day shadow soak.
- Attended lock, unlock, door-open, and door-close cycle.
- Restart and duplicate tests.
- Sleeping MBP, SSH outage, expired auth, malformed response, contradictory
  state, timeout, and rate-limit tests.
- Existing August mutation and approval suite remains unchanged.
- No automated unlock occurs in any test.

## Work package 7: limited policy activation — future, not authorized

Only after all shadow gates pass:

1. An unlock or open while confidently vacant opens one incident.
2. Wait 60–90 seconds for Ring and resident-arrival evidence.
3. A fresh resident arrival resolves the incident silently.
4. If still unexplained, optionally request a current camera image and have
   OpenClaw describe what it sees.
5. Send one combined message, not separate Ring, lock, door, and camera
   messages.
6. Informational delivery remains capped at one send attempt per site per hour.
7. A door remaining open or unlocked may receive one escalation, then the
   hourly cap applies.
8. Lock and close events normally resolve silently.
9. Never auto-unlock.
10. August activation must not automatically enable Crosstown camera delivery;
    that remains a separate rollout gate.

Notification recipients and escalation thresholds belong in protected policy
configuration and require explicit authorization before delivery is enabled.

Future delivery uses reserve-before-send. A reserved attempt consumes the
hourly slot even if a crash makes its outcome unknowable, preventing duplicate
household messages. Three unknown outcomes in 24 hours degrade delivery health
and suppress further nonurgent delivery until operator review. Any dead-letter
row immediately degrades the responsible source or consumer health.

## OpenClaw `home-events` skill

The deployed read-only `home-events` CLI and skill are limited to
`Bash(home-events:*)`. The CLI exposes structured JSON only:

```text
home-events status --json
home-events recent [--site cabin|crosstown] [--since 24h] [--limit 20] [--type TYPE] --json
home-events incidents [--site SITE] [--state open|resolved|all] [--since 24h] --json
home-events explain inc_<opaque-id> --json
```

The skill answers what happened, what remains open, why the shadow correlator
classified, suppressed, or rate-limited bus evidence, and whether the
subsystem is healthy. It cannot explain an actual message, camera capture, or
reviewer outcome; those remain with the owning Ring or Nest workflow. It also
cannot invoke the operator CLI, alter presence, mutate locks, replay or
acknowledge events, change policy, or capture media automatically.

The event journal stores no historical media. On an explicit trusted-owner
request for a current image, the skill delegates to `nest-camera` and inherits
that skill's authorization, exact-camera, same-route delivery, and cleanup
rules. Cabin maps to Kitchen. An ambiguous Crosstown request must distinguish
Living Room from Living Room Wired.

## Security and privacy requirements

- Accept source input through stdin, never provider data in argv.
- Reject unknown or extra fields and cap normalized attributes at 2 KiB.
- Reject stale or implausibly future timestamps.
- Open protected files without following symlinks; verify owner, type, and mode.
- Keep runtime directories `0700` and files `0600`.
- Store no raw source IDs, account identifiers, network identifiers, GPS,
  provider payloads, image URLs, credentials, chat targets, message text, or
  provider error strings.
- Logs contain bounded safe metadata and error codes only.
- LaunchAgents do not invoke `op` or load broad interactive environments.
- Device binding is exact and fails closed for unknown devices.

## Observability

Provide `home-eventctl status` and `check-config`. Safe status may contain:

- schema and operating mode
- last update and start timestamps
- accepted, duplicate, invalid, and suppressed counts
- last successful event time by source and site
- oldest pending age and queue depth
- consumer lag
- adapter health and consecutive failures
- last safe error code
- database size and retention setting

The durable automatic-prune timestamp is internal maintenance state, not a
public operational counter. Safe status exposes the resulting `prune_runs`
count but not the epoch marker.

Status and dashboards must not depend on parsing logs.

The operator CLI is deliberately unavailable to the agent skill. The separate
read-only `home-events` CLI must return bounded results, opaque public event or
incident identifiers, and no database path or provider identity.

## Verification matrix

### Bus durability and security

- Duplicate delivery across restart.
- Concurrent producers and SQLite contention.
- Crash before commit, after commit, before spool deletion, during cursor
  update, and during send.
- Out-of-order, stale, and future events.
- Symlink, wrong-owner, wrong-mode, corrupt-schema, read-only-disk, and full-disk
  behavior.
- Transactional migration and retention pruning, including restart-durable
  24-hour cadence, the exact due boundary, manual forced pruning, invalid/future
  marker recovery, concurrent-worker serialization, and no automatic WAL
  checkpoint.
- Status projection failure after durable commit.
- First-run consumer baselines at the current tail without historical replay.
- Database growth and WAL checkpointing at ten times expected household event
  volume.
- Incident quiet timeout, 24-hour unresolved expiry, presence becoming stale
  mid-incident, dead-letter health degradation, and burned send slots.
- Privacy sentinel proving forbidden values do not appear in the database,
  spool, status, or logs.

### Ring

- Exact device mapping and unknown-device rejection.
- Update events ignored.
- Ding, person motion, and generic motion normalization.
- Burst, reconnect, and restart dedupe.
- Callback-before-loop and FCM reconnect failure.
- Legacy dog-walk and ding behavior unchanged under bus failure.

### Presence

- Existing stale, ambiguity, sticky relocation, concurrent evaluation,
  Taildrop replay, and vacancy suites remain green.
- First baseline emits zero transitions.
- Each true transition emits once.
- Ordinary repeated evaluation emits nothing.
- Source-outbox recovery at every crash boundary.
- The normalizer never invokes a live scan.
- Cabin exact-ID enrollment proves reconnect and idle behavior for both phones,
  five ground-truth scenarios at the real cadence, and zero mismatches. The
  original four-tick downstream-disabled gate recorded two clean scheduled
  ticks; the operator explicitly waived the remaining two before activation.

### Nest

- First enable baselines the committed listener outbox with zero historical
  replay.
- Exact camera aliases and sites normalize person and motion metadata once.
- Motion remains non-actionable; person evidence remains shadow-only.
- Listener database replacement, rewind, and incompatible schema fail closed.
- Bridge failure or disablement cannot delay Pub/Sub acknowledgement, camera
  review, or explicit on-demand image capture.
- Privacy sentinels prove no camera resource, provider ID, media, model output,
  or message text enters bridge or bus state.

### Correlation

- Vacant plus Ring plus unlock/open without arrival produces one incident.
- The same sequence with a fresh arrival resolves silently.
- Occupied activity remains shadow.
- Duplicate and out-of-order bursts create one incident.
- Door close and lock silently resolve.
- Cross-site evidence cannot correlate.
- Stale or unknown presence cannot activate cameras or mutations.
- Send failure and restart cannot duplicate a notification.

### August

- Sanitized status strips raw fields.
- Baseline is silent.
- Valid state transitions emit once.
- Contradictory state fails closed.
- Sleeping MBP, SSH failure, API rate limit, expired auth, delayed response, and
  insecure config tests.
- Backoff and recovery produce one health transition rather than repeated
  noise.

### Attended physical tests

- Ring ding and person motion at each site.
- Cabin exact-ID reconnect, idle, all-scenario, and activation procedure is
  complete; one controlled post-enrollment presence relocation remains.
- One later Nest person event correlated against corrected presence without
  changing reviewer behavior.
- August manual lock, unlock, door open, and door close.
- One combined Ring, presence, and August sequence.
- Service restart while events are queued.
- No automated unlock in any scenario.

### Agent skill and operational budgets

- Validate skill metadata and tool restrictions, plus forward tests for “what
  happened today,” “why didn't you alert,” “show me a current image,” and an
  attempted lock or policy command.
- Confirm that only an explicit current-image request invokes `nest-camera`.
- Healthy Ring, presence, and bridged Nest events become queryable within five
  seconds of durable publication.
- Ring incident decisions complete within 120 seconds, including the arrival
  grace period. Initial August detection may take up to seven minutes.
- At ten times expected volume, the retained 30-day database plus WAL remains
  below 100 MiB after pruning and checkpointing.

## Deployment and remaining rollout

The bus core, correlator, read-only skill, adapters, bridges, and LaunchAgents
are installed on the Mac Mini with SQLite schema 2 in shadow mode. Presence,
Nest, Ring, August, and both local-presence sites are enabled in the attended
runtime. The local adapter completed zero-event baselines and duplicate-scan
no-ops for Cabin and Crosstown. The remaining order is:

1. Verify one true post-enrollment presence transition and correlate a later
   Nest person event against the corrected canonical state.
2. Complete Ring's attended ding and person-motion test at each configured
   site, including restart/dedupe and legacy dog-walk parity.
3. Retain the completed attended August lock/unlock cycle evidence. Attempt
   door open/close evidence only if DoorSense begins returning a known state;
   never infer it from `unknown` or automate unlock.
4. Soak Ring and August concurrently, assessing and rolling back each source
   independently.
5. Verify one Cabin local departure/arrival interval and household excursion
   pair with zero canonical-state or incident side effects.
6. Collect a natural or attended Crosstown local departure/arrival interval
   with zero canonical-state or incident side effects.
7. Follow the separate
   [event bus promotion plan](event-bus-promotion-plan.md) before considering
   limited delivery; no delivery is authorized by this shadow activation.
8. Consider any limited delivery only under separate explicit authorization;
   it is not part of the current rollout.

At each gate, run unit/integration tests, Python compilation, `check-config`,
plist validation when applicable, permissions checks, safe health/backlog
inspection, and verification of zero delivery attempts.

Daily dotfiles pulls may refresh an already installed service but must never
silently create state, initialize a baseline, bootstrap a new job, or enable
delivery. Restart only affected LaunchAgents; this subsystem should not require
an OpenClaw Gateway restart.

## Rollback

1. Disable delivery or boot out the correlator first while preserving
   ingestion.
2. Stop the August adapter independently; the existing August CLI remains
   available.
3. Disable the Nest bridge independently without stopping the listener,
   reviewer, or on-demand camera path.
4. Disable Ring and presence producer hooks independently.
5. Restore prior tracked scripts and restart only affected jobs.
6. Preserve the database, checkpoints, spool, and source outboxes for diagnosis.
7. Never delete state or credentials as part of rollback.
8. Make migrations transactional and require an older binary to understand or
   explicitly refuse the newer schema.

## Decisions required before activation

These do not block the core build or shadow rollout:

1. Select authorized notification recipients.
2. Select the door-open/unlocked escalation threshold.
3. Decide separately when, if ever, Crosstown camera delivery may leave shadow
   mode.

## Definition of done

The core shadow deployment is operational. Its acceptance remains:

- Presence and Nest survive restarts without duplicate normalized events, and
  all privacy, permissions, durability, and maintenance-cadence tests pass.
- Existing Ring, dog-walk, presence, vacancy, Nest camera/reviewer, and August
  mutation behavior remain independent and unchanged.
- OpenClaw can query recent events, incidents, explanations, and safe health
  through the read-only skill without operator mutations or implicit capture.
- The bus performs no user-facing delivery; durable shadow decisions preserve
  the one-per-site-per-hour policy for any separately authorized future phase.
- Every producer and consumer can be disabled independently without touching
  canonical presence or existing household automation.

Remaining rollout is complete when:

- Ring proves restart/dedupe and legacy-path parity, then one real multi-source
  sequence becomes one site-scoped shadow incident.
- August is demonstrated read-only, reports observed-interval timing honestly,
  and completes its attended seven-day shadow gate.
