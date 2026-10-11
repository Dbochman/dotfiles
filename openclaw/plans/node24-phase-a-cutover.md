# Node24 Phase A: draft implementation and cutover preparation

**Status:** DRAFT PR — NOT MERGE-READY OR DEPLOYED. Prepared 2026-10-11.
Branch: `codex/openclaw-node24-phase-a`, based on `f0367a0`.
Worktree: `/Users/dbochman/repos/dotfiles-openclaw-node24-phase-a`.

The [reviewed upgrade plan](node24-openclaw-stable-upgrade.md) controls approvals,
backup coverage, isolation and rollback. This document records the smaller first
implementation slice. It is not permission to run the cutover. The live checkout
remains on `main`; its existing uncommitted plan amendments are preserved. Do not
merge this branch or enable auto-merge before the approved maintenance window:
the live app is symlinked into that checkout and `main` feeds automatic deployment.

## Prepared source changes

- The FDA app wrapper selects the absolute Node24 executable and Node24-first
  runtime PATH; a missing/nonexecutable runtime stops before secrets or logs.
- The pull script stops before Git/deployment work if Node24 is unavailable,
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

Node24 has not been staged on the Mini. The executable-exists guards are not
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

The focused suites are fixture-only, not real Node24 execution, SQLite/ABI or
live canaries. The original runtime tests failed against the old contract, then
passed after the source edits. The four pre-existing deployment-suite failures
were caused by omitted `desktop-compute` and `remote-splat` helper fixtures;
those are now repaired, with a regression that compares fixture coverage to
the installer's source list. No production desktop helpers changed.

Verification totals and exact reviewed commit are recorded in the draft PR.
Required checks also include shell syntax, recovery-plist lint, changed Python
compilation, `git diff --check` and `./sync.sh validate`. The verifier's macOS
process/launchctl/lsof contracts remain mocked until an approved live canary.

The follow-up passes 86 focused Python tests (27 activation, 11 runtime, 26
deployment, 10 OAuth, 2 native Codex, 10 cron SQLite); all 64 activation/runtime/
deployment tests also pass under system Python3.9. Both shell suites pass.
Additional plan checks pass 19 triage-handoff and 5 memory-bootstrap tests.
Cron-prompt contracts pass 19/21: the unchanged Dylan policy and Julia outcome
review tests still assert the superseded `--validate-handoff` prompt rather than
`--publish-handoff`. Both failures reproduce on unchanged main. Track those
separately; the full Phase A matrix is not green and Node24 execution is untested.

Fable's follow-up identified a removed bounded skill retry; restoring it added
warm-up, permanent-failure and deadline regressions. Its subsequent re-review
reported no remaining material P1/P2 and accepted draft-branch publication, not
merge/cutover. Live platform/auth canaries remain required.

## Before marking ready for cutover

- [ ] Close the reviewed plan's read-only preflight gates: pinned runtime/package,
  dependency transaction, actual SQLite safety, package entry point, schedules,
  native addons, named deployed invokers and current generated launch contract.
- [ ] Stage Node24 only with the separately approved side-by-side transaction;
  prove Node22/dependencies unchanged and old-package/Node24 compatibility.
- [ ] Complete isolated backup/restore rehearsal and measure the window. No
  credential-bearing second gateway, live secret providers or network delivery.
- [x] Implement and synthetically test the narrow activation verifier, including
  Node22 rollback, stale evidence, lock contention and failed publication.
- [x] Repair the four baseline deployment-fixture failures.
- [x] Independently review the verifier and close material findings with Fable.
- [ ] Complete the full required Phase A test matrix, including the two unchanged
  cron-prompt test failures, and approve/perform bounded live macOS verification. Mocked process
  evidence does not establish the real launch contract or grant cutover approval.
  Include an explicitly approved read-only check against the existing Node22
  gateway before staging Node24, using its approved baseline hash and version,
  to validate generated paths, ownership, mapping and listener assumptions.
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
   no-replay posture; no package update in A. Bound health/runtime checks by the
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
  --expected-package-version 2026.8.35
```

During the approved activation window only, add `--record --previous-pid
"$PRE_STOP_GATEWAY_PID"`. Capture that PID before stopping the old gateway;
use `0` only when its absence was verified. Obtain the expected wrapper hash
from the approved source/checkpoint, not from an arbitrary current file. Do not
run the broad pull script to activate this change.
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
The expected launch shape is intentionally strict and still needs a live canary.

Read-only CLI help on the installed OpenClaw2026.8.35, under explicit Node22,
confirmed `gateway call --url`, `--json` and `--timeout` on 2026-10-11. Inspection
of its installed 2026.8.35 client-bootstrap/credential modules showed that CLI `--url`
requires explicit CLI credentials, whereas `OPENCLAW_GATEWAY_URL` supports the
environment token. The helper therefore forces the loopback URL through that
environment variable, never putting the token in argv. The CLI's generic call
uses its read-only shared-state mode. Fixtures check the exact forced target,
token availability and non-export of unrelated cached values; authenticated live
RPC behavior still requires the approved canary.
That canary must also confirm read-only shared-state authentication works with
the existing device identity; do not enable pairing/state writes as a workaround.

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
