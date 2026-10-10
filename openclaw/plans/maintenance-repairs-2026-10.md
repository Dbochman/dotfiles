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

- [ ] **Diagnose and repair the Claude OAuth refresher.** Its scheduled login
  attempts repeatedly return HTTP 400. Establish whether credentials, refresh
  handling, or CLI compatibility are responsible before choosing recovery.
  Preserve credentials and model defaults; never print token values. Validate
  refresh and the affected consumer without messages or household actions.
  **Progress:** Both file-based caches contain credentials that expired
  September 6 UTC; scheduled refresh logs repeatedly report HTTP 400. With
  Node 22, interactive CLI auth reports logged in, but that is not validation
  of the separate automation refresh chain. No token copied or rotated;
  intended-account reconciliation and supervised recovery remain pending.

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

- [ ] **Verify heartbeat recovery after the memory fix.** Its last failure
  was at 09:19 Eastern, before the memory-auth repair, reporting an unresolved
  `memory.search.remote.apiKey`. The inspection showed the next run scheduled
  for 21:19 Eastern on October 10. Check that organic run or its next available
  successor and record the actual outcome. Do not clear error history or force
  a potentially message-producing run merely to make the dashboard green.
  **Progress:** Still awaiting the organic post-repair run. The 09:19 failure
  remains historical evidence; no heartbeat was forced or history cleared.

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
- Changes are uncommitted and unpushed. Unrelated presence and Codex-rule edits
  are preserved; no broad deployment, gateway restart, or message send occurred.
- Rollback copies for the three deployed files are protected in
  `/tmp/openclaw-repairs.Lw00hj/deployment-backup`; this is temporary rollback
  storage, not a substitute for a commit. Do not overwrite the preserved runtime
  decisions or start cleaning to validate the Roomba reporting change.
