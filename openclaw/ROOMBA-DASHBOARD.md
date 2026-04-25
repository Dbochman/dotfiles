# Roomba Dashboard

**Port 8553**  
**URL:** `http://dylans-mac-mini:8553`  
**Service:** `ai.openclaw.roomba-dashboard`

Roomba status and controls dashboard covering both Crosstown and Cabin, including automation snooze controls and run-history visualization.

## What It Shows

- **Crosstown Roomba cards** — real-time battery, cleaning phase, bin status, tank level (via `dorita980` MQTT path)
- **Cabin Roomba cards** — last mission outcome, duration, area cleaned (via iRobot Cloud API)
- **Snooze controls** — temporarily disable Roomba automation per location (1h/3h/8h/Indef)
- **Calendar heatmap** — monthly run history per location with hover details

## Data Sources

| Source | Frequency | Data |
|--------|-----------|------|
| Crosstown Roomba (`dorita980`) | 5 min cache | Real-time battery, phase, bin, tank via SSH to MBP |
| Cabin Roomba (iRobot Cloud) | 10 min cache | Last mission outcome via Gigya + AWS SigV4 REST API |
| Dog Walk history JSONL | On demand | Roomba start/dock events per walk |
| Snooze state | Real-time | Per-location snooze expiry |

## Locations

| Location | Roombas |
|----------|---------|
| Cabin (Phillipston) | Floomba + Philly (Google Assistant) |
| Crosstown (West Roxbury) | Roomba Combo 10 Max + J5 (`dorita980` MQTT) |

## Files

| File | Path |
|------|------|
| Server | `openclaw/bin/roomba-dashboard.py` → `~/.openclaw/bin/roomba-dashboard.py` |
| iRobot Cloud API | `openclaw/skills/cabin-roomba/irobot-cloud.py` → `~/.openclaw/skills/cabin-roomba/irobot-cloud.py` |
| LaunchAgent | `openclaw/launchagents/ai.openclaw.roomba-dashboard.plist` |
| Snooze state | `~/.openclaw/dog-walk/snooze.json` |
| Run history | `~/.openclaw/dog-walk/history/YYYY-MM-DD.jsonl` |
| Logs | `~/.openclaw/logs/roomba-dashboard.{log,err.log}` |

## Known Limitations

- Cabin roombas use Google Assistant responses (natural language, not strict JSON)
- Crosstown collectors depend on SSH reachability to MBP; if MBP is offline, those status calls may time out

## Troubleshooting

Check service:

```bash
ssh mac-mini "launchctl list | grep ai.openclaw.roomba-dashboard"
```

Restart service (KeepAlive auto-restarts):

```bash
ssh mac-mini "launchctl stop ai.openclaw.roomba-dashboard"
```

Check logs:

```bash
ssh mac-mini "tail -20 ~/.openclaw/logs/roomba-dashboard.log"
ssh mac-mini "tail -20 ~/.openclaw/logs/roomba-dashboard.err.log"
```
