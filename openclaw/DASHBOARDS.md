# OpenClaw Dashboards

All dashboards run on Mac Mini (`dylans-mac-mini`) as KeepAlive LaunchAgents. Access via Tailscale only.

## Quick Reference

| Port | Dashboard | URL | Data Refresh |
|------|-----------|-----|-------------|
| 8550 | [Nest Climate](#nest-climate-dashboard) | http://dylans-mac-mini:8550 | 5 min (UI) · 30 min (snapshots) |
| 8551 | [OpenClaw Usage](#openclaw-usage-dashboard) | http://dylans-mac-mini:8551 | 5 min (UI) · 15 min (snapshots) |
| 8552 | [Dog Walk & Roomba](#dog-walk--roomba-dashboard) | http://dylans-mac-mini:8552 | 5 min (UI) · event-driven (JSONL) |
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

## Dog Walk & Roomba Dashboard

**Port 8552** · [Full spec](DOG-WALK-DASHBOARD.md)

Visualizes dog walk departures, Roomba operations, return signal detection, and the departure suppression funnel.

### What It Shows

- **Status cards** — current walk, last walk summary, return monitor state
- **Potato (Fi collar) cards** — battery %, activity (Rest/Walk), GPS location, connection type, Fi base station status
- **Crosstown Roomba cards** — real-time battery, cleaning phase, bin status, tank level (via dorita980 MQTT)
- **Cabin Roomba cards** — last mission outcome, duration, area cleaned (via iRobot Cloud API)
- **Recent Walks table** — date, location, duration, return signal badge, walkers, Roomba result
- **Walk Duration** — scatter chart by location over time
- **Return Signal Distribution** — doughnut (WiFi / Ring Motion / Fi GPS / Timeout)
- **Detection Funnel** — horizontal bar showing skip reasons vs departures vs docks
- **Walks per Day** — daily bar chart for trend analysis

### Data Sources

| Source | Frequency | Data |
|--------|-----------|------|
| Dog Walk Listener | Event-driven | Fi GPS departure detection, return monitoring, dock lifecycle |
| Crosstown Roomba (dorita980) | 5 min cache | Real-time battery, phase, bin, tank via SSH to MBP |
| Cabin Roomba (iRobot Cloud) | 10 min cache | Last mission outcome via Gigya + AWS SigV4 REST API |
| Fi GPS Collar | 2 min cache | Potato GPS, battery, activity, connection, geofence |
| Network presence | Per walk + 60s polling | WiFi scans (Starlink gRPC for cabin, ARP for crosstown) |
| `dog-walk-start` CLI | Manual trigger | Inbox IPC to dog-walk listener |

### Locations

| Location | Roombas |
|----------|---------|
| Cabin (Phillipston) | Floomba + Philly (Google Assistant) |
| Crosstown (West Roxbury) | Roomba Combo 10 Max + J5 (dorita980 MQTT) |

### Files

| File | Path |
|------|------|
| Server | `openclaw/dog-walk-dashboard.py` → `~/.openclaw/bin/dog-walk-dashboard.py` |
| Fi collar API | `openclaw/skills/fi-collar/fi-api.py` → `~/.openclaw/skills/fi-collar/fi-api.py` |
| iRobot Cloud API | `openclaw/skills/cabin-roomba/irobot-cloud.py` → `~/.openclaw/skills/cabin-roomba/irobot-cloud.py` |
| LaunchAgent | `openclaw/launchagents/ai.openclaw.dog-walk-dashboard.plist` |
| Event history | `~/.openclaw/dog-walk/history/YYYY-MM-DD.jsonl` |
| Current state | `~/.openclaw/dog-walk/state.json` |
| iRobot session cache | `~/.config/irobot-cloud/session.json` (1hr TTL) |
| Logs | `~/.openclaw/logs/dog-walk-dashboard.{log,err.log}` |

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
curl -s http://dylans-mac-mini:8585/ | head -5   # Financial
```
