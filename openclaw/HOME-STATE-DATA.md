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

### Event-Driven (Dog Walk Listener)

Collected by `dog-walk-listener.py`, written on each dog walk event.

| Source | Data | Trigger |
|--------|------|---------|
| Fi GPS Collar | Departure detection (Potato leaves geofence) | Every 3 min during walk hours |
| Ring motion + WiFi + Fi GPS | Return detection (multi-signal) | Every 60s during active walk |
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
├── dog-walk/                            # Event-driven data
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

### Dog Walk Listener State (`dog-walk/state.json`)

```json
{
  "timestamp": "2026-04-04T13:18:00Z",
  "event_type": "return_poll",
  "home_location": "crosstown",
  "home_location_seen_at": "2026-04-04T13:03:00Z",
  "home_location_source": "fi_gps",
  "home_location_distance_m": 19,
  "dog_walk": {
    "active": true,
    "location": "crosstown",
    "departed_at": "2026-04-04T13:06:00Z",
    "returned_at": null,
    "people": 0,
    "dogs": 1,
    "walkers": ["dylan", "julia"],
    "return_signal": null,
    "walk_duration_minutes": null
  },
  "roombas": {
    "crosstown": {
      "status": "running",
      "started_at": "2026-04-04T13:06:01Z",
      "docked_at": null,
      "trigger": "dog_walk_departure",
      "last_command_result": {
        "success": true,
        "results": [
          {"name": "crosstown-roomba", "command": "start all", "returncode": 0, "output": "OK", "error": null}
        ]
      }
    }
  },
  "return_monitoring": {
    "active": true,
    "location": "crosstown",
    "started_at": "2026-04-04T13:06:01Z",
    "polls": 3,
    "last_poll_at": "2026-04-04T13:18:00Z",
    "last_fi_gps": {
      "distance_m": 512,
      "at_location": false,
      "battery": 88,
      "activity": "Walk",
      "age_s": 41
    },
    "last_network_check": {
      "any_present": false,
      "people": {
        "dylan": {"present": false},
        "julia": {"present": false}
      }
    }
  }
}
```

`home_location` is the last home geofence Potato was positively inside. Departure detection now anchors to that Fi-derived home rather than choosing a house from presence state first.

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
- **Dog walk state.json**: overwritten on each event (history in JSONL)

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
- Which return signal fires first (WiFi / Ring motion / Fi GPS / timeout)
- How often first Fi departures reset before confirmation

## Dependencies

| Dependency | Required For | Auth |
|------------|-------------|------|
| Litter-Robot venv | Cat weights | Whisker/AWS Cognito at `~/.config/litter-robot/config.yaml` |
| Eight Sleep API | Sleep data | Token at `~/.config/eightctl/token-cache.json` |
| Ring venv | Doorbell dings + return-motion signal | OAuth at `~/.config/ring/token-cache.json` |
| Fi collar session | Dog walk GPS + geofence checks | `~/.config/fi-collar/session.json` |
| `BLUEBUBBLES_PASSWORD` | Dog walk notifications | `~/.openclaw/.secrets-cache` |

## Future Extensions

- **Petlibro**: food level, water level, feeding schedule adherence
- **HVAC runtime**: aggregate heating/cooling hours from nest-history snapshots
- **Dashboard integration**: serve home-state data from nest-dashboard on port 8550
- **Alerts**: OpenClaw notifies if cat weight changes >10% in a week, or Ring battery drops below 20%
