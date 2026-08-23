# Vacancy Automation

Automated energy-saving actions triggered when a house becomes vacant, reversed when someone returns.

## How It Works

```
Presence Detection (every 15 min)
        ↓
  state.json updated
        ↓ (WatchPaths)
  com.openclaw.vacancy-actions
        ↓
  vacancy-actions.sh evaluates occupancy
        ↓
  confirmed_vacant → run vacancy actions
  occupied + sticky arrival → clear vacancy marker
  ambiguous occupied → keep vacancy marker

Daily at 6:00 AM local
        ↓
  fresh Crosstown confirmed_vacant
        ↓
  cat activity / snooze / robot readiness gates
        ↓
  start each idle Crosstown Roomba once for that local day
```

The vacancy system piggybacks on the [presence detection](skills/presence/SKILL.md) system. When `state.json` changes, `launchd` triggers `vacancy-actions.sh`, which reads the occupancy field for each location and acts accordingly.

The runner writes a protected journal around its site-wide vacancy actions.
The future-only vacancy adapter silently baselines existing history and then
publishes completed runs into the event bus. A protected ownership policy
delegates only Crosstown `all_lights` to the separate bus action worker; every
other target and Cabin lighting retain their existing command order, retry,
marker, notification, and exit behavior.

## Trigger Conditions

| Occupancy | Meaning | Action |
|-----------|---------|--------|
| `confirmed_vacant` | All tracked people absent AND confirmed present at the other location with a fresh scan | Run vacancy actions |
| `occupied` (after vacancy) | At least one person has an unambiguous sticky assignment here, or fresh ambiguous evidence vetoes vacancy | Clear the marker only for a sticky arrival; ambiguity alone keeps it |
| `possibly_vacant` | Nobody detected but can't confirm elsewhere | No action (too uncertain) |

Ambiguity means both fresh location snapshots directly report the same person
present. It vetoes `confirmed_vacant` for safety, but does not prove a return.

## Actions by Location

### Crosstown (West Roxbury)

**On vacancy:**

| System | CLI | Action |
|--------|-----|--------|
| Hue lights | Event-bus reservation and guarded Hue worker | All lights off after fresh vacancy revalidation and exact readback |
| Nest thermostat | `nest eco crosstown on` | Eco mode |
| Cielo minisplits | `cielo off -d <unit>` | Bedroom, Office, Living Room off |
| Eight Sleep Pods | `8sleep --location cabin home <side>` | For each person confirmed at Cabin, make Cabin current; their Crosstown side becomes away |
| August lock | `august status` / `august lock` | Check status first, lock if unlocked, iMessage notification |
| Roombas | `crosstown-vacant-roomba.py --source vacancy_transition` | Route the departure run through the shared daily safety and deduplication controller |

**On return to occupied:**

| System | CLI | Action |
|--------|-----|--------|
| Eight Sleep Pods | Presence-driven `home` reconciliation | Dylan follows his detected location; Julia follows only after strict Cabin enrollment is active |

Lights, thermostat, and Cielos are NOT automatically restored — welcome-home routines handle those contextually.

While Crosstown remains vacant, `ai.openclaw.crosstown-vacant-roomba` makes a
new cleaning decision at 6:00 AM local each day. The controller requires the
canonical and protected producer presence states to have an exact hash match,
be no more than 30 minutes old, and report fresh `confirmed_vacant` occupancy.
It then:

1. honors the Crosstown Roomba Dashboard snooze;
2. suppresses cleaning after a Crosstown `Cat Detected` or `Cat Sensor
   Interrupted` event within the preceding 12 hours;
3. reads both Roombas before issuing any command and fails closed on an
   unavailable, low-battery, full-bin, errored, or non-idle robot;
4. leaves a robot already in `run` alone, starts only safely idle robots, and
   requires the guarded CLI to verify each new start reaches `run`.

Whisker history failure is a safety failure, not evidence that the cats are
absent. The controller therefore sends no Roomba command when litter-box
history cannot be validated.

### Cabin (Philly)

**On vacancy:**

| System | CLI | Action |
|--------|-----|--------|
| Hue lights | `hue --cabin all-off` | All lights off |
| Nest thermostat | `nest eco cabin on` | Eco mode |
| Eight Sleep Pods | `8sleep --location crosstown home <side>` | For each person confirmed at Crosstown, make Crosstown current; their Cabin side becomes away |
| Roombas | `roomba start floomba` / `roomba start philly` | Both Roombas start cleaning |

**On return to occupied:**

| System | CLI | Action |
|--------|-----|--------|
| Eight Sleep Pods | Presence-driven `home` reconciliation | Each returning person's detected location becomes current independently |

The general Cabin vacancy marker is also cleared; other systems are not
automatically restored.

## Deduplication

Marker files at `~/.openclaw/presence/vacancy-dispatched/` prevent duplicate triggers:

- `vacancy-dispatched/crosstown` — created after Crosstown vacancy actions run
- `vacancy-dispatched/cabin` — created after Cabin vacancy actions run
- `vacancy-dispatched/8sleep-dylan-home` — last verified current location for Dylan
- `vacancy-dispatched/8sleep-julia-home` — last verified current location for Julia

Actions only fire when `confirmed_vacant` AND no corresponding marker exists.
General vacancy markers are cleared only after at least one tracked person's
sticky location resolves to that house through an unambiguous fresh arrival.
An ambiguity-only `occupied` state preserves the marker so it cannot re-arm
physical actions when the location returns to `confirmed_vacant`.
Roomba starts also honor the per-location Roomba Dashboard snooze at
`~/.openclaw/dog-walk/snooze.json`. A snooze skips only the automatic Roomba
start; the remaining vacancy actions and general marker still proceed. An
invalid snooze policy fails closed for Roomba starts.

Crosstown Roomba decisions additionally use one protected record per local day
under `~/.openclaw/vacant-roomba/crosstown/runs/`. The vacancy-transition run
and the 6:00 AM run share this ledger, so whichever evaluates first consumes
that day's decision and the other cannot duplicate it. Intent is written
before Whisker, Roomba status, or start calls; an interrupted or uncertain run
is therefore not retried automatically that day. An occupied 6:00 AM check
does not create a record, allowing a later vacancy transition that day to run.
Every evaluation also publishes an owner-only `latest-status.json` projection
for the Roomba Dashboard. Failure to update that observational projection does
not change the durable daily decision or turn a completed physical action into
a retryable failure.

Eight Sleep is reconciled from each person's sticky `people.<name>.location`
when that location changes. This handles split households without polling the
cloud on every 15-minute state write. The per-person marker records the last
verified location only after Eight Sleep confirms the target household set,
the person's exact device/side assignment, and `away-mode = false`. A failed or
partial cloud update leaves the old marker in place so the ordinary
reconciliation path can retry. While the location is unchanged, manual Eight
Sleep app overrides are preserved; the next positive relocation re-applies
automation. Invalid or unknown locations preserve the marker and perform no
device action.

The deployed Cabin scanner now validates the protected exact schema-v2
binding, so Julia's temporary Crosstown pin is released and her Eight Sleep
side follows canonical presence. If that protected binding or scanner later
fails validation, the guard conservatively pins her back to Crosstown rather
than trusting an unvalidated Cabin relocation. Dylan's reconciliation is
unchanged.

## Observation Journal

Before journaling a vacancy run, `vacancy-action-journal.py` requires an exact
timestamp and recomputed SHA-256 match between canonical `state.json` and the
protected presence producer state, plus fresh `confirmed_vacant` state for the
site. It then records opaque vacancy-cycle, run, and per-target attempt IDs in
owner-only local files.

Outcomes distinguish independently confirmed state, an accepted command,
failure, policy skip, and an unknown result. A zero command exit is only
`command_accepted` unless an existing independent readback confirms state.
Stale unfinished attempts become `outcome_unknown` and are never retried by
the journal. A run becomes complete only after the legacy vacancy marker is
present.

Journal errors are deliberately fail-open for legacy vacancy behavior. One
sanitized warning is written, the existing commands continue, and a partial
journal is left for honest stale recovery rather than being marked complete.
The journal helper has no device, presence-mutation, camera, model, or
messaging interface. The separately site-gated adapter is the only publisher.
It ignores all pre-enable history and advances its protected cursor only after
each event reaches the source spool.

## Crosstown lighting handoff

`~/.openclaw/home-events/config/action-policy.json` is the exact ownership
boundary. With the current policy, `vacancy-actions.sh` journals Crosstown Hue
as `delegated_to_event_bus` and continues every remaining vacancy action. Cabin
Hue remains legacy-owned. Missing or inactive policy preserves legacy
ownership; an invalid protected policy fails closed for the affected Hue
target so two authorities cannot issue the same command.

The bus reserves one `all_lights` action for the exact vacancy cycle. Before
execution it requires the canonical presence file and protected producer state
to match by hash, be fresh, and still report Crosstown `confirmed_vacant`. It
reads Hue group 0 first, sends at most one `all-off` command only when needed,
and requires an all-off readback. A prior claimed attempt is terminal
`outcome_unknown` after restart and is never replayed.

The same protected policy separately delegates Crosstown
`daily_automations`. At vacancy start, the worker inventories the three exact
allowlisted Hue routines, durably records only those that were enabled, then
disables all three with per-routine readback. While the site remains confirmed
vacant, the 30-second worker also corrects a manually or externally re-enabled
routine. On a fresh `occupied` state that places at least one sticky resident at
Crosstown, it restores only the routines recorded as enabled at the start of
that vacancy cycle. Previously disabled routines therefore remain disabled.
Stale, uncertain, malformed, or ambiguous presence defers restoration.

The owner-only suspension record is
`~/.openclaw/home-events/state/hue-automation-suspensions.json`. It contains
only exact routine names, lifecycle state, counts, and timestamps; Hue resource
IDs and credentials are never persisted there.

## Files

| Path | Purpose |
|------|---------|
| `~/.openclaw/workspace/scripts/vacancy-actions.sh` | Main script |
| `~/.openclaw/bin/vacancy-action-journal.py` | Observation-only protected action journal helper |
| `~/.openclaw/bin/vacancy-event-adapter.py` | Future-only journal-to-bus adapter |
| `~/.openclaw/bin/home-event-action` | Exact ownership, reservation, status, and canary interface |
| `~/.openclaw/home-events/config/action-policy.json` | Protected per-site target ownership policy |
| `~/.openclaw/bin/crosstown-vacant-roomba.py` | Shared departure/daily Crosstown Roomba controller |
| `~/.openclaw/vacancy-actions/journal/` | Owner-only bounded run and vacancy-cycle records |
| `~/.openclaw/vacant-roomba/crosstown/runs/` | Owner-only per-local-day cleaning decisions |
| `~/.openclaw/vacant-roomba/crosstown/latest-status.json` | Owner-only latest evaluation projection for the Roomba Dashboard |
| `~/.openclaw/presence/state.json` | Input: occupancy state (from presence detection) |
| `~/.openclaw/presence/vacancy-dispatched/` | Marker files for dedup |
| `~/.openclaw/logs/vacancy-actions.log` | Execution log |
| `~/.openclaw/logs/crosstown-vacant-roomba.log` | Bounded daily-controller log |

## LaunchAgent

| Label | Trigger | Host |
|-------|---------|------|
| `com.openclaw.vacancy-actions` | WatchPaths on `state.json` | Mac Mini |
| `ai.openclaw.crosstown-vacant-roomba` | Daily at 6:00 AM local | Mac Mini |
| `ai.openclaw.vacancy-event-adapter` | Every 60 seconds | Mac Mini |
| `ai.openclaw.home-event-action` | Every 30 seconds | Mac Mini |

## Debugging

Check the log:
```bash
tail -50 ~/.openclaw/logs/vacancy-actions.log
tail -50 ~/.openclaw/logs/crosstown-vacant-roomba.log
```

Check current occupancy:
```bash
cat ~/.openclaw/presence/state.json | python3 -m json.tool
```

Check marker state:
```bash
ls -la ~/.openclaw/presence/vacancy-dispatched/
```

Check safe aggregate journal status:
```bash
~/.openclaw/bin/vacancy-action-journal.py status
~/.openclaw/bin/home-event-action status
```

Do not delete vacancy markers, touch `state.json`, or run a live presence scan
as a test. Clearing a marker re-arms every physical action for that location on
the next `confirmed_vacant` evaluation. Use the isolated tests instead:
`bash openclaw/tests/test-presence-detect.sh`,
`bash openclaw/tests/test-presence-receive.sh`, and
`bash openclaw/tests/test-vacancy-actions.sh`. The daily controller has separate
fake-only coverage in
`python3 -m unittest openclaw.tests.test_crosstown_vacant_roomba`.
