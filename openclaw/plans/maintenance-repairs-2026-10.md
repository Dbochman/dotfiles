# October 2026 OpenClaw repair checklist

**Status:** Dashboard and reporting repairs deployed; attended recovery and verification remain open.
**Evidence date:** October 10, 2026, Eastern time.
**Host:** Mac Mini.

Track the repairs identified during the usage-dashboard review. Start with
descriptive cron labels, trustworthy health reporting, and deployment
reconciliation; then address authentication, financial refresh, and device
failures. Recheck live evidence before acting: these observations are a dated
snapshot, not a continuously updated health report.

## Dashboard and deployment

### October 12 Cabin ventilation notifications

- [x] Add a deterministic, shadow-first rule alongside the existing five-minute
  Airthings sampler; gate CO2/VOC duration, fresh positive resident presence,
  quiet hours, and per-episode deduplication. No humidity-only window alerts.
- [x] Validate with 71 focused tests, then deploy only the helper, sampler, and
  Airthings instructions. Sensor collection remains independent of messaging.
- [x] Send the single explicitly approved Dylan test
  `AIR-20261012T141319Z`; record transport submission and his receipt confirmation
  separately, then enable live mode while preserving state. No automatic retry.
- [x] Verify the next ordinary sample (10:15:52 Eastern) reports live mode and
  successful collection; no automatic alert was generated during this check.
- [ ] Observe the first naturally qualifying notification; do not synthesize
  a poor-air event or run another test to satisfy this item.

Full policy, storage, pause procedure, and acceptance criteria:
[Cabin ventilation notifications](cabin-air-quality-notifications.md).

### October 12 cron outcome repairs

- [x] **Repair the post-upgrade triage publication contract for both owners.**
  Applied the shared helper's receipt-backed active-run validation, retaining
  legacy task checks, exact job/store/receipt identity, freshness, completion
  rejection, and the pre-publication recheck. All 96 focused tests pass. Both
  live collectors import the patched source; no cleanup rerun, message resend,
  gateway restart, or historical handoff rewrite occurred. Next organic runs
  remain the end-to-end acceptance check.
- [x] **Handle empty successful Gmail mutation responses correctly.** Dylan's
  triage classified five messages but its ad-hoc parser rejected empty success
  responses, so subsequent read-state/archive work was conservatively skipped.
  Add a bounded shared response contract and regressions for both owners;
  preserve readback verification and never retry ambiguous mailbox writes.
  Added a pure captured-response classifier and both owners' prompt contract;
  empty exit-zero responses still require exact-ID label readback. No mailbox
  writes were replayed; next organic runs remain the behavior check.
  Both targeted live prompts were updated through the gateway and read back;
  schedules, enabled state, delivery, and model settings were preserved.
- [x] **Expose triage outcome health separately from scheduler success.**
  Surface missing/invalid, partial, and healthy same-day handoffs without
  publishing mailbox contents or rewriting historical scheduler results.
  The two triage rows now include a dated, content-free outcome from the same
  receipt validator used by briefings. Pending, unavailable, partial, and
  verified states remain distinct from the last scheduler exit status.
  Deployed with hash verification and a usage-dashboard-only restart; the
  loopback API correctly shows both historical scheduler successes alongside
  today's invalid handoffs. No historical run or receipt was rewritten.
- [x] **Reconcile Flower Watch's stopped state with deployment.** Source was
  enabled while the live job is disabled; repeated 8AM attempts report operator
  disablement. Establish the stop reason, preserve the existing live stop, and
  prevent source deployment from silently re-enabling it. No camera or message
  canary is authorized by this maintenance task.
  Source now preserves the live disabled state. The referenced
  `plant-bloom-watch/scripts/bloom_state.py` is absent from the deployed skill
  and repository; repeated attempts cannot complete. Restoration requires a
  separately reviewed implementation and explicit re-enable, not a retry.
  **Follow-up restoration:** Recovered both original files from the October 11
  Phase B restored backup, verifying each against `source-manifest.json`.
  The state helper is byte-identical to that backup; skill instructions change
  only its managed deployment path and add an explicit completed-watch hold.
  Tracked source now prevents loss of the workspace-only implementation.
  Existing state has both alert flags true, so this was a completed watch,
  not a reason to reset or resume it. Restore code, retain state and disabled
  schedule, and require a separately approved policy for any new watch.
- [x] **Restore or explain Julia's missing sleep snapshot.** Inspect the
  existing 6:50AM producer and identity/freshness contract. Restore truthful,
  private cached input without changing Pod controls or inventing sleep data.
  Reproduced missing Eight Sleep environment in the launchd-like environment;
  the old wrapper silently suppressed that failure. Deployed cache-only,
  bounded user/date queries and private atomic snapshots with explicit status.
  A read-only validation under an empty launchd-like environment returned valid
  current data for both owners; Julia's briefing reader accepts her snapshot.
  Existing schedule is unchanged; no Pod controls, new jobs, or messages.
- [ ] **Observe Eversource's next unattended renewal.** Its October 11 targeted
  recovery succeeded after the historical weekly failure. Do not rerun the
  full scrape or clear that evidence merely to make scheduler status green.

The user authorized working through these repairs and separate commits/pushes.
Live cron edits, new external calls, and scoped service reloads still require
their stated approval boundaries; do not broaden household or mailbox policy.

- [x] **Show descriptive cron titles throughout the usage dashboard.** All 15
  enabled jobs had names, but upcoming jobs rendered shortened IDs instead.
  Use live names for upcoming jobs and available history/chart mappings; retain
  a safe ID fallback for removed or unknown jobs. Escape names when rendering
  HTML. Verify named, unnamed, historical, and system-owned jobs with regression
  tests and a rendered dashboard check. Do not rename jobs or change schedules.
  **Progress:** Implemented and hash-verified live in `bin/usage-dashboard.py`;
  names render in the live dashboard and HTML escaping has an executable JS
  test. Long chart legend titles truncate visually, retaining full tooltip names.

- [x] **Make service health timestamps and error labels trustworthy.** Log
  modification times are not reliable run timestamps, especially for
  `/dev/null`, quiet logs, or shared logs. Report verified run/receipt times
  where available; otherwise label log activity accurately or show unknown.
  Distinguish historical exits from current failures and expose evidence age.
  Test running services, failed scheduled jobs, missing evidence, and stale
  logs. Validate the dashboard APIs and rendered states without rerunning jobs.
  **Progress:** Deployed regular-file-only log activity, unknown interval
  deadlines, and historical exit labels. Also repaired long-range downsampling
  selecting the oldest hourly sample, which caused false staleness and old
  gauge values. Regression coverage preserves all activity and chronological
  cron history while retaining the latest sample.

- [ ] **Repair completed one-shot reconciliation in automatic deployment.**
  The October 10 deployment stopped at cron sync because old one-shot IDs were
  considered unregistered. Determine whether completion evidence is missing,
  expired, or misread before selecting a fix; the exact cause is not yet proven.
  Preserve system-owned jobs, active job state, and completed-job tombstones.
  Never register or rerun old booking or messaging jobs to satisfy parity.
  Add regressions for historical completions and genuinely unknown IDs; review
  a no-side-effect reconciliation preview before any approved deployment.
  A successful Git pull alone does not establish deployment success.
  **Progress:** The 19 expired definitions are absent from the active store
  and retained completion history; historical copies in a different store are
  not completion evidence. Dylan approved retirement rather than resurrection.
  Exact definitions and five associated booking scopes are archived under
  `cron/archive/`, explicitly not verified completed. Read-only ID preview now
  finds 13 canonical jobs, no missing IDs, and no unexpected active-store jobs
  after allowing system-owned declarations. Remaining definitions are unchanged.
  Retirement regressions and the existing cron-sync shell suite pass; a full
  deployment has not been run. The reduced four-job booking-scope registry is
  hash-verified live under explicit approval; no cron definition RPC was needed
  or executed. The next automatic deployment's final outcome remains unverified.
  **October 10 evening:** Repair commit `a236299` is pushed to `origin/main`.
  Read-only active-store parity still matches all 13 canonical jobs. The daily
  deployment is scheduled for 06:00 Eastern; it was not forced during the
  user's movie or while unrelated local edits remain present.

## Authentication and financial refresh

- [x] **Recover Cielo authentication with attended sign-in.** Refresh returns
  HTTP 401 and headless recovery explicitly requires attended authentication.
  Use the supported isolated recovery flow, stop for MFA/security prompts,
  and verify a read-only refresh afterward. Do not issue thermostat commands.
  **Progress:** Agent-session authentication was rejected by the isolated
  instance. Dylan explicitly approved the existing operator-assisted helper
  for Cielo only, without security-setting changes. A new dedicated headed
  window captured a fresh refresh token after Dylan signed in. The finish
  helper successfully rotated it through the API; an independent read-only
  status request returned valid JSON for four devices. Recovery state and
  login backoff were removed, no Cielo instances remain running, and the
  credential file remains mode 0600. Failed preliminary instances were also
  cleaned up. No thermostat actions ran. A prior LaunchAgent failure may remain
  displayed until its next normal scheduled refresh; no job was forced merely
  to clear that historical exit.

- [x] **Diagnose and repair the Claude OAuth refresher.** Its scheduled login
  attempts repeatedly return HTTP 400. Establish whether credentials, refresh
  handling, or CLI compatibility are responsible before choosing recovery.
  Preserve credentials and model defaults; never print token values. Validate
  refresh and the affected consumer without messages or household actions.
  **Progress:** Both file-based caches contain credentials that expired
  September 6 UTC; scheduled refresh logs repeatedly report HTTP 400. With
  Node 22, interactive CLI auth reports logged in, but that is not validation
  of the separate automation refresh chain. No token copied or rotated;
  intended-account reconciliation and supervised recovery remain pending.
  **October 10 evening:** Dylan confirmed NVIDIA enterprise and approved a
  separate automation login, preserving the working interactive CLI login.
  The revised wrapper uses a dedicated config directory, no personal/cache
  fallback, a refresh lock, bounded CLI execution, private validated credentials,
  and atomic consumer-cache publication. Fake-only regressions pass. No manual
  deployment or attended SSO/bootstrap will run during the movie; the pushed
  source is eligible for the next daily deployment but will fail closed until
  its dedicated login exists. No authentication change or browser launch was
  performed during that preparation phase.
  **Verified after the movie:** Wrapper `1802af0` is hash-verified live. The
  first personal/Pro login was rejected without publishing its credentials;
  personal CLI credentials remained unchanged. NVIDIA rejected the Mini's
  browser, so Dylan completed the approved enterprise SSO using Prisma Access
  Browser on `dylans-mac`, with loopback-only SSH forwards returning the callback
  to the Mini. The saved identity matches the requested NVIDIA enterprise
  account. A subsequent unattended refresh passed and atomically published the
  private consumer cache. The usage API returned HTTP 200, and one bounded
  usage snapshot is visible through the live dashboard API. This enterprise
  response has null 5-hour/7-day quota windows; do not invent percentages or
  treat their absence as failed authentication. Both temporary forwards and
  the local handoff listener are closed. No model defaults, personal CLI
  credentials, gateway services, or household controls were changed.

- [ ] **Repair the failing Plaid component of daily finance refresh.** The
  October 10 run finished partial: Plaid failed twice; crypto succeeded.
  Identify the failing connection and safe error category, then choose targeted
  recovery rather than refreshing every account. Preserve prior data and
  holdings/balance safety checks. Keep the 06:15 source refresh and 07:35
  Forecast capture order; require a valid component receipt and downstream
  freshness check before marking resolved.
  **Progress:** A targeted read-only Chase Item probe returned `NO_ACCOUNTS`.
  Dylan confirmed the accounts are active and access should continue. No Item
  removed or relinked. Local financial-dashboard changes now retain safe error
  codes instead of truncated SDK headers; 32 Plaid foundation tests pass.
  Attended account-sharing recovery and subsequent targeted sync remain pending.
  This checkout is the daily sync's source, so the diagnostic change is eligible
  for the next scheduled run without a service restart; no sync was triggered.
  **October 10 evening:** Financial-dashboard commit `27ddf0a` is pushed to
  `origin/master`; all 32 foundation tests pass. A new production Item probe
  still reports `NO_ACCOUNTS`. An existing-Item, account-selection-only Link
  recovery was prepared without new product consent or token exchange. No
  successful completion was recorded; Dylan deferred sign-ins until after the
  movie and the owned loopback listener was stopped. Create a fresh bounded
  Link session when he is ready, then verify account access and targeted sync.
  **Verified after the movie:** Existing-Item account selection completed;
  `NO_ACCOUNTS` cleared and one existing account is accessible. A bounded,
  lock-protected Chase-only balance and incremental transaction sync passed:
  50 additions and three provider-reported removals, with unchanged account
  mapping and no other Item status changes. The source API matches current
  reconciliation data, apart from its per-request timestamp. The protected
  `~/.openclaw/financial-dashboard/chase-recovery-status.json` records targeted
  success; the prior full daily-run receipt is deliberately unchanged. Keep
  this item pending until the next normal 06:15 component run confirms recovery;
  no other provider, full-batch refresh, or Forecast capture was forced.

- [x] **Recover Eversource weekly scraping.** The latest inspected weekly
  receipt shows failed scraping and reauthentication, with import skipped;
  PennyMac succeeded in that receipt. Recheck the receipt timestamp and current
  authentication, then recover only the affected provider. Use attended MFA
  when required, preserve prior data, and verify both scrape and import status
  plus dashboard freshness. Do not rerun the entire financial batch blindly.
  **Verified October 10:** Dylan completed attended sign-in in the explicitly
  approved isolated scraper browser. Browser billing validation and a separate
  direct-HTTP dry run passed before the provider-only session was atomically
  published with mode `0600` and a protected backup. An Eversource-only direct
  scrape and current-run guarded import passed; the live utilities API matches
  all 28 readings through October 2026, with previous history retained. No
  shared browser settings, payments, other providers, or services were changed.
  The protected targeted recovery receipt is in the directory referenced by
  `/tmp/eversource-recovery-current`; the October 4 weekly failure receipt is
  intentionally unchanged. The next scheduled weekly run remains unverified.
  **October 11 follow-up:** The 04:05 weekly run failed only for Eversource;
  the other six sources imported successfully. The saved portal token had
  expired, browser recovery was signed out, and automatic reauthentication
  failed. Reauthentication also ignored the exported provider-only state and
  used an older persistent profile. The financial repository now restores the
  validated state before credentials, uses an empty isolated context when its
  cookies have all expired, and rejects unsafe state. This is the live source
  path used by the scheduler; the fix is committed in the financial repository
  as `0296c03`. Regression and
  safety coverage passes 235 tests. Dylan completed another isolated attended
  login. Independent direct validation, Eversource-only contract-v2 scrape,
  guarded current-run import, database history checks, and live dashboard
  readback passed for 28 readings through October. No other source reran, no
  service restarted, and no MFA/security settings changed. The durable receipt
  is `~/.openclaw/financial-dashboard/eversource-recovery-status.json`; the
  original weekly failure remains historical evidence. The restoration bug
  is fixed, but it is not proven to be the sole cause of the login failure;
  future unattended renewal remains unverified and may still need MFA.

## Household automation and follow-up verification

- [ ] **Investigate the Roomba status-validation failure.** The October 10
  scheduled vacancy run stopped with `robot_status_invalid` and no robots
  started. Compare the supported read-only response with the parser contract
  using sanitized fixtures. Preserve vacancy, freshness, pet-activity, and
  duplicate-action safeguards. Test valid and malformed responses without
  starting cleaning; obtain explicit approval for any actuation test.
  **Progress:** Read-only checks show Roomba connected with a full bin and
  Scoomba disconnected, with cached low battery and mission state. Those cached
  values are not evidence of current activity. The local failure classifier now
  reports `robot_disconnected` explicitly and keeps missing evidence invalid;
  this helper is now hash-verified live under explicit approval.
  Eleven fake-only automation tests pass. Physical bin/dock/connectivity checks
  are needed; no robot commands or daily-receipt resets were performed.
  **Operator follow-up:** Dylan and Julia are at the Cabin; Dylan will inspect
  the Crosstown robots when they return Monday, October 12. Leave this pending
  without remotely starting either robot.

- [ ] **Verify heartbeat recovery after the memory fix.** Its last failure
  was at 09:19 Eastern, before the memory-auth repair, reporting an unresolved
  `memory.search.remote.apiKey`. The inspection showed the next run scheduled
  for 21:19 Eastern on October 10. Check that organic run or its next available
  successor and record the actual outcome. Do not clear error history or force
  a potentially message-producing run merely to make the dashboard green.
  **Progress:** Still awaiting the organic post-repair run. The 09:19 failure
  remains historical evidence; no heartbeat was forced or history cleared.

## Restaurant access recovery — October 10 evening

- Resy recovered through the existing cache-only reservation reader; its
  normal credential refresh succeeded without 1Password. Authenticated profile
  and upcoming-reservation reads passed, with no upcoming reservations.
- OpenTable's initial agent-session 403 was PinchTab's expected admin-route
  restriction, not proof of a production outage. Operator-assisted recovery
  was separately approved. The cached API token was rejected, and a persisted
  session refresh passed, but browser restarts exposed an exact-token binding
  mismatch. Later sign-in was required; Dylan completed it in the dedicated
  Mini browser through Screen Sharing from dylans-mac.
- The changes permit one same-account persisted-session-only refresh before
  a fresh exact-binding/list check. They never request email verification,
  accept a different account, or treat an incomplete list as empty. Missing
  identity receives bounded hydration polling. The patched headless reader
  passed a live post-login read with a complete empty upcoming list.
- Validation: 78 focused reader, refresh, browser-helper, booking, and Resy
  tests passed; repository validation, syntax, and diff checks passed. No
  reservations changed. Dylan approved a narrow three-file deployment exception:
  refresh helper first, paired reader, then skill documentation. All three
  deployed files are hash-verified. Two source cold-start reads and a deployed
  cold-start read passed; all four restaurant skills remain gateway-eligible.
  No gateway restart or cron change occurred. The fix is committed in dotfiles
  as `d0755e7`; unrelated local edits are preserved. The protected temporary
  rollback receipt is located by `/tmp/opentable-deploy-current`.

## Completion and safety

Adding an item here is not approval to deploy, restart services, change account
access, send messages, rerun cleanup, or actuate household devices. Obtain any
required approval for those actions separately. Keep credentials, account
identifiers, message contents, and raw private logs out of this checklist.

For each completed item, record the fix/commit reference, relevant tests,
deployment status, observed live outcome, and remaining limitations. Keep
local validation distinct from deployed verification. Preserve unrelated
working-tree changes and update the relevant operational documentation with
each implementation.

### Validation notes

- Usage dashboard: 31 tests passed, JavaScript syntax passed, isolated browser
  preview and live browser verified named jobs and log-activity semantics.
  Live APIs cover 43 services and 15 named enabled jobs; long-range freshness
  uses the latest sample. Only the usage dashboard restarted; gateway PID stayed
  unchanged. The temporary preview server was stopped.
- Cron SQLite compatibility: 10 tests passed; retirement regression: 1 passed;
  cron-sync shell suite passed; historical restaurant coordinator: 39 passed.
- The cron prompt suite retains two unrelated failures, reproduced against
  unchanged HEAD definitions and tests. Retirement-specific failures are fixed
  by keeping historical safety fixtures explicitly separate from live scopes.
- Repository validation, Python compilation, and diff whitespace checks passed.
- The dashboard/retirement repair batch is committed and pushed as `a236299`;
  financial diagnostics are committed and pushed as `27ddf0a`. Unrelated
  presence and Codex-rule edits are preserved; no broad deployment, gateway
  restart, or message send occurred. The isolated OAuth follow-up is source
  preparation only until attended bootstrap and live verification are complete.
- Rollback copies for the three deployed files are protected in
  `/tmp/openclaw-repairs.Lw00hj/deployment-backup`; this is temporary rollback
  storage, not a substitute for a commit. Do not overwrite the preserved runtime
  decisions or start cleaning to validate the Roomba reporting change.
