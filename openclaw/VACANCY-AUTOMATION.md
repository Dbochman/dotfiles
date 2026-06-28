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
  occupied (was vacant) → run restore actions
```

The vacancy system piggybacks on the [presence detection](skills/presence/SKILL.md) system. When `state.json` changes, `launchd` triggers `vacancy-actions.sh`, which reads the occupancy field for each location and acts accordingly.

## Trigger Conditions

| Occupancy | Meaning | Action |
|-----------|---------|--------|
| `confirmed_vacant` | All tracked people absent AND confirmed present at the other location with a fresh scan | Run vacancy actions |
| `occupied` (after vacancy) | At least one person detected again | Run restore actions, clear marker |
| `possibly_vacant` | Nobody detected but can't confirm elsewhere | No action (too uncertain) |

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
| Eight Sleep Pods | Presence-driven `home` reconciliation | Each returning person's detected location becomes current independently |

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
Eight Sleep is reconciled from each person's sticky `people.<name>.location`
when that location changes. This handles split households without polling the
cloud on every 15-minute state write. The per-person marker records the last
verified location. While the location is unchanged, manual Eight Sleep app
overrides are preserved; the next positive relocation re-applies automation.
Invalid or unknown locations preserve the marker and perform no device action.

## Files

| Path | Purpose |
|------|---------|
| `~/.openclaw/workspace/scripts/vacancy-actions.sh` | Main script |
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

Do not delete vacancy markers, touch `state.json`, or run a live presence scan
as a test. Clearing a marker re-arms every physical action for that location on
the next `confirmed_vacant` evaluation. Use the isolated tests instead:
`bash openclaw/tests/test-presence-receive.sh` and
`bash openclaw/tests/test-vacancy-actions.sh`.
