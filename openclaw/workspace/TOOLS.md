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

CLI at `/opt/homebrew/bin/pinchtab` (v0.11.0). Headless Chrome control for web tasks. The native binary lives at `~/.pinchtab/bin/<version>/pinchtab-darwin-arm64`; the npm `pinchtab` shim resolves it.

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

- React SPAs: `element.click()` via `eval` may not fire React handlers — use `pinchtab click <ref>` instead.
- Always `pkill -f pinchtab` when done (auto-spawned servers persist past `daemon stop`).
- v0.11.0+: `security.allowEvaluate` defaults off (eval returns 403). Profiles now at `~/.pinchtab/profiles/<name>/`. v0.11 transition details: `qmd query "pinchtab 0.11 upgrade"`.

## BlueBubbles

### Architecture

OpenClaw v2026.3.7+ uses **webhook-only** for BB (no socket.io client). BB POSTs events to `http://localhost:18789/bluebubbles-webhook`. The gateway registers this webhook on startup.

**Gateway health monitor is DISABLED** (`gateway.channelHealthCheckMinutes: 0`). The BB watchdog handles stale detection instead — it only triggers when new messages exist but aren't being delivered.

### Watchdog (`com.openclaw.bb-watchdog`)

Script at `~/.openclaw/workspace/scripts/bb-watchdog.sh`, runs every 60s. Four detection modes (helper down, chat.db observer stall, webhook dead, gateway plugin dead). 15-min cooldown; gateway restarts deferred while cron jobs run (checks `runningAtMs` in `jobs.json`). Details: `qmd query "bb watchdog modes"`.

### Key Gotchas

- **Chat history search: always query by handle, not just chat GUID** — BB sometimes stores DM messages with empty `chats[]` arrays. Use `/api/v1/message/query` with `handle.id = :value` to search by phone/email.
- **NEVER use soft restart for recovery** — reconnects the Private API helper but does NOT restart the chat.db observer, leaving BB blind to new messages.
- **BB + gateway restarts must be sequenced** — watchdog waits 15s after BB relaunch before restarting gateway to re-register webhook.
- Other gotchas (Cloudflare daemon crash, v2026.3.7 import bug): `qmd query "bluebubbles gotchas"`.

### Quick Reference

- Base URL: `http://localhost:1234/api/v1`
- Auth: `?password=${BLUEBUBBLES_PASSWORD}`
- DM GUIDs: `any;-;<address>` (e.g., `any;-;julia.joy.jennings@gmail.com`)
- Group GUIDs: `any;+;<chat-identifier-hex>` (e.g., the Dylan+Julia date-night chat is `any;+;7010feab69b14fa19071a88340495f2f`). Older docs said `iMessage;+;` but BB returns `any;+;` as canonical.
- **Send `target` should be a chat GUID**, not raw phone/email — the plugin's `parseRawChatGuid` routes `<service>;<+|->;<id>` strings as `kind: chat_guid` and skips the slow `/chat/query` + `/chat/new` lookup (saves 30-90s per send to a known contact). See SOUL.md "BlueBubbles routing" for the canonical Dylan / Julia / group-chat targets.
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

## Financial Dashboard

Repo `~/repos/financial-dashboard/` on Mini; SPA on port 8585; weekly cron `financial-scrape-0001` (Sundays 4:05 ET) runs 7 scrapers, all self-healing:

- **Tier 1** — Tesla Solar (API only).
- **Tier 2** — Eversource, NG Electric, NG Gas, BWSC, PennyMac. Playwright with `--re-auth` flag; each saves `storage_state.json` in its `.NAME_session/` dir. PennyMac auto-fetches email-MFA codes from Julia's Gmail via `gws`. Creds at `op://OpenClaw/<url-style-title>/...`.
- **Tier 2b** — BoA. Bot detection defeats every Playwright-launched approach; instead the scraper does `playwright.chromium.connect_over_cdp(...)` to Pinchtab's already-running Chrome (port discovered by `ps`-grep for `--user-data-dir=~/.pinchtab/profiles`). Bootstrap rare (weeks-months) — user Screen Shares + manually logs in once. Never `page.goto()` or `context.close()` in CDP mode.

Cron prompt at `openclaw cron list --json` (id `financial-scrape-0001`) is the canonical operational spec. Dev architecture: `~/repos/financial-dashboard/CLAUDE.md`. Reusable patterns: skills `playwright-email-mfa-flow`, `playwright-device-trust-bootstrap`, `web-auth-check-by-title-not-url`.

## Dashboards

| Dashboard | Port | Data |
|---|---|---|
| Nest Climate | 8550 | Thermostat + weather + presence |
| Usage | 8551 | Token consumption + agent activity |
| Dog Walk | 8552 | Walk history, Fi GPS, Roomba status, route maps |
| Financial | 8585 | Utilities, mortgage, solar, water |

For API endpoints and UI features: `qmd query "nest dashboard API"` etc.
