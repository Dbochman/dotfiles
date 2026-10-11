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
- The pull script stops before Git/deployment work if Node24 is unavailable.
  Its final gateway PATH agrees with the wrapper/recovery plist, and its skill
  RPC invokes absolute Node24 plus the existing package entry point, not an
  ambient `node` selected by the OpenClaw shim.
- The recovery plist's PATH follows the same contract. It is still **not** a
  replacement for the live generated plist/service-env launch chain.
- Synthetic tests cover these contracts, no Node22 fallback, rejected receipts,
  and the existing health/live-skill gate before atomic receipt publication.
  They execute temporary fixture wrappers and extracted deployment blocks, never
  the real gateway or the complete pull script.
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
| `dotfiles-pull.command` | `read_active_gateway_skills`; final `GATEWAY_RUNTIME_PATH` | Prepared in A; deploy/hash-check `~/.openclaw/bin/dotfiles-pull.command`, not just tracked source |
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
/opt/homebrew/bin/python3 -m unittest discover -s openclaw/tests -p test_oauth_refresh.py
/opt/homebrew/bin/python3 -m unittest discover -s openclaw/tests -p test_native_codex_config.py
/opt/homebrew/bin/python3 -m unittest discover -s openclaw/tests -p test_cron_sqlite_compat.py
/bin/bash openclaw/tests/test-imsg-bridge-ensure.sh
/bin/bash openclaw/tests/test-sync-cron-jobs.sh
```

The focused Python suites pass 32 tests; both fixture-only shell suites pass.
New runtime tests first failed against the old contract, then passed after the
source edits. These are not real Node24 execution, SQLite/ABI or live canaries.
Shell syntax, recovery-plist lint, changed Python compilation, `git diff --check`
and `./sync.sh validate` also pass. The deployment-contract suite was rerun after
the changes with the same four baseline failures and no additional failures.

**Existing baseline failures:** `test_deployment_contracts.py` has 3 failures and
1 error among 25 tests, reproduced before runtime changes with both system
Python3.9 and Homebrew Python3.14. The guarded-helper fixture lacks the required
`desktop-compute` source; installation stops before restaurant wrappers are
copied. Affected cases:

- `test_fresh_install_copies_required_guarded_helpers`
- `test_guarded_helper_dry_run_reports_every_copy_without_writing`
- `test_installer_publishes_every_standalone_skill_wrapper`
- `test_real_gateway_path_exposes_guarded_restaurant_wrappers`

Do not report this suite green. The new isolated runtime tests avoid that fixture
and cover the changed PATH contract independently. Repair the fixture in a
separate scoped change before final Phase A acceptance; this PR does not change
the desktop installer or production helpers to mask those failures.

## Before marking ready for cutover

- [ ] Close the reviewed plan's read-only preflight gates: pinned runtime/package,
  dependency transaction, actual SQLite safety, package entry point, schedules,
  native addons, named deployed invokers and current generated launch contract.
- [ ] Stage Node24 only with the separately approved side-by-side transaction;
  prove Node22/dependencies unchanged and old-package/Node24 compatibility.
- [ ] Complete isolated backup/restore rehearsal and measure the window. No
  credential-bearing second gateway, live secret providers or network delivery.
- [ ] Implement and test the **narrow activation verification/receipt path** using
  the existing authoritative parser and receipt logic. This PR adds regression
  coverage but does not yet expose a production activation command. It must check
  the replacement PID/executable, health, actual live skill results, expected
  wrapper hash, and a still-current result before publishing mode0600 atomically;
  no caller-supplied "health passed" override and no hidden restart/deployment.
- [ ] Resolve the four baseline fixture failures, complete the full required
  Phase A test matrix, and independently review the resulting implementation.
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
7. Use the reviewed narrow verification path from the readiness checklist, then
   run A canaries. Only publish the matching activation receipt after real health
   and authenticated live `skills.status` succeed; never invoke the broad deployer
   or write a hash merely to suppress its restart check. Failure keeps the hold.
8. After queue review and all checks, restore previously held controls and the
   deploy job only when source, deployed copy and receipt agree and the live tree
   is clean. Observe the agreed soak; Phase B requires a new go/no-go.

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
