# Cat Care Dashboard

**Port 8554** · `http://dylans-mac-mini:8554`

The Cat Care dashboard brings the household's cat-specific signals and guarded
actions into one view. It is organized around care—weights, litter-box visits,
food, and water—rather than around vendor accounts or a generic device grid.

## Experience

- **Both / Crosstown / Cabin filter** scopes stations and the combined cat
  activity timeline. A move between homes appears in both location views.
- **Feeding between homes** leads with one plain-English household state, such
  as `Cats are at Cabin`, then says which home's meals are on, which are paused,
  and whether the paused schedule will turn back on automatically. Separate
  Cabin/Crosstown meal readbacks, litter-box freshness, and waiting changes
  remain visible without exposing policy-direction or event-bus terminology.
  Split human occupancy is a normal state: when the cats remain at one home
  while a person occupies each, the other feeder stays paused and the card
  explains that directly instead of presenting a false automation warning.
- **Cat profiles** show current Whisker weight, the direction of recent
  samples, and a compact time-scaled chart with the observation dates, reading
  count, and recent weight range.
- **Whisker stations** show connectivity, state, waste level, litter level,
  and cycle count for the exact enrolled robot at each home.
- **Cat activity** combines named, weighted litter visits, provider-confirmed
  scheduled feedings, and confirmed moves between homes. Each litter visit is
  one row built from the timestamp-aligned Whisker pet-weight record; raw
  `Cat Detected`, `DFILevelPercent`, clean-cycle progress, and `Clean Cycle
  Complete` records are not rendered as separate activity. Petlibro feed rows
  come only from successful scheduled-plan dispense records and show the actual
  portion count. A successful event-bus feeder transfer becomes one
  plain-English `Cats moved to …` row rather than exposing action-journal codes.
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
  verified on/paused state while omitting the meal count. Scheduled-feeding
  history is collected independently, bounded to confirmed successes, and
  never includes plan IDs, device IDs, raw provider text, or manual feeds.
- **Attention state** calls out unavailable integrations, stale or incomplete
  transfer evidence, unknown feeder outcomes, offline robots, and full or
  nearly-full waste drawers. Paired litter readiness is evaluated from the
  Whisker observer and both fresh site polls, independently of unrelated
  top-level event-bus degradation; broader bus health is shown as a separate
  advisory and does not mislabel healthy feeder protection as unavailable. A
  prior feeder-readback error stops appearing as current attention after the
  action worker or a fresh dashboard readback confirms the expected schedule.

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

If a transfer stops before any command because the occupied destination's
schedule is disabled or unavailable, the failed outcome remains visible in the
event journal. A newer litter event at that destination must complete the same
30-minute settle period before the current vacancy cycle may reserve one new
attempt. Reusing the same event is blocked, and any prior command attempt,
confirmed action, pending claim, or uncertain outcome prevents a retry.

When the event bus owns a feeder pause, the manual schedule button is disabled
and labeled `Managed automatically`. The card explains that meals are paused
while the home is vacant and will turn back on automatically. The action
worker—not the dashboard—restores that exact schedule after a later qualifying
paired-home return. A manually paused schedule is never adopted or
automatically resumed. Both exact feeder directions were activated on
`2026-08-27`; the action worker still fails closed whenever the underlying
state cannot be verified.

Browser mutations require a per-process bearer token embedded into the served
page, a same-origin request, a bounded JSON body, and an exact allowlisted
selector. The underlying skill wrappers retain their own locking, durable
audit, validation, and outcome-unknown protections. Reset, night-light,
enrollment, individual-plan editing, and arbitrary vendor commands are
intentionally absent from this care-focused surface.

Reads are available on the home LAN and Tailscale tailnet. The service is not
intended for public-internet exposure or router port forwarding.

## Runtime

The dashboard caches a combined Whisker/Petlibro/event-bus snapshot for 60
seconds. A
new page load and the Refresh button bypass that cache so feeder schedule cards
start from a fresh provider readback. One
Whisker account session supplies both robot state and recent cat weights and
activity, avoiding separate cloud logins for every card. Petlibro supplies a
sanitized 30-day window of successful scheduled dispenses, and the action
worker status supplies at most eight confirmed recent cat transfers. Confirmed
actions invalidate the snapshot immediately.

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
