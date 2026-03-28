# Dog Walk & Roomba Dashboard — Implementation Spec

## Status: v1.0 (2026-03-28)

Single-file Python HTTP server with embedded Chart.js UI. Visualizes dog walk departures, Roomba operations, return signal detection, and the departure suppression funnel. Serves at port 8552 on Mac Mini, Tailscale-only access.

---

## System Overview

| Component | Source | Data |
|-----------|--------|------|
| Ring Doorbell Listener | `ring-listener.py` (LaunchAgent) | Motion events, vision analysis, departure/dock lifecycle |
| Roomba CLIs | `roomba` (cabin), `crosstown-roomba` (crosstown) | Start/dock commands and results |
| Network Presence | `presence-detect.sh` | WiFi scans (Starlink gRPC for cabin, ARP for crosstown) |
| FindMy Polling | Peekaboo + Claude Haiku | Location proximity to home |
| Manual Trigger | `dog-walk-start` CLI | Inbox IPC to ring-listener |

Two locations: **Cabin** (Phillipston) and **Crosstown** (West Roxbury).

---

## Architecture

```
Mac Mini (dylans-mac-mini)
├── Ring Listener: ~/.openclaw/skills/ring-doorbell/ring-listener.py
│   ├── State: ~/.openclaw/ring-listener/state.json
│   ├── History: ~/.openclaw/ring-listener/history/YYYY-MM-DD.jsonl
│   ├── Inbox: ~/.openclaw/ring-listener/inbox/ (dog-walk-start IPC)
│   └── Frames: ~/.openclaw/ring-listener/frames/ (temp, cleaned after send)
│
├── Dashboard Server: ~/.openclaw/bin/ring-dashboard.py (port 8552)
│   ├── GET / → embedded HTML SPA
│   ├── GET /api/events?days=N → JSONL event history
│   └── GET /api/current → current state.json
│
├── Roomba CLIs:
│   ├── ~/.openclaw/bin/roomba (cabin — Google Assistant)
│   └── ~/.openclaw/bin/crosstown-roomba (crosstown — dorita980 MQTT)
│
└── Presence: ~/.openclaw/workspace/scripts/presence-detect.sh
```

### LaunchAgents

| Label | Type | Command | Port/Logs |
|-------|------|---------|-----------|
| `ai.openclaw.ring-listener` | KeepAlive | `ring-listener-wrapper.sh` | `~/.openclaw/logs/ring-listener.log` |
| `ai.openclaw.ring-dashboard` | KeepAlive | `python3 ~/.openclaw/bin/ring-dashboard.py` | `~/.openclaw/logs/ring-dashboard.{log,err.log}` |

---

## Data Sources

### JSONL Event History (`~/.openclaw/ring-listener/history/YYYY-MM-DD.jsonl`)

One JSON object per line. Every line is a full state snapshot with an `event_type` field identifying what triggered the write. Event types:

| event_type | Meaning | Frequency |
|------------|---------|-----------|
| `departure` | Walk started, Roombas running | Per walk |
| `walkers_detected` | Who left detected ~2 min after departure | Per walk |
| `dock` | Walk ended, Roombas docked (return detected) | Per walk |
| `dock_timeout` | 2-hour safety fallback dock | Rare |
| `departure_skip` | Departure suppressed by a filter | Per motion event during walk hours |
| `vision` | Claude Haiku video frame analysis completed | Per motion event with Ring Protect |
| `findmy_start` | Return monitoring started | Per walk |
| `findmy_poll` | Network/FindMy check during monitoring | Every ~60s during walk |
| `findmy_stop` | Return monitoring stopped | Per walk |
| `state_update` | Generic state write (default) | Miscellaneous |

### Current State (`~/.openclaw/ring-listener/state.json`)

Live snapshot of the ring-listener's state. Same schema as JSONL lines but always reflects the latest write.

---

## JSONL Event Schema

### Walk Lifecycle Fields (`dog_walk`)

```json
{
  "active": true,
  "location": "cabin",
  "departed_at": "2026-03-28T14:30:00Z",
  "returned_at": "2026-03-28T15:05:00Z",
  "people": 2,
  "dogs": 1,
  "walkers": ["dylan", "julia"],
  "return_signal": "network_wifi",
  "walk_duration_minutes": 35.0
}
```

| Field | Type | Set on | Description |
|-------|------|--------|-------------|
| `active` | bool | departure/dock | True while walk in progress |
| `location` | string | departure | "cabin" or "crosstown" |
| `departed_at` | ISO 8601 | departure | Walk start time |
| `returned_at` | ISO 8601 | dock | Walk end time |
| `people` | int | departure | People detected (0 = manual trigger) |
| `dogs` | int | departure | Dogs detected (0 = manual trigger) |
| `walkers` | string[] | walkers_detected | Who left (from WiFi scan ~2 min after departure) |
| `return_signal` | string | dock | What detected the return |
| `walk_duration_minutes` | float | dock | Computed from departed_at → returned_at |

**Return signal values:**

| Value | Meaning |
|-------|---------|
| `network_wifi` | Phone rejoined WiFi (most common) |
| `ring_motion` | Person detected at doorbell during monitoring |
| `findmy` | FindMy pin appeared near home |
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

### Skip Event Fields (transient — only on `departure_skip` events)

```json
{
  "event_type": "departure_skip",
  "skip_reason": "wifi_present",
  "skip_location": "cabin",
  "skip_details": {
    "wifi": {"dylan": {"present": true}, "julia": {"present": true}}
  }
}
```

**Skip reasons:**

| Reason | Description |
|--------|-------------|
| `outside_walk_hours` | Motion outside 8-10 AM / 11 AM-1 PM / 5-8 PM windows |
| `confirmed_vacant` | Location already confirmed vacant (no one home to leave) |
| `wifi_present` | Phone detected on WiFi at decision time (returning, not departing) |
| `cabin_prompt_suppressed` | Already prompted in this walk window at cabin |

### FindMy Polling Fields (`findmy_polling`)

```json
{
  "active": true,
  "location": "cabin",
  "started_at": "2026-03-28T14:30:00Z",
  "polls": 13,
  "last_poll_at": "2026-03-28T14:45:00Z",
  "last_network_check": {
    "any_present": false,
    "people": {"dylan": {"present": false}, "julia": {"present": false}}
  },
  "last_result": {
    "street": "School House Rd",
    "near_home": false,
    "description": "Walking on School House Rd"
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

---

## Dashboard UI

### Status Cards

| Card | Source | Display |
|------|--------|---------|
| **Current Walk** | `dog_walk.active` | Elapsed time (green) or "None" (gray) with last return time |
| **Last Walk** | `dog_walk.walk_duration_minutes` | Duration + return signal badge |
| **Roombas (per location)** | `roombas.<loc>.status` | Running (green) / Docked (gray) + last command result |
| **Return Monitor** | `findmy_polling.active` | Poll count + last FindMy description |

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
| Location | `dog_walk.location` (+ "manual" tag if `people == 0`) |
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
| **Detection Funnel** | Horizontal bar | Skip reasons (from `departure_skip`) + departures + docks |
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
| FindMy | Purple | `#8b5cf6` |
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
| `openclaw/ring-dashboard.py` | `~/.openclaw/bin/ring-dashboard.py` |
| LaunchAgent plist (see below) | `~/Library/LaunchAgents/ai.openclaw.ring-dashboard.plist` |

### LaunchAgent Plist

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>ai.openclaw.ring-dashboard</string>
  <key>ProgramArguments</key>
  <array>
    <string>/usr/bin/python3</string>
    <string>/Users/dbochman/.openclaw/bin/ring-dashboard.py</string>
  </array>
  <key>RunAtLoad</key>
  <true/>
  <key>KeepAlive</key>
  <true/>
  <key>StandardOutPath</key>
  <string>/Users/dbochman/.openclaw/logs/ring-dashboard.log</string>
  <key>StandardErrorPath</key>
  <string>/Users/dbochman/.openclaw/logs/ring-dashboard.err.log</string>
  <key>WorkingDirectory</key>
  <string>/Users/dbochman</string>
</dict>
</plist>
```

### Smoke Test

```bash
# Local test (runs in foreground)
python3 ~/.openclaw/bin/ring-dashboard.py &
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
| How long was the last walk? | Status card + table | `walk_duration_minutes` |
| How does the system detect returns? | Doughnut chart | `return_signal` distribution |
| How many false positives does WiFi prevent? | Funnel chart | `departure_skip` count by reason |
| What time of day do walks happen? | Walk table | `departed_at` timestamps |
| Are Roomba commands reliable? | Walk table | `last_command_result.success` |
| How many walks per day/week? | Walks per day chart | `departure` events grouped by date |
| Who usually walks the dogs? | Walk table | `walkers` field |
| How long are walks at cabin vs crosstown? | Duration scatter | Points colored by location |
