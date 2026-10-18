# Post-upgrade review — October 11, 2026

Operational follow-up to the accepted [Node cutover](node24-phase-a-cutover.md)
and [OpenClaw cutover](node24-phase-b-cutover.md). This record documents bounded
checks, not a claim that every household job or future overnight cycle passed.

## Accepted state

- Gateway and its migrated CLI consumers use the pinned official Node24.21.0
  runtime with OpenClaw2026.10.1. The separate enterprise OAuth refresher still
  retains Node22; neither that runtime nor its dependencies are cleanup targets.
- Official Codex and iMessage plugins are pinned at2026.10.1. The registry's
  stable versions matched at review time; no additional plugin update was needed.
- Model defaults remain Astra with medium thinking and the sole Opus5.5 fallback
  with high thinking. The approved Luna compatibility entry is not a new default.
- Claude CLI was separately upgraded from2.1.76 to the stable-tag release2.1.287,
  not the newer latest/next-tag release. Its installed executable is native,
  signed by Anthropic; Node was used for package installation, not CLI execution.
  The pre-existing alternate CLI installation and disabled auto-updater remain
  unchanged.

## Authentication and permissions

- Attended personal Claude enterprise sign-in completed through Prisma Access
  Browser on `dylans-mac`. The Mini's GUI CLI reported enterprise authentication
  and passed a tool-free Fable request. Temporary login forwarding was closed.
- The separate automation credentials were unchanged. A tool-free request with
  the new CLI and the existing automation access token passed; this does not
  establish that its next scheduled refresh-token rotation will succeed.
- Read-only checks found the GUI Aqua session's login Keychain unlocked, while
  the remote/background security session's Keychain remained locked. A remote
  CLI reporting signed-out does not contradict the successful GUI login.
  Full Disk Access does not resolve that separate Keychain-session boundary.
- Node24 passed bounded loopback/LAN TCP and Messages database-header reads.
  The gateway's native iMessage bridge/FDA probe passed. A bounded current-log
  check found no matching permission denials, not proof of every macOS permission.
- No blanket Keychain trust, TCC copying, or extra permissions were granted.
  Accessibility, Screen Recording, and Apple Events should be tested only when
  an actual attended workflow needs them. Keep personal and automation refresh
  authorities separate; never copy a rotating personal refresh token into the
  automation cache.

## Plugin follow-up

The plugin Doctor returned a nonzero result for the existing
`reachy-continuity`0.5.0 missing-installation-provenance warning. It reported no
plugin load errors or compatibility failures. Target-SDK tests, build, and the
bounded gateway registration check passed during cutover; provenance is still
unresolved. Verify the tracked source and supported installation record before
repairing it. Do not suppress the warning with broader capability trust or an
unverified package install.

## Cleanup register and retention

The private, owner-only register is on the Mini at
`~/Backups/openclaw-cleanup-20261011/INDEX.txt`, alongside JSON/CSV inventory and
validation receipts. Keep those inventories, authentication artifacts, raw logs,
and backup contents out of Git. Held backups remain at their original paths to
avoid breaking recovery manifests or absolute references.

**Reconciled October 17:** The subsequently approved October 11 cleanup removed
all 15 catalogued staging/review candidates after fresh identity, process,
open-file, reference, Git, and retained recovery-archive checks. The private
`cleanup-execution-20261011.json` records the operations; all 15 paths were
confirmed absent again on October 17. Their prior allocated footprint was
597,602,304 bytes (about570MiB); actual reclaimed APFS space was not measured.
The original JSON/CSV inventory is a historical baseline, not a pending deletion
queue. No held backups, active runtimes, authentication stores, or running
services were removed by that batch.

| Classification | Measured size | Disposition |
| --- | --- | --- |
| October11 recovery/preflight sets | 46.33GiB | Retain; no automatic deletion |
| Older upgrade/rollout sets | 18.08GiB | Coverage review hold |
| Completed staging/review cleanup (15 entries) | About570MiB before removal | Removed October11 with approval; absence rechecked October17 |
| Previous Claude CLI package archive | About72MiB | Separate recovery hold |

Sizes above are the October11 inventory measurements, not a fresh disk-usage
survey. The merged review worktree was removed through Git without force; its
branch was retained. Only the catalogued missing-worktree registration was
pruned.

November11 is the common **review date** for the October11 recovery sets, not a
deletion deadline or scheduled cleanup. Keep those recovery sets at least30 days
after acceptance. The older 2026.8.35 full-state archive is not proven redundant:
newer scoped gateway checkpoints deliberately exclude unrelated
household/financial state and older
backups. Map that coverage before considering its removal. Active runtimes,
plugin generations, credentials, locks, and unrelated backups remain protected.
Any further cleanup requires fresh checks and exact-path approval.

The October17 home-event, OAuth, and deployment repair backups are separate from
this inventory. Retain them while scheduled verification finishes; see the
[maintenance repair record](maintenance-repairs-2026-10.md) for their rollback
locations and acceptance status. Recent temporary diagnostics have not been
approved for removal.

## Remaining checks

- [ ] Observe the next scheduled enterprise OAuth refresh with Claude2.1.287;
  confirm durable cache publication without exposing credentials.
- [ ] Perform an attended real iMessage round trip. Healthy native components
  and a responding dashboard do not establish delivery latency.
- [x] Repair BoA logout-page handling and authenticated-session recognition on
  October18 (`1089aa0` in dotfiles; `6aa0ec9` in financial-dashboard). Complete
  attended BoA-only scrape/import recovery and a subsequent normal direct-HTTP
  scrape/import; confirm the protected weekly status is healthy for all seven
  sources, with the other six source results preserved rather than rerun.
- [ ] Observe the next scheduled weekly financial scrape; attended recovery does
  not establish scheduled-run acceptance or reset the scheduler's failure history.
- [ ] Observe the next normal overnight cron cycle; keep the pre-existing
  flower-watch failure and financial scheduled-run verification separate from
  upgrade acceptance.
- [ ] Resolve Reachy's installation provenance through its supported workflow.
- [x] Complete the approved October11 staging/review cleanup; reconcile its
  execution receipt and recheck all 15 paths on October17.
- [ ] Review retained October11 backups and replacement coverage on or after
  November11; obtain exact-path approval before deletion. No automatic cleanup
  is scheduled.

The post-upgrade audits did not restart the gateway or alter model defaults,
household policies, cron schedules, or production delivery.
