# TOOLS.md - Local Notes

Skills define _how_ tools work. This file is for _your_ specifics — the stuff that's unique to your setup.

## QMD (Markdown Search)

CLI at `/opt/homebrew/bin/qmd`. Local hybrid search (BM25 + vector) over all OpenClaw docs. **Use this when you need details not in this file** — device IDs, API endpoints, curl examples, troubleshooting steps, etc.

```bash
qmd query "how does the BB watchdog work"    # hybrid search (recommended)
qmd search "cart URL"                         # keyword-only search
qmd get qmd://skills/grocery-reorder/skill.md # read a specific doc
```

Four collections indexed: `workspace` (SOUL/TOOLS/HEARTBEAT), `skills` (all SKILL.md files), `plans` (BB implementation, Private API, workspace state), `bin-scripts` (README, weekly upgrade doc).

## Smart Home Devices

### Cabin (Philly)
- Philips Hue lights
- iRobot Roombas (Floomba + Philly)
- Nest thermostats (Solarium, Living Room, Bedroom)
- Google Nest cameras
- Google smart speakers
- Petlibro feeder + fountain (unplugged, seasonal)

### Crosstown (West Roxbury)
- Philips Hue lights
- Cielo Breez Plus smart AC controllers (Basement, Living Room, Dylan's Office, Bedroom)
- Mysa baseboard heaters (Cat Room, Basement door, Movie room)
- iRobot Roombas — Roomba Combo 10 Max + scoomba J5 (local MQTT via MacBook Pro)
- Eight Sleep Pod 3 (cloud API, both sides: Dylan left, Julia right)
- Petlibro Granary Smart Feeder + Dockstream 2 Cordless Fountain (cloud API)
- Google smart speakers
- Litter-Robot 4 (cloud API via pylitterbot, tracks Sopaipilla + Burrito weights)
- August Wi-Fi Smart Lock (5th gen, front door — cloud API via august-api on MBP)

### Vacancy Automation
When a house becomes `confirmed_vacant` (both people detected at the other location), the `vacancy-actions` LaunchAgent automatically: turns off lights, sets thermostat to eco, turns off Cielos (Crosstown only), locks front door (August), and starts all Roombas. iMessage notification sent for lock status.

## Eight Sleep Pod

CLI at `/opt/homebrew/bin/8sleep`. Controls the Pod 3 (King) at Crosstown. Both sides: Dylan (left), Julia (right).

```bash
8sleep status                  # Both sides: temp, state, water
8sleep sleep dylan              # Last night's sleep (score, duration, stages)
8sleep sleep julia 2026-04-01   # Specific date
8sleep temp dylan -30           # Set temp (-100 to +100)
8sleep off julia                # Turn off side
8sleep on dylan                 # Resume smart schedule
8sleep device                   # Device info, firmware, connectivity
```

- Sleep data is keyed by **wake-up date** (today), not bedtime (yesterday)
- Env vars (`EIGHTSLEEP_*`) loaded from `~/.openclaw/.secrets-cache`
- Token cache at `~/.config/eightctl/token-cache.json` (auto-refreshes)
- API rate-limits aggressively on repeated auth failures — wait 5-10 min

## August Smart Lock

CLI at `/opt/homebrew/bin/august`. Controls the August Wi-Fi Smart Lock (5th gen) on Crosstown front door.

```bash
august status       # Lock state, door position, battery, WiFi signal
august lock         # Lock the front door
august unlock       # Unlock the front door
august locks        # List all locks on account
```

- Account: `dylanbochman@gmail.com`
- Lock: "Front Door" at "Potato's House", serial L5V82000F7
- Auth: JWT token via installId (cached at `~/.openclaw/august/config.json` on MBP, ~120 day expiry)
- Re-auth: `august authorize` then `august validate <code>` (sends 6-digit code to email)
- Architecture: SSH to MBP → Node.js august-cmd.js → August cloud API
- Auto-locks on vacancy via `vacancy-actions.sh` (checks status first, texts result)

## Image Tool — Path Policy

The `image` tool is restricted to workspace paths (`tools.fs.workspaceOnly: true`). Always save images to `~/.openclaw/workspace/tmp/` before passing them to the image tool — never use `/tmp` or `~/Downloads`.

```bash
# Good
cp /tmp/screenshot.png ~/.openclaw/workspace/tmp/screenshot.png
# Then use: image(path="~/.openclaw/workspace/tmp/screenshot.png")
```

## GWS (Google Workspace CLI)

CLI at `/opt/homebrew/bin/gws` (**pinned at v0.4.4**, Rust binary). Gmail, Calendar, Drive, Tasks. Do NOT bump — 0.22.x is a breaking redesign that drops `--account` in favor of per-account `GOOGLE_WORKSPACE_CLI_CONFIG_DIR` dirs. See `openclaw/plans/gws-0.22-migration.md` for the full migration plan before upgrading.

- Command pattern: `gws <service> <resource> <method> [--params '<JSON>'] [--json '<JSON>'] [--account <email>]`
- Credentials: AES-256-GCM encrypted at `~/.config/gws/`
- **DANGER: `gws auth logout` without `--account <email>` NUKES ALL accounts**

### Accounts

| Account | Owner | Flag |
|---|---|---|
| `dylanbochman@gmail.com` | Dylan | Default (no flag needed) |
| `julia.joy.jennings@gmail.com` | Julia | `--account julia.joy.jennings@gmail.com` |
| `bochmanspam@gmail.com` | Dylan (spam) | `--account bochmanspam@gmail.com` |
| `clawdbotbochman@gmail.com` | OpenClaw | `--account clawdbotbochman@gmail.com` |

### Skills

| Skill | Details |
|---|---|
| `gws-calendar` | Calendar read/write, event creation, availability |
| `gws-gmail` | Email search, read, send, label, archive |
| `gws-drive` | File search, read, create, share |

## Pinchtab (Browser Automation)

CLI at `/opt/homebrew/bin/pinchtab` (v0.7.6). Headless Chrome control for web tasks.

### Lifecycle

```bash
pinchtab &       # Start server (Chrome headless, port 9867)
sleep 5
# ... do work ...
pkill -f pinchtab 2>/dev/null || true   # Always clean up
```

### Common Commands

```bash
pinchtab nav <url>                  # Navigate
pinchtab snap -i -c                 # Snapshot interactive elements (compact)
pinchtab click <ref>                # Click element by ref from snapshot
pinchtab type <ref> <text>          # Type into element
pinchtab fill <ref|selector> <text> # Fill input directly
pinchtab press Enter                # Press key
pinchtab eval "document.title"      # Run JavaScript
pinchtab text                       # Extract readable page text
pinchtab ss -o /tmp/screenshot.png  # Screenshot
```

### Gotchas

- React SPAs: `element.click()` via `eval` may not trigger React handlers — use `pinchtab click <ref>` instead
- Always `pkill -f pinchtab` when done — leftover Chrome processes eat memory
- Cookie/session state persists between `nav` calls within the same server session

## BlueBubbles

### Architecture

OpenClaw v2026.3.7+ uses **webhook-only** for BB (no socket.io client). BB POSTs events to `http://localhost:18789/bluebubbles-webhook`. The gateway registers this webhook on startup.

**Gateway health monitor is DISABLED** (`gateway.channelHealthCheckMinutes: 0`). The BB watchdog handles stale detection instead — it only triggers when new messages exist but aren't being delivered.

### Watchdog (`com.openclaw.bb-watchdog`)

Script at `~/.openclaw/workspace/scripts/bb-watchdog.sh`, runs every 60s. Four detection modes:

1. **Private API helper disconnected** — queries `/api/v1/server/info` for `helper_connected: false`. Full BB restart (not soft — soft restart doesn't fix chat.db observer co-stalls).
2. **Chat.db observer stall** — GUID changes but no webhook dispatch. Poke-first, then full restart after 3 failed pokes.
3. **Webhook service dead** — no dispatch in 30+ min but new messages arriving. Full BB restart.
4. **Gateway BB plugin dead** — BB dispatching but gateway not processing. Gateway-only restart.

All restarts share a 15-min cooldown. Gateway restarts are **deferred while cron jobs are running** — the watchdog checks `runningAtMs` markers in `jobs.json` and retries on the next 60s cycle. Full details: `openclaw/plans/bluebubbles-implementation-current-state.md`

### Key Gotchas

- **NEVER use soft restart for recovery** — `/server/restart/soft` reconnects the Private API helper but does NOT restart the chat.db file system observer, leaving BB blind to new messages
- **BB + gateway restarts must be sequenced** — watchdog waits 15s after BB relaunch before restarting gateway to re-register webhook
- **Cloudflare daemon crash-loop** — BB runs it even with `lan-url` proxy; can corrupt event loop
- **v2026.3.7 BB plugin import bug** — `monitor-normalize.ts` has broken import; weekly upgrade script auto-patches

### Quick Reference

- Base URL: `http://localhost:1234/api/v1`
- Auth: `?password=${BLUEBUBBLES_PASSWORD}`
- DM GUIDs: `any;-;` prefix; Group GUIDs: `iMessage;+;` prefix
- Reaction types: `love`, `like`, `dislike`, `laugh`, `emphasize`, `question`
- For Private API curl examples: `qmd query "bluebubbles private API"`

## FindMy Locate

CLI at `~/.openclaw/bin/findmy-locate`. Locates Dylan, Julia, or both via Apple FindMy screenshots using Peekaboo screen automation.

```bash
findmy-locate dylan     # Screenshot of Dylan's map pin
findmy-locate julia     # Screenshot of Julia's map pin
findmy-locate both      # Single pass: Dylan then Julia
```

Returns JSON with the screenshot path. **Read the screenshot image** to determine street address, neighborhood, or proximity to known locations. After locating, consider using the **places** skill (`goplaces`) for nearby search, directions, or recommendations.

- Captures saved to `~/.openclaw/findmy-locate/`
- Requires Peekaboo Screen Recording + Accessibility TCC grants
- Must run from GUI context (LaunchAgent or local terminal, not SSH)
- People sidebar order: Me (0) → Dylan (1) → Julia (2)

## Presence Detection

Script at `~/.openclaw/workspace/scripts/presence-detect.sh`. Sticky/arrival-based model: once detected at a location, person stays until detected at the other.

- `cabin` mode runs on Mini (Starlink gRPC), `crosstown` on MacBook Pro (ARP scan), `evaluate` correlates both
- States: **occupied** / **confirmed_vacant** / **possibly_vacant**
- For device fingerprints, output files, and gotchas: `qmd query "presence detection"`

## Crosstown Network

Mac Mini → MacBook Pro SSH via Tailscale (`ssh dylans-macbook-pro`), dedicated key `~/.ssh/id_mini_to_mbp` (bypasses 1Password agent — hangs under launchd). Configured via `Match originalhost` in `~/.ssh/config`.

## Dashboards

| Dashboard | Port | Data |
|---|---|---|
| Nest Climate | 8550 | Thermostat + weather + presence |
| Usage | 8551 | Token consumption + agent activity |
| Dog Walk | 8552 | Walk history, Fi GPS, Roomba status, route maps |
| Financial | 8585 | Utilities, spending |

For API endpoints and UI features: `qmd query "nest dashboard API"` or `qmd query "usage dashboard"` or `qmd query "dog walk dashboard"`
