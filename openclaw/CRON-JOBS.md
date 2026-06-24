# OpenClaw Cron Jobs

Reference for all cron jobs defined in `~/.openclaw/cron/jobs.json` on the Mac Mini.

## Editing & reading jobs

### State lives in 4 places — and the repo wins

There are **four** independent stores of cron-job state, and they can drift:

| Layer | Path | Updated by |
|-------|------|------------|
| **1. Repo (apex source of truth)** | `~/dotfiles/openclaw/cron/jobs.json` (in this repo) | git commits |
| **2. Live config** | `~/.openclaw/cron/jobs.json` on the Mini | `dotfiles-pull` + `sync-cron-jobs` (daily at 6 AM and on manual deploy); gateway state writes |
| **3. Gateway in-memory** | gateway process state | `openclaw cron edit/add/rm` (and on-startup load from layer 2) |
| **4. Run history / one-shot tombstones** | `~/.openclaw/cron/runs/<id>.jsonl` | gateway after each run; read by `sync-cron-jobs deploy` to suppress completed one-shots |

The **repo wins on the next deployment**. The scheduled deployment is daily at 6 AM; manual `dotfiles-pull.command` runs deploy immediately. The deploy preserves live runtime state, skips any successful `deleteAfterRun` one-shot recorded in `runs/`, and otherwise overwrites live definitions from the repo. Any CLI-only `cron edit` or `cron rm` is lost at that next deployment unless the repo receives the same change.

This is the trap: `cron edit ... --message "..."` shows the change in `cron list --json` immediately and looks durable, but a later deploy can reseed the live file from the repo. The successful-run tombstone prevents completed one-shots from being resurrected; recurring jobs and never-successful one-shots still require the canonical repo edit.

### CLI commands

> - **Edit:** `openclaw cron edit <id> --message "..."` — mutates gateway memory; **also commit the same change to the repo `jobs.json`** or the next deploy replaces it.
> - **Read (authoritative for current run cycle):** `openclaw cron list --json`
> - **Add / disable / enable / remove:** `openclaw cron add|disable|enable|rm` — same caveat: also update repo.

### CRITICAL: a CLI-only edit is doomed

Two ways the layer drift kills you:

- **Within ~60 seconds**: `openclaw cron edit` mutates in-memory state only. The gateway syncs to live `jobs.json` on its own cadence. If the gateway restarts in that gap (`launchctl kickstart`, npm upgrade, crash, reboot), the in-memory change is silently discarded and reload happens from the unchanged live file. No warning, no error. Hit on 2026-05-01 — edits to 7 jobs were wiped by an unrelated restart.

- **At the next scheduled or manual deploy**: even after the in-memory change syncs to live disk, `dotfiles-pull` re-deploys the repo's `jobs.json`. CLI changes that are not also committed die quietly. The scheduled run is daily at 6 AM, but a manual deployment can happen at any time.

**Therefore: every cron-job change is a two-step commit:**

```bash
# 1. Apply via CLI (so the change takes effect immediately, with
#    proper validation and gateway notification)
openclaw cron edit <job-id> --message "..."

# 2. Mirror the same change into the repo and push
$EDITOR ~/dotfiles/openclaw/cron/jobs.json
cd ~/dotfiles && git add openclaw/cron/jobs.json && git commit && git push
# (or wait for the next daily dotfiles-pull to deploy)
```

Verify durability before any restart:

```bash
# Confirm live jobs.json mtime is fresh (within ~60s of the edit)
stat -f '%Sm  %N' ~/.openclaw/cron/jobs.json

# Confirm live jobs.json reflects the change
grep -A2 '<job-id>' ~/.openclaw/cron/jobs.json | head

# Confirm repo also has the change (so the next deploy doesn't undo it)
grep -A2 '<job-id>' ~/dotfiles/openclaw/cron/jobs.json | head
```

If live shows the change but repo does not, the next scheduled or manual deployment will revert it. Commit the repo before walking away.

### Removing jobs (ghost-job pitfalls)

A job can keep firing even after you think you've deleted it. The
canonical removal procedure:

```bash
# 1. Remove from gateway state
openclaw cron rm <job-id>

# 2. Remove from repo jobs.json so a later deployment cannot restore it
$EDITOR ~/dotfiles/openclaw/cron/jobs.json   # delete the entry
cd ~/dotfiles && git add openclaw/cron/jobs.json && git commit && git push

# 3. Deploy now, or wait for the daily 6 AM deployment
~/dotfiles/openclaw/sync-cron-jobs.sh deploy

# 4. Verify absent from all executable layers
openclaw cron list --json | grep <job-id>            # → empty
grep <job-id> ~/.openclaw/cron/jobs.json             # → empty
grep <job-id> ~/dotfiles/openclaw/cron/jobs.json     # → empty

# Keep runs/<job-id>.jsonl as audit history and a completed-run tombstone.
```

If the repo and live files are clean but a removed job still fires, stop the
gateway before changing run history:

```bash
# 1. Stop the gateway and archive, rather than destroy, the anomalous history
launchctl bootout gui/$(id -u)/ai.openclaw.gateway
mv ~/.openclaw/cron/runs/<job-id>.jsonl ~/.openclaw/cron/runs/<job-id>.jsonl.quarantine
# 2. Confirm both jobs.json files are clean, then bootstrap the gateway
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/ai.openclaw.gateway.plist
# 3. Re-apply and commit any pending CLI-only edits the restart would lose
```

Verify the repo, live jobs file, and gateway memory before considering a job
removed. An orphan run file is normally retained history, not an executable
definition; quarantine it only when a current-version reproduction proves it
is independently scheduling a job.

## Avoiding double delivery

Pick **one** delivery path per job, not both:

- Cron-managed: set `delivery: { mode: "announce", channel: ..., to: ... }` and **do not** instruct the agent to send the message itself. The cron subsystem sends the agent's summary and applies a stale-delivery guard so very-late retries are dropped.
- Agent-managed: set `delivery.mode: "none"` (cron does not deliver) and instruct the agent to send via the `message` tool. Good when the agent should compose a custom-formatted message; the prompt still needs an idempotency check because an agent retry can send or act again.

If both fire, the recipient gets two messages per run, and BB stalls during the agent's send produce `status:error` (despite cron's announce delivering successfully) which triggers cron retry — re-running the entire agent task (including any side effects like restaurant bookings). See the 2026-05-01 `datenight-may-mediterranean` incident for the full failure mode.

## Safe one-shots with side effects

Any one-shot (`deleteAfterRun: true`) whose agent makes external bookings,
purchases, or other irreversible side effects MUST follow all four of these
or it can run multiple times and create duplicates:

1. **`delivery.mode: "none"`** (set via `--no-deliver`). With `announce`,
   a delivery-channel failure (BB stall, network blip) flips the run to
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

   Do not delete the successful run file while the definition still exists
   in the repo. Removing that tombstone makes the next deployment eligible
   to restore the one-shot.

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

| ID | Schedule | Tools | Delivery | Description |
|----|----------|-------|----------|-------------|
| `gws-julia-morning-triage-0001` | Daily 6:45 AM ET | `exec` | `none` | Silent, fully paginated Gmail triage: labels, thread-aware reply drafts, read-state cleanup, archiving, and conservative spam trashing |
| `gws-julia-morning-briefing-0001` | Daily 7 AM ET | `exec` | announce to Julia via BB | Read-only, high-signal briefing from the triage handoff, today's calendar, cached Eight Sleep, and live household net worth/FIRE aggregates |
| `gws-dylan-morning-briefing-0001` | Daily 8 AM ET | `exec` | announce to Dylan via BB | Dylan's morning briefing: calendar (7-day) + inbox summary (24h) + 8sleep summary. Read-only, no email actions |
| `weekly-report-0001` | Sundays 3 PM ET | `agentTurn` | announce to Dylan via BB | Runs `openclaw-weekly-report.py`, then announces its deterministic activity and live-health report |
| `financial-scrape-0001` | Sundays 4:05 AM ET | `exec` | `none` (agent self-messages on failure only) | Weekly financial dashboard refresh: Tesla Solar (API), Tier 2 self-healing utilities and PennyMac, plus BoA cookie replay/raw-CDP with one guarded re-auth only after an explicitly signed-out tab, then SQLite imports. Production Plaid sync is a separate daily cache-only LaunchAgent. |

`financial-scrape-0001` must not become the production Plaid sync path. `ai.openclaw.financial-dashboard-plaid-sync` owns that daily 07:15 local run, reads only protected local caches, and never invokes `op`. The cron's historical conditional fallback is intentionally unconfigured; do not add Plaid credentials to its environment.

### Tool allowlists (added 2026-04-04, requires OpenClaw v2026.4.1+)

Jobs with `tools: exec` can invoke shell commands via the exec tool. Isolated cron sessions do NOT have `~/.openclaw/bin` on PATH — all custom CLI commands must use **full absolute paths** (e.g. `/Users/dbochman/.openclaw/bin/8sleep sleep dylan`).

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
| `qd-booking-2026-07-june15` | 2026-06-21 | Completed job repeatedly redeployed; removed after tombstone hardening and duplicate cleanup |
| `datenight-jun-tapas` | 2026-06-21 | Completed June one-shot |
| `doubledate-q2-apr-thai` | 2026-06-21 | Completed Q2 one-shot |
| `crisismode-health-scan-0001` | 2026-03-24 | Consolidated into `weekly-report-0001` |
| `weekly-activity-report` | 2026-03-24 | Consolidated into `weekly-report-0001` |
| `weekly-security-reminder` | 2026-03-24 | Consolidated into `weekly-report-0001` |
| `weekly-upgrade-verify-0001` | 2026-03-12 | Weekly auto-upgrade removed; upgrades now manual |
