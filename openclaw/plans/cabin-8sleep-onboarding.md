# Cabin Eight Sleep Pod Onboarding — Plan

## Status

Pending — Pod not yet purchased. CLI scaffolding landed 2026-05-23 (commits `cff4b45`, `7a65864`).

## Context

A second Eight Sleep Pod will be installed at the Cabin. It will be linked to the **same Eight Sleep accounts** as the Crosstown Pod (Dylan and Julia each keep one account that now owns two devices). The `8sleep` CLI has been preemptively refactored to accept `--location <crosstown|cabin>` on every subcommand, but the actual Cabin code path is unverified until a real second device exists.

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

### 4. Verify user-scoped routing (the open question)

User-scoped endpoints (`temp`, `off`, `on`, `away`) hit `users/<uid>/...` paths. Eight Sleep's API routes these to the user's "current device" — there's no obvious per-device targeting in the request. Test before relying on it:

```bash
# Physically at Crosstown, with Cabin Pod also online
8sleep --location cabin away dylan start
# Check Eight Sleep app: did Cabin Pod's Dylan side enter away mode? Or Crosstown's?
```

**If the Cabin Pod responds correctly:** great, no further code changes needed for the user-scoped path. Move to step 5.

**If the Crosstown Pod responds instead** (likely outcome — Eight Sleep apps use a "current device" concept):

- Option A: Set primary device first. Look for a `users/<uid>/current-device` PUT (pyEight hints at this) — call it with `{"deviceId": "<target>"}` before every user-scoped action, then restore afterward. Cleanest if the endpoint exists.
- Option B: Add `deviceId` (or `currentDeviceId`) to the PUT body of `users/<uid>/temperature` and `users/<uid>/away-mode`. Try both field names. Update `cmd_temp`, `cmd_off`, `cmd_on`, `cmd_away` in `openclaw/skills/8sleep/8sleep-api.py` to thread the resolved device ID into the body when `--location` is explicit.
- Option C (last resort): Spawn the request from a different user session. Probably not feasible from a single account.

Whichever option works, the `resolve_device_id(token_data, location)` helper already exists — wire it into the user-scoped commands.

### 5. Add Cabin block to vacancy actions

In `openclaw/workspace/scripts/vacancy-actions.sh`:

**Cabin vacant block** (currently lines ~163-194) — add the away-start loop alongside lights/thermostat/Roombas:

```bash
# Eight Sleep Pod — enable away mode (Cabin)
for side in dylan julia; do
  if 8sleep --location cabin away "$side" start >> "$LOG_FILE" 2>&1; then
    log "  Cabin Eight Sleep $side: AWAY MODE ON"
  else
    log "  ERROR: Failed to enable Cabin Eight Sleep $side away mode"
  fi
done
```

**Cabin re-occupied branch** (currently lines ~196-199; only clears marker today) — expand to mirror Crosstown's re-occupied branch:

```bash
elif [[ "$cabin_occupancy" == "occupied" ]] && [[ -f "$MARKER_DIR/cabin" ]]; then
  log "Cabin occupied again — ending Eight Sleep away mode and clearing vacancy marker"

  # End Eight Sleep away mode (resume smart schedule)
  for side in dylan julia; do
    if 8sleep --location cabin away "$side" end >> "$LOG_FILE" 2>&1; then
      log "  Cabin Eight Sleep $side: AWAY MODE OFF"
    else
      log "  ERROR: Failed to end Cabin Eight Sleep $side away mode"
    fi
  done

  rm -f "$MARKER_DIR/cabin"
fi
```

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

Add Cabin Eight Sleep entries to the Cabin vacancy/restore tables (`openclaw/VACANCY-AUTOMATION.md`), mirroring the Crosstown rows added on 2026-05-23.

### 10. Deploy + smoke test

```bash
git commit -m "feat(8sleep): wire up Cabin Pod" && git push
ssh dbochman@dylans-mac-mini 'cd ~/dotfiles && git pull --ff-only && bash ~/dotfiles/openclaw/bin/dotfiles-pull.command'
```

Then on the Mini, with `~/.openclaw/.secrets-cache` already sourced by the gateway:

```bash
8sleep --location cabin status                    # smoke
8sleep --location cabin away dylan start          # vacancy-style trigger
# wait, check Eight Sleep app for Cabin Pod state
8sleep --location cabin away dylan end            # restore
```

Force a vacancy state transition by editing `~/.openclaw/presence/state.json` (or just leave the Cabin for ~30 minutes and let presence-detect do its thing). Tail `~/.openclaw/logs/vacancy-actions.log` for the new Cabin Eight Sleep lines.

## Risks

- **User-scoped routing is unverified** — step 4 may turn into a small subproject. Worst case is a one-time spike of API exploration to find the right per-device field. The CLI is designed so this change lives entirely inside `cmd_temp` / `cmd_off` / `cmd_on` / `cmd_away`; no caller has to change.
- **Token cache collision** — both Pods are under the same Eight Sleep account, so token cache (`~/.config/eightctl/token-cache.json`) is shared. No action needed; just be aware that auth issues affect both Pods at once.
- **Away mode is per-user, not per-device** — if you're at Crosstown and put "Dylan away" on the Cabin Pod, Eight Sleep may interpret this as "Dylan is away from his current bed", which is the Crosstown one. This is the same concern as step 4 from the user's perspective; the fix is the same.

## Out-of-scope follow-ups (capture, don't act)

- Refactor `vacancy-actions.sh` into a data-driven YAML/TOML config table mapping `location → [actions]`. Worth doing if a third location ever appears, not before.
- Add a per-location `8sleep status --all` that fetches both Pods in one call for dashboard efficiency.
- Wire Cabin Pod water level into CrisisMode checks (parity with Crosstown if that's already done).
