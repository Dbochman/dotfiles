# Cat Care Dashboard

**Port 8554** · `http://dylans-mac-mini:8554`

The Cat Care dashboard brings the household's cat-specific signals and guarded
actions into one view. It is organized around care—weights, litter-box visits,
food, and water—rather than around vendor accounts or a generic device grid.

## Experience

- **Both / Crosstown / Cabin filter** scopes stations and litter-box activity.
- **Transfer automation** shows whether both exact feeder directions are owned
  and armed by the event bus, whether Cabin and Crosstown litter evidence is
  paired and fresh, how many actions are pending, and which feeders are
  actually paused by OpenClaw. `Armed` describes policy readiness; it does not
  claim that a feeder schedule is currently paused or that the current vacancy
  cycle has qualified.
- **Cat profiles** show current Whisker weight and the direction of recent
  samples.
- **Whisker stations** show connectivity, state, waste level, litter level,
  cycle count, and recent activity for the exact enrolled robot at each home.
- **Petlibro stations** show live feeder and fountain telemetry when the cloud
  account returns those devices. Each feeder gets a separate exact schedule
  readback: the card shows the provider's master switch, effective number of
  active saved meals, and observation time. A paused master reports zero active
  meals even though its definitions remain saved. An OpenClaw-owned vacancy
  pause is marked for automatic resume; an ambiguous transition is surfaced as
  automation attention. An explicit unavailable state replaces an unverified
  schedule value, and an explicit empty state replaces stale or invented values
  when no devices are reporting. If Petlibro verifies the master switch but
  temporarily rejects the separate meal-list query, the card preserves that
  verified on/paused state while omitting the meal count.
- **Attention state** calls out unavailable integrations, stale or incomplete
  transfer evidence, unknown feeder outcomes, offline robots, and full or
  nearly-full waste drawers.

## Controls and safety

The dashboard exposes only three narrow controls:

- Start a clean cycle on an exact enrolled Litter-Robot.
- Dispense 1–3 portions from an exact enrolled Petlibro feeder.
- Pause or resume all scheduled meals on an exact enrolled Petlibro feeder.

The schedule control is available only when a fresh state is known. It names
the selected home in a confirmation step, changes the whole schedule once, and
then requires a verified readback. Pausing the schedule does not delete meal
definitions or block manual feeding. An uncertain result is shown as a failure
and is never retried automatically.

When the event bus owns a feeder pause, the manual schedule button is disabled
and labeled `Vacancy-managed`. The action worker—not the dashboard—restores
that exact schedule after a later qualifying paired-home return. A manually
paused schedule is never adopted or automatically resumed. Both exact feeder
directions were activated on `2026-08-27`; every feeder card identifies that
ownership, while the overview marks unavailable paired evidence as attention
and the action worker fails closed.

Browser mutations require a per-process bearer token embedded into the served
page, a same-origin request, a bounded JSON body, and an exact allowlisted
selector. The underlying skill wrappers retain their own locking, durable
audit, validation, and outcome-unknown protections. Reset, night-light,
enrollment, individual-plan editing, and arbitrary vendor commands are
intentionally absent from this care-focused surface.

Reads are available on the home LAN and Tailscale tailnet. The service is not
intended for public-internet exposure or router port forwarding.

## Runtime

The dashboard caches a combined Whisker/Petlibro snapshot for 60 seconds. A
new page load and the Refresh button bypass that cache so feeder schedule cards
start from a fresh provider readback. One
Whisker account session supplies both robot state and recent cat weights and
activity, avoiding separate cloud logins for every card. Confirmed actions
invalidate the snapshot immediately.

| Component | Tracked source | Runtime path |
|-----------|----------------|--------------|
| Server | `openclaw/bin/cat-dashboard.py` | `~/.openclaw/bin/cat-dashboard.py` |
| LaunchAgent | `openclaw/launchagents/ai.openclaw.cat-dashboard.plist` | `~/Library/LaunchAgents/ai.openclaw.cat-dashboard.plist` |
| Whisker integration | `openclaw/skills/litter-robot/` | `~/.openclaw/skills/litter-robot/` |
| Petlibro integration | `openclaw/skills/petlibro/` | `~/.openclaw/skills/petlibro/` |
| Feeder automation status | `openclaw/bin/home_event_action.py` | `~/.openclaw/home-events/state/feeder-schedule-suspensions.json` |
| Transfer coverage | `openclaw/bin/home_event_bus.py` | Sanitized `home-eventctl status` fields only |
| Logs | — | `~/.openclaw/logs/cat-dashboard.{log,err.log}` |

## Checks

```bash
curl -fsS -o /dev/null -w 'Cat dashboard HTTP %{http_code}\n' http://127.0.0.1:8554/
curl -fsS http://127.0.0.1:8554/api/status | python3 -m json.tool
launchctl print gui/$(id -u)/ai.openclaw.cat-dashboard
```
