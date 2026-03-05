# TOOLS.md - Local Notes

Skills define _how_ tools work. This file is for _your_ specifics — the stuff that's unique to your setup.

## Smart Home Devices

### Cabin (Philly)
- Philips Hue lights
- iRobot Roombas (Floomba + Philly)
- Nest thermostats (Solarium, Living Room, Bedroom)
- Google Nest cameras
- Google smart speakers

### Crosstown (Boston)
- Philips Hue lights
- Cielo Breez Plus smart AC controllers (Basement, Living Room, Dylan's Office, Bedroom)
- Google smart speakers

## Spotify Connect Devices

These device IDs are used with the Andre Spotify Connect API (`api/spotify/transfer`).

| Device Name | Device ID | Type | Notes |
|---|---|---|---|
| Kitchen speaker | `b8581271559fd61aa994726df743285c` | CastAudio | Google Nest Audio, Cabin (currently active) |
| Dylan's Mac mini | `0eb8b896cf741cd28d15b1ce52904ae7940e4aae` | Computer | Cabin server |
| Dylan's MacBook Pro | `173fd1e1d533e5a1c59fc25979c3baccc3af5d07` | Computer | |
| Dylan's Mac | `13bc12a88f007bf49d13997fc64c0a6640f49440` | Computer | |

## What Goes Here

Environment-specific notes: camera names, SSH hosts, speaker names, device nicknames, TTS preferences. Skills define _how_ tools work; this file captures _your_ specific setup details.
## GOG (Google Workspace CLI)

CLI at `/opt/homebrew/bin/gog` (v0.11.0). Gmail, Calendar, Drive, Contacts.

- Always use `--account=<email>` flag — multiple Google accounts are configured
- Always use `--json` for parseable output
- Headless/launchd: requires `GOG_KEYRING_PASSWORD` env var (in `.secrets-cache`)
- `invalid_grant` error = refresh token revoked, must re-auth on Mini screen: `gog auth add <email>`
- Auth health check: `gog gmail search "is:unread" --account=<email> --json --max=1`

### Accounts

| Account | Used for |
|---|---|
| `julia.joy.jennings@gmail.com` | Julia's Gmail (morning triage, evening digest cron jobs) |
| `dylanbochman@gmail.com` | Dylan's Gmail and Calendar |

## Catt (Chromecast CLI)

CLI at `~/.local/bin/catt` (v0.13.1). Not on default PATH.

- Wake speakers before Spotify Connect — Google Home speakers only appear to Spotify when actively casting
- `catt -d <IP> cast_site https://example.com` to wake a speaker

### Speakers (Cabin)

| Name | IP |
|---|---|
| Kitchen | 192.168.1.66 |
| Bedroom | 192.168.1.163 |

## Roomba

CLI at `~/.openclaw/skills/roomba/roomba` (Python venv at `~/.openclaw/roomba/venv`).

- Controls Floomba + Philly (Roomba 105 Combo) at the cabin via Google Assistant text API
- Auth: Google Assistant OAuth at `~/.openclaw/roomba/credentials.json`
- `invalid_grant` = refresh token revoked, must re-auth via `roomba setup` **on Mini screen** (requires browser, can't do over SSH)
- Venv uses Python 3.13 (Homebrew)

## Nest Climate Dashboard

Single-file Python server at `~/.openclaw/bin/nest-dashboard.py`, port 8550. LaunchAgent: `ai.openclaw.nest-dashboard`.

### Data Sources

| Source | Path | Interval | Format |
|---|---|---|---|
| Nest + Cielo snapshots | `~/.openclaw/nest-history/YYYY-MM-DD.jsonl` | 30 min (`ai.openclaw.nest-snapshot`) | temp, humidity, setpoint, HVAC state per room (Nest rooms have `source: "nest"`, Cielo rooms have `source: "cielo"`) |
| Presence history | `~/.openclaw/presence/history/YYYY-MM-DD.jsonl` | 15 min (presence-detect cron) | occupancy + people list per location |
| Current presence | `~/.openclaw/presence/state.json` | Written by `presence-detect.sh evaluate` | Full evaluated state |

### API Endpoints

| Endpoint | Returns |
|---|---|
| `GET /` | Dashboard HTML (embedded Chart.js UI) |
| `GET /api/data?hours=N` | `{ snapshots, presence, meta }` — climate + presence history |
| `GET /api/current` | Latest nest snapshot |
| `GET /api/presence` | Current presence state from `state.json` |

### Dashboard UI Features

- **Presence cards** — occupancy badges (Occupied / Vacant / Possibly Vacant) per location with who's there
- **Climate cards** — current temp, setpoint, HVAC status, humidity per room
- **Weather cards** — outside conditions per structure
- **Temperature chart** — actual + setpoint (dotted) lines per room
- **Humidity chart** — per room + outside
- **HVAC duty cycle chart** — active % per hour per room (heating, cooling, fan, etc.)
- **Occupancy overlay bands** — colored background on charts (green=occupied, gray=vacant) when a single structure is selected
- **Location tags** — each card shows a Cabin or Crosstown badge
- **Structure filter** — Both / Cabin / Crosstown
- **Time range** — 24h / 7d / 30d / 1Y (downsampled to ~1/hour beyond 7d)

### Restart

```bash
launchctl kickstart -k gui/$(id -u)/ai.openclaw.nest-dashboard
```

## Cielo AC (Crosstown)

CLI at `~/repos/cielo-cli/cli.js` (Node.js). Controls Mr Cool minisplits via Cielo Breez Plus sensors at Crosstown.

### Devices

| Room | Device Type | Sensor Data |
|---|---|---|
| Basement | Breez Plus | temp (°F), humidity (%) |
| Living Room | Breez Plus | temp (°F), humidity (%) |
| Dylan's Office | Breez Plus | temp (°F), humidity (%) |
| Bedroom | Breez Plus | temp (°F), humidity (%) |

### Commands

```bash
# Status of all devices (JSON array)
/usr/local/bin/node ~/repos/cielo-cli/cli.js status --json

# Control a device
/usr/local/bin/node ~/repos/cielo-cli/cli.js set --device "Bedroom" --power on --mode cool --temp 72
```

### Data Shape (per device in status JSON)

- `latEnv.temp` — room temperature (°F)
- `latEnv.humidity` — room humidity (%)
- `latestAction.mode` — heat/cool/auto/fan/dry
- `latestAction.power` — on/off
- `latestAction.temp` — setpoint (°F)
- `deviceStatus` — 1 = online, 0 = offline
- `deviceName` — room name

### Integration with Nest Snapshot

The `nest snapshot` command automatically calls `cielo-cli status --json` and appends Crosstown rooms (prefixed `19Crosstown <room>`) to the snapshot JSONL. Cielo rooms have `source: "cielo"` to distinguish from Nest-sourced rooms. If Cielo API fails, the snapshot still records Nest data (tolerant).

### Token Refresh

Cielo API tokens expire every 30 min. Auto-refreshed by `com.openclaw.cielo-refresh` LaunchAgent. Config at `~/.config/cielo/config.json`.

## Crosstown Network Access (SSH)

The Mac Mini can SSH to the MacBook Pro at Crosstown (and vice versa) via Tailscale without 1Password approval, using a dedicated ed25519 keypair.

### Setup

| Machine | SSH Config Host | Key Path |
|---|---|---|
| Mac Mini | `dylans-macbook-pro` | `~/.ssh/id_mini_to_mbp` |
| MacBook Pro | `dylans-mac-mini` | `~/.ssh/id_mini_to_mbp` |

Both configs use `IdentityAgent none` to bypass 1Password SSH agent for automated connections.

### Usage

```bash
# From Mac Mini — run commands on Crosstown network
ssh dylans-macbook-pro 'arp -a | grep 192.168.165'
ssh dylans-macbook-pro '~/.openclaw/workspace/scripts/presence-detect.sh crosstown'

# From Mac Mini — deploy files to MBP
scp <local-file> dylans-macbook-pro:<remote-path>
```

### When to Use

- Running ARP scans or network diagnostics on the Crosstown LAN (192.168.165.x)
- Deploying updated scripts to the MBP
- Triggering crosstown presence scans manually
- Any task that requires being on the Crosstown local network

### Key Backup

Keypair stored in 1Password: "Mini↔MBP SSH Key (ed25519)" in the OpenClaw vault.

## Presence Detection

Script at `~/.openclaw/workspace/scripts/presence-detect.sh`. Detects who's home at each location.

### Modes

| Mode | Where it runs | Method |
|---|---|---|
| `cabin` | Mac Mini | Starlink gRPC API (WiFi client list) |
| `crosstown` | MacBook Pro | ARP scan (ping sweep + MAC match, IP fallback) |
| `evaluate` | Mac Mini | Correlates both scans, writes state + history |

### Tracked Devices

| Person | Cabin (Starlink) | Crosstown (ARP) |
|---|---|---|
| Dylan | Device name match "Dylan" + "iPhone" | MAC `6c:3a:ff:5f:fc:ba`, IP `192.168.165.124` |
| Julia | Device name match "Julia" | Hostname `julias-iphone`, MAC `38:e1:3d:c0:40:63`, IP `192.168.165.248` |

Crosstown matching priority: MAC → IP → hostname (mDNS `.lan` name from ARP table). Hostname is the most durable — survives iOS MAC/IP rotation.

### Vacancy Logic (Arrival-Based / Sticky)

- **occupied** — any tracked person detected at that location (or sticky from last detection)
- **confirmed_vacant** — all tracked people confirmed at the other location
- **possibly_vacant** — no one detected and no previous location on record

Once a person is detected at a location, they **stay there** until positively detected at the other location. Phone sleep, MAC rotation, or missed scans don't cause flicker.

### Output Files

| File | Written by | Purpose |
|---|---|---|
| `~/.openclaw/presence/cabin-scan.json` | `cabin` mode | Raw cabin scan result |
| `~/.openclaw/presence/crosstown-scan.json` | `crosstown` mode (pushed via Tailscale) | Raw crosstown scan result |
| `~/.openclaw/presence/state.json` | `evaluate` mode | Correlated state (used by dashboard) |
| `~/.openclaw/presence/prev-evaluated.json` | `evaluate` mode | Previous state for transition detection |
| `~/.openclaw/presence/events.json` | `evaluate` mode | Last 100 occupancy/relocation transitions |
| `~/.openclaw/presence/history/YYYY-MM-DD.jsonl` | `evaluate` mode | Date-partitioned history (used by dashboard) |

### Gotchas

- iPhones in low-power mode may not respond to ARP pings — "Limit IP Address Tracking" should be disabled on tracked phones
- iOS randomizes MAC addresses per-network — hostname matching (`julias-iphone.lan`) is preferred; MAC and IP are fallbacks that may need updating after rotation
- Crosstown scan runs on MacBook Pro and pushes results to Mini via `tailscale file cp`
- Mac Mini can SSH to MacBook Pro via `ssh dylans-macbook-pro` (Tailscale, key: `~/.ssh/id_mini_to_mbp`)
- Presence script must be deployed to **both** machines — Mini runs cabin scan + evaluate, MacBook Pro runs crosstown scan
- After updating device fingerprints, deploy to both: `scp` to Mini, then Mini `scp` to MacBook Pro

## Pinchtab (Browser Automation)

CLI at `/opt/homebrew/bin/pinchtab` (v0.7.6). Headless Chrome control for web tasks the agent can't do via API.

### Lifecycle

```bash
# Start server (runs Chrome headless on port 9867)
pinchtab &
sleep 5

# Do work...

# Clean up when done
pkill -f pinchtab 2>/dev/null || true
```

### Common Commands

```bash
pinchtab nav <url>                  # Navigate
pinchtab snap -i -c                 # Snapshot interactive elements (compact, token-efficient)
pinchtab click <ref>                # Click element by ref from snapshot
pinchtab type <ref> <text>          # Type into element
pinchtab fill <ref|selector> <text> # Fill input directly (bypasses focus issues)
pinchtab press Enter                # Press key
pinchtab eval "document.title"      # Run JavaScript
pinchtab text                       # Extract readable page text
pinchtab ss -o /tmp/screenshot.png  # Screenshot
pinchtab tabs                       # List open tabs
```

### Snapshot Flags

- `-i` — interactive elements only (buttons, links, inputs)
- `-c` — compact format (most token-efficient)
- `-d` — diff since last snapshot (saves tokens on repeat checks)
- `-s <selector>` — scope to CSS selector
- `--max-tokens N` — truncate output

### Used By

- **OpenTable bookings** — `~/.openclaw/workspace/scripts/opentable-book.sh`
- **Cielo token refresh** — `~/.openclaw/workspace/scripts/cielo-refresh.sh` (browser-based login fallback)

### Gotchas

- React SPAs: `element.click()` via `eval` may not trigger React handlers — use `pinchtab click <ref>` instead (dispatches proper pointer events)
- Always `pkill -f pinchtab` when done — leftover Chrome processes eat memory
- Cookie/session state persists between `nav` calls within the same server session

## BlueBubbles

### Architecture

OpenClaw v2026.3.2+ uses **webhook-only** for BB (no socket.io client). BB POSTs events to the gateway's HTTP endpoint at `http://localhost:18789/bluebubbles-webhook`. The gateway registers this webhook in BB on startup.

### Watchdog (`com.openclaw.bb-watchdog`)

Script at `~/.openclaw/workspace/scripts/bb-watchdog.sh`, runs every 60s.

**What it monitors:**
- Tracks latest message GUID from BB API — detects chat.db observer stalls
- Checks BB's internal log for `WebhookService Dispatching` entries — detects dead webhook service
- If no webhook dispatch in 30+ min but new messages are arriving → **full BB app restart** (skip poke)
- If individual message lag > 90s → poke-first, then restart after 3 failed pokes

**Recovery sequence:** poke Messages.app → retry → full BB restart (quit + relaunch)

### Gotchas

- **Cloudflare daemon crash-loop**: BB still runs a Cloudflare tunnel daemon even with `lan-url` proxy. It crash-loops with "context canceled" errors, which can corrupt BB's internal event loop and silently kill webhook dispatch.
- **Soft restart doesn't fix dead webhooks**: `GET /api/v1/server/restart/soft` does NOT restore webhook dispatch. Only a full app restart (`osascript -e 'tell application "BlueBubbles" to quit'` + `open -a BlueBubbles`) works.
- **Gateway restarts can kill BB webhooks**: Any OpenClaw gateway restart (config change, upgrade, manual) can cause BB's webhook dispatch to silently die. If OpenClaw stops responding after a gateway restart, restart BB too.
- **TODO**: Consider having the watchdog detect gateway PID changes and preemptively restart BB.

### Logs

| Log | Path | Contents |
|-----|------|----------|
| BB server log | `~/Library/Logs/bluebubbles-server/main.log` | Webhook dispatch, API requests, auth errors, Cloudflare daemon, Private API events |
| BB watchdog log | `/tmp/bb-watchdog.log` | Stall detection, restarts, lag alerts (rotated daily, 7-day retention) |
| BB ingest lag metrics | `/tmp/bb-ingest-lag.log` | CSV of lag events: `timestamp,lag_sec,threshold,guid` |

### Private API

Base URL: `http://localhost:1234/api/v1`
Auth: `?password=${BLUEBUBBLES_PASSWORD}` (env var, no 1Password needed)

### Typing Indicator

```bash
curl -s -X POST "http://localhost:1234/api/v1/chat/${CHAT_GUID}/typing?password=${BLUEBUBBLES_PASSWORD}"
```

Stops automatically when you send a message.

### Send Reaction / Tapback

```bash
curl -s -X POST "http://localhost:1234/api/v1/message/react?password=${BLUEBUBBLES_PASSWORD}" \
  -H "Content-Type: application/json" \
  --data-raw '{"chatGuid":"<CHAT_GUID>","selectedMessageGuid":"<MESSAGE_GUID>","reaction":"<TYPE>"}'
```

Reaction types: `love`, `like`, `dislike`, `laugh`, `emphasize`, `question`

### Send Message (Private API method)

> **Note:** Prefer the OpenClaw message tool for sending messages — it handles
> routing, logging, and delivery queue. Use these Private API curl commands
> only for things OpenClaw doesn't expose yet (typing indicators, reactions).

```bash
curl -s -X POST "http://localhost:1234/api/v1/message/text?password=${BLUEBUBBLES_PASSWORD}" \
  -H "Content-Type: application/json" \
  --data-raw '{"chatGuid":"<CHAT_GUID>","message":"<TEXT>","tempGuid":"temp-<UNIQUE>","method":"private-api"}'
```

### Chat GUIDs

- DMs use `any;-;` prefix (e.g., `any;-;dylanbochman@gmail.com`)
- Group chats use `iMessage;+;` prefix
- Always read the GUID from inbound message metadata, don't hardcode
