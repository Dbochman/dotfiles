# Cat Care Dashboard

**Port 8554** · `http://dylans-mac-mini:8554`

The Cat Care dashboard brings the household's cat-specific signals and guarded
actions into one view. It is organized around care—weights, litter-box visits,
food, and water—rather than around vendor accounts or a generic device grid.

## Experience

- **Both / Crosstown / Cabin filter** scopes stations and litter-box activity.
- **Cat profiles** show current Whisker weight and the direction of recent
  samples.
- **Whisker stations** show connectivity, state, waste level, litter level,
  cycle count, and recent activity for the exact enrolled robot at each home.
- **Petlibro stations** show live feeder and fountain telemetry when the cloud
  account returns those devices. An explicit empty state replaces stale or
  invented values when no devices are reporting.
- **Attention state** calls out unavailable integrations, offline robots, and
  full or nearly-full waste drawers.

## Controls and safety

The dashboard exposes only two narrow controls:

- Start a clean cycle on an exact enrolled Litter-Robot.
- Dispense 1–3 portions from an exact enrolled Petlibro feeder.

Browser mutations require a per-process bearer token embedded into the served
page, a same-origin request, a bounded JSON body, and an exact allowlisted
selector. The underlying skill wrappers retain their own validation and
outcome-unknown protections. Reset, night-light, enrollment, and arbitrary
vendor commands are intentionally absent from this care-focused surface.

Reads are available on the home LAN and Tailscale tailnet. The service is not
intended for public-internet exposure or router port forwarding.

## Runtime

The dashboard caches a combined Whisker/Petlibro snapshot for 60 seconds. One
Whisker account session supplies both robot state and recent cat weights and
activity, avoiding separate cloud logins for every card. Manual refreshes
bypass the cache. Confirmed actions invalidate the snapshot immediately.

| Component | Tracked source | Runtime path |
|-----------|----------------|--------------|
| Server | `openclaw/bin/cat-dashboard.py` | `~/.openclaw/bin/cat-dashboard.py` |
| LaunchAgent | `openclaw/launchagents/ai.openclaw.cat-dashboard.plist` | `~/Library/LaunchAgents/ai.openclaw.cat-dashboard.plist` |
| Whisker integration | `openclaw/skills/litter-robot/` | `~/.openclaw/skills/litter-robot/` |
| Petlibro integration | `openclaw/skills/petlibro/` | `~/.openclaw/skills/petlibro/` |
| Logs | — | `~/.openclaw/logs/cat-dashboard.{log,err.log}` |

## Checks

```bash
curl -fsS -o /dev/null -w 'Cat dashboard HTTP %{http_code}\n' http://127.0.0.1:8554/
curl -fsS http://127.0.0.1:8554/api/status | python3 -m json.tool
launchctl print gui/$(id -u)/ai.openclaw.cat-dashboard
```
