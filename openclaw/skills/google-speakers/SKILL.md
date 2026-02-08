---
name: google-speakers
description: Control Google Nest/Cast speakers. Use when asked about speakers, announcements, volume, playing music, or TTS.
allowed-tools: Bash(speaker:*)
metadata: {"openclaw":{"emoji":"S","requires":{"bins":["speaker"]}}}
---

# Google Speaker Control

Control Google Nest/Cast speakers via the `speaker` CLI. Uses Cast protocol over Tailscale subnet routing.

## Available Commands

### Show all speakers
```bash
speaker status
```

### Set volume (0-100)
```bash
speaker volume <name> 75
```

### Mute / unmute
```bash
speaker mute <name>
speaker unmute <name>
```

### Playback control
```bash
speaker play <name>       # resume
speaker pause <name>
speaker stop <name>
```

### Text-to-speech announcement
```bash
speaker tts <name> "Dinner is ready"
```

### Cast a media URL
```bash
speaker cast <name> "https://example.com/audio.mp3"
```

## Speakers

### Crosstown (Boston — 19 Crosstown Ave)
- **Bedroom speaker** — Google Nest Mini (`192.168.165.146`)
- **Living Room speaker** — Nest Audio (`192.168.165.113`)

These speakers are at **Crosstown only**. The Cabin (Philly) speakers are controlled via `catt`/`spogo` in the `spotify-speakers` skill.

Speaker names are fuzzy-matched (e.g. "bed" for Bedroom, "living" for Living Room).

## Network

Speakers are on the Crosstown LAN (`192.168.165.0/24`). The Mac Mini reaches them via Tailscale subnet routing through `dylans-mac` (must be awake at Crosstown).

Speaker IPs are configured in `~/.openclaw/speakers.json`.

## Notes

- Always run `speaker status` first to check reachability before sending commands
- TTS uses Google Translate TTS — works for short messages, no API key needed
- Volume should be set before TTS/cast for best results
- If speakers show UNREACHABLE, the subnet router (`dylans-mac`) may be asleep or offline
- Cast protocol connects on port 8009 — no authentication needed on the LAN
