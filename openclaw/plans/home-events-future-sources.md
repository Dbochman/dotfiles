# Home Events Future Work — Vacant-House Actions, Pet Care, and Connectivity

## Status: IMPLEMENTATION IN PROGRESS — OBSERVATION ONLY

On `2026-08-15`, the operator approved the first implementation boundary: a
protected local source journal around the existing vacancy runner. That
approval covers code, tests, documentation, routine deployment, and local
observation initialization only. It does **not** authorize a bus adapter,
source flag, schema migration, reservation worker, new physical action,
notification, Julia route, LaunchAgent, connectivity probe, pet mutation, or
intentional internet outage. Publication, shadow decisions, and every physical
action promotion remain separate attended decisions.

The next product focus is confident, mostly silent operation of a confirmed-
vacant house. Expanding the alert audience is deferred. The existing vacancy
runner remains authoritative until its outcomes are observable and each later
action cutover meets an independent gate. The Ring, August,
canonical-presence, Nest, and local-presence producers continue unchanged.

### `2026-08-15` observation checkpoint

- Added `vacancy-action-journal.py`, an owner-only, bounded, local-file helper
  with strict site/target/action enums and opaque vacancy-cycle, run, and
  attempt identifiers.
- Instrumented only the existing site-wide vacancy dispatch blocks. Command
  order, arguments, marker behavior, Roomba snooze policy, Eight Sleep
  reconciliation, iMessage behavior, and every existing physical executor are
  unchanged.
- The helper accepts a run only when canonical presence and protected producer
  state have an exact timestamp and recomputed state-hash match and the target
  site is fresh `confirmed_vacant`. Telemetry failure logs one sanitized
  warning and remains fail-open for the legacy runner.
- A stale unfinished intent becomes terminal `outcome_unknown`; it never
  infers success or retries a command. A run is complete only after the legacy
  vacancy marker exists.
- Added isolated journal and fake-device regression coverage. No bus schema,
  source list, policy, worker, recipient, or service lifecycle changed.
- The journal is the observation source of record but is not yet published to
  the event bus. An adapter and schema-6 migration remain future gates.

## Purpose

Extend the private home-event journal and its separately gated action plane in
this priority order:

1. **Vacancy-action confidence** — distinguish independently confirmed state,
   accepted commands, failures, policy skips, and unknown results for the
   actions already taken when a house becomes vacant.
2. **Lighting and HVAC promotion** — define exact per-site vacant profiles,
   prove readback and idempotency, then canary low-risk reversible actions one
   target at a time.
3. **Pet-equipment readiness and care** — record low supplies, maintenance
   needs, faults, and explicit offline/recovery transitions before considering
   one tightly bounded missed-feed recovery action.
4. **Site internet loss and recovery** — distinguish a quiet home from a site
   whose external connectivity disappeared; this remains useful context but
   follows the vacant-house action work.

Observation begins query-only and shadow-only. Canonical presence remains the
only occupancy authority. The existing automation remains the sole executor
until an exact target is explicitly promoted; a target may never be live in
both legacy and bus-reserved execution paths at once.

## Goals

1. Reuse the existing strict, vendor-neutral event envelope and durable SQLite
   ingester.
2. Keep every new source independently disabled by default and independently
   reversible.
3. Preserve exact causal context for existing vacancy actions before changing
   their execution path.
4. Classify each action as state-confirmed, command-accepted, failed, skipped,
   or unknown without overstating a successful command exit.
5. Promote lighting and HVAC only through exact per-site profiles, fresh
   confirmed-vacancy revalidation, one reservation per vacancy cycle, bounded
   execution age, independent readback, and per-target rollback.
6. Detect internet reachability from a process running at the affected site,
   rather than equating an unreachable bridge with an internet outage.
7. Publish pet-device exception transitions, not continuous telemetry, before
   introducing any pet-care mutation.
8. Allow a future feeder recovery action only if an exact device exposes an
   independently verifiable missed scheduled feed and confirmed dispense
   result; vacancy alone can never authorize feeding.
9. Baseline sampled sources silently and use bounded hysteresis or debounce to
   prevent flapping.
10. Retain only safe aliases, bounded enums, transition intervals, and opaque
   local identifiers.
11. Keep successful automation quiet. Notifications, if later approved, cover
    only failed or unknown safety-relevant outcomes and remain separate from
    action authorization.

## Non-goals

- Changing the current vacancy trigger, action order, retry behavior, marker
  semantics, Roomba snooze policy, Eight Sleep reconciliation, or existing
  iMessage path during the observation phase.
- Treating an automation result, internet state, or pet-device state as
  evidence that a person is home or away.
- Inferring a provider or ISP outage when only a bridge, observer, DNS lookup,
  or management path is unavailable.
- Retaining raw command output, provider payloads, account or device IDs,
  serial numbers, network addresses, SSIDs, probe targets, DNS answers, HTTP
  bodies, or topology.
- Retaining pet visit history, weights, inferred pet identity, raw feeding
  schedules, consumption history, or health timelines.
- Adding Ring Alarm, HomeKit, Home Assistant, a network broker, or new hardware.
- Restoring lights or comfort settings on arrival; welcome-home routines remain
  contextual and separate.
- Starting a Roomba, locking a door, cleaning or resetting a litter box,
  resetting a filter, or dispensing food through a generic model/tool route.
- Feeding because a house is vacant, food is low, a provider is unreachable,
  or a previous dispense has an ambiguous outcome.
- Expanding notification recipients as a prerequisite for action work.
- Intentionally disconnecting either residence to manufacture production
  evidence.

## Activation preconditions

Before observation is considered ready for an attended rollout:

1. Confirm the existing bus has no unexplained backlog, dead letters,
   cross-site incidents, duplicate evidence, or unreviewed delivery outcome.
   Julia recipient expansion and another successful notification are not
   prerequisites for read-only vacancy outcome instrumentation.
2. Confirm canonical presence and both local-presence adapters remain healthy;
   no action may use local-presence inference or camera evidence as occupancy
   authority.
3. Freeze the event contracts, safe aliases, thresholds, probe policy, and
   source-specific retention behavior in tests before touching production.
4. Obtain a separate explicit decision to implement, then later explicit
   decisions to activate each producer and each physical action target.

Before any new physical action is activated:

1. Complete the matching read-only outcome and readback soak at both sites.
2. Freeze the exact target, desired state, seasonal constraints, maximum event
   age, cooldown, independent verification, and rollback behavior.
3. Prove the current executor and proposed executor cannot both act on the same
   target in one vacancy cycle.
4. Revalidate fresh `confirmed_vacant` state and the protected presence hash
   immediately before reservation and immediately before execution.

## Proposed architecture

```text
Canonical presence -> existing vacancy runner -> outcome journal --\
Petlibro/Litter-Robot sanitized observe -> adapters ----------------+--> protected
Cabin/Crosstown connectivity observers -> adapters/bridge ----------/    source spools
                                                                         |
                                                                 SQLite ingester
                                                                    /         \
                                                   safe outcomes/queries   disabled action
                                                                          reservations
                                                                                |
                                                                     exact action worker
```

During observation, the bus is not a prerequisite for any existing physical
action. Each producer first commits to a protected source-owned journal or
state file; a separate adapter publishes normalized events. A stopped ingester
therefore causes a backlog, not changed household behavior.

A later action worker is a separate, disabled-by-default boundary. It accepts
only durable reservations containing a frozen site, target alias, action enum,
vacancy-cycle ID, expiry, and protected policy hash. It has no arbitrary
command, argument, device-discovery, model, or notification interface. A
target moves to that path only through an attended handoff that disables its
legacy invocation first. Failure or ambiguity fails closed and never triggers
an automatic retry.

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

Physical actions use a separate mode-`0600` protected policy and exact
per-site, per-target installed gates, all tracked disabled by default. There is
no `all`, site-wide, provider-wide, or discovered-device action selector. The
action worker must validate that its deployed code, policy hash, canonical
presence contract, and target binding match before claiming a reservation.

### Event-envelope and database versions

The normalized envelope can remain schema version 1 because its existing
fields are sufficient. The current production database is schema version 5;
SQLite must migrate transactionally to schema version 6 before any new source
can enqueue because the current
`producer_inbox`, `events`, and `producer_state` table constraints hard-code
the four existing sources.

The attended migration must quiesce the ingester and correlator, take an
owner-only WAL-consistent backup through SQLite's backup API, run
`PRAGMA integrity_check` against the copy, and retain it until the migration
and post-migration verification complete. Copying only `events.sqlite3` while
writers or a WAL are active is not an acceptable backup.

The schema-6 transaction then rebuilds the source-constrained tables while
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
- Add durable action reservations and outcomes keyed by exact
  `(site, target_alias, vacancy_cycle_id)`, with policy hash, expiry, attempt
  boundary, executor, terminal result, and independent verification class.
- Enforce one physical attempt per reservation. `unknown` is terminal; a later
  observation may annotate it but cannot make it retryable.
- Require older binaries to understand schema 6 or refuse it explicitly.
- Update the routine pull's schema guard so it cannot deploy schema-6 code
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
- New observation events cannot open an activity incident, change presence, or
  create a notification outbox row. Only a separately activated action policy
  may create an exact, expiring reservation after revalidating canonical
  vacancy; source-health, connectivity, camera, and local-presence events can
  never authorize one.

### Capacity and retention

Freeze a total byte limit, record limit, and maximum record age for every
source journal, adapter pending set, and remote cursor before implementation.
No producer may delete an unacknowledged terminal record merely to make room.

- Vacancy journal exhaustion records a bounded degraded-health code when
  possible, drops only the new telemetry attempt, and lets the physical action
  continue unchanged.
- Action-reservation capacity exhaustion fails closed before command execution.
  It cannot evict an unexpired or nonterminal reservation, fall back to the
  legacy executor, or authorize an unjournaled action.
- Connectivity and pet observers preserve committed records and last-known
  state, stop advancing publication when capacity is exhausted, and require
  attended repair rather than silently skipping a transition.
- The Crosstown bridge acknowledges an exact journal identity and sequence
  through a single constrained observer command only after the Mini cursor is
  durable. The observer may compact only the acknowledged prefix. Failed
  acknowledgement is harmless replay and must deduplicate.
- Status exposes safe queued-record and byte counts, reservation state counts,
  terminal outcome counts, and a bounded overflow counter, never record
  content or device arguments.

## Workstream A: Vacancy-action confidence and promotion

### Scope

First instrument the site-wide actions already inside the two
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

After outcome parity, promote only the low-risk reversible tier:

| Site | Target | Current behavior | Proposed action-profile decision |
|------|--------|------------------|----------------------------------|
| Crosstown | Hue `all_lights` | Off on vacancy | Retain and require aggregate off readback |
| Crosstown | Central HVAC | Nest eco on vacancy | Retain and require eco-state readback |
| Crosstown | Cielo minisplits | Bedroom, Office, and Living Room off | Freeze exact aliases; decide separately whether Basement joins the vacant profile |
| Cabin | Hue `all_lights` | Off on vacancy | Retain and require aggregate off readback |
| Cabin | Central HVAC | Nest eco on vacancy | Retain and require eco-state readback |
| Cabin | Midea ACs | Not vacancy-managed | Select an explicit off or protective setpoint profile for `cabin-air-conditioner` and `cabin-lil-air-conditioner` before either is eligible |

The August lock and Roomba actions remain in the outcome journal but are not
part of the first promotion canary. Locking has a stronger security impact;
Roomba starts have pet, obstacle, and shared-snooze considerations. Their
existing behavior remains unchanged until separately reviewed.

### Confidence contract

An action is promotable only when all of the following are frozen in protected
configuration and tested:

1. Exact site and target alias; discovery or first-returned-device selection is
   forbidden.
2. Exact desired state, including a seasonal HVAC profile where `off` may be
   unsafe for humidity or freeze protection.
3. Fresh canonical `confirmed_vacant` state and matching protected state hash
   at reservation and execution time.
4. One action reservation per target and vacancy cycle, plus a bounded maximum
   age. A later state-file rewrite cannot re-arm the same cycle.
5. Independent state readback with a bounded settle window. A zero exit status
   alone remains only `command_accepted`.
6. No retry after an unknown outcome. A later independent observation may
   confirm state, but it cannot cause another command.
7. Per-target enablement and rollback. No site-wide master switch may silently
   promote an unreviewed target.

Successful actions remain quiet. Safe status and queries expose aggregate run
results; failure or uncertainty may become an owner-only exception only under
a later, separate notification decision.

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
changed and maps only to `automation.action_command_accepted`. The existing
August readback, including an already-locked readback, is the first proven
`state_confirmed` contract. Hue, Nest, Cielo, and Midea may receive equivalent
status readbacks only after sanitized fixtures prove exact-device selection,
desired-state comparison, settle timing, and failure behavior. A command
accepted without reliable readback is never described as physically
successful.

Exact target aliases are site-scoped and must be frozen in the source schema.
They may include `all_lights`, `central_hvac`, the exact configured minisplit
aliases, `cabin-air-conditioner`, `cabin-lil-air-conditioner`,
`front_door_lock`, the Crosstown Roomba group, and the two Cabin Roombas.
Provider IDs, command text, command output, message recipients, and message
bodies are forbidden.

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

### Action promotion behavior

1. **Observe:** instrument the current runner with action policy disabled and
   compare every outcome to the sanitized legacy log and independent state
   readback.
2. **Shadow:** compute exact would-reserve decisions from the canonical vacancy
   event, but create no reservation and invoke no command. Compare them with
   the current runner for at least two organic vacancy cycles per site.
3. **Canary:** move one target at one site to the reservation worker while
   atomically disabling that target in the legacy runner. Start with Hue, then
   central HVAC, then individually approved Cielo or Midea targets.
4. **Expand:** promote another target only after the previous target has two
   independently confirmed organic cycles, zero duplicates, zero stale
   actions, and a demonstrated per-target rollback.

The worker revalidates current canonical vacancy immediately before command
execution. `occupied`, `possibly_vacant`, stale, malformed, hash-mismatched, or
source-degraded state cancels the reservation without acting. A bus outage may
delay or skip a promoted action; it must never fall back to a second executor
or replay an expired reservation.

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
- Hue, Nest, Cielo, and Midea readbacks distinguish exact desired state,
  conflicting state, offline/unavailable, timeout, and malformed data without
  guessing success.
- Reservation expiry, duplicate vacancy writes, occupied-before-execution,
  policy-hash change, worker crash, and ambiguous command completion cannot
  produce a second physical command.
- A target cannot be simultaneously enabled in the legacy runner and action
  worker; deployment and rollback tests reject overlap before either process
  starts.
- No command output, recipient, provider identifier, or secret reaches the
  spool, database, status projection, or logs.

## Workstream B: Site internet loss and recovery (deferred)

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
- The connectivity path has no notification or automatic remediation.

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

## Workstream C: Pet-equipment readiness and bounded care

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

The feeder observer may expose a bounded scheduled-feed outcome only if the
provider contract independently distinguishes a completed feed from a missed
feed. Merely having a schedule configured, seeing a next-feed time, or failing
to read the provider is insufficient. If that distinction cannot be proven
from sanitized fixtures, automatic feeder recovery remains unavailable.

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
| `device.feed_window_missed` / `device.feed_window_satisfied` | `pet_feeder` | `feeder` | `scheduled_feed` |
| `source.unavailable` / `source.recovered` | `adapter` | Exact site/provider component alias | Provider or adapter health only |

Device transitions use `observed_interval` and require `condition`,
`not_before`, and `not_after`; exception openings may also carry a bounded
`severity` enum. Do not store provider names in event types, raw percentages,
food or water amounts, filter-day counts, waste percentages, pet names, or
weights. A feed-window event retains only an opaque local `meal_window_id` and
bounded observation interval; schedule time, recipe, and portion count remain
in protected policy and adapter state.

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
  unreachable from the adapter and read-only skill. Any later feeding recovery
  is available only to the separate exact action worker.
- A manual action result never clears an exception; only a later independent
  observation can produce the recovery transition.

### Optional missed-feed recovery

Feeding is a later action tier, not part of the initial pet observer rollout.
Vacancy, low food, offline state, source failure, or a user request inferred by
a model can never authorize it. A recovery reservation requires every item
below:

1. Exact protected feeder alias and a provider contract that independently
   proves the scheduled feed was missed.
2. Fresh canonical `confirmed_vacant` state at the feeder's site and matching
   protected presence hash at reservation and execution.
3. Healthy provider and adapter, explicit device online state, and no active
   food-low condition.
4. An exact active meal window, locally generated opaque `meal_window_id`,
   configured maximum portions, and one reservation for that feeder/window.
5. A bounded execution deadline inside the meal window. Expired work is
   cancelled, not replayed.
6. One dispense attempt followed by independent completion readback. Accepted
   without verification becomes terminal `unknown` and is never retried.
7. Per-feeder enablement and immediate rollback without affecting observation,
   schedules, another site, or another pet device.

The first implementation, if the evidence contract is viable, canary-runs on
one exact feeder and synthetic fixtures only. Production activation waits for
a natural provider-reported missed feed; do not disable a real schedule, empty
a hopper, disconnect a feeder, or skip a meal to create evidence. Successful
recovery remains quiet. Failure or uncertainty may become an owner-only
exception only through a separately approved notification policy.

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
- A configured schedule, next-feed time, stale history, provider error, and
  absent history cannot become `device.feed_window_missed`; only a proven
  provider outcome contract may emit it.
- Litter-Robot drawer-full, explicit online, and each allowlisted fault map only
  through frozen fixtures.
- The observer cannot invoke Petlibro feed or Litter-Robot clean/reset paths,
  including through imported helper code or arbitrary arguments.
- Feeding reservations deduplicate per exact feeder and meal window; expiry,
  worker crash, ambiguous completion, occupied-before-execution, and policy
  change cannot issue a second dispense.
- Food-low, offline, source-degraded, unmapped, disabled, seasonal, or
  possibly-vacant state always blocks feeding.
- Spool, database, status, safe queries, and logs contain no serial, provider
  ID, raw payload, feeding history, visit history, weight, pet identity, or
  precise consumption value.

## Work packages

### Work package 0: freeze contracts and attended decisions

1. Capture sanitized, synthetic fixtures for every supported input state.
2. Freeze exact vacant profiles and readback contracts for Hue, Nest, each
   current Cielo target, both Midea aliases, August, and each Roomba target.
   Decide whether the basement Cielo joins the profile and whether each Midea
   uses off or a protective seasonal setpoint.
3. Select pet thresholds, recovery bands, severity buckets, active devices,
   seasonal enable policy, and determine whether the provider exposes a proven
   missed-feed and completion contract.
4. Select connectivity probes and prove their failure domains without logging
   their raw responses.
5. Freeze source names, flags, aliases, event types, exact attributes, reason
   codes, component-health identities, timestamp bounds, delayed replay
   window, journal and reservation capacity, retention, compaction, overflow,
   action expiry, settle windows, and policy-hash behavior.
6. Confirm Eight Sleep, arrival routines, general tool/model control, and all
   delivery paths remain excluded.

Exit gate: every retained field and every excluded field can be reviewed
without running a producer.

### Work package 1: core schemas and subject-aware incidents

1. Extend strict source, event, entity, alias, time-precision, and attribute
   allowlists.
2. Add the four protected source spools with safe status projection.
3. Implement the attended transactional SQLite schema-6 table rebuild,
   producer-state backfill, pull guard, and downgrade refusal.
4. Add source/site/component health, subject-aware incident storage, durable
   exact action reservations, terminal outcomes, and safe aggregate action
   status.
5. Keep every new producer and action policy disabled and add no LaunchAgent
   activation.

Exit gate: isolated core and migration tests pass with zero runtime changes.

### Work package 2: vacancy outcomes and readback

1. Implement the protected local intent/outcome journal.
2. Add bounded, failure-independent instrumentation to the existing shell
   script.
3. Add exact sanitized readback contracts for the current action targets,
   classifying confirmed, accepted, failed, skipped, and unknown honestly.
4. Publish records through a separate adapter and expose aggregate cycle
   outcomes through safe status and queries.
5. Prove disabled-mode behavioral parity, crash honesty, and that telemetry
   failure cannot change current physical behavior.

Exit gate: fake physical command traces and markers match the current behavior,
source failure cannot block a legacy action, and no readback overstates state.

### Work package 3: lighting and HVAC action foundation

1. Add a mode-`0600` protected action policy with exact per-site, per-target
   gates, desired states, seasonal constraints, expiry, and policy hash.
2. Implement the exact reservation worker without discovery, arbitrary
   arguments, general skill invocation, delivery, or model access.
3. Add shadow would-reserve decisions and per-target legacy/action-worker
   exclusion checks.
4. Add exact Hue, Nest, Cielo, and Midea executors with one-attempt boundaries
   and independent readback.
5. Implement cancellation on occupied, possibly-vacant, stale, malformed,
   hash-mismatched, policy-changed, or degraded canonical state.

Exit gate: the worker remains disabled; isolated tests prove one action at most
per target and vacancy cycle, no legacy overlap, and honest terminal outcomes.

### Work package 4: pet exception adapters

1. Add provider-specific sanitized, read-only `observe` contracts.
2. Harden Litter-Robot with an exact protected binding before adding separate
   Petlibro and Litter-Robot adapters.
3. Add exact protected active-device and threshold configuration.
4. Implement silent baselines, debounce, hysteresis, pending-event recovery,
   and source-health separation.
5. Prove mutation paths are structurally unreachable.

Exit gate: only exception transitions from exact enabled aliases can spool.

### Work package 5: optional feeder recovery

Begin this package only if work package 4 proves independent missed-feed and
completion evidence. Otherwise record it as unavailable and stop.

1. Freeze one exact feeder, maximum portions, meal-window identity, deadline,
   source-health gate, vacancy gate, and readback contract.
2. Add `device.feed_window_missed`/`satisfied` observation without exposing raw
   schedules or feeding history.
3. Add a dedicated action policy entry and exact dispense executor with one
   reservation and one attempt per meal window.
4. Prove that every ambiguous, stale, occupied, offline, low-food, degraded,
   disabled, or duplicate case fails closed without dispensing.

Exit gate: the feeder action remains disabled; fixtures prove no possible
double feed and no path from vacancy alone to dispensing.

### Work package 6: connectivity observers and transfer

1. Implement the common site-local observer and state machine.
2. Add direct Cabin publication.
3. Add the protected Crosstown journal and the Mini's ordered, cursor-based
   Tailscale SSH bridge.
4. Test entirely with isolated probe and transport fakes.

Exit gate: observer-path loss and true site-local internet loss cannot be
confused in fixtures or status.

### Work package 7: safe queries and documentation

1. Expose the new normalized events through the existing read-only
   `home-events` CLI.
2. Extend explanations with exact vacancy causality, action confidence, feeder
   readiness, and connectivity context without exposing internal subject keys.
3. After source parity soak, gate subject-aware connectivity and pet incidents
   separately; vacancy outcomes remain journal context.
4. Update `HOME-EVENTS.md`, `LAUNCHAGENTS.md`, `logs/README.md`, the
   `home-events` skill, and deployment checks in the same implementation
   change.
5. Document operator status checks and independent rollback commands.

Exit gate: the agent can explain evidence and action outcomes but cannot alter
protected policy, activate a target, replay or acknowledge work, notify, or
invoke an action outside the exact worker.

## Future attended rollout

Roll out one boundary at a time; observation and action activation are separate
checkpoints:

1. Re-run compilation, unit/integration tests, `home-eventctl check-config`,
   plist validation, permissions checks, backlog/dead-letter inspection, and
   unreviewed-outcome and protected-policy inspection.
2. Quiesce writers, take and integrity-check the WAL-consistent SQLite backup,
   then apply schema 6 in an attended migration while every new source and
   LaunchAgent remains off. A failed preflight or migration leaves the original
   database and binaries in place.
3. Install the vacancy bridge disabled and prove it creates no state, replays
   no history, and invokes no device command.
4. Enable Cabin vacancy publication first. Wait for one organic vacancy cycle;
   never touch canonical presence or delete a vacancy marker to manufacture
   it. After parity, repeat separately at Crosstown.
5. Soak vacancy outcomes for at least seven days and two organic vacancy cycles
   per site. Compare every target with the sanitized action log and independent
   readback; resolve every unknown or mismatch before action shadowing.
6. Enable lighting/HVAC shadow decisions. Require two more organic cycles per
   site with exact parity and no stale, duplicate, wrong-site, or uncertain
   reservation decision.
7. Promote one Hue target at one site while atomically disabling only its
   legacy invocation. After two confirmed organic cycles and a demonstrated
   rollback, promote the other site's Hue target.
8. Promote central HVAC and then individually approved Cielo/Midea targets one
   at a time. Do not activate a basement Cielo or Cabin Midea profile until its
   seasonal desired state and readback are explicitly approved.
9. Enable Crosstown Petlibro, then Crosstown Litter-Robot, one provider at a
   time. Enable Cabin Petlibro only during an attended period when its seasonal
   devices are physically in service. Baseline silently and wait for a natural
   exception or use only an isolated fixture. Do not empty supplies, fill a
   waste drawer, or disconnect a device as a test.
10. If and only if missed-feed and completion evidence is proven, shadow one
    exact feeder through natural meal windows. A later attended decision may
    activate one missed-feed recovery canary; never manufacture a missed meal.
11. Enable the Cabin connectivity observer, then Crosstown connectivity, only
    after the action and pet work above. Use injected tests and natural outage
    evidence; do not disconnect a live site.
12. Assess and roll back every source and target independently. Any duplicate,
    double-executor overlap, stale action, guessed alias, privacy leak,
    ordering gap, false outage, device-offline fan-out, unexpected physical
    behavior, or ambiguous action retry stops that exact boundary.
13. Keep successful operation silent. Consider failure/unknown notifications
    only in a separate plan and under separate explicit authorization;
    recipient expansion is not an action-promotion gate.

## Rollback

1. Set only the affected installed producer flag to `0` and stop only its
   adapter or site observer.
2. For an action target, take the action lock, disable only that protected
   target, cancel unclaimed reservations, and allow an already-started one-
   attempt command/readback boundary to reach an honest terminal outcome.
3. Re-enable a legacy target only after no matching reservation is executing
   or claimable and deployment validation proves the two paths cannot overlap.
   Preserve the vacancy-cycle marker so rollback cannot repeat an action that
   may already have happened.
4. Disabling feeder recovery leaves the provider's ordinary schedule and
   read-only observer unchanged. Never issue a compensating feed.
5. Preserve source journals, remote observer journals, adapter state, cursors,
   reservations, outcomes, spools, database, and safe logs for diagnosis.
6. Do not delete vacancy markers, canonical presence, pet configuration,
   connectivity observer state, or provider credentials during rollback.
7. Do not undo or compensate for a physical vacancy action from bus state.
8. Keep the other producers, action targets, current automations, and provider
   CLIs unchanged.
9. If schema 6 has been applied, use binaries that understand it; do not
   destructively downgrade the database.

## Expected tracked implementation surface

The first observation checkpoint changed or added:

- `openclaw/bin/vacancy-action-journal.py`
- `openclaw/workspace/scripts/vacancy-actions.sh`
- `openclaw/tests/test_vacancy_action_journal.py`
- `openclaw/tests/test-vacancy-actions.sh`
- `openclaw/HOME-EVENTS.md`
- `openclaw/VACANCY-AUTOMATION.md`
- `openclaw/bin/README.md`

Later publication and action-promotion work is expected to touch or add:

- `openclaw/bin/home_event_bus.py`
- `openclaw/bin/home-event-correlator.py`
- an exact `home-event-action` worker and wrapper under `openclaw/bin/`
- a tracked disabled action-policy template with protected installed state
- `openclaw/bin/home-event-service-wrapper.sh`
- `openclaw/bin/dotfiles-pull.command`
- a vacancy source-journal adapter under `openclaw/bin/`
- a site-connectivity observer and cursor-based bridge under `openclaw/bin/`
- dedicated Petlibro and Litter-Robot observe paths and adapters, plus an exact
  feeder executor only if the scheduled-outcome contract is viable
- disabled-by-default LaunchAgents under `openclaw/launchagents/`
- source-specific tests under `openclaw/tests/`
- `openclaw/HOME-EVENTS.md`
- `openclaw/VACANCY-AUTOMATION.md`
- `openclaw/LAUNCHAGENTS.md`
- `openclaw/bin/README.md`
- `openclaw/logs/README.md`
- `openclaw/skills/home-events/SKILL.md`
- the exact Hue, Nest, Cielo, Midea, Petlibro, and Litter-Robot control or
  observer documentation affected by an implemented target

## Decisions required before publication or physical promotion

1. Exact vacant profile for central HVAC, every current Cielo target, and both
   Cabin Midea aliases, including whether Basement participates and where a
   protective seasonal setpoint replaces `off`.
2. Per-target action expiry, settle window, readback requirement, promotion
   order, and whether the eventual bus-reserved worker should replace that
   legacy invocation after parity.
3. Whether lock, Roomba, or person-scoped Eight Sleep behavior should receive a
   later action promotion; the default is outcome observation only.
4. Whether safe queries show every successful vacancy action by default or
   only failures, skips, unknown outcomes, and an aggregate run summary.
5. Exact pet low/recovery thresholds, severity buckets, active aliases, and
   seasonal enable policy.
6. Whether Petlibro exposes independently verifiable missed-feed and dispense
   outcomes. If yes: exact feeder, maximum portions, meal-window deadline, and
   activation evidence; if no, automatic feeding is out of scope.
7. Exact connectivity probe targets, delayed replay window, and whether a
   DNS-only degradation should remain status-only.
8. Whether any failed/unknown action, pet, or connectivity condition may ever
   notify; the default is status/query only, successful actions are always
   silent, and recipient expansion remains separate.

## Definition of done

Future implementation is complete only when:

- Existing home-event producers remain healthy with no unexplained backlog,
  dead letter, cross-site join, duplicate evidence, or unreviewed outcome.
- Every new source defaults off and can be disabled independently.
- Existing vacancy physical behavior and markers are unchanged under all
  disabled, observation-only, and telemetry-failure tests.
- Vacancy records preserve exact presence causality and honest unknown
  outcomes.
- Every promoted action has an exact protected profile, fresh canonical-
  vacancy revalidation, bounded reservation, one-attempt boundary, independent
  readback, per-target rollback, and no possible legacy-worker overlap.
- Hue is proven first; HVAC targets are promoted individually only after their
  seasonal desired state and readback are frozen. Cabin Midea and the basement
  Cielo remain inactive until explicitly decided.
- Successful actions remain silent; failures and unknowns are safely visible
  without notification-recipient expansion becoming a promotion dependency.
- Site-local internet loss is distinct from observer or bridge failure, and
  delayed Crosstown transitions retain order.
- Pet provider failure cannot masquerade as a device failure, and only exact
  enabled aliases produce exception transitions.
- Feeder recovery is absent unless independent missed-feed and completion
  evidence exists; if activated, vacancy alone cannot feed, one meal window
  can produce at most one attempt, and an ambiguous outcome can never retry.
- Per-site component health and subject-aware incidents cannot resolve another
  source, site, component, or device's condition, and ambiguous legacy
  incidents make migration fail closed.
- No forbidden identifier, raw payload, personal pet history, probe detail, or
  command output enters protected or safe projections.
- No action path can mutate presence, capture media, invoke a model, discover a
  device, accept arbitrary arguments, or send a message.
