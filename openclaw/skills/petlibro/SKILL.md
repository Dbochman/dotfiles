---
name: petlibro
description: Control Petlibro smart pet devices (feeder and fountain) at Crosstown and Cabin. Use when asked about feeding the cats, cat food, pet feeder, water fountain, how much the cats drank, feeding schedule, or anything about the Petlibro devices. NOT for Litter Robot (different device).
allowed-tools: Bash(petlibro:*)
metadata: {"openclaw":{"emoji":"🐱","requires":{"bins":["petlibro"]}}}
---

# Petlibro Pet Device Control

Control Petlibro smart feeders and fountains at Crosstown and Cabin via the Petlibro cloud API.

## Devices

| Location | Device | Model | Notes |
|----------|--------|-------|-------|
| Crosstown | Granary Smart Feeder | PLAF103 | Double bowl, battery backup |
| Crosstown | Dockstream 2 Smart Cordless Fountain | PLWF116 | Battery-powered, water weight sensor |
| Cabin | Feeder (unplugged) | — | Seasonal, shows offline |
| Cabin | Fountain (unplugged) | — | Seasonal, shows offline |

## Commands

### Check all device status
```bash
petlibro status
```
Shows food level, water level, battery, next feed time, filter status for all devices.

### Manual feed
```bash
petlibro feed feeder          # 1 portion
petlibro feed feeder 3        # 3 portions
```

### Today's water intake
```bash
petlibro water fountain
```

### Today's feeding schedule
```bash
petlibro schedule feeder
```

### List all devices with details
```bash
petlibro devices
```

## Device Name Matching

Names are fuzzy-matched. Use any of:
- `feeder`, `feed`, `food` → matches the Granary Smart Feeder
- `fountain`, `water`, `drink` → matches the Dockstream Fountain
- Or any part of the device name (e.g., `granary`, `dockstream`)

## Architecture

```
Petlibro Devices ←─cloud─→ api.us.petlibro.com ←─HTTPS─→ petlibro-api.py (Mac Mini)
```

Cloud-only. Uses secondary account (`bochmanspam@gmail.com`) to avoid single-session conflicts with the phone app.

## Troubleshooting

### "auth_failed"
The API token expired or the secondary account credentials changed. Check `~/.config/petlibro/config.yaml` on Mac Mini.

### "device_not_found"
Device name didn't match. Run `petlibro devices` to see available devices.

### Cabin devices showing offline
The Cabin feeder and fountain are unplugged (seasonal). They'll show online when reconnected.

### Filter/cleaning overdue
The fountain shows negative filter days when overdue. Remind Dylan/Julia to replace the filter.
