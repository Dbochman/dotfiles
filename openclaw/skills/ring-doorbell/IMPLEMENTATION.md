# Ring Doorbell Integration — Implementation Spec

## Overview

The Ring doorbell integration provides three capabilities for OpenClaw:

1. **CLI skill** — query doorbell status, events, video, and health on demand
2. **Real-time event listener** — persistent FCM push listener that sends iMessage notifications with camera frames and AI-powered scene descriptions
3. **Vision-triggered home automation** — when Claude Haiku detects the full household departing or arriving with both dogs, Roombas are automatically started or docked

## Architecture

```
Ring Doorbell Hardware
        |
        v (cloud)
Ring Cloud API (auth-api.8slp.net / client-api.8slp.net)
        |
        +---> python-ring-doorbell library (venv)
        |         |
        |         +---> ring-api.py (CLI commands: status, events, video, etc.)
        |         |         |
        |         |         +---> ring (bash wrapper, human-readable output)
        |         |                   |
        |         |                   +---> ~/.openclaw/bin/ring (PATH wrapper)
        |         |
        |         +---> ring-listener.py (persistent FCM listener)
        |                   |
        |                   +---> Claude Haiku Vision API (scene analysis)
        |                   |         |
        |                   |         +---> Structured JSON: people, dogs, direction
        |                   |
        |                   +---> BlueBubbles API (iMessage notifications)
        |                   |         |
        |                   |         +---> Text alerts + camera frame attachments
        |                   |
        |                   +---> Roomba CLIs (departure/arrival automation)
        |                             |
        |                             +---> crosstown-roomba start/dock all
        |                             +---> roomba start/dock floomba/philly
        |
        +---> Ring Protect (subscription, Crosstown only)
                  |
                  +---> Video recording URLs (pre-signed S3)
                  +---> Frame extraction via ffmpeg
```

## Devices

| Name | Ring ID | Type | Location | Ring Protect | Family |
|------|---------|------|----------|-------------|--------|
| Front Door | 684794187 | cocoa_doorbell_v3 | Crosstown | Yes | doorbots |
| Front Door | 697442349 | cocoa_doorbell_v3 | Cabin | No | authorized_doorbots |

Both are battery-powered doorbells. The Cabin doorbell is a shared device (authorized_doorbots).

## Files

### Skill Directory (`openclaw/skills/ring-doorbell/`)

| File | Purpose | Size |
|------|---------|------|
| `SKILL.md` | OpenClaw skill metadata and agent documentation | 3.6K |
| `ring` | Bash CLI wrapper — human-readable output from ring-api.py | 7.1K |
| `ring-api.py` | Python API wrapper using python-ring-doorbell library | 14K |
| `ring-listener.py` | Persistent FCM event listener with vision + automation | 19K |
| `ring-listener-wrapper.sh` | Bash wrapper that sources secrets before starting listener | 499B |
| `IMPLEMENTATION.md` | This file | — |

### Other Files

| File | Purpose |
|------|---------|
| `openclaw/bin/ring` | PATH wrapper → delegates to skill dir |
| `openclaw/ai.openclaw.ring-listener.plist` | LaunchAgent for persistent listener |

### Files on Mini (not in repo)

| Path | Purpose |
|------|---------|
| `~/.config/ring/config.yaml` | Ring account credentials (email + password) |
| `~/.config/ring/token-cache.json` | Cached Ring OAuth tokens (auto-refreshed) |
| `~/.openclaw/ring/venv/` | Python venv with `ring_doorbell[listen]`, `requests`, `aiohttp`, `aiofiles` |
| `~/.openclaw/ring-listener/fcm-credentials.json` | FCM push registration credentials |
| `~/.openclaw/ring-listener/frames/` | Temporary directory for video frames (cleaned after send) |
| `~/Library/LaunchAgents/ai.openclaw.ring-listener.plist` | Deployed LaunchAgent |

## Component 1: CLI Skill (`ring`)

### Commands

| Command | Description | Requires Ring Protect |
|---------|-------------|----------------------|
| `ring status` | All doorbells: name, model, battery, wifi, firmware, last event | No |
| `ring events [N]` | Last N ding/motion events with timestamps, type, person detection, duration | No |
| `ring health` | WiFi signal strength, RSSI, connection status per device | No |
| `ring video [ID]` | Pre-signed S3 URL for a recording (default: latest) | Yes |
| `ring videos [N]` | List recent recordings with URLs (default: 5) | Yes |
| `ring snapshot [FILE]` | Capture live snapshot (fails on sleeping battery doorbells) | No |
| `ring download ID FILE` | Download recording MP4 to local file | Yes |

### Authentication Flow

```
1. First run (interactive on Mini):
   - Load email/password from ~/.config/ring/config.yaml
   - POST to https://auth-api.8slp.net/v1/tokens (wrong — Ring uses oauth.ring.com)
   - Ring requires 2FA → user enters code from SMS/email
   - Tokens cached to ~/.config/ring/token-cache.json

2. Subsequent runs:
   - Load cached token → python-ring-doorbell auto-refreshes via oauthlib
   - token_updater callback persists new tokens to cache
   - If refresh token expires → re-auth needed (2FA again)
```

### Event History Data Structure

The Ring API returns event history as dicts (not objects). Key fields:

| Field | Example | Description |
|-------|---------|-------------|
| `id` | `7620878129758806347` | Unique recording/event ID |
| `created_at` | `datetime(2026, 3, 24, 17, 39, ...)` | UTC timestamp |
| `kind` | `motion`, `ding`, `on_demand` | Event type |
| `answered` | `True`/`False` | Whether someone viewed the live feed |
| `duration` | `19.0` | Recording duration in seconds |
| `cv_properties.person_detected` | `True`/`False` | Ring's computer vision: human detected |
| `cv_properties.detection_type` | `human`, `motion` | CV classification |
| `recording.status` | `ready` | Whether video is available for download |

### Video Download

Recording URLs are pre-signed Amazon S3 URLs with ~15 minute expiry:
```
https://download-us-east-2.prod.phoenix.devices.amazon.dev/v1/download/<uuid>.mp4?X-Amz-Date=...&X-Amz-Expires=900&...
```

The `ring download` command fetches the signed URL via `async_recording_url()`, then downloads the MP4 directly via `aiohttp` (the library's built-in `async_recording_download()` uses a deprecated `/clients_api/` endpoint that returns 404).

## Component 2: Real-Time Event Listener

### Process Model

The listener runs as a persistent `KeepAlive` LaunchAgent (`ai.openclaw.ring-listener`). It:

1. Authenticates with Ring using cached tokens
2. Registers with Firebase Cloud Messaging (FCM) for push notifications
3. Subscribes to Ring event notifications
4. Runs an async event loop indefinitely, processing events as they arrive

### LaunchAgent Configuration

```xml
<key>Label</key>        ai.openclaw.ring-listener
<key>KeepAlive</key>    true
<key>ProgramArguments</key>  /bin/bash ring-listener-wrapper.sh
<key>StandardOutPath</key>   /tmp/ring-listener.log
<key>StandardErrorPath</key> /tmp/ring-listener.log
```

The wrapper script (`ring-listener-wrapper.sh`) sources `~/.openclaw/.secrets-cache` for `BLUEBUBBLES_PASSWORD` before exec'ing the Python listener. This follows the cache-only secrets pattern (no `op read` at startup — would hang under launchd).

### Event Processing Flow

```
FCM Push Notification
        |
        v
on_event(RingEvent)
        |
        +-- is_update? → skip (dedup)
        +-- already seen in last 5min? → skip (dedup)
        |
        +-- kind == "ding"
        |       |
        |       v
        |   _handle_ding()
        |       |
        |       +-- Send immediate text: "🔔 Front Door: Doorbell rang!"
        |       +-- _send_event_frame() (async, ~10s delay)
        |
        +-- kind == "motion"
                |
                v
            _handle_motion()
                |
                +-- Wait 5s for Ring CV processing
                +-- Query history API for cv_properties.person_detected
                |
                +-- person_detected == True?
                |       |
                |       +-- Send text: "🔔 Front Door: Person detected at door"
                |       +-- _send_event_frame() (async, ~10s delay)
                |
                +-- person_detected == False?
                        |
                        +-- Log and skip (no notification)
```

### Frame Extraction Pipeline

```
_send_event_frame()
        |
        +-- Wait 8s for Ring to transcode recording
        +-- Get signed video URL via async_recording_url()
        +-- Download MP4 via aiohttp (30s timeout)
        +-- Extract first frame: ffmpeg -i event.mp4 -vframes 1 -q:v 2 event.jpg
        +-- Delete MP4, keep frame
        |
        v
analyze_frame() — Claude Haiku Vision
        |
        +-- Load OAuth token from ~/.openclaw/.anthropic-oauth-cache
        +-- Encode frame as base64
        +-- POST to https://api.anthropic.com/v1/messages
        |       Headers: Authorization: Bearer <oauth-token>
        |                anthropic-beta: oauth-2025-04-20
        |       Model: claude-haiku-4-5-20251001
        |
        +-- Parse structured JSON response
        |
        v
check_departure_arrival() — Roomba automation
        |
        v
send_imessage_image() — BB attachment + caption
        |
        +-- POST multipart to /api/v1/message/attachment (image)
        +-- POST JSON to /api/v1/message/text (description caption)
```

### Deduplication

Two layers prevent duplicate notifications:

1. **Library-level**: `RingEvent.is_update` flag — Ring sends duplicate FCM pushes for the same event (first without image, second with). Skip when `is_update == True`.
2. **Application-level**: In-memory dict `_recent_events` maps event ID → timestamp. Same event ID within 5 minutes is skipped.

## Component 3: Vision-Triggered Automation

### Vision Analysis Prompt

The listener sends each camera frame to Claude Haiku with a structured JSON prompt:

```
Analyze this front door camera image. Respond with ONLY valid JSON:
{
  "description": "<1 sentence describing the scene>",
  "people": ["<name or unknown>"],
  "dogs": ["<name or unknown>"],
  "direction": "<arriving|departing|unclear>"
}

Known residents: Dylan (man), Julia (woman with long brown hair).
Known dogs: large brown/gold dog with dark black face;
            Coconut (white and pink pitbull).
```

### Decision Logic

```python
has_dylan = "dylan" in people
has_julia = "julia" in people
both_people = has_dylan and has_julia
both_dogs = len(dogs) >= 2

# ALL FOUR must be present for automation to trigger
if not both_people or not both_dogs:
    return  # no automation

if direction == "departing":
    start_roombas(location)

elif direction == "arriving":
    dock_roombas(location)
```

### Doorbell → Location → Roomba Mapping

| Doorbell ID | Location | Start Command | Dock Command |
|-------------|----------|---------------|-------------|
| 684794187 | Crosstown | `crosstown-roomba start all` | `crosstown-roomba dock all` |
| 697442349 | Cabin | `roomba start floomba` + `roomba start philly` | `roomba dock floomba` + `roomba dock philly` |

Crosstown Roombas use dorita980 MQTT via MacBook Pro SSH. Cabin Roombas use Google Assistant text API.

### Cooldown

A 2-hour cooldown per location per action prevents re-triggering:
- After starting Roombas at Crosstown, won't start again for 2 hours
- Dock cooldown is independent from start cooldown
- Cooldown is in-memory only (resets on listener restart)

### Notification Messages

| Scenario | Message |
|----------|---------|
| Departure detected | "🧹 Starting Roombas at crosstown — everyone left for a walk!" |
| Arrival detected | "🏠 Welcome home! Docking Roombas at crosstown." |

## Authentication & Credentials

### Ring Auth
- **Method**: OAuth 2.0 Resource Owner Password Credentials (masquerades as Android app)
- **Client ID**: `ring_official_android` (hardcoded in library)
- **2FA**: SMS/email-based, required on first auth (interactive on Mini)
- **Token refresh**: Automatic via oauthlib, persisted by `token_updater` callback
- **Config**: `~/.config/ring/config.yaml` (email + password, chmod 600)
- **Cache**: `~/.config/ring/token-cache.json` (chmod 600)

### Claude API (Vision)
- **Method**: OAuth Bearer token from Claude Max subscription
- **Token source**: `~/.openclaw/.anthropic-oauth-cache` → `claudeAiOauth.accessToken`
- **Header**: `Authorization: Bearer <token>` + `anthropic-beta: oauth-2025-04-20`
- **Model**: `claude-haiku-4-5-20251001` (fast, cheap, multimodal)
- **Token refresh**: Handled externally by `ai.openclaw.usage-token-push` LaunchAgent (pushes from local Mac every 30min)
- **Failure mode**: If token expired, vision analysis is skipped silently (notifications still work, just without AI description)

### BlueBubbles API
- **Password**: `BLUEBUBBLES_PASSWORD` from `~/.openclaw/.secrets-cache`
- **Text endpoint**: `POST /api/v1/message/text?password=<pw>`
- **Attachment endpoint**: `POST /api/v1/message/attachment?password=<pw>` (multipart, requires `name` field)
- **Chat GUID**: `any;-;dylanbochman@gmail.com` (Dylan DM)
- **Method**: `private-api` (uses Messages.app Private API via BB)

### FCM Credentials
- **Purpose**: Firebase Cloud Messaging registration for Ring push notifications
- **Cache**: `~/.openclaw/ring-listener/fcm-credentials.json`
- **Lifecycle**: Generated on first listener start, persisted and reused across restarts
- **Refresh**: Library calls `credentials_updated_callback` when credentials change

## Timing

| Step | Latency | Notes |
|------|---------|-------|
| Ring event → FCM push | ~1s | Ring cloud to Firebase to listener |
| Person detection check | +5s | Wait for Ring CV processing + history API call |
| Text notification sent | +0.5s | BB API call |
| Recording available | +8s | Ring cloud transcoding |
| Video download | +2-5s | Depends on recording size (~3MB typical) |
| Frame extraction | +0.1s | ffmpeg single frame |
| Vision analysis | +2-3s | Claude Haiku API call |
| Image + caption sent | +1s | BB attachment + text API calls |
| **Total: ding → full notification** | **~12-15s** | Text arrives instantly, image + AI follows |
| **Total: motion → full notification** | **~18-20s** | Includes CV wait + vision analysis |

## Operational Notes

### Checking Listener Status
```bash
launchctl list | grep ring-listener    # PID and exit code
tail -f /tmp/ring-listener.log         # live logs
```

### Restarting Listener
```bash
launchctl unload ~/Library/LaunchAgents/ai.openclaw.ring-listener.plist
launchctl load ~/Library/LaunchAgents/ai.openclaw.ring-listener.plist
```

### Log Format
```
[2026-03-24 14:45:08] Event listener started — waiting for Ring events...
[2026-03-24 14:50:12] Event: kind=motion device=Front Door doorbot_id=684794187 state=ringing
[2026-03-24 14:50:17] NOTIFY: 🔔 Front Door: Person detected at door
[2026-03-24 14:50:25] Vision raw: {"description":"...","people":["Dylan","Julia"],"dogs":["unknown","Coconut"],"direction":"departing"}
[2026-03-24 14:50:25] DEPARTURE DETECTED at crosstown: Dylan + Julia + both dogs leaving!
[2026-03-24 14:50:25] ROOMBA: crosstown-roomba start all
```

### Known Limitations

1. **Battery doorbells sleep** — `ring snapshot` returns empty when doorbell is asleep between events. Frame extraction uses recordings instead (available after events).
2. **Cabin doorbell has no Ring Protect** — no video/frame for Cabin events. Text notifications still work, but no image or vision analysis.
3. **WiFi health data unavailable** — `ring health` shows `None` for WiFi name/signal on these doorbell models. Connection status ("online") is available.
4. **Vision accuracy** — Claude Haiku does scene description, not face recognition. It uses contextual clues (hair color, build, dog breed) to identify residents. May occasionally misidentify or miss people in poor lighting.
5. **Direction detection** — "arriving" vs "departing" depends on body orientation in the fisheye frame. May report "unclear" in ambiguous cases, which does NOT trigger automation.
6. **OAuth token expiry** — If the Claude Max OAuth cache expires and isn't refreshed, vision analysis silently degrades. Notifications and event detection continue working.
7. **FCM reliability** — Firebase push connections can drop. The `KeepAlive` LaunchAgent restarts the listener if it crashes, and FCM credentials are persisted across restarts.

### Deployment

Files are deployed via `dotfiles-pull.command`:
- Skills copied to `~/.openclaw/skills/ring-doorbell/` (real copies, not symlinks)
- `ring` wrapper copied to `~/.openclaw/bin/ring`
- Plist manually deployed to `~/Library/LaunchAgents/`
- Venv and config are Mini-only (not in repo)
