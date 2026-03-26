# Climate Dashboard

Self-hosted web dashboard for visualizing thermostat + weather history data across all heating/cooling systems: Nest (central HVAC), Cielo (minisplit AC), and Mysa (baseboard heaters).

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

## Data Sources

The `nest snapshot` LaunchAgent runs every 30 minutes, collecting data from three systems and writing unified JSONL to `~/.openclaw/nest-history/YYYY-MM-DD.jsonl`.

| Source | Devices | Location | How |
|--------|---------|----------|-----|
| **Nest** (SDM API) | Solarium, Living Room, Bedroom | Cabin (Philly) | Google Nest SDM REST API |
| **Cielo** (cli.js) | Basement, Living Room, Dylan's Office, Bedroom | Crosstown | `cielo-cli status --json` |
| **Mysa** (mysotherm) | Cat Room, Basement door, Movie room | Crosstown | `mysa-status.py` via REST API |

Each room entry has a `source` field (`"nest"`, `"cielo"`, or `"mysa"`) and Crosstown rooms are prefixed with `19Crosstown`.

```json
{
  "timestamp": "2026-03-06T21:00:40Z",
  "weather": {
    "Philly": { "temp_f": 29.7, "humidity": 94, "description": "Overcast" },
    "19Crosstown": { "temp_f": 32.9, "humidity": 98, "description": "Fog" }
  },
  "rooms": [
    { "room": "Solarium", "temp_f": 62.8, "hvac": "OFF", "source": "nest" },
    { "room": "19Crosstown Basement", "temp_f": 61.0, "hvac": "OFF", "source": "cielo" },
    { "room": "19Crosstown Cat Room", "temp_f": 65.6, "hvac": "HEATING", "source": "mysa", "duty_pct": 45 }
  ]
}
```

**Mysa-specific fields:** `duty_pct` (0-100) gives real heater duty cycle. Other sources use binary HEATING/OFF.

**Weather format:** Per-structure dict keyed by structure name. Old flat-dict snapshots are backwards compatible.

File-per-day structure acts as a natural date index.

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
  - Mysa rooms: uses real `duty_pct` (weighted average per hour)
  - Nest/Cielo rooms: binary 100%/0% based on HVAC status (HEATING vs OFF)
- **Time range buttons:** 24h, 7d, 30d, 1Y
- **Auto-refresh:** Every 5 minutes via `setInterval`
- **Dark mode** by default, respects `prefers-color-scheme`

**Room name disambiguation:** When viewing "Both" structures, rooms that exist in both locations get a suffix — e.g., "Bedroom (Cabin)" and "Bedroom (XTown)". Rooms unique to one structure (Solarium, Basement, Dylan's Office) keep their short name. When viewing a single structure, all names are short. This is automatic based on collision detection across structures.

**Room colors:**
| Room | Color | Source |
|------|-------|--------|
| Solarium | `#FF8C00` (orange) | Nest |
| Living Room | `#4A90D9` (blue) | Nest / Cielo |
| Bedroom | `#8B5CF6` (purple) | Nest / Cielo |
| Basement | `#14B8A6` (teal) | Cielo |
| Dylan's Office | `#F59E0B` (amber) | Cielo |
| Cat Room | `#EC4899` (pink) | Mysa |
| Basement door | `#06B6D4` (cyan) | Mysa |
| Movie room | `#84CC16` (lime) | Mysa |
| Outside (Cabin) | `#6B7280` (gray) | Weather |
| Outside (Crosstown) | `#9CA3AF` (light gray) | Weather |

Disambiguated rooms (e.g., "Bedroom (XTown)") get a hue-shifted variant of the base color so both are visually distinct in charts.

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
| `openclaw/launchagents/ai.openclaw.nest-dashboard.plist` | LaunchAgent plist |
| `openclaw/nest-dashboard.md` | This file |
| `bin/nest` | CLI wrapper (dashboard subcommand added) |

## Deployment

```bash
# 1. Copy server script
scp dotfiles/openclaw/nest-dashboard.py dylans-mac-mini:~/.openclaw/bin/nest-dashboard.py

# 2. Copy LaunchAgent plist
scp dotfiles/openclaw/launchagents/ai.openclaw.nest-dashboard.plist \
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
