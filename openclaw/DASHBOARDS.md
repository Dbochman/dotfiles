# OpenClaw Dashboards

All dashboards run on Mac Mini (`dylans-mac-mini`) as KeepAlive LaunchAgents. Access via Tailscale only.

## Quick Reference

| Port | Dashboard | URL | Data Refresh |
|------|-----------|-----|-------------|
| 8550 | [Nest Climate](#nest-climate-dashboard) | http://dylans-mac-mini:8550 | 5 min (UI) · 30 min (snapshots) |
| 8551 | [OpenClaw Usage](#openclaw-usage-dashboard) | http://dylans-mac-mini:8551 | 5 min (UI) · 15 min (snapshots) |
| 8552 | [Dog Walk](#dog-walk-dashboard) | http://dylans-mac-mini:8552 | 5 min (UI) · event-driven (JSONL) |
| 8553 | [Roomba](#roomba-dashboard) | http://dylans-mac-mini:8553 | 5 min (UI) · event-driven (JSONL) |
| 8558 | [Home Control Plane](#home-control-plane-dashboard) | http://dylans-mac-mini:8558 | 60s cache · 5 min background refresh |
| 8585 | [Financial](#financial-dashboard) | http://dylans-mac-mini:8585 | On demand |

---

## Nest Climate Dashboard

**Port 8550** · [Full spec](NEST-CLIMATE-DASHBOARD.md)

Monitors thermostats and weather across two locations via three heating/cooling systems.

### What It Shows

- **Presence cards** — occupancy status per location (Occupied / Vacant / Possibly Vacant)
- **Temperature cards** — current temp, setpoint, HVAC mode, humidity per room
- **Temperature chart** — line graph with room temps + setpoints + outdoor weather
- **Humidity chart** — per-room humidity over time
- **HVAC Duty Cycle** — hourly bar chart showing heating/cooling activity

### Data Sources

| Source | Frequency | Data |
|--------|-----------|------|
| Nest SDM API | 30 min | Thermostat temps, setpoints, HVAC mode |
| Cielo CLI | 30 min | Minisplit AC status (Crosstown) |
| Mysa API | 30 min | Baseboard heater temps + duty cycle (Crosstown) |
| Open-Meteo | 30 min | Outdoor weather (no API key needed) |
| Presence scanner | Continuous | WiFi-based occupancy per location |

### Locations & Rooms

| Location | System | Rooms |
|----------|--------|-------|
| Cabin (Philly) | Nest central HVAC | Solarium, Living Room, Bedroom |
| Crosstown (19Crosstown) | Cielo minisplit | Living Room, Basement, Dylan's Office, Bedroom |
| Crosstown (19Crosstown) | Mysa baseboard | Cat Room, Basement door, Movie room |

### Files

| File | Path |
|------|------|
| Server | `openclaw/nest-dashboard.py` → `~/.openclaw/bin/nest-dashboard.py` |
| LaunchAgent | `openclaw/launchagents/ai.openclaw.nest-dashboard.plist` |
| Data | `~/.openclaw/nest-history/YYYY-MM-DD.jsonl` |
| Presence | `~/.openclaw/presence/state.json` + `history/` |
| Logs | `~/.openclaw/logs/nest-dashboard.{log,err.log}` |

---

## OpenClaw Usage Dashboard

**Port 8551**

Tracks token consumption, costs, and agent activity for OpenClaw's Claude API usage.

### What It Shows

- **Utilization gauges** — 5-hour and 7-day token usage rings (green/amber/red thresholds)
- **Stat cards** — total cost, total tokens, cron runs, messages sent/received, sessions, errors, gateway restarts
- **Token Usage Over Time** — stacked bar chart (hourly or daily adaptive) by model
- **Activity chart** — sent/received/cron messages over time
- **Cost Over Time** — cache write/read/output/input cost breakdown
- **Model Split** — doughnut chart (Opus / Sonnet / Haiku usage)
- **Tool Usage** — horizontal bar of most-used tools
- **Recent Cron Runs** — table with status badges, duration, token counts

### Data Sources

| Source | Frequency | Data |
|--------|-----------|------|
| Anthropic Usage API | 15 min | 5h/7d utilization percentages |
| Usage snapshots | 15 min | Per-model token counts, costs, cache stats |
| Gateway RPC | 15 min | Session data (tool calls, costs, latency) |
| Cron run logs | Event-driven | Job ID, status, duration, tokens |
| BlueBubbles API | 15 min | Message send/receive counts |
| ccusage push | 30 min | Claude Code daily token usage (from MacBook) |

### Files

| File | Path |
|------|------|
| Server | `openclaw/bin/usage-dashboard.py` → `~/.openclaw/bin/usage-dashboard.py` |
| LaunchAgent | `openclaw/launchagents/ai.openclaw.usage-dashboard.plist` |
| Data | `~/.openclaw/usage-history/YYYY-MM-DD.jsonl` |
| Claude Code data | `~/.openclaw/usage-history/ccusage-daily.json` |
| Logs | `~/.openclaw/logs/usage-dashboard.{log,err.log}` |

---

## Dog Walk Dashboard

**Port 8552** · [Full spec](DOG-WALK-DASHBOARD.md)

Visualizes dog walk departures, return signal detection, route maps, and the Fi departure pipeline.

### What It Shows

- **Status cards** — current walk, last walk summary, return monitor state
- **Potato (Fi collar) cards** — battery %, activity (Rest/Walk), GPS location, connection type, Fi base station status
- **Walk map** — three layer modes: Routes (single-walk selection), Coverage (all walks at full weight with date range picker), Heatmap (density)
- **Recent Walks table** — date, location, duration, return signal badge, walkers
- **Walk Duration** — scatter chart by location over time
- **Return Signal Distribution** — doughnut (WiFi / Ring Motion / Fi GPS / Timeout)
- **Departure Pipeline** — horizontal bar showing first outside reads, candidate resets, Fi departures, manual starts, and completed walks
- **Walks per Day** — daily bar chart for trend analysis

### Data Sources

| Source | Frequency | Data |
|--------|-----------|------|
| Dog Walk Listener | Event-driven | Fi GPS departure detection, return monitoring, dock lifecycle |
| Fi GPS Collar | 2 min cache | Potato GPS, battery, activity, connection, geofence |
| Network presence | Per walk + 60s polling | WiFi scans (Starlink gRPC for cabin, ARP for crosstown) |
| `dog-walk-start` CLI | Manual trigger | Inbox IPC to dog-walk listener |

### Files

| File | Path |
|------|------|
| Server | `openclaw/dog-walk-dashboard.py` → `~/.openclaw/bin/dog-walk-dashboard.py` |
| Fi collar API | `openclaw/skills/fi-collar/fi-api.py` → `~/.openclaw/skills/fi-collar/fi-api.py` |
| LaunchAgent | `openclaw/launchagents/ai.openclaw.dog-walk-dashboard.plist` |
| Event history | `~/.openclaw/dog-walk/history/YYYY-MM-DD.jsonl` |
| Current state | `~/.openclaw/dog-walk/state.json` |
| Logs | `~/.openclaw/logs/dog-walk-dashboard.{log,err.log}` |

---

## Roomba Dashboard

**Port 8553**

Roomba status, snooze controls, and run history calendar heatmap for both locations.

### What It Shows

- **Crosstown Roomba cards** — real-time battery, cleaning phase, bin status, tank level (via dorita980 MQTT)
- **Cabin Roomba cards** — last mission outcome, duration, area cleaned (via iRobot Cloud API)
- **Snooze controls** — temporarily disable Roomba automation per location (1h/3h/8h/Indef)
- **Calendar heatmap** — monthly view of Roomba runs per location, gradient color scale, hover tooltips with run details

### Data Sources

| Source | Frequency | Data |
|--------|-----------|------|
| Crosstown Roomba (dorita980) | 5 min cache | Real-time battery, phase, bin, tank via SSH to MBP |
| Cabin Roomba (iRobot Cloud) | 10 min cache | Last mission outcome via Gigya + AWS SigV4 REST API |
| Dog Walk History JSONL | On demand | Roomba start/dock events per walk |
| Snooze state | Real-time | Per-location snooze expiry |

### Locations

| Location | Roombas |
|----------|---------|
| Cabin (Phillipston) | Floomba + Philly (Google Assistant) |
| Crosstown (West Roxbury) | Roomba Combo 10 Max + J5 (dorita980 MQTT) |

### Files

| File | Path |
|------|------|
| Server | `openclaw/roomba-dashboard.py` → `~/.openclaw/bin/roomba-dashboard.py` |
| iRobot Cloud API | `openclaw/skills/cabin-roomba/irobot-cloud.py` → `~/.openclaw/skills/cabin-roomba/irobot-cloud.py` |
| LaunchAgent | `openclaw/launchagents/ai.openclaw.roomba-dashboard.plist` |
| Snooze state | `~/.openclaw/dog-walk/snooze.json` |
| Run history | `~/.openclaw/dog-walk/history/YYYY-MM-DD.jsonl` |
| Logs | `~/.openclaw/logs/roomba-dashboard.{log,err.log}` |

---

## Financial Dashboard

**Port 8585**

Julia's financial dashboard tracking spending, income, net worth, and utilities across multiple views.

### What It Shows

- **Main dashboard** — spending trends, income streams, net worth, savings rate, FIRE progress
- **Utilities — Electricity** — Eversource bills, year-over-year comparison
- **Utilities — Gas** — National Grid bills, year-over-year comparison
- **Utilities — Water** — BWSC bills, year-over-year comparison
- **Mortgage** — amortization schedule, payment history
- **Expenses** — category breakdown, trends, top merchants

### Data Sources

| Source | Frequency | Data |
|--------|-----------|------|
| SQLite database | On demand | Transactions, categories, balances |
| Plaid API | Manual import | Bank/credit card transactions |
| Config YAML | Static | Category overrides, FIRE settings, utility accounts |

### Files

| File | Path |
|------|------|
| Server | `~/repos/financial-dashboard/serve_dashboard.py` (separate repo) |
| LaunchAgent | `openclaw/launchagents/ai.openclaw.financial-dashboard.plist` |
| Database | `~/repos/financial-dashboard/finance.db` (gitignored) |
| Config | `~/repos/financial-dashboard/config.yaml` |
| Logs | `~/.openclaw/logs/financial-dashboard.{log,err.log}` |

---

## Home Control Plane Dashboard

**Port 8558**

Unified control plane for all smart home devices across both locations. Single-pane-of-glass for monitoring status and issuing commands to 18 device categories, organized into five collapsible sections.

### Layout

Cards are grouped into collapsible sections (all open by default, click header to collapse):

1. **Lighting** — Hue Crosstown, Hue Cabin
2. **Temperature** — Nest, Cielo, Mysa, Eight Sleep
3. **Security** — August Lock, Ring Doorbell, Nest Camera
4. **Pets** — Litter-Robot, Petlibro, Dog Walk
5. **Misc** — TV, Speakers, Cabin Speakers, Roombas (Crosstown + Cabin)

Command feedback (Running/Success/Error) appears inline below the section header of the clicked card, not at the top of the page. Success/error auto-dismiss after 4s.

### What It Shows

- **Hue Lights** — room chip cards (ON/OFF indicator, brightness%, color temp label e.g. "Warm White") with on/off, brightness, and color controls (Crosstown: 9 rooms, Cabin: 8 rooms)
- **Nest Thermostat** — per-room temp, setpoint, HVAC mode with set temp / set mode / eco controls (Cabin: 3 rooms)
- **Cielo AC** — per-unit temp, mode, fan speed with on/off, temp, and mode controls (Crosstown: 4 units)
- **Mysa Heaters** — per-heater temp, setpoint, humidity, duty cycle (read-only; Crosstown: 3 units)
- **Eight Sleep** — chip cards per side (bed temp °F, active/idle status) with on/off/set temp
- **August Lock** — lock state, door state, battery with lock/unlock controls
- **Ring Doorbell** — chip cards per doorbell (battery, last event with relative time) + snapshot capture with Crosstown/Cabin selector
- **Nest Camera** — live snapshot capture via WebRTC (Kitchen camera, Cabin); image displayed inline with relative timestamp
- **Litter-Robot** — chip card with status, waste level, cycle count, cat weights; clean/reset controls
- **Petlibro** — chip cards per device (fountain: water level, battery, filter alert; feeder: food level, next feed) with manual feed
- **Dog Walk** — active/inactive status, last walk details (read-only)
- **Samsung TV** — power state with on/off controls; shows friendly "TV is likely off" when unreachable
- **Google Speakers** — volume with set volume / mute / unmute; shows friendly "Speakers are likely asleep" when unreachable
- **Cabin Speakers** — chip cards per speaker (online/asleep status) with set volume / stop (via catt by IP)
- **Roombas** — chip cards per robot (status, battery, bin state) with start/stop/dock (Crosstown: 2 MQTT, Cabin: 2 Google)

### Architecture

```
Browser → home-dashboard.py (port 8558)
            ├── GET /                        → embedded HTML dashboard
            ├── GET /api/status              → cached results (instant, non-blocking)
            ├── GET /api/status?refresh=true → force re-poll all 18 CLIs
            ├── GET /api/status/<device>     → refresh single device
            ├── POST /api/command            → execute device command
            ├── GET /api/camera-snap/<name>  → serve JPEG snapshot (nest/ring)
            └── GET /api/presence            → presence state
```

- **Progressive loading** — `GET /api/status` returns cached data instantly (no blocking). Uncached devices listed in `meta.pending`; frontend polls them individually in background. Cards render as data arrives.
- **Precache on startup** — all 17 collectors run in parallel via `ThreadPoolExecutor` at boot
- **Background refresh** — every 5 minutes, all collectors re-run in background
- **Per-device refresh** — `GET /api/status/<device_name>` refreshes one collector and updates cache
- **60s cache TTL** — CLI results cached to avoid hammering APIs
- **30s command timeout** — accommodates slower SSH-based collectors (crosstown roombas, speakers)
- **Secrets loading** — sources `~/.openclaw/.secrets-cache` at startup for CLI env vars (Petlibro, 8sleep, etc.)
- **Custom renderers** — all device categories have dedicated JS renderers with room-chip card layout; TV and Speakers show friendly messages when devices are off/asleep; Hue shows human-readable color temp (Warm White, Daylight, etc.) only when lights are on
- **Camera snapshots** — Nest (WebRTC) and Ring snapshots saved to `~/.openclaw/camera-snaps/`, served via `/api/camera-snap/<name>` with timestamp header; loaded on page refresh
- **Inline feedback** — command status messages appear below the section header of the clicked card, auto-dismiss success/error after 4s

### Controls

All controls use dropdown selectors (not text inputs) with pre-populated room/device lists:

| Device | Room/Device Selector | Extra Controls |
|--------|---------------------|----------------|
| Hue Crosstown | 9 rooms dropdown | Brightness, Color (warm/cool/daylight/red/blue/green/purple/orange/pink) |
| Hue Cabin | 8 rooms dropdown | Brightness, Color |
| Nest | 3 rooms dropdown | Temp °F, Mode (HEAT/OFF), Eco on/off |
| Cielo | 4 devices dropdown | Temp °F, Mode (cool/heat/auto/dry/fan) |
| Eight Sleep | Side (Dylan/Julia) | Level (-100 to +100), On / Off |
| August | — | Lock / Unlock |
| Ring Doorbell | Crosstown/Cabin dropdown | Take Snapshot |
| Nest Camera | Kitchen dropdown | Take Snapshot |
| Litter-Robot | — | Clean / Reset |
| Petlibro | — | Feed (portions) |
| Samsung TV | — | Power On / Off |
| Speakers | Speaker selector | Volume, Mute / Unmute |
| Crosstown Roomba | Robot selector | Start / Stop / Dock |
| Cabin Roomba | Robot selector | Start / Stop / Dock |

### Data Sources

| Source | Type | Data |
|--------|------|------|
| `~/.openclaw/presence/state.json` | File | Occupancy per location |
| `hue --crosstown/--cabin status` | CLI | Room-by-room light status |
| `~/.openclaw/nest-history/*.jsonl` | File | Latest Nest snapshot |
| `cielo status --json` | CLI | Minisplit status (JSON) |
| `mysa` | CLI | Baseboard heater status (JSON) |
| `august status` | CLI | Lock state (JSON, via SSH to MBP) |
| `crosstown-roomba status` | CLI | Roomba status (via SSH+MQTT to MBP) |
| `roomba status <name>` | CLI | Cabin roombas (per-robot, Google Assistant) |
| `samsung-tv status` | CLI | TV power state |
| `speaker status` | CLI | Speaker volume/reachability |
| `litter-robot status` | CLI | LR4 status |
| `petlibro status` | CLI | Feeder + fountain |
| `8sleep status` | CLI | Pod temp, both sides |
| `ring status` | CLI | Doorbell battery, motion |
| `ring snapshot <path> [id]` | CLI | Doorbell camera snapshot (JPEG) |
| `nest camera snap <room> <path>` | CLI | Nest camera snapshot via WebRTC (JPEG) |
| `~/.openclaw/camera-snaps/*.jpg` | File | Cached camera/doorbell snapshots |
| `~/.openclaw/dog-walk/state.json` | File | Walk state |

### Device → Location Mapping

| Device | Crosstown | Cabin |
|--------|-----------|-------|
| Hue Lights | Entryway, Kitchen, Bedroom, Movie, Living, Office, Upstairs, Downstairs, Master | Kitchen, Living, Bathroom, Hallway, Bedroom, Office, Solarium, Staircase |
| Nest | — | Solarium, Living Room, Bedroom |
| Cielo AC | Basement, Living Room, Dylan's Office, Bedroom | — |
| Mysa Heaters | Cat Room, Basement door, Movie room | — |
| August Lock | Front Door | — |
| Roombas | 10 Max + J5 (MQTT via MBP) | Floomba + Philly (Google) |
| Samsung TV | Frame 65 | — |
| Google Speakers | Bedroom + Living Room | Kitchen + Bedroom |
| Litter-Robot | LR4 | — |
| Petlibro | Feeder + Fountain | (seasonal, unplugged) |
| Eight Sleep | Pod 3 (both sides) | — |
| Ring Doorbell | Front Door (snap + status) | Front Door (snap + status) |
| Nest Camera | — | Kitchen (snap) |
| Dog Walk | Yes | Yes |
| Presence | Yes | Yes |

### Files

| File | Path |
|------|------|
| Server | `openclaw/bin/home-dashboard.py` → `~/.openclaw/bin/home-dashboard.py` |
| LaunchAgent | `openclaw/launchagents/ai.openclaw.home-dashboard.plist` |
| Camera snaps | `~/.openclaw/camera-snaps/*.jpg` |
| Logs | `~/.openclaw/logs/home-dashboard.{log,err.log}` |

### Known Limitations

- **Mysa is read-only** — the Mysa API doesn't expose setpoint changes or on/off; use the Mysa app or physical thermostat
- **Cabin Roombas use Google Assistant** — responses are natural language text, not structured JSON
- **Petlibro/8sleep** require env vars from `~/.openclaw/.secrets-cache` — if secrets are stale, these collectors will error
- **Crosstown Roombas and Speakers** route through SSH to MBP — if MBP is offline, these time out
- **Nest Camera snapshot** takes ~10-15s (WebRTC negotiation + first frame); SDM API exposes no battery/online status for cameras
- **Ring snapshot** may fail on battery doorbells if the doorbell is asleep; requires Ring Protect subscription

---

## Common Architecture

All dashboards follow the same single-file Python server pattern:

- **Server:** `ThreadingMixIn` + `HTTPServer` (stdlib only, zero pip dependencies)
- **UI:** Embedded HTML SPA with Chart.js 4.x + Luxon time adapter
- **Theme:** Dark default (`#0f1117`), light via `prefers-color-scheme`
- **Fonts:** System font stack (-apple-system, BlinkMacSystemFont, Segoe UI)
- **Data:** JSONL daily files, loaded on-demand with time range filtering
- **Refresh:** 5-minute auto-refresh via `setInterval`
- **Access:** Tailscale-only (Mac Mini firewall blocks external ports)
- **Process:** KeepAlive LaunchAgent (auto-restarts on crash)

The financial dashboard differs in two expected ways: it uses SQLite instead of JSONL (relational data with complex queries), and serves separate HTML files instead of an embedded SPA (5 distinct dashboard pages). These are justified by its data model. The operational patterns (ThreadingMixIn, SIGTERM handling, `0.0.0.0` binding, KeepAlive LaunchAgent) are aligned with the other three dashboards.

---

## Troubleshooting

### Check if a dashboard is running

```bash
ssh dbochman@dylans-mac-mini "launchctl list | grep dashboard"
```

### Restart a dashboard

```bash
ssh dbochman@dylans-mac-mini "launchctl stop ai.openclaw.<name>-dashboard"
# KeepAlive auto-restarts it
```

### Check logs

```bash
ssh dbochman@dylans-mac-mini "tail -20 ~/.openclaw/logs/<name>-dashboard.log"
ssh dbochman@dylans-mac-mini "tail -20 ~/.openclaw/logs/<name>-dashboard.err.log"
```

### Verify from local machine

```bash
curl -s http://dylans-mac-mini:8550/ | head -5   # Nest
curl -s http://dylans-mac-mini:8551/ | head -5   # Usage
curl -s http://dylans-mac-mini:8552/ | head -5   # Dog Walk
curl -s http://dylans-mac-mini:8553/ | head -5   # Roomba
curl -s http://dylans-mac-mini:8558/ | head -5   # Home Control Plane
curl -s http://dylans-mac-mini:8585/ | head -5   # Financial
```
