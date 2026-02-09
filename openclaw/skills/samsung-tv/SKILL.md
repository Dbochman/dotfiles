---
name: samsung-tv
description: Control Samsung The Frame TV. Use when asked about TV, television, art mode, Samsung Frame, turning on/off the TV, or changing TV input/volume/apps.
allowed-tools: Bash(samsung-tv:*)
metadata: {"openclaw":{"emoji":"T","requires":{"bins":["samsung-tv"]}}}
---

# Samsung TV Control

Control Samsung The Frame TVs via the `samsung-tv` CLI. Uses WebSocket protocol (port 8002) over Tailscale subnet routing.

## Available Commands

### Show TV status
```bash
samsung-tv status
```

### Power on/off
```bash
samsung-tv power <name> on     # Wake-on-LAN (same LAN only!)
samsung-tv power <name> off    # WebSocket power off
```

### Volume / mute
```bash
samsung-tv volume <name> up         # Volume up 1 step
samsung-tv volume <name> down       # Volume down 1 step
samsung-tv volume <name> 5          # Volume up 5 steps
samsung-tv volume <name> -5         # Volume down 5 steps
samsung-tv mute <name>              # Toggle mute
```

### Send remote key
```bash
samsung-tv key <name> KEY_HOME
samsung-tv key <name> KEY_SOURCE
samsung-tv key <name> KEY_HDMI1
samsung-tv key <name> KEY_HDMI2
samsung-tv key <name> KEY_RETURN
samsung-tv key <name> KEY_ENTER
samsung-tv key <name> KEY_UP
samsung-tv key <name> KEY_DOWN
samsung-tv key <name> KEY_LEFT
samsung-tv key <name> KEY_RIGHT
```

### Apps
```bash
samsung-tv app <name> list              # List installed apps
samsung-tv app <name> launch netflix    # Launch by fuzzy name
samsung-tv app <name> launch 3201907018807  # Launch by app ID
samsung-tv app <name> close netflix     # Close app
```

### Art mode (Frame TV only)
```bash
samsung-tv art <name>                   # Show art mode state
samsung-tv art <name> on                # Enter art mode
samsung-tv art <name> off               # Exit art mode
samsung-tv art <name> list              # List available artwork
samsung-tv art <name> select <id>       # Display specific artwork
```

## TVs

### 19 Crosstown Ave
- **Samsung The Frame 65** — QN65LS03BAFXZA, 2022 (`192.168.165.2`, MAC `A0:D7:F3:B2:C0:AC`)

TV names are fuzzy-matched (e.g. "frame" for Samsung The Frame 65).

## Known App IDs

Netflix, YouTube, Spotify, Disney+, Hulu, Prime Video, HBO Max / Max, Apple TV, Plex, Paramount+, Peacock, Tubi — all fuzzy-matched by name.

## Network

TV is on the Crosstown LAN (`192.168.165.0/24`). The Mac Mini reaches it via Tailscale subnet routing through `dylans-mac` (must be awake at Crosstown).

**Wake-on-LAN limitation**: WoL uses broadcast packets which don't traverse Tailscale subnet routing. Power-on only works from `dylans-mac` (same LAN). Power-off works from anywhere via WebSocket.

TV config is in `~/.openclaw/samsung-tvs.json`. Auth tokens are cached at `~/.cache/samsung-tv/<name>/token.txt`.

## Notes

- Always run `samsung-tv status` first to check if the TV is reachable and powered on
- First connection to a new TV requires physical "Allow" on the TV remote
- Art mode commands only work on Samsung The Frame models
- Volume uses remote key simulation (KEY_VOLUP/KEY_VOLDOWN) — there's no direct volume API
- If TV shows UNREACHABLE, the subnet router (`dylans-mac`) may be asleep or offline
