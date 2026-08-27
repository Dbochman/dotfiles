# Cat Feeder Vacancy Automation

## Status

Implemented on `2026-08-26` through rollout stage 1. Schema 7, the Whisker
observer, paired-home transfer correlation, exact Petlibro actions, and guarded
automatic resume are tracked and tested. Both installed Whisker site flags and
both exact `feeding_schedule` policy modes remain disabled, so this deployment
cannot poll Litter-Robot or mutate a feeder. Later activation must move through
the rollout gates below.

This plan extends the existing home event bus and vacancy-action control plane.
It does not create a second source of occupancy truth.

## Goal

When the cats move with the household from one home to the other:

1. use a fresh litter-box visit at the occupied home as bounded evidence of the
   cats' destination;
2. ensure scheduled feeding is active at that destination;
3. disable the complete saved feeding schedule at the confirmed-vacant home;
4. automatically restore a schedule later only when this automation previously
   disabled it; and
5. fail toward two enabled feeders, never toward two disabled feeders.

The action changes schedule enablement only. It never dispenses food, changes
meal times or portions, deletes a meal, or controls a fountain.

## Non-goals

- Litter-Robot activity does not set, clear, or override canonical presence.
- A visit does not identify an individual cat or prove that every cat moved.
- Weight, pet profile, serial number, raw activity history, and feeding history
  are not stored in the event bus.
- This does not add missed-feed recovery or any automatic manual dispense.
- This does not infer a move from an offline device, an empty history response,
  a feeder's next-meal time, or human presence alone.
- The dashboard remains an attended manual control surface. It does not own
  automatic feeder state.

## Existing control-plane alignment

The implementation must preserve the deployed flow:

```text
canonical presence
  -> protected vacancy journal
  -> vacancy event adapter
  -> home event bus
  -> correlator and exact action reservation
  -> one-attempt action worker
```

Whisker supplies an additional observation to the correlator; it does not run
its own vacancy loop. Petlibro mutation belongs in the existing exact action
worker and policy, not in the observer, dashboard, or presence scanner.

The Hue daily-automation suspension record is the behavioral model for feeder
resume ownership: record what automation changed, continuously reconcile only
that owned change while its gate remains valid, and restore only the state that
automation owns.

## Exact bindings

These are compile-time bindings, not discovery candidates.

| Site | Bus litter-box alias | Litter-Robot CLI selector | Petlibro feeder selector |
|------|----------------------|---------------------------|---------------------------|
| Crosstown | `crosstown_litter_robot` | `crosstown-litter-robot` | `crosstown-feeder` |
| Cabin | `cabin_litter_robot` | `cabin-litter-robot` | `cabin-feeder` |

The bus aliases use its safe-name format; adapter configuration maps them to
the already enrolled CLI selectors. The adapter must reject an observation if
the account returns zero, more than one, or a differently bound robot for a
selector. The action worker must reject a policy whose selector differs from
the compiled site binding.

The exact action target is `feeding_schedule`, separately policy-owned at each
site. No fountain, manual-feed, or wildcard device selector is accepted.

## Whisker observation contract

### Read-only CLI contract

Extend the tracked Litter-Robot integration with a dedicated, read-only
machine contract rather than scraping dashboard-oriented output:

```text
litter-robot --json observe 100
```

One command must open one account session and return both exact enrolled robots
with at most 100 newest history entries per robot. Its sanitized response is:

```json
{
  "ok": true,
  "observedAt": "2026-08-26T22:55:00Z",
  "robots": [
    {
      "selector": "cabin-litter-robot",
      "site": "cabin",
      "historyExhausted": false,
      "activities": [
        {
          "occurredAt": "2026-08-26T09:15:39Z",
          "classification": "cat_detected"
        }
      ]
    }
  ]
}
```

Required response rules:

- `robots` contains exactly the two compiled selectors, once each.
- `site` is derived locally from the selector, never trusted from provider
  metadata.
- `observedAt` and every `occurredAt` are strict UTC RFC 3339 timestamps.
- `historyExhausted` reflects only an explicit provider end-of-history signal.
  The adapter, which owns the prior committed overlap anchor, determines
  continuity by finding that anchor in the returned page or by observing
  provider-reported exhaustion.
- Activities are sorted newest first and bounded to the request limit.
- The response omits device IDs, serials, names, pet profiles, weights, raw
  action strings, raw provider payloads, and account data.
- A partial account response fails the entire observation. The adapter must not
  treat the surviving site's data as safe cross-site evidence.

Only two exact normalized provider actions qualify:

| Case-folded, trimmed provider action | Classification |
|--------------------------------------|----------------|
| `cat detected` | `cat_detected` |
| `cat sensor interrupted` | `cat_sensor_interrupted` |

All other actions are ignored. Substring matching is forbidden. In particular,
cycle completion, drawer state, reset, clean, power, weight, and generic status
events cannot become cat-location evidence.

### Adapter

Add `openclaw/bin/whisker-event-adapter.py`, invoked as:

```text
home-event-service-wrapper.sh whisker
```

Deploy it with a dedicated LaunchAgent and these site gates, both defaulting to
off in tracked configuration:

```text
HOME_EVENTS_WHISKER_CABIN_ENABLED=0
HOME_EVENTS_WHISKER_CROSSTOWN_ENABLED=0
```

Feeder actions require both gates to be enabled and healthy. A single enabled
site is useful for shadow observation but never sufficient for mutation.

The adapter must follow the vacancy adapter's protected lock, atomic write,
future-only baseline, and publish-before-cursor-advance rules. Its protected
state is:

```text
~/.openclaw/home-events/state/whisker-adapter.json
~/.openclaw/home-events/state/whisker-adapter.lock
```

The state file is owner-only and contains only schema version, per-site source
health, last successful poll, prior overlap anchor, baseline time, and a
bounded set of safe activity fingerprints. It contains no raw history.

Because Whisker history does not expose a stable provider event ID, derive a
local source-event fingerprint from this canonical tuple:

```text
(site, occurred_at, classification)
```

Persist only its SHA-256 digest. The bus source event ID is the digest with a
version prefix. Duplicate and reordered history must converge on one event.

On first enablement, the adapter silently baselines the returned history. It
publishes no historical activity and creates no action candidate. Later polls
must overlap the prior committed anchor. If a full page no longer contains the
anchor, mark both Whisker sites `unavailable` with reason `history_gap`, publish
no new activity from that poll, and require a new attended baseline. Absence of
history is never evidence that a home has no cats.

### Normalized bus event

Schema 7 adds `whisker` as a strict source with one accepted event:

```json
{
  "source": "whisker",
  "event_type": "pet.litter_box_activity",
  "site": "crosstown",
  "entity_kind": "litter_box",
  "entity_alias": "crosstown_litter_robot",
  "occurred_at": "2026-08-26T21:08:24Z",
  "observed_at": "2026-08-26T21:09:00Z",
  "time_precision": "source",
  "source_event_id": "v1:<sha256>",
  "attributes": {
    "classification": "cat_detected"
  }
}
```

The bus accepts only the exact site/alias bindings and the two classification
values above. This event is correlation context: it never opens a security
incident, requests camera evidence, sends a notification, or changes presence.

Schema 7 migration must rebuild every source-constrained table transactionally,
preserve row IDs and foreign keys, add safe Whisker component health, and
refuse downgrade. The source remains inactive until its LaunchAgent and both
site flags are explicitly enabled.

## Transfer evidence

For a candidate destination site `D`, the origin `O` is the other exact site.
A candidate is eligible only when all of the following are true:

1. `O` is canonically `confirmed_vacant` and has a current protected vacancy
   cycle.
2. `D` is canonically occupied and has at least one sticky resident.
3. The candidate litter event occurred at `D` after the start of `O`'s current
   vacancy cycle.
4. Both sites' Whisker histories have uninterrupted coverage beginning no later
   than that vacancy-cycle start and their latest successful poll is at most
   five minutes old.
5. No qualifying litter activity occurred at `O` after the vacancy-cycle start.
6. The first qualifying event at `D` has settled for 30 minutes without an
   origin-site event, occupancy reversal, source-health loss, or vacancy-cycle
   change.
7. Canonical presence and the protected vacancy marker are reread immediately
   before each mutation and remain mutually consistent.

The 30-minute window is deliberate: one litter event proves activity by at
least one cat, not that all cats moved. Household vacancy plus destination
activity is the bounded operational signal; the system must never describe it
as individual-cat identification.

An origin litter event after vacancy start blocks the transfer indefinitely for
that vacancy cycle unless a later implementation introduces an explicit,
attended resolution. It is safer to leave both schedules enabled than to guess
that the event was stale or belonged to a cat that later moved.

`possibly_vacant`, stale canonical state, unknown occupancy, absent sticky
resident, a missing protected marker, an adapter gap, or unavailable provider
data blocks the action. None can be weakened by dashboard state or a model's
interpretation.

## Action policy and reservation

Advance the exact action policy to schema 3 and add this shape for each site:

```json
{
  "cabin": {
    "feeding_schedule": {
      "owner": "bus",
      "mode": "active",
      "trigger": "cat_transfer",
      "action": "suspend_restore",
      "selector": "cabin-feeder",
      "destination_site": "crosstown",
      "destination_selector": "crosstown-feeder",
      "desired_state": "vacant_disabled",
      "evidence_settle_seconds": 1800,
      "expiry_seconds": 600,
      "settle_seconds": 3
    }
  },
  "crosstown": {
    "feeding_schedule": {
      "owner": "bus",
      "mode": "active",
      "trigger": "cat_transfer",
      "action": "suspend_restore",
      "selector": "crosstown-feeder",
      "destination_site": "cabin",
      "destination_selector": "cabin-feeder",
      "desired_state": "vacant_disabled",
      "evidence_settle_seconds": 1800,
      "expiry_seconds": 600,
      "settle_seconds": 3
    }
  }
}
```

Schema 3 adds a required per-target mode of `disabled`, `shadow`, or `active`;
the sample shows the final active state. The policy loader requires exact
equality between policy selectors and compiled bindings. It rejects unknown
fields, duplicate selectors, same-site destination bindings, non-reciprocal
pairs, wildcard aliases, and values outside the fixed bounds.

The existing vacancy-event reservation path must skip targets whose trigger is
`cat_transfer`. The correlator creates `feeding_schedule` only after the
settled Whisker candidate satisfies every evidence gate. Shadow mode records a
bounded safe `would_reserve` explanation and creates no action reservation.
The active reservation key remains:

```text
(origin_site, feeding_schedule, vacancy_cycle_id)
```

That permits at most one feeder transfer action per home and vacancy cycle.
The reservation stores the exact triggering Whisker event, vacancy-cycle ID,
canonical state hash, source-coverage hash, and policy hash. Policy or state
changes before claim cancel it. A reservation expires after ten minutes.

## Petlibro read and mutation contract

Add a sanitized exact read:

```text
petlibro --json schedule-state <crosstown-feeder|cabin-feeder>
```

It returns only:

```json
{
  "ok": true,
  "selector": "crosstown-feeder",
  "site": "crosstown",
  "online": true,
  "scheduleEnabled": true,
  "enabledMealCount": 3,
  "observedAt": "2026-08-26T22:55:00Z"
}
```

`site` is derived from the exact local selector. `enabledMealCount` is the
count of enabled saved meals; no meal time, portion, name, ID, or raw schedule
is returned. A null or unverified field makes the observation unavailable.

The only mutation commands reachable from the action worker are:

```text
petlibro --json schedule-set crosstown-feeder on|off
petlibro --json schedule-set cabin-feeder on|off
```

The existing Petlibro helper already provides a protected lock, pre-state
read, at most one provider mutation per invocation, protected audit, and fresh
readback. The worker must preserve those semantics and treat
`outcome_unknown` as terminal and non-retryable. It must not import or invoke
the manual `feed` command.

## Owned suspension and automatic resume

Use an owner-only, atomic state file:

```text
~/.openclaw/home-events/state/feeder-schedule-suspensions.json
```

An active per-site record contains only safe fields: schema, site, exact
selector, vacancy-cycle ID, policy hash, phase, whether restore is owned,
suspension time, last reconciliation time, and bounded status/error enums.

Before changing an enabled origin schedule to off, write an intent record with
`phase=suspending` and `restore_owned=true`. After verified readback, change it
to `phase=suspended`. This write-before-mutate ordering makes a crash
recoverable without guessing ownership.

If the origin schedule is already disabled and no active owned record exists,
the action completes as `already_satisfied_manual`. It does **not** create a
restorable record. OpenClaw must never enable that schedule automatically.

While the original vacancy cycle and cat-transfer evidence remain valid, the
worker reconciles an owned suspension back to off if the provider reports it
enabled. This is equivalent to the existing Hue daily-automation ownership
behavior. An operator can stop reconciliation by moving the action policy out
of `bus` ownership; simply toggling the feeder on is not a durable override
while the owning vacancy cycle is still active.

Automatic resume is authorized only when:

1. the destination site has an active `restore_owned=true` record created by a
   previously verified automation disable;
2. that destination is now canonically occupied with a sticky resident;
3. a qualifying destination litter event occurred after the other home's
   current vacancy cycle started;
4. both Whisker histories and canonical state meet the same freshness and gap
   requirements; and
5. the exact destination feeder is online with a readable saved schedule.

If no owned record exists, OpenClaw cannot resume the destination schedule even
when it appears disabled. A manual pause stays paused.

On successful verified resume, retain a bounded terminal audit record and
remove the active suspension. On known failure, leave the record active and
report the reason. On unknown outcome, stop without retry, reread on the next
worker pass, and resolve only if readback proves the requested state; otherwise
require attended repair.

## Safe transfer order

The action worker executes one transfer under its global feeder lock:

1. Reread canonical presence, both source-health records, the vacancy marker,
   policy hash, and the exact reservation.
2. Read destination and origin schedule state.
3. If the destination has an owned suspension, resume it and verify
   `scheduleEnabled=true` and `enabledMealCount>=1`.
4. If the destination is disabled without an owned suspension, stop with
   `destination_schedule_manually_disabled`.
5. If the destination is unavailable, offline, has no enabled saved meal, or
   has an unknown resume outcome, stop before touching the origin.
6. Revalidate every evidence gate after the destination read or mutation.
7. Create the origin owned-suspension intent, disable the exact origin
   schedule once, and verify `scheduleEnabled=false`.
8. Record the exact, sanitized outcome in the existing reservation/outcome
   tables and protected suspension state.

This order intentionally allows both feeders to remain enabled if the origin
disable fails. It must never disable the origin first, because a destination
resume failure could leave both homes without scheduled feeding.

## Reconciliation after partial outcomes

| Observed state | Required behavior |
|----------------|-------------------|
| Destination verified on; origin verified off | Complete transfer; maintain origin owned suspension |
| Destination already on; origin disable known failed | Leave destination on; record failed; do not retry reservation |
| Destination resume unknown | Do not touch origin; mark outcome unknown until attended/readback resolution |
| Origin disable unknown | Keep destination on; mark outcome unknown; never repeat disable blindly |
| Origin manually off before action | Do not claim ownership; do not enable it on a later move |
| Owned origin schedule externally re-enabled during same valid vacancy | Reconcile it off after full gate revalidation |
| Presence changes before origin disable | Cancel reservation; leave both current schedules unchanged |
| Whisker coverage gaps after suspension | Preserve current feeder states; surface degraded health; do not guess a move |

## Health and safe status

Home-event status schema 7 should add a sanitized `whisker` source summary per
site:

- enabled/baselined/healthy state;
- last successful poll age;
- coverage start age;
- `ok`, `disabled`, `baseline_required`, `provider_unavailable`,
  `history_gap`, or `invalid_observation`; and
- accepted/quarantined counts.

It should also expose one aggregate feeder automation summary per site:

- policy owner and mode;
- `not_owned`, `suspending`, `suspended`, `restoring`, `attention`, or
  `inactive`;
- exact safe selector;
- current vacancy-cycle match; and
- last verified schedule state and age.

Do not expose activity timestamps, weights, pet identity, meal configuration,
provider IDs, raw errors, or account data through general status. Detailed
protected audits remain local and operator-only.

## Current-state bootstrap

At specification time, canonical state has Cabin confirmed vacant and
Crosstown occupied. A Crosstown litter event occurred after the Cabin vacancy
cycle began. Cabin's feeder schedule is enabled, but Crosstown's schedule is
disabled and there is no feeder suspension record proving OpenClaw owns that
pause.

The designed behavior is therefore to take no automatic mutation: it may not
enable Crosstown's manually disabled schedule, and it may not disable Cabin
before the destination is verified feeding. Before a production canary, an
operator must manually enable and verify `crosstown-feeder`, or separately
authorize a one-time bootstrap adoption flow. Manual enablement is the preferred
path; the implementation must not silently manufacture ownership from current
state.

Historical litter activity present at adapter activation is baseline data and
cannot trigger this action. A production transfer requires future-only source
coverage and an organic qualifying event after baseline.

## Implementation work packages

### 1. Read-only device contracts

- Add the strict two-robot `observe` command and sanitized fixtures.
- Add exact Petlibro `schedule-state` with enabled-meal count.
- Keep both commands independent of dashboard rendering.
- Document the new read-only commands in their skills and `TOOLS.md`.

Exit gate: malformed, partial, duplicated, renamed, or unmapped device data
fails closed without revealing provider identifiers.

### 2. Whisker adapter and schema 7

- Add `whisker-event-adapter.py`, wrapper route, disabled LaunchAgent, protected
  cursor/lock, and per-site flags.
- Add the strict source/event/entity/alias/attribute bindings to the bus.
- Add transactional schema migration, component health, safe status, and
  downgrade refusal.

Exit gate: first activation baselines silently; duplicate/reordered activity
does not republish; a history gap disables action eligibility at both sites.

### 3. Correlation and policy schema 3

- Add the exact `cat_transfer` policy form and validators.
- Build settled candidates only from future Whisker events.
- Require the current canonical vacancy cycle, sticky destination resident,
  paired source coverage, no origin activity, and full freshness checks.
- Reserve once per origin target and vacancy cycle.

Exit gate: shadow explanations agree with hand-reviewed organic moves and no
ordinary vacancy event reserves a feeder action by itself.

### 4. Feeder worker and owned suspension

- Add exact Petlibro action targets to the existing worker.
- Add protected suspension intent, verified disable, guarded automatic resume,
  and continuous owned-state reconciliation.
- Preserve the one-attempt and unknown-outcome boundaries.
- Show guarded state and attended controls on the cat dashboard without making
  the dashboard an automation owner.

Exit gate: injected crash tests cannot double-mutate, resume a manual pause,
or disable an origin whose destination is not verifiably scheduled.

### 5. Rollout

1. Deploy schema and code with Whisker flags off, bus ownership declared, and
   both feeder policy modes set to `disabled`.
2. Enable both Whisker observers in future-only baseline mode. Confirm paired
   coverage, bounded cursor growth, no private data, and no action reservation.
3. Run `cat_transfer` in shadow mode across organic household moves. Require at
   least two reviewed moves in each direction, including one origin-side litter
   event that correctly blocks action.
4. Manually place both feeder schedules in their intended starting state;
   never create a canary by skipping a meal or disabling the destination.
5. Activate only Cabin `feeding_schedule` ownership first, so cats arriving at
   Crosstown can suspend Cabin. Verify the next scheduled meal at Crosstown
   normally rather than issuing a test dispense.
6. Observe one organic return and verify the owned Cabin suspension is restored
   before Crosstown is considered for disable.
7. Activate Crosstown only after the first site proves suspend, maintain, and
   automatic-resume behavior with verified readback.

No rollout stage creates synthetic production litter events or manual feed
commands.

## Rollback

Set both `feeding_schedule` policy modes to `disabled` and unload only the
Whisker adapter LaunchAgent. Do not delete the canonical vacancy marker,
source cursor, reservation history, or suspension state.

Rollback freezes current feeder states; it does not guess whether to enable or
disable either schedule. An active owned suspension is shown to the operator
with the exact selector and last verified state. Restoring it during rollback
is an attended, explicit `schedule-set <selector> on` action followed by
readback and closure of the owned record.

The observer, action target, and each site policy remain independently
disableable. Disabling feeder action must not affect presence, vacancy actions,
the cat dashboard, or existing litter-box/Roomba behavior.

## Required tests

### Device and adapter contracts

- Exact two-selector binding; zero, duplicate, extra, or renamed robots fail.
- Only exact `cat detected` and `cat sensor interrupted` actions normalize.
- First run baselines silently; duplicate, reordered, and delayed events
  converge on one event.
- Publisher failure does not advance the cursor.
- A full page without the committed overlap anchor produces `history_gap` and
  no event.
- Partial site failure blocks both-site action coverage.
- Cursor/lock permissions, atomic replacement, corrupt-state quarantine, and
  bounded fingerprints are enforced.
- Normalized payloads and logs contain no raw action, serial, weight, pet,
  account, meal, or provider identifiers.

### Bus and correlation

- Schema 7 migration preserves events, incidents, deliveries, reservations,
  outcomes, IDs, and foreign keys; schema 6 binaries refuse schema 7.
- Invalid source/event/entity/alias/classification combinations quarantine.
- A destination event before the origin vacancy-cycle start is ignored.
- A destination event after vacancy plus 30 quiet minutes becomes eligible
  only with fresh paired coverage and occupied/sticky destination state.
- Any origin event after vacancy start blocks the cycle.
- Stale, unknown, or `possibly_vacant` presence blocks the candidate.
- Original vacancy publication cannot reserve `feeding_schedule`.
- Repeated destination events create one reservation for the vacancy cycle.
- Policy, state, source-health, or vacancy-cycle changes cancel pending work.

Include the current scenario as a regression fixture: Cabin litter activity
before Cabin vacancy does not veto; later Crosstown litter activity can create
a settled candidate; a manually disabled Crosstown destination still blocks
the Cabin mutation.

### Action and recovery

- Policy selectors must exactly match compiled site bindings.
- Destination resume and verification always precede origin disable.
- Destination offline, unknown, disabled without ownership, or lacking an
  enabled saved meal leaves the origin untouched.
- An enabled origin creates intent before disable and becomes owned only after
  verified readback.
- An already manually disabled origin never creates resume ownership.
- Automatic resume operates only on an owned record and does not edit meals.
- External re-enable of an owned vacant-site schedule is reconciled off only
  after all gates revalidate.
- Claimed-worker crash and unknown outcomes never repeat a mutation blindly.
- No action path can call `petlibro feed`, a fountain command, or a
  Litter-Robot mutation.

## Acceptance criteria

- Canonical presence remains the sole occupancy authority.
- Both exact litter-box histories are future-only, continuous, fresh, and
  privacy-bounded before any feeder decision.
- The exact destination schedule is on with at least one enabled saved meal
  before the exact origin schedule can be disabled.
- At most one origin schedule action is reserved per vacancy cycle.
- A manual schedule pause is never automatically resumed.
- A schedule disabled by OpenClaw is automatically restored and verified on a
  later qualifying return before the newly vacant feeder is disabled.
- Every mutation is protected, audited, exact-selector-only, one-attempt, and
  confirmed by fresh readback or honestly marked unknown.
- Any ambiguity leaves food availability unchanged or results in both feeders
  enabled; the automation cannot intentionally produce two disabled feeders.
