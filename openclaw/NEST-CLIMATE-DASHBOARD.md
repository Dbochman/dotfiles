# Home Climate Dashboard — Implementation Spec

## Status: v2.3 (2026-08-10)

Single-file Python HTTP server with embedded Chart.js UI. Monitors thermostats,
air conditioners, indoor air quality, and weather across two locations via four
heating/cooling systems and one local sensor. Serves at port 8550 on the Mac
Mini with home-LAN and Tailscale-tailnet access.

---

## System Overview

| Location | System | Devices | Source Tag |
|----------|--------|---------|------------|
| Cabin (Philly) | Nest (central HVAC) | Solarium, Living Room, Bedroom | `nest` |
| Cabin (Philly) | Midea (local AC) | Air Conditioner, Lil Air Conditioner | `midea` |
| Cabin (Philly) | Airthings Wave Enhance (local BLE) | Living Room | `airthings` |
| Crosstown (19Crosstown) | Cielo (minisplit AC) | Living Room, Basement, Dylan's Office, Bedroom | `cielo` |
| Crosstown (19Crosstown) | Mysa (baseboard heater) | Cat Room, Basement door, Movie room | `mysa` |

Weather data from Open-Meteo (free, no key) for both locations.

---

## Architecture

```
Mac Mini (dylans-mac-mini)
├── CLI: /opt/homebrew/bin/nest → ~/dotfiles/bin/nest
├── Dashboard Server: ~/.openclaw/bin/nest-dashboard.py (port 8550)
├── Mysa Wrapper: ~/.openclaw/bin/mysa-status.py
├── Midea Wrapper: ~/.openclaw/bin/midea-ac
├── Midea LAN Bindings: ~/.openclaw/midea-ac/bindings.json
├── Airthings Wrapper: ~/.openclaw/bin/airthings
├── Airthings Binding: ~/.openclaw/airthings/config.json
├── Airthings Runtime: ~/.openclaw/venvs/airthings-monitor
├── Camera Media: ~/.openclaw/bin/nest-camera-snap.py
├── History: ~/.openclaw/nest-history/YYYY-MM-DD.jsonl
├── Presence: ~/.openclaw/presence/state.json + history/
├── Token Cache: ~/.cache/nest-sdm/ (access_token, credentials)
├── Mysa Token Cache: ~/.config/mysotherm/ (AWS Cognito)
└── Cielo CLI: cielo (wrapper at ~/.openclaw/bin/cielo)
```

### LaunchAgents

| Label | Interval | Command | Logs |
|-------|----------|---------|------|
| `ai.openclaw.nest-snapshot` | 30 min (StartInterval) | `/opt/homebrew/bin/nest snapshot` | `~/.openclaw/logs/nest-cron.{log,err.log}` |
| `ai.openclaw.nest-dashboard` | KeepAlive | `python3 ~/.openclaw/bin/nest-dashboard.py` | `~/.openclaw/logs/nest-dashboard.{log,err.log}` |

The snapshot agent shows `-` for PID in `launchctl list` — this is normal (runs and exits).

---

## Data Collection Pipeline

### Snapshot Command (`nest snapshot`)

Runs every 30 minutes. Queries four climate systems, the local Airthings
monitor, and weather; merges them into a single JSONL record; and appends it to
the daily history file.

**Pipeline:**

1. **Nest SDM API** — OAuth 2.0 bearer token, queries `GET /devices` for thermostat traits
2. **Open-Meteo Weather API** — Per-structure lat/lon, current conditions
3. **Cielo CLI** — `cielo status --json` for minisplit AC units
4. **Mysa API** — `~/.openclaw/bin/mysa-status.py` via mysotherm library
5. **Midea local LAN** — `midea-ac status --json` using enrolled per-device token/key bindings
6. **Airthings local BLE** — `airthings status --json` using the exact protected Wave Enhance binding and a five-minute cache
7. **Merge** — All devices assembled into `rooms[]` array with structure prefix and `source` tag
8. **Write** — Append JSON line to `~/.openclaw/nest-history/YYYY-MM-DD.jsonl`
9. **Prune** — Delete history files older than 1000 days

### Room Name Convention

Raw room names include a structure prefix for multi-structure disambiguation:

| Raw Name | Structure | Display Name (filtered) | Display Name (Both) |
|----------|-----------|------------------------|---------------------|
| `Solarium` | Philly | Solarium | Solarium |
| `Living Room` | Philly | Living Room | Living Room (Cabin) |
| `19Crosstown Living Room` | 19Crosstown | Living Room | Living Room (XTown) |
| `19Crosstown Dylan's Office` | 19Crosstown | Dylan's Office | Dylan's Office |
| `Air Conditioner` | Philly | Air Conditioner | Air Conditioner |
| `Lil Air Conditioner` | Philly | Lil Air Conditioner | Lil Air Conditioner |

Rooms without a prefix default to Philly (Cabin). Crosstown rooms are prefixed with `19Crosstown `.

When the same structure and short room name are reported by multiple sources, the dashboard adds a source suffix and keeps independent time series. For example, the two Crosstown Living Room feeds render as `Living Room (Nest)` and `Living Room (Cielo)` rather than merging their samples. The suffix is conditional, so rooms with a single source keep their concise names.

**Note:** The apostrophe in "Dylan's Office" is Unicode U+2019 (right single quotation mark `'`), not ASCII U+0027 (`'`). This comes from the Nest SDM API's room naming. The dashboard COLORS map includes both variants for safety.

---

## JSONL Snapshot Schema

```json
{
  "timestamp": "2026-03-08T19:20:15Z",
  "weather": {
    "Philly": {
      "temp_f": 29.7,
      "feels_like_f": 24.1,
      "humidity": 94,
      "wind_mph": 2.5,
      "wind_gusts_mph": 5.0,
      "code": 3,
      "description": "Overcast"
    },
    "19Crosstown": { ... }
  },
  "rooms": [
    {
      "room": "Solarium",
      "temp_c": 17.1,
      "temp_f": 62.8,
      "humidity": 52,
      "mode": "HEAT",
      "hvac": "OFF",
      "eco": "OFF",
      "setpoint_c": 21.1,
      "setpoint_f": 70.0,
      "connectivity": "ONLINE",
      "source": "nest"
    },
    {
      "room": "19Crosstown Basement",
      "temp_f": 61.0,
      "temp_c": 16.1,
      "humidity": 48,
      "mode": "cool",
      "hvac": "OFF",
      "eco": "OFF",
      "setpoint_f": 76.0,
      "setpoint_c": 24.4,
      "connectivity": "ONLINE",
      "source": "cielo"
    },
    {
      "room": "19Crosstown Cat Room",
      "temp_f": 65.8,
      "temp_c": 18.7,
      "humidity": 41,
      "mode": "heat",
      "hvac": "HEATING",
      "eco": "OFF",
      "setpoint_f": 65.8,
      "setpoint_c": 18.8,
      "connectivity": "ONLINE",
      "duty_pct": 5,
      "source": "mysa"
    }
  ]
}
```

### Field Differences by Source

| Field | Nest | Cielo | Mysa | Midea | Airthings |
|-------|------|-------|------|-------|-----------|
| `mode` | `HEAT`, `COOL`, `HEATCOOL`, `OFF` | `heat`, `cool`, `auto`, `fan`, `dry` | `heat`, `off` | `auto`, `cool`, `dry`, `heat`, `fan` | `sensor` |
| `hvac` | `HEATING`, `COOLING`, `OFF` | Mapped: `HEATING`, `COOLING`, `AUTO`, `FAN`, `DRY`, `OFF` | `HEATING` if duty > 0, else `OFF` | Mapped active mode, or `OFF` when power is off | absent; excluded from HVAC duty |
| `duty_pct` | absent | absent | 0–100 (real heater duty cycle) | absent; the duty chart derives active percentage from `fan` while power is on | absent |
| `connectivity` | from API trait | `ONLINE`/`OFFLINE` from `deviceStatus` | always `ONLINE` | Local discovery/readback result | Exact-device BLE read result |
| Temperature | `ambientTemperatureCelsius` (C primary) | `latEnv.temp` (F primary) | `CorrectedTemp` (C primary, converted) | Local indoor sensor (F normalized) | Local Wave Enhance sensor (C primary, converted) |
| Extra telemetry | — | — | — | `power`, `fan`, `power_w`, `error_code` | `co2_ppm`, `voc_ppb`, `pressure_hpa`, `noise_dba`, `light_lux`, `battery_percent`, `air_quality` |

---

## Dashboard Server (`nest-dashboard.py`)

### Server

- Python stdlib HTTP server (no dependencies)
- ThreadingMixIn for concurrent requests
- Binds `0.0.0.0:8550`
- SIGTERM/SIGINT graceful shutdown
- CORS enabled (`Access-Control-Allow-Origin: *`)

### API Endpoints

| Endpoint | Method | Query Params | Description |
|----------|--------|-------------|-------------|
| `/` | GET | — | Embedded HTML dashboard (single-page app) |
| `/api/data` | GET | `hours=N` (default 24, max 8760) | Snapshots + presence history for time range |
| `/api/current` | GET | — | Latest snapshot only |
| `/api/presence` | GET | — | Current canonical vacancy state from `state.json`; dashboard roster is limited to Dylan and Julia |

### Response: `/api/data?hours=24`

```json
{
  "meta": {
    "hours": 24,
    "count": 48,
    "downsampled": false
  },
  "snapshots": [ ... ],
  "presence": [ ... ]
}
```

### Downsampling

For time ranges > 7 days (168 hours), snapshots are downsampled to ~1 per hour. The algorithm keeps the snapshot closest to each hour boundary (smallest minute value).

---

## Dashboard UI

### Layout

1. **Vacancy Cards** — Per-location canonical state (Occupied/Confirmed Vacant/Possibly Vacant), Dylan/Julia names, duration since last state change
2. **Status Cards** — Current temperature, setpoint, HVAC status, humidity, source tag per room; Midea cards add eco, fan, and live wattage, while the Airthings card adds CO2, VOC, pressure, noise, light, battery, cache state, and the overall air-quality band
3. **Structure Filter** — Both | Cabin | Crosstown
4. **Time Range** — 24h | 7d | 30d | 1Y
5. **Temperature Chart** — Line chart with setpoint overlays (dotted lines)
6. **Humidity Chart** — Line chart
7. **CO2 and VOC Charts** — Independent Wave Enhance line charts in ppm and ppb
8. **HVAC Duty Cycle Chart** — Bar chart (hourly averages); read-only Airthings rows are excluded

### Color Scheme

Cabin rooms use a **cool** palette; Crosstown rooms use a **warm** palette. Outside temps use light variants.

| Room (internal color key) | Legend Label | Hex | Palette |
|---------------------------|-------------|-----|---------|
| Solarium | Solarium | `#3B82F6` | Cool (blue) |
| Living Room (Cabin) | Living Room | `#8B5CF6` | Cool (purple) |
| Bedroom (Cabin) | Bedroom | `#06B6D4` | Cool (cyan) |
| Outside (Cabin) | Outside | `#C9E2FE` | Cool (light blue) |
| Living Room (XTown) | Living Room | `#F97316` | Warm (orange) |
| Bedroom (XTown) | Bedroom | `#FB7185` | Warm (pink) |
| Dylan's Office | Dylan's Office | `#F59E0B` | Warm (amber) |
| Basement | Basement | `#EF4444` | Warm (red) |
| Cat Room | Cat Room | `#EC4899` | Warm (pink) |
| Basement door | Basement door | `#F97316` | Warm (orange) |
| Movie room | Movie room | `#FB923C` | Warm (light orange) |
| Outside (Crosstown) | Outside | `#FEDDBA` | Warm (peach) |

**Structure-aware color resolution:** When viewing a single structure, `roomColor()` checks `COLORS[name + ' (XTown)']` or `COLORS[name + ' (Cabin)']` before the bare name — prevents colliding rooms (Living Room, Bedroom) from inheriting the wrong palette.

### Room Name Disambiguation

Internally, rooms that collide across structures get a suffix for color/grouping resolution:
- `Living Room (Cabin)` / `Living Room (XTown)`
- `Bedroom (Cabin)` / `Bedroom (XTown)`

**Chart legends** always show short names without location identifiers (e.g., `Bedroom`, `Living Room`, `Outside`). The grouped legend headers (Cabin / Crosstown) provide the location context.

**Chart tooltips** (on hover) show the full name with location (e.g., `Bedroom (Cabin)`, `Dylan's Office (Crosstown)`). In single-structure views, the location is appended to all rooms for clarity.

Unique rooms (Solarium, Cat Room, Dylan's Office) keep short names in all views. Helper functions:
- `displayNameFull()` — internal key with `(Cabin)`/`(XTown)` suffix for colliding names
- `displayNameLegend()` — strips location suffix for chart legend labels
- `displayNameTooltip()` — adds location context for chart hover tooltips

### HVAC Duty Cycle Computation

For each room, snapshots are bucketed by hour. Duty cycle per bucket:
- **Mysa:** Uses real `duty_pct` (0–100)
- **Midea:** Uses fan speed as active percentage while HVAC is active: silent 20%, low 40%, medium 60%, high 80%, and full 100%. Off is 0%; auto or unknown fan data retains the binary 100% active fallback.
- **Nest/Cielo:** Binary 100/0 based on `hvac` status (active = 100, OFF = 0)
- **Displayed:** `Math.round(sum(duty) / count(snapshots))` per hourly bucket

### Presence Overlay

When a single structure is selected, temperature/humidity/duty charts render background bands:
- Green (7% opacity) — Occupied
- Gray (6% opacity) — Confirmed Vacant
- Amber (5% opacity) — Possibly Vacant

The badge and overlay map directly from each location's canonical `occupancy`
enum. They never infer an additional state from the roster. Potato's Fi-collar
entry is informational, is removed from the dashboard API response, and cannot
affect vacancy presentation.

### Technical Stack

| Component | Library | Source |
|-----------|---------|--------|
| Charts | Chart.js 4.x | CDN |
| Time axis | Luxon 3.x + chartjs-adapter-luxon | CDN |
| Styling | Dark-first CSS, `prefers-color-scheme` for light mode | Embedded |
| Refresh | `setInterval(refresh, 5 * 60 * 1000)` | Embedded |

---

## CLI Commands (`nest`)

```
nest status                    Show all thermostats + outdoor weather
nest weather                   Show current outdoor weather only
nest set <room> <temp°F>       Set temperature (e.g. nest set bedroom 72)
nest mode <room> <HEAT|OFF>    Set thermostat mode
nest eco <room> [on|off]       Toggle eco mode
nest camera snap [room] [out]  Attended display-name camera diagnostic
nest camera snap-config <alias> <out>  Exact protected camera capture
nest camera clip-config <alias> <seconds> <out>  Exact 1-30s live clip
nest snapshot                  Record current state + weather to history
nest history [hours] [room]    Show history summary (default: 24h, all rooms)
nest dashboard [open|start|stop|restart|status]
nest raw                       Raw JSON device dump
```

Room names are fuzzy-matched case-insensitively by substring (e.g., "bed" matches "Bedroom").

---

## Data Sources

### 1. Nest SDM API

- **Base URL:** `https://smartdevicemanagement.googleapis.com/v1/enterprises/{project_id}`
- **Auth:** OAuth 2.0 bearer token (60-min expiry, auto-refresh)
- **Credentials:** 1Password vault "OpenClaw", item "Google Nest" (fields: `clientID`, `client_secret`, `refresh_token`, `project_id`)
- **Token cache:** `~/.cache/nest-sdm/access_token` (55-min TTL)
- **Credential cache:** `~/.cache/nest-sdm/{clientid,client_secret,project_id}` (1-year TTL)

**API calls:**
- `GET /structures` — List homes (for structure map)
- `GET /devices` — All devices with traits
- `POST /devices/{id}:executeCommand` — Set temp/mode/eco

**Thermostat traits queried:**
- `Temperature` (ambientTemperatureCelsius)
- `Humidity` (ambientHumidityPercent)
- `ThermostatMode` (mode)
- `ThermostatHvac` (status)
- `ThermostatEco` (mode)
- `ThermostatTemperatureSetpoint` (heatCelsius / coolCelsius)
- `Connectivity` (status)

### 2. Open-Meteo Weather API

- **URL:** `https://api.open-meteo.com/v1/forecast`
- **No API key required**
- **Params:** `current=temperature_2m,relative_humidity_2m,wind_speed_10m,wind_gusts_10m,weather_code,apparent_temperature&temperature_unit=fahrenheit&wind_speed_unit=mph`
- **Per-structure coordinates** from `~/.openclaw/nest-location.conf`:
  ```bash
  NEST_LOCATIONS="Philly:42.6021:-72.1510 19Crosstown:42.3601:-71.0589"
  ```

**WMO weather code mapping:** 0=Clear, 1=Mainly clear, 2=Partly cloudy, 3=Overcast, 45/48=Fog, 51–55=Drizzle, 61–67=Rain, 71–77=Snow, 80–82=Showers, 85–86=Snow showers, 95–99=Thunderstorm.

### 3. Cielo AC (Minisplit)

- **CLI:** `cielo status --json`
- **Returns:** JSON array of device objects
- **Config:** `~/.config/cielo/config.json`
- **Token refresh:** Every 30 min via the locked, atomic `cielo-auth.py` `/web/token/refresh/1` client under `com.openclaw.cielo-refresh`
- **Fields used:** `deviceName`, `latEnv.temp`, `latEnv.humidity`, `latestAction.mode`, `latestAction.power`, `latestAction.temp`, `deviceStatus`

### 4. Mysa Baseboard Heaters

- **Wrapper:** `~/.openclaw/bin/mysa-status.py`
- **Invocation:** `mysa`
- **Library:** mysotherm (cloned at `~/.openclaw/mysa/mysotherm`, venv at `~/.openclaw/mysa/venv`)
- **Auth:** AWS Cognito tokens cached at `~/.config/mysotherm` (INI format)
- **API base:** `https://app-prod.mysa.cloud` — endpoints: `/devices`, `/devices/state`, `/devices/firmware`
- **Auth header:** bare JWT `id_token` (no "Bearer" prefix)
- **Token expiry:** ~30 days without use. The wrapper first renews from managed `MYSA_USERNAME`/`MYSA_PASSWORD` values loaded from `~/.openclaw/.secrets-cache`; `mysa --login` from an interactive Mini terminal remains the fallback when those values are unavailable or rejected

**Key fields from Mysa:**
- `CorrectedTemp` → `temp_f` (thermostat-corrected ambient reading, used in snapshots)
- `SensorTemp` → `sensor_temp_f` (raw sensor, reads higher due to baseboard proximity)
- `SetPoint` → `setpoint_f`
- `Humidity` → `humidity`
- `Duty` → `duty_pct` (0–1 ratio, converted to 0–100 %)
- Model: BB-V1-1 (Baseboard V1)

### 5. Airthings Wave Enhance

- **CLI:** `airthings status --json`
- **Transport:** Local BLE through Airthings' maintained `airthings-ble` library; no account, token, or cloud API
- **Binding:** One exact `cabin-living-room-airthings` device in an owner-only `~/.openclaw/airthings/config.json`
- **Runtime:** Locked Python environment at `~/.openclaw/venvs/airthings-monitor`; BLE access is serialized, successful readings are cached for five minutes, and stale or explicitly refreshed reads launch the authorized Homebrew `Python.app` through Launch Services so unattended callers do not depend on Terminal's privacy grant
- **Fields:** CO2, VOC, temperature, humidity, pressure, ambient noise, light, and calculated battery percentage
- **Bands:** CO2 is good below 800 ppm and poor at 1000 ppm or above; VOC is good below 250 ppb and poor at 2000 ppb or above; the intermediate band is fair
- **Boundary:** Readings are dashboard/environmental context only. They do not establish occupancy, publish home events, or authorize HVAC actions.

#### Airthings history backfill

`~/.openclaw/bin/airthings-history-import` accepts the Airthings dashboard's
UTC CSV export. It is attended and dry-run-first; mutation requires both
`AIRTHINGS_ALLOW_HISTORY_IMPORT=1` and `--apply`. It validates the source
schema and values, skips existing Airthings timestamps, preserves other room
records, backs up only affected daily files under the protected Airthings
state tree, sorts the merged JSONL, and writes mode-`0600` files atomically.
Imported samples carry `history_origin: airthings_csv_v1` and remain in the
same Cabin Living Room series as live BLE samples.

The consumer cloud API currently covers device inventory and latest samples,
not historical series. A future HAR-derived dashboard client may automate CSV
exports and feed this importer, but it can retrieve only measurements already
synchronized to Airthings' cloud. A remote Wave still needs a nearby phone,
compatible Airthings gateway/View device, or local BLE proxy.

### 6. Presence Detection

- **State file:** `~/.openclaw/presence/state.json`
- **History:** `~/.openclaw/presence/history/YYYY-MM-DD.jsonl`
- **Detection methods:**
  - Cabin: Starlink gRPC API (`192.168.1.1:9000`)
  - Crosstown: ARP scan (`192.168.165.0/24`) + hostname matching
- **Model:** Sticky/arrival-based — once detected, person stays at location until detected at the other
- **Occupancy states:** `occupied`, `confirmed_vacant`, `possibly_vacant` (scans stale > 30 min)
- **Roster:** Only Dylan and Julia are displayed; Potato is informational and excluded from vacancy decisions
- **Split occupancy:** If Dylan and Julia are at different houses, both locations use the canonical `occupied` state and each card shows the person there
- **State duration:** Each location tracks `stateChangedAt` (ISO timestamp) — carried forward from previous evaluation, reset when occupancy changes. Dashboard displays human-friendly duration ("for 3h 15min", "for 2d 5h")

---

## Camera Stills and Live Clips

```bash
# Attended display-name diagnostic only:
nest camera snap [room] [output_path]
# Defaults: room=kitchen, output=~/.openclaw/workspace/camera-snap.jpg

# Automation and agent boundary (exact canonical alias only):
nest camera snap-config <alias> <output_path>
nest camera clip-config <alias> <seconds> <output_path>  # 1-30 seconds
```

Unattended callers must use the exact-resource `*-config` commands; mutable
Google display-name lookup fails closed without an interactive terminal. The
on-demand agent flow wraps these commands with `nest-camera-image` to stage,
validate, deliver, and clean up ephemeral owner-only JPEG or MP4 media.
`capture-video` measures the requested duration from the first received video
frame and writes an iMessage-compatible H.264/yuv420p MP4. It records only the
live stream and cannot retrieve historical Nest Aware footage.

- **Implementation:** `~/.openclaw/bin/nest-camera-snap.py`
- **Protocol:** WebRTC via Nest SDM `CameraLiveStream` trait
- **Video codec:** Offer only H.264 profile `42e01f`. Offering both aiortc
  baseline profiles can make Nest duplicate payload IDs in its SDP answer;
  ICE/DTLS and inbound RTP then succeed, but no complete frame is decoded.
- **Still process:** Create RTCPeerConnection → POST SDP offer to SDM API → receive answer → wait for first video frame (15s timeout) → save JPEG (quality=90) → POST stop command
- **Clip process:** Use the same protected WebRTC negotiation → begin timing on the first decoded frame → record 1-30 seconds through PyAV/libx264 → atomically publish MP4 → POST stop command
- **Requirements:** Python aiortc, PyAV/libx264, Pillow

---

## Configuration Files

| File | Purpose |
|------|---------|
| `~/.openclaw/nest-location.conf` | Lat/lon per structure for weather |
| `~/.cache/nest-sdm/` | OAuth access token + credential caches |
| `~/.config/mysotherm/` | Mysa/Cognito auth tokens |
| `~/.config/cielo/config.json` | Cielo API credentials |
| `~/.openclaw/airthings/config.json` | Exact owner-only Wave Enhance BLE binding |
| `~/.openclaw/airthings/state/` | Owner-only BLE lock and five-minute status cache |
| `~/.openclaw/nest-history/` | Daily JSONL snapshot files |
| `~/.openclaw/presence/` | Presence state + history |
| `~/.openclaw/logs/nest-*.log` | Dashboard and snapshot logs |

---

## Files Reference

| File | Location | Purpose |
|------|----------|---------|
| `bin/nest` | Dotfiles repo + `/opt/homebrew/bin/nest` (symlink) | Main CLI (status, set, snapshot, dashboard mgmt) |
| `openclaw/bin/nest-dashboard.py` | `~/.openclaw/bin/` on Mini | HTTP server + embedded HTML/JS dashboard |
| `openclaw/bin/mysa-status.py` | `~/.openclaw/bin/` on Mini | Mysa API wrapper (JSON output) |
| `openclaw/bin/airthings` | `~/.openclaw/bin/` and `/opt/homebrew/bin/` | Read-only Airthings runtime wrapper |
| `openclaw/bin/airthings-history-import` | `~/.openclaw/bin/` on Mini | Attended, guarded Airthings CSV history importer |
| `openclaw/skills/airthings-monitor/` | `~/.openclaw/skills/airthings-monitor/` | OpenClaw skill, BLE reader, and locked dependency manifest |
| `openclaw/bin/nest-camera-snap.py` | `~/.openclaw/bin/` on Mini | WebRTC camera still and short-clip capture |
| `openclaw/launchagents/ai.openclaw.nest-dashboard.plist` | `~/Library/LaunchAgents/` on Mini | Dashboard KeepAlive service |
| `openclaw/launchagents/ai.openclaw.nest-snapshot.plist` | `~/Library/LaunchAgents/` on Mini | 30-min snapshot cron |

---

## Deployment

### Copy files to Mini

```bash
# Dashboard server
scp openclaw/bin/nest-dashboard.py dbochman@dylans-mac-mini:~/.openclaw/bin/

# CLI (symlinked from dotfiles)
scp bin/nest dbochman@dylans-mac-mini:~/dotfiles/bin/nest

# Mysa wrapper
scp openclaw/bin/mysa-status.py dbochman@dylans-mac-mini:~/.openclaw/bin/

# Nest WebRTC camera capture helper
scp openclaw/bin/nest-camera-snap.py dbochman@dylans-mac-mini:~/.openclaw/bin/

# Protected on-demand media wrapper and skill
scp openclaw/bin/nest-camera-image dbochman@dylans-mac-mini:~/.openclaw/bin/
scp openclaw/skills/nest-camera/SKILL.md \
  dbochman@dylans-mac-mini:~/.openclaw/skills/nest-camera/SKILL.md

# Restart dashboard
ssh dbochman@dylans-mac-mini "launchctl kickstart -k gui/501/ai.openclaw.nest-dashboard"
```

### Verify

```bash
ssh dbochman@dylans-mac-mini "curl -s http://localhost:8550/api/current" | python3 -m json.tool
ssh dbochman@dylans-mac-mini "/opt/homebrew/bin/nest snapshot"
```

---

## History & Retention

- **Snapshot interval:** 30 minutes (48/day)
- **File size:** ~2.5 KB/snapshot → ~120 KB/day
- **Retention:** ~1000 days (auto-pruned by `nest snapshot`)
- **Max query range:** 8760 hours (1 year) via `/api/data?hours=8760`
- **Downsampling:** Engaged beyond 7 days — keeps ~1 snapshot/hour

---

## Known Issues

- **OAuth consent screen:** If GCP project is in "Testing" mode, refresh tokens expire after 7 days. Must be set to "In production" for long-lived tokens.
- **Mysa token expiry:** Cognito tokens expire after extended inactivity. Managed credentials in `~/.openclaw/.secrets-cache` normally renew the session automatically; use interactive `mysa --login` on the Mini if managed renewal fails.
- **Cielo token staleness:** Token refresh depends on `com.openclaw.cielo-refresh`. If `cielo-auth.py check` reports an unproven/rejected chain, use `cielo-reauth --attended start`, complete the visible login, and run `cielo-reauth finish`; post-login access-token capture alone is not a durable repair.
- **Airthings Bluetooth permission:** macOS must list and enable `/opt/homebrew/opt/python@3.14/Frameworks/Python.framework/Versions/3.14/Resources/Python.app` under Privacy & Security → Bluetooth. Terminal's separate grant is insufficient for the scheduled snapshot. The wrapper refreshes through that app identity and reports `bluetooth_unauthorized` without exposing the device identifier if the grant is absent.
- **1Password over SSH:** `op read` hangs under launchd. Credentials are pre-cached to `~/.cache/nest-sdm/` files with 1-year TTL.
- **Camera snap timing:** WebRTC handshake takes 5–10 seconds; camera must be online with streaming enabled.
- **Dylan's Office apostrophe:** Nest API uses Unicode U+2019 (smart quote) in the room name. COLORS map includes both U+2019 and U+0027 variants.
