# Dog Walk & Roomba Dashboard — Implementation Spec

## Status: v2.1 (2026-04-04)

Single-file Python HTTP server with embedded Chart.js UI. Visualizes dog walk departures, Roomba operations, return signal detection, and the Fi departure pipeline. Serves at port 8552 on Mac Mini, Tailscale-only access.

---

## System Overview

| Component | Source | Data |
|-----------|--------|------|
| Dog Walk Listener | `dog-walk-listener.py` (LaunchAgent) | Fi GPS departure detection, return monitoring, dock lifecycle |
| Roomba CLIs | `roomba` (cabin), `crosstown-roomba` (crosstown) | Start/dock commands and results |
| Crosstown Roomba Status | dorita980 MQTT via SSH to MBP | Real-time battery, phase, bin, tank |
| Cabin Roomba Status | iRobot Cloud API (`irobot-cloud.py`) | Last mission outcome, duration, area |
| Fi GPS Collar | `fi-collar status` | Potato's GPS, battery, activity, connection |
| Network Presence | `presence-detect.sh` | WiFi scans (Starlink gRPC for cabin, ARP for crosstown) |
| Fi GPS Departure | `fi-collar status` (3min poll) | Departure detection anchored to the collar's own nearest-home geofence |
| Manual Trigger | `dog-walk-start` CLI | Inbox IPC to dog-walk |

Two locations: **Cabin** (Phillipston) and **Crosstown** (West Roxbury).

---

## Architecture

```
Mac Mini (dylans-mac-mini)
├── Dog Walk Listener: ~/.openclaw/skills/dog-walk/dog-walk-listener.py
│   ├── State: ~/.openclaw/dog-walk/state.json
│   ├── History: ~/.openclaw/dog-walk/history/YYYY-MM-DD.jsonl
│   ├── Routes: ~/.openclaw/dog-walk/routes/<location>/<YYYY-MM-DD>/<walk_id>.json
│   ├── Inbox: ~/.openclaw/dog-walk/inbox/ (dog-walk-start IPC)
│   ├── Home anchor: last Fi geofence Potato was inside (`home_location`)
│   └── FCM credentials: ~/.openclaw/dog-walk/fcm-credentials.json
│
├── Dashboard Server: ~/.openclaw/bin/dog-walk-dashboard.py (port 8552)
│   ├── GET / → embedded HTML SPA
│   ├── GET /api/events?days=N → JSONL event history
│   ├── GET /api/current → current state.json
│   ├── GET /api/routes?days=N&location=all|cabin|crosstown → per-walk route summaries
│   ├── GET /api/fi → Fi collar GPS/battery/activity (2min cache)
│   ├── GET /api/roombas → Crosstown Roomba status via dorita980 (5min cache)
│   └── GET /api/cabin-roombas → Cabin Roomba last mission via iRobot Cloud (10min cache)
│
├── Fi Collar: ~/.openclaw/skills/fi-collar/fi-api.py
│   └── GraphQL → api.tryfi.com (GPS, battery, connection, geofence)
│
├── Roomba CLIs:
│   ├── ~/.openclaw/bin/roomba (cabin — Google Assistant for start/stop/dock)
│   ├── ~/.openclaw/bin/crosstown-roomba (crosstown — dorita980 MQTT via SSH to MBP)
│   └── ~/.openclaw/skills/cabin-roomba/irobot-cloud.py (cabin — cloud mission history)
│
└── Presence: ~/.openclaw/workspace/scripts/presence-detect.sh
```

### LaunchAgents

| Label | Type | Command | Port/Logs |
|-------|------|---------|-----------|
| `ai.openclaw.dog-walk-listener` | KeepAlive | `dog-walk-listener-wrapper.sh` | `~/.openclaw/logs/dog-walk-listener.log` |
| `ai.openclaw.dog-walk-dashboard` | KeepAlive | `python3 ~/.openclaw/bin/dog-walk-dashboard.py` | `~/.openclaw/logs/dog-walk-dashboard.{log,err.log}` |

---

## Data Sources

### JSONL Event History (`~/.openclaw/dog-walk/history/YYYY-MM-DD.jsonl`)

One JSON object per line. Every line is a full state snapshot with an `event_type` field identifying what triggered the write. Event types:

| event_type | Meaning | Frequency |
|------------|---------|-----------|
| `departure_candidate` | First Fi reading outside geofence | Per candidate |
| `departure_candidate_reset` | Candidate cleared before confirmation | Per reset |
| `departure` | Walk started, Roombas running | Per walk |
| `walkers_detected` | Who left detected ~2 min after departure | Per walk |
| `dock` | Walk ended, Roombas docked (return detected) | Per walk |
| `dock_timeout` | 2-hour safety fallback dock | Rare |
| `vision` | (legacy, no longer generated) | — |
| `return_start` | Return monitoring started | Per walk |
| `return_poll` | Network/Fi GPS check during monitoring | Every ~30s during walk |
| `return_stop` | Return monitoring stopped | Per walk |
| `state_update` | Generic state write (default) | Miscellaneous |

### Current State (`~/.openclaw/dog-walk/state.json`)

Live snapshot of the dog-walk's state. Same schema as JSONL lines but always reflects the latest write.

The listener also persists a Fi-derived home anchor in `state.json`:

- `home_location`: last home geofence Potato was positively inside (`cabin` or `crosstown`)
- `home_location_seen_at`: when that anchor was last refreshed
- `home_location_source`: currently always `fi_gps`
- `home_location_distance_m`: last in-geofence distance from that home center

### Route Files (`~/.openclaw/dog-walk/routes/<location>/<YYYY-MM-DD>/<walk_id>.json`)

One JSON file per walk. These are written by the listener on departure, appended during return monitoring, and finalized on dock or timeout.

Inter-home transits are marked at the route-file level and excluded from `GET /api/routes`, so future map views only see walks that start and end at the same house.

Top-level fields:

- `walk_id`: immutable walk identifier
- `origin_location`: house the walk started from
- `started_at` / `ended_at`
- `end_location`: inferred home geofence for the final route point, when known
- `is_interhome_transit`: true when `end_location` differs from `origin_location`
- `return_signal`
- `distance_m`
- `point_count`
- `points`: minimal Fi point list with `ts`, `lat`, `lon`

---

## JSONL Event Schema

### Walk Lifecycle Fields (`dog_walk`)

```json
{
  "active": true,
  "walk_id": "20260328T143000Z-cabin-deadbeef",
  "location": "cabin",
  "origin_location": "cabin",
  "departed_at": "2026-03-28T14:30:00Z",
  "returned_at": "2026-03-28T15:05:00Z",
  "people": 0,
  "dogs": 1,
  "walkers": ["dylan", "julia"],
  "return_signal": "network_wifi",
  "walk_duration_minutes": 35.0,
  "distance_m": 2487,
  "point_count": 18
}
```

| Field | Type | Set on | Description |
|-------|------|--------|-------------|
| `active` | bool | departure/dock | True while walk in progress |
| `walk_id` | string | departure | Immutable ID for route files and future map views |
| `location` | string | departure | "cabin" or "crosstown" |
| `origin_location` | string | departure | House the walk started from; stable even if Fi later reports a different nearest home |
| `departed_at` | ISO 8601 | departure | Walk start time |
| `returned_at` | ISO 8601 | dock | Walk end time |
| `people` | int | departure | Always `0` in the Fi-only system (no people counting) |
| `dogs` | int | departure | `1` for Fi-triggered auto walks, `0` for manual starts |
| `walkers` | string[] | walkers_detected | Who left (from WiFi scan ~2 min after departure) |
| `return_signal` | string | dock | What detected the return |
| `walk_duration_minutes` | float | dock | Computed from departed_at → returned_at |
| `distance_m` | int | departure/dock | Route distance from Fi walk distance when available, else haversine over stored points |
| `point_count` | int | departure/dock | Number of persisted Fi points in the route file |

**Return signal values:**

| Value | Meaning |
|-------|---------|
| `network_wifi` | Phone rejoined WiFi (most common) |
| `ring_motion` | Person detected at doorbell during monitoring |
| `findmy` | FindMy pin appeared near home (legacy — may appear in pre-2026-04-04 history) |
| `fi_gps` | Potato's Fi collar entered home geofence |
| `timeout` | 2-hour safety fallback (no return detected) |

### Roomba Fields (`roombas.<location>`)

```json
{
  "status": "running",
  "started_at": "2026-03-28T14:30:01Z",
  "docked_at": null,
  "trigger": "dog_walk_departure",
  "last_command_result": {
    "success": true,
    "results": [
      {"name": "floomba", "command": "start", "returncode": 0, "output": "OK", "error": null},
      {"name": "philly", "command": "start", "returncode": 0, "output": "OK", "error": null}
    ]
  }
}
```

### Departure Candidate Fields (transient — only on candidate events)

```json
{
  "event_type": "departure_candidate_reset",
  "candidate_location": "cabin",
  "candidate_started_at": "2026-04-04T13:12:00Z",
  "candidate_last_seen_at": "2026-04-04T13:15:00Z",
  "candidate_first_distance_m": 418,
  "candidate_last_distance_m": 26,
  "candidate_source": "fi_gps",
  "candidate_reset_reason": "inside_geofence"
}
```

**Reset reasons:**

| Reason | Description |
|--------|-------------|
| `inside_geofence` | Potato returned inside the monitored geofence before a second outside reading |
| `outside_walk_hours` | Candidate aged into a non-walk window before confirmation |
| `return_monitor_active` | A walk was already active, so the candidate was discarded |
| `no_occupied_location` | No Fi home anchor or nearest-home result was available for departure anchoring |
| `location_changed` | The Fi-derived home anchor changed before confirmation |

Candidates are anchored to `home_location` when available. If the listener restarts, it bootstraps from the persisted home anchor before evaluating new departures. If no anchor exists yet, it falls back to Fi's current nearest-home result until Potato is seen inside a home geofence again.

### Return Monitoring Fields (`return_monitoring`)

```json
{
  "active": true,
  "location": "cabin",
  "started_at": "2026-04-02T14:30:00Z",
  "polls": 13,
  "last_poll_at": "2026-04-02T14:45:00Z",
  "last_network_check": {
    "any_present": false,
    "people": {"dylan": {"present": false}, "julia": {"present": false}}
  },
  "last_fi_gps": {
    "distance_m": 450,
    "at_location": false,
    "battery": 92,
    "activity": "Walk",
    "age_s": 45
  }
}
```

---

## API Endpoints

### `GET /` — Dashboard HTML

Returns the embedded single-page application. Same pattern as nest-dashboard: Chart.js 4.x + Luxon time adapter, dark theme with `prefers-color-scheme: light` support.

### `GET /api/events?days=N` — Event History

Returns JSONL events from the last N days (default 30, max 365).

```json
{
  "meta": {"days": 30, "count": 847},
  "events": [{ "timestamp": "...", "event_type": "...", ... }, ...]
}
```

### `GET /api/current` — Current State

Returns the latest `state.json` snapshot.

### `GET /api/fi` — Potato (Fi Collar)

Returns Potato's GPS location, battery, activity, and connection status. Cached for 2 minutes (Fi GPS updates ~7min at rest).

```json
{
  "pet": {
    "name": "Potato", "activity": "Rest", "battery": 95,
    "connection": "User", "connectionDetail": "Dylan",
    "latitude": 42.602, "longitude": -72.151,
    "location": "cabin", "distance_m": 19, "at_location": true
  },
  "base": { "name": "Crosstown", "online": true }
}
```

### `GET /api/roombas` — Crosstown Roombas (Real-Time)

Returns live status for Crosstown Roombas via dorita980 MQTT (SSH to MBP). Cached for 5 minutes. Each robot query takes ~5-15s (SSH + MQTT handshake).

```json
{
  "location": "crosstown",
  "robots": {
    "10max": { "label": "Roomba Combo 10 Max", "phase": "charge", "status": "Charging", "battery": 100, "binFull": false, "binPresent": true, "tank": 42, "error": 0, "missions": 615 },
    "j5": { "label": "Roomba J5 (Scoomba)", "phase": "charge", "status": "Charging", "battery": 100, "binFull": false, "binPresent": true, "error": 0, "missions": 291 }
  }
}
```

### `GET /api/cabin-roombas` — Cabin Roombas (Last Mission)

Returns last mission data for cabin Roombas via iRobot Cloud API. Cached for 10 minutes. Uses Gigya + iRobot OAuth → AWS SigV4 signed mission history endpoint.

```json
{
  "location": "cabin",
  "robots": {
    "floomba": { "name": "Floomba", "lastMission": "stuck", "durationMin": 30, "sqft": 210, "startTime": 1774796428, "missions": 24 },
    "philly": { "name": "philly", "lastMission": "ok", "durationMin": 45, "sqft": 380, "startTime": 1774803184, "missions": 27 }
  }
}
```

Note: Real-time cabin Roomba status (battery, phase) is not available — the cabin Roombas (Y354020 firmware ver 4) don't expose local MQTT (port 8883 closed), and the iRobot cloud temp credentials don't grant `iot:GetThingShadow`. Mission history is the best available via REST.

---

## Dashboard UI

### Status Cards

| Card | Source | Display |
|------|--------|---------|
| **Current Walk** | `dog_walk.active` | Elapsed time (green) or "None" (gray) with last return time |
| **Last Walk** | `dog_walk.walk_duration_minutes` | Duration + return signal badge |
| **Departure Candidate** | `event_type=departure_candidate` | Pending first outside reading awaiting confirmation |
| **Return Monitor** | `return_monitoring.active` | Poll count + Potato GPS distance |
| **Potato Battery** | `/api/fi` | Battery % (green/amber/red), connection type, last report age |
| **Potato Location** | `/api/fi` | Activity (Rest/Walk), distance from home, geofence status |
| **Fi Base** | `/api/fi` | Base station online/offline |
| **Crosstown Roombas** | `/api/roombas` | Per-robot: phase, battery %, bin status, tank level |
| **Cabin Roombas** | `/api/cabin-roombas` | Per-robot: last mission outcome, duration, area, time ago |

### Location Filter

- **Both** (default) — all events
- **Cabin** — cabin events only
- **Crosstown** — crosstown events only

### Time Range

- **7d** (default), **30d**, **90d**, **1Y**

### Recent Walks Table

| Column | Source |
|--------|--------|
| Date | `dog_walk.departed_at` |
| Location | `dog_walk.location` (+ "manual" tag if `roombas.<loc>.last_command_result.source == "dog-walk-start"`) |
| Duration | `dog_walk.walk_duration_minutes` |
| Return Signal | `dog_walk.return_signal` (color-coded badge) |
| Walkers | `dog_walk.walkers` |
| Roombas | `roombas.<loc>.last_command_result.success` (OK/Failed badge) |

Pairs departure events with their matching dock events by location + departure timestamp.

### Charts

| Chart | Type | Data Source |
|-------|------|-------------|
| **Walk Duration** | Scatter | `event_type=dock` → `walk_duration_minutes` over time, colored by location |
| **Return Signal Distribution** | Doughnut | `event_type=dock` → group by `return_signal` |
| **Departure Pipeline** | Horizontal bar | `departure_candidate` + reset reasons + Fi departures + manual starts + completed walks |
| **Walks per Day** | Bar | `event_type=departure` grouped by date |

### Visual Style

Matches nest-dashboard exactly:
- Dark theme default (`#0f1117` background, `#1a1d27` surface)
- Light theme via `prefers-color-scheme: light`
- System fonts (-apple-system stack)
- 8px border-radius cards with `var(--border)` borders
- Blue active buttons (`#3b82f6`)
- Color-coded badges (green/amber/red/blue/purple)
- Auto-refresh every 5 minutes

---

## Color Scheme

### Location Colors

| Location | Color | Hex |
|----------|-------|-----|
| Cabin | Orange | `#FF8C00` |
| Crosstown | Blue | `#4A90D9` |

### Return Signal Colors

| Signal | Color | Hex |
|--------|-------|-----|
| WiFi | Green | `#22c55e` |
| Ring Motion | Blue | `#3b82f6` |
| FindMy | Purple | `#8b5cf6` | (legacy — displayed for old history data only) |
| Fi GPS | Teal | `#14b8a6` |
| Timeout | Red | `#ef4444` |

### Skip Reason Colors

| Reason | Color | Hex |
|--------|-------|-----|
| Outside Hours | Gray | `#6b7280` |
| Vacant | Light Gray | `#9ca3af` |
| WiFi Present | Blue | `#3b82f6` |
| Prompt Suppressed | Amber | `#f59e0b` |

---

## Deployment

### Files to deploy to Mini

| Source (dotfiles) | Destination (Mini) |
|-------------------|--------------------|
| `openclaw/dog-walk-dashboard.py` | `~/.openclaw/bin/dog-walk-dashboard.py` |
| `openclaw/skills/fi-collar/fi-api.py` | `~/.openclaw/skills/fi-collar/fi-api.py` |
| `openclaw/skills/cabin-roomba/irobot-cloud.py` | `~/.openclaw/skills/cabin-roomba/irobot-cloud.py` |
| LaunchAgent plist (see below) | `~/Library/LaunchAgents/ai.openclaw.dog-walk-dashboard.plist` |

### LaunchAgent Plist

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>ai.openclaw.dog-walk-dashboard</string>
  <key>ProgramArguments</key>
  <array>
    <string>/usr/bin/python3</string>
    <string>/Users/dbochman/.openclaw/bin/dog-walk-dashboard.py</string>
  </array>
  <key>RunAtLoad</key>
  <true/>
  <key>KeepAlive</key>
  <true/>
  <key>StandardOutPath</key>
  <string>/Users/dbochman/.openclaw/logs/dog-walk-dashboard.log</string>
  <key>StandardErrorPath</key>
  <string>/Users/dbochman/.openclaw/logs/dog-walk-dashboard.err.log</string>
  <key>WorkingDirectory</key>
  <string>/Users/dbochman</string>
</dict>
</plist>
```

### Smoke Test

```bash
# Local test (runs in foreground)
python3 ~/.openclaw/bin/dog-walk-dashboard.py &
curl -s http://localhost:8552/api/current | python3 -m json.tool
curl -s http://localhost:8552/api/events?days=1 | python3 -c "import sys,json; d=json.load(sys.stdin); print(f'{d[\"meta\"][\"count\"]} events')"
kill %1

# Tailscale access
curl -s http://dylans-mac-mini:8552/ | head -5
```

---

## Dashboard Queries (what the UI answers)

| Question | Chart/Component | Data Path |
|----------|----------------|-----------|
| Is a walk happening now? | Status card | `dog_walk.active` |
| Which house is the next departure anchored to? | Current state API | `home_location` |
| How long was the last walk? | Status card + table | `walk_duration_minutes` |
| How does the system detect returns? | Doughnut chart | `return_signal` distribution |
| How often does the Fi pipeline need a second reading? | Pipeline chart | `departure_candidate` count |
| Why do candidates fail to confirm? | Pipeline chart | `departure_candidate_reset` grouped by `candidate_reset_reason` |
| What time of day do walks happen? | Walk table | `departed_at` timestamps |
| Are Roomba commands reliable? | Walk table | `last_command_result.success` |
| How many walks per day/week? | Walks per day chart | `departure` events grouped by date |
| Who usually walks the dogs? | Walk table | `walkers` field |
| How long are walks at cabin vs crosstown? | Duration scatter | Points colored by location |
