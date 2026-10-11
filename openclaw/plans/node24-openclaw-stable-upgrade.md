# Node 24 LTS + OpenClaw 2026.10.1 Upgrade Plan (mac-mini)

**Path:** `openclaw/plans/node24-openclaw-stable-upgrade.md`
**Status:** PHASE A ACCEPTED; PHASE B IN PREPARATION. Claude Opus 5.5 drafted the plan; Codex reviewed and revised it against the repository and installed CLI. The separately verified Node24 cutover is recorded in [the execution record](node24-phase-a-cutover.md). OpenClaw2026.10.1 is not yet activated.
**Draft provenance:** explicit `claude-opus-5-5`, high effort, one successful tool-disabled turn; returned model usage confirmed that exact model. A temporary isolated Claude Code 2.1.296 client was used; the installed client was not upgraded.
**Date drafted:** 2026-10-11 (America/New_York). **Host:** `mac-mini`. **User:** `dbochman`. **Repo:** `/Users/dbochman/dotfiles`.
**Source of truth:** the tracked `openclaw/` tree. Runtime `~/.openclaw` is separate deployed state. Future runtime changes use reviewed deployment or supported configuration/migration commands, not untracked ad hoc patches.

> This documentation commit changes no executable or runtime configuration. The authorized push also includes the two previous presence/Codex-rule fixes, making those eligible for normal automatic deployment under their existing guards. Publishing this plan does **not** authorize the Node/OpenClaw upgrade. Approve the bounded maintenance phases below before execution; do not turn every already-authorized substep into another approval prompt.

---

## 0. Scope, target, and non-goals

**Goal.** Move the OpenClaw gateway from Node 22 to Node 24 LTS, then move OpenClaw from `2026.8.35` (extended-stable) to `2026.10.1`. Do this in two separately gated phases with tested rollback.

**Target choice**

| Item | Target | Evidence |
|---|---|---|
| OpenClaw | `2026.10.1` exact, pinned by version and integrity | [Release](https://github.com/openclaw/openclaw/releases/tag/v2026.10.1) and [npm metadata](https://registry.npmjs.org/openclaw/latest) agree on 2026-10-11 |
| Target engines | `>=24.16.0 <25 \|\| >=26.1.0` | [Version-specific package metadata](https://registry.npmjs.org/openclaw/2026.10.1) |
| Node | **24.21.0**, official macOS ARM64 runtime at `~/.local/share/openclaw/runtimes/node-v24.21.0-darwin-arm64` | Approved side-by-side alternative on 2026-10-11; official archive checksum verified. Homebrew preview would change dependencies also used by Node22, so no Homebrew transaction is allowed for A. See the [Phase A evidence and procedure](node24-phase-a-cutover.md). |

Node 24 is the current **LTS**, not the newest Node. Current is 26.11.1, which is a supported release line, not a beta. [OpenClaw's runtime guide](https://docs.openclaw.ai/install/node) recommends 26 for performance but supports 24. We choose 24 because Node itself recommends LTS for production, and 24 satisfies **both** the installed package (`>=24.15.0 <25`) and the target. That overlap is what lets us change the runtime and the package in separate phases.

**Re-resolution rule.** Re-check the exact Node runtime digest and installed package before A; re-check the OpenClaw stable tag before B. A changed target needs a short version/engine/migration review and an updated pin; do not silently install a moving tag or formula revision. Record the exact runtime digest and package tarball integrity. Node 26 is a separately reviewed alternative, not an automatic fallback.

**Non-goals**
- No general dependency upgrades and no `brew upgrade`.
- No upgrades to the Codex or Claude CLIs. No model, routing, or fallback changes.
- No system redesign.
- No change to `oauth-refresh.sh` or unrelated consumers that do not execute the OpenClaw package; those stay on Node22. Helpers that invoke the shared OpenClaw CLI are part of the compatibility cohort, even when their parent service is not the gateway. Change only their required invocation/runtime contract, not household policy or schedules.
- No machine-wide PATH rewrite or deletion of Node 18. This upgrade must nevertheless fix deterministic Node selection for the OpenClaw commands and deployment paths it owns (§3); general shell cleanup is a separate ownership audit.

---

## 1. Verified facts vs. open questions

**Verified at planning time**
- Installed: `/opt/homebrew/lib/node_modules/openclaw/package.json` is `2026.8.35`, with engines `>=22.22.3 <23 || >=24.15.0 <25 || >=25.9.0`.
- Homebrew `node@22` 22.23.1 is installed. There is no `/opt/homebrew/bin/node`.
- The noninteractive login shell resolves `/usr/local/bin/node` v18.16.0. `/opt/homebrew/bin/npm` (11.17.0) warns under Node 18. This is a PATH defect. It does **not** show that the gateway runs on Node 18.
- `/health` on `127.0.0.1:18789` returns `{ok:true,status:live}` and the launchd job is active. This is liveness only, not end-to-end health.
- The live generated plist runs `/bin/sh` with the env wrapper, the env file, and `~/Applications/OpenClawGateway.app/Contents/MacOS/OpenClawGateway gateway --port 18789`.
- `~/Applications/OpenClawGateway.app` is a **symlink** into `~/dotfiles/openclaw/OpenClawGateway.app`. Any edit to the tracked wrapper in the live checkout takes effect on the **next launch**.
- The tracked wrapper is bash. It is the final PATH authority, sources an owner-only secrets cache, requires the gateway token, and execs `/opt/homebrew/opt/node@22/bin/node …/openclaw/dist/index.js gateway --port 18789`.
- Disk available is about 535 GiB. That is free space, not a backup budget.

**Open questions (must be answered during preflight, not assumed)**
1. Resolved for A: the Homebrew preview would upgrade shared Node22 dependencies. The operator approved the isolated official runtime instead; no Homebrew installation occurred.
2. Resolved for the staged runtime: SQLite3.53.4 passed the installed package's safety gate and NUL round trips in isolation. Revalidate the exact runtime before activation.
3. Does the installed 2026.8.35 updater admit 2026.10.1 directly? Per the [updating guide](https://docs.openclaw.ai/install/updating), the old updater may not, so a first hop may need an explicit supported path.
4. Are any retired data or schema obligations still present? This covers the pre-July state that would need the 2026.9.5 bridge, and the retired same-file memory index that would need 2026.9.7. Assume neither until it is detected.
5. Does `reachy-continuity` import any SDK module retired in 2026.10.1? See the [release notes](https://docs.openclaw.ai/releases/2026.10.1): `channel-lifecycle`, `channel-message`, `channel-reply-pipeline`, `config-runtime`, `infra-runtime`.
6. Does 2026.10.1 replay missed cron jobs at startup? Is there a documented way to suppress catch-up?
7. Does the 2026.10.1 install or upgrade regenerate the launchd plist? See `.claude/skills/openclaw-upgrade-plist-overwrite/SKILL.md`.
8. The pull job is `ai.openclaw.dotfiles-pull`; its owner-only receipt is `~/.openclaw/state/gateway-wrapper.sha256`. What exact bounded hold/release and receipt-publication procedure will the implementation use without invoking a broad deployment? Check whether a pull is already running; unloading a scheduler alone does not prove its work is quiescent.
9. Does TCC Full Disk Access stay attached to the app wrapper after its script content changes?
10. Which installed Python path will run the existing unittest fixtures? Derive commands/import paths from the repository; do not assume `pytest` exists.

**Additional verified boundary:** the installed `openclaw update --help` supports `--channel`, `--tag`, `--dry-run`, `--no-restart`, and `--timeout`; it does not advertise newer candidate-admission flags. Help was checked using explicit Node 22. Its feature list does not prove that the real first-hop admission or migrations will succeed.

---

## 2. File map and test matrix

**Tracked files expected to change** (in a worktree, §4)

| File | Phase | Change |
|---|---|---|
| `openclaw/OpenClawGateway.app/Contents/MacOS/OpenClawGateway` | A | Change the absolute exec and PATH from `node@22` to `/Users/dbochman/.local/share/openclaw/runtimes/node-v24.21.0-darwin-arm64/bin/node`. Verify that versioned runtime and its digest at execution. |
| `openclaw/bin/dotfiles-pull.command` (near line 1379, `GATEWAY_RUNTIME_PATH`) and its deployed `~/.openclaw/bin/dotfiles-pull.command` copy | A | Point at the same official runtime directory. Keep it consistent with the wrapper. Narrowly deploy and hash-verify the actual launchd-invoked copy during A; do not rely on a future self-update. Preserve the active-gateway skill gate when reusing its verification/receipt logic for narrow activation, including Node24-first child PATH; do not call the whole deployer. |
| `openclaw/launchagents/ai.openclaw.gateway.plist` (recovery source) | A | Change the PATH entry from `node@22` to the versioned official runtime. This stays a recovery reference and is **not** a restore target for the live plist. |
| `openclaw/bin/gateway_activation.py`, `openclaw/lib/deployment.sh` | A | Shared narrow verification: pinned process/runtime/package, generated launch contract, actual listener/executable, strict health and authoritative live skills; read-only by default, explicit locked atomic receipt recording, no hidden deployment/restart. Pin changes for B require separate review. |
| `openclaw/tests/test_deployment_contracts.py`, `openclaw/tests/test_gateway_runtime_contract.py`, `openclaw/tests/test_gateway_activation.py` | A | Verify agreement among app wrapper, recovery plist and deployment PATH; retain Node22 assertions for unrelated wrappers. Cover missing Node24, mismatched receipts, unavailable/invalid/ineligible live skills and stale process/hash evidence preventing receipt publication. |
| `openclaw/sync-cron-jobs.sh`, `openclaw/bin/home-event-camera.py`, `openclaw/bin/home-event-delivery.py`, their focused tests, and any additional confirmed CLI invokers from §3 | Before B | Remove their explicit Node22 selection for shared OpenClaw CLI calls; use the reviewed Node24 invocation contract and verify actual child runtime selection with synthetic/no-delivery fixtures. Coordinate deployment and any necessary targeted reload before replacing the global package; do not alter policies, schedules or send messages. |
| `openclaw/plugins/reachy-continuity/**` | B | Change only if import or compatibility checks fail. Use a separate commit for any port. |
| `openclaw/LAUNCHAGENTS.md`, `openclaw/plans/dependency-maintenance.md` | Docs | Document planned vs actually activated runtime, rollback, and unchanged OAuth authority; do not claim rollout until verified. |
| `Brewfile`, `zshrc`, other Node consumers | Audit only initially | Decide whether Node 24 needs a reproducible dependency declaration. Do not run `brew bundle` or change shared shell/service runtime selection as a shortcut. |

**Explicitly unchanged:**
- `openclaw/bin/oauth-refresh.sh` and `openclaw/tests/test_oauth_refresh.py`. Node 22 stays intentional; it is a separate enterprise-Claude credential authority.
- LaunchAgents outside the confirmed OpenClaw invocation cohort; do not change a service merely because its name contains OpenClaw.
- Model and routing config.

**Test matrix — required future checks, not recorded passes.** Use the selected absolute Python interpreter and Node-first PATH with synthetic fixtures. Record baseline results before implementation edits. Classify pre-existing failures separately; a failure in a changed contract still blocks that phase even if it predates this work.

| Check | Baseline (pre-edit) | After A | After B |
|---|---|---|---|
| `test_deployment_contracts.py` | Required (expects 22) | Required (expects 24) | Required |
| `test_gateway_runtime_contract.py`, `test_gateway_activation.py` | Record expected old-contract failures for new tests | Required; synthetic fixtures only | Required; synthetic fixtures plus separately approved live canaries |
| `test_oauth_refresh.py` | Required | Required (still 22) | Required |
| `test_native_codex_config.py` | Required | Required | Required |
| `test_cron_sqlite_compat.py` | Required | Required under Node 24 | Required |
| `test_morning_triage_handoff.py`, `test_cron_prompt_contracts.py`, `test_memory_bootstrap_contract.py` | Required | Required | Required |
| `test-sync-cron-jobs.sh`, `test-imsg-bridge-ensure.sh` | Required (synthetic only) | Required | Required |
| `test_home_event_camera.py`, `test_home_event_delivery.py`, plus focused tests for each confirmed CLI invoker | Record current child runtime selection | Required if its invocation changes in A | Required under the candidate package/runtime; mocked capture/inference/sending only |
| `./sync.sh validate`; `bash -n` on changed shell; `plutil -lint` on changed plists; `py_compile`; `git diff --check` | Required | Required | Required |
| `reachy-continuity`: `npm run verify` in isolated worktree | Existing locked SDK/runtime baseline | Existing locked SDK under Node 24 | Deliberately update candidate SDK dependency/lockfile in isolation, then verify against 2026.10.1 and test host registration |

A plain `npm ci` with today's plugin lockfile still selects the old SDK; it is not a 2026.10.1 compatibility test. Exercise `plugin-entry`, runtime LLM calls, hooks and registered gateway methods using synthetic data, not only searches for retired import names. The current broad peer range proves no compatibility.

Do not run `install.sh` or `dotfiles-pull` as validation. Neither is read-only.

---

## 3. Preflight (read-only, no window required)

All of these are requirements. Where a command form is shown, it is illustrative. Confirm syntax against local `--help` or `man` first.

1. **Record state.**
   - Commit SHA of `main`.
   - Hash of the live generated plist.
   - `ProgramArguments`.
   - The live plist's `KeepAlive` and `RunAtLoad` values, plus the job's loaded/enabled state. Both plist values were verified true during final review; recheck before the window.
   - The `launchctl print` state for the gateway and for `dotfiles-pull`.
   - The last-activated wrapper receipt.
   - Store this in the backup manifest (§4), not in the repo.
2. **Inventory runtimes.**
   - Resolve `node`, `npm`, and `openclaw` on the relevant PATHs and inspect configured launch arguments plus the known `/opt/homebrew` and `/usr/local/bin` locations. Do not inventory the entire home directory or disk.
   - Identify consumers of those specific executables using narrowly selected process fields or executable mappings; do not expose secret-bearing argv/environment or use broad `pkill`.
   - Build a bounded invocation inventory from tracked scripts/LaunchAgents/cron definitions and their named deployed counterparts: parent owner, shared OpenClaw entry point, effective child Node/PATH, deployment/reload needs and synthetic validation. Known Node22-pinned CLI invokers include `sync-cron-jobs.sh`, `home-event-camera.py` and `home-event-delivery.py`; a gateway PATH change does not override their own child environments. Trace indirect callers and distinguish an actual CLI invocation from a mere OpenClaw-named path. Every confirmed invoker must select a supported runtime before Phase B, or Phase B is blocked. No all-home/disk search and no real notification/camera/cron action for this audit.
   - Do **not** remove Node 18 or 22 until an ownership audit shows nothing depends on them.
3. **npm identity.**
   - Determine which `node` the intended npm will run under.
   - Use the versioned official runtime's own npm with an explicit Node24 and command-scoped child PATH.
   - Confirm the global prefix/root and resolved OpenClaw entry point. Preserve `/opt/homebrew/lib/node_modules/openclaw` as the active install owner; use an explicit reviewed `--prefix /opt/homebrew` if required rather than changing global npm configuration. The isolated runtime's npm may default to a different prefix.
   - Validate controlled deployment and gateway commands under Node24. For operator commands, including interactive and SSH terminals, use `/Users/dbochman/.local/share/openclaw/runtimes/node-v24.21.0-darwin-arm64/bin/node /opt/homebrew/lib/node_modules/openclaw/dist/index.js <arguments>` with a command-scoped Node24-first PATH for child tools, after verifying that entry point against the active package. Baseline commands use the explicit Node22 equivalent. Bare `openclaw` in an arbitrary shell is not a supported upgraded invocation until the separate shell audit. Do not rely on `.zshrc`, change global PATH, or add a shadowing shim as a shortcut.
4. **Target pinning.**
   - Run `npm view openclaw@2026.10.1 version engines dist.integrity` and record the output.
   - Preserve the actual installed 2026.8.35 tree, dependencies and plugin builds in addition to registry integrity: reinstalling an old version may resolve different transitive dependencies or require unavailable network access.
   - Verify downloaded tarball bytes against registry integrity and keep a file manifest of the staged/installed tree. Tarball integrity cannot be compared directly to a post-install directory hash.
5. **Homebrew transaction preview.**
   - Determine exactly which formulae a `node@24` install would add or upgrade. Use only preview options that `brew help install` confirms exist.
   - Plan to set `HOMEBREW_NO_INSTALL_CLEANUP` and `HOMEBREW_NO_AUTO_UPDATE` for the install. Verify both in `man brew`.
   - **Stop condition:** if the install would upgrade a shared dependency that `node@22` links, and that cannot be isolated, do not force it. Instead write a separately reviewed alternative: a checksum-verified official Node 24 runtime installed side by side.
6. **Updater path.**
   - Start with the installed CLI's supported preview: `openclaw update --channel stable --tag 2026.10.1 --dry-run --json`, only under the explicit runtime and reviewed environment. This command has not been run during planning.
   - Compare its admission result with [updating guidance](https://docs.openclaw.ai/install/updating). Dry-run is not proof of candidate rehearsal.
   - Prefer the supported, exact-target updater path when rehearsal verifies ownership, plugin convergence and migration fencing. Document the manual-install fallback only if that first hop refuses or cannot preserve this service's custom wrapper. Do not bypass safety refusals with guessed flags.
   - Record the updater's own resolved package root, npm executable and effective prefix in preview/rehearsal, including child processes. Prove it targets `/opt/homebrew/lib/node_modules/openclaw` before production mutation; an operator's separate `npm --prefix` is not evidence about the updater's child. If ownership cannot be proved, stop for a supported correction or separately reviewed fallback, not post-hoc duplicate-install cleanup.
   - Preserve the exact target while explicitly moving `update.channel` from extended-stable to stable through the supported path. A direct npm install alone does not select the new update channel.
7. **Migration obligations.**
   - Inspect state for the retired markers described in the updating docs: the pre-July layout and the same-file memory index.
   - Record findings from bounded schema/layout metadata, not private records. Only add a bridge when the target's documented migration checks require it; retain its exact version and isolated rehearsal. The calendar version alone does not decide migration compatibility.
8. **Plugin audit.**
   - Search `openclaw/plugins/reachy-continuity/src` (source only) for the retired SDK module names.
   - Inventory installed official plugin versions and capability declarations.
   - Do not read Reachy or iMessage transcripts or the continuity capsule.
9. **Schedules.**
   - Read actual cron and LaunchAgent schedules from tracked source and from the live job list. The historical "7 am" reference may be stale.
   - Choose a window that avoids household and briefing jobs and keeps finance order intact: 06:15 refresh, then 07:35 Forecast ledger.
10. **Config snapshot.** Export a redacted semantic snapshot of:
    - default agent (Astra),
    - native Codex routing,
    - model metadata, including `supportsUsageInStreaming` removed for Astra only,
    - fallback order (Opus 5.5 high),
    - tool allowlists,
    - delivery recipients.

    This is the before-image for the semantic diffs later. Never print secrets, tokens, or the raw `paired.json`.

**Diagnostic example** (in-memory only; the packaged SQLite gate and additional NUL round trips passed against the staged official runtime; see the Phase A evidence):

```sh
/Users/dbochman/.local/share/openclaw/runtimes/node-v24.21.0-darwin-arm64/bin/node -e '
const { DatabaseSync } = require("node:sqlite");
const db = new DatabaseSync(":memory:");
const v = db.prepare("select sqlite_version() as v").get().v;
db.exec("create table t(a text, b blob, j text)");
const s = "x\u0000y\u0000";
db.prepare("insert into t values (?,?,json_quote(?))").run(s, Buffer.from(s), s);
const r = db.prepare("select a, b, j from t").get();
if (r.a !== s || !Buffer.from(r.b).equals(Buffer.from(s)) || JSON.parse(r.j) !== s) {
  throw new Error("SQLite round-trip mismatch");
}
console.log({ sqliteVersion: v, roundTripsVerified: true });
db.close();
'
```

This example verifies exact round-trip content, not just length. Numeric support and actual loaded-library safety are separate gates. Follow the selected release's packaged checks and [runtime compatibility reference](https://docs.openclaw.ai/install/node-compatibility):
- The binding loads.
- Embedded and trailing NULs survive in TEXT, BLOB, and JSON without truncation or substitution.
- The **actual** SQLite is WAL-safe: ≥3.51.3, or ≥3.50.7 on the 3.50 line, or ≥3.44.6 on the 3.44 line.

`process.versions.sqlite` alone, or the Node version alone, is not sufficient. Run the target's packaged compatibility gate as well. The rolling documentation labels some capability-admission behavior as unreleased; verify which checks 2026.10.1 actually ships instead of assuming all current documentation applies. Passing a custom check cannot relax the package's numeric engine requirement.

---

## 4. Protected backup and rehearsal isolation

**Backup location and policy**
- Use an owner-only (`0700`) directory outside `/tmp`. The operator chooses it, for example under `~/Backups/openclaw/<timestamp>`.
- Measure first with `du`, then set an explicit size budget.
- Do not take an arbitrary whole-home archive.
- Retention: keep until acceptance plus an agreed hold period. Never auto-delete older backups.
- Write a manifest with `shasum -a 256` for every file.

**2026-10-11 decision:** the operator approved `~/Backups/openclaw`, a total
64 GiB budget including restore copies, retention until 30 days after acceptance,
and no automatic deletion. The supported full archive failed on an existing
absolute symlink; no verified full checkpoint exists. Do not alter live links to
make it pass. Preparation of the [bounded attended procedure](node24-phase-a-cutover.md#attended-maintenance-proposal)
is approved; the operator subsequently asked to begin and approved normal recovery
after verified drain and backup for A. Other readiness gates still apply. Prefer a stopped, symlink-preserving
checkpoint over another full live staging tree. This is a custom recovery set,
not an archive supported by `openclaw backup restore`.

**Recovery set (scoped and manifest-driven)**
- OpenClaw configuration, shared and per-agent databases, sessions, channel/plugin state and configured workspace/agent roots, including roots outside `~/.openclaw`. Inventory paths without exposing private contents.
- Prefer the installed supported `backup create --verify` and `backup verify` workflow after checking local help. It snapshots SQLite; its restore command stages into a fresh directory rather than restoring in place. Prove coverage against the actual config/manifest instead of assuming one core DB is everything.
- Preserve the live generated plist, `service-env` wrapper/env file, the actual FDA app bundle contents and symlink target identity, the actual installed core/dependency tree, official/custom plugin code and dependencies, and the activation receipt. Backup archives omit plugin `node_modules`, so retain these separately.
- Preserve pre-phase-A and pre-phase-B recovery points separately. A Git SHA or registry version alone is not a working offline rollback artifact.
- Secrets and identity/delivery metadata belong only in mode-0600 recovery files under the mode-0700 directory, never Git or console output.

**Credential exception:** preserve the live enterprise-Claude authority at `~/.openclaw/claude-automation` and its consumer cache `.anthropic-oauth-cache`; exclude both from automatic rewind. Do not copy refresh tokens into the rehearsal, create a competing refresh authority, or restart the personal CLI login. Other credentials can rotate after a checkpoint too: compare protected metadata during rollback and stop for supported auth recovery rather than blindly restoring stale OAuth/ratchet state. Preserve post-checkpoint evidence before any restore.

**Consistency and restore drill.** Preliminary backup/rehearsal can be prepared ahead of the window, but the final rollback checkpoint must be consistent across all affected stores:
1. Hold the named automatic deploy/update jobs and drain active gateway work; record prior loaded/enabled state. Verify no deployment process remains active.
2. Through their actual owners, stop the gateway and any specifically identified writers to its migration scope. Do not stop household services just because their label contains OpenClaw. Failure to establish quiescence is a stop condition.
   - For this launchd-managed gateway, use the supported stop from an independent operator terminal, verified to boot out `gui/<uid>/ai.openclaw.gateway`; the installed 2026.8.35 stop implementation already does this and checks port release. Verify the job is unloaded, the recorded gateway process has exited and port18789 is unbound. A signal or plain `launchctl stop` alone is insufficient with `KeepAlive` enabled. Keep it unloaded through checkpoint, mutation and startup preparation; recheck after updater/Doctor steps. Use this same hold for both phases and rollback.
   - Before any production start, including Phase A and rollback, establish a release-supported no-replay/no-unapproved-send posture and review pending work. If this requires changing production jobs or delivery controls, include those exact temporary holds in the phase approval or defer. Preserve queued work and prior control states; restore them only after queue review and required canaries.
3. Create and verify the supported snapshot/archive plus supplemental artifacts. For raw offline copies, capture complete SQLite/WAL sets before opening any copy; never mix file copies from different times or delete live WAL/SHM files. Supported snapshot archives intentionally omit sidecars and contain self-consistent database images instead.
4. Validate checksums, database integrity and role/schema checks on staged copies; restore the archive to a fresh private directory and prove the old runtime/package can open that recovery copy without network/channel/scheduler activity. Preserve an untouched archive.
5. Record coverage, size and exact restore procedure. Do not open a partial backup or declare readiness from `cp`/archive exit status alone.

**Rehearsal isolation.** The rehearsal must never become a second live gateway.
- Use a separate git worktree, created with `git worktree add`, for all tracked edits. This keeps the symlinked live app wrapper untouched until activation.
- Install the candidate package into an isolated prefix with `npm install --prefix <rehearsal-dir>`, not globally.
- Use a separate HOME, configuration, state, agent roots, workspace, plugin storage, caches and temporary directory. Rewrite all absolute references to production roots. The installed CLI has `agent exec --config/--cwd/--state-dir` options, but those alone are not a whole-gateway isolation guarantee.
- Use a non-production port. Do not bind 18789.
- Disable all channels: no iMessage, no APNs, no outbound messaging.
- Disable cron and catch-up.
- Remove provider and OAuth credentials, or replace them with inert placeholders. Use a rehearsal-only gateway token.
- No refresh of shared OAuth.
- Before executing candidate/plugin code on a copy, review its effective paths, environment, secret-provider hooks, cron/channel enablement and filesystem/network boundaries. Use an enforceable isolated environment for credential-bearing migration copies; HOME and a different port alone are insufficient. If isolation cannot be demonstrated, stop rather than launching a second gateway.
- Keep the verified recovery archive untouched; sanitize only a disposable rehearsal copy. Any intentional sanitization must be recorded and distinguished from migration changes. Do not remove schema-bearing auth/channel tables just to suppress network activity.
- Run candidate Doctor/migrations only on this isolated copy first. Compare schemas, row counts/role coverage and semantic policy changes without dumping private records. Do not source the production secrets cache or invoke `op`/live secret providers in rehearsal.

---

## 5. Phase A: Node 24 runtime with the existing 2026.8.35 package

**Precondition:** §3 is complete with no stop conditions, and the operator has approved a Phase A window.

**Approved A-only startup exception, 2026-10-11:** the operator approved normal
built-in recovery after verified cooperative drain, stopped databases with no
pending work, and validated backup. The installed package's recovery is always on;
its two-minute suspension lease is not persistent across restart. Follow the
[updated drain/startup gate](node24-phase-a-cutover.md#startup-gate-approved-normal-recovery-after-drain)
for A and Node22 rollback instead of a strict no-replay startup hold. Existing
messaging/schedules may resume, but no manual job reruns or resends are authorized.
This does not approve B, skipping backup or ignoring surviving writers.

1. In the separate worktree, run the current-runtime baseline and prepare Phase A source/test edits. After Node24 staging in step 2, run the candidate matrix. Review and commit without advancing the live checkout or publishing activation-triggering changes before the approved window.
2. Use the separately approved, checksum-verified official Node24 runtime; do not install the Homebrew alternative. Before activation:
   - confirm `node@22` still runs and its linked dependencies are unchanged;
   - rerun the SQLite diagnostic against the exact staged runtime.
3. Hold `dotfiles-pull`, verify no in-flight deployment, and record its prior state. Confirm the supported old-package/Node24 pairing in isolation. Native addons must load under Node24; if rebuilding them is required, rebuild a staged copy at the unchanged package version and include that tree in the reviewed activation/rollback set, not an in-place experiment on production.
4. Run 2026.8.35 under Node24 in the rehearsal (§4); complete offline checks, including native modules. Live authentication/FDA canaries are deferred until activation, not attempted from the credential-isolated rehearsal.
5. Activation (in the window):
   - drain work, stop the gateway through its owner and take the verified pre-A checkpoint before changing the live wrapper;
   - require a clean live checkout and fast-forward it to the already-published reviewed commit; do not copy worktree files into that checkout. If local work or divergence prevents this, stop for reconciliation without stashing, discarding or overwriting it. Then narrowly deploy the necessary runtime-contract files, accounting for the app symlink;
   - preserve service environment → FDA app wrapper → absolute Node24 → exact OpenClaw entry-point ownership; don't assume `dist/index.js` remains valid after later package changes;
   - after the §4 startup controls are established, bootstrap the verified live generated plist to restore the unloaded job, preserving its recorded enabled state and custom launch chain. Bootout is required for the maintenance hold even when the plist is unchanged; do not restrict it to plist edits. Confirm replacement PID, executable mapping and health with bounded waits.
6. Publish the owner-only activation receipt atomically only after the replacement gateway passes health/runtime checks and the live `skills.status` gate in §7 canary8. Reuse the deployer's bounded verification and atomic mode-0600 receipt logic through a narrow tested path, without another restart or the deployer's unrelated actions. Required skills must be eligible and model-visible in the active gateway; an unavailable/invalid RPC or missing skill keeps the receipt unchanged and blocks acceptance. Never substitute a local-process skill report or pre-write a receipt merely to silence the restart check. The normal deployer checks live skills even when the wrapper hash matches; that later check does not replace activation-time verification.
7. Run the Phase A canaries, then release `dotfiles-pull` back to its recorded state.

**Phase A gate:** all required A checks pass, old OpenClaw remains at 2026.8.35, unrelated consumers still work on22, and the actual controlled CLI/service contexts select24. Agree a bounded soak and stop deadline before starting. Phase B requires a separate go/no-go; no automatic continuation just because A passed.

---

## 6. Phase B: OpenClaw 2026.10.1, official plugins, and migrations

**Preconditions**
- Phase A has been accepted and stable for an agreed soak period.
- Rehearsal of 2026.10.1 against copied state has passed, including the doctor/migration semantic diff.
- `reachy-continuity` passes `npm run verify` against 2026.10.1 in isolation.
- Any import port is committed separately and reviewed.
- Every confirmed shared-CLI invoker from §3 is deployed and synthetically verified with supported Node24 selection, including helpers with their own child PATH. Any necessary targeted reload is named in the Phase B approval; background consumers that do not execute OpenClaw remain unchanged. Verify this cohort before replacing the global package, not only through later gateway canaries.

**Steps**
1. In the window: hold `dotfiles-pull`, stop the gateway, take the consistent backup (§4), and verify the manifest.
2. Use the rehearsed exact-target updater from an independent operator terminal outside the gateway process tree, with explicit Node24 invocation (§3), `--channel stable --tag 2026.10.1 --no-restart`. Request stable-channel intent and preserve the custom launch chain. Keep the gateway unloaded through step6; `--no-restart` suppresses the updater's normal restart, not other lifecycle or migration effects. If a manual fallback was separately justified in §3, install only `openclaw@2026.10.1` with the verified npm and explicit active prefix, then run supported Doctor/plugin convergence in the documented order without service startup. In either case:
   - do not invoke the retired weekly-upgrade helper, `npm update -g`, or an unversioned install;
   - validate npm lifecycle-script authorization (`--allow-scripts=openclaw` for the supported npm) without blanket script grants; record any required native-dependency build;
   - verify tarball integrity before install, then the installed version, entry point and expected file manifest;
   - ensure CLI and service resolve the same one active package owner; retain recovery copies without confusing them with active duplicate installs;
   - leave unrelated global packages, Codex/Claude versions and global npm settings unchanged.
   - verify after installation and each subsequent mutating helper that the gateway remains unloaded with no gateway process or listener. An unexpected start is a stop condition: re-establish containment and assess state/queue effects before any continuation; do not treat a later successful stop as proof that nothing ran.
3. Check the live generated plist against the recorded `ProgramArguments` and env wrapper. If the install regenerated it, follow the `openclaw-upgrade-plist-overwrite` skill. **Do not** copy the recovery plist over the live one.
4. Run the target's Doctor/migrations only under the verified writer-quiescence and backup boundary. Record migration ownership/completion; require no unresolved mandatory migration or deferred publication before acceptance. Diff effective config/permissions semantically: necessary schema/key rewrites and the approved stable-channel change are allowed only when reviewed, while effective defaults, routing, fallback order, allowlists and recipients must remain unchanged. An unexpected policy change is a stop.
5. Converge Codex and iMessage to the target's compatible release cohort and verify actually loaded paths/versions, not merely package presence. Do not assume the old 2026.8.35 pins remain loadable: current update guidance identifies pre-2026.9.7 Codex runtime builds as incompatible. Compare capability manifests before activation; obtain exact new consent if needed, never blanket-accept scopes. Verify any reviewed custom plugin port through host registration. Stop for attended auth/FDA recovery rather than substituting another account.
6. Before startup, resolve scheduler catch-up behavior and pending deliveries with the release's supported controls. Establish the reviewed no-replay/no-unapproved-send startup posture; a different profile or a hopeful disabled-job flag is not enough. If this cannot be enforced without altering production jobs, obtain that narrow approval or defer.
7. Start once, verify stable-channel intent, and run B canaries. Before releasing the deploy hold, feed the active gateway's `skills.status` response through the actual `openclaw_gateway_missing_skills_from_status` function from the reviewed `openclaw/lib/deployment.sh` used by the deployed pull job. Verify cron-sync's actual read-back parser/comparison against protected read-only candidate snapshots and synthetic fixtures as well; isolate that reader in a tested harness, never invoke the whole synchronizer or assume it has a safe dry-run flag. An incompatible response shape or comparison failure blocks release. Restore any temporarily held channel/scheduler controls to their exact baseline only after queue review; do not delete pending work or describe skipped work as completed. Release the deploy hold only after these checks and the verified receipt are current.

---

## 7. Canary matrix (read-only and synthetic first)

| # | Canary | A | B |
|---|---|---|---|
| 1 | Actual executable mapping of the replacement gateway PID is Node24, and the active package version/path is correct (not merely shell PATH or process title) | Required | Required |
| 2 | `/health` reports live (liveness only) | Required | Required |
| 3 | In-process `node:sqlite` compatibility per docs | Required | Required |
| 4 | Config semantic diff is empty for protected fields | Required | Required |
| 5 | Read-only iMessage DB access through the app wrapper (FDA retained); imsg bridge left untouched and still supervised | Required | Required |
| 6 | Prior no-delivery read fixture: native Codex routing on Astra, no fallback triggered | Required | Required |
| 7 | Compare production job IDs, payloads, schedules, ownership, enablement, tools and delivery routes; no production rerun/catch-up. After scoped approval, run one disabled future-scheduled synthetic fixture job manually, with read-only tools, no delivery and a fixed deadline; verify terminal receipt and remove only that test job. Listing alone is not scheduler validation. | Required | Required |
| 8 | `reachy-continuity`, Codex and iMessage load with expected capabilities and actual paths/versions; use synthetic hook/method cases. The active gateway's authenticated `skills.status` RPC must also report `opentable`, `restaurant-book`, `restaurant-snipe` and `resy` eligible and model-visible, matching the deployer's required-skill set. Use bounded retries and fail closed on unavailable/invalid results; no local fallback. This skill gate must pass before any activation receipt, including rollback. | Required | Required |
| 9 | Memory credential resolution/retrieval succeeds without private content in output; cron/usage dashboard read-only queries understand the new stores; report pre-existing health faults separately | Required | Required |
| 10 | Exactly one real messaging round-trip, **only after explicit approval**, never retried automatically | optional | optional |

Never take these actions as part of canaries:
- production cron reruns;
- catch-up actions;
- unbounded wait loops;
- source or log dumps;
- automatic resend of messages.

---

## 8. Rollback

**Phase A rollback**
1. Keep/restore the deployment hold and stop the gateway before replacing any referenced executable or addon tree.
2. Restore the exact pre-A wrapper, service contract, deployed `~/.openclaw/bin/dotfiles-pull.command` copy and any staged addon changes. Hash-verify the deployed pull copy against the restored reviewed source before release. Restore the Node22 runtime/dependencies only if the manifest proves their identity; do not assume Homebrew preserved them.
3. Start the old package on22 under the §4 startup controls, verify health and functionality including the live skill gate, then publish the activation receipt. Revert only the runtime implementation commit(s) in Git without force-pushing or undoing unrelated work.
4. Restore previously loaded jobs only after verification. Keep `dotfiles-pull` held until the rollback source is published to its tracked `origin/main`, the live checkout is fast-forwarded to that reviewed rollback commit with `git status --porcelain` empty, and a read-only wrapper/receipt comparison predicts no reactivation. Emergency artifact restoration under the hold is not source convergence: reconcile any restored tracked files before fast-forward without losing unrelated work; no file-copy substitute, automatic stash/drop or destructive reset is allowed. Include any required rollback publication in the phase approval; if publishing or reconciliation is blocked, retain the deployment hold and report it rather than relying on an uncommitted restored wrapper. This source-convergence gate applies to Phase B rollback too, including target-only plugin/CLI-contract commits. Do not invoke the deployer to test the gate.

The isolated official Node24 runtime can stay installed. Removing it is a later, separate decision.

**Phase B rollback before migration/state repair**
1. Stop gateway/update processes through their owners and confirm no state writer remains.
2. Prove that no DB, policy, plugin-state or delivery migration/repair ran. If uncertain, use the post-migration recovery branch.
3. Restore the exact pre-B core/dependency/plugin trees and coherent config/service contract, retaining Node24 as the accepted A runtime. Do not rely on a network reinstall as the only rollback.
4. Recheck credential freshness and queued work, start under the no-replay posture, then run A canaries.

**Phase B rollback after migration, or when mutation status is uncertain**
1. Keep deployment/channel/scheduler holds, stop affected writers and preserve the failed package/state plus post-checkpoint receipts privately; do not delete them.
2. Restore the verified pre-B archive into a fresh directory. Verify integrity, full agent/plugin coverage and exact old code/dependency trees before offline activation.
3. Restore the coherent pre-B config, core/plugins, service environment/FDA wrapper and accepted Node24 runtime. Handle current auth authorities separately as above; never rewind them automatically.
4. Reconcile any post-checkpoint messages, approvals, executions, dedupe records and rotated credentials before enabling channels or schedules. State rewind must not cause duplicate messages or household actions. If reconciliation is uncertain, keep delivery/actuation held and escalate rather than replaying work.
5. Verify the old package can open the restored compatible state, activate it offline, start under the reviewed containment posture and run A canaries. Only then restore prior job states and publish the matching activation receipt.

A package downgrade alone cannot reverse a newer DB schema. **2026.8.35 must never open migrated production state.** Follow the [downgrade boundary](https://docs.openclaw.ai/install/updating). If a consistent recovery set cannot be restored, leave the affected service stopped and report what remains unavailable; do not force older code to start.

**Rollback triggers:** any failed canary, a protected-field config diff, a plist regression that the skill procedure cannot restore, loss of FDA, or an unexpected outbound action.

---

## 9. Commit and deployment sequence

1. **Now (plan only).**
   - Add this file.
   - Add a link from `openclaw/plans/dependency-maintenance.md`, with a note that its older outline is **superseded for this upgrade**. That outline includes raw `paired.json` printing, `/tmp` backups, a floating "latest" install, and a live cron rerun. Do not reuse those steps.
   - Codex reviews the staged diff, then commit.
   - Push to `main` together with the two already-reviewed local commits, as the user authorized.
   - Expected runtime effect of this plan commit: none. The wrapper hash is unchanged. The deployment effects of the two earlier commits are covered by their own reviews.
2. **Phase A branch** (worktree): runtime edits and test updates. Codex review. Merge only in the approved window (§5).
3. **Plugin compatibility commit**, if needed. Review/build it in isolation; activate only with the matching host in Phase B. Do not merge a target-only plugin into an auto-deployed live checkout running the old host.
4. **Phase B.** Expect review of tracked configuration, official-plugin pins/channel intent, custom-plugin lockfiles and schema-aware readers. Any required changes get focused commits and coordinated activation. Do not assume the core package update is documentation-only.
5. **Post-acceptance docs:** update `LAUNCHAGENTS.md` and `dependency-maintenance.md` with actual versions, runtime ownership, test evidence, unresolved issues and recovery locations. Record any remaining general shell/Node18 ownership cleanup separately; OpenClaw's own controlled command paths must already pass.

---

## 10. Go/no-go acceptance and future approvals

**Accepted when all of the following hold:**
- The gateway runs `2026.10.1` on the pinned official Node24 runtime, with one active global OpenClaw install. Downloaded tarballs pass their pinned registry integrity checks; the installed code/dependency tree matches the recorded manifest from the reviewed staged build, including authorized lifecycle/native-build outputs. Do not compare a tarball digest directly to a directory hash or create a post-install manifest and call that independent verification.
- All required §7 canaries pass; any optional messaging test is explicitly recorded as approved/passed or not run.
- No new relevant test failures remain; any baseline failures are documented with their impact on confidence, and none invalidate a changed contract.
- Protected config fields are unchanged.
- FDA and the imsg bridge are intact.
- Finance and briefing jobs fire at their next scheduled times, observed and not forced.
- `dotfiles-pull` is released with a matching receipt and reviewed source convergence: the live checkout matches the approved published `origin/main` commit for the accepted or rolled-back cohort, `git status --porcelain` is empty, and the deployed pull copy matches the reviewed source. No pending source change would reactivate the rejected wrapper/plugin cohort; verify without running the full deployer. Preserve and reconcile unrelated local work rather than cleaning it to satisfy this gate.
- The backup set and manifest are retained.

**Execution approval boundaries**
- Approve one scoped A package: reviewed side-by-side Node transaction, narrow deployment hold, backup/restore drill, runtime activation/restart, bounded synthetic checks including the one no-delivery scheduler fixture, and the predefined rollback including its source publication. Agree stop/soak deadlines and operator availability first.
- Approve B only after A acceptance: exact package/plugin cohort, verified migrations, narrowly scoped writer/channel/scheduler holds, synthetic checks, state-aware rollback and any explicitly named one-message test. No production-job reruns.
- Pause for deviations: changed release target, required bridge, broader shared-library changes, new plugin/security consent, auth/FDA sign-in, or recovery beyond the approved recipe. A preapproved rollback need not wait for a fresh prompt during an outage.
- Removal of Node18/22, old backups, unrelated upgrades or changes to household policy remains outside this plan.

---

## 11. Codex review disposition

Reviewed against tracked sources, installed 2026.8.35 CLI help and current official
release/runtime documentation. This is a planning review, not proof of a successful
migration or a tested live rollback.

Corrections to the Opus draft:
- Kept OpenClaw-controlled noninteractive runtime selection in scope while avoiding
  a global PATH rewrite or an all-disk inventory.
- Added the actual FDA app bundle, installed dependency/plugin trees, external
  agent roots and separate A/B recovery points to backup coverage.
- Distinguished supported SQLite snapshots from raw WAL copies, and required a
  restore drill rather than checksum-only assurance.
- Strengthened rehearsal isolation beyond HOME/port changes; protected production
  credentials, secret-provider hooks, channels and scheduler from candidate code.
- Put gateway shutdown before symlinked-wrapper activation; made the activation
  receipt health-gated and prevented premature target-only plugin deployment.
- Added explicit stable-channel intent, old-updater/target-plugin compatibility,
  actual npm prefix ownership and tarball-vs-installed-tree verification.
- Added synthetic scheduler execution, memory/dashboard checks and post-rollback
  delivery/dedupe reconciliation; preserved rotating auth authorities.
- Replaced checkmark-looking test results with future requirements and grouped
  approvals by phase, including a preapproved bounded recovery recipe.

Amendments following the first Fable 5.1 review and source cross-check:
- Made the launchd maintenance hold explicit: supported stop/bootout, verified
  quiescence and controlled bootstrap, including unchanged plists.
- Required the Phase B updater's `--no-restart` and checks for unintended starts;
  applied startup queue/replay containment to Phase A and rollback as well.
- Specified absolute operator CLI invocation with a command-scoped child PATH,
  rather than promising that arbitrary shells select Node24.
- Preserved the active-gateway skill gate before receipt publication and required
  fail-closed regression coverage, without running the whole deployer.
- Separated tarball integrity from installed-tree/build-manifest acceptance.

The second Fable review cleared those five topics. Subsequent source checks
confirmed shared-CLI consumers with their own Node22 PATH, so their bounded
inventory, invocation tests and coordinated deployment are now a Phase B gate.
Rollback release now also requires convergence with the deployer's published
source; updater-internal npm ownership must be proved before mutation. These
are plan requirements, not claims that the runtime changes have been tested.

**Updated execution status, 2026-10-11:** the approved official runtime replaces
Homebrew, isolated runtime/SQLite/native probes passed, the narrow receipt verifier
is implemented, and the read-only Node22 live baseline passed. The [Phase A
procedure](node24-phase-a-cutover.md#attended-maintenance-proposal) records the
remaining startup-containment, writer/hold, complete checkpoint/restore, timing,
review and candidate live-canary gates. Budget is approved; full restoration and
its duration are not verified. Phase B admission/migrations and target SDK/plugin
compatibility remain separate outstanding work. Document publication is not
evidence of execution or permission to activate.
