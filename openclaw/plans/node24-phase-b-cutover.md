# Phase B: OpenClaw stable cutover record

Status: ACCEPTED 2026-10-11. Production runs OpenClaw2026.10.1 on official
Node24.21.0. The [reviewed plan](node24-openclaw-stable-upgrade.md) controls rollout.

## Accepted production cutover

- Phase A completed its 30-minute soak before B. The gateway was drained and
  unloaded; its process tree exited. Only the deployer and two approved camera
  reviewers were held. The nine stopped-work counters were zero.
- Fresh pre-B checkpoint: 126,808 exact manifest entries, 67 SQLite integrity
  checks, and copied old-package preflight under both Node22 and Node24 passed.
  Archive SHA256: `9bc572218de2fc0ab497470d9137f5c6e51163b3b1fbcf14f1aeb9f62076de3a`.
  The first restored-copy verifier opened SQLite before finishing its header
  inventory; SQLite removed a temporary SHM sidecar, interrupting verification.
  An eager inventory fix and permanent regression passed. A new extraction of
  the unchanged archive then passed exact manifest and integrity verification.
- Source and the nine-file CLI cohort converged at `b3fca60` before package
  mutation. The supported updater selected stable and installed exactly2026.10.1
  with `--no-restart`; installation, atomic swap and Doctor all passed. A bounded
  watcher verified the gateway stayed unloaded and port18789 remained unbound.
- All 13,203 installed published regular files matched the integrity-verified
  release. The sole absent archive file, `.openclaw-lifecycle-pending`, is
  intentionally removed by the verified official postinstall lifecycle contract.
  Dependency files are outside this published-file comparison.
- Codex and iMessage actually loaded at2026.10.1, with exact approved manifests,
  no compatibility findings, and no blanket capability approval. Fresh config
  validation and all five post-upgrade plugin probes passed.
- Shared schema20 and agent schema24 passed integrity checks. Against the fresh
  pre-B checkpoint, 587 session nodes, 12,609 transcript events/identities and
  17 conversations were preserved before startup. Protected config, skill policy,
  credentials, launch files, Astra primary and Opus fallback remained unchanged;
  only the documented schema rewrites and approved Luna compatibility addition
  changed model/config structure. The old mini alias remains on its original model.
- Fifteen original cron definitions were preserved exactly. The target retires
  the old internal `skillCollectionReview` payload and recreates that one
  system-owned monitor as an isolated, no-delivery agent turn. Its enablement,
  weekly schedule and ownership were preserved; prompt/tools match the target's
  published contract. After startup there are again16 jobs. The actual tracked
  cron read-back parser passed against live RPC and SQLite, with this explicit
  system-job migration accounted for; no operator-authored job changed.
- One controlled bootstrap restored normal approved recovery. Executable mapping,
  pinned package/runtime, generated launch chain, health, listener ownership and
  the live required-skill gate passed before activation-receipt publication.
- The disabled future synthetic cron ran once on Astra, read its fixture,
  produced the expected terminal response without delivery, and was removed.
  A separate no-delivery native canary confirmed Codex harness/Astra routing and
  no tool failures. No production cron was manually rerun or message resent.
- iMessage's native bridge/FDA probe passed without sending a message. Memory's
  embedding probe and Node24's in-process SQLite probe passed. Reachy's target
  SDK tests/build passed; its live gateway method rejected intentionally invalid
  input before accessing the store, proving host registration without reading
  private context or changing the capsule. Its existing operator-managed origin
  warning remains, not a load failure.
- The usage dashboard serves HTTP200 and its gateway-usage API responds; its
  session cache rebuild is asynchronous. Both reviewers returned to their prior
  running states, their queues were preserved, and the deployer returned loaded
  but idle. Deployed cohort hashes match source. No unrelated service was restarted.
- The prior Weekly Financial Data Scrape and Julia Flower Bloom Watch errors
  have identical last-run timestamps/errors to the pre-B checkpoint. These are
  separate maintenance issues, not new upgrade failures. No real messaging
  round-trip or camera event was generated as a canary.

Protected execution evidence: `~/Backups/openclaw/phase-b-20261011T2215Z`.
The fresh pre-B archive is named `pre-a.tar` inside that distinct Phase B directory
because the checkpoint helper was reused; it contains the pre-B state, not A's
older checkpoint. Total measured backup usage was about46.3GiB, below the approved
64GiB budget. Keep both phase recovery sets and Node22 until at least30 days after
acceptance; do not automatically delete them or launch old code against migrated
state. Acceptance covers these bounded checks, not a full future overnight cycle.

See the [October11 post-upgrade review](post-upgrade-review-2026-10-11.md) for
subsequent Claude CLI/authentication and permission checks, the remaining plugin
provenance warning, and the private cleanup register. November11 is a review
date only; the older full-state backup is not superseded by these scoped sets.

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
  post-upgrade plugin probes pass with no findings. Codex loads at2026.10.1;
  iMessage2026.10.1 remains intentionally disabled by the isolated channel config.
  Its live loaded-path/FDA check is still required. This fixture correction is
  not a production permission weakening.
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
- These preparation results preceded production mutation; the accepted execution
  and its fresh checkpoint are recorded above.

Protected evidence lives under `~/Backups/openclaw/phase-b-preflight-20261011`.
Do not commit copied config, databases, authentication material or private logs.
