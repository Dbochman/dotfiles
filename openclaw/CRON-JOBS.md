# OpenClaw Cron Jobs

Reference for all cron jobs defined in `~/.openclaw/cron/jobs.json` on the Mac Mini.

## Editing & reading jobs

> **The gateway is the source of truth, not `jobs.json`.** The gateway loads `jobs.json` on startup and holds job definitions in memory. Direct edits to the file are silently reverted the next time the gateway syncs in-memory state to disk (which it does on its own cadence). Always go through the CLI:
>
> - **Edit:** `openclaw cron edit <id> --message "..."` (and other `--flag` options — see `openclaw cron edit --help`)
> - **Read (authoritative):** `openclaw cron list --json` — `jobs.json` on disk can be stale
> - **Add / disable / enable / remove:** `openclaw cron add|disable|enable|rm`

### CRITICAL: edits are volatile until the gateway syncs to disk

`openclaw cron edit` mutates **gateway in-memory state only**. The
gateway syncs to `jobs.json` on its own periodic cadence (typically
within ~60s). If the gateway restarts in that gap — manual `launchctl
kickstart`, plist reload, npm upgrade post-install, crash, machine
reboot — the in-memory change is silently discarded and the gateway
re-loads the OLD `jobs.json` config on startup. **No warning is
logged, no error is returned**. Hit on 2026-05-01 with the
`datenight-may` incident: edits to 7 jobs were wiped by an unrelated
gateway restart and had to be re-applied.

After any `openclaw cron edit`, verify durability before any restart:

```bash
# 1. Confirm jobs.json mtime is fresh (within ~60s of the edit)
stat -f '%Sm  %N' ~/.openclaw/cron/jobs.json

# 2. Confirm jobs.json content reflects the change
grep -A2 'datenight-jun' ~/.openclaw/cron/jobs.json | head
```

If `jobs.json` doesn't show the change yet, **wait** — don't restart
the gateway. For batch edits, add a 30-60s sleep at the end of the
script and re-read `jobs.json` to confirm all changes landed before
exiting.

### Removing jobs (ghost-job pitfalls)

A job can keep firing even after you think you've deleted it. The
canonical removal procedure:

```bash
# 1. Remove from gateway state
openclaw cron rm <job-id>

# 2. Delete the run state file (cron subsystem reads nextRunAtMs from
#    here independently of jobs.json — orphan files re-fire jobs)
rm -f ~/.openclaw/cron/runs/<job-id>.jsonl

# 3. Verify gone everywhere
openclaw cron list --json | grep <job-id>           # → empty
grep <job-id> ~/.openclaw/cron/jobs.json            # → empty
ls ~/.openclaw/cron/runs/<job-id>.jsonl 2>/dev/null # → no such file
```

If the gateway is in a weird state and a removed job keeps firing
(seen 2026-05-01: `cron rm` returned `removed: false` but the agent
still ran 18 minutes later), the recovery is:

```bash
# 1. Delete runs file
rm -f ~/.openclaw/cron/runs/<job-id>.jsonl
# 2. Restart gateway to flush in-memory state
launchctl kickstart -k gui/$(id -u)/ai.openclaw.gateway
# 3. CRITICAL: re-apply any pending `cron edit` changes the restart wiped
#    (see "edits are volatile" above)
# 4. Verify jobs.json on disk reflects the desired state
```

Triple-verify across all three layers (gateway memory, jobs.json,
runs/) before considering a job dead — they can drift independently.

## Avoiding double delivery

Pick **one** delivery path per job, not both:

- Cron-managed: set `delivery: { mode: "announce", channel: ..., to: ... }` and **do not** instruct the agent to send the message itself. The cron subsystem sends the agent's summary and applies a stale-delivery guard so very-late retries are dropped.
- Agent-managed: set `delivery.mode: "silent"` (cron does not deliver) and instruct the agent to send via the `message` tool. Good when the agent should compose a custom-formatted message — but no stale-delivery guard, so an agent retry hours later still sends.

If both fire, the recipient gets two messages per run, and BB stalls during the agent's send produce `status:error` (despite cron's announce delivering successfully) which triggers cron retry — re-running the entire agent task (including any side effects like restaurant bookings). See the 2026-05-01 `datenight-may-mediterranean` incident for the full failure mode.

## Safe one-shots with side effects

Any one-shot (`deleteAfterRun: true`) whose agent makes external bookings,
purchases, or other irreversible side effects MUST follow all three of these
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
   existing Friday 7 PM upcoming booking in <month> 2026. If one already
   exists, send chat-id 170 a status message ("<month> date night already
   booked: [restaurant], [date]") and STOP — do NOT book another.

   Otherwise: <original task prompt>
   ```

3. **`deleteAfterRun: true`** (set via `--delete-after-run`). Combined
   with (1), this guarantees the job is removed after the agent's first
   successful return — no second attempt, no orphan run state.

   **Caveat**: `deleteAfterRun` does NOT actually delete the job — it
   flips `enabled: false` and writes `updatedAtMs`, but the job entry
   stays in `jobs.json` AND the runs/ state file persists. Combined
   with the runs/ file's stored `nextRunAtMs`, a gateway restart can
   re-fire the "consumed" job on the next tick. Always follow up with
   the explicit removal procedure below once the agent confirms
   success — don't trust `deleteAfterRun` alone.

4. **Explicit removal after the run lands.** The morning after the
   agent fires (or as soon as you've verified the side effect), run:

   ```bash
   openclaw cron rm <job-id>
   rm -f ~/.openclaw/cron/runs/<job-id>.jsonl
   ```

   The run state file persists independently and the cron subsystem
   will keep trying to fire the job otherwise — it logs "skipping
   stale delivery" as clutter, and is a live foot-gun if a gateway
   restart resets the in-memory disable.

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

## Recurring Jobs

| ID | Schedule | Tools | Delivery | Description |
|----|----------|-------|----------|-------------|
| `gws-julia-morning-briefing-0001` | Daily 7 AM ET | `exec` | announce to Julia via BB | Julia's morning briefing: calendar preview, inbox triage (label, draft replies, cleanup), 8sleep summary |
| `gws-dylan-morning-briefing-0001` | Daily 8 AM ET | `exec` | announce to Dylan via BB | Dylan's morning briefing: calendar (7-day) + inbox summary (24h) + 8sleep summary. Read-only, no email actions |
| `weekly-report-0001` | Sundays 3 PM ET | `exec` | announce to Dylan via BB | Combined weekly activity report, security check (gateway, BB, auth, disk, services), and CrisisMode health scan |

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
| `datenight-jun-tapas` | Jun 1, 2026 | Spanish/Tapas |
| `datenight-jul-japanese` | Jul 1, 2026 | Japanese/Asian |
| `datenight-aug-farmtotable` | Aug 1, 2026 | Farm-to-Table |
| `datenight-sep-steakhouse` | Sep 1, 2026 | American/Steakhouse |
| `datenight-oct-indian` | Oct 1, 2026 | Indian |
| `datenight-nov-american` | Nov 1, 2026 | Modern American |
| `datenight-dec-upscale` | Dec 1, 2026 | Upscale (French/Italian/Contemporary) |

## One-Shot Double Date Bookings

Quarterly double dates for 4 (Dylan, Julia, Will, Ayesha). Thursdays or Fridays at 7 PM, Brookline, via OpenTable or Resy. All `deleteAfterRun: true`, delivered to group chat.

| ID | Fires On | Cuisine |
|----|----------|---------|
| `doubledate-q2-apr-thai` | Apr 1, 2026 | Thai |
| `doubledate-q3-jul-korean` | Jul 1, 2026 | Korean |
| `doubledate-q4-oct-mexican` | Oct 1, 2026 | Mexican |
| `doubledate-q1-jan27-french` | Jan 2, 2027 | French |

## One-Shot Quarterly Group Dinner Bookings

Quarterly group dinners for 4 via Resy. Party of 4 at 6:30 PM on Fridays, Brookline/JP area. Booked ~2 weeks before the target month. All `deleteAfterRun: true`, delivered to group chat. Calendar events created on Julia's calendar inviting Dylan.

| ID | Fires On | Target Month |
|----|----------|--------------|
| `qd-booking-2026-07-june15` | Jun 15, 2026 | July 2026 |
| `qd-booking-2026-10-sep15` | Sep 15, 2026 | October 2026 |
| `qd-booking-2027-01-dec15` | Dec 15, 2026 | January 2027 |

## Removed Jobs (Historical)

| ID | Removed | Reason |
|----|---------|--------|
| `crisismode-health-scan-0001` | 2026-03-24 | Consolidated into `weekly-report-0001` |
| `weekly-activity-report` | 2026-03-24 | Consolidated into `weekly-report-0001` |
| `weekly-security-reminder` | 2026-03-24 | Consolidated into `weekly-report-0001` |
| `weekly-upgrade-verify-0001` | 2026-03-12 | Weekly auto-upgrade removed; upgrades now manual |
