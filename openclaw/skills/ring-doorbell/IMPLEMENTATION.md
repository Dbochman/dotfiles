# Ring Doorbell Integration — Implementation Spec

## Overview

The Ring doorbell integration provides four capabilities for OpenClaw:

1. **CLI skill** — query doorbell status, events, video, and health on demand
2. **Real-time event listener** — persistent FCM push listener that sends iMessage notifications with multi-frame video analysis and AI-powered scene descriptions
3. **Vision-triggered home automation** — when Claude Haiku detects 2+ people departing or arriving with 2+ dogs, Roombas are automatically started or docked
4. **FindMy return-home tracking** — after departure, polls Apple FindMy via Peekaboo every 5 minutes to detect return to Crosstown Ave and proactively dock Roombas

## Architecture

```
Ring Doorbell Hardware
        |
        v (cloud)
Ring Cloud API (oauth.ring.com)
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
        |                   +---> Claude Haiku Vision API (multi-frame video analysis)
        |                   |         |
        |                   |         +---> Structured JSON: people, dogs, direction
        |                   |
        |                   +---> BlueBubbles API (iMessage notifications)
        |                   |         |
        |                   |         +---> Text alerts + camera frame attachments
        |                   |
        |                   +---> Roomba CLIs (departure/arrival automation)
        |                   |         |
        |                   |         +---> crosstown-roomba start/dock all
        |                   |         +---> roomba start/dock floomba/philly
        |                   |
        |                   +---> FindMy polling (return-home detection)
        |                             |
        |                             +---> Peekaboo (screenshot FindMy app)
        |                             +---> Claude Haiku (is pin on Crosstown Ave?)
        |                             +---> Dock Roombas when near home
        |
        +---> Ring Protect (subscription, Crosstown only)
                  |
                  +---> Video recording URLs (pre-signed S3)
                  +---> Multi-frame extraction via ffmpeg
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
| `~/.openclaw/ring-listener/frames/` | Temporary directory for video frames and MP4s (cleaned after send) |
| `~/.openclaw/ring-listener/findmy/` | Temporary directory for FindMy screenshots (cleaned after analysis) |
| `~/Library/LaunchAgents/ai.openclaw.ring-listener.plist` | Deployed LaunchAgent |
| `~/Applications/Peekaboo.app` | TCC wrapper for Peekaboo CLI (Screen Recording + Accessibility grants) |

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
        |       +-- _send_event_recording() (async, ~20-30s delay)
        |
        +-- kind == "motion"
                |
                v
            _handle_motion(state=event.state)
                |
                +-- state == "human"? → person_detected = True (trust FCM)
                +-- else: wait 5s, query history API for cv_properties.person_detected
                |
                +-- person_detected == True?
                |       |
                |       +-- _send_event_recording() (silent, no text notification)
                |
                +-- person_detected == False?
                        |
                        +-- Log and skip
```

### Recording & Multi-Frame Vision Pipeline

```
_send_event_recording()
        |
        +-- Wait 8s for Ring to transcode recording
        +-- download_recording() with retry (3 attempts, 0/10/15s backoff)
        |       +-- Get signed video URL via async_recording_url()
        |       +-- Download MP4 via aiohttp (30s timeout)
        |       +-- 404 = not ready yet, retry
        |       +-- Extract preview frame at 3s mark via ffmpeg
        |       +-- Return (mp4_path, frame_path)
        |
        v
analyze_video() — Claude Haiku Multi-Frame Vision
        |
        +-- extract_multi_frames(mp4, count=5)
        |       +-- ffprobe to get video duration
        |       +-- Sample 5 frames centered in clip (skip edges)
        |       +-- 18s clip → frames at 3, 6, 9, 12, 15s
        |       +-- Scales proportionally: margin = max(2s, duration/6)
        |
        +-- Load OAuth token from ~/.openclaw/.anthropic-oauth-cache
        +-- Encode all 5 frames as base64 images
        +-- POST to https://api.anthropic.com/v1/messages
        |       5 image content blocks + text prompt
        |       Model: claude-haiku-4-5-20251001
        |
        +-- Parse structured JSON response
        |
        v
        +-- If 1+ people + 1 dog: retry with 10 frames for second dog
        |
        v
check_departure() — Roomba + FindMy automation (departures only)
        |
        +-- Time-of-day filter (8-10 AM, 11 AM-1 PM, 5-8 PM)
        +-- Presence cross-check (skip if confirmed_vacant)
        +-- Accumulate across 10-min sliding window
        +-- 1+ people + 2+ dogs → auto-start Roombas + FindMy polling
        +-- 1+ people + 1 dog → iMessage confirmation to Dylan
        |
        v
send_imessage_image() — BB attachment + caption
        |
        +-- POST multipart to /api/v1/message/attachment (preview frame)
        +-- POST JSON to /api/v1/message/text (description caption)
```

### Deduplication

Two layers prevent duplicate notifications:

1. **Library-level**: `RingEvent.is_update` flag — Ring sends duplicate FCM pushes for the same event (first without image, second with). Skip when `is_update == True`.
2. **Application-level**: In-memory dict `_recent_events` maps event ID → timestamp. Same event ID within 5 minutes is skipped.

## Component 3: Vision-Triggered Automation

### Vision Analysis Prompt

The listener sends 5 frames from each recording to Claude Haiku with a structured JSON prompt:

```
These are 5 frames sampled evenly from a doorbell video clip.
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
# Pre-checks
if not _is_walk_hour():       # 8-10 AM, 11 AM-1 PM, 5-8 PM
    return
if not _is_location_occupied(location):  # skip if confirmed_vacant
    return

# Only departures — arrivals handled by FindMy polling
if direction != "departing":
    return

# Accumulate across 10-minute sliding window
# (people/dogs may pass doorbell in separate motion events)
total_people = max(people_per_event)  # use max per event
total_dogs = sum(dogs_per_event)      # sum across events

if total_people >= 1 and total_dogs >= 2:
    start_roombas(location)
    start_findmy_polling(location)

elif total_people >= 1 and total_dogs == 1:
    send_confirmation_imessage(location)  # ask Dylan
```

### Frame Retry for Second Dog

When initial 5-frame analysis finds people but only 1 dog, the listener automatically retries with 10 frames for better coverage. Dogs may appear briefly or be partially occluded by people in fisheye footage.

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
| Doorbell ding | "🔔 Front Door: Doorbell rang!" + camera frame + AI description |
| Departure (2+ dogs) | "🧹 Starting Roombas at crosstown — everyone left for a walk!" |
| Departure (1 dog) | "🐶 Ring saw X people and 1 dog leaving. Reply 'start roombas'" |
| FindMy tracking started | "📍 Tracking your walk — will dock Roombas when you're back on Crosstown Ave" |
| Walk timeout (2hr) | "⏰ Walk tracking timed out after 2 hours — docking Roombas" |
| Outside walk hours | (logged, no notification) |
| Already vacant | (logged, no notification) |

## Component 4: FindMy Return-Home Tracking

### Overview

When the listener detects a departure (1+ people + 2+ dogs leaving), it starts polling Apple FindMy every 5 minutes to detect the household's return. When the person's FindMy pin appears back on Crosstown Ave, Roombas are docked proactively — before the household reaches the front door.

### How It Works

```
Departure detected
        |
        v
start_findmy_polling(location)
        |
        +-- Wait 5 minutes (just left, no point checking)
        |
        v (every 5 minutes)
capture_findmy()
        |
        +-- Peekaboo screenshot of FindMy app window
        |       peekaboo image --app "Find My" --path <capture.png>
        |
        v
analyze_findmy(capture.png)
        |
        +-- Send screenshot to Claude Haiku
        +-- Prompt: "Is the pin on or near Crosstown Ave?"
        +-- Response: {"street":"<name>", "near_home": true/false, "description":"..."}
        |
        +-- near_home == true?
        |       +-- Dock Roombas silently
        |       +-- Stop polling
        |
        +-- near_home == false?
                +-- Log location, continue polling
                +-- After 2 hours: dock Roombas as safety fallback
```

### Peekaboo Requirements

- **Peekaboo** (`/opt/homebrew/bin/peekaboo` v3.0.0-beta3) installed via Homebrew
- **Screen Recording** + **Accessibility** permissions granted to Peekaboo
- Works from LaunchAgent context (verified) but NOT from SSH sessions (TCC restriction)
- **Find My app must be open** on the Mini with the People tab showing the shared location
- Location sharing: Dylan shares location with `clawdbotbochman@gmail.com` (the Mini's iCloud account)

### FindMy Vision Prompt

```
Look at this FindMy app screenshot. There is a pin on the map showing a person's location.
Respond with ONLY valid JSON:
{
  "street": "<street name the pin is on or nearest to>",
  "near_home": true/false,
  "description": "<1 sentence describing where the pin is>"
}

The person's home is on Crosstown Ave in West Roxbury/Boston.
'near_home' should be true if the pin appears to be ON Crosstown Ave or within ~1 block of it.
```

### Timing & Limits

| Parameter | Value | Notes |
|-----------|-------|-------|
| Poll interval | 5 minutes | Balance between responsiveness and API cost |
| First poll delay | 5 minutes | Skip initial poll (just departed) |
| Maximum duration | 2 hours | Safety fallback — dock Roombas if no return detected |
| Haiku timeout | 30 seconds | Per vision API call |

### Cancellation

FindMy polling is cancelled when:
1. **Haiku confirms near_home** → dock Roombas silently, stop polling
2. **2-hour timeout** → dock Roombas as safety net
3. **Listener restart** → polling state is in-memory only

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
| Person detection (FCM state=human) | instant | Trusted directly from FCM, no API delay |
| Person detection (history fallback) | +5s | Only when FCM state is not "human" |
| Text notification sent | +0.5s | BB API call |
| Recording available | +8-33s | Initial 8s wait + up to 2 retries (10s, 15s) on 404 |
| Video download | +2-5s | Depends on recording size (~3-4MB typical) |
| Multi-frame extraction | +1-2s | 5 ffmpeg calls (ffprobe + 5 frame seeks) |
| Vision analysis (5 frames) | +3-5s | Claude Haiku API call with 5 images |
| Image + caption sent | +1s | BB attachment + text API calls |
| **Total: ding → full notification** | **~15-20s** | Text arrives instantly, image + AI follows |
| **Total: motion → full notification** | **~20-30s** | Text instant, multi-frame analysis follows |

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
4. **Vision accuracy** — Claude Haiku does scene description, not face recognition. It uses contextual clues (hair color, build, dog breed) to identify residents. People are typically identified as "unknown" rather than by name. The count-based automation (2+ people, 2+ dogs) works around this.
5. **Direction detection** — "arriving" vs "departing" depends on body orientation across multiple frames. May report "unclear" in ambiguous cases, which does NOT trigger automation.
6. **Recording availability** — Ring may take 10-30 seconds to process and upload a recording after an event. The listener retries up to 3 times with increasing delays (0s, 10s, 15s) to handle this.
7. **OAuth token expiry** — If the Claude Max OAuth cache expires and isn't refreshed, vision analysis silently degrades. Notifications and event detection continue working.
8. **FCM reliability** — Firebase push connections can drop. The `KeepAlive` LaunchAgent restarts the listener if it crashes, and FCM credentials are persisted across restarts.
9. **FindMy requires open app** — The Find My app must be open on the Mini for Peekaboo screenshots to capture location data. If the app is closed or the window is minimized, FindMy polling will fail silently and fall back to the 2-hour timeout dock.
10. **FindMy TCC restriction** — Peekaboo only works from LaunchAgent/Terminal context, not SSH. The Ring listener runs as a LaunchAgent so this works, but manual testing via SSH will fail.
11. **FindMy polling is in-memory** — Polling state does not survive listener restarts. If the listener crashes mid-walk, FindMy polling stops and Roombas won't auto-dock (the 2-hour cooldown on the start action prevents re-starting them).

### Deployment

Files are deployed via `dotfiles-pull.command`:
- Skills copied to `~/.openclaw/skills/ring-doorbell/` (real copies, not symlinks)
- `ring` wrapper copied to `~/.openclaw/bin/ring`
- Plist manually deployed to `~/Library/LaunchAgents/`
- Venv and config are Mini-only (not in repo)
