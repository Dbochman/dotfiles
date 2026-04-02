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

## Dog Walk Mode

**IMPORTANT: During walk hours (8-10 AM, 11 AM-1 PM, 5-8 PM), ALWAYS use `dog-walk-start` instead of `roomba start`.** This starts Roombas AND activates return monitoring (Fi GPS + WiFi + Ring motion), so they auto-dock when Dylan/Julia return. Without this, Roombas run until battery dies.

```bash
dog-walk-start cabin       # Start Roombas + return monitoring at cabin
dog-walk-start crosstown   # Start Roombas + return monitoring at crosstown
```

Use bare `roomba start` ONLY for non-walk cleaning (cleaning day, routine, specific room, etc.) where you don't need auto-dock on return.

When Dylan replies "start roombas" to a Ring doorbell dog walk prompt, the **ring-listener handles everything directly** — it detects the reply via BB message polling, starts the Roombas, and begins return monitoring. The agent does NOT need to act on these replies.

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

## Skill Boundaries

This skill controls Roombas at the **Cabin only** (Floomba + Philly via Google Assistant).

For related tasks, switch to:
- **crosstown-roomba**: Roomba control at Crosstown (Boston) — different robots, different protocol (MQTT via MacBook Pro)
- **cabin-routines**: Full cabin routines (away, welcome home, goodnight) that include Roomba start/dock alongside lights, thermostats, and audio
- **ring-doorbell**: Automated dog walk detection triggers Roomba start/dock at the cabin via iMessage confirmation prompt
- **presence**: Check if anyone is home at the cabin before starting Roombas

## Troubleshooting

- If commands fail, the Google Assistant OAuth token may need refreshing — run `roomba setup` on the Mac Mini
- Ensure the Roombas are powered on, connected to wifi, and registered in Google Home
- The Mac Mini must have internet access to reach Google Assistant APIs
