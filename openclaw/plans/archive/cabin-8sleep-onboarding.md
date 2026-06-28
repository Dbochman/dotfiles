# Cabin Eight Sleep Pod Onboarding — Plan

## Status

Complete — Cabin Pod 5 was discovered and wired into location-safe vacancy
automation on 2026-06-27. The earlier CLI scaffolding landed 2026-05-23
(commits `cff4b45`, `7a65864`).

## Outcome

- Both Pod device IDs are durable in the managed secrets-cache generator.
- User-scoped household-set selection is treated as a semantic relocation. The
  `home` command selects the detected location and leaves it current; ordinary
  writes fail unless that Pod is already current.
- Presence reconciliation runs independently per person, so both-together and
  split-household states make the other Pod away for the correct side without
  replaying lights, HVAC, locks, or Roombas.
- Isolated API and vacancy tests replace live state-file mutation as the
  verification path.

## Context

A second Eight Sleep Pod was installed at the Cabin and linked to the same
household as the Crosstown Pod. Dylan and Julia each keep one user identity
across both devices.

## Goal

Flip the Cabin Pod from "scaffolded but unused" to "fully wired into vacancy automation, dashboards, and morning briefings" with as little code change as possible on install day.

## Non-Goals

- Building a new CLI from scratch — the location-aware `8sleep` CLI is already in place.
- Refactoring `vacancy-actions.sh` into a data-driven config table. With only two locations, parallel inline blocks are fine.
- Adding Cabin-side sleep tracking to `8sleep-snapshot.sh` — Dylan/Julia's "last night" is whichever Pod they slept on; the user-scoped sleep endpoint already returns that. Revisit only if it turns out to return the wrong device.

## Steps

### 1. Capture Cabin Pod device ID

After unboxing and pairing the second Pod to Dylan's Eight Sleep account:

```bash
# On Mini, with secrets loaded
set -a; source ~/.openclaw/.secrets-cache; set +a
8sleep raw users/me/devices
```

(Or `8sleep raw users/<uid>/current-device` while physically near each Pod to confirm which ID maps to which house.) Record both device IDs.

### 2. Add device IDs to secrets cache

Append to `~/.openclaw/.secrets-cache` on the Mini (chmod 600):

```
EIGHTSLEEP_CROSSTOWN_DEVICE_ID=<crosstown-id>
EIGHTSLEEP_CABIN_DEVICE_ID=<cabin-id>
```

Both should be set together — once `EIGHTSLEEP_CABIN_DEVICE_ID` is present without its Crosstown peer, `--location crosstown` would fall back to `current-device` while `--location cabin` uses the explicit ID, which can produce confusing inconsistencies if the Eight Sleep app shifts "current device" between Pods.

### 3. Verify device-scoped routing

```bash
8sleep --location crosstown status   # should show Crosstown Pod
8sleep --location cabin status       # should show Cabin Pod
8sleep --location crosstown device   # confirm serial matches Crosstown unit
8sleep --location cabin device       # confirm serial matches Cabin unit
```

If `status` and `device` both correctly distinguish the Pods, the device-scoped path is good.

### 4. Verify user-scoped routing

Live API inspection established that `household/users/<uid>/current-set` is a
semantic relocation, not neutral request routing: selecting a set makes that
Pod current and marks the same person's side on the other Pod away.

The final CLI therefore has two distinct behaviors:

- `8sleep --location <house> home <side>` deliberately relocates one person,
  ends away mode on the target, and verifies both Pods' side assignments.
- `temp`, `off`, `on`, and manual `away` never select a set. They fail closed
  unless the requested location is already that person's current Pod; thermal
  writes also require the person not to be away there.

### 5. Add presence-driven home reconciliation

`vacancy-actions.sh` reads both occupancy fields and both sticky person
locations in one snapshot. For each valid `crosstown|cabin` person location,
it invokes `home` only when that person's last-verified-location marker differs.
This makes the person's detected Pod current and the other Pod away, including
split-household states. Failed or unknown locations do not advance markers.

### 6. Update `8sleep-snapshot.sh` if needed

Currently it pre-captures last-night sleep data for both sides into `/tmp/8sleep-{dylan,julia}-latest.txt`. The user-scoped sleep endpoint should return Dylan's last night on whichever Pod he slept on, so this likely Just Works.

If it returns the wrong Pod's data after the Cabin Pod is online, snapshot both locations:

```bash
snapshot_side dylan crosstown
snapshot_side julia crosstown
snapshot_side dylan cabin
snapshot_side julia cabin
```

And teach the morning briefing agent which one to read based on presence state (`~/.openclaw/presence/state.json`).

### 7. Update the home dashboard

`openclaw/bin/home-dashboard.py:202` collects `8sleep status` once. Decide:

- **Phase 1 (likely sufficient):** keep single-Pod display, defaulting to Crosstown. Cabin Pod state is implicit (it's in away mode when occupants are at Crosstown, and vice versa).
- **Phase 2 (if useful):** add a second collector for the Cabin Pod, render both side-by-side in `renderEightSleep()`. Skip until there's actual demand.

### 8. Update SKILL.md

In `openclaw/skills/8sleep/SKILL.md`, update the Locations table with the Cabin Pod's specifics (model, size, install date). Remove the "(TBD — to be added)" placeholder. Remove or refine the "Note: Eight Sleep's per-side endpoints are user-scoped..." caveat depending on what step 4 discovered.

### 9. Update VACANCY-AUTOMATION.md

Document the per-person Cabin/Crosstown reconciliation in
`openclaw/VACANCY-AUTOMATION.md`.

### 10. Deploy + smoke test

Keep the vacancy LaunchAgent unloaded while installing the CLI and script. Run
the isolated suites first, then execute `vacancy-actions.sh` once against the
unchanged live state. Existing general vacancy markers prevent lights/HVAC/
locks/Roombas from replaying; missing per-person markers cause only verified,
idempotent `home` calls. Confirm the marker contents and both Pods' assignments,
then bootstrap the LaunchAgent again.

Do not edit `state.json` or clear live vacancy markers for testing. The isolated
verification commands are `python3 openclaw/tests/test-8sleep-api.py` and
`bash openclaw/tests/test-vacancy-actions.sh`.

## Risks

- **User-scoped routing is current-set based** — Eight Sleep ignores device IDs
  on per-user writes. Current-set changes are relocations and must never be
  used as temporary routing with rollback.
- **Token cache collision** — both Pods are under the same Eight Sleep account, so token cache (`~/.config/eightctl/token-cache.json`) is shared. No action needed; just be aware that auth issues affect both Pods at once.
- **Away mode is per-user and current-set scoped** — losing either location's
  device ID must fail closed rather than guessing which Pod to change.

## Out-of-scope follow-ups (capture, don't act)

- Refactor `vacancy-actions.sh` into a data-driven YAML/TOML config table mapping `location → [actions]`. Worth doing if a third location ever appears, not before.
- Add a per-location `8sleep status --all` that fetches both Pods in one call for dashboard efficiency.
- Wire Cabin Pod water level into CrisisMode checks (parity with Crosstown if that's already done).
