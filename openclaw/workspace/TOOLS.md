# TOOLS.md - Local Notes

Skills define _how_ tools work. This file is for _your_ specifics — the stuff that's unique to your setup.

## Smart Home Devices

### Both Houses
- Philips Hue lights
- iRobot Roombas
- Nest thermostats
- Google Nest cameras
- Google smart speakers

## Spotify Connect Devices

These device IDs are used with the Andre Spotify Connect API (`api/spotify/transfer`).

| Device Name | Device ID | Type | Notes |
|---|---|---|---|
| Kitchen speaker | `b8581271559fd61aa994726df743285c` | CastAudio | Google Nest Audio (currently active) |
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

### Speakers

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

## BlueBubbles Private API

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
