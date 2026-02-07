---
name: applescript
description: Control macOS system functions via AppleScript. Use when asked about system volume, notifications, app control, display brightness, or Do Not Disturb.
allowed-tools: Bash(osascript:*)
metadata: {"openclaw":{"emoji":"A"}}
---

# AppleScript System Control

Run AppleScript commands via `osascript` for macOS system control.

## Common Commands

### Volume control
```bash
# Get current volume (0-100)
osascript -e 'output volume of (get volume settings)'

# Set volume (0-100)
osascript -e 'set volume output volume 50'

# Mute/unmute
osascript -e 'set volume output muted true'
osascript -e 'set volume output muted false'
```

### Notifications
```bash
osascript -e 'display notification "message here" with title "Title"'
```

### Say text aloud (text-to-speech)
```bash
osascript -e 'say "Hello from the cabin"'
```

### Application control
```bash
# Open an app
osascript -e 'tell application "Music" to activate'

# Quit an app
osascript -e 'tell application "Music" to quit'

# List running apps
osascript -e 'tell application "System Events" to get name of every process whose background only is false'
```

### Music app control
```bash
# Play/pause
osascript -e 'tell application "Music" to playpause'

# Next track
osascript -e 'tell application "Music" to next track'

# Current track info
osascript -e 'tell application "Music" to get {name, artist} of current track'
```

### System info
```bash
# Screen brightness (0.0-1.0) — may not work on Mac Mini without display
osascript -e 'tell application "System Events" to get value of slider 1 of group 1 of window "Control Center" of application process "ControlCenter"'
```

## Notes

- Always use `osascript -e` for single-line commands
- For multi-line scripts, use `osascript << 'EOF' ... EOF`
- Mac Mini may not have a display connected — skip brightness commands
- The `say` command speaks through connected speakers
- Be careful with volume — check current level before changing
