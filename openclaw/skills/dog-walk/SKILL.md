---
name: dog-walk
description: Automated dog walk detection and Roomba control. Detects departures via Fi GPS collar, starts Roombas, and monitors return via Ring motion + WiFi + Fi GPS. Use when asked about dog walks, Roomba automation during walks, or walk tracking.
allowed-tools: Bash(dog-walk:*)
metadata: {"openclaw":{"emoji":"D","requires":{"bins":["fi-collar","crosstown-roomba","roomba"]}}}
---

# Dog Walk Automation

Detects dog walks via **Fi GPS collar** (departure) and manages Roomba automation with **multi-signal return detection** (Ring motion + WiFi + Fi GPS).

## Current Model

- Departure detection is **Fi-only**. Ring and presence no longer decide whether a walk started.
- The listener uses Potato's Fi GPS/geofence result to choose the home, and stores the last confirmed in-geofence home as `home_location`.
- Walks now get immutable `walk_id` and `origin_location` fields at departure.
- Route files are persisted during return monitoring at `~/.openclaw/dog-walk/routes/<location>/<YYYY-MM-DD>/<walk_id>.json`.
- Route files include `distance_m`, `point_count`, inferred `end_location`, and `is_interhome_transit`.
- Inter-home transits are filtered out of the dashboard route-summary API, so future map views only show same-home walks.
- Walk hours now cover the full day in three contiguous sections: `7 AM-12 PM`, `12 PM-5 PM`, `5 PM-9 PM`.

## How It Works

### Departure Detection (Fi GPS only)

A polling loop checks Potato's Fi collar GPS every 3 minutes during walk hours. The listener treats the collar's GPS/geofence result as the source of truth for which home Potato is at, remembers the last home geofence he was inside, and starts Roombas when he leaves that home's geofence.

**Trigger conditions:**
- 2 consecutive Fi GPS readings outside geofence, confirmed after a time threshold
- Both readings must be < 10 min old (not stale)
- Only during walk hours, using the last home geofence Potato was inside as the departure anchor
- Only when no walk is already active

**Base station disconnect acceleration:** When the Fi collar disconnects from its base station (`connection` transitions from `"Base"` to `"Unknown"`/`"User"`), the departure polling switches from 3min to 30s and the confirmation threshold drops from 3min to 60s. Backyard time does not trigger departure — the 150m geofence is large enough that GPS still shows Potato at home even with base station BLE out of range (~30-50m).

**Walk hours:** 7 AM-12 PM, 12-5 PM, 5-9 PM

**Pre-checks:**
- **Time-of-day filter**: only active during walk hours
- **Base-station echo filter**: when Fi API is slow to transition from Rest to Walk, it returns base station coords as pet position. If pet coords match a home location within 5m and connection is not "Base", the reading is discarded as stale.

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
| **WiFi / network presence** | Every 30s (after 10min) | ARP scan (Crosstown via MBP) or Starlink gRPC (Cabin). Detects phone reconnecting to WiFi. **Ignored for first 10 minutes** — phones linger on WiFi at the front door. |
| **Fi GPS** | Every 30s | Polls Potato's Fi collar GPS. Docks when Potato re-enters home geofence. Base-station echo detection prevents false "at home" readings. |

- Departure GPS point is seeded as the first route point for dashboard maps
- 2 minutes after departure, a network scan identifies **who left**
- WiFi return signals are suppressed for the first 10 minutes (phones stay connected at front door)
- On return, a final Fi GPS point is captured before docking (for route completeness)
- An iMessage notification is sent on return with walk duration and which signal triggered it
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

## Deploy Notes

Recent dog-walk changes touched these paths:

- `openclaw/skills/dog-walk/dog-walk-listener.py`
- `openclaw/dog-walk-dashboard.py`
- `openclaw/skills/fi-collar/fi-api.py`

If deploying to the Mac Mini, make sure the updated files are present under `~/.openclaw/`, then restart:

```bash
launchctl kickstart -k gui/$(id -u)/ai.openclaw.dog-walk-listener
launchctl kickstart -k gui/$(id -u)/ai.openclaw.dog-walk-dashboard
```

Quick verification:

```bash
tail -20 ~/.openclaw/logs/dog-walk-listener.log
tail -20 ~/.openclaw/logs/dog-walk-dashboard.log
curl -s http://localhost:8552/api/routes?days=30 | jq '.meta'
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
