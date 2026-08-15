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
```

The vacancy system piggybacks on the [presence detection](skills/presence/SKILL.md) system. When `state.json` changes, `launchd` triggers `vacancy-actions.sh`, which reads the occupancy field for each location and acts accordingly.

The runner also writes an observation-only protected journal around its
existing site-wide vacancy actions. This records intent and a bounded terminal
classification without changing a device command, its ordering, retry
behavior, marker semantics, notification, or exit status. The journal is not
yet an event-bus producer or action authority.

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
| Hue lights | `hue --crosstown all-off` | All lights off |
| Nest thermostat | `nest eco crosstown on` | Eco mode |
| Cielo minisplits | `cielo off -d <unit>` | Bedroom, Office, Living Room off |
| Eight Sleep Pods | `8sleep --location cabin home <side>` | For each person confirmed at Cabin, make Cabin current; their Crosstown side becomes away |
| August lock | `august status` / `august lock` | Check status first, lock if unlocked, iMessage notification |
| Roombas | `crosstown-roomba start all` | Combo 10 Max + Roomba J5 start cleaning |

**On return to occupied:**

| System | CLI | Action |
|--------|-----|--------|
| Eight Sleep Pods | Presence-driven `home` reconciliation | Dylan follows his detected location; Julia follows only after strict Cabin enrollment is active |

Lights, thermostat, and Cielos are NOT automatically restored — welcome-home routines handle those contextually.

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
The helper has no device, presence-mutation, event-bus, camera, model, or
messaging interface.

## Files

| Path | Purpose |
|------|---------|
| `~/.openclaw/workspace/scripts/vacancy-actions.sh` | Main script |
| `~/.openclaw/bin/vacancy-action-journal.py` | Observation-only protected action journal helper |
| `~/.openclaw/vacancy-actions/journal/` | Owner-only bounded run and vacancy-cycle records |
| `~/.openclaw/presence/state.json` | Input: occupancy state (from presence detection) |
| `~/.openclaw/presence/vacancy-dispatched/` | Marker files for dedup |
| `~/.openclaw/logs/vacancy-actions.log` | Execution log |

## LaunchAgent

| Label | Trigger | Host |
|-------|---------|------|
| `com.openclaw.vacancy-actions` | WatchPaths on `state.json` | Mac Mini |

## Debugging

Check the log:
```bash
tail -50 ~/.openclaw/logs/vacancy-actions.log
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
```

Do not delete vacancy markers, touch `state.json`, or run a live presence scan
as a test. Clearing a marker re-arms every physical action for that location on
the next `confirmed_vacant` evaluation. Use the isolated tests instead:
`bash openclaw/tests/test-presence-detect.sh`,
`bash openclaw/tests/test-presence-receive.sh`, and
`bash openclaw/tests/test-vacancy-actions.sh`.
