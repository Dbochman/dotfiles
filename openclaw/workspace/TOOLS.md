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

### Crosstown (Boston)
- Philips Hue lights
- Cielo Breez Plus smart AC controllers (Basement, Living Room, Dylan's Office, Bedroom)
- Mysa baseboard heaters (Cat Room, Basement door, Movie room)
- Google smart speakers

## Image Tool — Path Policy

The `image` tool is restricted to workspace paths (`tools.fs.workspaceOnly: true`). Always save images to `~/.openclaw/workspace/tmp/` before passing them to the image tool — never use `/tmp` or `~/Downloads`.

```bash
# Good
cp /tmp/screenshot.png ~/.openclaw/workspace/tmp/screenshot.png
# Then use: image(path="~/.openclaw/workspace/tmp/screenshot.png")
```

## GWS (Google Workspace CLI)

CLI at `/opt/homebrew/bin/gws` (v0.4.4, Rust binary). Gmail, Calendar, Drive, Tasks.

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

Script at `~/.openclaw/workspace/scripts/bb-watchdog.sh`, runs every 60s.

- Tracks latest message GUID from BB API — detects chat.db observer stalls
- Checks BB log for webhook dispatch entries — detects dead webhook service
- Cross-checks gateway runtime log for BB plugin activity — detects silent plugin failures
- No dispatch in 30+ min but new messages → **full BB restart**
- Individual message lag > 90s → poke-first, then restart after 3 failed pokes
- BB dispatching but gateway BB plugin inactive → **gateway-only restart**

### Key Gotchas

- **Soft restart doesn't fix dead webhooks** — only full app restart works
- **BB + gateway restarts must be coordinated** — watchdog handles sequencing
- **Cloudflare daemon crash-loop** — BB runs it even with `lan-url` proxy; can corrupt event loop
- **v2026.3.7 BB plugin import bug** — `monitor-normalize.ts` has broken import; weekly upgrade script auto-patches

### Quick Reference

- Base URL: `http://localhost:1234/api/v1`
- Auth: `?password=${BLUEBUBBLES_PASSWORD}`
- DM GUIDs: `any;-;` prefix; Group GUIDs: `iMessage;+;` prefix
- Reaction types: `love`, `like`, `dislike`, `laugh`, `emphasize`, `question`
- For Private API curl examples: `qmd query "bluebubbles private API"`

## Presence Detection

Script at `~/.openclaw/workspace/scripts/presence-detect.sh`. Sticky/arrival-based model: once detected at a location, person stays until detected at the other.

- `cabin` mode runs on Mini (Starlink gRPC), `crosstown` on MacBook Pro (ARP scan), `evaluate` correlates both
- States: **occupied** / **confirmed_vacant** / **possibly_vacant**
- For device fingerprints, output files, and gotchas: `qmd query "presence detection"`

## Crosstown Network

Mac Mini ↔ MacBook Pro SSH via Tailscale (`dylans-macbook-pro` / `dylans-mac-mini`), 1Password SSH agent.

## Dashboards

| Dashboard | Port | Data |
|---|---|---|
| Nest Climate | 8550 | Thermostat + weather + presence |
| Usage | 8551 | Token consumption + agent activity |

For API endpoints and UI features: `qmd query "nest dashboard API"` or `qmd query "usage dashboard"`
