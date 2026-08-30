# OpenClaw Cron Jobs

Reference for the canonical cron definitions in this repository and the live
SQLite-backed scheduler on the Mac Mini.

## Editing & reading jobs

### State lives in SQLite now

OpenClaw 2026.6 migrated executable cron state and run history to SQLite:

| Layer | Location | Updated by |
|-------|----------|------------|
| **1. Repo (canonical intent)** | `~/dotfiles/openclaw/cron/jobs.json` | git commits |
| **2. Live definitions and runtime** | `~/.openclaw/state/openclaw.sqlite`, table `cron_jobs` | gateway cron API and the deploy bridge |
| **3. Gateway scheduler** | in-memory timers loaded from `cron_jobs` | gateway process |
| **4. Run history / one-shot tombstones** | the same SQLite database, table `cron_run_logs` | gateway after each run |

Files named `~/.openclaw/cron/jobs.json.migrated`, `jobs.json.bak*`, and
`runs/*.jsonl.migrated` are historical migration artifacts. They are not
executable scheduler state and must not be copied back into place as a recovery
shortcut.

The repository remains the durable definition record, but SQLite changes how
deployment works. `sync-cron-jobs.sh deploy` filters completed
`deleteAfterRun` jobs using `cron_run_logs`, stages the remaining definitions,
reconciles changed existing definitions through the live gateway, then imports
new IDs through `openclaw doctor`. Only drifted fields are patched, so payload
or delivery edits preserve runtime state and retry backoff while schedule or
enabled edits recalculate `nextRunAtMs`. The daily deployment runs at 6 AM; a
manual `dotfiles-pull.command` deploys immediately. Deployment returns nonzero
when protected identities are unavailable or SQLite normalization fails; a
caller must never report those cases as a successful rollout. It also reads
SQLite and the active Gateway back after reconciliation and requires both live
ID sets to match the deployable canonical set exactly, then verifies every
canonical field. Missing, extra, or field-drifted jobs therefore fail the
deployment instead of letting a successful-but-no-op RPC mask stale work. The
script reports extra IDs but does not silently remove them; inspect an unknown
job before removing it through the cron API and the canonical file.

### CLI commands

> - **Edit:** `openclaw cron edit <id> ...` — updates gateway memory and SQLite immediately; mirror the change in repo `jobs.json`.
> - **Read:** `openclaw cron list --all --json` — authoritative for the current scheduler cycle.
> - **Add / disable / enable / remove:** `openclaw cron add|disable|enable|rm` — also make the corresponding repo change.
> - **Deploy canonical definitions:** `~/dotfiles/openclaw/sync-cron-jobs.sh deploy` — jobs only; it does not deploy restaurant scopes.
> - **Snapshot live definitions:** `~/dotfiles/openclaw/sync-cron-jobs.sh save` — copies the current executable definition set with runtime state stripped and protected identities redacted, so already-consumed one-shots are normally absent. Always review the resulting diff; live drift is not automatically canonical intent.

### Every change still has two durable planes

The old pre-2026.6 ~60-second JSON persistence gap no longer applies: cron API
mutations synchronously persist to SQLite. A CLI-only change is nevertheless
incomplete because the repository would still describe different intent and
could restore it during later recovery or re-creation.

Make every cron-job change in both places:

```bash
# 1. Apply through the gateway so validation, SQLite, and the live timer agree.
openclaw cron edit <job-id> --message "..."

# 2. Mirror the same change into the canonical repo and push.
$EDITOR ~/dotfiles/openclaw/cron/jobs.json
cd ~/dotfiles && git add openclaw/cron/jobs.json && git commit && git push
```

For schedule edits, always pass the schedule through `openclaw cron edit` even
when the repo already contains the desired value. This forces the gateway to
recompute `nextRunAtMs`; `sync-cron-jobs.sh deploy` now performs the same repair
when it detects schedule-identity or one-shot timestamp drift.

Verify all three views before considering a change complete:

```bash
# Gateway / in-memory view
openclaw cron list --all --json | jq '.jobs[] | select(.id == "<job-id>")'

# Persisted SQLite view (read-only)
sqlite3 -readonly ~/.openclaw/state/openclaw.sqlite \
  "SELECT job_json, state_json, next_run_at_ms FROM cron_jobs WHERE job_id = '<job-id>';"

# Canonical repo view
grep -A2 '<job-id>' ~/dotfiles/openclaw/cron/jobs.json | head
```

### Removing jobs (ghost-job pitfalls)

A job can keep firing even after you think you've deleted it. The
canonical removal procedure:

```bash
# 1. Remove from gateway state
openclaw cron rm <job-id>

# 2. Remove from repo jobs.json so a later deployment cannot restore it
$EDITOR ~/dotfiles/openclaw/cron/jobs.json   # delete the entry
cd ~/dotfiles && git add openclaw/cron/jobs.json && git commit && git push

# 3. Deploy now, or wait for the daily 6 AM deployment.
~/dotfiles/openclaw/sync-cron-jobs.sh deploy

# 4. Verify absent from all executable layers
openclaw cron list --all --json | grep <job-id>       # → empty
sqlite3 -readonly ~/.openclaw/state/openclaw.sqlite \
  "SELECT job_id FROM cron_jobs WHERE job_id = '<job-id>';"  # → empty
grep <job-id> ~/dotfiles/openclaw/cron/jobs.json      # → empty

# Keep cron_run_logs rows as audit history and completed-run tombstones.
```

Run history is not executable state; do not delete it to remove a job. If a job
is absent from `cron_jobs` but still appears armed in the current process,
restart the gateway so it reloads SQLite, then verify again:

```bash
launchctl kickstart -k gui/$(id -u)/ai.openclaw.gateway
openclaw cron list --all --json | grep <job-id>        # → empty
```

Do not edit `openclaw.sqlite` directly. Diagnose any persistence anomaly with
read-only queries, preserve the database and logs, and use the supported cron
API for repair.

## Avoiding double delivery

Pick **one** delivery path per job, not both:

- Cron-managed: set `delivery: { mode: "announce", channel: ..., to: ... }` and **do not** instruct the agent to send the message itself. The cron subsystem sends the agent's summary and applies a stale-delivery guard so very-late retries are dropped.
- Agent-managed: set `delivery.mode: "none"` (cron does not deliver) and instruct the agent to send via the `message` tool. Good when the agent should compose a custom-formatted message; the prompt still needs an idempotency check because an agent retry can send or act again.

If both fire, the recipient gets two messages per run, and a delivery-channel
failure during the agent's send can produce `status:error` even if the cron
announce succeeds. That can retry the whole task, including side effects. See
the historical 2026-05-01 `datenight-may-mediterranean` incident below.

## Safe one-shots with side effects

Any one-shot (`deleteAfterRun: true`) whose agent makes external bookings,
purchases, or other irreversible side effects MUST follow all four of these
or it can run multiple times and create duplicates:

The canonical date-night, double-date, and quarterly-dinner jobs are a
deliberate exception to exact-venue confirmation: their enabled definitions
and tracked restaurant-booking scopes are standing user authorization to
choose and book one surprise restaurant within the cuisine, location, date,
time, party-size, and fee-policy constraints. The uncertainty is part of the
experience. No durable exact-restaurant or exact-platform approval is
required. Do not disable these jobs or convert them to proposal-only work
merely because the venue is unknown. This does not waive dual-provider
idempotency, cache-only credentials, the one-total-mutation limit,
no-unattended-cancellation, `delivery.mode: none`, `deleteAfterRun`, or
successful-run tombstone requirements below.

1. **`delivery.mode: "none"`** (set via `--no-deliver`). With `announce`,
   a delivery-channel failure (service stall, network blip) flips the run to
   `status:error` even when the agent's task succeeded — the cron subsystem
   then retries, and each retry spawns a fresh agent session that re-does
   the side effect. Use `none` and have the agent self-deliver via the
   `message` tool inside the prompt; cron will record the run as `ok`
   based on agent return alone, and `deleteAfterRun` consumes the job.

2. **Idempotency check at the top of the prompt.** Even with delivery
   disabled, a worker crash, gateway restart, or manual re-run could
   re-fire the agent. The nine canonical restaurant one-shots must use the
   deployed `restaurant-book` scope as the sole reservation-account check,
   search, ranking, and booking path. The coordinator reads both Resy and
   OpenTable before search and again at the mutation boundary; the prompt
   must not reproduce provider-specific commands. Calendar-bearing jobs do
   their calendar precheck first, then invoke the coordinator only when clear.
   Clear means the Calendar read succeeded, covered the complete interval,
   and every returned record was parseable. An unavailable, incomplete,
   malformed, or otherwise inconclusive Calendar read stops the run before
   the coordinator:

   ```
   IDEMPOTENCY CHECK FIRST: The deployed scope treats any active booking
   in the scope's idempotency window on either Resy or OpenTable as already
   booked. Do not pivot around it.

   Run `~/.openclaw/bin/restaurant-book run --job-id <canonical-job-id>`
   exactly once. Across both providers, at most one total reservation
   mutation is allowed. After an attempted or possibly attempted mutation,
   or any ambiguous/unknown outcome, do not retry or fall back to another
   provider, restaurant, date, or time.
   ```

   **Critical scope rule**: date nights use any active reservation in the
   target month, regardless of date, time, restaurant, or party size. Double
   dates and quarterly group dinners use their tracked party-of-four windows.
   The idempotency window must contain every candidate date. A stopped,
   blocked, `already_reserved`, `manual_review_required`, or ambiguous result
   is final for that run; the agent reports it and performs no alternate
   reservation action.

3. **`deleteAfterRun: true`** (set via `--delete-after-run`). After the
   first successful return, OpenClaw removes the live definition and keeps
   append-only run history.

4. **Keep the successful run record as a deployment tombstone, then clean
   the repo definition up.** `sync-cron-jobs.sh deploy` checks the run history
   for an `ok` record at or after the one-shot's scheduled time. When found,
   it refuses to copy that completed repo definition back into the live file.
   This makes repeated daily or manual deployments safe. After verifying the
   side effect, remove the stale definition from the repo for clarity. For a
   canonical restaurant job, remove the same ID from the paired scope registry
   in the same commit so the completed standing authorization is no longer
   callable:

   ```bash
   # Live state (normally already absent after deleteAfterRun)
   openclaw cron rm <job-id>

   # Repo source of truth
   $EDITOR ~/dotfiles/openclaw/cron/jobs.json   # delete the entry
   # Restaurant jobs only: delete the same ID from this file too
   $EDITOR ~/dotfiles/openclaw/cron/restaurant-booking-scopes.json
   cd ~/dotfiles && git add openclaw/cron/jobs.json openclaw/cron/restaurant-booking-scopes.json
   git commit -m "..." && git push

   # Restaurant job/scope changes require the full deployment, not cron-only sync
   ~/dotfiles/openclaw/bin/dotfiles-pull.command
   ```

   Do not delete the successful `cron_run_logs` row while the definition still
   exists in the repo. Removing that tombstone makes the next deployment
   eligible to restore the one-shot. Keep protected booking-attempt and receipt
   state as investigation/idempotency evidence; removing the job and scope does
   not authorize deleting that state.

## Historical incident record (legacy JSON / BlueBubbles era)

The incidents below describe the storage and delivery stack that existed when
they happened. References to `jobs.json`, run JSONL files, the old sync gap, and
BlueBubbles are intentionally retained as history; they are not current
operating instructions.

### 2026-05-02 datenight-may La Morra ghost-booking
After the morning ghost re-fire (below) was cleaned up via `cron rm` +
runs file delete at 07:57 ET, two more runs fired the same day at
08:46 and 09:10 ET because the canonical removal had skipped the
**repo** step — the repo's `~/dotfiles/openclaw/cron/jobs.json` still
contained the May job, and `dotfiles-pull` ran at 08:25 ET via
launchd, calling `sync-cron-jobs`, which re-deployed the repo's
config back over the live file. The gateway re-loaded the May job
with its old schedule (`at: 2026-05-01T12:00:00Z`) which the cron
loop reads as "fire ASAP".

The 08:46 ET run found an existing May 8 booking (Thistle & Leek),
got a 412 from Resy, and reported `BOOKING FAILED — Existing
reservation conflict` (no booking created — Resy's own collision
guard saved us). The 09:10 ET run reasoned around the conflict
instead: "Dylan already has Thistle & Leek on May 8 and Olivia's
Bistro on May 16. Let me try Friday May 15 for La Morra." The
"try around May 8-16" wording in the original (un-migrated) prompt
gave the agent license to pick *any* Friday, and the prompt had no
idempotency stop because May was supposed to be deleted before the
Jun-Dec migration. So La Morra got booked on May 15.

Two compounding root causes:

1. **Removal didn't propagate to the repo.** The CRON-JOBS.md "canonical
   removal" recipe at the time only listed `cron rm` + runs file delete.
   It missed the repo. Updated above to include the repo edit + commit
   as step 3 of the 4-layer removal procedure.

2. **Idempotency check was missing AND would have been too narrow even
   if present.** The May job was never migrated (it was being deleted),
   so the original prompt — "try around May 8-16" with no stop condition —
   ran instead. Even the migrated form ("Friday 7 PM in May") would have
   been too narrow if combined with a multi-day search window: it would
   match May 8's Thistle & Leek but not prevent booking another Friday.
   Updated the idempotency template above to require "any booking in
   <month>", matching the agent's own search window.

### 2026-05-02 datenight-may ghost re-fire
The `datenight-may-mediterranean` job was consumed (`enabled: false`,
`deleteAfterRun: true`) after the 2026-05-01 incident — but the job
entry stayed in `jobs.json` and the runs/ jsonl file was never deleted.
At 2026-05-02 06:00 ET the gateway re-fired the "consumed" job from
the persisted `nextRunAtMs`. The agent's idempotency check correctly
identified the existing Thistle & Leek booking and did NOT double-book,
but it self-delivered another "May Date Night booked!" message to the
group chat, and the cron-layer announce delivery failed (`status:error`
recorded in runs/). Fixed by `cron rm` + deleting the runs/ file, plus
re-applying the `--no-deliver` migration to Jun-Dec (which a prior
gateway restart had wiped per the sync-gap pitfall above). Lesson: the
"Safe one-shots" pattern needs an explicit cleanup step (4) after the
run lands — `deleteAfterRun` only disables.

### 2026-05-01 datenight-may incident
The `datenight-may-mediterranean` job ran 8 times that morning between
08:04 and 12:21 UTC. Each delivery failed (BB watchdog had been silently
broken since the last `node@22` upgrade — `/opt/homebrew/bin/node`
hardcode), the cron retried, and each retry's fresh agent session booked
a different restaurant. Resy ended up holding 2 actual reservations
(May 8 and May 16) plus 3 attempted bookings that hit 412 conflicts
against the earlier ones. Fixed by switching all 7 remaining datenight
jobs (Jun-Dec) to `delivery.mode: "none"` + idempotency-check prompts.

### 2026-06-21 quarterly-dinner repo resurrection

`qd-booking-2026-07-june15` completed on June 15, but remained enabled in
the repo after `deleteAfterRun` removed it from the live file. Each daily or
manual `dotfiles-pull` copied it back, producing nine total executions and two
real July reservations. The duplicate Iru reservation and calendar event were
cancelled on June 21; Washington Square Tavern on July 10 remains.

The durable fix is in `sync-cron-jobs.sh deploy`: a successful run at or after
an `at` job's scheduled timestamp is a tombstone, so completed one-shots are
not redeployed. All remaining booking one-shots also use `delivery.mode: none`
and perform reservation plus calendar idempotency checks before acting.

## Recurring Jobs

| ID | Schedule | Payload | Delivery | Description |
|----|----------|---------|----------|-------------|
| `gws-julia-morning-triage-0001` | Daily 6:45 AM ET | `agentTurn` | `none` | Silent, fully paginated Gmail triage: labels, thread-aware reply drafts, read-state cleanup, archiving, and conservative spam trashing |
| `gws-julia-morning-briefing-0001` | Daily 7 AM ET | `agentTurn` | announce to Julia via iMessage | Read-only, high-signal briefing from the deterministic `julia-morning-briefing-data.py` collector |
| `gws-dylan-morning-briefing-0001` | Daily 8 AM ET | `agentTurn` | announce to Dylan via iMessage | Read-only seven-day calendar and 24-hour inbox briefing from the deterministic `dylan-morning-briefing-data.py` collector |
| `weekly-report-0001` | Sundays 3 PM ET | `agentTurn` | announce to Dylan via iMessage | Runs `openclaw-weekly-report.py`, then announces its deterministic activity and live-health report |
| `financial-scrape-0001` | Sundays 4:05 AM ET | exact-argv `command` | `none`; nonzero/timeout/output-bound failures trigger the six-hour-cooldown job failure alert, while detailed nonhealthy results attempt a durable alert-outbox handoff | Invokes only the deterministic cache-only `openclaw/bin/weekly-financial-scrape.py` helper without a model turn. It requires the repo's exact contract-v2 version and seven-source manifest before credentials/browser/data, pins every merge to `--wrapper-contract 2`, permits bounded browser recovery, validates one allowlisted status marker per successful scraper, and uses one run ID for every scraper/import. BoA retains exact-profile raw CDP and one guarded re-auth only after explicit `not_authenticated`. Production Plaid sync is a separate daily cache-only LaunchAgent. |
| `financial-scrape-alert-delivery-0001` | Every 15 minutes | exact-argv `command` | `none`; nonzero/timeout/output-bound failures trigger a six-hour-cooldown job failure alert | Invokes only `financial-scrape-alert-notifier.py` without a model turn. It drains due protected alerts, retains failed delivery with bounded exponential backoff, and cannot run a scraper, import, browser, or source sync. |

`financial-scrape-0001` owns Redfin refresh through guarded mortgage import commands; no separate property-value command is needed in its prompt. Before any source, profile, or data work, the helper reads the verified, bounded canonical owner-only repository `.env` through one file descriptor and retains only its exact `TESLA_EMAIL` assignment. It then requires both the repository child's sole `financial_scraper_contract.py --version` line `FINANCE_SCRAPER_CONTRACT 2` and its exact compact `--manifest`, validates the optional provider-mode file, and validates the dedicated credential cache. Every normal merge receives `--wrapper-contract 2`; a missing or wrong value is rejected by the scraper before source access. It reads the five finance pairs only from its dedicated owner-only `scraper-credentials.json` cache (never the gateway-exported general cache), builds every child environment from a closed runtime allowlist, disables dotenv loading in Python children, passes `TESLA_EMAIL` only to the Tesla scraper, and injects one selected pair only into a guarded re-authentication child. It never reads `.env-token`, invokes `op`, or receives a service-account token.

Every normal scraper receives one whole-run UUID and every import requires it. A successful scraper must emit exactly one compact `FINANCE_SCRAPER_STATUS` object whose keys are exactly `contract`, `source`, and `path`. Missing, duplicate, malformed, wrong-source, wrong-version, and unknown-path markers skip import and fail the source. Tesla accepts only healthy `direct_api`; Eversource, both National Grid sources, BWSC, and PennyMac accept healthy `direct_http` plus degraded browser paths; BoA accepts healthy `direct_http` plus its degraded browser paths. A validated browser fallback may import, but the helper records `degraded` and exits nonzero. It atomically persists the same safe final contract/run/time/results metadata mode `0600` at `~/.openclaw/financial-dashboard/weekly-scrape-status.json`; no child output, credential, account data, or financial value is included.

The helper acquires the existing `finance` PinchTab profile with a sanitized environment and leaves it running, always drains the complete child process group before returning from a child attempt, and captures aggregate child stdout/stderr only in memory under a 64 KiB ceiling. Invalid UTF-8 or oversized output is discarded and fails closed before auth recovery or import. The profile preflight never navigates and does not relax the exact `not_authenticated` credential gate; a failed preflight is independently unhealthy even when cookie replay succeeds. After an attended BoA login repair, `weekly-financial-scrape.py --recover-source boa` reruns only BoA and reconciles its result into the protected weekly status. Guarded mortgage imports preserve older months omitted by a partial response and reject malformed/non-finite payment records before SQLite access. It must not become the production Plaid or crypto sync path. `ai.openclaw.finance-refresh` owns the daily 06:15 local source refresh, runs the cache-only Plaid component before crypto, and never invokes `op`. The cron's historical conditional fallback is removed; do not add Plaid or crypto credentials to its environment.

Failed, degraded, interrupted, preflight, internal, and final-status-write outcomes each attempt to create one idempotent alert keyed by the whole-run UUID. Successful handoffs place a pending file under `~/.openclaw/financial-dashboard/weekly-scrape-alerts/`; the directory is owner-only mode `0700` and each strict, bounded, atomic JSON record is mode `0600`. Records contain only status, affected source names, allowlisted phase/path states, bounded retry/delivery state, and safe reasons—never child output, financial values, credentials, cookies, URLs, or message targets. The weekly status records `alert_handoff` as `not_required`, `pending`, `persisted`, or `failed`, so a crash or enqueue failure cannot disappear behind a previously successful status write. A healthy run never attempts an alert handoff.

Both finance jobs are deterministic exact-argv `payload.kind: command` jobs with explicit overall timeout, no-output timeout, and output byte limits. A helper nonzero exit is therefore a real cron error rather than text a model can normalize to `NO_REPLY`; each job has an `after: 1` failure alert with a six-hour cooldown. `delivery.mode: none` keeps ordinary safe JSON out of chat. A nonhealthy weekly run can intentionally produce one generic scheduler failure alert plus one later detailed durable alert; this bounded dual path is the reliability fallback when the outbox handoff itself is damaged.

The separate notifier is the detailed alert delivery path for completed wrapper runs. It resolves only Dylan's validated numeric chat ID from the tightly scoped `OPENCLAW_FINANCE_ALERT_CHAT_ID` environment value or by reading one exact `DYLAN_CHAT_ID=<digits>` assignment from a single verified owner-only secrets-cache file descriptor; it never sources that cache. Fixed `/opt/homebrew/bin/imsg` runs in a new process group with bounded stdout/stderr and timeout cleanup. Alerts transition atomically through `pending`, `inflight`, and `sent`; a sent record is cleanup-only, so an unlink/fsync failure cannot resend it every 15 minutes. Invalid and orphan entries are moved to the owner-only sibling quarantine without blocking valid UUID records, and safe run health is written to `weekly-scrape-alert-notifier-status.json`. Failed sends retain 15-minute-to-six-hour bounded backoff. A crash or ambiguous transport result after external send but before the durable `sent` transition can still produce one later duplicate because `imsg` exposes no idempotency key; the notifier deliberately prefers rare at-least-once delivery to silent loss. `--canary` exercises only target resolution and delivery with a fixed message and cannot invoke financial work. Do not run the canary without an attended notification test.

### Julia morning triage account routing

`gws-julia-morning-triage-0001` uses raw Gmail API resource commands. With
pinned GWS 0.4.4, each shell invocation must export
`GOOGLE_WORKSPACE_CLI_ACCOUNT` for Julia before running those commands; the
raw resource path must not rely on the CLI account flag. During the preflight
auth check, retry once only for the exact transient `Failed to get token`
cache race. Treat a preflight `No credentials provided` response as a
non-retryable routing/configuration error and return an `auth_error` handoff
before any mailbox mutation. Later per-message failures retain the prompt's
existing leave-unread-and-record-error behavior.

### Julia morning briefing data path

`gws-julia-morning-briefing-0001` must call
`/Users/dbochman/dotfiles/openclaw/bin/julia-morning-briefing-data.py` exactly
once and must not synthesize `gws`, retry, SQLite, HTTP, or shell control-flow
commands itself. The helper owns Julia's raw-API environment routing, the
single token-cache retry, same-day triage validation, Calendar and Gmail
pagination, cached sleep validation, and aggregate finance reads. It emits
only bounded fields and omits message, thread, event, and pagination IDs.
Expected source failures become section-level `unavailable`, `partial`, or
`skipped` objects while the process exits zero, so one unavailable source does
not suppress the rest of the briefing. The helper has a 150-second global
deadline and the agent turn has a 240-second timeout.

### Dylan morning briefing data path

`gws-dylan-morning-briefing-0001` must call
`/Users/dbochman/dotfiles/openclaw/bin/dylan-morning-briefing-data.py` exactly
once and must not synthesize `gws`, retry, or `jq` shell pipelines itself. The
helper uses `--account` only for the Calendar helper and
`GOOGLE_WORKSPACE_CLI_ACCOUNT` for raw Gmail endpoints, retries only the known
token-cache race once, handles an empty inbox as success, and filters metadata
to From/Subject/Date without emitting message IDs or snippets. Expected
Calendar or Gmail failures are returned as bounded `unavailable`/`partial`
status objects with exit zero so the other section can still be delivered.
The collector has a 150-second global deadline, while the agent turn has a
240-second timeout so partial data can still be composed before cron aborts.

## Temporary World Cup Briefings

The date-specific jobs named `world-cup-briefing-2026-*` cover 9:00 AM ET from
June 25 through the July 19 final. Each is an `at` job with
`deleteAfterRun: true`, announces one read-only briefing to Dylan, and follows
`openclaw/prompts/world-cup-2026-briefing.md`. Successful run history acts as a
tombstone, so daily cron deployment skips consumed definitions even before
they are removed from the repo. Successful past slots should be pruned from the
canonical file during routine cleanup; SQLite run history remains the
authoritative audit record, and only future or unresolved slots should remain
deployable.

The jobs use lightweight context, minimal thinking, the canonical
`openai/gpt-5.5` model alias (resolved at runtime through the `openai-codex`
provider), and a direct Sonnet fallback. Their normal data path is
`openclaw/bin/world-cup-briefing-data.py`, which concurrently fetches ESPN's
date-scoped World Cup scoreboards and standings with six-second deadlines,
normalizes kickoff times and US broadcasts, and keeps a date-specific cache.
FIFA's official fixtures page is authoritative but browser-rendered, so the
agent consults it only to resolve missing or conflicting material facts rather
than making it part of every run's critical path.

Historical note: the June 24 first run reached its 300-second deadline before any tool call. Its
primary `openai-codex/gpt-5.5` request failed immediately because the OAuth
token had been invalidated, then the Opus fallback stalled for the remainder of
the deadline. The OpenAI profile was reauthenticated and pinned first in the
Mini's auth order. The failed past-due definition was removed from the repo and
live state while its run log remains as audit history. A delivery-enabled
June 24 one-off then completed on `openai-codex/gpt-5.5` in 22.6 seconds,
delivered through the then-active BlueBubbles channel, and consumed its
temporary definition.

### Cron tools and paths

Most definitions use `payload.kind: agentTurn`; the two weekly-finance jobs use
exact-argv `payload.kind: command` so process exit, timeout, stall, and output
overflow are scheduler-visible without a model turn. No agent-turn definition
sets a restrictive `toolsAllow` list. The generated gateway service `PATH`
includes `~/.openclaw/bin`, and the daily deployment verifies required
restaurant wrappers against that live gateway environment. Canonical agent
prompts use explicit home-qualified paths, while command jobs pin both the
Homebrew Python interpreter and deployed helper with absolute argv entries.

### Canonical restaurant coordinator

The nine enabled restaurant one-shots call exactly one public entry point:

```bash
~/.openclaw/bin/restaurant-book run --job-id <canonical-job-id>
```

Their bounded authorizations live in
`openclaw/cron/restaurant-booking-scopes.json` and deploy as the protected
runtime registry `~/.openclaw/restaurant-bookings/scopes.json`. A job ID must
exist in both `jobs.json` and that registry. The scope fixes both providers,
candidate dates, time window, party size, cuisine/locality filters, fee policy,
optional structured minimum price tier, idempotency window, execution window,
deterministic selection, and a maximum of one total mutation attempt. The
December upscale scope requires provider price tier 3 or higher; missing or
malformed price metadata is ineligible.

`sync-cron-jobs.sh deploy` reconciles job definitions only; it does not install
the protected scope registry. Both `dotfiles-pull.command` and the initial
installer atomically install the scope file; only `dotfiles-pull.command` then
reconciles cron and checks the active gateway skill catalog. After changing a
restaurant job or scope, run the full dotfiles deployment. A manual cron-only
sync can otherwise leave the live job paired with an older scope.

The current standing scopes set deposit, prepayment, due-now, cancellation,
and no-show fee caps to `$0`. A card guarantee is eligible only when the
provider affirmatively reports no monetary hold or fee; unknown terms fail
closed. Raising any cap is a separate payment-authorization change.

`restaurant-book run` reads both reservation accounts fail-closed, searches
both Resy and OpenTable under strict call budgets, and selects one eligible
candidate only when both searches complete. A partial provider result blocks
mutation. The OpenTable browser guard and API token must carry the same
protected account/token attestation. The coordinator repeats both account
guards next to the mutation boundary, requires exact provider read-back after
booking, and never tries the other provider after a mutation is attempted or
its outcome is ambiguous. Its `plan` subcommand is read-only rehearsal only
and must not appear in a live cron prompt. Cron agents must not invoke
provider-specific reservation/search/booking commands or ask for an
exact-restaurant approval around the coordinator.

Every prompt sends exactly one status message because cron delivery is
disabled. A `confirmed` result may create the requested calendar event only
after a second complete and conclusive calendar check. If that repeat check is
unavailable or uncertain, the reservation remains confirmed but no event is
created or updated. No non-confirmed result creates a new calendar event;
`mutation_attempted` or `reservation_may_exist` requires an explicit warning
that a reservation may exist and attended review is needed. The coordinator
intentionally withholds Resy cancellation-capable tokens. A calendar event may
include a confirmation/reference only when the safe coordinator result
explicitly supplies one; the cron agent must never fetch, derive, or invent it.

### Coordinator operator checks and recovery

Use `plan` for a read-only end-to-end rehearsal; never invoke `run` as a health
check because an active scope is authorization to make one live booking:

```bash
~/.openclaw/bin/restaurant-book plan --job-id <canonical-job-id>
~/.openclaw/bin/opentable-reservations --json
~/.openclaw/bin/resy-read reservations
```

Interpret coordinator statuses conservatively:

| Status | Meaning and required action |
|---|---|
| `ready` | Read-only proposal from `plan`; no reservation exists yet |
| `confirmed` | One new reservation was strictly confirmed and read back |
| `already_reserved` | Stop; an account reservation or durable local receipt already covers the scope |
| `no_availability` | Stop for this run; do not broaden the tracked scope |
| `blocked`, `guard_unavailable`, or `busy` | Stop for this run; do not work around the coordinator or call a provider directly |
| `unknown` or `manual_review_required` | A reservation may exist or durable state is unresolved; attended account review is required before any later action |

Durable run state lives under
`~/.openclaw/restaurant-snipes/state/cron-<job-id>/`. A
`booking-attempt.json` deliberately blocks later overlapping work after an
ambiguous boundary; `confirmed.json` is the token-free local receipt. Never
delete or edit either merely to make a job run again. Reconcile both provider
accounts first, preserve the files for investigation, and change state only as
an attended recovery with a documented outcome.

## One-Shot Date Night Bookings

Monthly date nights for Dylan and Julia (2 people, Fridays at 7 PM,
Newton/Brookline area across Resy and OpenTable through `restaurant-book`). All
`deleteAfterRun: true`, agent
self-delivers to group chat (chat-id ${HOUSEHOLD_CHAT_ID}) via the `message` tool —
`delivery.mode: "none"` at the cron layer (see "Safe one-shots" above).
Each scope treats any active reservation in the target month on either
platform as already booked. Calendar creation is optional and only follows a
new `confirmed` result.

| ID | Fires On | Cuisine |
|----|----------|---------|
| `datenight-aug-farmtotable` | Aug 1, 2026 | Farm-to-Table |
| `datenight-sep-steakhouse` | Sep 1, 2026 | American/Steakhouse |
| `datenight-oct-indian` | Oct 1, 2026 | Indian |
| `datenight-nov-american` | Nov 1, 2026 | Modern American |
| `datenight-dec-upscale` | Dec 1, 2026 | Upscale (French/Italian/Contemporary) |

## One-Shot Double Date Bookings

Quarterly double dates for 4 (Dylan, Julia, Will, Ayesha). Thursdays or
Fridays at 7 PM, Brookline, across Resy and OpenTable through
`restaurant-book`. All use `deleteAfterRun: true`, `delivery.mode: none`, and
reservation plus calendar idempotency checks. The agent sends exactly one
group-chat status itself and creates a calendar event only after a new
`confirmed` result.

| ID | Fires On | Cuisine |
|----|----------|---------|
| `doubledate-q4-oct-mexican` | Oct 1, 2026 | Mexican |
| `doubledate-q1-jan27-french` | Jan 2, 2027 | French |

## One-Shot Quarterly Group Dinner Bookings

Quarterly group dinners for 4 across Resy and OpenTable through
`restaurant-book`. Party of 4 at 6:30 PM on Fridays in the Brookline/JP area,
booked before the target month. All use `deleteAfterRun: true`,
`delivery.mode: none`, and reservation plus calendar idempotency checks. The
agent sends one group-chat status and creates an event on Julia's calendar only
after a new `confirmed` reservation and only when no matching event exists.

| ID | Fires On | Target Month |
|----|----------|--------------|
| `qd-booking-2026-10-sep15` | Sep 15, 2026 | October 2026 |
| `qd-booking-2027-01-dec15` | Dec 15, 2026 | January 2027 |

## Removed Jobs (Historical)

| ID | Removed | Reason |
|----|---------|--------|
| `datenight-jul-japanese` | 2026-07-05 | Completed successfully on July 1; run-history tombstone retained and canonical one-shot removed |
| `doubledate-q3-jul-korean` | 2026-07-05 | Completed successfully on July 1; run-history tombstone retained and canonical one-shot removed |
| `world-cup-briefing-2026-06-25` through `-07-05` | 2026-07-05 | Each completed once and delivered through native iMessage; SQLite run history retained |
| `qd-booking-2026-07-june15` | 2026-06-21 | Completed job repeatedly redeployed; removed after tombstone hardening and duplicate cleanup |
| `datenight-jun-tapas` | 2026-06-21 | Completed June one-shot |
| `doubledate-q2-apr-thai` | 2026-06-21 | Completed Q2 one-shot |
| `crisismode-health-scan-0001` | 2026-03-24 | Consolidated into `weekly-report-0001` |
| `weekly-activity-report` | 2026-03-24 | Consolidated into `weekly-report-0001` |
| `weekly-security-reminder` | 2026-03-24 | Consolidated into `weekly-report-0001` |
| `weekly-upgrade-verify-0001` | 2026-03-12 | Weekly auto-upgrade removed; upgrades now manual |
