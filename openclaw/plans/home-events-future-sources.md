# Home Events Future Sources — Vacancy Outcomes, Connectivity, and Pet Exceptions

## Status: PLANNED — DOCUMENTATION ONLY

This plan records possible future implementation work. It does **not** authorize
code deployment, runtime initialization, producer activation, LaunchAgent
changes, device actions, notifications, or an intentional internet outage.

The current Ring, August, canonical-presence, Nest, and local-presence rollout
must finish its remaining canaries and shadow soak before any producer in this
plan is enabled. Implementation and activation are separate future decisions.

## Purpose

Extend the private home-event journal with three kinds of evidence that improve
explainability and unattended-home awareness without creating another control
path:

1. **Vacancy-action outcomes** — distinguish independently confirmed state,
   accepted commands, failures, policy skips, and unknown results.
2. **Site internet loss and recovery** — distinguish a quiet home from a site
   whose external connectivity disappeared.
3. **Pet-equipment exceptions** — record low supplies, maintenance needs,
   faults, and explicit offline/recovery transitions for configured devices.

All three sources remain query-only and shadow-only. Canonical presence stays
the only occupancy authority, and the existing automations remain the only
owners of physical actions.

## Goals

1. Reuse the existing strict, vendor-neutral event envelope and durable SQLite
   ingester.
2. Keep every new source independently disabled by default and independently
   reversible.
3. Preserve exact causal context for vacancy actions without putting the bus
   in their execution path.
4. Detect internet reachability from a process running at the affected site,
   rather than equating an unreachable bridge with an internet outage.
5. Publish pet-device exception transitions, not continuous telemetry.
6. Baseline sampled sources silently and use bounded hysteresis or debounce to
   prevent flapping.
7. Retain only safe aliases, bounded enums, transition intervals, and opaque
   local identifiers.
8. Add no delivery, camera, model, device-control, feed, clean, reset, or
   presence-mutation capability.

## Non-goals

- Changing the vacancy trigger, action order, retry behavior, marker semantics,
  Roomba snooze policy, Eight Sleep reconciliation, or existing iMessage path.
- Treating an automation result, internet state, or pet-device state as
  evidence that a person is home or away.
- Inferring a provider or ISP outage when only a bridge, observer, DNS lookup,
  or management path is unavailable.
- Retaining raw command output, provider payloads, account or device IDs,
  serial numbers, network addresses, SSIDs, probe targets, DNS answers, HTTP
  bodies, or topology.
- Retaining pet visit history, weights, inferred pet identity, feeding
  schedules, consumption history, or health timelines.
- Adding Ring Alarm, HomeKit, Home Assistant, a network broker, or new hardware.
- Enabling notification delivery or changing the current shadow policy.
- Intentionally disconnecting either residence to manufacture production
  evidence.

## Activation preconditions

Before implementation is considered ready for an attended rollout:

1. Complete the remaining natural presence transition, Nest correlation, Ring,
   August, Cabin local-transition, and Crosstown exact-source gates in
   [home-events-bus.md](home-events-bus.md).
2. Confirm the existing bus has no unexplained backlog, dead letters,
   cross-site incidents, duplicate evidence, or delivery attempts.
3. Freeze the event contracts, safe aliases, thresholds, probe policy, and
   source-specific retention behavior in tests before touching production.
4. Obtain a separate explicit decision to implement, then a later explicit
   decision to activate each producer.

## Proposed architecture

```text
Canonical presence -> vacancy source journal -> adapter ---------\
Cabin observer -> protected local journal -> adapter -------------+
Crosstown observer -> protected journal -> Mini SSH bridge -------+-->
Petlibro sanitized observe -> adapter -----------------------------+   protected
Litter-Robot sanitized observe -> adapter ------------------------/    source spools
                                                                       |
                                                               SQLite ingester
                                                                       |
                                                       shadow correlation and queries
```

The bus must never become a prerequisite for a physical action. Each producer
first commits to a protected source-owned journal or state file; a separate
adapter publishes normalized events. A stopped ingester therefore causes a
backlog, not lost action execution or changed household behavior.

## Shared contract changes

### Sources and flags

Add four source names, with tracked defaults of `0`:

| Source | Proposed flag | Rollback boundary |
|--------|---------------|-------------------|
| `vacancy` | `HOME_EVENTS_VACANCY_CABIN_ENABLED` and `HOME_EVENTS_VACANCY_CROSSTOWN_ENABLED` | Each site's telemetry independently |
| `connectivity` | Site-local `HOME_EVENTS_CONNECTIVITY_OBSERVER_ENABLED`, plus Mini bridge flags `HOME_EVENTS_CONNECTIVITY_CABIN_ENABLED` and `HOME_EVENTS_CONNECTIVITY_CROSSTOWN_ENABLED` | Observation and publication independently per site |
| `petlibro` | `HOME_EVENTS_PETLIBRO_CROSSTOWN_ENABLED` and `HOME_EVENTS_PETLIBRO_CABIN_ENABLED` | Each site's exact mappings independently |
| `litter_robot` | `HOME_EVENTS_LITTER_ROBOT_CROSSTOWN_ENABLED` | Litter-Robot observer only |

Create one mode-`0700` spool directory for each source. Runtime state and
outboxes must be owner-only regular files or directories, reject symlinks, use
bounded file sizes, and perform atomic write, file `fsync`, rename, and parent
directory `fsync` before acknowledging a record.

### Event-envelope and database versions

The normalized envelope can remain schema version 1 because its existing
fields are sufficient. SQLite must nevertheless migrate transactionally to
schema version 3 before any new source can enqueue: the current
`producer_inbox`, `events`, and `producer_state` table constraints hard-code
the four existing sources.

The attended migration must quiesce the ingester and correlator, take an
owner-only WAL-consistent backup through SQLite's backup API, run
`PRAGMA integrity_check` against the copy, and retain it until the migration
and post-migration verification complete. Copying only `events.sqlite3` while
writers or a WAL are active is not an acceptable backup.

The schema-3 transaction then rebuilds the source-constrained tables while
preserving row IDs, foreign keys, counters, consumer state, and event ordering,
adds the new producer-state rows, and:

- Add an internal `subject_key` to incidents.
- Add protected producer-component health keyed by
  `(source, site, component_alias)`. Keep `producer_state` as aggregate ingest
  health, but derive its source health as degraded while any component remains
  degraded.
- Backfill each existing incident from its attached opening evidence:
  site-level activity uses `site_activity`; source health uses source, site,
  and component alias; battery uses source, site, and entity alias. Abort the
  migration if an incident has no unambiguous derivation or multiple subjects.
- Make open-incident lookup key on `(site, category, subject_key)`.
- Add a partial unique index for one open incident per subject.
- Key future source-health incidents by source, site, and component; battery
  incidents by source, site, and entity; connectivity by site and `internet`;
  and pet exceptions by source, site, entity, and condition.
- Keep `subject_key` internal; safe queries should return the normalized event
  alias and condition instead.
- Require older binaries to understand schema 3 or refuse it explicitly.
- Update the routine pull's schema guard so it cannot deploy schema-3 code
  before the attended migration or silently perform that migration.

This prevents one source recovery from resolving another source's outage and
prevents a recovery at one site or component from clearing another site's
health, and prevents two pet-device exceptions at one site from collapsing
into one incident.

### Common invariants

- `occurred_at` describes the source observation or local action time;
  `observed_at` describes when the adapter observed the record.
- Sampled transitions use `observed_interval` with bounded `not_before` and
  `not_after` attributes.
- Every source uses deterministic event identity and at-least-once delivery.
- Adapters enqueue oldest-first and stop at the first failed publication so a
  delayed outage/recovery pair cannot be reversed.
- Provider or transport failure emits source health only after its own debounce
  and must not be rewritten as a device, pet, or site condition.
- Unknown aliases, unmapped devices, unexpected statuses, unsafe configuration,
  future timestamps, and stale replay fail closed.
- New events cannot open an activity incident, change presence, authorize an
  action, or create a notification outbox row.

### Capacity and retention

Freeze a total byte limit, record limit, and maximum record age for every
source journal, adapter pending set, and remote cursor before implementation.
No producer may delete an unacknowledged terminal record merely to make room.

- Vacancy journal exhaustion records a bounded degraded-health code when
  possible, drops only the new telemetry attempt, and lets the physical action
  continue unchanged.
- Connectivity and pet observers preserve committed records and last-known
  state, stop advancing publication when capacity is exhausted, and require
  attended repair rather than silently skipping a transition.
- The Crosstown bridge acknowledges an exact journal identity and sequence
  through a single constrained observer command only after the Mini cursor is
  durable. The observer may compact only the acknowledged prefix. Failed
  acknowledgement is harmless replay and must deduplicate.
- Status exposes safe queued-record and byte counts plus a bounded overflow
  counter, never record content.

## Workstream A: Vacancy-action outcomes

### Scope

Instrument only the site-wide actions inside the two
`confirmed_vacant` dispatch blocks:

- all lights off;
- central thermostat eco;
- each configured Crosstown minisplit off;
- Crosstown front-door lock check or lock command;
- Crosstown Roomba group start;
- each Cabin Roomba start.

The initial implementation explicitly excludes Eight Sleep's person-scoped
home-Pod reconciliation, arrival routines, manual commands, and the legacy
iMessage delivery result. Those paths can receive a separate privacy and
causality design later.

### Source journal

Add a small local helper used by `vacancy-actions.sh`:

1. Verify that canonical `state.json` and the protected presence producer state
   agree on the evaluation timestamp and 64-character state hash. A mismatch
   disables telemetry for that invocation but does not block the legacy
   actions.
2. Persist one opaque `cycle_<32-lowercase-hex>` identifier for the site's
   canonical `stateChangedAt`. This remains stable across later state-file
   rewrites during the same vacancy episode.
3. At the beginning of each shell invocation that will dispatch actions,
   generate a separate opaque `run_<32-lowercase-hex>` identifier and record a
   protected run intent under that cycle.
4. Before each device command, durably record an action intent with its own
   random attempt identifier. The attempt identifier becomes the stable raw
   `source_event_id` for publisher retries and is HMAC-discarded by the bus.
5. After the existing command and any existing verification, atomically finish
   the record with one terminal result.
6. On the next adapter run, convert a stale unfinished intent to
   `outcome_unknown`; never infer success and never retry the action.
7. Mark the run complete only after the existing vacancy marker has been
   written.

The current presence `state_hash` is retained as an exact causal join when the
protected states agree, but it is not the episode or idempotency key: normal
presence evaluations rewrite the state and can change that hash while a house
remains vacant.

The helper is local-file-only and bounded. If it cannot record telemetry,
`vacancy-actions.sh` logs one sanitized warning and continues with the existing
action and marker behavior. Neither the bus nor the helper changes the shell
command's success value.

### Proposed taxonomy

| Event type | Entity kind | Entity alias | Required safe attributes |
|------------|-------------|--------------|--------------------------|
| `automation.vacancy_run_started` | `workflow` | `vacancy` | `cycle_id`, `run_id`, `trigger_state_hash`, `triggered_at` |
| `automation.action_state_confirmed` | `automation_target` | Exact site-scoped target | `workflow`, `action`, `cycle_id`, `run_id`, `trigger_state_hash`, `verification`, `reason_code`, `not_before`, `not_after` |
| `automation.action_command_accepted` | `automation_target` | Exact site-scoped target | `workflow`, `action`, `cycle_id`, `run_id`, `trigger_state_hash`, `verification`, `reason_code`, `not_before`, `not_after` |
| `automation.action_failed` | `automation_target` | Exact site-scoped target | `workflow`, `action`, `cycle_id`, `run_id`, `trigger_state_hash`, `verification`, `reason_code`, `not_before`, `not_after` |
| `automation.action_skipped` | `automation_target` | Exact site-scoped target | `workflow`, `action`, `cycle_id`, `run_id`, `trigger_state_hash`, `verification`, `reason_code`, `not_before`, `not_after` |
| `automation.action_outcome_unknown` | `automation_target` | Exact site-scoped target | `workflow`, `action`, `cycle_id`, `run_id`, `trigger_state_hash`, `verification`, `reason_code`, `not_before`, `not_after` |
| `automation.vacancy_run_completed` | `workflow` | `vacancy` | `cycle_id`, `run_id`, `trigger_state_hash`, bounded confirmed/accepted/failed/skipped/unknown counts |
| `automation.vacancy_run_interrupted` | `workflow` | `vacancy` | `cycle_id`, `run_id`, `trigger_state_hash`, bounded confirmed/accepted/failed/skipped/unknown counts |
| `source.unavailable` | `adapter` | `site_writer` or `event_bridge` | `failure_count`, `reason_code` |
| `source.recovered` | `adapter` | Exact matching component alias | `outage_seconds` |

Allow only enumerated actions such as `turn_off`, `enable_eco`, `lock`, and
`start_cleaning`. Allow only bounded reason codes such as `completed`,
`already_satisfied`, `command_failed`, `verification_failed`, `snoozed`,
`policy_invalid_fail_closed`, `interrupted`, and `ack_unverified`.
Allow only `command_exit`, `state_confirmed`, `policy_decision`, or `none` as
verification. A successful command exit is not proof that physical state
changed and maps only to `automation.action_command_accepted`. Only the
existing August readback, including an already-locked readback, can produce
`automation.action_state_confirmed`. A command accepted without reliable
readback is never described as physically successful.

Exact target aliases are site-scoped and must be frozen in the source schema.
They may include `all_lights`, `central_hvac`, the three current minisplit
aliases, `front_door_lock`, the Crosstown Roomba group, and the two Cabin
Roombas. Provider IDs, command text, command output, message recipients, and
message bodies are forbidden.

### Correlation behavior

- Vacancy events remain journal context and do not open incidents.
- Initial correlator handling acknowledges them and increments bounded safe
  counters only. It performs no incident attachment or resolution.
- Safe queries may join an action to the matching canonical presence event
  through `trigger_state_hash`; a time-window guess is insufficient.
- The vacancy bridge retains a terminal record until the matching canonical
  `presence.occupancy_changed` event is available or reports bounded degraded
  telemetry health. It never substitutes a nearby event or blocks the action
  that already occurred.
- An observed August lock transition remains independent physical evidence.
  The action result explains intent and outcome but cannot replace the lock
  observer.
- A failed or unknown action result may be visible in safe queries, but any
  future notification policy requires separate authorization.

### Required tests

- Existing fake-device call order, counts, snooze behavior, marker behavior,
  and Eight Sleep behavior are byte-for-byte unchanged when the flag is off.
- An unavailable journal helper cannot block or alter a physical action.
- A full journal preserves every committed record, degrades safe telemetry
  health, and still cannot block or alter a physical action.
- Crash injection before a command, after a command, and before terminal record
  commit produces honest `outcome_unknown` behavior.
- Replayed journal records deduplicate without hiding a later real retry;
  retries share `cycle_id` but have distinct run and attempt identifiers.
- The cycle remains stable across ordinary state rewrites during one vacancy
  episode, while a later vacancy receives a different cycle.
- A protected presence-state mismatch disables telemetry only and leaves the
  existing actions and markers unchanged.
- `already_locked`, snoozed, malformed-snooze, partial Cabin Roomba success,
  August verification failure, zero-exit unverified commands, and command
  failure map to exact confirmed, accepted, skipped, unknown, or failed
  outcomes without overstating physical state.
- No command output, recipient, provider identifier, or secret reaches the
  spool, database, status projection, or logs.

## Workstream B: Site internet loss and recovery

### Observation model

Run an observer at each residence:

- **Cabin:** the Mac Mini can observe and journal locally even while its
  external internet path is unavailable.
- **Crosstown:** the always-on MacBook Pro must observe locally. The Mini being
  unable to reach that Mac is only `observer_unreachable`, never proof of an
  internet outage.

One observation round uses at least two bounded external probes with different
failure domains, but it may declare internet loss only while the site-local
route and gateway checks remain healthy. A gateway failure, reboot gap, local
interface problem, or observer clock jump is unknown/degraded observer state,
not a proven internet outage. Exact targets and route checks live only in a
protected configuration file and never enter events or logs.

The event claims only that the site-local observer could not reach the
internet; it does not claim a specific ISP, router, DNS provider, or upstream
service caused the loss.

### State machine

Start with these conservative defaults, then tune only from canary evidence:

- one round every 60 seconds;
- transition to unavailable after three consecutive external-probe quorum
  failures spanning at least two minutes while the local route and gateway are
  healthy;
- transition to recovered after two consecutive successful rounds;
- first valid state is a silent baseline;
- an unknown, malformed, or partially executed round does not advance either
  debounce counter and breaks a pending transition candidate;
- retain the last good state through observer errors;
- use observation intervals rather than pretending the transition occurred at
  an exact second.

The protected state records only the minimum required for debounce, transition
intervals, an opaque outage ID, and monotonic source sequence.

### Crosstown delayed transfer

Because a true Crosstown outage prevents immediate delivery:

1. The site-local observer appends sanitized transition records to a protected
   journal with a random journal identity and monotonic sequence.
2. A Mini bridge reads only the sanitized observer contract over the existing
   Tailscale SSH path after connectivity returns. The remote observer journal
   exposes only bounded read and exact monotonic-ack commands to the bridge and
   remains independent of canonical presence.
3. On first attach, the bridge establishes a silent watermark at the current
   journal head. It must not replay pre-activation history.
4. After activation, the bridge validates schema, size, exact site binding,
   journal identity, monotonic sequence, timestamp skew, and replay window,
   then enqueues records oldest-first.
5. The local cursor advances only after `home-eventctl enqueue` crosses its
   durable spool boundary. A crash after enqueue but before cursor commit
   safely replays the same source event ID and deduplicates.
6. After the local cursor is durable, the bridge may acknowledge that exact
   journal identity and sequence so the observer can compact only the accepted
   prefix. Failure to acknowledge preserves records and causes safe replay.
7. Journal identity change, rewind, retention overrun, or a cursor ahead of the
   remote head fails closed for attended repair; it must not silently
   rebaseline.
8. Preserve the source occurrence interval even though ingestion is delayed.

Allow a bounded delayed replay window sufficient for a multi-day outage; freeze
the exact limit in tests before activation. Records older than that window
quarantine safely rather than moving current state backwards.

### Proposed taxonomy

| Event type | Entity kind | Entity alias | Required safe attributes |
|------------|-------------|--------------|--------------------------|
| `connectivity.internet_unavailable` | `site_connectivity` | `internet` | `outage_id`, `not_before`, `not_after`, `distinct_failures`, `failure_span_seconds` |
| `connectivity.internet_recovered` | `site_connectivity` | `internet` | `outage_id`, `not_before`, `not_after`, `success_rounds` |
| `source.unavailable` | `adapter` | Exact component alias | `failure_count`, `reason_code` |
| `source.recovered` | `adapter` | Exact matching component alias | `outage_seconds` |

Freeze component aliases such as `site_observer`, `crosstown_bridge`, and
`bus_adapter`. `source.unavailable` covers only the named component and must
use safe codes such as `observer_unreachable`, `bridge_failed`, or
`invalid_observation`; recovery for one component cannot clear another. Source
health is not interchangeable with `connectivity.internet_unavailable`.

### Correlation behavior

- Initial correlator handling is journal-only with bounded safe counters.
- After source parity and ordering soak, a separate shadow-correlation gate may
  open a `connectivity` incident keyed to the site's `internet` subject;
  recovery resolves only that outage ID.
- Connectivity evidence may annotate overlapping activity or source-health
  incidents, but it cannot suppress, authorize, or reclassify activity.
- Presence scanners keep their current freshness and fail-closed behavior.
  Connectivity does not extend presence freshness.
- There is no notification or automatic remediation in this plan.

### Required tests

- A remote observer or SSH/Tailscale failure cannot emit internet loss.
- Partial probe success, malformed output, timeout, DNS-only failure, and
  observer exception do not produce a false outage transition.
- Debounce, recovery, flapping, boot baseline, clock skew, process restart, and
  stale replay are deterministic.
- A Crosstown loss/recovery pair retained during an outage arrives in source
  order and deduplicates after bridge or ingester restart.
- Unsafe remote output, wrong-site records, identity changes, rewinds,
  retention gaps, duplicate sequences, and future records fail closed.
- Journal capacity, exact-prefix acknowledgement, compaction, acknowledgement
  loss, and full-journal recovery preserve every unacknowledged transition.
- Events, status, and logs contain no endpoint, network, address, response, or
  topology data.
- Production verification never requires intentionally taking down either
  residence's internet.

## Workstream C: Pet-equipment exceptions

### Scope and aliases

Petlibro may reuse its existing exact protected selectors. Litter-Robot must
first gain an owner-only exact device binding and a dedicated sanitized
read-only `observe` command; its current general status path includes serial
and pet data, while its mutation helpers select the first returned robot.
Neither current general status command is a valid bus input.

Both providers require a dedicated closed-schema `observe` contract that emits
only the exact safe alias and exception inputs needed by the adapter:

| Source | Entity kind | Entity alias | Included conditions |
|--------|-------------|--------------|---------------------|
| Petlibro | `pet_feeder` | `feeder` | food low/recovered and explicit offline/online |
| Petlibro | `pet_fountain` | `fountain` | water low/recovered, filter due/cleared, battery low/recovered, and explicit offline/online |
| Litter-Robot | `litter_box` | `litter_box` | drawer full/cleared, allowlisted cycle fault/cleared, and explicit offline/online |

Cabin Petlibro devices that are seasonally unplugged must be marked
administratively disabled in protected configuration. A disabled device emits
neither offline nor recovered events and requires a new silent baseline when
re-enabled.

### Polling and transition rules

- Poll no more often than every 15 minutes initially.
- Serialize provider calls and perform no retry burst inside a tick.
- Baseline every enabled exact device silently.
- Require three complete provider polls explicitly reporting offline before an
  offline transition and two explicit online polls for recovery.
- Require two consecutive matching observations for low supply, maintenance,
  fault, and their recoveries unless a stable categorical provider state is
  proven during implementation.
- Apply hysteresis to numerical low/recovery thresholds.
- Store exact thresholds only in protected configuration and adapter state.
  Publish the condition and severity bucket, not raw consumption or health
  telemetry.
- Treat an authentication, API, transport, or malformed-response failure as
  source health. Never fan it out into an offline event for every device.
- Treat an explicit provider `online=false` field as a device observation only
  after the device mapping and entire response contract validate.
- An unknown provider status fails closed and degrades source health; it is not
  guessed into a known condition.
- Prefer Litter-Robot's explicit drawer-full field over an invented percentage
  threshold. A transient `PAUSED` state is not a fault; only persistent,
  reviewed fault enums may cross into `device.fault_detected`.

Threshold values, categorical mappings, and recovery bands must be selected
from sanitized fixtures and owner preference during work package 0. They are
not silently inherited from dashboard display logic.

### Proposed taxonomy

| Event type | Entity kind | Entity alias | Safe `condition` |
|------------|-------------|--------------|------------------|
| `device.supply_low` / `device.supply_recovered` | `pet_feeder` or `pet_fountain` | `feeder` or `fountain` as allowed above | `food` or `water` |
| `device.maintenance_required` / `device.maintenance_cleared` | `pet_fountain` or `litter_box` | `fountain` or `litter_box` | `filter` or `waste_drawer` |
| `device.fault_detected` / `device.fault_cleared` | `litter_box` | `litter_box` | Frozen allowlist of proven cycle faults |
| `device.battery_low` / `device.battery_recovered` | `pet_fountain` | `fountain` | `device_battery` |
| `device.offline` / `device.online` | Any exact pet-equipment kind above | Exact mapped alias | `connectivity` |
| `source.unavailable` / `source.recovered` | `adapter` | Exact site/provider component alias | Provider or adapter health only |

Device transitions use `observed_interval` and require `condition`,
`not_before`, and `not_after`; exception openings may also carry a bounded
`severity` enum. Do not store provider names in event types, raw percentages,
food or water amounts, filter-day counts, waste percentages, pet names, or
weights.

### Correlation behavior

- Initial correlator handling is journal-only with bounded safe counters.
- After each provider completes its parity soak, a separate
  shadow-correlation gate may open one `pet_equipment` incident per source,
  entity alias, and condition. The matching recovery resolves only that
  subject.
- Provider source health has a separate source, site, and component subject.
  Its recovery cannot clear another site, component, or device exception.
- Pet exceptions are never activity or occupancy evidence.
- Manual feed, clean, reset, filter-reset, or other mutation commands remain
  unreachable from the adapter and read-only skill.
- A manual action result never clears an exception; only a later independent
  observation can produce the recovery transition.

### Required tests

- Silent baseline, two-poll transition, hysteresis, recovery, restart,
  deduplication, stale input, and pending-event recovery.
- Exact mapping is required; unmapped, duplicate, missing, disabled, or
  wrong-type devices fail closed.
- The Litter-Robot observer rejects the current first-returned-robot behavior
  and requires its exact protected binding before publication.
- Provider failure produces source health without device-offline fan-out.
- Seasonal disable/re-enable does not create a false outage or recovery.
- Pending-capacity exhaustion preserves committed events, degrades only the
  exact site/provider component, and never invokes a mutation.
- Petlibro food, water, filter, battery, and explicit online fields map only
  through frozen fixtures.
- Litter-Robot drawer-full, explicit online, and each allowlisted fault map only
  through frozen fixtures.
- The observer cannot invoke Petlibro feed or Litter-Robot clean/reset paths,
  including through imported helper code or arbitrary arguments.
- Spool, database, status, safe queries, and logs contain no serial, provider
  ID, raw payload, feeding history, visit history, weight, pet identity, or
  precise consumption value.

## Work packages

### Work package 0: freeze contracts and attended decisions

1. Capture sanitized, synthetic fixtures for every supported input state.
2. Select connectivity probes and prove their failure domains without logging
   their raw responses.
3. Select pet thresholds, recovery bands, severity buckets, active devices,
   and seasonal enable policy.
4. Freeze source names, flags, aliases, event types, exact attributes, reason
   codes, component-health identities, timestamp bounds, delayed replay
   window, journal capacity, retention, compaction, and overflow behavior.
5. Confirm Eight Sleep and all delivery paths remain excluded.

Exit gate: every retained field and every excluded field can be reviewed
without running a producer.

### Work package 1: core schemas and subject-aware incidents

1. Extend strict source, event, entity, alias, time-precision, and attribute
   allowlists.
2. Add the four protected source spools with safe status projection.
3. Implement the attended transactional SQLite schema-3 table rebuild,
   producer-state backfill, pull guard, and downgrade refusal.
4. Add source/site/component health and subject-aware incident storage, but
   keep every new producer
   journal-only in the correlator initially.
5. Keep all new sources disabled and add no LaunchAgent activation.

Exit gate: isolated core and migration tests pass with zero runtime changes.

### Work package 2: vacancy source journal and adapter

1. Implement the protected local intent/outcome journal.
2. Add bounded, failure-independent instrumentation to the existing shell
   script.
3. Publish future records through a separate adapter.
4. Prove disabled-mode behavioral parity and crash honesty.

Exit gate: fake physical command traces and markers match the current behavior,
and source failure cannot block an action.

### Work package 3: connectivity observers and transfer

1. Implement the common site-local observer and state machine.
2. Add direct Cabin publication.
3. Add the protected Crosstown journal and the Mini's ordered, cursor-based
   Tailscale SSH bridge.
4. Test entirely with isolated probe and transport fakes.

Exit gate: observer-path loss and true site-local internet loss cannot be
confused in fixtures or status.

### Work package 4: pet exception adapters

1. Add provider-specific sanitized, read-only `observe` contracts.
2. Harden Litter-Robot with an exact protected binding before adding separate
   Petlibro and Litter-Robot adapters.
3. Add exact protected active-device and threshold configuration.
4. Implement silent baselines, debounce, hysteresis, pending-event recovery,
   and source-health separation.
5. Prove mutation paths are structurally unreachable.

Exit gate: only exception transitions from exact enabled aliases can spool.

### Work package 5: safe queries and documentation

1. Expose the new normalized events through the existing read-only
   `home-events` CLI.
2. Extend explanations with exact vacancy causality and connectivity context
   without exposing internal subject keys.
3. After source parity soak, gate subject-aware connectivity and pet incidents
   separately; vacancy outcomes remain journal context.
4. Update `HOME-EVENTS.md`, `LAUNCHAGENTS.md`, `logs/README.md`, the
   `home-events` skill, and deployment checks in the same implementation
   change.
5. Document operator status checks and independent rollback commands.

Exit gate: the agent can explain the new evidence but cannot activate,
configure, replay, acknowledge, notify, or control anything.

## Future attended rollout

Roll out one boundary at a time; do not enable these sources concurrently:

1. Re-run compilation, unit/integration tests, `home-eventctl check-config`,
   plist validation, permissions checks, backlog/dead-letter inspection, and
   zero-delivery verification.
2. Quiesce writers, take and integrity-check the WAL-consistent SQLite backup,
   then apply schema 3 in an attended migration while every new source and
   LaunchAgent remains off. A failed preflight or migration leaves the original
   database and binaries in place.
3. Install the vacancy bridge disabled and prove it creates no state, replays
   no history, and invokes no device command.
4. Enable Cabin vacancy publication first. Wait for one organic vacancy cycle;
   never touch canonical presence or delete a vacancy marker to manufacture
   it. After parity, repeat separately at Crosstown.
5. Soak vacancy evidence for seven days and compare every outcome with the
   existing sanitized action log and physical observer evidence where
   available.
6. Enable the Cabin connectivity observer first, establish its silent healthy
   baseline, then enable its local adapter. Use injected tests for loss/recovery and
   wait for natural production evidence; do not disconnect the live site.
7. After a clean Cabin soak, prove the Crosstown journal identity, cursor,
   ordering, and replay contract with synthetic fixtures, then enable its
   observer and wait for natural evidence.
8. Enable Crosstown Petlibro, then Crosstown Litter-Robot, one provider at a
   time. Enable Cabin Petlibro only during an attended period when its seasonal
   devices are physically in service. Baseline silently and wait for a natural
   exception or use only an isolated fixture. Do not empty supplies, fill a
   waste drawer, or disconnect a device as a test.
9. Assess and roll back every source independently. Any duplicate, guessed
   alias, privacy leak, ordering gap, false outage, device-offline fan-out,
   physical-behavior change, or delivery attempt stops that producer.
10. Consider notifications only in a separate plan and under separate explicit
   authorization.

## Rollback

1. Set only the affected installed producer flag to `0` and stop only its
   adapter or site observer.
2. Preserve source journals, remote observer journals, adapter state, cursors,
   spools, database,
   and safe logs for diagnosis.
3. Do not delete vacancy markers, canonical presence, pet configuration,
   connectivity observer state, or provider credentials during rollback.
4. Do not undo or compensate for a physical vacancy action from bus state.
5. Keep the other producers, current automations, and current provider CLIs
   unchanged.
6. If schema 3 has been applied, use binaries that understand it; do not
   destructively downgrade the database.

## Expected tracked implementation surface

No file below is changed by this planning task. A later implementation is
expected to touch or add:

- `openclaw/bin/home_event_bus.py`
- `openclaw/bin/home-event-correlator.py`
- `openclaw/bin/home-event-service-wrapper.sh`
- `openclaw/bin/dotfiles-pull.command`
- `openclaw/workspace/scripts/vacancy-actions.sh`
- a vacancy source-journal helper and adapter under `openclaw/bin/`
- a site-connectivity observer and cursor-based bridge under `openclaw/bin/`
- dedicated Petlibro and Litter-Robot observe paths and adapters
- disabled-by-default LaunchAgents under `openclaw/launchagents/`
- source-specific tests under `openclaw/tests/`
- `openclaw/HOME-EVENTS.md`
- `openclaw/VACANCY-AUTOMATION.md`
- `openclaw/LAUNCHAGENTS.md`
- `openclaw/bin/README.md`
- `openclaw/logs/README.md`
- `openclaw/skills/home-events/SKILL.md`

## Decisions required before implementation

1. Exact pet low/recovery thresholds and severity buckets.
2. Which seasonal Petlibro devices are administratively enabled.
3. Exact connectivity probe targets, delayed replay window, and whether a
   DNS-only degradation should remain status-only.
4. Whether safe queries should show every successful vacancy action by default
   or only failures, skips, unknown outcomes, and an aggregate run summary.
5. Whether any pet or connectivity incident may ever notify; the default in
   this plan is no.
6. Whether person-scoped Eight Sleep reconciliation should receive a later,
   separate action-outcome design; the default in this plan is no.

## Definition of done

Future implementation is complete only when:

- Current home-event canaries and soak have already closed cleanly.
- Every new source defaults off and can be disabled independently.
- Existing vacancy physical behavior and markers are unchanged under all
  disabled, enabled, and telemetry-failure tests.
- Vacancy records preserve exact presence causality and honest unknown
  outcomes.
- Site-local internet loss is distinct from observer or bridge failure, and
  delayed Crosstown transitions retain order.
- Pet provider failure cannot masquerade as a device failure, and only exact
  enabled aliases produce exception transitions.
- Per-site component health and subject-aware incidents cannot resolve another
  source, site, component, or device's condition, and ambiguous legacy
  incidents make migration fail closed.
- No forbidden identifier, raw payload, personal pet history, probe detail, or
  command output enters protected or safe projections.
- The bus remains shadow-only with zero physical-control, presence-mutation,
  media, model, or delivery path.
