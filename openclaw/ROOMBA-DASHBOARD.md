# Roomba Dashboard

**Port 8553**  
**URL:** `http://dylans-mac-mini:8553`  
**Service:** `ai.openclaw.roomba-dashboard`

Two-home Roomba status and automation dashboard. It distinguishes Crosstown's
live local readiness from Cabin's narrower Assistant-confirmed running state,
explains the current vacancy-cleaning decision, and is reachable on the home
LAN and Tailscale tailnet.

## What It Shows

- **Both/Crosstown/Cabin selector** — compare the homes side by side or focus
  the entire dashboard on one location
- **Crosstown live-local cards** — current battery, phase, bin, tank, and the
  guarded vacancy controller's readiness classification
- **Cabin Assistant-status cards** — current cleaning/stopped state from exact
  read-only Google Assistant queries; ambiguous replies remain unverified
- **Home automation summary** — verified occupancy, schedule, next 6:00 AM
  evaluation, latest protected decision, and safety-hold explanation
- **Automation Pause** — temporarily suppress automatic Roomba starts per
  location (1h/3h/8h/Indef); manual starts remain available through the
  guarded CLIs
- **Occupied-home protection** — automatic Fi departure starts require a
  complete fresh network observation showing both residents absent. A collar
  traveling without the dog cannot start the robots around someone who stayed
  home; missing presence evidence also suppresses the command.
- **Cleaning & Decision History** — monthly dog-walk activity plus protected
  Crosstown vacancy-controller outcomes with hover details

## Data Sources

| Source | Frequency | Data |
|--------|-----------|------|
| Crosstown guarded Roomba CLI | 5 min cache | Live local battery, phase, bin, and tank through the MBP rest980 services |
| Cabin guarded Roomba CLI | 15 min cache | Read-only Google Assistant running/stopped response; no physical command. The longer cache keeps routine status traffic below the daily request quota. |
| Protected canonical presence | On demand | Hash-verified, freshness-bounded per-home occupancy summary; people and raw evidence are not published |
| Crosstown vacancy decisions | On demand | Latest evaluation plus owner-only per-day 6 AM/vacancy-transition decisions |
| Dog Walk history JSONL | On demand | Roomba start/dock events per walk |
| Automation pause state | Real-time | Per-location pause expiry |

## Locations

| Location | Roombas |
|----------|---------|
| Cabin (Phillipston) | Floomba + Philly (Google Assistant) |
| Crosstown (West Roxbury) | Roomba Combo 10 Max + J5 (`dorita980` MQTT) |

## Files

| File | Path |
|------|------|
| Server | `openclaw/bin/roomba-dashboard.py` → `~/.openclaw/bin/roomba-dashboard.py` |
| Cabin Roomba skill | `openclaw/skills/roomba/` → `~/.openclaw/skills/roomba/` |
| LaunchAgent | `openclaw/launchagents/ai.openclaw.roomba-dashboard.plist` |
| Snooze state | `~/.openclaw/dog-walk/snooze.json` |
| Run history | `~/.openclaw/dog-walk/history/YYYY-MM-DD.jsonl` |
| Daily decisions | `~/.openclaw/vacant-roomba/crosstown/runs/YYYY-MM-DD.json` |
| Latest decision | `~/.openclaw/vacant-roomba/crosstown/latest-status.json` |
| Logs | `~/.openclaw/logs/roomba-dashboard.{log,err.log}` |

## Known Limitations

- Cabin Assistant status cannot assert battery, bin, dock, error, or safe-start
  readiness. An ambiguous or failed response is reported as unverified, not as
  proof a robot is offline. Battery and maintenance detail remain app-only.
- Assistant quota failures are reduced to a bounded error code and a concise UI
  explanation; provider tracebacks are never returned by the dashboard API.
- Crosstown live status depends on the MBP rest980 services and guarded CLI.
  If that path is unavailable, the dashboard reports readiness unavailable and
  the vacancy controller fails closed.
- The daily 6:00 AM continuation currently applies only to Crosstown. Cabin
  retains vacancy-transition cleaning.

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
