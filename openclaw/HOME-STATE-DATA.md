# Home State Data — Implementation Spec

## Overview

Centralized state tracking across Crosstown and Cabin locations. Data is collected from multiple IoT devices and services, stored as daily JSONL history files for trend analysis, and exposed via `current.json` for real-time queries.

## Data Sources

### Daily Snapshot (once per day, 9 AM ET)

Collected by `home-state-snapshot.py` via LaunchAgent `ai.openclaw.home-state-snapshot`.

| Source | Data | CLI | Update Frequency |
|--------|------|-----|-----------------|
| Litter-Robot 4 | Cat weights (per cat, per cycle) | `litter-robot pets` | Each litter box visit |
| Eight Sleep Pod 3 | Sleep scores, duration, stages, HRV, HR, RR, snoring | `8sleep sleep <side>` | Previous night |
| Ring Doorbell | Battery level per doorbell | `ring status` | On-demand |

### Event-Driven (Ring Listener)

Collected by `ring-listener.py`, written on each dog walk event.

| Source | Data | Trigger |
|--------|------|---------|
| Ring + Haiku Vision | People/dog counts, scene descriptions | Each person-detected motion event |
| FindMy + Haiku Vision | Street location, near_home status | Every 5 min during active dog walk |
| Roomba automation | Start/dock times, triggers | Departure/return events |

### Existing (not part of this system)

| Source | Data | Location |
|--------|------|----------|
| Nest/Cielo/Mysa | Temperature, humidity, HVAC state | `~/.openclaw/nest-history/` |
| Presence detection | Person locations, occupancy | `~/.openclaw/presence/` |
| Usage tracking | API token consumption | `~/.openclaw/usage-history/` |

## File Layout

```
~/.openclaw/
├── home-state/                          # Daily snapshot data
│   ├── current.json                     # Latest snapshot (overwritten)
│   └── YYYY-MM-DD.jsonl                 # Daily history (appended)
│
├── ring-listener/                       # Event-driven data
│   ├── state.json                       # Current dog walk + Roomba state
│   └── history/
│       └── YYYY-MM-DD.jsonl             # Dog walk event history
│
├── nest-history/                        # Climate data (existing)
│   └── YYYY-MM-DD.jsonl
│
├── presence/                            # Presence data (existing)
│   ├── state.json
│   └── history/
│       └── YYYY-MM-DD.jsonl
│
└── usage-history/                       # API usage (existing)
    └── YYYY-MM-DD.jsonl
```

## Schemas

### Daily Snapshot (`home-state/current.json`)

```json
{
  "timestamp": "2026-03-25T13:00:00Z",
  "type": "daily_snapshot",
  "cats": [
    {
      "name": "Sopaipilla",
      "weight_lbs": 10.09,
      "gender": "female",
      "last_weighed": "2026-03-24T21:27:23+00:00"
    },
    {
      "name": "Burrito",
      "weight_lbs": 12.07,
      "gender": "female",
      "last_weighed": "2026-03-23T13:08:18+00:00"
    }
  ],
  "sleep": {
    "dylan": {
      "date": "2026-03-24",
      "score": 77,
      "duration_min": 416,
      "rem_pct": 21,
      "deep_pct": 10,
      "light_pct": null,
      "awake_pct": null,
      "snoring_min": 2,
      "tosses_turns": null,
      "time_in_bed": null,
      "bed_temp_avg": null,
      "hrv_avg": null,
      "hr_avg": null,
      "rr_avg": null
    },
    "julia": {
      "date": "2026-03-24",
      "score": 60,
      "duration_min": 284,
      "rem_pct": 17,
      "deep_pct": 33,
      "snoring_min": 8
    }
  },
  "doorbell_battery": [
    {
      "name": "Front Door",
      "id": 684794187,
      "battery_pct": 70,
      "location": "crosstown"
    },
    {
      "name": "Front Door",
      "id": 697442349,
      "battery_pct": 78,
      "location": "cabin"
    }
  ]
}
```

### Ring Listener State (`ring-listener/state.json`)

```json
{
  "timestamp": "2026-03-24T20:45:54Z",
  "dog_walk": {
    "active": true,
    "location": "crosstown",
    "departed_at": "2026-03-24T20:45:54Z",
    "returned_at": null,
    "people": 2,
    "dogs": 2
  },
  "roombas": {
    "crosstown": {
      "status": "running",
      "started_at": "2026-03-24T20:45:55Z",
      "docked_at": null,
      "trigger": "dog_walk_departure"
    }
  },
  "findmy_polling": {
    "active": true,
    "location": "crosstown",
    "started_at": "2026-03-24T20:46:00Z",
    "polls": 3,
    "last_poll_at": "2026-03-24T21:01:00Z",
    "last_result": {
      "street": "Washington St",
      "near_home": false,
      "description": "Pin is on Washington St, about 0.5 miles from Crosstown Ave"
    }
  },
  "last_vision": {
    "event_id": 7620926177557946699,
    "description": "Two people with two dogs walking away from the front door",
    "people": 2,
    "dogs": 2,
    "people_list": ["unknown", "unknown"],
    "dogs_list": ["Potato", "Coconut"],
    "analyzed_at": "2026-03-24T20:46:32Z"
  }
}
```

## LaunchAgent

**Label:** `ai.openclaw.home-state-snapshot`
**Schedule:** Once daily at 9 AM ET
**Script:** `~/.openclaw/bin/home-state-snapshot.py`

```xml
<key>Label</key>        ai.openclaw.home-state-snapshot
<key>StartCalendarInterval</key>
  <key>Hour</key>    9
  <key>Minute</key>  0
<key>ProgramArguments</key>  /bin/bash home-state-wrapper.sh
<key>StandardOutPath</key>   ~/.openclaw/logs/home-state-snapshot.log
<key>StandardErrorPath</key> ~/.openclaw/logs/home-state-snapshot.log
```

The wrapper script sources `~/.openclaw/.secrets-cache` for API credentials before running the Python snapshot script.

## Data Retention

- **Daily JSONL files**: kept indefinitely (small, ~1-5 KB per day)
- **current.json**: overwritten on each snapshot
- **Ring listener state.json**: overwritten on each event (history in JSONL)

## Trend Analysis Opportunities

### Cat Health
- Weight trends per cat over weeks/months (Sopaipilla ~10 lbs, Burrito ~12 lbs)
- Weight variance (consistent vs fluctuating)
- Visit frequency from Litter-Robot activity history

### Sleep Quality
- Sleep score trends per person
- Duration patterns (weekday vs weekend)
- Deep sleep and REM percentages over time
- Snoring trends
- HRV and heart rate baselines

### Device Health
- Ring doorbell battery drain rate → predict when to charge
- Battery level correlation with temperature (batteries drain faster in cold)

### Dog Walk Patterns
- Walk frequency per day/week
- Walk duration (departed_at → returned_at)
- Time-of-day patterns
- How many FindMy polls before return detected
- Vision detection accuracy (how often both dogs seen in one event)

## Dependencies

| Dependency | Required For | Auth |
|------------|-------------|------|
| Litter-Robot venv | Cat weights | Whisker/AWS Cognito at `~/.config/litter-robot/config.yaml` |
| Eight Sleep API | Sleep data | Token at `~/.config/eightctl/token-cache.json` |
| Ring venv | Battery level | OAuth at `~/.config/ring/token-cache.json` |
| `BLUEBUBBLES_PASSWORD` | Ring listener notifications | `~/.openclaw/.secrets-cache` |
| Anthropic OAuth | Vision analysis | `~/.openclaw/.anthropic-oauth-cache` |
| Peekaboo | FindMy screenshots | Screen Recording + Accessibility TCC grants |

## Future Extensions

- **Petlibro**: food level, water level, feeding schedule adherence
- **HVAC runtime**: aggregate heating/cooling hours from nest-history snapshots
- **Dashboard integration**: serve home-state data from nest-dashboard on port 8550
- **Alerts**: OpenClaw notifies if cat weight changes >10% in a week, or Ring battery drops below 20%
