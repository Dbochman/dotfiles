# Node24 Phase A: draft implementation and cutover preparation

**Status:** PHASE A ACCEPTED 2026-10-11; Phase B remains unactivated.
Branch: `codex/openclaw-node24-phase-a`, based on `f0367a0`.
Worktree: `/Users/dbochman/repos/dotfiles-openclaw-node24-phase-a`.

The [reviewed upgrade plan](node24-openclaw-stable-upgrade.md) controls approvals,
backup coverage, isolation and rollback. This document records the smaller first
implementation slice. It is not permission to run the cutover. The live checkout
remains on `main`; its existing uncommitted plan amendments are preserved. Do not
merge this branch or enable auto-merge before the approved maintenance window:
the live app is symlinked into that checkout and `main` feeds automatic deployment.

## Accepted Phase A

The later attended cutover accepted official Node24.21.0 with the unchanged
OpenClaw2026.8.35 package. Source and deployed wrapper/pull checks converged at
`c8d1507`; the deployment LaunchAgent returned to its recorded loaded state.
The fresh stopped checkpoint restored with all 126,811 manifest entries matching
and 67 SQLite integrity checks passing. Both copied runtimes passed the old
package's schema admission checks. The archive remains protected and unchanged.

Live executable mapping, package/wrapper hashes, health, active required skills,
iMessage bridge/FDA, memory embedding probe and usage dashboard checks passed.
A disabled, future-scheduled no-delivery fixture completed and was removed;
a separate read-only agent fixture explicitly confirmed the native Codex/Astra
harness without fallback. The 16 production job definitions and effective config
were preserved; no production job or message was manually rerun. An owner-only
activation receipt was published through the tested verifier after acceptance.

Acceptance began at 17:39 Eastern with a 30-minute observation window. Phase B
preparation may proceed during that window, but package activation remains gated
on the separate migration rehearsal, fresh checkpoint and compatibility checks.
Node22 and the enterprise OAuth authority remain unchanged for rollback and
unrelated consumers. Protected evidence is under
`~/Backups/openclaw/phase-a-20261011T212300Z`; no private evidence belongs in Git.

## Earlier attempt: stopped before runtime mutation

The 2026-10-11 attended attempt drained successfully with no active work, unloaded
the gateway, and verified both Codex provider children exited. The final checks
on manifest-matching offline database copies found no pending deliveries, running
cron/tasks, unfinished subagents, restart sentinel, pending inputs or recovery
sessions. Read-only opens of the live closed WAL-mode database were unavailable;
the copy checks avoided creating live sidecars or ignoring WAL.

The archive was created, but restore acceptance failed: non-root bsdtar restored
49,300 entries' group as `staff` instead of their recorded `admin` group. The check
was **not** loosened. A preceding budget-monitor scan also encountered a transient
extraction filename; the private monitor now tolerates files disappearing during
that scan. Failed artifacts remain protected and are not accepted checkpoints.

The cutover was aborted before changing runtime, package, wrapper, generated
plist, deployed pull script, activation receipt or live Git checkout. The original
Node22.23.1 / OpenClaw2026.8.35 gateway was restored (PID15208 at verification),
its read-only activation/health/required-skill checks passed, iMessage reported
connected, and the original deployment schedule was restored. The scheduler
still reported 16 jobs, 15 enabled and none running at the check. Normal recovery
was allowed; no manual job or message was run. Main remains `f0367a0`, with its
pre-existing local plan edits preserved; candidate code is published only on the
review branch at `9d97c25`.

The offline fix passed using explicit `tar --same-owner` extraction.
All archived entries are owned by the current UID and use groups20/80, both of
which this operator belongs to; no sudo or ownership weakening is needed.
`test_gateway_recovery_archive.py` permanently covers alternate-group ownership,
modes, absolute/relative links, committed WAL restoration and unchanged archive
hash. The historical archive's 126,811 restored entries matched the original
manifest, all 67 SQLite integrity checks passed, and its hash remained unchanged.
The archive is 4,093,090,816 bytes; protected artifacts remain within the approved
64 GiB budget. Nothing was deleted automatically.

The copied OpenClaw2026.8.35 package then passed its own `database preflight` under
both copied Node22.23.1 and Node24.21.0: schema15, `status: exact`,
`requiresWrite: false`, zero issues. The disposable shared-database snapshot was
consolidated through SQLite backup and `VACUUM INTO`, integrity-checked, and
confirmed free of WAL/SHM/journal sidecars. The initial backup copies left a stale
SHM file and were rejected rather than used for preflight. No live database or
archive was rewritten. The isolated CLI used a clean HOME, denied network access,
and a tested denial of production config reads; `OPENSSL_CONF=/dev/null` avoided
Homebrew Node22 trying to read host OpenSSL settings. This is offline schema
compatibility evidence, not a production TLS, messaging or gateway canary.

Private receipts are in `~/Backups/openclaw/phase-a-20261011T203555Z/`:
`offline-validation.json`, `runtime-restore-validation.json` and
`recovery-status.json`. This successful historical restore drill still requires
a **fresh stopped checkpoint** before another cutover; never rewind to this older
state or credentials. No further production stop was made during the offline drill.

## Prepared source changes

- The FDA app wrapper selects the absolute Node24 executable and Node24-first
  runtime PATH; a missing, nonexecutable or checksum-mismatched runtime stops
  before secrets or logs. The pin hashes the extracted executable, not its archive.
- The pull script stops before Git/deployment work if Node24 is unavailable or
  its extracted executable does not match the same readonly checksum pin,
  emitting a timestamped failure to its persistent log as its only guard side effect.
  Its final gateway PATH agrees with the wrapper/recovery plist, and its skill
  RPC invokes absolute Node24 plus the existing package entry point, not an
  ambient `node` selected by the OpenClaw shim.
- The recovery plist's PATH follows the same contract. It is still **not** a
  replacement for the live generated plist/service-env launch chain.
- `bin/gateway_activation.py` is the shared, narrow verifier used by the pull
  script and the future attended cutover. It checks an already-started process;
  it never starts, stops, restarts or deploys anything. Default operation is
  read-only; explicit `--record` publishes the activation hash only after all
  checks pass. It reuses the authoritative live-skill parser in `lib/deployment.sh`.
- Synthetic tests cover these contracts, no Node22 fallback, rejected receipts,
  and health/live-skill checks before atomic receipt publication. They execute
  temporary fixtures and extracted deployment blocks, never the real gateway
  or the complete pull script. The desktop/remote-splat helper fixtures are
  repaired without changing their production installers or helpers.
- No package install, global PATH edit, native-addon rebuild, model/auth change,
  scheduler mutation, service restart or live receipt write is included.

The approved official Node24 runtime is staged on the Mini at
`/Users/dbochman/.local/share/openclaw/runtimes/node-v24.21.0-darwin-arm64`.
This replaces the proposed Homebrew transaction, whose preview would upgrade
dependencies also linked by Node22. No Homebrew packages were installed.
The executable-exists guards are not
version, SQLite, ABI, package-engine or actual-process-mapping validation; those
remain mandatory preflight/canary checks. Other CLI consumers intentionally
remain unchanged in this Phase A slice while OpenClaw stays at 2026.8.35.

## Bounded shared-CLI inventory

This is a tracked-source inventory, **not a completed deployed-host audit**.
Search scope: `openclaw/bin`, `openclaw/lib`, `openclaw/launchagents`,
`openclaw/sync-cron-jobs.sh` and top-level `bin`. Match actual invocations, not
an OpenClaw-named file or a comment. Before B, inspect named deployed counterparts,
configured cron/helper entry points and any indirect callers; record hashes,
effective child runtime and targeted reload needs without exposing payloads.

| Invoker / owner | Source evidence | Disposition |
|---|---|---|
| Gateway app | absolute `node …/dist/index.js gateway` | Prepared for Node24 in A; actual FDA/process checks pending |
| `dotfiles-pull.command` / `gateway_activation.py` | shared `openclaw_gateway_read_active_skills`; final `GATEWAY_RUNTIME_PATH` | Prepared in A; deploy/hash-check `~/.openclaw/bin/dotfiles-pull.command`, not just tracked source |
| `sync-cron-jobs.sh` | prepends `OPENCLAW_NODE22_BIN`; invokes `cron.update`, Doctor and `cron list` | Before B: change child runtime and test actual readers; never run reconciliation as a test |
| `bin/home-event-camera.py` | `CameraCommands._environment` pins22; `analyze` calls shared CLI | Before B: synthetic inference fixtures and named service reload if needed; no camera action |
| `bin/home-event-delivery.py` | `send_message` pins22 before shared CLI `message send` | Before B: mocked sending/receipt tests; no test delivery implied |
| `bin/usage-dashboard.py` | `fetch_session_usage` sets Node22 PATH then `sessions.usage` RPC | Before B: verify the actual child runtime and reader contract; dashboard-only reload if required |
| `bin/nest-activity-reviewer.py` and its wrapper | reviewer `RUNTIME_PATH` and wrapper pin22; CLI image inference | Before B: coordinate both layers; fixture-only inference, no household actions |
| `bin/cabin-entry-verifier.py` and its wrapper | shared CLI image inference inherits wrapper's Node22 PATH | Before B: synthetic inference and wrapper checks; no live camera/notification |
| `bin/oauth-refresh.sh` | invokes Claude CLI, not OpenClaw | Excluded; retain Node22 and the existing enterprise credential authority |
| `bin/imsg-bridge-ensure` | native `imsg`, `curl` and launchctl, not OpenClaw CLI | No runtime edit; it does not bootstrap an unloaded gateway; retain supervision |

The first two rows are this PR's runtime changes. Remaining shared-CLI rows are
a separate reviewed compatibility cohort, deployed and verified before replacing
the global package in B. A successful gateway canary does not prove them ready.

## Verification completed during preparation

Using `/opt/homebrew/bin/python3` (3.14.3):

```sh
/opt/homebrew/bin/python3 -m unittest discover -s openclaw/tests -p test_gateway_runtime_contract.py
/opt/homebrew/bin/python3 -m unittest discover -s openclaw/tests -p test_gateway_activation.py
/opt/homebrew/bin/python3 -m unittest discover -s openclaw/tests -p test_deployment_contracts.py
/opt/homebrew/bin/python3 -m unittest discover -s openclaw/tests -p test_oauth_refresh.py
/opt/homebrew/bin/python3 -m unittest discover -s openclaw/tests -p test_native_codex_config.py
/opt/homebrew/bin/python3 -m unittest discover -s openclaw/tests -p test_cron_sqlite_compat.py
/bin/bash openclaw/tests/test-imsg-bridge-ensure.sh
/bin/bash openclaw/tests/test-sync-cron-jobs.sh
```

The Python/shell suites are fixture-only, distinct from the runtime probes below.
The original runtime tests failed against the old contract, then
passed after the source edits. The four pre-existing deployment-suite failures
were caused by omitted `desktop-compute` and `remote-splat` helper fixtures;
those are now repaired, with a regression that compares fixture coverage to
the installer's source list. No production desktop helpers changed.

Verification totals and exact reviewed commit are recorded in the draft PR.
Required checks also include shell syntax, recovery-plist lint, changed Python
compilation, `git diff --check` and `./sync.sh validate`. The verifier's macOS
process/launchctl/lsof contracts also passed the approved read-only Node22
baseline check; Node24 activation remains untested.

The follow-up passes 86 focused Python tests (27 activation, 11 runtime, 26
deployment, 10 OAuth, 2 native Codex, 10 cron SQLite); all 64 activation/runtime/
deployment tests also pass under system Python3.9. Both shell suites pass.
Additional plan checks pass 19 triage-handoff and 5 memory-bootstrap tests.
Cron-prompt contracts now pass 21/21 after local commit `6b7154a` updates two
stale assertions to the durable `--publish-handoff` contract. No live prompts
changed. After selecting the official runtime path and enforcing its checksum,
all 69 activation/runtime/deployment tests pass on system Python3.9, including an independent path
assertion for official Node24 versus Homebrew Node22 rollback. The 21 cron-prompt
and 19 triage-handoff tests also pass. A flaky read-only receipt assertion now
checks content, identity, permissions and modification/change timestamps without
treating read access-time updates as writes. These results do not establish a
completed full Phase A matrix.

Fable's follow-up identified a removed bounded skill retry; restoring it added
warm-up, permanent-failure and deadline regressions. Its subsequent re-review
reported no remaining material P1/P2 and accepted draft-branch publication, not
merge/cutover. Live platform/auth canaries remain required.

### Isolated and live-baseline evidence, 2026-10-11

- Official archive: `node-v24.21.0-darwin-arm64.tar.xz`, downloaded from
  `https://nodejs.org/dist/v24.21.0/`; SHA256 compared to official SHASUMS256:
  `6239d4cf92d864487ec8cd3615038f7b67e7f58b77b21cd2f09ea9fbd68065fe`.
  Node executable SHA256:
  `e4b5a3af0e05c75de2eae013904145f40fe7fc2a6e6f17510128bf45cca4e79b`.
- Eighteen recorded Node22/dependency paths were unchanged after staging.
  The official Node24 executable links system libraries, not Homebrew libraries.
- Copied 2026.8.35 package under Node24 passed its runtime and SQLite safety
  gates, SQLite3.53.4 TEXT/BLOB/JSON embedded/trailing-NUL round trips, and three
  native loads: fs-safe, tree-sitter-bash and node-pty. This is not all-addon QA.
  Sandbox probes denied production configuration reads, writes outside isolation,
  and networking. No second gateway or credential-bearing rehearsal was started.
- Copied `reachy-continuity` with the unchanged locked SDK passed 29 tests and
  TypeScript build under Node24 with networking denied. A rehearsal-only Vite
  host of `127.0.0.1` avoided localhost DNS; no production plugin edit was needed.
- Read-only verifier on the live Node22.23.1 / OpenClaw2026.8.35 gateway passed
  actual generated launch arguments, executable mapping, listener, health and
  authenticated required-skill visibility. `receiptRecorded` was false.
- A private inventory observed 16 jobs, 15 enabled, none running. That was a
  point-in-time cron observation, not proof that sessions or delivery queues
  were idle; refresh it before selecting a window.
- Protected evidence is under `~/Backups/openclaw/preflight-20261011T1921`.
  It includes supplemental code/service artifacts, not a complete recovery set.
  The full supported archive failed on an absolute symlink in the Claude
  automation browser helper. There is **no verified full-state backup/restore**.
- Synthetic macOS bsdtar recovery passed absolute/relative symlink identity,
  file mode, xattr, SQLite committed-WAL restoration and integrity checks, with
  the archive hash unchanged after extraction. No live data was copied by that
  test; ACL/FDA restoration and full-state coverage are not established by it.

## Attended maintenance proposal

**Approval update, 2026-10-11:** the operator asked to begin and explicitly approved
normal built-in recovery for the Node-only cutover after verified drain, empty
stopped work stores and validated backup. Existing schedules and messaging may
resume normally; no manual job rerun or resend is authorized. This replaces the
strict no-replay startup hold for A and its Node22 rollback, not the other recovery,
review or source-convergence gates. Runtime staging and the 64 GiB recovery budget
remain approved. The later attended attempt and recovery are recorded above.

Keep this a single Node-only change: unchanged OpenClaw2026.8.35, plugins, auth
authority, model defaults, household policy and schedules. No install/update,
Doctor/migrations, broad deployer, cron reconciliation, production-job rerun or
test message. Phase B is a separate decision after acceptance and soak.

### Resolve before requesting downtime

1. Finish review of the official-path diff and required A matrix; record the
   published review commit and expected wrapper/deployed-pull hashes. Reconcile
   the dirty live plan without automatic stash, reset or loss of its amendments.
2. Complete the named writer and startup-work inventory. For every required
   recovery root, identify its writer, exact owner hold/release operation and
   original loaded/enabled state. Do not stop unrelated household services merely
   to make a whole-directory copy easy. Classify mutable, reproducible caches or
   logs separately; any exclusion must be explicit in the coverage manifest.
3. Close the startup gate below, including safe restoration of Node22 if the
   checkpoint fails. Do not discover an unsafe restart only after stopping it.
4. Propose a quiet interval using fresh schedule/session/queue metadata. Reserve
   **60 minutes provisionally**: up to 35 for stopped checkpoint and restore
   validation, 10 for activation checks, 15 for restoring Node22. These are stop
   budgets, not measured promises. Refine them from measured size/throughput
   before approval. A deadline never authorizes forced kills or unsafe startup.
5. Obtain one final approval naming the commit, exact holds, checkpoint method,
   startup controls, activation/receipt, rollback including any source publication,
   time budget and attending operator. Stop for deviations, not every approved
   substep. Do not merge/main-push in advance of the deployment hold.

### Startup gate: approved normal recovery after drain

Inspection of the installed 2026.8.35 code established these limits:

| Path | Evidence | Consequence |
|---|---|---|
| Cron | `server-cron-Cyepv7HV.js`: `OPENCLAW_SKIP_CRON=1` gates cron | Does not prove other recovery paths are held |
| Channels | `server-startup-post-attach-CjcLl0CY.js`: skip-channel/provider flags gate channel start | Does not gate orphan-session marking or all plugin services |
| Main/subagent recovery | Same startup module schedules main-session recovery and activates the subagent registry independently | May resume work even with channel/cron skip flags |
| Outbound/session delivery | `server-runtime-services-B17CaMCd.js` starts separate delivery recovery routines | A disabled channel-start flag is not an outbound-send containment guarantee |

Installed `docs/gateway/restart-recovery.md` confirms recovery is always on.
`gateway.suspend.prepare` supports a cooperative drain but its in-memory lease
expires after two minutes and is reset by lifecycle restart; it is not a durable
maintenance mode. Do not patch vendor code, delete queues, change credentials or
use cron/channel skip flags as a substitute. The operator approved normal startup
instead, with this precondition:

1. Use `gateway.suspend.prepare` with a unique request ID, `drain: true` and
   `terminalPolicy: preserve`. Poll `gateway.suspend.status`; renew the same
   request before expiry if needed. Busy/unknown/expired is not permission to
   stop. Never use the terminate policy to ignore open terminals.
2. Once ready, use the supported gateway stop within the live lease. Verify the
   service is unloaded, its PID/listener gone and owned provider children exited.
   If abandoning before stop, explicitly resume that lease; do not rely on expiry.
3. Recheck stopped shared/per-agent databases read-only: no pending delivery or
   settlement, running cron receipts/tasks, restart sentinel, unfinished subagent
   lifecycle, running/aborted session or pending input. Unknown schema/status or
   a new writer is a stop. Terminal failed/permanent receipts are not pending
   messages; preserve them for deduplication, never clear or retry them.
4. Validate the recovery set before activation. Normal startup may handle new
   input and missed schedules under existing policy; no claim of a no-delivery
   canary is made. Do not manually invoke jobs/messages to test recovery.

Read-only observations at preparation: the live scheduler has 16 jobs, 15 enabled,
none running; restart preflight reported zero in all returned active-work counts.
The shared database contains only terminal delivery receipts (56 failed permanent,
3 completed bounded), terminal tasks and two fully cleaned terminal subagents;
no restart sentinel or pending workspace result. The registered agent database
has no running/aborted/recovery-marked session and no pending input. These are
point-in-time observations, not the required atomic drain/stopped-state proof.
The shared database also contains historical cron namespaces; its total row count
is not the active scheduler count. Two Codex provider processes are descendants
of the gateway and hold native agent databases open; verify their exit, too.

### Stopped checkpoint, without the rejected live staging tree

This replaces the proposed full live staging/snapshot copy, not the requirement
for a validated recovery point. The candidate method uses built-in macOS bsdtar
as an offline recovery artifact; it is **not** an OpenClaw backup-format archive.

The proposed source inventory is gateway-specific, not the entire household
application directory: shared/agent/native-provider state, configured workspace,
channel/plugin state, credentials as protected evidence, tools, exact installed
core/plugins, generated service contract, app, Node22 kegs/dependencies and the
staged Node24 runtime. Unrelated financial/household services, volatile logs and
historical backups remain untouched and are explicitly excluded in the private
manifest. On 2026-10-11 this scope measured about 3.71 GiB logical, with a
conservative peak estimate about 11.15 GiB including existing evidence and restore
copies, within the 64 GiB budget. Scope/size must be rechecked after drain.
The one-time operator script and proposed root/exclusion list are retained under
`~/Backups/openclaw/phase-a-20261011T203555Z`; none of these files is a verified
checkpoint until its stopped-source, manifest and restore checks pass.

1. Hold deployment, drain work and stop only the approved writers through their
   owners. Verify gateway job unloaded, old PID gone, port18789 unbound, no
   independent deployer, and no relevant writer. Record this before copying.
2. Create a fresh owner-only checkpoint directory under `~/Backups/openclaw`.
   Count existing evidence, archives, supplemental trees and restore copies
   against the **total 64 GiB** budget. Measure logical as well as allocated sizes;
   compression/APFS sharing must not be assumed to create spare restore capacity.
   Preflight the peak estimate and monitor usage. Abort before crossing the budget;
   preserve partial artifacts as incomplete and delete nothing automatically.
3. Record scoped source roots, entry types, SHA256 of regular files, link targets
   via `lstat`/`readlink`, modes/ownership and metadata needed for restore. Never
   hash a symlink's target as if it were the link. Preserve the actual FDA bundle
   and its symlink identity separately. Include generated plist/service-env,
   receipt, deployed pull, exact core/plugin/dependency trees, Node22 and linked
   dependencies, configured external agent/workspace roots and all required state.
   Revalidate existing supplements against stopped sources; they are not a final
   checkpoint merely because they were copied earlier.
4. Use relative archive member paths with `/usr/bin/tar --mac-metadata --acls
   --xattrs -cf ... -C <approved-parent> <explicit-roots>`. Do not use `-L`, `-h`,
   `-P`, link rewriting or an unreviewed exclusion glob. Preserve absolute symlinks
   **as links**; never change live links to satisfy the supported backup tool.
   Archive every required SQLite database together with its present WAL/SHM set
   while writers remain stopped. Hash/archive warnings or source changes fail
   the checkpoint. Keep private manifests/archives mode0600 under mode0700 roots.
5. Before extraction, validate member paths and types: no absolute member names,
   `..` traversal, escaping hard links, devices or entries nested under symlinks.
   Absolute symlink targets may be preserved only as inert recorded metadata,
   never traversed during validation. Restore to a fresh private directory with
   `/usr/bin/tar --same-owner --mac-metadata --acls --xattrs -xpf ... -C <fresh-directory>`;
   do not disable tar's path protections. Compare source/archive/restore manifests
   before opening any restored databases, including symlink targets and metadata.
   Preflight all archived UIDs/GIDs against the operator's ownership/membership;
   ordinary non-root extraction does not preserve group ownership even with `-p`.
   Do not silently ignore a group mismatch or automatically escalate privileges.
6. Keep the archive untouched. On the disposable restored copy only, check each
   required database's integrity and expected schema/role coverage; demonstrate
   that committed WAL content survives. Never use `immutable=1` to ignore WAL.
   Reject symlinked database paths and production-resolving references. Run only
   reviewed offline database checks with network/production access denied; never
   start a credential-bearing restored gateway or invoke its secret providers.
   Record duration, sizes, checks and explicit omissions without private payloads.
7. **No runtime switch until coverage and restore verification pass.** A failed or
   over-budget checkpoint leaves wrapper/package untouched; restore original
   service availability using the already-reviewed Node22 startup procedure.
   Do not downgrade the recovery requirement to meet the time budget.

Retain the verified set until 30 days after acceptance; there is no automatic
cleanup. Enterprise Claude authority/cache and other rotated credentials are
protected recovery evidence, **not automatic rewind targets**. Ordinary Phase A
rollback restores runtime-contract artifacts, not wholesale state or credentials.

### Activate, verify, or revert

Once the checkpoint passes, use the narrow activation sequence below. Leave
more than one second between final artifact writes and bootstrap; keep the real
generated service-env/FDA chain, not the recovery plist. Run the read-only
verifier first; publish a receipt only after its pinned version, process/listener,
health and authenticated skills checks and required A canaries pass.

Allow at most 10 minutes for candidate startup and validation; the verifier's
individual deadline remains 45 seconds. Failure returns to Node22 using the
bounded rollback below, with deployment still held. Do not repeatedly restart,
weaken checks or reinstall packages. If safe rollback cannot be verified, keep
the affected service held and escalate rather than resume unreviewed actions.

After queue review and successful acceptance, restore only the recorded controls
and deployment state once source/deployed files/receipt agree. Propose a further
30-minute attended observation with no test sends or job reruns. No automatic
Phase B continuation; report runtime/package/PID, checkpoint location, restored
holds, remaining limitations and any operator action.

## Before marking ready for cutover

- [ ] Close the reviewed plan's read-only preflight gates: pinned runtime/package,
  dependency transaction, actual SQLite safety, package entry point, schedules,
  native addons, named deployed invokers and current generated launch contract.
- [x] Stage Node24 only with the separately approved side-by-side transaction;
  prove Node22/dependencies unchanged and old-package/Node24 compatibility.
- [ ] Verify the stopped checkpoint and restore drill and measure the window. No
  credential-bearing second gateway, live secret providers or network delivery.
- [x] Implement and synthetically test the narrow activation verifier, including
  Node22 rollback, stale evidence, lock contention and failed publication.
- [x] Repair the four baseline deployment-fixture failures.
- [x] Independently review the verifier and close material findings with Fable.
- [x] Fix the two stale cron-prompt assertions and verify 21/21 tests.
- [x] Perform approved read-only Node22 macOS verification without recording.
- [ ] Complete the full required Phase A test matrix and Node24 live canaries. Mocked process
  evidence does not establish the real launch contract or grant cutover approval.
  The read-only Node22 baseline validates current paths and process assumptions,
  not the candidate Node24 gateway or future FDA behavior.
- [ ] Record the exact approved commit, runtime digest, checkpoint locations,
  owner identities, hold/release operations, startup queue controls, deadlines,
  rollback publication permission and operator availability.

## Narrow activation sequence — future approved window only

1. Record the current `main`/remote commit, clean-tree status, loaded/enabled
   states, wrapper and deployed-pull hashes, receipt, generated plist and process
   identity. Reconcile the preserved local plan edits without losing work.
2. Hold `ai.openclaw.dotfiles-pull` through its owner, and prove no already-started
   Terminal/deployer process remains. Do not run the full deployer, installer or
   cron synchronizer to establish a hold or to smoke-test this change.
3. Drain the gateway and establish the approved queue/delivery controls. Use the
   supported stop from an independent operator terminal; verify bootout, exited
   process and no port18789 listener. Stop only other identified checkpoint writers.
4. Take and verify the consistent pre-A recovery set. Preserve the actual app
   bundle behind the symlink, generated service contract, receipt, deployed pull
   copy, runtime/dependency trees and all state required by the parent plan.
5. Fast-forward the clean live checkout to the published reviewed commit only
   while held/stopped. Narrowly atomically copy the reviewed pull script to its
   deployed destination and verify its hash/mode. The app changes through the
   existing symlink; do not replace that symlink or install the recovery plist.
6. Bootstrap the verified generated job with its original enabled state and
   service-env → FDA app → Node24 launch chain. Start once under the approved
   normal-recovery posture after the stopped-work gate; no package update in A. Bound health/runtime checks by the
   agreed deadline. Verify actual executable mapping, not just process title.
7. Use the reviewed narrow verification command below, then
   run A canaries. Only publish the matching activation receipt after real health
   and authenticated live `skills.status` succeed; never invoke the broad deployer
   or write a hash merely to suppress its restart check. Failure keeps the hold.
8. After queue review and all checks, restore previously held controls and the
   deploy job only when source, deployed copy and receipt agree and the live tree
   is clean. Observe the agreed soak; Phase B requires a new go/no-go.

## Narrow verifier contract — not a cutover authorization

From the reviewed, fast-forwarded live checkout, the future operator can check
an already-started gateway without writing state:

```sh
/usr/bin/python3 openclaw/bin/gateway_activation.py \
  --expected-wrapper-sha256 "$APPROVED_WRAPPER_SHA256" \
  --expected-node-version 24.21.0 \
  --expected-node-sha256 e4b5a3af0e05c75de2eae013904145f40fe7fc2a6e6f17510128bf45cca4e79b \
  --expected-package-version 2026.8.35
```

During the approved activation window only, add `--record --previous-pid
"$PRE_STOP_GATEWAY_PID"`. Capture that PID before stopping the old gateway;
use `0` only when its absence was verified. Obtain the expected wrapper hash
from the approved source/checkpoint, not from an arbitrary current file. Do not
run the broad pull script to activate this change.
The verifier also requires `--expected-node-sha256` for Node22 rollback and
read-only baseline checks; use the protected approved Node22 dependency manifest,
never substitute the Node24 pin or adopt a changed executable's current hash.
Health must already be green; a transient health failure permits a later attended
recheck, never automatic restart or fabricated success.

Wrapper/app/state paths are fixed to the live `~/dotfiles`, `~/Applications` and
`~/.openclaw` locations; they do not switch to the current worktree. The shared
library comes from the script's checkout. Any separately approved pre-cutover
check from the review worktree must explicitly use the live baseline's hash and
versions and remains read-only; it does not activate worktree code.

The command requires the canonical generated service-env → FDA app launch
arguments both on disk and loaded in launchd; matching package/runtime versions;
the actual mapped Node executable/inode; exclusive loopback listener ownership;
strict top-level health; and authenticated live visibility/eligibility of the four
restaurant skills. It checks artifact fingerprints and process identity before
and after the probes and again immediately before recording. Probes share a
45-second deadline, including at most ten live-skill attempts with one-second
pauses for transient RPC failures or catalog warm-up. Exhaustion fails closed;
identity is rechecked after retries. Failures return sanitized status and preserve
the old receipt.
The expected launch shape is intentionally strict. It passed the read-only
Node22 baseline; the replacement Node24 process still requires a live canary.

Read-only CLI help on the installed OpenClaw2026.8.35, under explicit Node22,
confirmed `gateway call --url`, `--json` and `--timeout` on 2026-10-11. Inspection
of its installed 2026.8.35 client-bootstrap/credential modules showed that CLI `--url`
requires explicit CLI credentials, whereas `OPENCLAW_GATEWAY_URL` supports the
environment token. The helper therefore forces the loopback URL through that
environment variable, never putting the token in argv. The CLI's generic call
uses its read-only shared-state mode. Fixtures check the exact forced target,
token availability and non-export of unrelated cached values. Read-only live RPC
authentication with the existing device identity passed on Node22; repeat for
the candidate without enabling pairing/state writes as a workaround.

An owner-only nonblocking lock protects recording. The existing receipt and
state directory must be safe; a temporary mode0600 file is atomically published
only after verification. Read-only checks create neither lock nor receipt.
The process must start later than every checked artifact's mtime. Because macOS
`ps` reports seconds, leave more than one second between the last artifact write
and bootstrap; do not backdate files or weaken the gate to make it pass.

The shared deployer pins the Phase A versions above. Phase B must separately
review/update that package pin before releasing deployment; accepting any version
would defeat the verification. The same verifier supports explicitly pinned
Node22 rollback with the verified checkpoint wrapper hash and old package version.
There is no health override, automatic restart, auth refresh or delivery action.
Checks also run on unchanged-wrapper deployments. A later runtime/package/env
replacement, unexpected bind address or process mapping can therefore fail
verification without triggering the deployer's wrapper-hash restart. Investigate
and, where appropriate, perform an attended approved restart; do not loosen the
verifier or rewrite the receipt to silence it. Such failures leave deployment's
smoke check failed even if preceding unrelated deployment steps succeeded.

## Bounded Phase A rollback

Keep deployment held. Stop the gateway before restoring referenced code or
addons. Restore the verified pre-A wrapper, generated service contract, deployed
pull copy and any changed addon/runtime artifacts; preserve unrelated work and
current rotating credentials. Start the unchanged old package on Node22 under
the approved queue controls, verify health/FDA/functionality/live skills, then
record its verified activation. Node24 may remain installed.

Publish only the reviewed runtime revert(s) with the preapproved rollback scope.
Keep auto-deployment held until the clean live checkout fast-forwards to that
published `origin/main` commit, the deployed pull copy matches it and its wrapper
hash matches the receipt. Emergency file restoration is not source convergence.
No destructive reset, automatic stash/drop, unrelated revert or forced push.
If recovery, publication or reconciliation cannot be verified, leave the affected
service/deployment held and report the blocker. Do not proceed to Phase B.
