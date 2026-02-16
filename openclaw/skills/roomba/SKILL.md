---
name: roomba
description: Control iRobot Roomba vacuums at the Cabin (Philly). Use when asked to vacuum, start/stop/dock the Roomba, clean the house, or anything about Floomba or Philly (the two Roombas).
allowed-tools: Bash(roomba:*)
metadata: {"openclaw":{"emoji":"🧹","requires":{"bins":["roomba"]}}}
---

# Roomba Control (Cabin)

Control two iRobot Roomba 105 Combo vacuums at the **Cabin (Philly)** via Google Assistant text commands. The Roombas are registered in Google Home.

## Roombas

| Name | Full Name | Model |
|------|-----------|-------|
| floomba | Floomba | Roomba 105 Combo |
| philly | Philly | Roomba 105 Combo |

## Available Commands

### Start vacuuming
```bash
roomba start floomba
roomba start philly
```

### Stop vacuuming
```bash
roomba stop floomba
roomba stop philly
```

### Send back to dock
```bash
roomba dock floomba
roomba dock philly
```

### Check status
```bash
roomba status floomba
roomba status philly
```

### List known Roombas
```bash
roomba list
```

## Usage Notes

- Names are fuzzy-matched: "floo" matches Floomba, "phil" matches Philly
- Commands go through Google Assistant → Google Home → iRobot cloud → Roomba
- Requires internet connectivity (not local control)
- Response text from Google Assistant is printed — may say "OK, starting Floomba" or similar
- If response is empty, the command likely still worked (Google Assistant sometimes doesn't return text for device commands)
- The Roombas are vacuum+mop combos — "start" runs the default cleaning mode

## Routine Integration

This skill works well with cabin routines:

- **Away / Leaving**: Start both Roombas after setting eco mode
- **Welcome Home**: Dock both Roombas
- **Cleaning day**: Start both Roombas on demand

Example for Away routine:
```bash
roomba start floomba
roomba start philly
```

Example for Welcome Home:
```bash
roomba dock floomba
roomba dock philly
```

## Troubleshooting

- If commands fail, the Google Assistant OAuth token may need refreshing — run `roomba setup` on the Mac Mini
- Ensure the Roombas are powered on, connected to wifi, and registered in Google Home
- The Mac Mini must have internet access to reach Google Assistant APIs
