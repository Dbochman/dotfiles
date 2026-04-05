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
| `confirmed_vacant` | All tracked people absent AND confirmed present at other location | Run vacancy actions |
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
| Eight Sleep Pod | `8sleep off <side>` | Both sides off (stops thermal unit) |
| August lock | `august status` / `august lock` | Check status first, lock if unlocked, iMessage notification |
| Roombas | `crosstown-roomba start all` | Combo 10 Max + Roomba J5 start cleaning |

**On return to occupied:**

| System | CLI | Action |
|--------|-----|--------|
| Eight Sleep Pod | `8sleep on <side>` | Both sides resume smart schedule |

Lights, thermostat, and Cielos are NOT automatically restored — welcome-home routines handle those contextually.

### Cabin (Philly)

**On vacancy:**

| System | CLI | Action |
|--------|-----|--------|
| Hue lights | `hue --cabin all-off` | All lights off |
| Nest thermostat | `nest eco cabin on` | Eco mode |
| Roombas | `roomba start floomba` / `roomba start philly` | Both Roombas start cleaning |

**On return to occupied:**

No automated restore actions. Vacancy marker cleared.

## Deduplication

Marker files at `~/.openclaw/presence/vacancy-dispatched/` prevent duplicate triggers:

- `vacancy-dispatched/crosstown` — created after Crosstown vacancy actions run
- `vacancy-dispatched/cabin` — created after Cabin vacancy actions run

Actions only fire when `confirmed_vacant` AND no marker exists. Markers are deleted when occupancy returns to `occupied`.

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

Force re-evaluation (clears markers so next vacancy triggers actions again):
```bash
rm -f ~/.openclaw/presence/vacancy-dispatched/{crosstown,cabin}
```
