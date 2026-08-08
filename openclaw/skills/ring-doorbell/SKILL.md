---
name: ring-doorbell
description: Check Ring doorbell and driveway-camera status, battery, events, and wifi health. Use when asked about a front door, doorbell ring, driveway or front-yard motion, or Ring device status. NOT for locks or alarm (Ring Alarm not supported).
allowed-tools: Bash(ring:*)
metadata: {"openclaw":{"emoji":"D","requires":{"bins":["ring"]}}}
---

# Ring Cameras and Doorbells

Check Ring device status, event history, and connectivity via the Ring cloud API.

## Devices

| Name | Safe binding | Role | Location |
|------|--------------|------|----------|
| Front Door | `front_door` | Doorbell | Crosstown |
| Front Door | `front_door` | Doorbell (shared) | Cabin |
| Sliding Door | `driveway` | Driveway/front-yard camera | Cabin |

Provider identifiers remain inside the exact runtime binding. The normalized
home-event bus accepts `driveway` only at Cabin and never guesses a site for an
unknown device. Driveway motion is not a legacy dog-walk departure signal.

## Commands

### Check status (all doorbells)
```bash
ring status
```
Shows name, model, battery level, firmware, last event, and wifi signal for each doorbell.

### Recent events
```bash
ring events          # last 10 ding/motion events
ring events 25       # last 25 events
```

### Device health
```bash
ring health
```
WiFi name, signal strength (RSSI), signal category per device.

### Get latest video URL
```bash
ring video              # latest recording URL
ring video 7620878...   # specific recording by ID
```
Returns a pre-signed S3 download URL (expires after some time). Requires Ring Protect.

### List recent recordings
```bash
ring videos         # last 5 recordings with URLs
ring videos 10      # last 10
```
Shows timestamp, event type, duration, person detection, and download URL for each.

### Capture a snapshot
```bash
ring snapshot                       # saves to /tmp/ring-snapshot-<id>.jpg
ring snapshot /tmp/front-door.jpg   # custom path
```
Takes a live snapshot from the doorbell camera. May fail if doorbell is asleep (battery models).

### Download a recording
```bash
ring download 7620878129758806347 /tmp/recording.mp4
```
Downloads the full MP4 video file. Get recording IDs from `ring videos` or `ring events`.

## IMPORTANT: Do NOT use `ring-doorbell` CLI directly

Always use the `ring` command (custom wrapper). Never `pip install ring-doorbell` globally or run the library's built-in CLI. The wrapper handles venv paths and token caching.

## Architecture

```
Ring devices <-cloud-> Ring API <-HTTPS-> ring_doorbell (venv) <- ring-api.py <- ring (bash) <- OpenClaw
```

Cloud-only. Auth via Ring account (email + password + 2FA on first use). Tokens auto-refresh after initial setup.

## Troubleshooting

### "2fa_required"
First-time setup needs interactive 2FA. Run `ring status` on the Mini terminal — Ring will send a code via SMS/email. Enter it when prompted.

### "auth_failed"
Check `~/.config/ring/config.yaml` on Mac Mini. Credentials must match your Ring account.

### "rate_limited" or HTTP 429
Too many auth attempts. Wait a few minutes. Token caching prevents this under normal use.

## Skill Boundaries

This skill handles on-demand Ring device CLI queries only. The independent
`ai.openclaw.ring-event-listener` service owns the sole persistent FCM
connection, normalized home-event publication, direct dings, and the narrow
safe-site signal consumed by dog-walk automation; do not start another Ring
listener from this skill.

The home-event camera worker may call the private exact-binding snapshot
boundary for Cabin `driveway`/`front_door` and Crosstown `front_door` evidence.
That boundary accepts safe site/alias keys only; it does not expose provider
identifiers to the event journal, status projection, or OpenClaw prompt.

For related tasks, switch to:
- **dog-walk**: Automated dog walk detection + Roomba automation (uses Ring for return monitoring only)
- **presence**: Check who is home
- **roomba**: Direct Roomba control at the Cabin
- **crosstown-roomba**: Direct Roomba control at Crosstown
- "lock", "alarm", "arm/disarm" -> Ring Alarm NOT supported
- "thermostat", "temperature" -> `nest-thermostat` skill
