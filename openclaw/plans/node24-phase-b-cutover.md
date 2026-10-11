# Phase B: OpenClaw stable cutover record

Status: preparation only; production remains OpenClaw2026.8.35 on accepted
Node24.21.0. The [reviewed plan](node24-openclaw-stable-upgrade.md) controls rollout.

## Approved scope

- Exact target: OpenClaw2026.10.1, official Codex2026.10.1 and iMessage2026.10.1.
- Capability consent applies only to those two pinned plugins; preserve tool
  restrictions, model routing, accounts, household policy and delivery recipients.
- Separately approved compatibility exception: add `openai/gpt-5.6-luna` alongside
  the existing mini model entry and allowlist, retaining inherited settings.
  Astra primary and Opus fallback stay unchanged; no other model-policy expansion.
- Normal startup recovery follows verified drain, fresh stopped backup,
  migration checks and pending-work review. No manual production reruns/resends.
- Briefly hold the long-running Nest activity and Cabin entry reviewers; retain
  queued work and restore their prior service states. Reload only the usage
  dashboard after its runtime-path deployment. No camera/notification canaries.
- Keep the September splat artifacts and unrelated services untouched.

## Shared CLI invocation cohort

All confirmed invokers keep `/opt/homebrew/bin/openclaw` as their package owner
and prepend the official versioned Node24 bin directory, not Homebrew's ambient
Node. Changing the gateway's PATH alone does not repair these child environments.

| Source | Owner / activation | Validation |
| --- | --- | --- |
| `sync-cron-jobs.sh` | Scheduled deployer; source update while held | Synthetic shell fixtures; never run synchronizer as a probe |
| `home-event-camera.py` and wrapper | Periodic camera worker; next invocation loads new files | Mocked child environment; no camera events |
| `home-event-delivery.py` and wrapper | Periodic delivery worker; next invocation loads new files | Mocked sender environment; no notifications |
| `nest-activity-reviewer.py` and wrapper | Long-running reviewer; scoped hold/reload required | Runtime environment regression; queue retained |
| `cabin-entry-verifier-wrapper.sh` | Long-running verifier inherits reviewer environment; scoped hold/reload required | Wrapper contract and inherited runtime |
| `usage-dashboard.py` | Long-running dashboard8551; targeted restart required | Mocked RPC environment and loopback health |

The enterprise Claude OAuth refresh remains on Node22. Native imsg, Python-only
state workers and unrelated npx/PinchTab consumers are not swept into this change.

## Preparation evidence

- Target core archive integrity matches the exact npm release metadata.
- Custom Reachy plugin: 29 tests and TypeScript build pass against2026.10.1 SDK
  in isolation; no production plugin code changed.
- Helper regression suites: 130 tests and synthetic cron-sync shell suite pass;
  Python/shell syntax, repository validation and diff checks pass.
- Copied shared DB requires schema15 to20; copied agent store requires19 to24.
  These are forward migrations, not an in-place rollback-compatible update.
- Offline rehearsal preserves the recovery archive and all schema-bearing
  tables. Disposable copies have inert credentials, relocated paths, disabled
  cron/channels, denied network/production files and denied service-control exec.
  Early isolated attempts exposed path/exec restrictions; they are not acceptance.
- The updater's own resolver selects `/opt/homebrew/lib/node_modules/openclaw`,
  npm11.17.0 and prefix `/opt/homebrew`, with explicit Node24 child runtime and
  `--allow-scripts=openclaw`. The exact old-to-new isolated package swap and
  candidate Doctor passed without service activation.
- Copied-state first hop completed core/Doctor but correctly refused an inert
  secret-provider fixture owned by root. A current-user-owned inert replacement
  restored the real ownership contract; scoped consent and supported
  `update repair --no-restart` then passed. Fresh config validation and the
  post-upgrade plugin probes pass with no findings; both approved plugins load
  at2026.10.1. This fixture correction is not a production permission weakening.
- The migration preserves 588 session nodes, 12,609 transcript events and
  conversation role coverage. Both migrated DB integrity checks pass. The actual
  tracked cron read-back parser verifies the original 16 definitions against the
  migrated DB and a baseline response fixture; live target RPC parity remains a
  separate acceptance gate. Skill policy is unchanged in the updater rehearsal.
- Rehearsal permits Unix IPC only inside its private root for the target's child
  broker. Explicit probes still deny production config, launchctl and TCP access.
  A separate exploratory standalone Doctor disabled unavailable skills only in
  its disposable copy; do not transplant that config or run broad standalone
  `doctor --fix` as a production canary.
- Final fresh checkpoint, production convergence and live acceptance remain
  required. No Phase B production mutation has occurred.

Protected evidence lives under `~/Backups/openclaw/phase-b-preflight-20261011`.
Do not commit copied config, databases, authentication material or private logs.
