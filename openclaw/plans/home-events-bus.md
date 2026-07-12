# Durable Home Events — Bus, Ring, Presence, August, and Agent Skill

## Status: IMPLEMENTED ON BRANCH — shadow-only v1; not deployed

## Overview

Build a private SQLite-backed event journal on the Mac Mini, with Ring and
canonical presence feeding it first in shadow mode. August then becomes the
first genuinely new adapter. Nest, dog-walk, vacancy actions, camera policy,
and August's guarded mutation workflow remain independent until deliberately
migrated.

```text
Existing Ring FCM callback ───────────────┐
Canonical presence evaluation (Mini) ────┼─→ protected atomic spool
August read-only status via MBP ──────────┘              │
                                                         ▼
                                               SQLite event journal
                                                         │
                                        durable consumer queues/leases
                                                         │
                                               correlation + policy
                                                         │
                                         notification/camera outbox

Nest SDM → existing listener/reviewer remains unchanged
```

This design does not introduce MQTT, NATS, another cloud Pub/Sub topic, or a
network listener. Household event volume is low, and SQLite already provides
the durability pattern proven by the existing Nest event store.

## Goals

1. Durably normalize Ring and canonical presence events without changing their
   current behavior.
2. Correlate multiple sources into site-scoped activity incidents instead of
   sending one message per raw event.
3. Add August as the first new, read-only adapter after Ring and presence prove
   the bus contract.
4. Preserve presence as the hard policy gate for active versus shadow camera
   monitoring.
5. Make every producer, consumer, and outbound policy independently reversible.
6. Give OpenClaw a narrowly read-only skill for answering questions about
   recent activity, incidents, decisions, and subsystem health.

## Non-goals

- Republishing into Google's SDM topic.
- Adding an internet-facing or LAN-facing broker.
- Migrating the existing Nest subscriber or Cabin camera reviewer.
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
- Nest stays isolated for this build; a read-only bridge can be added later.

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
│   └── august/
└── state/                         0700
    ├── events.sqlite3            0600
    ├── events.sqlite3-wal/-shm   0600 while SQLite is open
    ├── ingest.lock               0600
    ├── ring-producer.json        0600 safe worker health/counters
    └── status.json               0600
```

## Work package 0: freeze the contracts

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
6. Record that v1 has no delivery path and all correlation remains shadow-only.

Exit gate: schema, privacy rules, aliases, and failure semantics can be reviewed
without running anything.

## Work package 1: durable bus core

### Suggested tracked components

- `openclaw/bin/home_event_bus.py`
- `openclaw/bin/home-eventctl`
- `openclaw/bin/home-events`
- `openclaw/bin/home-event-correlator.py`
- `openclaw/bin/august-event-adapter.py`
- `openclaw/bin/home-event-service-wrapper.sh`
- attended-install LaunchAgents for ingestion, correlation, and August polling
- `openclaw/tests/test_home_event_bus.py`
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

A crash after database commit but before spool deletion replays the file and
hits the unique dedupe key.

SQLite settings should follow the Nest precedent: `foreign_keys=ON`,
`busy_timeout=15000`, `synchronous=FULL`, plus WAL for low-contention
producer/consumer concurrency. Use `AUTOINCREMENT` so pruning cannot reuse
event IDs. WAL permits concurrent readers; it does not create concurrent
writers. One ingester must serially drain every producer spool and own all
normal ingestion writes under `BEGIN IMMEDIATE`.

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

Exit gate: concurrency, crash-boundary, restart-dedupe, permissions, schema
corruption, pruning, and privacy-sentinel tests pass before any live producer
is enabled.

## Work package 2: Ring durable tee

Modify the existing Ring callback path; do not add another Ring service or FCM
registration.

Implementation:

1. When `HOME_EVENTS_RING_ENABLED=1`, map the exact known Ring device binding
   to a fixed site and alias. The default `0` does no bus publication.
2. Keep the FCM callback free of disk I/O. It submits a bounded normalized
   record to a dedicated publisher worker; the worker performs HMAC,
   validation, spool creation, fsync, and ready rename.
3. Keep the current five-minute in-memory dedupe temporarily for legacy
   behavior.
4. Preserve existing direct ding and dog-walk behavior unchanged.
5. If bus publication fails completely, record a safe health error and continue
   the legacy path.
6. Replace current raw-ID/display-name log entries with alias, canonical event
   type, opaque key, and result.
7. If backfill is implemented, limit it to approximately 15 minutes, mark it as
   backfill, and forbid it from generating doorbell messages or satisfying
   stale dog-walk motion windows.

The worker queue is bounded to 256 records. Queue overflow or worker failure
increments a safe health counter but never blocks or changes the legacy Ring
path. Ring bus durability begins only when the worker commits the spool file;
the small callback-to-worker crash window is an explicit tradeoff to preserve
FCM and dog-walk latency.

Dedupe identity:

```text
HMAC(source + bound device alias + provider event ID + kind + classification)
```

### Ring shadow gate

- At least 48 hours.
- One attended ding and person-motion test at each configured site.
- Reconnect and restart duplicate tests.
- Unknown device quarantine test.
- Existing dog-walk outputs are identical with the bus enabled, disabled, or
  unavailable.
- No additional messages are sent.

## Work package 3: canonical presence normalization

Only the Mini publishes canonical presence. The Crosstown MacBook continues
supplying observations through the existing validated Taildrop receiver.

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

### Presence shadow gate

- 48–72 hours plus one controlled relocation.
- One real transition produces the expected occupancy and person events
  exactly once.
- Repeated evaluations produce no events.
- Concurrent Cabin and Taildrop evaluation still serializes correctly.
- Crash injection at every source-outbox boundary loses nothing and duplicates
  nothing.
- All existing presence, Taildrop, and vacancy-action tests remain green.

## Work package 4: combined Ring and presence correlation

Run the correlator in shadow for at least seven days before adding August.

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
- Shadow mode records counters only—no proposed message text and no extra image
  capture.
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

## Work package 5: August as the first new adapter

Given the current local integration and lack of a documented personal consumer
webhook in August's public developer materials, the first adapter polls
read-only status through the existing MBP boundary.

### On the MBP

1. Add a separate sanitized `observe` or `status-summary` command beside
   `august-cmd.js`.
2. Bind one exact configured lock rather than using the first lock on the
   account.
3. Return only:
   - safe alias
   - `locked|unlocked|unknown`
   - `open|closed|unknown`
   - optional validated battery percentage
   - observation timestamp
4. Never return lock/account IDs or the raw API response.
5. Keep existing lock/unlock approval code unchanged.

### On the Mini

1. Poll through the existing protected August wrapper.
2. Use a separate read-only adapter whose code path cannot invoke mutation
   commands.
3. Treat the first good observation as baseline-only.
4. Store each successful safe observation; compare and checkpoint state
   transactionally during ingestion.
5. Represent simultaneous unlock and door-open as two events in one
   observation. Do not invent an ordering.
6. Because polling cannot know the physical event time, record:
   - `time_precision=observed_interval`
   - `not_before=previous_good_poll`
   - `not_after=current_poll`

### Initial cadence

- Start shadow testing at five minutes with jitter.
- Keep 60-second vacant polling and 15-second post-event bursts behind separate
  disabled feature flags until observed rate-limit behavior supports them.
- On failure, back off through 1, 2, 5, 10, and 15 minutes.
- Emit one `source.unavailable` only after three consecutive failures or ten
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

## Work package 6: limited policy activation

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

Deploy a read-only `home-events` CLI and a skill limited to
`Bash(home-events:*)`. The CLI exposes structured JSON only:

```text
home-events status --json
home-events recent [--site cabin|crosstown] [--since 24h] [--limit 20] [--type TYPE] --json
home-events incidents [--site SITE] [--state open|resolved|all] [--since 24h] --json
home-events explain inc_<opaque-id> --json
```

The skill answers what happened, what remains open, why OpenClaw alerted or
stayed silent, and whether the subsystem is healthy. It cannot invoke the
operator CLI, alter presence, mutate locks, replay or acknowledge events,
change policy, or capture media automatically.

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
- Transactional migration and retention pruning.
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
- One presence relocation.
- August manual lock, unlock, door open, and door close.
- One combined Ring, presence, and August sequence.
- Service restart while events are queued.
- No automated unlock in any scenario.

### Agent skill and operational budgets

- Validate skill metadata and tool restrictions, plus forward tests for “what
  happened today,” “why didn't you alert,” “show me a current image,” and an
  attempted lock or policy command.
- Confirm that only an explicit current-image request invokes `nest-camera`.
- Healthy Ring and presence events become queryable within five seconds.
- Ring incident decisions complete within 120 seconds, including the arrival
  grace period. Initial August detection may take up to seven minutes.
- At ten times expected volume, the retained 30-day database plus WAL remains
  below 100 MiB after pruning and checkpointing.

## Deployment

New services follow the attended Nest deployment pattern:

1. Verify the Mac Mini host, user, and home directory.
2. Create protected runtime paths.
3. Install scripts and LaunchAgents as regular files.
4. Run unit and integration tests, Python compilation, `check-config`, and
   `plutil -lint`.
5. Initialize producer and consumer baselines.
6. Bootstrap in shadow mode.
7. Verify status, permissions, process state, and zero delivery attempts.

Daily dotfiles pulls may refresh an already installed service but must never
silently create state, initialize a baseline, bootstrap a new job, or enable
delivery. Restart only affected LaunchAgents; this subsystem should not require
an OpenClaw Gateway restart.

## Rollback

1. Disable delivery or boot out the correlator first while preserving
   ingestion.
2. Stop the August adapter independently; the existing August CLI remains
   available.
3. Disable Ring and presence producer hooks independently.
4. Restore prior tracked scripts and restart only affected jobs.
5. Preserve the database, checkpoints, spool, and source outboxes for diagnosis.
6. Never delete state or credentials as part of rollback.
7. Make migrations transactional and require an older binary to understand or
   explicitly refuse the newer schema.

## Decisions required before activation

These do not block the core build or shadow rollout:

1. Bind the exact protected August lock to the fixed v1 alias `front_door`.
2. Select authorized notification recipients.
3. Select the door-open/unlocked escalation threshold.
4. Decide separately when, if ever, Crosstown camera delivery may leave shadow
   mode.

## Definition of done

- Ring and presence survive restarts without duplicate normalized events.
- Existing Ring, dog-walk, presence, vacancy, Nest, camera, and August mutation
  behavior remains unchanged during the shadow build.
- August is demonstrably read-only and reports timing honestly as an observed
  interval.
- All privacy and permissions tests pass.
- One real multi-source sequence becomes one site-scoped incident.
- OpenClaw can query recent events, open incidents, explanations, and safe
  health through the read-only skill without access to operator mutations.
- V1 performs no user-facing delivery; its durable shadow decisions enforce
  the one-per-site-per-hour policy that a separately authorized future
  delivery phase must preserve.
- Every producer, consumer, and delivery policy can be disabled independently
  without touching canonical presence or existing household automation.
