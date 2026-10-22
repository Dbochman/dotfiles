# Dependency maintenance plan (Mini + Crosstown MBP)

**Status:** Active reference. Last general audit 2026-04-29; targeted TLS incident response 2026-10-20.
**Owner:** Dylan
**Hosts in scope:** `dylans-mac-mini` (gateway, all cron jobs, briefings),
`dylans-macbook-pro` (Crosstown presence scanner).

This doc is a living maintenance checklist for the two macOS hosts that
run our home automation. Use it when:

- Doing a regular dependency audit (suggested cadence: monthly)
- Investigating a regression that might be a stale dependency
- Onboarding a third host into the fleet (extend the matrix below)

The goal is to keep dependencies fresh enough that security CVEs and
bug fixes flow through, while protecting load-bearing integrations
from breaking changes that need careful migration.

## Node security rollout — 2026-10-21 (deployed, with explicit gaps)

### October 22 fleet follow-up (in progress)

- The Mini retirement receipt confirms both old conversation processes exited
  and four standalone runtime observations met the patched floors. This
  supersedes the earlier pending Mini session retirement below, not the
  application-bundled/provider gaps.
- Windows main is reachable through the trusted WSL maintenance bridge. A
  Windows-native survey observed Windows 11 Home 26H2, build 26300.9711,
  Defender platform 4.18.26090.9 / engine 1.1.26090.9, enabled antivirus and
  real-time protection, and no CBS reboot-pending flag. An attended Windows
  Update scan succeeded with no pending software updates. Defender versions
  exceed the respective CNA thresholds for CVE-2026-33825 and CVE-2026-41091;
  this is not clearance for all Windows findings or the offline cabin desktop.
- Windows OpenAI.Codex 26.1016.8120.0 still has four active managed Node helpers
  reporting file versions 24.19.0 / 24.21.0. The Store offered no newer package.
  **Owner explicitly chose to keep it running and document the gap.** No app
  restart, session termination, signed-content replacement, or security-policy
  change was performed. OpenAI's supported update channel, not a replacement
  of its cached runtime binaries, remains the repair path. This is an accepted
  unresolved gap, not a mitigation or security clearance. Recheck vendor updates
  and verify the actual helper versions against the patched Node 24.22.1 floor
  after an owner-coordinated update; an app restart alone does not close it.
- Retired the remaining inactive CurseForge 1.321.1-39714 installation on
  dylans-mac under the existing retirement decision. No launch-definition
  references were found. Unregistered its exact installed path and moved its
  approximately 604 MB signed app bundle to owner-only recoverable Trash at
  `~/.Trash/retired-CurseForge-20261022.Dwf1xl/CurseForge.app`. Strict deep
  signatures passed before and after; mods, settings and user data untouched.
  Retained binaries are not safe to relaunch; no disk reclamation is claimed.
- Reachy is reachable through Crosstown MBP, not the Mini's direct subnet path.
  Its running kernel is 6.12.62+rpt-rpi-v8. Refreshed signed APT metadata offers
  Raspberry Pi 6.18.50-1+rpt1. A no-removal dry run selected 5 upgrades and 12
  new kernel/header/support packages, leaving 313 unrelated upgrades alone.
  Downloaded 80.5 MB; installation would add about 137 MB. **Downloaded only:**
  install/reboot and post-boot robot checks await confirmation that the owner's
  Crosstown macOS update has finished. No robot motion/audio command was sent.
- The current advisory inventory is
  `~/.local/state/security-inventory/2026-10-22-fleet-followup/`, retaining older
  snapshots and observation times. Windows metadata and refreshed dylans-mac
  evidence are included; the Windows snapshot does not inventory WSL. Original
  surveys remain intact. This inventory does not override the owner's accepted
  Windows runtime gap or automatically close any advisory.
- The additional advisory run starting at 16:12 UTC screened 585 advisories
  and completed 10 verification reviews before the overall collection deadline.
  It failed without publishing those partial results; the previous completed
  cache remains available with its 29-item verification backlog and a latest-run
  failure warning. That run used the earlier inventory, not the expanded Windows
  survey. The resumability repair now checkpoints validated batches and successful
  reviews separately from the published cache, invalidates changed/expired work,
  and stops starting new work at 24 minutes within the unchanged 30-minute hard
  limit. Deployed the repair and passed 81 security/briefing tests. A controlled
  live interruption after the first 50 advisories left the published cache
  byte-for-byte unchanged and a 0600 checkpoint intact. The LaunchAgent restart
  at 17:25 UTC reused all 50 decisions against the now-717-advisory feed, and is
  continuing the remaining discovery. The exercise also exposed slow model-pipe
  cleanup; that cleanup now has bounded waits and retains the original deadline
  exception. The 07:15 schedule and no-KeepAlive contract are unchanged. Full-feed
  completion remains a live verification item rather than a claimed result.
- Subsequent discovery reached 639/719 before a model-call timeout. Owner then
  authorized collector maintenance and capped unattended continuation. Discovery
  batches are now 20 instead of 50, with a two-attempt maximum for model timeouts
  (shared with discovery schema correction). The daily job uses a persisted
  three-pass allowance per local 07:15 window and 60-second backoff; it stops on
  no progress, non-timeout failures, operator stop, or cap. Each pass retains the
  30-minute deadline and twelve-review maximum. Deployed only this collector and
  its LaunchAgent; retained the 639-decision checkpoint and prior published cache.
  All 96 security/briefing tests pass. The live continuation supervisor started
  at 20:02 UTC with allowance 1/3 and reused 617 still-valid discovery decisions;
  the refreshed feed contains 719 advisories, leaving 102 to screen at startup.
  Remaining discovery and primary-source verification are not yet claimed complete.
- GitHub runner source metadata identifies a patched release candidate, but
  attended hosted validation has not produced new runtime evidence. The guarded
  rerun remained queued; cancellation returned an inconsistent completed-run
  response while the read API still reported queued. Restored PR Checks to
  disabled; no other held workflow was enabled and no merge was performed.

Sources: [Microsoft Defender platform CNA record](https://cveawg.mitre.org/api/cve/CVE-2026-33825),
[engine CNA record](https://cveawg.mitre.org/api/cve/CVE-2026-41091),
[Raspberry Pi maintainer's Copy Fail fix confirmation](https://github.com/raspberrypi/linux/issues/7346),
[IPv6 issue](https://security-tracker.debian.org/tracker/CVE-2026-53362),
[OpenAI Windows deployment/update channels](https://learn.chatgpt.com/docs/enterprise/windows-deployment).

### October 22 advisory follow-up: standalone runtimes

The inventory-backed advisory review found leftovers outside the already-patched
automation services. The attended follow-up:

- Removed Homebrew Node 22.23.1 from the Mini, 25.9.0 from Crosstown MBP, and
  26.7.0 from dylans-mac. Homebrew reported no installed dependents and also
  autoremoved dependencies it classified as unused. Global npm application
  installs and user/project data were not deleted. Reinstallation is possible
  through Homebrew, but do not restore an affected release.
- Retained but disabled execution of the Mini's root-owned `/usr/local/bin/node`
  18.16.0 (owner ran the sudo chmod) and the standalone 24.21.0 rollback binary.
  Both are mode 0644, not runnable rollback options. Recovery archives remain
  untouched; disabling/removing disk files does not repair an existing process.
- Added the shared tracked `node-runtime.zsh` selector. New interactive, login,
  and noninteractive shells select 24.22.1 on Mini/MBP and 26.12.1 on dylans-mac.
  Mini `.zprofile`/`.zshenv` source the checkout; remote startup files source its
  scoped deployed copy at `~/.local/libexec/node-runtime.zsh`. Original Python,
  NVM and Homebrew setup is preserved. When provisioning/rebuilding a host, put
  this source line **after** its existing PATH setup in `.zprofile` and `.zshenv`;
  `zshrc` sources the same selector after Homebrew. No full dotfiles install or
  remote checkout fast-forward was performed.
- Stopped the detached Crosstown Codex app-server (no active TCP clients or
  child work) and dylans-mac's Codex terminal after verifying its latest task
  completed October 18 and its session transcript was saved. The Mini's remaining
  old launcher belongs to this very remediation conversation: checkpoint first,
  then retire it with an identity-checked one-shot helper and resurvey the Mini.
  Until that receipt confirms process exit, this is **pending**, not clearance.
- Gateway health and magazine API health pass. Home dashboard/finance respond
  200; Forecast redirects to its existing UI. Both Crosstown bridge listeners
  still respond with their expected authentication requirement, and their PIDs
  remain mapped to patched 24.22.1. Robot MQTT connectivity is not retested or
  declared repaired. New Codex version smoke checks pass on both remote Macs.

Private refreshed evidence is under
`~/.local/state/security-inventory/2026-10-22-node-remediation/`; the advisory
LaunchAgent points there. Original survey evidence is retained. A removed but
still-mapped Node binary remains a running/unknown inventory observation, while
a disabled, unmapped binary is explicitly non-executable. The final one-shot
receipt lives under `~/.local/state/node-security-20261022/`; it must be checked
after resuming. The notification-only collector prioritizes rechecking changed
actionable findings before its discovery backlog and never closes an advisory
just because a repair command exited zero. Application-bundled, offline Windows
and provider-owned runtimes still require their separate evidence.

### Attended activation result (October 21 evening)

- Published runtime commits `cdf0b26`, `d64e458`, `a5470c4` to main and
  narrowly deployed the Mini's affected helpers/LaunchAgents. Preserved local
  `CCUSAGE_LOCAL_DIR` and `HOME_EVENTS_RING_ENABLED` overrides; no broad installer,
  cron sync, credential rotation, household-policy change or state rewind.
- Gateway drained with zero active work. The stopped recovery archive restored
  with 68,470 manifest entries matching and 18 databases passing integrity checks;
  all stopped-work counters were zero. Protected recovery set:
  `~/Backups/openclaw/node-security-20261022T0025Z`, about 2.2 GB archive plus restore.
  Scope is core state/agents, service definitions/helpers and unchanged gateway
  package/runtime—not a full-machine backup. Existing archives retained.
- Gateway PID 6434 maps to official 24.22.1; OpenClaw stays 2026.10.1. The generated
  live plist hash is unchanged. Health and authenticated required-skill checks
  pass, activation receipt recorded, deployer hold released. The first launch
  occurred in the wrapper-write second and failed the strict age check; a native
  graceful restart preserving the definition established fresh process evidence.
- Magazine PID 6285 maps to 22.23.4 and `/api/health` returns `ok:true`. Usage/home
  dashboard HTTP endpoints respond 200; usage service API passes. Restarted the
  affected persistent dashboard/reviewer owners. Ring/dog-walk owners reloaded
  only after verifying no active walk or return monitoring; local settings kept.
  Bounded camera/delivery helpers consume their new files on the next interval.
- Patched 24.22.1 with maintained CA settings rejects all 12 eMudhra chains and
  accepts both controls through both `https` and `fetch` (28 checks). No TLS
  verification bypass or exploit payload used.
- Crosstown bridges map to 24.22.1; authenticated `/api/info/version` confirms it
  for both services. Remote Roomba and August CLIs now explicitly choose that
  runtime. New Mini shells select 24.22.1; the MBP's existing user-local bin PATH
  selects a new `~/.local/bin/node` symlink to the verified runtime. Homebrew
  binaries remain untouched; the MBP's much older source checkout was not broadly
  fast-forwarded. Existing Codex-owned Node 22/25 processes still need session
  restart; changing a shell does not replace an already-running executable.
- **Robot connectivity is not cleared:** mission-status requests still time out.
  A temporary observer on an existing bridge's TLS sockets reported
  `EHOSTUNREACH` without logging credentials or changing TLS policy. It was removed
  and the tracked wrapper restored. Subsequent TCP reachability varied by robot;
  do not call listener/auth-version success proof of robot MQTT health. No robot
  mission/control command was sent and no TLS policy weakened to reconnect.
- **Third Mac:** patched 26.12.1 staged, then `dylans-mac` became unreachable.
  Existing 26.7.0 process/default selection has not been replaced. Wake/finish
  request sent to the owner.
- **Website:** [PR #339](https://github.com/Dbochman/personal-website/pull/339)
  is pushed, not merged. Hosted CI proved the runner-owned action Node is 24.21.0;
  the first guard correctly blocked it before checkout. All 13 repository-owned
  Actions workflows are temporarily `disabled_manually` to also contain main's
  unguarded scheduled jobs. Prior states are recorded in the protected recovery
  directory's `website-workflow-hold.json`. Static production remains online;
  analytics/review/build/deploy freshness is paused. Re-enable only after the
  guarded workflow and patched runner are verified. GitHub-managed Dependabot
  and external Cloudflare/Netlify/Vercel execution are outside that workflow hold.
- **Vendor gap:** Windows Codex managed Node 24.19/24.21, 1Password's bundled
  runtime and other provider-managed execution still require vendor evidence
  and launched-process verification. No signed/vendor-managed binary was patched
  in place. Offline devices remain unverified.

The preparation inventory below records the pre-activation state, not the current
running Mini/MBP service versions.

The [October security release](https://nodejs.org/en/blog/vulnerability/october-2026-security-releases)
fixes CVE-2026-111497 (Brotli decoder out-of-bounds write / RCE), TLS hostname
validation flaws and other issues. Supported patched targets: 22.23.4, 24.22.1,
26.12.1. Node 25 is EOL. Trust-store fixes alone do not repair the decoder.
No exploit was run and this work does not establish compromise.

Official macOS ARM64 archives downloaded over verified TLS and compared against
Node's release SHASUMS256.txt:

| Target | Archive SHA-256 |
| --- | --- |
| 22.23.4 | `c9e1dbc748b33ed2d14f42445191ecaf14c56259c7a9a26d0042c49ec2b66428` |
| 24.22.1 | `89c682593d76cd44233fa53b1563264e7de9cbb189cad169135c5b4fadfbc0a7` |
| 26.12.1 | `1901d539912c02afa10d1cc126943a8855a7eedebe830119463470db5e6c59e8` |

These are HTTPS checksum checks, not independent release-signature verification.
Node 24's extracted executable SHA-256 is
`6f32069480ec14d52b6d7b1f3c7124cfbbc46203abf5ddf5c65b127435f3f329`.

- **Mini:** 22.23.4 and 24.22.1 staged side by side in
  `~/.local/share/openclaw/runtimes/`. Gateway remains running 24.21.0,
  OpenClaw 2026.10.1; the existing live activation verifier passes. Source pins,
  checksums, helper PATHs and LaunchAgents are prepared in the isolated
  `fix/node-october-security` worktree. No live wrapper change or restart yet.
  Preserve the maintained CA bundle and OpenSSL-CA settings.
- **Crosstown MBP:** 24.22.1 staged and Express/dorita980 module loading passes.
  Both bridges still run Homebrew 25.9.0. Prepared wrappers require the exact
  patched runtime/hash and cannot fall back to EOL Node. Deploy only after the
  runtime exists; verify each replacement PID and read-only `/ping` response.
- **dylans-mac:** active standalone Node is 26.7.0; Homebrew reports 26.12.0,
  which is insufficient. Official 26.12.1 is staged and its version verified
  under `~/.local/share/openclaw/runtimes/`; staging is separate from switching
  consumers. No claim that existing processes have been patched.
- **Windows:** installed OpenAI.Codex 26.1016.6573.0 still has active managed
  Node 24.19.0 and 24.21.0 binaries. Use vendor update channels; do not overwrite
  content-addressed runtimes or signed package contents. Runtime verification
  after a vendor update remains open. The offline cabin desktop is unverified.
- **Vendor applications:** 1Password's October 20 stable release notes do not
  establish a fix for the new Node advisory. Do not equate latest application
  version, Electron version, or unchanged Brotli version text with a backport.
- **Website:** separate feature branch pins builds/.nvmrc/Netlify to24.22.1 and
  adds an offline pre-checkout check of the actual Runner.Worker-owned action
  Node24. A lagging GitHub runner fails closed; no runner executable replacement.
  Hosted CI and provider-managed Vercel/Netlify execution remain unverified.

Local checks: 46 gateway, 30 deployment, 13 OAuth, 16 camera, 21 delivery,
38 usage, 37 Nest reviewer, 3 browser-trust, 61 Reolink, 31 Ring/home-event and
18 cron SQLite Python tests passed; synthetic cron-sync shell tests also pass.
Magazine:
51/52 pass on22.23.4; the catalog-tag fixture failure also occurs on old22.23.1.
Its existing issue/content changes are outside this rollout and preserved.
Website: 301 unit tests, typecheck, lint, preview build, hard bundle budgets,
11 bootstrap tests and generated-workflow drift check pass; blog style score100.
The existing soft content-chunk budget remains exceeded.

Activation procedure used: publish the reviewed source, hold the narrow
deployer, cooperatively drain the gateway, preserve the original generated
shell-prefixed launch chain and recovery artifacts, activate without changing
the OpenClaw package or rewinding state, then run the live activation/health/skill
checks before releasing the hold. Restart long-running helper owners only after
their work is idle; no test messages, robot missions, or production cron reruns.
Old runtime directories remain rollback-only until ownership is audited.

## eMudhra TLS incident response — 2026-10-20

This is client trust remediation, not evidence that a host was breached. Browser
revocation updates do not repair every independent Python/Node/curl CA bundle.
Sources: [researcher canaries](https://emudhra.we-have-a-problem.com/),
[certifi 2026.10.19](https://github.com/certifi/python-certifi/releases/tag/2026.10.19),
[Node's merged fix](https://github.com/nodejs/node/pull/66871), and
[Cloudflare's response](https://blog.cloudflare.com/emudhra-certificate-compromise/).
A merged Node patch is not proof that our installed runtime contains it.

### Applied and verified

- **Mini:** Homebrew `ca-certificates` 2026-10-20, `certifi` 2026.10.19,
  `uv` 0.13.4, and Go 1.27.3. No broad Python/OpenSSL/Node upgrades. Updated
  certifi only in Apple Python's user site and the finance, crypto, Nest events,
  Ring, Mysa, Roomba, and litter-robot environments. Finance now declares
  `certifi>=2026.10.19`. The existing uv package-age cutoff was retained; only
  certifi received an explicit `--exclude-newer-package` exception.
- The Mini's Homebrew CA post-install failed under brew and initially left the
  effective bundle stale. Running the installed upstream `libexec/post-install`
  helper directly with its packaged source and effective destination succeeded.
  Do not infer protection from a successful package upgrade alone.
- The operator replaced the Mini's root-owned `/private/etc/ssl/cert.pem` with
  the verified Homebrew bundle using `install -o root -g wheel -m 0644`.
  The files match. No Keychain trust settings were modified.
- Gateway remains on pinned official Node 24.21.0 / OpenClaw 2026.10.1.
  Its FDA wrapper requires `/opt/homebrew/etc/ca-certificates/cert.pem`, sets
  `SSL_CERT_FILE`, `CURL_CA_BUNDLE`, and `NODE_OPTIONS=--use-openssl-ca`, and
  unsets `NODE_EXTRA_CA_CERTS` **and** `NODE_USE_SYSTEM_CA` after cache loading.
  The generated service environment sets both; leaving the latter enabled
  caused conflicting Node flags during the first restart. Both are now cleared
  in the tracked wrapper, without editing the generated environment file.
- Clean-environment camera, delivery, and reviewer subprocesses explicitly keep
  the same TLS settings. Usage/OAuth helpers clear inherited alternate CA flags.
  Both tracked Nest CLI copies set the maintained curl/OpenSSL bundle. Both
  Nest listener wrapper branches set `GRPC_DEFAULT_SSL_ROOTS_FILE_PATH` as well
  as `SSL_CERT_FILE`; gRPC's independent roots must not be overlooked.
- Gateway was drained, restarted, and accepted by `gateway_activation.py` with
  live health, runtime, listener, required-skill, and wrapper checks. Activation
  receipt was recorded; the daily dotfiles deployer was restored. Nest, Ring,
  reviewers, and usage services were restarted to discard cached TLS contexts.
  Finance restart exposed a signal-handler deadlock; its separate repository
  now requests `HTTPServer.shutdown()` from another thread, with a data-free
  regression test. Its loopback baseline endpoint recovered with HTTP 200.
- **Crosstown MBP:** refreshed stale Homebrew metadata (including Homebrew's
  managed portable Ruby), then upgraded only `ca-certificates` from 2026-03-19
  to 2026-10-20. Backed up and replaced its root-owned Apple PEM bundle using
  available noninteractive sudo. Apple Python 3.9.6 and Homebrew Node 25.9.0
  now reject all 12 canaries and accept both normal controls. Presence remained
  healthy; scanner configuration and schedules were not changed.
- **dylans-mac:** owner accepted the Xcode license; Homebrew then upgraded only
  `ca-certificates` from 2026-08-13 to 2026-10-20. Apple Python 3.9.6,
  Homebrew Python 3.14.7, and Homebrew Node 26.7.0 reject all 12 canaries and
  accept both controls. Its existing Apple PEM bundle already passed; no sudo
  replacement was needed. Homebrew Python had no importable certifi package.

Owner-only rollback material (old bundles/packages/wrappers; not an instruction
to restore vulnerable trust):

- Mini: `/Users/dbochman/Backups/emudhra-mini-20261020.RNcs0y`
- Crosstown: `/Users/dbochman/Backups/emudhra-20261020.6b1Q4V`
- dylans-mac: `/Users/dylanbochman/Backups/emudhra-20261020.0ljTuR`

### Repeatable verification and remaining scope

Run the new credential-free probe with the application's actual interpreter:

```bash
/usr/bin/python3 ~/dotfiles/openclaw/bin/tls-revocation-probe.py
~/repos/financial-dashboard/venv/bin/python3 ~/dotfiles/openclaw/bin/tls-revocation-probe.py --certifi
```

It checks all 12 DV/OV/EV, G3B/G3C, direct/cross-signed canaries and two normal
controls. Exit 0 means expected trust rejection plus successful controls;
1 means a compromised issuer was accepted; 2 means inconclusive. Timeouts,
expiry, hostname failures, and a changed successful canary are not protection.
It sends no HTTP requests, credentials, events, or messages. Mini Apple Python
and finance certifi passed the full probe; maintained CA and mitigated Node 24
`https`/`fetch` also passed all 12. Other updated Mini Python environments passed
both chain-route samples plus a normal control. Ordinary API TLS handshakes
passed without authenticating or running financial jobs.

- Recheck after macOS updates: they may overwrite `/private/etc/ssl/cert.pem`.
  Keep the maintained Homebrew and environment-specific certifi stores current.
- A fresh process passing does not prove an already-running app discarded its
  cached roots. Relaunch interactive apps when convenient. Homebrew Node passed
  with its default trust on these hosts; standalone official Node 24 remains
  exposed without the wrapper settings. Do not remove the mitigation merely
  because a Node version command or browser test succeeds.
- Go toolchain updates do **not** rebuild existing Go binaries. The installed
  PinchTab binary was built with Go 1.26.2. The follow-up below tests its actual
  macOS client separately from Chrome; a compiler update is still not a binary
  rebuild. Electron/native/Rust applications,
  additional project venvs, employer-managed Macs, phones, and IoT firmware were
  not comprehensively audited.
- Home event bus core remained healthy. Overall health still lists the existing
  camera degradation and a Ring publisher warning after restart. Ring started
  and heartbeated without recent TLS errors; historical failed/quarantined
  counters remain preserved. Await a real post-restart publication before
  claiming complete Ring recovery; do not fabricate events or reset counters.
- Website ingress checks passed: main/www/API and Cloudflare preview use Google
  Trust Services; Netlify editor uses DigiCert. No affected leaf issuer was seen.
  Cloudflare reports provider-side mitigation, but this does not independently
  verify our Worker outbound connections. Website CI still uses official Node
  24 and JavaScript actions with independent trust. A separate, reviewed CI
  hardening change must protect outbound requests **before credential-bearing
  checkout/actions**, not merely set an extra CA file after setup-node. Machine
  fixes are being committed separately from the website CI/post PR; no website
  deployment or workflow dispatch was performed during this host remediation.

### Application follow-up — evening of 2026-10-20

- **Website:** PR #338 merged as `effaa165` after the owner approved two narrow
  dependency patches (Vite 7.3.7, follow-redirects 1.16.1). Hosted audit, TLS
  canaries, lint, typechecks, all 301 tests, full build, browser checks, and review
  passed. Production serves the retitled incident post. This supersedes the
  earlier paragraph's pending-CI status; no audit gate was bypassed.
- **Existing Go clients on the Mini:** fresh invocations of the installed
  PinchTab 0.11.0 (Go 1.26.2) and GitHub CLI (Go 1.26.1) reject all 12 canaries
  with explicit macOS certificate-revoked errors. Both receive HTTP responses
  from normal controls. Tests used isolated empty configuration and deliberately
  non-secret placeholder auth strings, never real application credentials.
  This establishes observed default-client behavior on this host, not a Go
  rebuild, an audit of every custom-root path, or retirement of a daemon's
  cached state. The shared PinchTab daemon was not restarted or upgraded.
- **Isolated Chrome:** a fresh, unauthenticated PinchTab profile initially
  displayed a successful connection to a compromised-chain canary. Later the
  same browser rejected all 12 through trusted browser-protocol errors
  (`ERR_CERT_REVOKED` / `ERR_CERT_AUTHORITY_INVALID`) and loaded both controls.
  Its public CertificateRevocation and PKIMetadata components appeared after
  startup. This is consistent with a component-update readiness gap; it is not
  proof that every persistent automation profile is protected. Next: reproduce
  the cold-start case and add a fail-closed, browser-level trust check before
  credential-bearing work. Do not use web page text or a successful navigation
  command alone as evidence of rejection. No shared profile, TLS setting, or
  daemon permission was changed; only the audit-owned instance was stopped.
- **EchoNest Sync:** the installed 0.2.1 application's private certifi bundle
  accepted six direct-chain canaries (the six cross-signed paths already failed).
  Owner approved upgrading to 0.7.0. Rebuilt in an isolated environment with
  Homebrew Python 3.14.3 and certifi 2026.10.19, then installed the signed app.
  Both staged and installed executables pass `--tls-self-test`: all 12 canaries
  rejected and both controls accepted, with `frozen=true`. Strict deep signature
  verification and the 0.7.0 bundle-version check pass. User configuration was
  not touched. GUI/playback and Keychain access were not exercised; the app was
  not running before maintenance and was left stopped.
- EchoNest source branch `fix/sync-packaged-tls-trust` adds a certifi floor,
  offline packaging preflight (including the copied macOS CA file), accurate
  bundle version metadata, isolated build-output options, and a credential-free
  packaged self-test that runs before app/config/keyring imports. All 85 client
  tests pass (plus 12 subtests). Committed and pushed as `a52b23c` on
  `fix/sync-packaged-tls-trust`; the working tree is clean. No PR, release tag,
  or server deployment was created. Local installed-app signature and TLS
  self-tests were rerun successfully after the push; hosted CI was not verified.
  The unaffected ECC C3 root was restored upstream in certifi 2026.10.19; the
  packaging guard deliberately follows certifi's exclusions rather than
  blindly copying Homebrew's broader temporary policy.
- **NiceNode retired with owner approval:** its Electron 27.1.3 / Node 18.17.1
  TLS client accepted all 12 canaries, but the app, node clients, and dedicated
  VM were inactive. Removed `nicenode-machine` using Podman's machine removal
  command (including its image, configuration, dedicated SSH keys, and connection
  registrations). Permanently deleted approximately 949 GiB of Nethermind and
  Lodestar chain databases. Moved the app and remaining application state,
  caches, preferences, and saved state to
  `~/.Trash/NiceNode-retired-20261020.AvZi0o` (446 MiB, including retained keystore
  files whose contents were not inspected). Those trashed files remain
  recoverable until Trash is emptied; do not launch the retired app. Verified no
  registered Podman machines/connections or matching processes remain. Podman
  itself was preserved; filesystem available space increased by about 956 GiB.
- Still open: browser startup gating, application-specific native/Rust trust
  (including vendor-managed networking), the bundled-client findings below,
  and any needed rebuilt Go binaries. Fresh-client success is not blanket
  certification of a whole app or host.

EchoNest rollback artifact (vulnerable old app; preserve, do not launch):
`/Users/dbochman/Backups/echonest-tls-20261020.jFKtuo/EchoNest Sync.app`.

### Bundled-runtime sweep — 2026-10-20

All three Macs were reached. Tests used fresh credential-free processes and
public TLS handshakes, not account requests or the applications' normal entry
points. No vendor app, VPN, managed security control, or shared service was
updated, re-signed, restarted, or reconfigured during this sweep.

The baseline table below predates the approved vendor-update pass documented
later in this file. It counts **accepted compromised-chain canaries**, out of twelve. Both
normal controls passed for every conclusive row. A CA-file test proves the
bundle's trust behavior under the test interpreter, not the entire application.
Electron Node tests do not cover Chromium, custom TLS clients, or cached state.

| Host | App / tested component | Accepted | Evidence |
| --- | --- | --- | --- |
| Mini | Reolink 8.21.12, Electron 20.3.0 / Node 16.15.0 | 12 | Actual standalone bundled Node TLS client |
| Mini | Reolink's `Resources/app/cacert.pem` | 12 | Explicit public CA-file test |
| Mini | ChatGPT 26.721.30844, `cua_node` Node 24.14.0 | 6 | Actual helper; cross-signed six rejected |
| Crosstown MacBook | CurseForge 1.257.1-0, Electron 28.3.2 / Node 18.18.2 | 12 | Actual bundled Node TLS client |
| Crosstown MacBook | Cursor 2.4.23, Electron 39.2.7 / Node 22.21.1 | 6 | Actual bundled Node TLS client |
| Crosstown MacBook | Cursor's separate helper Node 22.21.1 | 6 | Actual helper, independent of Electron |
| Crosstown MacBook | GitHub Desktop 3.4.2, Electron 30.0.8 / Node 20.11.1 | 12 | Actual bundled Node TLS client |
| Crosstown MacBook | Kap 3.6.0, Electron 13.6.9 / Node 14.16.0 | 12 | Actual bundled Node TLS client |
| Crosstown MacBook | Linear 1.22.1, Electron 26.2.4 / Node 18.16.1 | 12 | Actual bundled Node TLS client |
| Crosstown MacBook | Notion 3.1.0, Electron 28.0.0 / Node 18.18.2 | 12 | Actual bundled Node TLS client |
| Crosstown MacBook | PyCharm 2024.1.4, JetBrains Java 17.0.11 | 12 | Actual bundled Java SSLSocket client, HTTPS hostname checks enabled |
| Crosstown MacBook | Inkscape 1.2.2, both certifi and pip-vendored CA files | 12 each | Explicit public CA-file tests |
| dylans-mac | EchoNest Sync 0.2.1 CA file | 6 | Public CA file copied to Mini and tested there |
| dylans-mac | Reolink 8.21.12 CA file | 12 | Public CA file copied to Mini and tested there |
| dylans-mac | ChatGPT 26.908.70816, Node 24.20.0 exported built-in roots | 6 | Exported public roots tested on Mini; not an actual-runtime result |

Important qualifications and follow-ups:

- **Network interception on dylans-mac:** direct probes of its newer
  CurseForge, Cursor, Orca, Reolink, ChatGPT helper, Apple Python, and old
  EchoNest CA file rejected all twelve endpoints, but this does **not** certify
  those runtimes. Inspection of the public leaf certificate for the direct DV
  G3B canary showed `NetAlerts Services`, not the expected eMudhra issuer;
  github.com retained a normal public issuer. Treat direct incident tests on
  this network as inconclusive until the expected chain can be established.
  No proxy/VPN/security control was bypassed or changed. Public CA-file exports
  above independently establish remaining trust paths without moving account
  data or app configuration. Even the old Reolink runtime appeared to pass
  remotely while that same version on the Mini accepted every chain.
- **Controlled mitigation experiment only:** on the Mini, the ChatGPT helper
  and Reolink Node client rejected all twelve and accepted both controls when
  launched solely for the test with `NODE_OPTIONS=--use-openssl-ca` and
  `SSL_CERT_FILE=/opt/homebrew/etc/ca-certificates/cert.pem`. No persistent
  setting was applied. This does not repair GUI launches, explicitly selected
  private CA files, or other networking paths. Do not label these apps fixed.
- **Vendor update is not automatically a fix:** Reolink's official download
  page still lists the installed macOS version 8.21.12 (July 23, 2026). Prefer
  keeping it closed pending an owner decision to retire it or a verified vendor
  repair; do not replace files inside its signed bundle.
  Source: https://reolink.com/software-and-manual/
- **Testability limits:** inspected Electron fuse metadata before invoking
  Node mode. 1Password, Claude, Slack, Discord, Figma, Granola, and some newer
  apps disable `RunAsNode`; they remain unverified, not passed. Did not flip
  fuses or use their normal account-bearing launch paths. Old 1Clipboard has
  no recognized fuse wire and was skipped. The Cluely standalone probe timed
  out; only its identified audit-owned process was terminated. Native/Rust
  clients and normal browser paths still need app-specific evidence.
  Method: https://www.electronjs.org/docs/latest/tutorial/fuses
- **Priority next actions:** deploy the already-tested EchoNest 0.7.0 repair
  to dylans-mac after confirming the owner wants its second installation
  upgraded; select update-versus-retirement for the old MacBook apps; then
  retest actual vendor builds. Separately resolve Reolink and the ChatGPT Node
  helper trust paths. Preserve configurations and obtain approval before
  quitting active apps, removing anything, or changing managed software.
- No evidence of exploitation was sought or established by these TLS checks.
  They establish reachable trust weaknesses, not a compromise investigation.

### Approved vendor-update pass — 2026-10-20

- Owner approved upgrading the second EchoNest installation and updating then
  retesting the affected MacBook apps, with settings preserved and removals
  excluded. None of the target apps were running at the initial process check.
- **EchoNest on dylans-mac is now 0.7.0.** Transferred the exact signed Mini build
  (archive SHA-256 `f5554706c2746203810af528ccddbda5464481c1b73649fca2ecf6b43784fa48`),
  verified the staged signature/version, preserved 0.2.1 at
  `/Users/dylanbochman/Backups/echonest-tls-20261020.4pYwF9/EchoNest Sync.app`,
  and swapped the app without touching configuration or Keychain. Installed
  signature and version checks pass. Its embedded CA file matches the tested
  Mini build byte-for-byte (SHA-256
  `50f79f98dac192b20dbf73f35c56f0b0c557ab267ff5199d1117cc1180b70785`).
  The frozen self-test rejects twelve endpoints and accepts both controls, but
  the remote network interception caveat still applies: independent evidence
  comes from the identical Mini build and bundle. GUI/playback were not tested;
  the app was left stopped. The retained rollback app is vulnerable; do not run.
- **Kap:** vendor releases and Homebrew still list 3.6.0, the installed version.
  No newer vendor artifact was found; it remains unchanged and unresolved.
  Source: https://github.com/wulkano/Kap/releases

Seven MacBook apps were updated from official vendor artifacts referenced by
Homebrew metadata, without adopting the existing installations into Homebrew
or changing CLI links. Each archive matched its SHA-256; the app identifier and
Developer ID team matched the previous installation; deep strict code-signature
verification and Gatekeeper assessment passed before replacement. Signatures
were checked again after installation. No vendor bundle was modified or re-signed.

| MacBook app | Installed update | Retest after update |
| --- | --- | --- |
| Cursor | 2.4.23 → 3.26.13 | Electron 42.10.0 / Node 24.18.1 and separate Node helper each still accept six direct chains |
| GitHub Desktop | 3.4.2 → 3.6.7 | Electron 42.0.1 / Node 24.15.0 still accepts six direct chains |
| Notion | 3.1.0 → 7.40.0 | Electron 44.9.1 disables RunAsNode; trust remains unverified, not cleared |
| Linear | 1.22.1 → 1.32.4 | Electron 41.3.0 disables RunAsNode; trust remains unverified, not cleared |
| CurseForge | 1.257.1-0 → 1.324.0-41180 | Electron 42.11.4 / Node 24.19.0 still accepts six direct chains |
| PyCharm | 2024.1.4 → 2026.2.3 | Actual bundled JetBrains Java 25.0.4 SSLSocket client still accepts all twelve chains |
| Inkscape | 1.2.2 → 1.4.4 | Main certifi bundle still accepts six direct chains; pip-vendored bundle still accepts all twelve |

Both normal controls passed in every completed retest. Unchanged Kap's
credential-free Node diagnostic still accepted all twelve with the expected
eMudhra issuers, providing a contemporaneous network baseline on the MacBook.
No normal GUI app was launched; existing settings, sessions, plugins, accounts,
and Keychain entries were left untouched. GUI behavior, project compatibility,
and any first-launch migration remain untested. All audit-owned disk images
were detached, and the target GUI apps remain stopped.

Rollback app bundles are retained under these MacBook directories (do not run
the old vulnerable versions):

- `/Users/dbochman/Backups/vendor-tls-20261020.mdiw9fyd/` — Cursor, Notion,
  Linear, CurseForge, PyCharm.
- `/Users/dbochman/Backups/vendor-tls-20261020.f1o5li5p/` — GitHub Desktop,
  Inkscape.

**Status after vendor-update pass: incident remediation not complete.** Do not
equate the new version numbers, valid signatures, or disabled test mode with
trust remediation. Next work requires application-scoped trust configuration
where supported and/or verified vendor fixes. No global launch environment,
Java trust store, browser settings, managed security controls, or signed vendor
bundle was changed to force a pass. Reolink and the separate ChatGPT helper
findings from the previous sweep remain open and were outside this approved
MacBook vendor-update batch. No apps or user data were removed.

### Approved application retirement — 2026-10-20

Owner subsequently approved retiring Kap on the Crosstown MacBook and the
Reolink desktop apps on the Mini and dylans-mac, preserving settings. This
supersedes the open retirement decisions for those three installations above;
it does not clear the other bundled-runtime findings.

- Verified exact bundle identifiers and versions before moving: Kap 3.6.0
  (`com.wulkano.kap`) and Reolink 8.21.12 (`com.reolink.app`). None were running;
  no matching loaded launch services or references in the checked user/system
  LaunchAgent and LaunchDaemon files were found.
- Unregistered the three installed paths with LaunchServices and moved only
  the app bundles into unique owner-only Trash directories. Verified installed
  paths are absent, retired bundles are present, and no matching app processes
  remain. Strict deep code-signature verification passed for all three retired
  bundles. No vendor bundle contents were edited or re-signed.
- Recoverable locations (vulnerable binaries; do not restore or launch):
  - Mini: `/Users/dbochman/.Trash/vendor-tls-retired-20261020.dsVlmA/Reolink.app`
    (419 MiB).
  - Crosstown MacBook:
    `/Users/dbochman/.Trash/vendor-tls-retired-20261020.HyCGEF/Kap.app`
    (324 MiB).
  - dylans-mac:
    `/Users/dylanbochman/.Trash/vendor-tls-retired-20261020.rM2cPM/Reolink.app`
    (419 MiB).
- Application settings, accounts, Keychain entries, recordings, and other user
  data were left untouched. No permanent deletion or disk-space reclamation
  is claimed; the bundles remain recoverable until their Trash copies are
  permanently removed.
- Reolink camera automation remains separate: the tracked helper connects to
  the local Home Hub using pinned TLS, not the desktop application. Its code,
  configuration, and services were not changed or restarted. No live camera
  request or capture was performed for this retirement.
- GitHub Desktop, CurseForge, Cursor, PyCharm, Inkscape, ChatGPT helpers, and
  unverified applications remain separate follow-ups. No global trust setting,
  outbound firewall, managed security control, or application-specific trust
  override was deployed in this retirement step.

### PyCharm mitigation, further retirement, and launch-path verification — 2026-10-20

Owner approved deploying the PyCharm mitigation, retiring GitHub Desktop and
CurseForge on the Crosstown MacBook, and then investigating Cursor/ChatGPT's
actual application launch paths. This section supersedes their pending actions
above; it is not a blanket clearance of every application.

**PyCharm default JVM trust mitigated on the Crosstown MacBook:**

- Installed `/Applications/PyCharm.app.vmoptions` beside the signed bundle.
  The native launcher selects this supported adjacent options file without
  environment overrides. It preserves the existing 4 GiB heap preference and
  sets the three JSSE trust-store properties. No `.app` contents were changed.
- Installed a PKCS12 public trust store at
  `/Users/dbochman/.local/share/app-trust/pycharm/public-ca.p12`, built from the
  repaired Homebrew PEM bundle (188 CA certificates). Its fixed password is
  public integrity metadata, not a credential. Source PEM SHA-256:
  `f356a97fb33148de963979b5b164c0b8de13c5bfe0ffe913ef68208d6364a419`.
- Added tracked `bin/refresh-pycharm-trust` and `lib/pycharm-trust/`, and deployed
  the refresh command/sources under `~/.local/lib/app-trust/` on the MacBook.
  The command builds and tests a candidate before atomic replacement, retaining
  prior public stores. Refresh after CA updates and restart PyCharm; no scheduled
  job was installed. See `lib/pycharm-trust/README.md` for scope and maintenance.
- Standalone bundled Java and a **native-launch JVM diagnostic** both rejected
  all twelve incident canaries and accepted github.com/pypi.org. The latter
  used a temporary external premain probe, confirmed the native launcher loaded
  the deployed options/store, then exited before IDE account/project/plugin
  initialization. The first native run had one DNS failure and correctly exited
  inconclusive; a fresh complete rerun passed. No diagnostic agent is configured
  in the permanent options file. Strict deep vendor-signature verification passes.
- The old `PyCharm2024.1` configuration is unchanged; no new `PyCharm2026.2`
  configuration was pre-created, so first-launch migration remains available.
  No legacy custom `ssl/cacerts` store or explicit automatic-certificate-accept
  setting was found in the checked legacy settings. Keep automatic acceptance
  disabled and recheck after migration. This validates default JVM trust, not
  JCEF, plugin-defined TLS, subprocesses or manually accepted certificates.

**GitHub Desktop and CurseForge retired on the Crosstown MacBook:**

- GitHub Desktop 3.6.7 (`com.github.GitHubClient`) moved to
  `/Users/dbochman/.Trash/vendor-tls-retired-20261020.Wxpzkl/GitHub Desktop.app`
  (approximately 1.0 GiB).
- CurseForge 1.324.0-41180 (`com.overwolf.curseforge`) moved to
  `/Users/dbochman/.Trash/vendor-tls-retired-20261020.a19Gcz/CurseForge.app`
  (604 MiB).
- Both were stopped and had no matching loaded launch services or references
  in the checked LaunchAgent/LaunchDaemon files. Unregistered their installed
  paths with LaunchServices. Verified installed paths absent, no matching app
  processes, and strict deep signatures intact at the retired locations.
- Settings, credentials, repositories, game/mod data and prior rollback copies
  were not touched. Trash bundles remain recoverable and vulnerable; do not
  restore or launch them. No disk-space reclamation is claimed.

**Cursor and ChatGPT: helper-only override is not a GUI mitigation.**

Launched the actual signed application executables with an isolated profile
argument, `NODE_OPTIONS=--use-openssl-ca`, and the repaired `SSL_CERT_FILE`.
Used an ephemeral loopback inspector to pause at the initial script breakpoint;
never sent `Debugger.resume`. Inspected only runtime versions, these two
environment variables and public default CA certificates, then terminated each
audit-owned process before vendor application JavaScript proceeded. No account,
project, user extension or normal application workflow was exercised.

| Application / host | GUI Node runtime | Effective default roots | Launch result |
| --- | --- | --- | --- |
| Cursor 3.26.13 / Crosstown MacBook | 24.18.1, Electron 42.10.0 | 120, matching bundled count | `NODE_OPTIONS` emptied; repaired `SSL_CERT_FILE` present but not selected |
| ChatGPT 26.721.30844 / Mini | 24.14.0, packaged runtime reports 150.0.7871.128 | 144, matching bundled count | Same |
| ChatGPT 26.908.70816 / dylans-mac | 24.19.0, packaged runtime reports 152.0.7977.83 | 120, matching bundled count | Same |

Exported only those public root sets and tested each on the Mini: **six direct
compromised chains accepted, six cross-signed chains rejected, both normal
controls passed**. Testing the public export on the Mini avoids mistaking
dylans-mac's intercepted canary responses for client distrust. This proves
continued trust in the GUI Node defaults, not that every application request
uses those defaults. The separate Node helper runtimes can differ from GUI
Node (notably ChatGPT on dylans-mac).

No ineffective global environment change or helper-only launcher was deployed.
No fuses, signed bundles, VPN or managed security controls were changed. At the
end of this verification phase, Cursor and both ChatGPT desktop installations
needed a verified vendor repair or an owner decision on retirement versus
enforced outbound containment; the approved retirement is recorded below. Inkscape's
mitigation remains unvalidated. Apps whose runtime tests are disabled remain
**unverified**, not newly confirmed vulnerable. Whole-app trust clearance is
not claimed for any of these unresolved cases.

### Approved Cursor and ChatGPT retirement — 2026-10-20

Owner approved retirement after the GUI launch-path findings above. Scope was
Cursor on the Crosstown MacBook and ChatGPT on the Mini and dylans-mac; no
other application installation or separate Codex CLI/app was removed.

| Host | Retired app | Recoverable Trash location |
| --- | --- | --- |
| Crosstown MacBook | Cursor 3.26.13 (approximately 1.0 GiB) | `/Users/dbochman/.Trash/vendor-tls-retired-20261020.LtyZMC/Cursor.app` |
| Mini | ChatGPT 26.721.30844 (approximately 1.3 GiB) | `/Users/dbochman/.Trash/vendor-tls-retired-20261020.U00qxz/ChatGPT.app` |
| dylans-mac | ChatGPT 26.908.70816 (approximately 1.3 GiB) | `/Users/dylanbochman/.Trash/vendor-tls-retired-20261020.mWvf2l/ChatGPT.app` |

- Confirmed versions, paths and bundle identifiers before moving. The ChatGPT
  bundles identify as `com.openai.codex`; targets were resolved by their exact
  `/Applications/ChatGPT.app` paths, not by an identifier-wide removal.
- All three were stopped. No relevant launch-service registrations or matching
  app-path references were found in the checked LaunchAgent/LaunchDaemon files;
  the Mini's tracked `bin`, OpenClaw wrappers and LaunchAgents also had no exact
  target-path references. Apple's unrelated CursorUIViewService was untouched.
- Unregistered each installed path with LaunchServices and moved only its
  signed app bundle to the listed owner-only Trash directory. Verified the
  original installed paths are absent, Trash bundles present, no matching app
  processes remain, and strict deep signature verification passes for all three.
- Settings, account state, credentials, extensions, projects and other user data
  were not removed or inspected. Existing rollback backups were preserved.
  Retained app binaries remain vulnerable: do not restore or launch them.
  Nothing was permanently deleted and disk-space reclamation is not claimed.
- **Non-security cleanup remaining:** the MacBook's root-owned
  `/usr/local/bin/cursor` symlink still points to
  `/Applications/Cursor.app/Contents/Resources/app/bin/code`. Its parent is not
  writable without administrator access. Left the now-dangling shortcut in
  place; it cannot launch the retired app. No sudo access or permission change
  was attempted.
- This closes the retirement decisions for these three specific installations.
  PyCharm's deployed default-JVM mitigation is unchanged. Inkscape remains
  unvalidated, and previously untestable apps remain unverified; this is not a
  whole-host trust clearance.

### Remaining-gap action and browser gate — late 2026-10-20

This supersedes the earlier pending Inkscape decision and narrows, rather than
erases, the native-networking and existing-Go caveats.

**Three more MacBook apps retired with explicit owner approval:**

| Crosstown MacBook app | Evidence and disposition | Recoverable Trash location |
| --- | --- | --- |
| Inkscape 1.4.4 | Public CA bundles had accepted affected chains. Standalone Python was killed by an AMFI launch-constraint violation under SSH, despite valid signatures. Two isolated native extension attempts crashed before producing a trust result; no runtime mitigation was validated. Retired, not patched or cleared. | `/Users/dbochman/.Trash/vendor-tls-retired-20261020.gj6jwQ/Inkscape.app` |
| Figma 116.14.9 / Electron 25.8.1 / Node 18.15.0 | Paused native startup exported 140 bundled roots; those roots accepted all 12 canaries and both controls on the Mini. Old Node lacks the default-root introspection API, so this is bundled-root evidence, not proof every app request uses that store. | `/Users/dbochman/.Trash/vendor-tls-retired-20261020.9s8avE/Figma.app` |
| Granola 7.41.2 / Electron 39.2.7 / Node 22.21.1 | Paused native startup stripped `NODE_OPTIONS`; its 146 default roots accepted six direct chains, rejected six cross-signed chains, and accepted both controls when tested on the Mini. The CA environment override was ineffective for GUI defaults. | `/Users/dbochman/.Trash/vendor-tls-retired-20261020.SO7sE7/Granola.app` |

All three were stopped, unregistered by exact installed path, and moved without
changing signed contents. Original paths are absent; strict deep signatures
still pass in Trash and no matching installed-app processes remain. Settings,
documents, account state and credentials were preserved. Approximately 1.35 GiB
was moved, not permanently deleted. Do not launch the retained binaries. Only
the Crosstown installations were approved for these retirements; Granola on
dylans-mac was not removed. Audit-created Inkscape processes were terminated
after their failed probes; no unrelated app or security service was stopped.

**Native Apple trust:** an ephemeral URLSession/SecTrust diagnostic, with no
shared cookies, credentials or cache and cancellation before HTTP requests,
rejected all 12 affected chains with `errSecCertificateRevoked` (`-67820`) on
both the Mini and Crosstown MacBook. Both normal controls passed on each host,
and the expected affected intermediate identities were present. On dylans-mac,
controls passed but canaries returned `-25318` with no evaluable issuer chain:
**inconclusive**, consistent with the earlier network-interception limitation.
Managed network/security controls were not changed. This verifies the tested
Apple trust path, not apps using custom anchors, Rust/Node stores, or custom
certificate callbacks.

**Installed Go clients:** fresh credential-free invocations on the Mini of
GitHub CLI (built Go 1.26.1), PinchTab 0.11.0 (Go 1.26.2), and goplaces (Go
1.26.4) each rejected all 12 with explicit x509 certificate-revoked errors and
received HTTP responses from github.com/pypi.org. Only isolated empty config
and non-secret placeholder auth strings were used. No rebuild was needed to
obtain those results; [Go's macOS verifier uses system APIs](https://pkg.go.dev/crypto/x509#Certificate.Verify).
No compiler update is counted as a binary repair. Mini `op` (Go 1.25.7), `spogo`
(Go 1.24.0), the Tailscale app/daemon, other-host clients and custom-root paths
remain unverified by these probes. They were not driven through real account
sessions or retired merely because their build metadata is older. Shared
PinchTab and Tailscale daemons were not restarted.

**Browser startup gap reproduced and acquisition gate implemented:** an
audit-owned warmed Chrome 155.0.8059.85 rejected all 12 and passed both controls
through browser-protocol diagnostics. A newly started audit profile accepted
six direct chains while rejecting six cross-signed chains; both controls
passed. The new headless acquisition gate refused to return that instance and
stopped it. Three bounded public-test readiness attempts also remained blocked.
The earlier readiness hypothesis is now supported by a reproduced cold/warm
difference, but its exact component-update cause is not asserted.

`pinchtab-headless-instance acquire` now requires all 12 rejections plus two
controls in a disposable context before returning an instance ID. It disables
page scripts/cache in its diagnostic tabs, uses no account state, has no pass
cache, and never treats page text, DNS/timeout/expiry errors, missing evidence or
missing tooling as success. Failed acquisition stops only a newly created
instance, never an existing shared instance. Paired helpers, deployment ordering,
and fail-closed regression tests are tracked; see
[the operational contract](../bin/README.md#headless-browser-trust-gate).

The paired helpers and acquisition wrapper were deployed on the Mini without
restarting the shared daemon or running an authenticated workflow. Source and
deployed copies match. The installed Homebrew-symlink entry point passed on the
audit profile; a restarted warm profile initially rejected acquisition's first
test batch, then passed after the bounded readiness retry. The earlier cold
profile remained blocked through all three attempts. All audit-owned instances
were stopped afterward. The prior wrapper is retained at
`/Users/dbochman/Backups/browser-trust-gate-20261020.DmVnoQ/pinchtab-headless-instance`;
restoring it would remove the gate and is not a routine workaround.

Verification: four Node gate tests, eight PinchTab Python tests, 27 deployment
contract tests and 103 weekly-finance tests passed, alongside Python compilation,
shell syntax checks, diff whitespace checks and `sync.sh validate`.
`install.sh --dry-run` exited 2 because of pre-existing protected conflicts at
the gateway plist and deployed dog-walk/presence skills. Those were not
overwritten; no broad installer or shared-service restart was performed.

The PinchTab skill required a dedicated session; restricted sessions could not
access the needed instance endpoints. The owner explicitly approved using the
existing local operator connection for audit-owned instances only. No session
policy or daemon permissions were changed, and no shared profile was tested.

**Still not cleared:** applications disabling both RunAsNode and Node inspection
(including the checked 1Password, Claude, Slack, Discord, Linear and Notion
builds) remain unverified, not confirmed vulnerable. The browser gate covers
caller work through the managed helper, not startup background traffic, Cielo's
separate lifecycle, direct browser launches, or vendor app networking. Future
work must use supported vendor diagnostics/updates or a specifically approved
retirement/containment decision; no global environment trick, fuse change,
ad-hoc re-signing, or managed VPN bypass was deployed.

### Windows desktop follow-up — late 2026-10-20

The owner authorized remediation of the Windows desktops, then explicitly
approved a process-only PowerShell execution-policy override and retirement
of CurseForge while preserving mods/data. No persistent execution policy,
antivirus setting, firewall rule, shared service or browser profile was changed.

`DESKTOP-R9JS0OK` is the reachable RTX 5090 Windows 11 Home desktop (build
26300), administered through the existing `desktop-compute` WSL bridge.
`DESKTOP-4G2S6KQ` is offline at the cabin; the owner cannot wake it currently.
The cabin desktop remains pending, not cleared.

| Tested path on the reachable desktop | Before | After / disposition |
| --- | --- | --- |
| Ubuntu 26.04 Python default trust | Six direct chains accepted; six cross-signs rejected; two controls passed | All 12 rejected and two controls passed after the CA package upgrade |
| WSL curl and distro certifi | Separate trust consumers checked after repair | All 12 rejected and two controls passed for each |
| Windows curl / Schannel | All 12 explicitly revoked, two controls passed | No native-store repair needed for this tested path |
| Native .NET SslStream | All 12 explicitly distrusted/revoked, two controls passed, with online revocation both off and on | Observed affected issuer identities; no HTTP/account requests |
| Fresh Chrome 155.0.8059.86 | Six direct accepted, six cross-signs rejected, two controls passed through repeated checks | All 12 rejected and two controls passed on first attempt and repeat after explicit local distrust |
| Fresh Edge 155.0.4283.62 | All 12 accepted, two controls passed through repeated checks | All 12 rejected and two controls passed on first attempt and repeat after explicit local distrust |
| CurseForge 1.324.0-41180 bundled Node | Electron 42.11.4 / Node 24.19.0 accepted six direct chains; rejected six cross-signs; two controls passed | Retired with explicit owner approval; no claim that the GUI was repaired |

**WSL repair:** upgraded only Ubuntu `ca-certificates`
`20260601~26.04.1 -> 20260816ubuntu0.26.04.1` using the configured signed APT
repository and documented trusted WSL maintenance path. The dry run selected
one package; 69 other upgrades were left untouched. No distro restart or
service restart was performed. Distro certifi reports version 2026.01.04 but
uses `/etc/ssl/certs/ca-certificates.crt`; its actual bundle passed. No separate
certifi package upgrade was needed for that tested interpreter.

**Browser mitigation:** added all 16 published compromised intermediate
certificates to `CurrentUser/Disallowed` for Owner, including the four
code-signing certificates. This does not remove broader root CAs. All DER
SHA-256 hashes were matched against Mozilla's incident description/comment 2
before mutation. The tracked helper is
`openclaw/desktop-compute/block-emudhra-intermediates.ps1`; it validates the
entire set before writing, defaults to read-only, preserves existing entries,
requires a new receipt and verifies read-back. Reapplication reported all 16
already present. Seven Windows-native validation/guard checks passed.
Code-signing entries were read back; no malicious executable was run as a test.

Chrome and Edge were vendor-signature checked and started headlessly with
fresh audit-only user-data directories. The existing
`pinchtab-browser-trust.cjs` inspected TLS results through local CDP, with
page scripts disabled and no verification bypass. A dedicated PinchTab session
was created, but the Windows/WSL boundary required running the diagnostic on
Windows rather than attaching the Mini's browser daemon. The signed CurseForge
binary's enabled RunAsNode fuse supplied the temporary diagnostic host without
loading app/account code; it was retired afterward. The first wrapper attempt
returned before its Windows GUI-subsystem helper finished; it was inconclusive.
Explicit process waiting fixed the harness before the reported before/after
measurements. All audit browser processes were stopped; user browsers were not.

Deployed helper, public inventory, native diagnostic, tests and trust receipts:
`C:\Users\Owner\AppData\Local\OpenClaw\Security\emudhra-20261020`.
Audit-only browser diagnostics and profiles:
`C:\Users\Owner\AppData\Local\Temp\emudhra-audit.kb2Wmp`.
The persistent execution-policy scopes remained Undefined after diagnostics.

**CurseForge retirement:** stopped only processes from its exact installation,
removed its startup value, two shortcuts, URL protocol and uninstall entry
after backing up their registrations, and moved the application into
`C:\Users\Owner\AppData\Local\OpenClaw\Security\emudhra-20261020\retired-CurseForge-20261021T033814Z`.
Five executables were hash-verified and renamed with `.disabled` extensions.
Signed bytes were preserved; these are unrepaired recovery copies and must not
be re-enabled without a verified repair. A transient file lock interrupted the
first move; the remaining installation files were moved without overwriting
recovery files and verified before the empty source directories were removed.
Mods, game installations, settings and account data outside the application
installation were untouched. No space reclamation or permanent application
deletion is claimed.

**Remaining limits:** local distrust is scoped to Owner, not every Windows
account/service. Independent application trust stores remain separate. Discord
1.0.9264 disables RunAsNode and Node inspection; no fuse was changed and no GUI
account session was instrumented. Firefox 158.0, Edge WebView2, Steam/Epic and
the installed Codex package are not independently cleared by the native or
fresh-Chrome/Edge checks. Existing user browser connections/profiles were not
inspected or restarted; fresh processes were verified. The offline cabin
desktop needs the same host-specific audit when available. These findings do
not establish prior interception or a whole-machine security clearance.

## Audit commands

Run these on each host. The output of each should be diffed against
the "Pinned / hold" section before any upgrade.

### Brew formulae + casks

```bash
HOMEBREW_NO_AUTO_UPDATE=1 brew outdated --verbose
HOMEBREW_NO_AUTO_UPDATE=1 brew outdated --cask --greedy
```

### npm globals

```bash
PATH=/opt/homebrew/bin:/opt/homebrew/opt/node@22/bin:$PATH npm outdated -g
```

### pipx

```bash
pipx list --short
# Then for each package, check upstream version manually
```

### macOS

```bash
sw_vers
softwareupdate --list
uptime
df -h /
```

### From a laptop, scan the Mini and MBP via SSH:

```bash
# Mini
ssh dylans-mac-mini '<command>'

# MBP (via Mini, since 1Password agent on laptop hangs SSH)
ssh dylans-mac-mini "ssh -i ~/.ssh/id_mini_to_mbp -o IdentityAgent=none dylans-macbook-pro '<command>'"
```

## Pinned / hold list

These are deliberately pinned. **Do not bump without consulting the
linked plan or skill.**

| Package | Pin | Reason | Plan/Skill |
|---|---|---|---|
| `@googleworkspace/cli` | 0.4.4 | Drops `--account` flag in 0.22.x; breaks every gws skill | `openclaw/plans/gws-0.22-migration.md` |
| `openclaw` | manual review | Auto-upgrade removed 2026-03-12. May overwrite LaunchAgent plist. | `openclaw-upgrade-plist-overwrite` skill |

The pin on `@googleworkspace/cli` is informational only — npm has no
global pinning mechanism. Future-Claude should read MEMORY.md /
TOOLS.md before any `npm install -g @googleworkspace/cli@latest`.

## Risk tiers

Categorize every outdated dep before deciding to bump.

### Tier 1 — Safe remote (do anytime)

- Brew formulae routine bumps (libraries, CLIs without persistent daemons)
- Brew casks where the app isn't in active use during the upgrade
- npm globals that are pure CLIs with no daemon/auth dependency

Recovery if broken: roll back via `brew install <name>@<old-version>`
or `npm install -g <pkg>@<old-version>`. SSH-recoverable.

### Tier 2 — Reversible but auth-sensitive

Touches an auth chain that takes meaningful effort to repair if it
breaks (browser OAuth re-auth, scp dance, etc).

Examples:
- `@openai/codex` — this is not only an interactive CLI on the Mini.
  OpenClaw runs the configured global binary as a shared Codex app-server, so
  an app-server that was already running can keep the old executable after npm
  replaces it. After an actual Codex version change, restart
  `ai.openclaw.gateway`, wait for gateway health, and run the live no-delivery
  OpenAI smoke check documented in `LAUNCHAGENTS.md`. `codex --version` and a
  model-list check only validate the replacement binary on disk; they do not
  prove that the gateway retired its previous child process.
- `@anthropic-ai/claude-code` — powers the `claude` CLI used by
  `ai.openclaw.oauth-refresh` LaunchAgent. If the refresh agent
  stops working, OAuth tokens age out and `openclaw` agent calls
  fail. Recovery: re-auth on laptop, scp to Mini.
- `1password-cli` — used only by attended exact-field cache refreshes. No
  LaunchAgent, cron job, or gateway child may read `.env-token` or invoke it;
  version skew can still break attended biometric refresh.
- Tailscale — the logged-in macOS app/network extension is the authoritative
  backend. `/opt/homebrew/bin/tailscale` is a source-controlled wrapper that
  executes the app-bundled CLI with `TAILSCALE_BE_CLI=1`, keeping the CLI and
  daemon on the exact same build. Keep `/usr/sbin` on the gateway PATH so
  backend discovery can use `lsof`; an app update or backend-selection
  regression can disable private Control UI/node access. The Homebrew formula
  is retained unlinked only as a recovery artifact; do not relink its CLI.
  See `tailscale-macos-localapi-stale-port` skill.

Recovery: SSH-recoverable but with a re-auth detour.

### Tier 3 — Requires babysitting / physical access

- `openclaw` — service reinstall can replace the gateway plist. Back up the
  live plist and service-environment files, then verify the generated
  service-env → FDA app-wrapper chain after upgrade. Regenerate with
  `openclaw gateway install --force --wrapper ...` when invalid; do not blindly
  restore an older plist. Use `bootout` + `bootstrap` only when the loaded plist
  changes. See `openclaw-upgrade-plist-overwrite` and
  `openclaw-post-upgrade-scope-fix` skills.
- `pinchtab` — browser-automation library. Major version bumps may
  break grocery-reorder, presence-receive flows. Test against the
  highest-impact skill before committing.
- macOS major / minor updates — restart-required, ~30 min downtime
  for all cron jobs and the gateway. Schedule.

Recovery: SSH still works after most failures, but the cost of a
stuck state is high (e.g. gateway crash-loops require reading err
logs and editing plists). Do during a window where you can babysit.

### Tier 4 — Skip / pinned

See "Pinned / hold list" above.

## 2026-04-29 audit results

**Hosts:** Mini (uptime 26 days), MBP (uptime 5 min — fresh boot for
macOS 26.4.1 install).

### Done remotely (Tier 1)

| Host | What | Versions |
|---|---|---|
| MBP | `brew upgrade` | ~57 formulae, all routine |
| MBP | macOS Tahoe | 26.3 → 26.4.1 (user-driven, required reboot) |
| MBP | Tailscale (in-app self-update before audit) | 1.94.x → 1.96.5 |
| Mini | `brew upgrade` | ~30 formulae + 1password-cli cask |
| Mini | npm: `@openai/codex` | 0.114.0 → 0.125.0 |
| Mini | npm: `@steipete/summarize` | 0.11.1 → 0.14.1 |
| Mini | npm: `@tobilu/qmd` | 2.0.1 → 2.1.0 |

Verification after each: gateway log activity, presence push freshness,
gws auth still works.

### Held during this audit

| Host | What | Current → Latest | Why held |
|---|---|---|---|
| Mini | `@anthropic-ai/claude-code` | 2.1.76 → 2.1.123 | Tier 2 — OAuth refresh chain. Bump in a window where you can verify the next refresh fires cleanly. |
| Mini | `openclaw` | 2026.4.2 → 2026.4.27 | Tier 3 — see procedure below. ~25 versions of drift. **Done 2026-05-10** — bumped past 2026.4.27 to v2026.5.7. Doctor auto-rewrote `openclaw.json` (added `anthropic.enabled: true`, version-stamp bump only); see `~/repos/openclaw-operator/audit-log.md` 2026-05-10 entry. |
| Mini | `pinchtab` | 0.7.6 → 0.10.0 | Tier 3 — major bump, breaks browser automation downstream if incompatible. Test against grocery-reorder before. **Done 2026-05-10** — bumped to v0.11.0. Required: (1) manual `node scripts/postinstall.js` after `npm install -g` because npm postinstall didn't fire (downloads platform-specific Mach-O binary to `~/.pinchtab/bin/<version>/`), (2) `pinchtab config set security.allowEvaluate true` because `/evaluate` is now 403 by default — grocery-reorder hits this endpoint directly. New profile path is `~/.pinchtab/profiles/default/` (was `~/.pinchtab/chrome-profile/`); session cookies migrated cleanly. Backup at `~/.pinchtab.pre-0.11.0/` on Mini. |
| Mini | macOS 26.4 → 26.4.1 | system | Tier 3 — full downtime ~30 min. Schedule. |
| MBP | `pinchtab` | check at next audit | unknown if installed |
| Both | `@googleworkspace/cli` | 0.4.4 | **Tier 4 / pinned** — see migration plan |

## Deferred procedures

### Codex CLI on Mini (Tier 2)

The gateway must recycle its shared Codex app-server after a real CLI version
change. On 2026-07-09, npm installed Codex `0.144.0` while the running gateway
continued using a shared `0.142.5` app-server; later `gpt-5.6-sol` turns failed
with a newer-Codex requirement and fell back to Anthropic. The stale child
eventually retired, but the upgrade procedure must not depend on that cleanup.

```bash
# 1. Snapshot the installed version
export PATH=/opt/homebrew/bin:/opt/homebrew/opt/node@22/bin:$PATH
before=$(codex --version)

# 2. Upgrade
npm install -g @openai/codex@latest

# 3. Restart only when the installed version actually changed
after=$(codex --version)
if [[ "$before" != "$after" ]]; then
  launchctl kickstart -k gui/$(id -u)/ai.openclaw.gateway
fi

# 4. Require the local health endpoint to recover
for _ in {1..30}; do
  curl -fsS http://127.0.0.1:18789/health >/dev/null && break
  sleep 1
done
curl -fsS http://127.0.0.1:18789/health >/dev/null
```

Then run the **OpenAI verification** no-delivery smoke request in
`LAUNCHAGENTS.md`. Require `agentMeta.provider=openai`,
`agentMeta.model=gpt-5.6-sol`, and `executionTrace.fallbackUsed=false`. A
successful fallback response is not a passing smoke test.

### Claude Code on Mini (Tier 2)

```bash
# 1. Snapshot current
ssh dylans-mac-mini 'PATH=/opt/homebrew/opt/node@22/bin:$PATH; \
  claude --version > /tmp/claude-version-pre.txt'

# 2. Upgrade
ssh dylans-mac-mini 'PATH=/opt/homebrew/bin:/opt/homebrew/opt/node@22/bin:$PATH; \
  npm install -g @anthropic-ai/claude-code@latest'

# 3. Verify CLI works
ssh dylans-mac-mini 'PATH=/opt/homebrew/opt/node@22/bin:$PATH; \
  claude --version'

# 4. Verify next OAuth refresh fires cleanly. The refresh runs every
#    6 hours via ai.openclaw.oauth-refresh. Watch:
ssh dylans-mac-mini 'tail -f ~/.openclaw/logs/oauth-refresh.log'
#    Or trigger manually:
ssh dylans-mac-mini 'launchctl kickstart -k gui/$(id -u)/ai.openclaw.oauth-refresh'

# 5. If refresh fails, roll back:
ssh dylans-mac-mini 'PATH=/opt/homebrew/bin:/opt/homebrew/opt/node@22/bin:$PATH; \
  npm install -g @anthropic-ai/claude-code@2.1.76'
```

### OpenClaw on Mini (Tier 3)

**2026-10-11 upgrade accepted:** the reviewed
[Node 24 LTS + current stable OpenClaw implementation plan](node24-openclaw-stable-upgrade.md)
completed both separately approved phases. Production now uses the checksum-pinned
official Node `24.21.0` runtime and OpenClaw `2026.10.1` stable, with matching
Codex/iMessage plugins. The [Phase A record](node24-phase-a-cutover.md) and
[Phase B record](node24-phase-b-cutover.md) document fresh restored checkpoints,
runtime/SDK tests, migrations, live no-delivery canaries and restored services.
Homebrew dependencies and unrelated Node22 consumers remain unchanged. Recovery
sets remain protected under the approved64GiB budget for at least30 days after
acceptance. Use the reviewed recovery gates, not a package-only downgrade: old
OpenClaw must not open the migrated production databases.

**Historical 2026-10-09 maintenance:** the target was pinned `2026.8.35` extended-stable,
compatible with the installed Node `22.23.1`. Do not substitute the `latest`
tag: September stable requires a newer Node runtime. Keep the unfinished
Crosstown additive phone-binding changes out of this deployment.

The July-to-August transition requires these additional checks:

- Create and verify a full-state backup, preserve the installed package, and
  separately preserve the generated launch plist, service environment, and FDA
  wrapper. An older package alone cannot undo database migrations.
- Inspect and obtain explicit consent for changed official plugin capabilities;
  pin Codex and iMessage to the core version. Do not blanket-approve other plugins.
- If startup rejects an unowned legacy Tailscale root handler, verify its exact
  upstream is this gateway before replacing only that handler. Preserve the
  independent port-10000 wake-hook route and Funnel settings.
- Update the read-only custom cron readers for compact `cron_jobs` and terminal
  `task_runs` history; see `CRON-JOBS.md`. Preserve newly visible system-owned
  jobs without adding them to the user-managed canonical definitions.
- Compare original job payloads, schedules, delivery routes, and tool allowlists;
  verify channel status and a no-delivery model turn. A working Anthropic fallback
  does not prove that the configured Astra/Codex authentication is healthy.

Recovery checkpoint (2026-10-09): core and the two explicitly approved official
plugins are installed at `2026.8.35`; migrations passed. The verified full-state
backup, exact old package, and protected diagnostic receipts are under
`~/Backups/openclaw-2026.8.35-yidy52u_/`. The original gateway launch contract was
restored byte-for-byte after temporary stderr capture. Gateway health and
iMessage running status pass. Cabin enrollment, scanner approval, and unfinished
Crosstown source changes remain unchanged.

The five custom cron readers/helpers are deployed with hash verification.
Receipt-backed active run IDs are checked exactly; 87 focused Python tests,
the cron-sync shell regression, and repository validation pass. Two broader
prompt-contract tests still expect the superseded `--validate-handoff` wording;
those tests and the canonical job definitions are unchanged. No triage cleanup
rerun or message test was performed. Attended device sign-in completed and the
OpenAI runtime auth route now reports usable. A fresh no-delivery gateway turn
returned `AUTH_OK` from `openai/gpt-6-astra`, with one successful attempt and
`fallbackUsed: false`. Defaults and fallback ordering are unchanged; all 13
original cron definitions still match, alongside three system-owned jobs.

The successful turn reports the `openclaw` harness, not native `codex`. The
existing Astra `compat.supportsUsageInStreaming` setting is an authored request
override under the new runtime selector, which permits exact-request OpenClaw
execution rather than discarding that setting. It was left unchanged; this
check proves Astra authentication and inference, not native Codex execution.
An older unused OAuth profile still reports expired; it was not deleted or
substituted. Keep the verified Astra default and existing fallback ordering;
evaluate native-runtime compatibility separately rather than changing models
as part of this recovery. Private authentication and backup artifacts remain
outside the repository.

#### Native Codex compatibility investigation (2026-10-09)

The installed `2026.8.35` route classifier treats
`compat.supportsUsageInStreaming: true` as authored request behavior. The Codex
harness explicitly declines that route and declares the OpenClaw runtime as
its exact-request fallback, even with `agentRuntime.id: "codex"`. Both the
Astra and Sol provider entries contain this flag; GPT-5.5 does not. Removing
only Astra's flag in memory changes its actual installed harness support check
from unsupported/OpenClaw fallback to supported/Codex, without changing model
defaults. This proves the selection cause, not end-to-end native execution.

A bounded `agent exec` canary used a private config copy, separate state and
empty workspace, denied all tools, selected only Astra, and requested the
freshly authenticated profile. The first attempt stopped at an unresolved
memory-search SecretRef; memory search was disabled only in the canary copy.
The second reached native Codex preflight but failed before inference because
the selected generic profile lacked a ChatGPT account ID.

Source inspection explains why that result is not a valid test of the fresh
login: `withAuthProfileStoreAgentDir` filters the shared store to
`portable-static-credential` entries, excluding OAuth profiles. With the
requested profile absent, auth ordering can select remaining stored profiles.
Do not treat a canary `auth.order` entry as a hard account lock, infer that the
attended login is broken, copy refresh tokens to bypass this boundary, or
request another sign-in based on this result. Live auth still reports usable,
with no unusable profiles. The live config hash is unchanged and gateway
health passes. No tool calls, messages, service restart, or live routing change
was performed by this investigation.

Protected evidence is in `~/Backups/openclaw-native-codex-9tnmYh9m/`, including
offline classifier/harness checks and failed canary receipts. Next, with an
approved live maintenance window, remove only Astra's streaming-usage override
through the supported config path and make one fresh no-delivery gateway turn
using the verified profile. Accept only the actual `codex` harness, Astra,
the expected profile, successful completion, and no model fallback; otherwise
restore the prior config. Preserve all model defaults, fallback ordering,
tool/cron restrictions, and other model entries. Do not roll out Sol's analogous
change without its own verification, or regard a text-only smoke test as proof
that cron/tool workflows remain compatible.

Approved live canary result: **passed**. A fresh gateway session pinned to the
attended-login profile returned `NATIVE_OK` with provider `openai`, requested and
effective model `gpt-6-astra`, actual harness `codex`, one successful attempt,
and `fallbackUsed: false`. The session's native thread binding independently
matched the attended profile. Its terminal receipt lists no successful tools.
The session used read-only permission mode with web search disabled; delivery
was false and the message tool was disabled explicitly. A preliminary attempt
to apply inherited tool-deny policy was rejected because that policy is scoped
to subagent/ACP sessions; no inference or live routing change occurred before
that rejection. No restrictions on existing sessions or cron jobs were changed.

The test removed only Astra's `supportsUsageInStreaming` flag using
`openclaw config unset`, then restored it with `config set` while reviewing the
receipts. The original config is semantically restored (only last-touched
metadata may differ); no model-default or fallback changes remain active.
Gateway health passes. Evidence and the protected config backup are under
`~/Backups/openclaw-native-live-rYa9nv6d/`. Native authentication/inference are
now proven; permanent removal should update the tracked Astra entry and live
config together, followed by separately bounded tool/cron compatibility checks.
Sol remains untested. This canary does not itself deploy a permanent routing
change or authorize rerunning household/email automation.

Approved permanent promotion (2026-10-09): removed only Astra's
`compat.supportsUsageInStreaming` from the tracked `openclaw/openclaw.json`
and the live config. A semantic comparison verifies that no other live setting
changed, apart from last-touched metadata. Astra stays primary, existing
fallbacks stay in order, and Sol's entry stays unchanged. Regression coverage
in `tests/test_native_codex_config.py` protects Astra's explicit native runtime
and compatible model metadata.

Separate bounded verification passed:

- A fresh read-only session used native Codex shell execution to read a
  generated non-sensitive fixture and return its unpredictable value. The
  receipt identifies `codex`, `gpt-6-astra`, and no model fallback.
- A temporary disabled cron job, scheduled in the future and manually run once,
  used an isolated/light-context agent turn with a 120-second deadline,
  `toolsAllow: ["read", "exec"]`, and `delivery.mode: "none"`. Its native
  Code Mode tool discovered and invoked `openclaw__read` for the fixture.
  Scheduler status and completion status succeeded, the returned value matched,
  and delivery was `not-requested`. Native rollout context independently
  confirms Astra; both test rollouts match the previously verified account.
- The temporary job was removed after terminal completion, with no running
  receipt remaining. All 16 pre-existing job definitions and IDs are unchanged.
  Gateway health and iMessage running status pass; no restart, message delivery,
  or household/email job rerun was needed.
- 89 focused Python tests, the cron-sync shell regression, compilation, and
  repository validation passed. This verifies basic native execution and the
  scheduled tool bridge, not every application workflow, mutation, approval,
  long-running continuation, or next morning's organic briefing handoff.

Protected rollout evidence is under
`~/Backups/openclaw-native-rollout-VsXZnvc9/`. The routing fix is live; tracked
configuration and regression coverage preserve the same Astra-only change.

**Historical outline — superseded for the next upgrade.** Preserve this as
history, not an executable runbook: its raw pairing-state output, temporary
backup locations, floating package target and live-job smoke example are not
the approved procedure. Follow the linked Node 24/stable plan and the current
`openclaw-upgrade-plist-overwrite` launch-contract guidance instead.

Historical outline:

```bash
# 1. Backup all OpenClaw plists
ssh dylans-mac-mini 'cp -a ~/Library/LaunchAgents/ai.openclaw.*.plist /tmp/openclaw-plists-backup/ \
  && cp -a ~/Library/LaunchAgents/com.openclaw.*.plist /tmp/openclaw-plists-backup/'

# 2. Backup gateway plist specifically
ssh dylans-mac-mini 'cp ~/Library/LaunchAgents/<the-gateway-plist>.plist /tmp/gateway-plist.bak'

# 3. Snapshot SQLite runtime state; canonical definitions already live in git
ssh dylans-mac-mini 'sqlite3 ~/.openclaw/state/openclaw.sqlite ".backup /tmp/openclaw.sqlite.pre-upgrade"'

# 4. Upgrade
ssh dylans-mac-mini 'PATH=/opt/homebrew/bin:/opt/homebrew/opt/node@22/bin:$PATH; \
  npm install -g openclaw@latest'

# 5. Inspect the post-upgrade launch contract. A generated regular plist is
#    healthy when service-env wrapper -> env file -> FDA app wrapper remains.
ssh dylans-mac-mini 'plutil -p ~/Library/LaunchAgents/ai.openclaw.gateway.plist'

# 6. If that chain is broken, follow openclaw-upgrade-plist-overwrite and
#    regenerate with: openclaw gateway install --force --wrapper <FDA-wrapper>

# 7. Check for new required scopes
ssh dylans-mac-mini 'cat ~/.openclaw/devices/paired.json'
# If "pairing required" / scope-upgrade in audit log, see openclaw-post-upgrade-scope-fix skill

# 8. Watch the current generated-service log
ssh dylans-mac-mini 'tail -f ~/Library/Logs/openclaw/gateway.log'

# 9. Smoke test: trigger a low-stakes cron job
ssh dylans-mac-mini 'openclaw cron run <test-job-id> --timeout 300000 --expect-final'
```

Strong signal to do this in the AM after Julia's 7AM briefing has
finished, so a botched upgrade only loses Dylan's 8AM briefing or
later jobs — not Julia's.

### pinchtab on Mini (Tier 3)

The risk is grocery-reorder breaking silently. Test before committing:

```bash
# 1. Save current
ssh dylans-mac-mini 'PATH=/opt/homebrew/bin:/opt/homebrew/opt/node@22/bin:$PATH; \
  pinchtab --version'  # or wherever the version flag is

# 2. Check the major-version changelog at npm/github for breaking changes
#    https://www.npmjs.com/package/pinchtab

# 3. If clean, upgrade
ssh dylans-mac-mini 'PATH=/opt/homebrew/bin:/opt/homebrew/opt/node@22/bin:$PATH; \
  npm install -g pinchtab@latest'

# 4. Test the highest-impact skill manually:
ssh dylans-mac-mini '<grocery-reorder dry-run command>'

# 5. If broken: roll back
ssh dylans-mac-mini 'PATH=/opt/homebrew/bin:/opt/homebrew/opt/node@22/bin:$PATH; \
  npm install -g pinchtab@0.7.6'
```

### Historical macOS 26.4.1 Mini upgrade (completed)

The commands below record the completed 26.4.1 maintenance window; do not rerun
that version-specific installer. The Mini is on macOS 26.5.1 as of 2026-06-27.

```bash
ssh dylans-mac-mini 'softwareupdate --install --restart --agree-to-license \
  "macOS Tahoe 26.4.1-25E253"'
```

For future macOS upgrades, retain the post-reboot verification:

- Gateway came back: `tail -20 ~/Library/Logs/openclaw/gateway.log`
- LaunchAgents loaded: `launchctl list | grep openclaw | head`
- Presence push: `stat -f '%Sm %N' ~/.openclaw/presence/crosstown-scan.json`
- Tailscale CLI works: `tailscale status` (this is where the LocalAPI
  stale-port issue could re-trigger; see skill)
- `~/.openclaw/.secrets-refresh.env` and `.secrets-cache` survived; the legacy
  `.env-token` is not part of the LaunchAgent/cron credential contract

## Audit cadence

Suggested rhythm:

- **Monthly**: run audit commands, file results in this doc's audit
  history section, do Tier 1 bumps remotely
- **Quarterly**: do one Tier 2 bump if the diff is substantial
- **Opportunistically**: when at Crosstown, do queued Tier 3 work
- **As needed**: macOS minor versions, security CVE responses

A `/schedule` recurring agent could remind us monthly. Not
implemented yet — flag if missed audits become a pattern.

## See also

- `openclaw/plans/gws-0.22-migration.md` — gws migration when we're
  ready to bump it
- `openclaw/plans/archive/system-hardening-2026-04.md` — completed broader hardening sprint
  context
- Skills: `openclaw-upgrade-plist-overwrite`,
  `openclaw-post-upgrade-scope-fix`,
  `tailscale-macos-localapi-stale-port`,
  `1password-cli-launchd-hang`
- `dotfiles/openclaw/workspace/TOOLS.md` — runtime tool reference
  (notes the gws pin)
