---
name: dog-walk
description: Automated dog walk detection and Roomba control. Detects departures via Fi GPS collar, starts Roombas, and monitors return via Ring motion + WiFi + Fi GPS. Use when asked about dog walks, Roomba automation during walks, or walk tracking.
allowed-tools: Bash(dog-walk:*)
metadata: {"openclaw":{"emoji":"D","requires":{"bins":["fi-collar","crosstown-roomba","roomba"]}}}
---

# Dog Walk Automation

Detects dog walks via **Fi GPS collar** (departure) and manages Roomba automation with **multi-signal return detection** (Ring motion + WiFi + Fi GPS).

## How It Works

### Departure Detection (Fi GPS only)

A polling loop checks Potato's Fi collar GPS every 3 minutes during walk hours. The listener treats the collar's GPS/geofence result as the source of truth for which home Potato is at, remembers the last home geofence he was inside, and starts Roombas when he leaves that home's geofence.

**Trigger conditions:**
- 2 consecutive Fi GPS readings outside geofence, >=3 min apart
- Both readings must be < 10 min old (not stale)
- Only during walk hours, using the last home geofence Potato was inside as the departure anchor
- Only when no walk is already active

**Walk hours:** 8-10 AM, 11 AM-1 PM, 5-8 PM

**Pre-checks:**
- **Time-of-day filter**: only active during walk hours

**Per-location Roomba commands:**

| Location | Start | Dock |
|----------|-------|------|
| Crosstown | `crosstown-roomba start all` | `crosstown-roomba dock all` |
| Cabin | `roomba start floomba` + `roomba start philly` | `roomba dock floomba` + `roomba dock philly` |

**Fi GPS geofences:**

| Location | Radius |
|----------|--------|
| Crosstown (19 Crosstown Ave, West Roxbury) | 150m |
| Cabin (95 School House Rd, Phillipston) | 300m |

### Return Detection (multi-signal)

After departure, the return monitor uses three signals — any one triggers Roomba docking:

| Signal | Interval | How it works |
|--------|----------|-------------|
| **Ring motion** | Event-driven | Person detected at doorbell during monitoring |
| **WiFi / network presence** | Every 60s | ARP scan (Crosstown via MBP) or Starlink gRPC (Cabin). Detects phone reconnecting to WiFi. |
| **Fi GPS** | Every 60s | Polls Potato's Fi collar GPS. Docks when Potato re-enters home geofence. |

- 2 minutes after departure, a network scan identifies **who left**
- Safety fallback: auto-docks after 2 hours if no return detected

### Manual Trigger

```bash
dog-walk-start <location>    # "cabin" or "crosstown"
```

Starts Roombas and signals the listener to begin return monitoring via inbox IPC.

## State Tracking

- Current state: `~/.openclaw/dog-walk/state.json`
- Daily history: `~/.openclaw/dog-walk/history/YYYY-MM-DD.jsonl`
- Per-walk routes: `~/.openclaw/dog-walk/routes/<location>/<YYYY-MM-DD>/<walk_id>.json`
- Inbox (IPC): `~/.openclaw/dog-walk/inbox/`

## LaunchAgent

The listener runs as a persistent `KeepAlive` LaunchAgent (`ai.openclaw.dog-walk-listener`).

To check:
```bash
launchctl list | grep dog-walk-listener    # should show PID
tail -f ~/.openclaw/logs/dog-walk-listener.log
```

To restart:
```bash
launchctl unload ~/Library/LaunchAgents/ai.openclaw.dog-walk-listener.plist
launchctl load ~/Library/LaunchAgents/ai.openclaw.dog-walk-listener.plist
```

## Skill Boundaries

This skill handles dog walk detection and Roomba automation triggered by Fi GPS departure.

For related tasks, switch to:
- **ring-doorbell**: Check Ring doorbell status, events, video, health (CLI only)
- **fi-collar**: Direct Fi collar GPS/battery queries
- **roomba**: Direct Roomba control at the Cabin
- **crosstown-roomba**: Direct Roomba control at Crosstown
- **cabin-routines** / **crosstown-routines**: Full home routines
- Vacancy automation (`com.openclaw.vacancy-actions`) is separate — starts Roombas on vacancy
