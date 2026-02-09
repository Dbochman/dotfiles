# Nest Climate Dashboard

Self-hosted web dashboard for visualizing Nest thermostat + weather history data.

## Architecture

Single Python file (`nest-dashboard.py`) using only stdlib — no dependencies, no build step, no database. Serves both a JSON API and an embedded HTML/CSS/JS dashboard with Chart.js from CDN.

```
Mac Mini (dylans-mac-mini)
├── ~/.openclaw/bin/nest-dashboard.py          # Server (port 8550)
├── ~/.openclaw/nest-history/YYYY-MM-DD.jsonl  # Data (written by nest snapshot)
├── ~/.openclaw/logs/nest-dashboard.{log,err.log}
├── ~/Library/LaunchAgents/ai.openclaw.nest-dashboard.plist
└── /opt/homebrew/bin/nest → dotfiles/bin/nest  # CLI with dashboard subcommand
```

## Data Source

The existing `nest snapshot` LaunchAgent records thermostat + weather data every 30 minutes to JSONL files at `~/.openclaw/nest-history/YYYY-MM-DD.jsonl`. Each line is a JSON object:

```json
{
  "timestamp": "2026-02-09T14:08:42Z",
  "weather": {
    "Philly": {
      "temp_f": 8.9, "feels_like_f": -2.7, "humidity": 66,
      "wind_mph": 11.5, "wind_gusts_mph": 28.4,
      "code": 0, "description": "Clear sky"
    },
    "19Crosstown": {
      "temp_f": 14.8, "feels_like_f": 3.0, "humidity": 57,
      "wind_mph": 12.4, "wind_gusts_mph": 33.3,
      "code": 0, "description": "Clear sky"
    }
  },
  "rooms": [
    {
      "room": "Philly Solarium", "temp_c": 5.3, "temp_f": 41.5,
      "humidity": 19, "mode": "HEAT", "hvac": "HEATING",
      "eco": "OFF", "setpoint_c": 13.89, "setpoint_f": 57.0,
      "connectivity": "ONLINE"
    }
  ]
}
```

**Weather format:** Per-structure dict keyed by structure name (`NEST_LOCATIONS` in `nest-location.conf`). Old snapshots with a flat `weather` dict (single `temp_f` at top level) are detected and rendered correctly — backwards compatible per-snapshot.

Rooms: Philly Solarium, Philly Living Room, Philly Bedroom, 19Crosstown Living Room. File-per-day structure acts as a natural date index.

## API Endpoints

| Endpoint | Description |
|----------|-------------|
| `GET /` | Embedded HTML dashboard |
| `GET /api/data?hours=24` | Snapshots for time range (default 24h, max 8760 = 1 year) |
| `GET /api/current` | Latest snapshot only |

**Data loading behavior:**
- Only reads JSONL files matching the requested date range
- `hours` parameter clamped: `min(max(1, hours), 8760)`
- For ranges > 7 days, downsamples to ~1 snapshot per hour (keeps closest to hour boundary)
- Graceful field fallback with `.get()` defaults — older records missing new fields won't crash

**Response format for `/api/data`:**
```json
{
  "meta": { "hours": 24, "count": 48, "downsampled": false },
  "snapshots": [ ... ]
}
```

## Dashboard UI

- **Structure filter buttons:** Both, Philly, Crosstown — filters room cards, weather cards, and all charts
- **Status cards:** Current temp, setpoint, HVAC status, humidity per room + outdoor weather per structure
- **Temperature chart** (line): Per-room temps + outdoor temp + setpoint lines (dotted)
- **Humidity chart** (line): Per-room + outdoor humidity
- **HVAC duty cycle chart** (bar): Heating percentage per hour per room
  - Calculation: for each hourly bucket, `count(hvac == "HEATING") / total_snapshots_in_bucket`
- **Time range buttons:** 24h, 7d, 30d, 1Y
- **Auto-refresh:** Every 5 minutes via `setInterval`
- **Dark mode** by default, respects `prefers-color-scheme`

**Room colors:**
| Room | Color |
|------|-------|
| Solarium | `#FF8C00` (orange) |
| Living Room | `#4A90D9` (blue) |
| Bedroom | `#8B5CF6` (purple) |
| Outside (Philly) | `#6B7280` (gray) |
| Outside (19Crosstown) | `#9CA3AF` (light gray) |

**CDN dependencies** (loaded in `<script>` tags):
- Chart.js 4.x
- Luxon 3.x
- chartjs-adapter-luxon 1.x

If CDN is unreachable, charts won't render but status cards still work (plain HTML from API data).

## LaunchAgent

Plist: `~/Library/LaunchAgents/ai.openclaw.nest-dashboard.plist`

| Key | Value | Why |
|-----|-------|-----|
| `RunAtLoad` | `true` | Start on login |
| `KeepAlive.SuccessfulExit` | `false` | Restart on crash, not clean SIGTERM |
| `Program` | `/usr/bin/python3` | System Python 3.9 (no dependencies needed) |

Logs: `~/.openclaw/logs/nest-dashboard.log` and `nest-dashboard.err.log`

The server handles SIGTERM/SIGINT for clean shutdown via `launchctl unload`.

## CLI Commands

```
nest dashboard           # Open in browser (macOS `open`)
nest dashboard start     # Load LaunchAgent
nest dashboard stop      # Unload LaunchAgent
nest dashboard restart   # Stop + start
nest dashboard status    # Show PID and port
```

Guards: if plist doesn't exist, prints install instructions.

## Access

- **Binds to:** `0.0.0.0:8550`
- **Intended access:** Tailscale network only (Mac Mini firewall is off but not exposed to public internet)
- **Tailscale IP:** `100.93.66.71` (as of 2026-02-08)
- **URL:** `http://100.93.66.71:8550/` or `http://localhost:8550/` on the Mac Mini

## Files (source repo)

All source files live in `/Users/dylanbochman/repos/dotfiles/`:

| File | Purpose |
|------|---------|
| `openclaw/nest-dashboard.py` | Server + embedded dashboard |
| `openclaw/ai.openclaw.nest-dashboard.plist` | LaunchAgent plist |
| `openclaw/nest-dashboard.md` | This file |
| `bin/nest` | CLI wrapper (dashboard subcommand added) |

## Deployment

```bash
# 1. Copy server script
scp dotfiles/openclaw/nest-dashboard.py dylans-mac-mini:~/.openclaw/bin/nest-dashboard.py

# 2. Copy LaunchAgent plist
scp dotfiles/openclaw/ai.openclaw.nest-dashboard.plist \
    dylans-mac-mini:~/Library/LaunchAgents/ai.openclaw.nest-dashboard.plist

# 3. Copy updated nest CLI
scp dotfiles/bin/nest dylans-mac-mini:/Users/dbochman/dotfiles/bin/nest

# 4. Load (or restart)
ssh dylans-mac-mini '/opt/homebrew/bin/nest dashboard start'
# or: ssh dylans-mac-mini '/opt/homebrew/bin/nest dashboard restart'
```

## Verification

```bash
# API works
curl http://localhost:8550/api/current
curl "http://localhost:8550/api/data?hours=24"

# LaunchAgent running
nest dashboard status

# Auto-restart on crash
kill -9 $(pgrep -f nest-dashboard.py)
sleep 3 && nest dashboard status   # should show new PID
```
