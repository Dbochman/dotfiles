---
name: hue-lights
description: Control Philips Hue smart lights. Use when asked about lights, lighting, brightness, room lighting, or turning lights on/off.
allowed-tools: Bash(hue:*)
metadata: {"openclaw":{"emoji":"L","requires":{"bins":["hue"]}}}
---

# Hue Lights Control

Control Philips Hue lights via the `hue` CLI. Credentials are managed via 1Password.

## Available Commands

### Show room status
```bash
hue status
```

### Turn a room on or off
```bash
hue on <room>
hue on <room> 50        # on at 50% brightness
hue off <room>
```

### Set brightness (0-100%)
```bash
hue bri <room> 75
```

### Set color
```bash
hue color <room> warm       # warm white
hue color <room> cool       # cool white
hue color <room> daylight   # daylight
hue color <room> red        # named colors: red, orange, yellow, green, blue, purple, pink
```

### Activate a scene
```bash
hue scene <room> <scene-name>
```

### All lights on/off
```bash
hue all-on
hue all-off
```

### List individual lights
```bash
hue lights
```

## Rooms

There are 8 rooms:
- **Kitchen** (2 lights)
- **Living room** (3 lights)
- **Bathroom** (4 lights)
- **Hallway** (1 light)
- **Bedroom** (4 lights)
- **Office** (3 lights)
- **Solarium** (3 lights)
- **Staircase** (1 light)

Room names are fuzzy-matched — use any substring (e.g. "bed" for Bedroom, "living" for Living room).

## Notes

- Always run `hue status` first to show the user current state before making changes
- Brightness is 0-100 (percent)
- Some lights may show as UNREACHABLE if powered off at the switch
- When asked to set a mood, combine brightness and color (e.g. `hue color bedroom warm` then `hue bri bedroom 30`)
