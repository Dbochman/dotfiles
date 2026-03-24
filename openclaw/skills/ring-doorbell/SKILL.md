---
name: ring-doorbell
description: Check Ring doorbell status, battery, events, and wifi health. Use when asked about the doorbell, front door, who rang the bell, motion at the door, or Ring device status. NOT for locks or alarm (Ring Alarm not supported).
allowed-tools: Bash(ring:*)
metadata: {"openclaw":{"emoji":"D","requires":{"bins":["ring"]}}}
---

# Ring Doorbell

Check **Ring doorbell** status, event history, and connectivity via the Ring cloud API.

## Devices

| Name | ID | Type | Location |
|------|-----|------|----------|
| Front Door | 684794187 | cocoa_doorbell_v3 | Crosstown |
| Front Door | 697442349 | cocoa_doorbell_v3 (shared) | Cabin |

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

## Real-Time Notifications

A persistent listener (`ai.openclaw.ring-listener`) runs as a LaunchAgent on the Mini. It receives Ring events via FCM push and sends iMessage alerts to Dylan:

- **Doorbell dings**: instant text notification + camera frame + AI description
- **Motion with person detected**: instant notification (trusts FCM `state=human`), followed by multi-frame video analysis (5 frames from recording sent to Claude Haiku)
- **Generic motion** (animals, cars): silently ignored

### Dog Walk Automation

When the listener detects 2+ people departing with 2+ dogs:
1. **Starts Roombas** immediately
2. **Begins FindMy polling** every 5 minutes via Peekaboo screenshots of the FindMy app
3. **Docks Roombas silently** when FindMy shows the person back on Crosstown Ave
4. **Safety fallback**: auto-docks after 2 hours if no return detected

If Ring detects arrival (2+ people + dogs arriving) before FindMy polling triggers, Roombas dock immediately and polling stops.

To check the listener:
```bash
launchctl list | grep ring-listener    # should show PID
tail -f /tmp/ring-listener.log         # live logs
```

To restart:
```bash
launchctl unload ~/Library/LaunchAgents/ai.openclaw.ring-listener.plist
launchctl load ~/Library/LaunchAgents/ai.openclaw.ring-listener.plist
```

## IMPORTANT: Do NOT use `ring-doorbell` CLI directly

Always use the `ring` command (custom wrapper). Never `pip install ring-doorbell` globally or run the library's built-in CLI. The wrapper handles venv paths and token caching.

## Architecture

```
Ring Doorbell <-cloud-> Ring API <-HTTPS-> ring_doorbell (venv) <- ring-api.py <- ring (bash) <- OpenClaw
```

Cloud-only. Auth via Ring account (email + password + 2FA on first use). Tokens auto-refresh after initial setup.

## Troubleshooting

### "2fa_required"
First-time setup needs interactive 2FA. Run `ring status` on the Mini terminal — Ring will send a code via SMS/email. Enter it when prompted.

### "auth_failed"
Check `~/.config/ring/config.yaml` on Mac Mini. Credentials must match your Ring account.

### "rate_limited" or HTTP 429
Too many auth attempts. Wait a few minutes. Token caching prevents this under normal use.

## Disambiguation

- "doorbell", "front door", "who rang", "motion at door" -> this skill
- "lock", "alarm", "arm/disarm" -> Ring Alarm NOT supported
- "thermostat", "temperature" -> `nest-thermostat` skill
