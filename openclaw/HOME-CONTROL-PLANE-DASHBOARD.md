# Home Control Plane Dashboard

**Port 8558**  
**URL:** `http://dylans-mac-mini:8558`  
**Service:** `ai.openclaw.home-dashboard`

Unified control plane for smart home devices across Crosstown and Cabin. The dashboard is a single-page control surface with grouped device cards, fast cached status, per-device refresh, and inline command feedback.

## Layout

Cards are grouped into collapsible sections:

1. **Lighting** — Hue Crosstown, Hue Cabin
2. **Temperature** — Nest, Cielo, Mysa, Eight Sleep
3. **Security** — August Lock, Ring Doorbell, Nest Cameras (Kitchen @ Cabin; Laundry + Living Room @ Crosstown)
4. **Pets** — Litter-Robot, Petlibro, Dog Walk
5. **Misc** — TV, Speakers, Cabin Speakers, Roombas (Crosstown + Cabin)

Command feedback appears inline under the section header for the card that triggered the action.

## API and Runtime

Server implementation: `openclaw/bin/home-dashboard.py` (deployed to `~/.openclaw/bin/home-dashboard.py`)

Routes:

- `GET /` — embedded HTML dashboard
- `GET /api/status` — non-blocking cached status
- `GET /api/status?refresh=true` — force refresh all collectors
- `GET /api/status/<device>` — refresh one collector
- `POST /api/command` — execute a control action
- `GET /api/camera-snap/<name>` — serve JPEG snapshot
- `GET /api/presence` — presence state

Runtime behavior:

- 60s status cache TTL
- startup precache of all collectors in parallel
- 5-minute background refresh for most collectors
- `speakers` and `cabin_speakers` are excluded from background refresh to avoid Cast chimes on idle devices
- command timeout is 30 seconds
- startup loads env vars from `~/.openclaw/.secrets-cache`

## Controls

All controls use selectors with predefined room/device values.

### Hue (Crosstown and Cabin)

- Per-room controls: `On`, `Off`, `Set Brightness`, `Set Color`
- **All Lights mode:** room selector includes `All Lights`
  - `On` maps to Hue CLI `all-on`
  - `Off` maps to Hue CLI `all-off`
  - brightness and color inputs are disabled in this mode

### Other Key Controls

- **Nest:** set temp, set mode, eco on/off
- **Cielo:** on/off, set temp, set mode
- **August:** lock/unlock
- **Ring/Nest Cameras:** take snapshots — Ring (Crosstown + Cabin doorbells), Nest (Kitchen @ Cabin, Laundry + Living Room @ Crosstown). Nest device discovery uses customName as a fallback to room name for cameras whose Google Home room doesn't match the dashboard label (e.g. "laundry camera" lives in the "Garage" room). Adding new Nest devices requires `nest reauth` to re-run Google Device Access OAuth consent.
- **Litter-Robot:** clean/reset
- **Petlibro:** manual feed
- **TV:** power on/off
- **Speakers/Cabin Speakers:** volume and mute/stop actions
- **Roombas (both locations):** start/stop/dock

## Data Sources

- Presence state: `~/.openclaw/presence/state.json`
- Nest latest snapshot: `~/.openclaw/nest-history/*.jsonl`
- Dog walk state: `~/.openclaw/dog-walk/state.json`
- Camera snapshots: `~/.openclaw/camera-snaps/*.jpg`
- Device CLIs: `hue`, `nest`, `cielo`, `mysa`, `august`, `crosstown-roomba`, `roomba`, `samsung-tv`, `speaker`, `litter-robot`, `petlibro`, `8sleep`, `ring`

## Files and Logs

- Server source: `openclaw/bin/home-dashboard.py`
- LaunchAgent: `openclaw/launchagents/ai.openclaw.home-dashboard.plist`
- Runtime script: `~/.openclaw/bin/home-dashboard.py`
- Logs:
  - `~/.openclaw/logs/home-dashboard.log`
  - `~/.openclaw/logs/home-dashboard.err.log`

## Troubleshooting

Check service:

```bash
ssh mac-mini "launchctl list | grep ai.openclaw.home-dashboard"
```

Restart service (KeepAlive auto-restarts):

```bash
ssh mac-mini "launchctl stop ai.openclaw.home-dashboard"
```

Smoke test API:

```bash
ssh mac-mini "python3 - <<'PY'
import urllib.request, json
with urllib.request.urlopen('http://127.0.0.1:8558/api/status', timeout=20) as r:
    obj = json.loads(r.read().decode('utf-8','ignore'))
print(r.status, obj.get('meta', {}))
PY"
```
