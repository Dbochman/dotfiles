# Cabin Starlink Presence Enrollment and Verification

## Status

ACTIVATED WITH AN EXPLICIT OPERATOR CANARY WAIVER — production schema v2
contains exact bindings for both residents on the primary controller and
Kitchen mesh node. Two corrected real-cadence ticks passed with zero mismatch;
the operator explicitly waived the remaining two ticks after also reviewing
the 12/12 live observation run and full test results. The exact scanner hash is
approved, enrollment staging is cleaned, and all protected jobs are restored.
A natural departure/return transition remains a non-blocking follow-up.

## Outcome

Safely replace the Cabin's permissive Starlink display-name matching with exact,
source-local `captiveClientId` bindings for both residents on every monitored
Starlink attachment domain. Prove that each identifier belongs to the intended
phone, remains stable across ordinary reconnects, exposes usable source-specific
liveness evidence, and behaves safely at the real 15-minute scan cadence before
the production scanner, vacancy automation, or camera reviewer can use it.

The enrollment must never disclose a raw provider row or turn an identification
session into a presence transition, vacancy action, camera review, home-event
publication, or outbound message.

## July 23 mesh migration update

The exact controller-only enrollment completed successfully and schema v1 was
activated. The first production canary tick was correct. After an ordinary
departure and return, however, Dylan's phone associated through the Kitchen
Starlink Router Mini. Its controller-local row disappeared even though the
phone was connected and producing traffic, so the canary was stopped at one of
four ticks before any downstream service was restored or scanner hash approved.

Read-only topology inspection established:

- the Kitchen device is one directly attached `REPEATER`, already paired to the
  primary controller;
- the primary controller can proxy `wifiGetClients` to it with `targetId`;
- a phone has a distinct, stable node-local `captiveClientId` in that targeted
  response;
- `active` is not authoritative: it remained false across ten connected,
  traffic-producing samples;
- the exact row, `CLIENT` role, finite association age and signal, and valid
  receive/transmit statistics were stable across those samples; and
- with Settings-level Wi-Fi off, the exact phone row was absent across four
  consecutive successful targeted queries and remained absent in a later
  check.

Schema v2 therefore represents one controller source plus one or more explicit
mesh sources. Presence is the union of exact per-source bindings. Controller
rows retain the DHCP/lease/idle predicate. A selected mesh row is present when
it has the exact node-local binding, `CLIENT` role, finite nonnegative
association age and signal, and valid receive/transmit statistics. The
provider's `active` field is diagnostic-only. An exact row is absent only after
that source returns a valid targeted response. Any configured source failure
or duplicate selected row invalidates the whole observation. Incomplete
liveness on one successful source is per-resident unknown: another source's
strict positive wins the union, while no positive plus unknown fails closed.

The current migration adds a separate operator-only helper so the already
activated v1 binding and the completed original enrollment session remain
untouched until an atomic v1-to-v2 promotion. Its default attended flow captures
an off baseline first as session-keyed fingerprints, then credits the subsequent
stable exact-IP identification as the same off-to-on proof. This deliberately
reuses the physical transition instead of requiring a redundant second toggle.

### July 23 activation record

Schema v2 was activated under the verified mesh-aware runtime. The corrected
source-union behavior passed 12 of 12 consecutive live observations, 148
enrollment/deployment tests, all four presence shell suites, and two clean
scheduled production ticks. Both ticks reported Dylan and Julia at Cabin,
Cabin occupied, no transition, unchanged vacancy markers, matching
source/runtime bytes, and a healthy zero-backlog event bus.

The operator then explicitly waived scheduled ticks three and four and approved
the exact installed scanner hash. That hash was written atomically with mode
`0600`. The helper revalidated the exact v1 rollback backup on an idempotent
activation check, removed the sensitive mesh session and staged config, and
retained the identifier-free report and exact v1 backup. The scanner, receiver,
vacancy actions, and reviewer were restored in that order.

Seven enrollment-window Kitchen triggers expired without capture or review.
A subsequent live Nest event was suppressed by the corrected occupied presence
state, with no image, model review, or message attempt. Restoring vacancy
actions released Julia's temporary Eight Sleep containment and moved her
verified home marker to Cabin. Dylan's initial move-to-Cabin attempt failed
under the old current-set-only wrapper and correctly left its marker unchanged.
The old verifier eventually passed after its secondary device telemetry caught
up, allowing ordinary reconciliation to advance Dylan's marker at 17:04. The
follow-up repair added an explicit user/device-side assignment and authoritative
readback so future moves do not depend on that delayed telemetry. No gateway
restart occurred.

## Eight Sleep containment and routing repair (2026-07-18–23)

Until Julia's exact Cabin binding is activated, `vacancy-actions.sh` pins her
Eight Sleep home to Crosstown even when the correlated sticky state says Cabin.
The deployed permissive scanner falsely relocated her from Crosstown to the
Cabin on five consecutive days, July 13–17. Each false relocation repeatedly
called the Eight Sleep Cabin `home` operation. Eight Sleep partially changed
the user-scoped routing but never completed the Cabin side assignment. The
command correctly reported failure, but its durable marker remained Crosstown,
so each new state write retried the bad Cabin move and the later Crosstown
correction did not force a repair.

The repaired wrapper treats household `current-set` selection and the
user-scoped `current-device` side assignment as distinct operations. A `home`
move collision-checks the target side, performs both operations when needed,
clears away mode, and succeeds only after the target set, exact target
device/side, and `away-mode = false` all read back correctly. Eight Sleep's
device-level `awaySides` field can lag and is retained as diagnostic telemetry,
not authoritative move proof.

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
- Mesh migration helper: `openclaw/bin/presence-cabin-mesh-enroll`
- Strict scanner: `openclaw/workspace/scripts/presence-detect.sh`
- Helper tests: `openclaw/tests/test_presence_cabin_enroll.py`
- Mesh helper tests: `openclaw/tests/test_presence_cabin_mesh_enroll.py`
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

- Protected mesh migration runtime:

  ```text
  ~/.openclaw/presence-mesh-enrollment/  0700
  ├── session.lock                       0600
  ├── session.json                       0600; sensitive until cleanup
  ├── candidate-config-v2.json           0600; staging only
  ├── safe-report.json                   0600; identifier-free
  └── production-v1-backup.json          0600; retained for exact rollback
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
   the eventual config. Each reconnect off sample also retains non-target
   clients only as session-keyed fingerprints with private liveness evidence;
   it never stores their raw IDs.
5. The candidate config stays outside the production path throughout the soak.
   Creating `~/.openclaw/presence-devices.json` alone does not authorize a
   routine pull to install the strict scanner: deployment also requires the
   site-local approval file to contain the exact canaried source hash. Do not
   create that approval until the downstream-disabled production canary passes.
6. All enrollment mutations require `--attended` and a real TTY. Production
   promotion additionally requires four explicit confirmations and refuses to
   overwrite any existing config, link, or unsafe path.
7. Unknown, malformed, duplicated, rotating, or ambiguous identity evidence
   fails closed. There is no name, hostname, recurring/production IP,
   generic-iPhone, or multiple-ID fallback. The attended one-time IP join
   described below is transient attribution evidence, never production
   identity.
8. The current five-minute idle rule is measured, not defended. If it fails
   real sleeping-phone or 15-minute-cadence tests, stop and revise the policy
   and tests before activation.
9. A false absence is the higher privacy risk for camera monitoring. No result
   is accepted merely because it produces fewer false occupied states.
10. Preserve databases, spools, camera cursors, canonical presence state, and
    vacancy markers throughout enrollment and rollback.

## Helper contract

Every command emits one bounded JSON object. Read-only commands are
`preflight`, `status`, `observe-selected`, and `shadow-report`.
`observe-selected` is an enrollment-troubleshooting aid that returns only the
selected candidate's safe seen/lease/idle classification plus a boolean
indicating whether the full liveness tuple is absent and bounded categorical
presence/type buckets for those fields and Starlink's generic `active` flag; it
never returns raw provider values or changes session or canonical state.
Commands that change the protected enrollment session require `--attended`
before the command name.

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
idle-reset candidate remains stable across all three.

If real household traffic prevents transition-only isolation, use the attended
exact-address fallback after the off baseline:

```bash
~/.openclaw/bin/presence-cabin-enroll --attended \
  identify-exact-address Julia
```

The address is entered twice through fixed, hidden terminal prompts. It is
never accepted in an argument or environment variable and never written to
stdout, stderr, the session, a log, or Git. The helper uses it only in process
memory to require one matching Starlink `macAddress` row, the same valid
`captiveClientId`, a phone-off baseline that did not classify that row present,
and fresh complete liveness in all three online samples; it then discards the
address and persists only the existing private opaque ID. Missing, malformed,
duplicated, stale, incomplete, changing, or already-present-at-baseline matches
fail closed.
This is attribution evidence only and does not bypass either reconnect cycle,
the idle profile, shadow verification, or the production canary.

The attended Cabin run on July 23 found that Starlink returned partially
masked `macAddress` values for 17 of 19 client rows, so Julia's valid address
could not match. When the provider masks the target row, use the separate
exact-IP join:

```bash
~/.openclaw/bin/presence-cabin-enroll --attended \
  identify-exact-ip Julia
```

The phone's current Cabin IPv4 address is entered twice as hidden transient
text, immediately normalized to four bytes, and never persisted or emitted. It
must be canonical and inside the Cabin subnet. At least three independent
Starlink samples—and at most ten over a bounded observation window—must map it
to exactly one row and the same valid `captiveClientId`. At least one sample
must pass the production presence rule; any remaining sample may omit the
entire provider lease/idle tuple, or omit only the idle counter while retaining
an explicitly found, active DHCP lease with positive time remaining. No sample
may be absent, stale, invalid, have any other partial shape, or map the IP to a
different identity. The phone-off baseline must be less than 30 minutes old and
must not have classified that opaque ID present in any of its three samples.
The helper then discards the IP and persists only the opaque ID. DHCP
reassignment can therefore never become production identity evidence. This is
materially narrower than the old Crosstown production IP fallback and retains
every later reconnect, idle, shadow, canary, and activation gate.

Each reconnect cycle
requires an attended transport-off state: the selected row is missing, has a
complete invalid lease, or remains present with the entire lease/idle tuple
absent after the phone's Settings-level Wi-Fi switch is off. The
incomplete-tuple case is enrollment-only transition evidence; it is never
production absence. Every persisted reconnect, idle, shadow, and production
present observation still requires complete liveness. At least five seconds
must separate the off and on observations. The off sample records a
session-keyed liveness map for every non-target client. A peer that was already
valid and fresh while the target phone was off may remain fresh without
invalidating the reconnect. For transition-only `new` or `idle_reset`
attribution, a peer that was absent, stale, incomplete, or invalid while the
target phone was off and then becomes fresh—or whose idle counter materially
resets only after the target reconnects—is a hard stop, covering dual-row
private-address rotation. Independently attributed exact-address or exact-IP
identities do not apply that peer guard: their selected opaque ID must still
disappear while off, remain the same across at least three and at most fifteen
reconnect samples, and pass the production presence rule in at least one.
Other samples may use only the same narrow whole-tuple or
valid-lease/missing-idle exceptions allowed during exact identification. The
persisted reconnect proof is always the complete production-present
observation, while unrelated household client activity is ignored.
Transition-only identities retain three fresh samples and the tighter
60-second freshness rule. An ambiguity-bearing
transition-only reconnect attempt consumes that off context and requires a new
attended Wi-Fi-off sample before any retry; a selected row that merely has not
met its applicable reconnect rule remains retryable. An idle counter reset
while the selected lease remains valid is not disconnect proof.

The idle checkpoints are measured from `idle-start`; the helper requires them
in 5-, 10-, then 20-minute order and never accepts a checkpoint before its
milestone. A later observation remains valid evidence for an earlier milestone,
so operator timing cannot invalidate an otherwise stronger observation. Each
recorded checkpoint must still be a distinct, later Starlink query. A selected
row whose entire lease/idle tuple is temporarily absent is unknown rather than
negative, so that checkpoint remains retryable instead of consuming the
milestone. Checkpoints record safe buckets and whether the current five-minute
rule would classify the known-connected phone as present. Every recorded
checkpoint must classify the phone as present before staging is allowed. The
helper does not silently change the threshold.

For an independently attributed exact identity, `idle-start` reuses the second
cycle's durable reconnect observation when it is no more than five minutes old
and was fresh within 60 seconds at capture. This starts the idle clock at the
attended `idle-start` invocation without demanding a redundant provider query;
the phone must then be locked and left untouched as usual. Older or
transition-derived reconnect evidence falls back to a new live snapshot.

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
  --scenario both-present

~/.openclaw/bin/presence-cabin-enroll shadow-report
```

`seal-candidate` writes only the private staging config. Shadow samples query
Starlink directly, compare the current rule with operator-supplied ground truth,
and save only booleans, lease state, idle buckets, counts, and timestamps.

Every activation path requires:

- both distinct identities fully enrolled;
- two proven reconnect cycles per phone;
- complete 5-, 10-, and 20-minute idle profiles per phone;
- zero saved mismatches or incomplete required evidence.

The default compact path additionally requires exact attribution for both
phones, protected enrollment evidence for both-away and Julia-only, and three
consecutive matching joint samples: both-present, Dylan-only, then both-present
after Julia returns.

The legacy alternative requires:

- at least eight shadow samples;
- at least one hour between the first and last samples;
- at least three sample intervals between 14 and 16 minutes, exercising the
  real 15-minute cadence rather than only rapid manual queries or multi-hour
  gaps;
- all five ground-truth scenarios, including a return after both-away.

That legacy one-hour gate remains available for unattended soak testing. After
both phones instead complete the attended exact-identity enrollment above, the
helper also accepts a compact joint-classifier sequence:

1. both phones present;
2. Julia disconnected while Dylan remains present;
3. Julia returned and both phones present again.

All three samples must be consecutive matches. Every present selected row must
have complete liveness; an expected-away row may be missing or complete
negative. An incomplete provider snapshot is unknown and retryable rather than
being stored as a mismatch. The helper algorithmically verifies that Julia's
three-sample baseline classified neither selected fingerprint present and that
a Dylan disconnect captured Dylan off while Julia was strictly present. This
compact alternative also relies on the required per-phone reconnect cycles and
5/10/20-minute idle profiles; it does not repeat those same tests for another
hour. A natural whole-house departure and return remains a non-blocking
production canary.

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
python3 -m unittest openclaw/tests/test_presence_cabin_mesh_enroll.py
python3 -m py_compile \
  openclaw/bin/presence-cabin-enroll \
  openclaw/bin/presence-cabin-mesh-enroll
bash openclaw/tests/test-presence-cabin-scan.sh
bash openclaw/tests/test-presence-observe.sh
bash openclaw/tests/test-presence-detect.sh
bash openclaw/tests/test-presence-receive.sh
bash openclaw/tests/test-presence-home-events.sh
bash openclaw/tests/test-vacancy-actions.sh
python3 -m unittest openclaw/tests/test_deployment_contracts.py
```

The helper's protected session schema is versioned. Before installing a newer
helper, preserve any older incomplete session inside the owner-only rollback
bundle and use the still-installed old helper to run its attended
`abort --confirm-delete-sensitive-session`. Only after the old session is
safely closed may the new helper be installed and a clean session started.
Never install a helper that cannot validate the active session and never
hand-edit or synthesize reconnect context.

The exact-IP fallback and any-sample off-baseline aggregation are a schema-v4
boundary. Any v1-v3 session must be preserved and closed with its still-
installed helper before installing v4; never downgrade while a v4 session
exists.

After satisfying that boundary, install the verified helper atomically and
compare source and installed hashes:

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

### Mesh source enrollment after schema-v1 activation

Keep the four protected jobs unloaded. Install the verified mesh helper
atomically, compare its source/runtime hashes, and then use one off-to-on cycle
per resident:

```bash
~/.openclaw/bin/presence-cabin-mesh-enroll --attended init

# While Dylan is disconnected:
~/.openclaw/bin/presence-cabin-mesh-enroll --attended baseline Dylan
# Reconnect, generate traffic, then enter the current IPv4 twice at hidden prompts:
~/.openclaw/bin/presence-cabin-mesh-enroll --attended identify Dylan

# Repeat the same off-baseline / reconnect / identify sequence for Julia.
~/.openclaw/bin/presence-cabin-mesh-enroll --attended baseline Julia
~/.openclaw/bin/presence-cabin-mesh-enroll --attended identify Julia

~/.openclaw/bin/presence-cabin-mesh-enroll --attended stage
~/.openclaw/bin/presence-cabin-mesh-enroll report
```

The IPv4 address is transient attribution evidence accepted only through two
fixed hidden prompts; any canonical RFC1918 subnet is allowed because the mesh
node may route a different private subnet. It is never stored or emitted. A
fresh baseline is valid for at most 30 minutes and retains only
person/session-keyed fingerprints. Identification must prove one stable exact
node-local ID with advancing association evidence. If no baseline is available,
the helper retains the explicit `identify` → `disconnect` → `reconnect`
fallback, with at least three consecutive exact-ID absences required for the
off proof.

When a targeted mesh row's exact `captiveClientId` is byte-for-byte identical
to that resident's already-active, fully enrolled schema-v1 controller binding,
the helper may instead use the explicit attended continuity path:

```bash
~/.openclaw/bin/presence-cabin-mesh-enroll --attended \
  credit-controller-identity Julia \
  --confirm-existing-controller-identity
```

This path never selects by name, device type, or IP. It starts from the trusted
existing controller identity, requires that exact row on the configured mesh
target across a final consecutive strict window with advancing association
evidence, and rejects any cross-person identity collision. It credits the
prior controller enrollment's identity/reconnect durability and the already
validated same-node row-removal semantics instead of interrupting the resident
for a redundant toggle. It is allowed only when the opaque values are exactly
equal. A Julia-specific stale row after departure would conservatively retain
occupancy rather than create a false vacancy.

`stage` writes only the protected v2 candidate. It must leave the active v1
config and the original enrollment session byte-for-byte unchanged. Before
activation, an attended `abort --confirm-abandon-mesh-enrollment` removes only
the new mesh session, stage, and report.

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

The downstream-disabled production canary supplies observations on the real
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

For the default compact path, collect three consecutive attended
`shadow-sample` records: both-present, Dylan-only after Julia turns Wi-Fi off,
then both-present after Julia returns. The helper also verifies exact bindings
and credits the protected enrollment evidence for both-away and Julia-only.
Compare every safe result with direct ground truth. A missing selected ID is
valid only for a person known to be away; incomplete selected-row evidence is
unknown and retryable without being recorded.

The legacy alternative remains at least eight attended samples over one hour,
including at least three 14–16-minute gaps, all one-person/both-away states, and
a controlled `return-both` immediately after matching both-away. Any recorded
mismatch or schema drift blocks both activation paths.

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
9. Stop the canary scanner and verify that all four protected jobs are
   unloaded. Run `cleanup` now, while its mutation-job guard can prove the
   session is no longer in use. Cleanup removes the redundant sensitive
   session and retains the production binding, exact-source approval,
   identifier-free report, and exact rollback backup.
10. Restore the Cabin scanner and verify another correct evaluation. Restore
   the receiver, verify canonical state again, and only then restore vacancy
   actions.
11. Restore the Nest activity reviewer last, only after presence remains correct
   and enrollment-window camera triggers are too old to review. Verify no
   delayed message or image analysis occurs.

There is no OpenClaw gateway restart in this procedure.

### Schema-v1 to schema-v2 mesh promotion

For the already-active controller-only binding, the following ordering replaces
steps 3–5 above:

1. Keep all four protected jobs unloaded.
2. Point the exact candidate scanner at the protected staged v2 config and run
   `validate-config cabin` plus the read-only `observe cabin`. Do not run the
   state-writing `cabin` mode. The candidate must contain exactly one
   `PRESENCE_SCANNER_CONFIG_CONTRACT="cabin-sources-v2"` capability marker.
   This is intentionally separate from the unchanged
   `strict-site-bindings-v1` deployment-protocol marker.
3. Install those exact candidate scanner bytes atomically while production is
   still schema v1. The scanner must validate both v1 and v2, and source/runtime
   hashes must match.
4. Run the mesh helper's four-confirmation `activate` command:

   ```bash
   ~/.openclaw/bin/presence-cabin-mesh-enroll --attended activate \
     --confirm-safe-report-reviewed \
     --confirm-mutation-jobs-stopped \
     --confirm-v2-consumer-ready \
     --confirm-exact-rollback-ready
   ```

   The confirmation is not trusted by itself: the helper rereads the installed
   scanner, requires the v2 capability marker and safe executable metadata,
   runs that runtime's `validate-config cabin` against the protected staged
   file, and verifies the scanner did not change during the check. It then
   retains the exact v1 bytes as a protected rollback file and atomically
   replaces only the production binding.
5. Validate and observe the production v2 config, then begin a new four-tick
   downstream-disabled canary from zero. The earlier controller-only tick is
   not credited.
6. Freeze scanner bytes throughout the canary. Only after all four ticks pass
   may the Mini approve that exact hash and restore receiver, vacancy actions,
   and reviewer in the documented order.
7. The shared scanner's changed bytes also invalidate the independent
   Crosstown approval. Routine sync must preserve the prior MacBook Pro runtime
   until that same source hash passes its own Crosstown canary.
8. After the post-canary state and rollback file are verified, stop the canary
   scanner, verify all four protected jobs are unloaded, and run:

   ```bash
   ~/.openclaw/bin/presence-cabin-mesh-enroll --attended cleanup \
     --confirm-post-canary-success
   ```

   Cleanup removes the redundant mesh session and staged config, retains the
   identifier-free report and exact v1 backup, and does not touch the original
   enrollment session. Then continue with the general restoration order:
   scanner, receiver, vacancy actions, and reviewer.

If any v2 canary tick fails, keep all four protected jobs unloaded and run:

```bash
~/.openclaw/bin/presence-cabin-mesh-enroll --attended rollback \
  --confirm-post-activation-rollback
```

Rollback accepts only the exact session-derived v2 bytes or an already-restored
exact v1 file, validates the protected backup against the original v1 hash and
bindings, atomically restores and rereads those exact bytes, and preserves the
session, staged v2 config, safe report, and backup for diagnosis. It is
idempotent after an interrupted successful restore. An unknown current config,
backup mismatch, or uncertain restore state is a hard stop.

## Success criteria

- The controller and every monitored mesh source contain exact per-person
  bindings, with distinct identities within each source and no cross-person
  identity collision.
- Each controller binding survives two ordinary off/on reconnect cycles with
  an observed off state, stable same-ID samples after reconnect, and a
  persisted complete production-present proof.
- Each mesh binding has either a credited attended off-to-on proof or the
  explicit trusted-controller continuity proof: byte-identical identity, a
  final consecutive strict mesh window, advancing association evidence, and
  collision rejection.
- Private-address behavior is reviewed and stable under ordinary use.
- Both controller bindings produce complete lease/idle evidence. Mesh bindings
  instead satisfy the documented role, association, signal, and RX/TX
  predicates; the provider's `active` field remains diagnostic-only.
- The idle policy works at the real 15-minute cadence and errs safely for camera
  privacy.
- No raw identity or provider value appears in output, logs, safe reports,
  command history, plans, tests, or Git.
- Candidate bindings remain outside the production path until the final gate.
- Either the compact credited joint sequence or the legacy eight-sample,
  one-hour path passes with zero recorded mismatch.
- Four scheduled strict production canary ticks normally create the expected
  presence state with no spurious relocation, vacancy action, home-event
  anomaly, camera review, or message. The July 23 rollout records an explicit
  operator waiver after two clean ticks plus 12/12 live observations and the
  full test suite; this does not relax the default gate for future scanner
  changes.
- Routine deployment accepts only the exact scanner hash that passed the
  downstream-disabled canary.
- The prior scanner and state remain recoverable.

## Hard-stop conditions

Stop and preserve evidence if any of the following occurs:

- Starlink is unavailable or its response schema changes.
- An ID is missing, malformed, duplicated, or changes on ordinary reconnect.
- Identification produces zero or multiple candidates.
- Another iPhone can satisfy the selected identity.
- Required lease/idle fields are incomplete for a selected phone outside the
  narrowly attended transport-off observation or exact-IP identification
  exception described above.
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
