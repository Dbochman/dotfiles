# OpenClaw Bin Scripts

Helper scripts deployed to `~/.openclaw/bin/` (and `~/bin/`) on the Mac Mini. These support the OpenClaw gateway, dashboards, device integrations, and maintenance automation.

## Deployment

`8sleep-snapshot.sh` delegates to `8sleep-snapshot.py` at the existing 06:50
schedule. The collector reads only four Eight Sleep keys from the owner-owned
0600 secrets cache (no shell evaluation or `op`), issues one bounded user/date
sleep query per owner, and atomically publishes 0600 JSON in a 0700 directory
at `~/.openclaw/8sleep-snapshots/`. Concurrent runs are locked. Missing data is
distinct from API/credential failure; failures exit nonzero and never publish
fresh success from old data. Logs contain only owner and status, not sleep
metrics or API responses. Deploy both files together; Julia's briefing reads
the new private JSON, while explicit legacy text overrides remain supported.

The usage dashboard imports the adjacent `morning-triage.py` and
`openclaw_cron_sqlite.py` for content-free outcome health. Keep this shared
validator current when deploying the dashboard; it does not run email jobs.

`gmail-batch-modify-result.py` runs directly from this repository. It classifies
captured stdout plus the actual exit code of an already completed Gmail
`batchModify` call; it never calls Gmail. Empty successful responses require
exact-message label readback, not another mutation. It is not a general JSON
parser for reads or draft creation.

Scripts in this directory are tracked in the dotfiles repo and deployed to Mini via `dotfiles-pull.command` (daily LaunchAgent) or manually via `scp`:

```bash
scp openclaw/bin/<script> dylans-mac-mini:~/.openclaw/bin/<script>
```

Per-file `scp` is only for an isolated helper with no paired artifacts. Never
use it for the restaurant coordinator: that rollout includes wrappers, Python,
provider readers, copied skills, the owner-only scope registry, cron
definitions, and the gateway `PATH` contract. Use `dotfiles-pull.command` for
the ordered, mode-correct rollout and its live gateway/cron verification. The
initial installer can seed the coordinator files, copied skills, and protected
scope on a new host, but it neither reconciles live cron nor runs the same
active post-deploy checks.

## Scripts

### Gateway & Maintenance

`security-inventory-host.py` is an attended, read-only survey collector. Invoke
with `python3 -I -S`; it prints metadata only, so redirect output into an
owner-only survey directory. It reads top-level application bundles, Homebrew
versions, selected runtime process names/executable mappings, known Node paths,
installed global npm manifests, explicitly scoped Python environments, Go build
metadata for named clients, managed LaunchAgent definitions and TCP listeners.
It never captures process arguments/environment, imports application packages,
reads secrets/databases/profiles, probes vulnerabilities or changes services.
It is deliberately partial: no recursive home inventory or automatic remote
discovery. Do not treat a missing component or metadata-only result as clearance.
Standalone Node processes are prioritized before application helpers within the
160-process cap; truncation is explicitly reported.
Non-executable Node files are recorded as disabled without attempting to run
them. A mapped Node executable removed from disk remains a running/unknown
observation until the process exits; uninstalling does not hide a live process.

`security-inventory-report.py <private-survey-directory>` combines explicit
`scope.json` asset metadata and host snapshots into private `inventory.json` and
`coverage-report.txt`. Keep scope, raw results and generated reports out of Git.
It separates running executable evidence, installed package metadata,
source-declared/locked dependencies, provider-managed gaps and unknown bundled
versions. The report is inventory evidence, not a CVE assessment. Reconcile
dated operator follow-ups before reusing a survey scope. Neither survey helper
is scheduled. The advisory collector below consumes its dated private output.

`security-inventory-windows.ps1` is an attended, read-only Windows-native survey.
It emits schema-1 JSON for the report normalizer: exact OS build, Defender
platform/engine, recent hotfix metadata, selected app/browser versions, and active
Node executable paths/file versions. It does not read process arguments, account
state or credentials, scan user files, install updates, or restart applications.
Treat its output as private (0700 directory / 0600 file on the Mini). Register
the snapshot in the private scope's `hosts` list, preserve older timestamps,
and remove only that host's obsolete unreachable entry. WSL and offline hosts
remain separate gaps. File versions are not proof of in-memory contents,
backports, application launch paths or exploitability.

`security-advisory-collector.py` is a Mini-only, notification-only daily review.
The attended-installed `com.dylanbochman.security-advisories` LaunchAgent runs
at 07:15 Mini local time (America/New_York), before Dylan's 08:00 briefing.
It reads pwn.watch's complete JSON incident index in bounded discovery batches,
matches against a minimized inventory with high-reasoning, tool-free local
OpenClaw inference (`openai/gpt-5.6-sol`), then fetches up to three approved
publisher pages for each of up to twelve pending matches per run. This uses
existing configured model access; it does not install dependencies or grant
the model shell, browser, messaging or remediation tools. Only product/version,
evidence type, running/source-only state and asset labels enter model inputs;
private source paths, IPs, accounts and freeform survey notes are excluded.

Publisher domains are code-reviewed, proxies/automatic redirects are disabled,
and at most three redirects are followed after revalidating every destination
against the same exact-domain/HTTPS/public-DNS rules. Responses are
size/time limited. Source material is untrusted data. Assessment references must
use fetched URLs and exact supporting excerpts. These checks establish citation
provenance, **not infallible model reasoning or proven exploitability**. No usable
primary evidence means unknown. Source-only dependencies, old observations,
unknown bundled versions and provider-owned infrastructure never become a fleet
clearance. Adding a publisher requires review; an unsupported publisher stays
unknown rather than broadening network access from feed instructions.
When cards link only news coverage, a fixed CVE Program API endpoint supplies
the assigning CNA's record for up to two syntactically valid CVE IDs, within
the same three-source budget. Kernel-maintainer archives and GitGuardian's
original GhostAction research are explicitly reviewed publishers too.

Private atomic state is in `~/.local/state/security-advisories/` (0700/0600).
The worker lock excludes concurrent collections; each pass is limited to 30 minutes.
Completed discovery batches and successful verification results are atomically
saved to a separate private `checkpoint.json`. The briefing never reads this
working file. Each run fetches and validates the current feed; it reuses individual
discovery decisions (including nonmatches) only for unchanged inventory/advisory
inputs, within 24 hours. Feed growth or reordering does not discard compatible
work. Model/prompt contract changes invalidate checkpoints; when changing other
review semantics, bump the internal contract version too. Verified findings are
reused only for matching inputs/observation dates within their normal review TTL.
No raw publisher pages or model responses are checkpointed.

After 24 minutes, no new discovery batch or advisory review starts. Incomplete
discovery exits cleanly with `status=paused` and `collection_paused` in the briefing,
preserving the last published cache. After full discovery, a time-limited review
pass may publish validated findings with an explicit remaining backlog. The hard
30-minute deadline still reports failure; completed checkpoint work survives for
the next daily or attended one-shot run. An in-flight unit may need to be repeated.
Model-process cleanup is also time-bounded, including when a detached descendant
holds a pipe open; cleanup must not swallow the original collection deadline.
The twelve-review cap remains per pass. The daily LaunchAgent now supplies
`--continue-pending`: a locked supervisor runs at most three passes per local
07:15-to-07:15 window, with 60 seconds between passes. That permits at most 36
advisory reviews (each with at most two model attempts) and roughly 92 minutes
of collection/backoff plus bounded cleanup
per window. `continuation.json` reserves each pass before starting it, so crashes
and restarts do not reset the allowance. The next daily window resets the budget;
there is no KeepAlive or extra scheduled trigger. Ordinary manual invocations
without this flag remain one-shot and are not automatic continuation.

Continuation requires a saved checkpoint change and unfinished work: paused
discovery, a verification backlog, or a model/collection timeout. Completion,
no saved progress, schema/transport/storage failures, operator termination, or
the three-pass cap stops the supervisor. Stopped/blocked/capped cycles remain
stopped for that window even after a restart; inspect rather than clearing their
ledger to evade the cap. The published cache and checkpoint are never deleted.
Progress and the briefing expose continuation status/counts; no new notification
delivery or host remediation is performed. The supervisor may still be working
at briefing time, when the briefing uses the last published cache and backlog.
Discovery supplies explicit observed-product and unverified-asset allowlists;
its product/ecosystem/state/asset table omits versions and evidence strings to
stay within the input budget. Primary-source verification still receives the
full version and evidence rows for each match.
Unverified product hints must be represented by their asset IDs, not invented
observed components. Batches contain at most 20 advisories (previously 50).
Each complete batch is strictly validated before acceptance.
Invalid JSON or selection structure/names gets one corrective retry of that batch,
within the same deadline. Nothing is silently dropped or guessed; a second invalid
response fails collection and preserves the prior cache. Model-call timeouts get
one retry in discovery and verification; discovery correction and timeout retries
share a two-attempt ceiling, not nested retry budgets. The 30-minute hard deadline
and operator stop are never retried within a pass. Other transport failures are
not retried here. Progress exposes only attempt counts and fixed rejection codes,
never rejected model output. The daily LaunchAgent has no KeepAlive retry loop;
attended runs must use one-shot mode or the same capped continuation flag, not
an external continuously restarting job.
Inspect live operational progress without network/model calls:
`python3 ~/.local/libexec/security-advisory-collector.py --progress`.
This reports discovery batches, source-fetch/model-review stages, attempted reviews
(out of at most twelve), errors, elapsed time and remaining 30-minute deadline budget.
It also reports reused discovery counts, resumed reviews at reconciliation, and
whether a completed pass stopped starting work at its soft time budget.
`seconds_since_update` measures the last stage transition, not a heartbeat or proof
of a stall; `liveness_verified` is false. A model call can take up to four minutes.
The deadline is a timeout budget, not an ETA. No advisory content, asset identifiers,
paths or model output enters progress. Older running collectors expose their existing
phase/counters but lack start timing and finer stages until the next invocation.
`--status` remains the cache-only briefing view. Neither command starts collection.
Failed collection preserves the prior cache and separately updates health.
Per-advisory failures retain old findings and count toward backlog. Unchanged
inputs are reverified after seven days (unknowns after 24 hours); changed feed/inventory inputs are queued
again. Discovery omission never resolves a prior finding. Changed/expired
affected or possibly-affected findings are reconciled first, then backlog is
oldest-check first, with twelve reviews/run. Model selection is advisory, not completeness proof.
The existing GitHub runner readiness watch remains separate and authoritative
for its deployment gate.

The morning helper reads this cache only, including when mail is unavailable.
`dylan-morning-briefing-data.py --security-status` inspects just that section,
without Gmail, Calendar, network, model calls or delivery. Cache older than 30
hours is stale; inventory older than seven days explicitly needs an attended
refresh. The current plist names the October 22 inventory: after a new scoped
survey, update that exact argument and reload only this LaunchAgent. Do not
redate old observations or treat a successful advisory poll as a fleet resurvey.

Only outstanding assessments enter the briefing. `not_applicable` findings are
excluded even when newly changed; completed maintenance is not a daily recap.
New/materially revised outstanding assessments remain eligible for 48 hours
(tolerating a missed briefing); stable unresolved findings are consolidated into a count.
This is not a confirmed-delivery ledger. Reading the cache never marks anything
delivered. Five cached changes maximum plus additional-change/backlog counts
bound the briefing input; its prompt prioritizes at most three. Neither the
collector nor the briefing patches, retires, restarts, merges or sends test alerts.

Attended verification can reconcile a stale finding before the collector finishes:
`security-advisory-collector.py --record-resolutions /absolute/private/receipt.json`.
This explicit local import never invokes collection, a model, or delivery. The
receipt must be owner-owned mode 0600 JSON with `schema_version: 1` and a
`resolutions` list. Each entry needs `slug`, `finding_hash` (the helper's `digest`
of the complete cached finding), the exact complete `asset_ids`, timezone-aware
`verified_at`, a concrete `reason`, and nonempty `evidence` references to the
attended checks and publisher guidance. Do not resolve a multi-asset finding when
only one asset is cleared, or use an accepted risk as proof of resolution.

The importer verifies each snapshot under the collector lock, then atomically
merges an owner-only `resolutions.json` ledger. It does not edit assessment
history, checkpoints, health, backlog, or continuation allowances. The briefing
omits an exact resolved snapshot from changes and outstanding counts; it does
not emit a completed-work recap. Any changed finding (including new inventory,
assets, or reassessment timestamp) invalidates that receipt; it is not a permanent
slug mute. Receipts expire after seven days. Invalid/unreadable evidence fails
open to displaying the findings with a warning, not silent suppression. Unknown
findings, coverage warnings, and the collector's actual verification backlog
remain visible. The collector never generates or imports these attended receipts.

Scoped deployment: install the collector as 0644 in `~/.local/libexec/`, keep
the tracked morning helper and cron prompt together, create its 0700 state
directory before loading the plist, and update **only** the existing Dylan
briefing message via `openclaw cron edit`. Preserve schedule/model/delivery and
verify live message equality. Do not use a wholesale cron sync or gateway
restart. To pause collection, boot out only
`gui/$(id -u)/com.dylanbochman.security-advisories`; preserve its private state.

`github-runner-readiness.py` is an attended-install, Mini-only seven-day watch
for website PR #339. Its separate installed copy is
`~/.local/libexec/github-runner-readiness.py`, not an OpenClaw job. Each hourly
poll reads public GitHub release metadata through the maintained CA bundle and
checks the release's immutable source commit for Node 24 >= 24.22.1. It never
executes downloaded code, enables CI, changes protection, merges, or deploys.
Source evidence produces only a validation candidate, not runner clearance.

Protected state and bounded logs live in `~/.local/state/github-runner-readiness/`.
Use `python3 ~/.local/libexec/github-runner-readiness.py --status` for a no-network
status read. A private lock serializes polls; atomic state preserves the original
seven-day deadline. Polling stops on a candidate, closed PR or expiry. One PR
comment each announces startup, candidate, three consecutive failures or expiry,
using existing `gh` authentication without printing credentials. Ambiguous send
outcomes are reconciled against comment markers; terminal notification retries
stop after one day. GitHub comment acceptance is not proof of email delivery.
The LaunchAgent remains loaded but terminal invocations perform no network work
after notification handling. Unload this exact label when retiring the watch:
`launchctl bootout gui/$(id -u)/com.dylanbochman.github-runner-readiness`.
Do not erase its state to silently restart or extend the watch.

TLS incident procedures and host-specific verification are recorded in
[`dependency-maintenance.md`](../plans/dependency-maintenance.md#emudhra-tls-incident-response--2026-10-20).
Run `python3 openclaw/bin/tls-revocation-probe.py` from the checkout with the
actual application interpreter (`--certifi` for its separate Requests bundle).
The probe performs credential-free TLS handshakes only; exit 0/1/2 means
expected rejection / confirmed acceptance / inconclusive. It is an operator
diagnostic, not a scheduled job or a blanket browser/app attestation.

The gateway and Node-based clean-environment helpers select the maintained
Homebrew bundle using `SSL_CERT_FILE` and `NODE_OPTIONS=--use-openssl-ca`.
Do not reintroduce `NODE_USE_SYSTEM_CA` or `NODE_EXTRA_CA_CERTS`. Curl helpers
also set `CURL_CA_BUNDLE`; Nest gRPC sets `GRPC_DEFAULT_SSL_ROOTS_FILE_PATH`.

| Script | Location on Mini | Description |
|--------|-----------------|-------------|
| `openclaw-refresh-secrets` | `~/bin/` | Attended exact-field refresh of `~/.openclaw/.secrets-cache` plus the optional complete dedicated finance cache; run with `--interactive` after key rotation, never from launchd/cron. |
| `oauth-refresh.sh` | `~/.openclaw/bin/` | Isolated enterprise Claude OAuth refresh using the config-scoped Keychain entry for `~/.openclaw/claude-automation`, with private-file fallback only when that entry is absent; explicit attended `--login`, serialized bounded refresh, validated atomic consumer-cache publication, no personal login/cache fallback. See `LAUNCHAGENTS.md` before migration. |
| `ola-webhook-bridge.py` | `~/.openclaw/bin/` | Loopback-only Ola callback boundary. Verifies the raw-body HMAC, discards the external envelope, and forwards one fixed content-free wake to OpenClaw with a separate private hook token. |
| `ola-webhook-bridge-wrapper.sh` | `~/.openclaw/bin/` | Cache-only LaunchAgent wrapper for the Ola bridge; enforces protected-cache ownership, exports only the two required credentials into a clean environment, and never invokes `op`. |
| `openai-memory-key` | `~/.openclaw/bin/` | Exec secret-provider adapter for memory embeddings. Emits only the existing gateway-exported `OPENAI_API_KEY`, admitted by the provider's exact `passEnv` list; no auth-database lookup or login-token reuse. Never log or invoke its secret output directly for diagnostics. |
| `pinchtab-headless-instance` | `~/.openclaw/bin/` | Acquires, inventories, scopes, and releases managed headless PinchTab instances. Acquisition requires the browser-level trust gate below; it never navigates a visible browser. |
| `pinchtab-browser-trust.cjs`, `pinchtab-browser-trust-port.py` | `~/.openclaw/bin/` | Paired acquisition dependencies: resolve the selected instance's CDP port and check Chromium trust in an ephemeral context with no account state. |
| `cielo-auth.py` | `~/.openclaw/bin/` | Canonical Cielo `/web/token/refresh/1` client. Serializes rotating-token use, atomically replaces the protected config, and emits only safe operational metadata. |
| `cielo-reauth` | `~/.openclaw/bin/` | Capture-first attended Cielo recovery. Owns one visible dedicated-profile instance, requires a refresh token from that exact login, proves one API rotation plus status, and cleans up only its own state. |
| `presence-cabin-enroll` | `~/.openclaw/bin/` | Attended, operator-only Starlink identity enrollment and verification helper. It has no LaunchAgent or agent route, keeps raw IDs in owner-only staging, requires downstream jobs unloaded, and activates the protected Cabin binding only after the documented evidence gate. |
| `presence-cabin-mesh-enroll` | `~/.openclaw/bin/` | Attended, operator-only migration from the protected controller-only Cabin binding to source-specific schema v2 bindings for the controller and its mesh node. It verifies exact node-local identities and off-to-on evidence, machine-checks the installed v2-capable scanner before activation, retains an exact owner-only v1 backup, and provides an attended exact rollback if the canary fails. It has no LaunchAgent, cron, gateway route, or unattended entry point. |
| `presence-crosstown-canary` | `~/.openclaw/bin/` | Runs one operator-only, downstream-disabled Crosstown strict-scanner shadow sample. It streams but never installs the candidate, reduces protected legacy state and live strict output to safe booleans on the remote host, detects canonical-state races, and has no scheduler or approval writer. |
| `opentable-reservations` | `~/.openclaw/bin/` | Reads the authenticated OpenTable account's upcoming reservations through its dedicated managed profile. It requires an exact protected account/token binding plus affirmative full-list evidence, rejects pagination, virtualization, opaque cards, and document-level login overlays, and emits only normalized token-free facts without reservation IDs. A same-account browser-token rotation permits one `--persisted-session-only` refresh followed by fresh page/binding checks; no email login or reservation mutation is allowed. Local credential caches can change. Deploy with the paired refresh helper and skill. |
| `opentable-refresh-token.sh` | `~/.openclaw/bin/` | Refreshes the OpenTable CLI token in a managed headless PinchTab instance. It matches the dashboard's hash-only account identity to the protected expected account, validates the exact token through a read-only API call, then writes token and attestation with atomic file replacements. A matching first-run session bootstraps without OTP; Gmail OTP is used only when browser identity cannot be proven. `--persisted-session-only` forbids that email fallback and fails closed. Scheduled mode permits one bounded retry in six hours and writes credential-free, owner-only stage status for health reporting. It never logs identity, fingerprints, or token material. |
| `restaurant-book` / `restaurant-book.py` | `~/.openclaw/bin/` | Runs a tracked standing-authorized dinner scope across both Resy and OpenTable with dual-account idempotency guards, deterministic selection, one total mutation boundary, and token-free receipts. The protected scope registry is deployed to `~/.openclaw/restaurant-bookings/scopes.json`. |
| `openclaw-weekly-report.py` | `~/.openclaw/bin/` | Generates the weekly cron report from durable session/cron records and live service checks; avoids obsolete transient gateway-log parsing. |
| `icloud-shared-album-ingest.py` | `~/.openclaw/bin/` | Daily deterministic downloader for configured public iCloud Shared Albums. Uses only unauthenticated public endpoints, atomically records per-inbox GUID state, and emits no event or notification. |
| `cabin-photo-catalog.py` | `~/.openclaw/bin/` | Daily post-ingest Cabin photo reconciler. Preserves human-curated metadata for decisive visual matches, quarantines ambiguity, model-tags up to 50 new post-cutoff derivatives per run with a closed vocabulary, atomically publishes an owner-only producer catalog, and reports content-free delta, duration, health, and structured backlog metadata. |
| `dylan-morning-briefing-data.py` | repo path (cron invokes it directly) | Collects Dylan's owner-scoped handoff, dated seven-day Calendar, verified post-triage arrivals, and recent inbox metadata. Bounds inventories and header samples; distinguishes exact counts from incomplete lower bounds. Supports local `--validate-handoff` without collecting sources. |
| `julia-morning-briefing-data.py` | repo path (cron invokes it directly) | Collects Julia's validated same-day handoff, Calendar, cached sleep, aggregate finances, and post-triage unread metadata. Separates verified snapshots from optional-review failures. Uses the shared handoff validator and reminder presenter; legacy version 1 remains readable. |
| `morning-triage.py` | repo library | Shared strict handoff validation, durable publication, receipt verification, and reminder presentation. Protected files replace truncated cron summaries; compact receipts bind owner/job/store/run/date/hash. Latest-run failures cannot fall back to earlier success. No mailbox calls. |
| `julia-inbox-review.py` | repo path (pending scheduled deployment) | Shared bounded read-only metadata collector, defaulting to Julia. `--scope actions` covers read/unread Action/Urgent threads; `--scope backlog` is on demand only. Sequential reads, no retries or mutations, at most 20 sampled threads. Keep sensitive output private and out of Git. |
| `dylan-inbox-review.py` | repo path (pending scheduled deployment) | Thin wrapper selecting `DYLAN_EMAIL` explicitly for the shared read-only collector. Missing identity fails closed, never falling back to Julia or a default account. |
| `sag-wrapper` | `~/.openclaw/bin/` | Wraps `sag` (speech audio generator) with 1Password env injection for ElevenLabs API key. |
| `send-audio-briefing` | `~/.openclaw/bin/` | Generates TTS audio via ElevenLabs (`sag-wrapper`) and sends as an iMessage attachment via `imsg`, plus optional summary text. Used by Julia's morning briefing cron job. |
| `reachyctl` | `~/.openclaw/bin/` and `/opt/homebrew/bin/` | Sends constrained status, movement, proactive speech, microphone mute/unmute, and camera commands to ClawBody through the always-on Crosstown MBP relay. On that MBP it uses the dedicated Reachy SSH identity and owner-only Unix control socket directly. Face tracking is automatic while the user speaks. |
| `desktop-compute` | `~/.openclaw/bin/` and `/opt/homebrew/bin/` | Stages private inputs to fixed Linux or Windows job roots on the RTX 5090 desktop, hash-binds approved scripts, monitors isolated tmux sessions, and verifies returned artifacts through a restricted forced-command SSH identity. |
| `remote-splat` | `~/.openclaw/bin/` and `/opt/homebrew/bin/` | Prepares long videos, stages private photo datasets or hash-bound large-file Taildrop receipts to the RTX 5090 desktop, hash-binds one immutable COLMAP/Brush phase per job, monitors retained tmux sessions, verifies returned `.sog`/`.ply` artifacts and private `.webp` QA renders, and prepares but never performs a SuperSplat upload. |

### Dashboards

| Script | Port | Description |
|--------|------|-------------|
| `nest-dashboard.py` | 8550 | Home climate dashboard — Chart.js UI over JSONL history. Serves thermostat/AC temperatures, Wave Enhance CO2/VOC data, humidity, weather, and presence over the home LAN and Tailscale tailnet. Presence cards poll the cached API every minute independently of five-minute chart refreshes. |
| `usage-dashboard.py` | 8551 | OpenClaw usage dashboard — token consumption, utilization, agent activity, named cron jobs, and native iMessage health over LAN/Tailscale. Separates component readiness from recent inbound evidence and log activity from execution; interval deadlines remain unknown without scheduler evidence. Reports metadata-only message timing without a new daemon or recovery actions. |
| `dog-walk-dashboard.py` | 8552 | Dog walk history, Fi route maps, coverage/heatmaps, and return-signal telemetry over the home LAN and Tailscale tailnet. |
| `roomba-dashboard.py` | 8553 | Crosstown/Cabin Roomba status, command, snooze, and run-history dashboard. |
| `cat-dashboard.py` | 8554 | Cat profiles, care stations, feeder-transfer state, and one combined timeline of named litter visits, successful scheduled feedings, and confirmed cat-home moves. |
| `home-dashboard.py` | 8558 | Home Control Plane status and command dashboard across both locations. |
| `finance-refresh.py` | — | Daily 06:15 orchestrator that runs the cache-only Plaid and crypto wrappers sequentially, retries each once, and writes combined protected status without reading source credentials or data. |
| `weekly-financial-scrape.py` | — | Sunday 04:05 deterministic cache-only HTTP-first scraper orchestrator. Before credentials, browsers, or data work it reads the verified bounded canonical owner-only repo `.env` through one file descriptor and retains only `TESLA_EMAIL`, requires the repo child to emit the exact `FINANCE_SCRAPER_CONTRACT 2` line and exact compact seven-source capability manifest, validates provider modes, then validates the dedicated credential cache. It pins every normal merge to `--wrapper-contract 2`, assigns one run ID to every normal scraper and guarded import, and accepts a successful artifact only with one compact `FINANCE_SCRAPER_STATUS` object whose exact `contract`/`source`/`path` fields match the closed per-source allowlist. Missing, duplicate, malformed, mismatched, or unknown markers skip import; validated browser fallback may import but makes the final status degraded/nonzero. Exact provider-owned auth lines gate one scoped re-auth child for Eversource, National Grid, BWSC, or PennyMac; Tesla has no standard re-auth and BoA keeps its exact-profile raw-CDP state machine, including short post-bootstrap settling retries. Every child receives a closed runtime allowlist, every Python child has dotenv loading disabled, only Tesla receives its identity, and only one guarded re-auth child receives a selected credential pair. The helper never reads `.env-token` or invokes `op`; it captures aggregate child stdout/stderr only in memory under a 64 KiB ceiling, requires strict UTF-8, and rejects/discards invalid output before auth recovery or import. It always fully drains the complete child process group before returning from each child attempt, binds BoA to the acquired headless `finance` profile, and atomically writes safe owner-only final metadata to `~/.openclaw/financial-dashboard/weekly-scrape-status.json`. Every nonhealthy final status attempts one idempotent, strict, owner-only per-run alert handoff and records `alert_handoff` as persisted or failed; healthy runs create none. The exact-argv command cron propagates helper failure directly to its bounded job-level alert. `--preflight` checks configuration without browser or data mutation; attended `--recover-source boa` reruns only BoA and reconciles that source in the existing weekly status. |
| `financial-scrape-alert-notifier.py` | — | Delivery-only consumer for `~/.openclaw/financial-dashboard/weekly-scrape-alerts/`. It validates owner/mode/schema/bounds, reads only an exact numeric Dylan chat assignment from a scoped value or one verified cache fd, and sends one strict bridge-required `/opt/homebrew/bin/imsg rpc` request through bounded stdin/output and process-group capture. Records transition `pending` → `inflight` → `sent`; confirmed sent state precedes cleanup so delete failure cannot resend, while failed sends retain 15-minute-to-six-hour backoff. Invalid/orphan entries move to a private sibling quarantine, safe health is persisted separately, and the command cron propagates nonzero/timeout/output failure to a cooldown-bounded job alert. The remaining send-before-sent-state crash window is explicitly at-least-once. It has no scraper/import/browser entry point; `--canary` sends one fixed attended test message without touching the queue or financial work. |
| `financial-dashboard-plaid-sync.py` | — | Daily cache-only Plaid sync wrapper for the separate financial-dashboard repo. Reads protected local caches, never calls `op`, serializes runs with a lock, refreshes local income-source review candidates through `update_data.py sync`, and writes status-only metadata to `~/.openclaw/financial-dashboard/plaid-sync-status.json`. |
| `forecast-crypto-sync.py` | — | Cache-only Coinbase/Etherscan holdings component used by the unified finance refresh; preserves the last known-good holdings cache and writes protected component status. |
| `forecast-ledger-capture.py` | — | Daily post-sync Forecast wrapper. Calls only the loopback aggregate-observation endpoint, retries short service outages, and writes status-only metadata to `~/.openclaw/forecast-dashboard/forecast-ledger-capture-status.json`; never calls `op` or reads Plaid data directly. |

BoA weekly recovery recognizes the secure-host logout promotion as signed out,
leaves that tab intact, and seeds one fixed sign-in tab if needed. It still
requires a fresh `not_authenticated` probe before its single credential attempt;
an explicit logout during that attempt fails closed as `login_rejected`.
The paired financial-dashboard scraper recognizes the current `/accounts-home/`
landing through visible signed-in controls, not the route alone. Deploy both
repositories together; see [BoA operations](../FINANCIAL-DASHBOARD.md).

### Nest Integration

| Script | Description |
|--------|-------------|
| `nest` | CLI wrapper for Google Nest SDM API + Open-Meteo weather. Handles OAuth token refresh, protected exact-resource still/video capture, thermostat status, history recording, and dashboard management. |
| `nest-camera-snap.py` | Captures a JPEG frame or bounded H.264 MP4 from a Nest camera via WebRTC (SDM API). Uses `aiortc`, pins the working H.264 profile, and patches Nest's non-standard ICE candidates. |
| `nest-camera-image` | Creates exact-resource, owner-only, short-lived Nest request stills or 1-30 second videos for native OpenClaw delivery; validates JPEG/MP4 structure and exposes token-scoped cleanup without arbitrary paths. |
| `nest-activity-reviewer.py` | Presence-gated Cabin reviewer. Captures a temporary live frame, retains only privacy-safe analysis diagnostics, and sends qualifying text through explicit `imsg rpc` bridge transport with a validated receipt and one-attempt-per-hour limit. |

### Reolink Integration

| Script | Description |
|--------|-------------|
| `reolink-camera` | Thin wrapper for safe status, exact-channel fresh stills, token-scoped image commentary, protected Dylan/Julia/household sharing, and verified manual spotlight state through the pinned local Home Hub binding. |
| `reolink-camera-enroll` | Attended operator-only enrollment for one exact private-LAN Hub channel. It requires pinned TLS and exact `TRUST`, verifies the approved local account with exact-channel status and a fresh in-memory snapshot, and only then activates owner-only generation-matched binding and credential state. Public runtime commands remain bounded; enrollment, users, firmware, arbitrary API calls, and network configuration stay outside the skill. |

### Midea AC Integration

| Script | Description |
|--------|-------------|
| `midea-ac` | Exact-alias LAN status and once-only verified controls for enrolled Midea ACs, including temperature, mode, fan, swing, features, energy telemetry, and safe device errors. |
| `midea-ac-enroll` | Attended operator-only V3 enrollment. It uses cloud credentials only long enough to obtain and verify each per-device LAN token/key, then retains only an owner-only local binding. |

### Airthings Integration

| Script | Description |
|--------|-------------|
| `airthings` | Read-only exact-device Wave Enhance wrapper. Uses a locked local BLE runtime, delegates fresh reads through the dedicated Bluetooth-authorized `Airthings Reader.app`, serializes connections, caches successful status for five minutes, and omits Bluetooth identifiers and serials from public output. |
| `airthings-history-import` | Attended, dry-run-first Airthings dashboard CSV importer. Apply requires `AIRTHINGS_ALLOW_HISTORY_IMPORT=1` plus `--apply`; affected daily history files are backed up, merged idempotently, sorted, and written atomically. It is not an OpenClaw skill tool. |
| `airthings-snapshot` | Non-model five-minute sampler wrapper. Reads only the exact enrolled Wave Enhance, appends one fresh Airthings-only climate row, and maintains protected status-only health. |
| `airthings-notify.py` | Deterministic Cabin CO2/VOC ventilation policy invoked after sampler append. Pairs alerts with 15-minute recovery of the episode's qualifying pollutants for initial recipients still present. Reconciles ambiguous sends against exact local delivered iMessage receipts without resending. Defaults to shadow; uses fresh positive presence, quiet hours, schema-3 episode/attempt/recovery deduplication, and the existing strict sender. Operator-only initialization/status; never a model or skill action. |
| `nest-history-append` | Shared private JSONL appender for the 5-minute Airthings and 30-minute merged climate jobs. It validates records, uses a common `flock`, and supports exact source/room/timestamp deduplication. |

The installer and managed pull build `~/.openclaw/apps/Airthings Reader.app`
from Homebrew's Python executable plus the tracked Bluetooth usage declaration.
The bundle has its own `ai.openclaw.airthings-reader` identity and must receive
an attended Bluetooth approval on first use (and potentially after a Python
upgrade). Unchanged installations retain the signed bundle. Homebrew files are
never patched; replaced reader bundles remain recoverable beside the app.
No macOS device pairing or Airthings account reset is required. On macOS 27,
the generic Python app returned empty scans even with permission enabled, while
the explicitly declared and authorized reader successfully read the sensor.

See [`../REOLINK-CAMERA.md`](../REOLINK-CAMERA.md) for the private runtime
contract, attended rollout, optional macOS Client role, verification, and V2
boundaries.

### Home Events

| Script | Description |
|--------|-------------|
| `home_event_bus.py` | Durable Ring, presence, August, Nest, vacancy-action, and Whisker household event journal with explicit `shadow` / `limited_delivery` modes: strict source validation, HMAC-minimized atomic spools, single-writer SQLite ingestion, retention, protected owner policy, safe status with explicit active/recovered camera degradation, and bounded read queries. |
| `home-eventctl` | Operator-only wrapper for `init`, `check-config`, producer `enqueue`, `ingest-once`, `status`, access/delivery-attention review, delivery-policy installation, mode changes, and `prune`; producers and policy installation use strict JSON on stdin. |
| `home-events` | Fixed-root, read-only JSON CLI exposed to the OpenClaw `home-events` skill for status, recent activity, incidents, and explanations. |
| `home-event-correlator.py` | Persistent correlator that claims durable consumer rows, applies fail-closed canonical presence context, groups site incidents, records rate-limited shadow decisions or owner-only reservations, schedules camera evidence only under the separately active exact-camera policy, and evaluates separately gated paired-home cat-transfer evidence. |
| `home-event-delivery.py` | Separate one-attempt fixed-template sender for policy-scoped Dylan reservations; rechecks fresh vacancy, validates successful gateway or native-direct iMessage receipts, and reconciles ambiguous results against one exact delivered row in the protected destination chat without resending or retaining Messages content, chat IDs, or receipt GUIDs. |
| `home-event-delivery-wrapper.sh` | Attended-install, cache-only LaunchAgent boundary for the sender with exact protected target/auth resolution and a bounded owner-only log. |
| `home-event-camera.py` | Separate bounded +30/+60 evidence worker for fresh confirmed-vacant canary events; rechecks canonical vacancy before each slot, after claiming a due slot, and immediately before every provider capture, cancelling safely after an arrival or uncertain state; otherwise combines the exact triggering Ring camera (or site front door) with the exact per-site Nest interior camera, retains only a strict aggregate person-visible result plus provider-specific failure codes, immediately deletes every frame, and cannot change alert eligibility. |
| `home-event-camera-wrapper.sh` | Attended-install, cache-only LaunchAgent boundary for camera inference authentication, protected image state, and a bounded owner-only log. |
| `cabin-entry-verifier.py` | Explicitly authorized future-only consumer for an ordered Cabin Ring `driveway` → `front_door` sequence. It schedules exact Kitchen stills at +30/+60 seconds from the front-door event, retains only strict person-visible results, deletes both images, and may send one fixed positive bridge message. |
| `cabin-entry-verifier-wrapper.sh` | Attended-install LaunchAgent boundary for the ordered verifier. It runs cache-only in a sanitized environment with protected state/images and bounded logs; registration and the one-shot occupied-Cabin canary remain operator-only. |
| `home-event-service-wrapper.sh` | LaunchAgent boundary for ingestion, correlation, August and Whisker polling, Nest bridging, and local network-presence enrichment; sanitizes the environment and writes one bounded owner-only operational log. |
| `august-event-adapter.py` | Disabled-by-default read-only August transition poller. Uses the exact protected MBP observe binding, baselines silently, persists safe poll-count/gap continuity, publishes through `home-eventctl` without a mutation path, and propagates only bounded allowlisted observation-stage failures. |
| `nest-home-event-bridge.py` | Disabled-by-default downstream reader of committed Nest listener rows. The first enabled run silently baselines prior rows; later person/motion records publish only normalized aliases, sites, times, and classifications, never media or model text. |
| `presence-local-event-adapter.py` | Disabled-by-default shadow adapter for per-residence network arrivals, inferred departures, and household excursion intervals. It consumes only sanitized exact-binding booleans and cannot change canonical presence or physical state. |
| `vacancy-action-journal.py` | Protected observation-only source journal for actions already owned by `vacancy-actions.sh`. It validates exact canonical-presence causality, records bounded allowlisted outcomes, recovers stale attempts as unknown without retry, and reconciles a marker-backed re-confirmed vacancy by advancing only its evidence cycle. It cannot publish, control a device, change presence, capture media, or send a message. |
| `whisker-event-adapter.py` | Disabled-by-default future-only Litter-Robot observer for the two exact site bindings. It silently baselines provider history, requires continuous paired coverage, publishes only normalized cat-presence classifications, and retains no serials, weights, pet identities, or raw activity. |
| `home_event_action.py` | Exact policy-owned Hue and Petlibro worker. Approved household-relocation feeding follows fresh confirmed vacancy with both residents at the destination; it verifies destination meals before origin-off. Re-confirmed vacancy preserves an existing verified pause's original ownership cycle without a device command or manual-pause adoption. Legacy litter gating remains compatible. `set-feeder-mode` holds both directions without changing devices; `return-feeder-to-automation` explicitly adopts a manual pause with cycle-pinned receipts. |

See [`../HOME-EVENTS.md`](../HOME-EVENTS.md) for the protected runtime,
producer contracts, shadow rollout, and rollback procedure. The subsystem does
not require an OpenClaw gateway restart.

### Usage Metrics

| Script | Description |
|--------|-------------|
| `usage-snapshot.sh` | Collects OpenClaw usage metrics every 15 minutes via LaunchAgent. Fetches Anthropic utilization, reads runtime logs plus SQLite `cron_run_logs`, counts native iMessage rows, and writes 90-day JSONL history. |

### Bluetooth

| Script | Description |
|--------|-------------|
| `bt_run.sh` | Reads a command from `/tmp/bt_command.txt`, runs `bt_connect` with it, writes result to `/tmp/bt_result.txt`. Used as a GUI-context wrapper for Bluetooth operations. |

> **Note**: `bt_connect` is a compiled Mach-O arm64 binary (not tracked in git). It lives only on the Mini at `~/.openclaw/bin/bt_connect`.

### Dotfiles Sync

| Script | Description |
|--------|-------------|
| `dotfiles-pull.command` | Daily git pull and deployment on Mini. Stashes local changes, pulls `--ff-only`, restores the stash, copies skills/wrappers/scripts, publishes required skill wrappers such as `reachyctl` into Homebrew's PATH, atomically installs the protected restaurant scope registry, reconciles cron, activates a changed gateway wrapper, and verifies restaurant skills through the active gateway before succeeding. Auto-closes its Terminal window and runs via Terminal.app for git credential access. |

### Markdown Search (qmd)

| Script | Description |
|--------|-------------|
| `qmd-setup.sh` | One-time setup for `qmd` (Quick Markdown Search) on Mini. Indexes all OpenClaw markdown (workspace, skills, dotfiles) with BM25 + vector embeddings for hybrid search. |

**Package**: `@tobilu/qmd` (npm), installed at `/opt/homebrew/bin/qmd`

**Collections** (4 — deduplicated to avoid duplicate result slots):
| Name | Path | Contents |
|------|------|----------|
| `workspace` | `~/.openclaw/workspace/` | Core instructions, short TOOLS.md compatibility index, on-demand OPERATIONS.md, and HEARTBEAT.md |
| `skills` | `~/.openclaw/skills/` | All SKILL.md files |
| `plans` | `~/dotfiles/openclaw/plans/` | Current plans plus archived architecture and migration records |
| `bin-scripts` | `~/dotfiles/openclaw/bin/` | README.md |

**Usage**:
```bash
qmd query "how does native iMessage health work"  # hybrid search (recommended)
qmd search "cart URL"                         # BM25 keyword search
qmd update --pull                             # re-index after dotfiles pull
qmd mcp                                       # start MCP server for AI agents
```

### Headless browser trust gate

`pinchtab-headless-instance acquire PROFILE` now checks the acquired Chromium
process before returning its instance ID. The paired `pinchtab-browser-trust-port.py`
reads only its managed-instance configuration, validates the bridge-port match,
and resolves the loopback debugging port. `pinchtab-browser-trust.cjs` uses the
pinned Node 24 runtime and built-in WebSocket support; no npm package is needed.
The wrapper resolves its own real path so Homebrew symlinks work too.

The gate creates a disposable browser context, disables page JavaScript and
cache use in four diagnostic tabs, and requires all 12 incident canaries to
fail with Chromium `ERR_CERT_REVOKED`/`ERR_CERT_AUTHORITY_INVALID` plus successful
TLS responses from github.com and pypi.org. It does not read account pages,
cookies, credentials, or shared tabs. Page text, DNS failure, expiry, timeout,
redirects, missing controls, and unavailable tooling never count as clearance.
Up to three public-test attempts allow a short startup-readiness window, with
a 20-second absolute gate deadline. Failure emits safe counts, returns no
instance ID, and stops only an instance created by that acquisition. Existing
instances are left running; this is not an outbound firewall.

Deploy both dependencies before the wrapper. `dotfiles-pull.command` handles
that ordering. If the gate fails, inspect the exact browser's component-update
readiness and rerun acquisition; do not bypass the gate or add certificate-error
exceptions. Canary availability is deliberately an operational dependency.

Scope: this gates new **caller work through this helper**, not browser background
traffic during startup, direct CDP/PinchTab launches, Cielo's separate lifecycle,
native apps, or unrelated TLS implementations. There is no persisted pass cache
and no blanket browser/host clearance. A browser restart after acquisition
requires reacquisition. The diagnostic does not repair the browser's CA data.

### Device Monitoring

| Script | Description |
|--------|-------------|
| `mysa-status.py` | Queries Mysa baseboard heater API for device status (temp, setpoint, mode). Outputs JSON. Uses Cognito auth cached at `~/.config/mysotherm`; an optional cached `Mysa` vault credential renews expired sessions automatically, or run `mysa --login` interactively. |
| `airthings` | Reads the exact Cabin Living Room Wave Enhance over local BLE for CO2, VOC, temperature, humidity, pressure, noise, light, and battery data. No cloud account is used; unattended reads refresh through the authorized `Airthings Reader.app` identity. |
| `airthings-history-import` | Imports UTC Airthings dashboard CSV history into the same Airthings room series without duplicating timestamps. This is an attended operator tool, not a scheduled or model-invocable command. |
| `airthings-snapshot` | Scheduled local-only collector for the exact Airthings binding. It does not call Nest, weather, HVAC providers, or the home-event bus. |
| `nest-history-append` | Serializes all ordinary climate JSONL writes and rejects unsafe history paths. The attended Airthings importer takes the same lock while merging a backfill. |
