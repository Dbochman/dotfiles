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
imports new IDs through `openclaw doctor`, and reconciles schedule/enabled
changes through the live gateway so `nextRunAtMs` is recalculated. Existing
payload/delivery edits and removals still require the matching cron API command
as well as the repo edit. The daily deployment runs at 6 AM; a manual
`dotfiles-pull.command` deploys immediately.

### CLI commands

> - **Edit:** `openclaw cron edit <id> ...` — updates gateway memory and SQLite immediately; mirror the change in repo `jobs.json`.
> - **Read:** `openclaw cron list --all --json` — authoritative for the current scheduler cycle.
> - **Add / disable / enable / remove:** `openclaw cron add|disable|enable|rm` — also make the corresponding repo change.

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

1. **`delivery.mode: "none"`** (set via `--no-deliver`). With `announce`,
   a delivery-channel failure (service stall, network blip) flips the run to
   `status:error` even when the agent's task succeeded — the cron subsystem
   then retries, and each retry spawns a fresh agent session that re-does
   the side effect. Use `none` and have the agent self-deliver via the
   `message` tool inside the prompt; cron will record the run as `ok`
   based on agent return alone, and `deleteAfterRun` consumes the job.

2. **Idempotency check at the top of the prompt.** Even with delivery
   disabled, a worker crash, gateway restart, or manual re-run could
   re-fire the agent. Make the agent check whether the side effect was
   already done before doing it. For Resy/OpenTable bookings:

   ```
   IDEMPOTENCY CHECK FIRST: Run `resy reservations` and look for any
   existing upcoming booking in <month> 2026 (any date, any time, any
   restaurant — not just the specific Friday). If ANY May 2026 booking
   exists, send chat-id 170 a status message ("<month> date night
   already booked: [restaurant], [date]") and STOP — do NOT book
   another.

   Otherwise: <original task prompt>
   ```

   **Critical wording**: the check must say "any booking in <month>",
   not "the specific Friday at 7 PM". If the prompt also says "try around
   May 8-16", an agent that finds a May 8 conflict will helpfully pivot
   to May 15 — which is exactly what happened on 2026-05-02 with La Morra
   (post-mortem below). Make the idempotency window match the agent's
   own search window, or it'll book around the conflict.

3. **`deleteAfterRun: true`** (set via `--delete-after-run`). After the
   first successful return, OpenClaw removes the live definition and keeps
   append-only run history.

4. **Keep the successful run record as a deployment tombstone, then clean
   the repo definition up.** `sync-cron-jobs.sh deploy` checks the run history
   for an `ok` record at or after the one-shot's scheduled time. When found,
   it refuses to copy that completed repo definition back into the live file.
   This makes repeated daily or manual deployments safe. After verifying the
   side effect, remove the stale definition from the repo for clarity:

   ```bash
   # Live state (normally already absent after deleteAfterRun)
   openclaw cron rm <job-id>

   # Repo source of truth
   $EDITOR ~/dotfiles/openclaw/cron/jobs.json   # delete the entry
   cd ~/dotfiles && git add openclaw/cron/jobs.json && git commit -m "..." && git push
   ```

   Do not delete the successful `cron_run_logs` row while the definition still
   exists in the repo. Removing that tombstone makes the next deployment
   eligible to restore the one-shot.

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
| `gws-julia-morning-briefing-0001` | Daily 7 AM ET | `agentTurn` | announce to Julia via iMessage | Read-only, high-signal briefing from the triage handoff, today's calendar, cached Eight Sleep, and live household net worth/FIRE aggregates |
| `gws-dylan-morning-briefing-0001` | Daily 8 AM ET | `agentTurn` | announce to Dylan via iMessage | Read-only seven-day calendar and 24-hour inbox briefing from the deterministic `dylan-morning-briefing-data.py` collector |
| `weekly-report-0001` | Sundays 3 PM ET | `agentTurn` | announce to Dylan via iMessage | Runs `openclaw-weekly-report.py`, then announces its deterministic activity and live-health report |
| `financial-scrape-0001` | Sundays 4:05 AM ET | `agentTurn` | `none` (agent self-messages on failure only) | Invokes the deterministic `openclaw/bin/weekly-financial-scrape.py` helper: Tesla Solar (API), Tier 2 self-healing utilities and PennyMac, plus BoA cookie replay/exact `finance`-profile raw CDP with one guarded re-auth only after explicit `not_authenticated`; imports only current-run successes, with mortgage run-ID validation and a weekly-gated authorized Redfin refresh. Production Plaid sync is a separate daily cache-only LaunchAgent. |

`financial-scrape-0001` owns Redfin refresh through guarded mortgage import commands; no separate property-value command is needed in its prompt. The helper serializes whole runs with a protected nonblocking lock, cleans the complete child process group on timeout or interruption, captures child output privately, emits only source/phase status metadata, binds both mortgage artifacts to one run ID, and skips every import whose scrape did not succeed in that execution. Guarded mortgage imports preserve older months omitted by a partial response and reject malformed/non-finite payment records before SQLite access. It must not become the production Plaid or crypto sync path. `ai.openclaw.finance-refresh` owns the daily 06:15 local source refresh, runs the cache-only Plaid component before crypto, and never invokes `op`. The cron's historical conditional fallback is removed; do not add Plaid or crypto credentials to its environment.

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
they are removed from the repo. The June 25-27 runs each completed and
delivered once; their definitions have been removed from the repo while their
SQLite run history remains.

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

All current definitions use `payload.kind: agentTurn`; none sets a restrictive
`toolsAllow` list. Prompts may still invoke shell commands through the exec
tool. Isolated cron sessions do not have `~/.openclaw/bin` on `PATH`, so custom
CLI commands must use full absolute paths.

## One-Shot Date Night Bookings

Monthly date nights for Dylan and Julia (2 people, Fridays at 7 PM,
Newton/Brookline area via Resy). All `deleteAfterRun: true`, agent
self-delivers to group chat (chat-id 170) via the `message` tool —
`delivery.mode: "none"` at the cron layer (see "Safe one-shots" above).
Each prompt opens with an idempotency check against `resy reservations`.

| ID | Fires On | Cuisine |
|----|----------|---------|
| `datenight-jul-japanese` | Jul 1, 2026 | Japanese/Asian |
| `datenight-aug-farmtotable` | Aug 1, 2026 | Farm-to-Table |
| `datenight-sep-steakhouse` | Sep 1, 2026 | American/Steakhouse |
| `datenight-oct-indian` | Oct 1, 2026 | Indian |
| `datenight-nov-american` | Nov 1, 2026 | Modern American |
| `datenight-dec-upscale` | Dec 1, 2026 | Upscale (French/Italian/Contemporary) |

## One-Shot Double Date Bookings

Quarterly double dates for 4 (Dylan, Julia, Will, Ayesha). Thursdays or Fridays at 7 PM, Brookline, via OpenTable or Resy. All use `deleteAfterRun: true`, `delivery.mode: none`, and reservation plus calendar idempotency checks; the agent sends exactly one group-chat status itself.

| ID | Fires On | Cuisine |
|----|----------|---------|
| `doubledate-q3-jul-korean` | Jul 1, 2026 | Korean |
| `doubledate-q4-oct-mexican` | Oct 1, 2026 | Mexican |
| `doubledate-q1-jan27-french` | Jan 2, 2027 | French |

## One-Shot Quarterly Group Dinner Bookings

Quarterly group dinners for 4 via Resy. Party of 4 at 6:30 PM on Fridays, Brookline/JP area. Booked ~2 weeks before the target month. All use `deleteAfterRun: true`, `delivery.mode: none`, and reservation plus calendar idempotency checks. The agent sends one group-chat status and creates a calendar event on Julia's calendar only when no matching event exists.

| ID | Fires On | Target Month |
|----|----------|--------------|
| `qd-booking-2026-10-sep15` | Sep 15, 2026 | October 2026 |
| `qd-booking-2027-01-dec15` | Dec 15, 2026 | January 2027 |

## Removed Jobs (Historical)

| ID | Removed | Reason |
|----|---------|--------|
| `world-cup-briefing-2026-06-25` through `-06-27` | 2026-06-27 | Each completed once and delivered through native iMessage; SQLite run history retained |
| `qd-booking-2026-07-june15` | 2026-06-21 | Completed job repeatedly redeployed; removed after tombstone hardening and duplicate cleanup |
| `datenight-jun-tapas` | 2026-06-21 | Completed June one-shot |
| `doubledate-q2-apr-thai` | 2026-06-21 | Completed Q2 one-shot |
| `crisismode-health-scan-0001` | 2026-03-24 | Consolidated into `weekly-report-0001` |
| `weekly-activity-report` | 2026-03-24 | Consolidated into `weekly-report-0001` |
| `weekly-security-reminder` | 2026-03-24 | Consolidated into `weekly-report-0001` |
| `weekly-upgrade-verify-0001` | 2026-03-12 | Weekly auto-upgrade removed; upgrades now manual |
