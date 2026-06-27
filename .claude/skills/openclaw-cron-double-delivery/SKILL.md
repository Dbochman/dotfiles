---
name: openclaw-cron-double-delivery
description: Diagnose and fix OpenClaw cron jobs that deliver the same message two or more times, report a message failure despite successful delivery, or retry side effects after a delivery error. Use when prompts and cron delivery may both send, retired channel targets remain, or run history shows conflicting execution and delivery outcomes.
---

# Fix OpenClaw Cron Double Delivery

## Problem

A cron job delivers the same output multiple times, or reports an execution
error even though cron delivery succeeded. Repeated failures can increase
`consecutiveErrors`, apply backoff, and rerun external side effects.

Inspect current state through the gateway and SQLite-backed run history:

```bash
openclaw cron list --all --json
openclaw cron runs --id <job-id> --limit 10
```

Do not inspect or edit `~/.openclaw/cron/jobs.json` or run JSONL files as live
state. OpenClaw 2026.6 stores live jobs in `cron_jobs` and history in
`cron_run_logs` inside `~/.openclaw/state/openclaw.sqlite`.

## Root cause

Cron jobs can have two independent delivery paths:

1. The agent sends during execution with the message tool or `imsg`.
2. The cron runner sends the final output using the job's `delivery` block.

If the prompt instructs a send and the job also uses
`delivery.mode: "announce"`, both paths fire. A failed agent-side send can mark
the execution as an error while cron still records successful final delivery.
Retries may then create a third message or repeat the task.

Common evidence:

- The prompt says to send or invoke `imsg`, while delivery mode is `announce`.
- Run history has `status: error` with `deliveryStatus: delivered`.
- Gateway logs show a message-tool failure for a retired channel or invalid
  iMessage target.
- The recipient receives the direct send and the cron summary.

## Choose exactly one path

### Ordinary reports and briefings

Use cron-managed delivery. Remove all direct-send instructions and make the
prompt return only the content to deliver:

```text
DELIVERY: Do not use the message tool or imsg. Your final text output is the
briefing; the cron runner delivers it automatically.
```

Keep the job delivery configuration:

```json
{
  "mode": "announce",
  "channel": "imessage",
  "to": "chat_id:171"
}
```

### One-shots with external side effects

Bookings, purchases, and other irreversible jobs use the inverse pattern:

- `delivery.mode: "none"` and no cron-layer `channel` or `to`.
- The agent sends exactly one final status.
- The prompt first checks the external system and matching calendar for an
  existing result.
- The job uses `deleteAfterRun: true`.
- The successful `cron_run_logs` row remains as the deployment tombstone until
  the canonical repo definition is removed.

A cron announce failure must not convert a successful purchase or booking into
a retryable task.

## Apply the fix

Live mutations must use the supported cron API, then be mirrored in the
canonical repo at `~/dotfiles/openclaw/cron/jobs.json`.

For a cron-managed report:

```bash
openclaw cron edit <job-id> \
  --message "<prompt with all direct-send instructions removed>" \
  --announce --channel imessage --to 'chat_id:171'
```

For an agent-managed side-effecting one-shot:

```bash
openclaw cron edit <job-id> --no-deliver --delete-after-run \
  --message "<idempotency preflight, task, and exactly one final status send>"
```

Mirror the resulting prompt and delivery block in the repo, validate, and run
the deploy consistency check:

```bash
python3 -m json.tool ~/dotfiles/openclaw/cron/jobs.json >/dev/null
~/dotfiles/openclaw/sync-cron-jobs.sh deploy
```

Do not reset error counters or runtime state by editing SQLite or a legacy JSON
file. A successful subsequent run updates status normally. If a schedule is
also wrong, reapply it with `openclaw cron edit --cron`, `--at`, or `--every`
so the gateway recomputes `nextRunAtMs`.

## Verify

Before running anything manually, remember that a debug run can repeat both
delivery and side effects. Obtain explicit authorization for a side-effecting
job.

After the next scheduled run, verify:

- `status` is `ok`.
- `deliveryStatus` is `delivered` for cron-managed jobs or `not-requested` for
  agent-managed jobs.
- The recipient received exactly one message.
- Gateway logs contain no message-tool failure for that session.
- `consecutiveErrors` returned to zero.

Use a read-only SQLite query when field-level evidence is needed:

```bash
sqlite3 -readonly ~/.openclaw/state/openclaw.sqlite \
  "SELECT job_id, last_run_status, last_delivery_status, consecutive_errors, last_error FROM cron_jobs WHERE job_id = '<job-id>';"
```

## Example

Broken prompt:

```text
If authentication fails, send Dylan an iMessage.
Then produce Julia's briefing.
```

with `delivery.mode: "announce"` can send both the direct error/status and the
cron final output.

Fixed prompt:

```text
DELIVERY: Do not use the message tool or imsg. Your final text output is the
briefing; the cron runner delivers it automatically.

If authentication fails, output a concise authentication error as the final
text. Otherwise output the briefing.
```

The agent's message tool and cron delivery have no shared deduplication. Apply
this rule to every cron job, not only briefings.
