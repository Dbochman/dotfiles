# Cabin Starlink Presence Enrollment and Verification

## Status

READY FOR ATTENDED EXECUTION — the operator helper and strict scanner exist in
source; production Cabin activation remains gated on physical phone testing,
multi-scenario read-only verification at the real cadence, and a downstream-
disabled production canary.

## Outcome

Safely replace the Cabin's permissive Starlink display-name matching with two
exact, site-local `captiveClientId` bindings. Prove that each identifier belongs
to the intended phone, remains stable across ordinary reconnects, exposes usable
lease/idle evidence, and behaves safely at the real 15-minute scan cadence
before the production scanner, vacancy automation, or camera reviewer can use
it.

The enrollment must never disclose a raw provider row or turn an identification
session into a presence transition, vacancy action, camera review, home-event
publication, or outbound message.

## Interim Eight Sleep containment (2026-07-18)

Until Julia's exact Cabin binding is activated, `vacancy-actions.sh` pins her
Eight Sleep home to Crosstown even when the correlated sticky state says Cabin.
The deployed permissive scanner falsely relocated her from Crosstown to the
Cabin on five consecutive days, July 13–17. Each false relocation repeatedly
called the Eight Sleep Cabin `home` operation. Eight Sleep partially changed
the user-scoped routing but never completed the Cabin side assignment. The
command correctly reported failure, but its durable marker remained Crosstown,
so each new state write retried the bad Cabin move and the later Crosstown
correction did not force a repair. Eight Sleep's device-level `awaySides` field
is not treated as a standalone active-away signal; the authoritative repair
readback is Julia's Crosstown `current-set` plus `away-mode = false`.

This containment is deliberately narrower than presence itself: occupancy,
vacancy, and other household actions continue to use the existing correlated
state, while only Julia's Eight Sleep relocation is pinned. The pin releases
automatically only when the deployed `presence-detect.sh validate-config cabin`
command succeeds against the strict production binding. Merely staging an
enrollment session or creating an unvalidated file does not release it.

During activation, keep the mutation jobs stopped, deploy the strict scanner,
verify that `validate-config cabin` succeeds, and then restore the jobs. The
next genuine one-sided Cabin detection may move Julia's Eight Sleep home to the
Cabin. Before that boundary, use an attended manual override only if Julia is
physically at the Cabin and explicitly wants her Cabin Pod active.

## Why this is an attended migration

The repository history never contained a committed exact Cabin device-ID
implementation:

- `c58d071` introduced Starlink `wifiGetClients` but deliberately matched
  display names because iPhone MAC addresses were considered unstable. Julia
  also inherited a generic non-Dylan-iPhone fallback.
- `bdc8012` added sticky locations after sleeping phones and rotating
  fingerprints caused false negatives. Sticky state reduces flicker but can
  retain a false one-sided positive.
- `e44c71d` records a Cabin parser failure caused by the Mini's different Node
  path and a stale deployed scanner. This demonstrated that an unavailable
  scanner can become an unsafe apparent absence.
- `c20ed4c` added the dual-positive ambiguity guard. It protects physical
  actions but cannot correct a false Cabin assignment while the Cabin keeps
  reporting the same false positive.
- `b2c66fd` and `0978b9a` added live evidence and protected exact bindings only
  at Crosstown. Cabin remained name-based.

The new source assumes a distinct 64-hex `captiveClientId`, active DHCP lease,
positive lease lifetime, and bounded `noDataIdleS`. A value-redacted live query
showed that these fields are promising but not uniform across all Starlink
client rows. Git contains no evidence that a phone's opaque identifier survives
ordinary reconnects, iOS private-address changes, a router reboot, or “Forget
This Network.” Those are physical facts to establish, not assumptions to encode.

## Components

- Operator helper: `openclaw/bin/presence-cabin-enroll`
- Strict scanner: `openclaw/workspace/scripts/presence-detect.sh`
- Helper tests: `openclaw/tests/test_presence_cabin_enroll.py`
- Scanner tests: `openclaw/tests/test-presence-cabin-scan.sh`
- Protected enrollment runtime:

  ```text
  ~/.openclaw/presence-enrollment/   0700
  ├── session.lock                   0600
  ├── session.json                   0600; sensitive until cleanup
  ├── candidate-config.json          0600; staging only
  └── safe-shadow-report.json        0600; contains no identifiers
  ```

- Production binding, created only at activation:

  ```text
  ~/.openclaw/presence-devices.json  0600
  ```

- Exact-source deployment approval, created only after the downstream-disabled
  production canary passes:

  ```text
  ~/.openclaw/presence-scanner-approved.sha256  0600; one lowercase hash line
  ```

The helper is intended to run under the Cabin Mini's operator account. It has
no LaunchAgent, cron entry, gateway route, messaging path, or agent skill
permission. Deployment requires no gateway restart.

## Safety invariants

1. The helper reads only the local Starlink `wifiGetClients` endpoint plus
   read-only `launchctl print` state during physical enrollment and activation.
2. It never calls `presence-detect.sh cabin`, writes under
   `~/.openclaw/presence/`, edits vacancy markers, changes a LaunchAgent,
   publishes a home event, captures a camera frame, or sends a message.
3. Provider names, client IDs, MACs, IPs, phone models, and raw rows never enter
   stdout, stderr, logs, the safe report, command arguments, or this plan.
4. Before identification, observed IDs exist in session state only as
   session-keyed fingerprints. After unique attribution, only the two selected
   raw IDs are retained in the owner-only session because they are required for
   the eventual config.
5. The candidate config stays outside the production path throughout the soak.
   Creating `~/.openclaw/presence-devices.json` alone does not authorize a
   routine pull to install the strict scanner: deployment also requires the
   site-local approval file to contain the exact canaried source hash. Do not
   create that approval until the downstream-disabled production canary passes.
6. All enrollment mutations require `--attended` and a real TTY. Production
   promotion additionally requires four explicit confirmations and refuses to
   overwrite any existing config, link, or unsafe path.
7. Unknown, malformed, duplicated, rotating, or ambiguous identity evidence
   fails closed. There is no name, hostname, IP, generic-iPhone, or multiple-ID
   fallback.
8. The current five-minute idle rule is measured, not defended. If it fails
   real sleeping-phone or 15-minute-cadence tests, stop and revise the policy
   and tests before activation.
9. A false absence is the higher privacy risk for camera monitoring. No result
   is accepted merely because it produces fewer false occupied states.
10. Preserve databases, spools, camera cursors, canonical presence state, and
    vacancy markers throughout enrollment and rollback.

## Helper contract

Every command emits one bounded JSON object. Read-only commands are
`preflight`, `status`, and `shadow-report`. Commands that change the protected
enrollment session require `--attended` before the command name.

### Safe preflight

```bash
~/.openclaw/bin/presence-cabin-enroll preflight
```

Reports only aggregate client, identifier, and liveness-shape counts plus
whether a production config already exists. It does not create an enrollment
directory or state file.

### Enrollment state machine

```bash
~/.openclaw/bin/presence-cabin-enroll --attended start
~/.openclaw/bin/presence-cabin-enroll --attended baseline Dylan
~/.openclaw/bin/presence-cabin-enroll --attended identify Dylan
~/.openclaw/bin/presence-cabin-enroll --attended disconnect Dylan --cycle 1
~/.openclaw/bin/presence-cabin-enroll --attended reconnect Dylan --cycle 1
~/.openclaw/bin/presence-cabin-enroll --attended disconnect Dylan --cycle 2
~/.openclaw/bin/presence-cabin-enroll --attended reconnect Dylan --cycle 2
~/.openclaw/bin/presence-cabin-enroll --attended idle-start Dylan
~/.openclaw/bin/presence-cabin-enroll --attended idle-check Dylan --minutes 5
~/.openclaw/bin/presence-cabin-enroll --attended idle-check Dylan --minutes 10
~/.openclaw/bin/presence-cabin-enroll --attended idle-check Dylan --minutes 20
```

Repeat that sequence for Julia. `baseline` stores keyed fingerprints only.
`identify` takes three samples and succeeds only when one fresh new or
idle-reset candidate remains stable across all three. Each reconnect cycle
requires an observable off state (the row is missing or has a complete invalid
lease), at least five seconds between the off and on observations, and the same
selected ID in three fresh reconnect samples. A second new or baseline-reset
fresh identity is a hard stop, covering dual-row private-address rotation. An
idle counter reset while the selected lease remains valid is not disconnect
proof.

The idle checkpoints are measured from `idle-start`; the helper requires them
in 5-, 10-, then 20-minute order and rejects samples outside a narrow attended
window. They record safe buckets and whether the current five-minute rule would
classify the known-connected phone as present. Every checkpoint must classify
the phone as present before staging is allowed. The helper does not silently
change the threshold.

`start` and every physical enrollment command also verify that all four
protected LaunchAgents remain unloaded. If one is restored during the attended
window, the next command fails before querying or changing enrollment state.

### Staging and shadow verification

```bash
~/.openclaw/bin/presence-cabin-enroll status
~/.openclaw/bin/presence-cabin-enroll --attended seal-candidate

~/.openclaw/bin/presence-cabin-enroll --attended shadow-sample \
  --scenario both-present
~/.openclaw/bin/presence-cabin-enroll --attended shadow-sample \
  --scenario dylan-only
~/.openclaw/bin/presence-cabin-enroll --attended shadow-sample \
  --scenario julia-only
~/.openclaw/bin/presence-cabin-enroll --attended shadow-sample \
  --scenario both-away
~/.openclaw/bin/presence-cabin-enroll --attended shadow-sample \
  --scenario return-both

~/.openclaw/bin/presence-cabin-enroll shadow-report
```

`seal-candidate` writes only the private staging config. Shadow samples query
Starlink directly, compare the current rule with operator-supplied ground truth,
and save only booleans, lease state, idle buckets, counts, and timestamps.

The helper's activation gate requires:

- both distinct identities fully enrolled;
- two proven reconnect cycles per phone;
- complete 5-, 10-, and 20-minute idle profiles per phone;
- at least eight shadow samples;
- at least one hour between the first and last samples;
- at least three sample intervals between 14 and 16 minutes, exercising the
  real 15-minute cadence rather than only rapid manual queries or multi-hour
  gaps;
- all five ground-truth scenarios, including a return after both-away;
- zero mismatches or incomplete required evidence.

An extended 24–48-hour soak is optional when identity, lease, or idle evidence
is inconsistent. It is not a default gate: elapsed time alone does not prove
survival across router reboots, iOS updates, or private-address rotation.

### Explicit production promotion

Run only after the activation preflight below has stopped the mutation and
delivery jobs:

```bash
~/.openclaw/bin/presence-cabin-enroll --attended activate \
  --confirm-private-address-reviewed \
  --confirm-idle-policy-reviewed \
  --confirm-shadow-soak-passed \
  --confirm-mutation-jobs-stopped
```

The helper first durably records an activation-prepared state, then creates the
exact production schema at mode `0600` as the final binding-promotion step.
It refuses an existing destination and attempts to remove the redundant staging
config. The result reports whether staging was removed; a retained owner-only
staging file is removed by `cleanup`. It does not deploy or run the scanner.
In addition to the confirmation flag, activation performs read-only
`launchctl print` checks and refuses promotion while any of the four protected
jobs is still loaded.

The production binding permits a manually installed strict scanner to validate
and run during the controlled canary, but it does not approve routine
deployment. The helper intentionally does not create
`presence-scanner-approved.sha256`; that separate exact-source approval is the
post-canary boundary.

An ordinary `production_config_create_failed` error rolls back the exact newly
created inode, leaves no production binding, and keeps the session
`activation_prepared`; correct the filesystem problem and rerun the same
confirmed `activate` command. Any `*_rollback_failed` error is a hard stop:
keep all jobs unloaded, run `status` and config validation, and follow the
rollback procedure according to whether the production capability exists. Do
not use `abort` after activation has started.

Once activation is prepared, enrollment evidence is frozen. Only read-only
status/report commands, an idempotent `activate` retry, and `cleanup` after a
verified production create remain available.

After the strict scanner has been activated and verified, remove the redundant
sensitive enrollment session while preserving the safe report and production
config:

```bash
~/.openclaw/bin/presence-cabin-enroll --attended cleanup \
  --confirm-delete-sensitive-session
```

To abandon an incomplete session without touching any production config:

```bash
~/.openclaw/bin/presence-cabin-enroll --attended abort \
  --confirm-delete-sensitive-session
```

## Phase 0 — prepare and verify the build

Run from `~/dotfiles` before visiting or changing any phone/network setting:

```bash
python3 -m unittest openclaw/tests/test_presence_cabin_enroll.py
bash openclaw/tests/test-presence-cabin-scan.sh
bash openclaw/tests/test-presence-observe.sh
bash openclaw/tests/test-presence-detect.sh
bash openclaw/tests/test-presence-receive.sh
bash openclaw/tests/test-presence-home-events.sh
bash openclaw/tests/test-vacancy-actions.sh
python3 -m unittest openclaw/tests/test_deployment_contracts.py
```

Install the verified helper atomically before its first runtime invocation,
then compare source and installed hashes:

```bash
tmp="$HOME/.openclaw/bin/.presence-cabin-enroll.$$"
/usr/bin/install -m 0755 openclaw/bin/presence-cabin-enroll "$tmp"
/bin/mv -f "$tmp" "$HOME/.openclaw/bin/presence-cabin-enroll"
/usr/bin/shasum -a 256 \
  openclaw/bin/presence-cabin-enroll \
  "$HOME/.openclaw/bin/presence-cabin-enroll"
```

The two hashes must match. This install does not create an enrollment session,
production binding, or service restart.

Confirm:

- the target is the Cabin Mac Mini user with the expected `HOME`;
- `~/.openclaw` is an owner-only, non-symlink directory;
- `grpcurl` reaches the local Starlink endpoint;
- the helper's aggregate preflight contains no provider values;
- the current production binding file is still absent;
- the installed scanner hash, relevant plists, canonical presence state, and
  vacancy markers have been recorded into a protected rollback directory
  without printing their contents.

Do not install a production binding during build verification.

## Phase 1 — freeze side effects for physical enrollment

Record current `launchctl` state, then stop only the paths that could turn
phone toggles or stale presence into an action. For each label, record whether
this succeeds before changing anything:

```bash
domain="gui/$(id -u)"
launchctl print "$domain/com.openclaw.presence-cabin"
launchctl print "$domain/com.openclaw.presence-receive"
launchctl print "$domain/com.openclaw.vacancy-actions"
launchctl print "$domain/ai.openclaw.nest-activity-reviewer"
```

Boot out only labels that were loaded, using their service targets:

```bash
launchctl bootout "$domain/com.openclaw.presence-cabin"
launchctl bootout "$domain/com.openclaw.presence-receive"
launchctl bootout "$domain/com.openclaw.vacancy-actions"
launchctl bootout "$domain/ai.openclaw.nest-activity-reviewer"
```

Verify `launchctl print` now fails for each label that was booted out. The
tracked/runtime plist names are:

- `com.openclaw.presence-cabin`
- `com.openclaw.presence-receive`
- `com.openclaw.vacancy-actions`
- `ai.openclaw.nest-activity-reviewer`

Restore a service only if it was recorded as loaded on entry, with:

```bash
launchctl bootstrap "$domain" \
  "$HOME/Library/LaunchAgents/<recorded-label>.plist"
launchctl print "$domain/<recorded-label>"
```

Do not stop the Nest Pub/Sub listener or the shadow home-event bus. Preserve
all state, databases, queues, spools, reviewer cursors, and vacancy markers.
Enrollment-window camera triggers will exceed the reviewer's maximum age before
it is restored; verify that no delayed review runs.

Never run `presence-detect.sh cabin` during this phase. That is a state-writing
mode. Use only the enrollment helper.

## Phase 2 — controlled attribution, one phone at a time

For each person independently:

1. Turn both tracked phones' Cabin Wi-Fi off. Wait at least one minute so the
   target row can become inactive or accumulate idle time.
2. Capture that person's baseline.
3. Turn on only the target phone's Cabin Wi-Fi and deliberately load several
   pages or another bounded network task.
4. Run `identify`. It must find exactly one stable fresh/new or idle-reset
   candidate. Zero or multiple candidates is a hard stop, not a prompt to pick.
5. For reconnect cycle 1, turn Wi-Fi off, wait at least one minute, capture
   `disconnect`, reconnect, generate traffic, then capture `reconnect`.
6. Repeat the off/on proof for cycle 2.
7. Generate traffic, run `idle-start`, lock the phone, and take the enforced
   5-, 10-, and 20-minute samples without deliberately waking it between them.
8. Return both phones to Wi-Fi off before beginning the other person's
   baseline.

After both people pass independently, `status` must report two distinct stored
candidates. The first post-staging `both-present` shadow sample supplies the
fresh live-evidence check for both phones; `status` itself does not query
Starlink.

### Private Wi-Fi Address review

Record each phone's Cabin SSID Private Wi-Fi Address mode without putting the
address itself in notes or output. Ordinary reconnect stability is mandatory.

“Forget This Network,” manually rotating the private address, or changing the
mode is disruptive and must be a separately accepted attended action. If the
opaque ID changes under the phone's normal configured behavior, abort. Either
choose a user-approved stable network setting and restart enrollment from a new
baseline or redesign for safely expiring multiple identities. Never add an
unbounded alias list.

## Phase 3 — evaluate the liveness policy

Compare each known-connected 5-, 10-, and 20-minute idle observation with the
current rule:

```text
dhcpLeaseFound == true
dhcpLeaseActive == true
secondsUntilDhcpLeaseExpires > 0
noDataIdleS <= 300
```

The post-staging shadow phase supplies at least three observations on the real
15-minute cadence, including:

- a locked, sleeping phone;
- an arrival/reconnect shortly before a scheduled tick;
- both phones present but mostly idle;
- traffic followed by a visible idle reset.

Acceptance requires prompt arrival detection, consistent field shape, a stable
ID, and a reviewed explanation for every false result. If a known-present
sleeping phone is false at the real cadence, the five-minute rule is not ready.
Update the scanner, helper constant, documentation, and fixtures together, then
restart this phase. Do not tune the number merely to obtain a green check.

Consider separating “fresh enough to prove relocation” from a longer
privacy-preserving “local occupancy veto” if one boolean cannot safely represent
both. A stale local exact binding may suppress monitoring; a false absence can
monitor an occupied home. Those risks are not symmetric.

## Phase 4 — stage and soak without canonical writes

Run `status`, then `seal-candidate`. Confirm:

- the candidate file is a regular, single-link, owner-owned mode-`0600` file;
- `~/.openclaw/presence-devices.json` is still absent;
- no file under `~/.openclaw/presence/` changed;
- no presence home event, vacancy action, camera review, or message occurred.

Restore the legacy production scanner/receiver only if ordinary household
automation must continue during the soak; the candidate remains isolated. The
safest camera posture is to leave the Cabin reviewer stopped or explicitly
shadowed until strict presence passes. If legacy jobs are restored, record that
their output is not evidence for the candidate scanner.

Collect at least eight attended `shadow-sample` records over at least one hour.
At least three gaps between samples must be 14–16 minutes. Cover both-present,
each one-person state, both-away, and a controlled `return-both` immediately
after a matching both-away sample. Compare every safe result with direct ground
truth. A missing selected ID is valid only for a person known to be away; a
present row with incomplete lease/idle evidence is always a mismatch. Any
mismatch or schema drift resets the activation decision.

This evidence-based gate is deliberately narrower than a fixed multi-day soak.
The reconnect and idle phases already stress the deterministic identity and
liveness rules. Use an optional 24–48-hour extension only when results are
borderline or ordinary household behavior exposes a mismatch.

## Phase 5 — activation

Only when `shadow-report` says `ready_for_activation: true`:

1. Stop the Cabin presence scanner, presence receiver, vacancy actions, and
   activity reviewer again. Verify they are actually unloaded.
2. Create a protected rollback bundle containing the deployed legacy scanner,
   relevant plists, current canonical presence files, and hashes/status of the
   existing runtime. Do not delete or rewrite live state.
3. Run the helper's fully confirmed `activate` command. This writes only the
   production binding.
4. Install the strict tracked scanner atomically while the jobs remain stopped.
5. Run:

   ```bash
   ~/.openclaw/workspace/scripts/presence-detect.sh validate-config cabin
   ~/.openclaw/workspace/scripts/presence-detect.sh observe cabin
   ```

   `observe` must contain only the documented safe schema and must not change
   canonical presence files or vacancy markers.
6. If `com.openclaw.presence-cabin` was loaded on entry, restore only that
   service. Keep the receiver, vacancy actions, and reviewer stopped for four
   real scheduled ticks—about one hour. This is the production canary: it
   exercises the deployed path and scheduler without permitting physical
   actions or camera commentary.
7. Inspect every canary tick for state freshness, assignments, occupancy,
   transitions, presence bus status, and vacancy markers. Stop on any
   unexpected relocation or `confirmed_vacant` state.
8. Compare the tracked and installed scanner hashes and require an exact match.
   Then atomically approve only that hash for future routine deployments:

   ```bash
   source_scanner="$HOME/dotfiles/openclaw/workspace/scripts/presence-detect.sh"
   runtime_scanner="$HOME/.openclaw/workspace/scripts/presence-detect.sh"
   source_hash=$(/usr/bin/shasum -a 256 "$source_scanner" | /usr/bin/awk '{print $1}')
   runtime_hash=$(/usr/bin/shasum -a 256 "$runtime_scanner" | /usr/bin/awk '{print $1}')
   test "$source_hash" = "$runtime_hash"
   approval="$HOME/.openclaw/presence-scanner-approved.sha256"
   tmp=$(/usr/bin/mktemp "$HOME/.openclaw/.presence-scanner-approved.XXXXXX")
   /usr/bin/printf '%s\n' "$source_hash" > "$tmp"
   /bin/chmod 600 "$tmp"
   /bin/mv -f "$tmp" "$approval"
   ```

   The deployment guard rejects symlinks, extra lines, insecure mode, hard
   links, wrong length, and any hash other than the current candidate.
9. Restore the receiver. After another correct evaluation, restore vacancy
   actions.
10. Restore the Nest activity reviewer last, only after presence remains correct
   and enrollment-window camera triggers are too old to review. Verify no
   delayed message or image analysis occurs.
11. Run `cleanup` to remove the redundant sensitive session and retain the
    production binding, exact-source approval, and safe report.

There is no OpenClaw gateway restart in this procedure.

## Success criteria

- Two distinct opaque IDs are uniquely attributed one phone at a time.
- Each ID survives two ordinary off/on reconnect cycles with an observed off
  state and three fresh same-ID samples after reconnect.
- Private-address behavior is reviewed and stable under ordinary use.
- Lease and idle fields are structurally complete for both selected phones.
- The idle policy works at the real 15-minute cadence and errs safely for camera
  privacy.
- No raw identity or provider value appears in output, logs, safe reports,
  command history, plans, tests, or Git.
- Candidate bindings remain outside the production path until the final gate.
- At least eight samples over one hour, including three 14–16-minute intervals,
  cover every required occupancy scenario and a verified away-to-return
  transition with zero mismatch.
- Four scheduled strict production canary ticks create the expected presence state with
  no spurious relocation, vacancy action, home-event anomaly, camera review, or
  message.
- Routine deployment accepts only the exact scanner hash that passed the
  downstream-disabled canary.
- The prior scanner and state remain recoverable.

## Hard-stop conditions

Stop and preserve evidence if any of the following occurs:

- Starlink is unavailable or its response schema changes.
- An ID is missing, malformed, duplicated, or changes on ordinary reconnect.
- Identification produces zero or multiple candidates.
- Another iPhone can satisfy the selected identity.
- Required lease/idle fields are incomplete for a selected phone.
- The current idle threshold fails known-present or real-cadence testing without
  a reviewed replacement.
- Parent, session, staging, or production permissions/ownership/link counts are
  wrong.
- Any raw name, ID, MAC, IP, row, or config body reaches output or a log.
- The helper changes canonical presence, a vacancy marker, a bus queue, camera
  state, or messaging state.
- Ground truth is ambiguous.
- The previous runtime cannot be restored confidently.

## Rollback

1. Stop the strict Cabin scanner, receiver, vacancy actions, and reviewer.
2. Move—not print or discard—the new production config and scanner approval
   into the protected rollback bundle.
3. Restore the exact legacy scanner and plist from the pre-activation backup.
4. Preserve the enrollment session and safe report for diagnosis; do not delete
   canonical presence state, databases, spools, cursors, or vacancy markers.
5. Run the legacy scanner only after verifying host and rollback hashes, then
   deliberately re-evaluate canonical presence.
6. Restore receiver, vacancy automation, and reviewer only after the state is
   trustworthy and no delayed camera work remains.

Rollback does not require a gateway restart.
