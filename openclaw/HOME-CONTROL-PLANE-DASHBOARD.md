# Home Control Plane Dashboard

**Port 8558**  
**URL:** `http://dylans-mac-mini:8558`  
**Service:** `ai.openclaw.home-dashboard`

Unified control plane for smart home devices across Crosstown and Cabin. The dashboard is a single-page control surface with grouped device cards, fast cached status, per-device refresh, and inline command feedback. It is reachable on the home LAN and Tailscale tailnet.

## Layout

Cards are grouped into collapsible sections:

1. **Lighting** — Hue Crosstown, Hue Cabin
2. **Temperature** — Nest, Midea AC, Cielo, Mysa, Eight Sleep at both homes
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
- `POST /api/command` — execute a bearer-protected control action
- `GET /api/camera-snap/<name>` — serve JPEG snapshot
- `GET /api/presence` — presence state

Runtime behavior:

- 60s status cache TTL
- startup precache of all collectors in parallel
- 5-minute background refresh for most collectors
- `speakers` and `cabin_speakers` are excluded from background refresh to avoid Cast chimes on idle devices
- Cabin Roomba status is projected from the dedicated Roomba dashboard's
  bounded local API. That service caches Assistant checks for 15 minutes so
  this dashboard does not duplicate calls or exhaust the daily request quota.
- command timeout is 30 seconds
- startup loads env vars from `~/.openclaw/.secrets-cache`

### LAN trust and mutation protection

The service continues to listen on `0.0.0.0` so status reads and the dashboard
remain available on the trusted LAN and Tailscale tailnet. Read routes are not
account-authenticated.

Control mutations have an additional browser boundary:

- each server process generates a fresh bearer token and embeds it in the
  no-store dashboard page;
- dashboard JavaScript sends that token in the `Authorization` header for
  `POST /api/command`;
- browser requests with an `Origin` header must match the request's `Host`;
- responses do not advertise wildcard CORS, so another website cannot use the
  mutation endpoint through a cross-origin browser request;
- the dashboard refuses cross-origin framing to prevent clickjacking; and
- a service restart invalidates tokens in already-open pages, so refresh an old
  tab before sending another command.

This is CSRF-style protection for a trusted network, not per-user login. Anyone
who can load the dashboard page from the trusted LAN can also obtain its current
mutation token and operate the exposed controls. Header-only clients may omit
`Origin`, but must supply the bearer token from the current process.

## Controls

All controls use selectors with predefined room/device values.

### Hue (Crosstown and Cabin)

- Per-room controls: `On`, `Off`, `Set Brightness`, `Set Color`
- **All Lights mode:** room selector includes `All Lights`
  - `On` maps to Hue CLI `all-on`
  - `Off` maps to Hue CLI `all-off`
  - brightness and color inputs are disabled in this mode
- Each Hue card also lists standing Hue automations with their enabled state and
  schedule. Enable/disable controls use an exact, static name allowlist and the
  CLI verifies the resulting state before reporting success.
- The Crosstown card indicates when vacancy automation is managing its selected
  daily routines. A manual enable remains available, but the action worker will
  disable that routine again while Crosstown remains confidently vacant.

### Other Key Controls

- **Nest:** set temp (45–90°F), set mode, eco on/off. A write is reported as
  successful only after the API response is valid and a bounded device readback
  confirms the requested state.
- **Cielo:** on/off, set temp, set mode
- **Midea AC:** exact-device on/off, set temp (60–86°F), set mode, set fan,
  and eco on/off. The local CLI sends a control once and verifies it with one
  device readback before the dashboard reports success. The dashboard consumes
  that verified result directly instead of opening an immediate second LAN
  session to refresh the card.
- **Eight Sleep:** separate Crosstown and Cabin Pod cards show connection,
  water, temperature, thermal state, and authoritative Home/Away routing for
  Dylan and Julia. Each location's on/off/temperature controls include that
  exact Pod in the command, select only a person currently Home there, and
  remain guarded by the CLI's current-user route.
- **August:** lock/unlock
- **Ring/Nest Cameras:** take snapshots — Ring (Crosstown + Cabin doorbells), Nest (Kitchen @ Cabin, Laundry + Living Room @ Crosstown). Nest device discovery uses customName as a fallback to room name for cameras whose Google Home room doesn't match the dashboard label (e.g. "laundry camera" lives in the "Garage" room). Adding new Nest devices requires `nest reauth` to re-run Google Device Access OAuth consent.
- **Litter-Robot:** separate Crosstown and Cabin cards; clean/reset commands carry an exact protected robot alias
- **Petlibro:** manual feed
- **TV:** power on/off
- **Speakers/Cabin Speakers:** volume and mute/stop actions
- **Roombas (both locations):** start/stop/dock. Cabin status cards use the
  bounded local Roomba API and show a concise degraded-state explanation
  rather than provider tracebacks when Assistant is unavailable.

## Data Sources

- Presence state: `~/.openclaw/presence/state.json`
- Safe vacancy-action status: `~/.openclaw/bin/home-event-action status`
- Nest latest snapshot: `~/.openclaw/nest-history/*.jsonl`
- Dog walk state: `~/.openclaw/dog-walk/state.json`
- Camera snapshots: `~/.openclaw/camera-snaps/*.jpg`
- Device CLIs: `hue`, `nest`, `midea-ac`, `cielo`, `mysa`, `august`, `crosstown-roomba`, `roomba`, `samsung-tv`, `speaker`, `litter-robot`, `petlibro`, `8sleep`, `ring`
- Cabin Roomba status: bounded local API at
  `http://127.0.0.1:8553/api/cabin-roombas`; controls still use the guarded
  `roomba` CLI.
- Eight Sleep overview: `8sleep overview` returns a bounded two-Pod view and
  derives each Home/Away label from exact current-device and away-mode
  readbacks without changing either person's selected Pod.

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

### Authentication and Offline Devices

- A Nest snapshot that reaches `connected` and receives video RTP packets but
  never produces a JPEG is a codec-negotiation failure, not an offline camera.
  Keep `nest-camera-snap.py` pinned to H.264 profile `42e01f`; advertising both
  aiortc baseline profiles can produce duplicated payload IDs in Nest's answer.
- Cielo and Mysa status collectors never prompt from the dashboard process. An expired provider session is shown as an actionable reauthentication state rather than raw CLI output.
- Midea status and control stay on the Cabin LAN and use only the exact aliases
  enrolled in the owner-only local binding file. The dashboard does not load or
  retain Midea cloud credentials.
- The `Mysa` item in the `OpenClaw` 1Password vault, with `username` and `password` fields, is cached as `MYSA_USERNAME` and `MYSA_PASSWORD` by `openclaw-refresh-secrets`. When present, those values renew an expired Mysa token without an interactive prompt.
- Cielo's 30-minute refresher atomically rotates through the current API contract under a shared lock. Retryable API failures do not open a browser. Authentication rejection may use the dedicated PinchTab profile; a failed headless submission enters a six-hour backoff, and access-only capture remains explicitly non-durable until a refresh token is proven.
- Samsung TV and Google Cast status polls treat an unreachable local port as an offline or standby device. Speaker checks fail fast before invoking Cast discovery, so an offline device does not hold up the dashboard refresh.

To restore an expired provider session from an interactive Mac mini terminal:

```bash
# Prompts only in an interactive terminal and updates ~/.config/mysotherm.
mysa --login

# Cielo capture-first attended recovery; finish only succeeds after the newly
# captured refresh token rotates through the API and status is verified.
cielo-reauth --attended start
# Complete the visible login over VNC, then:
cielo-reauth finish
```
