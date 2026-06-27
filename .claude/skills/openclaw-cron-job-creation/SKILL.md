---
name: openclaw-cron-job-creation
description: Create, edit, inspect, test, or remove OpenClaw cron jobs using the supported CLI/gateway, the canonical dotfiles definition, and SQLite-backed runtime state. Use when adding recurring or one-shot jobs, repairing schedule drift, debugging cron schema or delivery, checking run history, or cleaning up completed jobs without resurrecting them.
---

# Manage OpenClaw Cron Jobs

## Problem

OpenClaw 2026.6 stores executable cron definitions, runtime state, and run
history in `~/.openclaw/state/openclaw.sqlite`. Older instructions that edit
`~/.openclaw/cron/jobs.json` or `runs/*.jsonl` directly are unsafe and stale:
those paths are now temporary migration inputs or archived artifacts.

Use the gateway API for live mutations and keep
`~/dotfiles/openclaw/cron/jobs.json` as the canonical definition record. Never
write the live SQLite database directly.

## Storage model

- Canonical intent: `~/dotfiles/openclaw/cron/jobs.json`
- Live definitions/runtime: SQLite table `cron_jobs`
- Run history and one-shot tombstones: SQLite table `cron_run_logs`
- Active timers: gateway memory, synchronously persisted to SQLite by cron APIs
- Non-live artifacts: `~/.openclaw/cron/*.migrated`, `*.bak*`, and
  `runs/*.jsonl.migrated`

`~/dotfiles/openclaw/sync-cron-jobs.sh deploy` stages canonical definitions,
skips successfully completed `deleteAfterRun` jobs, imports new IDs, and repairs
schedule/enabled drift through the gateway. Existing payload/delivery edits and
removals still require both the supported cron API and the repo edit.

## Prepare the CLI

Source the protected gateway environment without printing it:

```bash
set -a
source ~/.openclaw/.secrets-cache
set +a
```

Inspect all jobs, including disabled ones:

```bash
openclaw cron list --all --json
```

## Create a job

Create through the CLI so OpenClaw validates the schema, assigns the ID, writes
SQLite, and arms the timer:

```bash
openclaw cron add \
  --name "Daily briefing" \
  --cron "0 7 * * *" \
  --tz America/New_York \
  --session isolated \
  --wake next-heartbeat \
  --message "Produce the briefing. Your final text is the delivered message." \
  --announce \
  --channel imessage \
  --to 'chat_id:171' \
  --json
```

Then capture the generated ID in the canonical repo, review the full diff, and
commit it:

```bash
~/dotfiles/openclaw/sync-cron-jobs.sh save
git -C ~/dotfiles diff -- openclaw/cron/jobs.json
```

Do not commit unrelated live drift produced by `save`; review before staging.

## Edit a job

Apply the live change with `openclaw cron edit`, then mirror the same field in
the repo. Examples:

```bash
openclaw cron edit <job-id> --message "Updated prompt"
openclaw cron edit <job-id> --cron "30 7 * * *" --tz America/New_York
openclaw cron edit <job-id> --at "2026-10-01T14:00:00.000Z"
openclaw cron edit <job-id> --announce --channel imessage --to 'chat_id:171'
```

Always reapply a changed schedule through the CLI even if the repo already has
the desired value. This forces `nextRunAtMs` to be recomputed. After mirroring
the repo definition, run the deploy bridge as a consistency check:

```bash
python3 -m json.tool ~/dotfiles/openclaw/cron/jobs.json >/dev/null
~/dotfiles/openclaw/sync-cron-jobs.sh deploy
```

## Canonical schema

Use `schedule.expr`, not `schedule.cron`, and `schedule.tz`, not
`schedule.timezone`:

```json
{
  "id": "stable-job-id",
  "agentId": "main",
  "name": "Daily briefing",
  "enabled": true,
  "createdAtMs": 1771191000000,
  "schedule": {
    "kind": "cron",
    "expr": "0 7 * * *",
    "tz": "America/New_York"
  },
  "sessionTarget": "isolated",
  "wakeMode": "next-heartbeat",
  "payload": {
    "kind": "agentTurn",
    "message": "Your prompt"
  },
  "delivery": {
    "mode": "announce",
    "channel": "imessage",
    "to": "chat_id:171"
  }
}
```

Schedule forms:

```json
{ "kind": "cron", "expr": "0 7 * * *", "tz": "America/New_York" }
{ "kind": "at", "at": "2026-10-01T14:00:00.000Z" }
{ "kind": "every", "everyMs": 3600000 }
```

Put `timeoutSeconds` inside `payload`. Use `deleteAfterRun: true` only for
one-shots that should be consumed after success.

## Side-effecting one-shots

Bookings, purchases, and other irreversible jobs must have all of these:

- `delivery.mode: "none"`; the agent sends exactly one final status.
- An idempotency preflight against the external system and matching calendar.
- `deleteAfterRun: true` / CLI `--delete-after-run`.
- A retained successful `cron_run_logs` row until the canonical definition is
  removed; that row is the deployment tombstone.

Do not delete run history to remove a job. History is not executable state.

## Run manually

Manual runs can repeat delivery and side effects. Obtain explicit authorization
before running a side-effecting job.

```bash
openclaw cron run <job-id> --wait --wait-timeout 10m --timeout 30000
```

Do not substitute `openclaw agent --deliver`; it creates an independent agent
execution outside the cron scheduler.

## Remove a job

Remove both live and canonical definitions, then deploy:

```bash
openclaw cron rm <job-id>
# Delete the matching object from ~/dotfiles/openclaw/cron/jobs.json and commit.
~/dotfiles/openclaw/sync-cron-jobs.sh deploy
```

Keep the corresponding `cron_run_logs` records. They preserve audit history
and prevent a completed one-shot still present in an older checkout from being
reseeded.

## Verify

Check the gateway, persisted SQLite state, and canonical repo:

```bash
openclaw cron list --all --json | jq '.jobs[] | select(.id == "<job-id>")'
openclaw cron runs --id <job-id> --limit 10

sqlite3 -readonly ~/.openclaw/state/openclaw.sqlite \
  "SELECT job_id, enabled, schedule_kind, at, schedule_expr, next_run_at_ms, last_run_status, last_delivery_status FROM cron_jobs WHERE job_id = '<job-id>';"

jq '.jobs[] | select(.id == "<job-id>")' \
  ~/dotfiles/openclaw/cron/jobs.json
```

For an enabled one-shot, confirm `next_run_at_ms` equals the epoch represented
by `schedule.at`. For a completed `deleteAfterRun` job, absence from
`cron_jobs` plus an `ok` row in `cron_run_logs` is expected.

## Delivery rule

Use exactly one path:

- Ordinary reports: cron-managed `delivery.mode: "announce"`; the prompt must
  not send with the message tool or `imsg`.
- Side-effecting one-shots: `delivery.mode: "none"`; the prompt performs one
  idempotent status send.

Native delivery uses `delivery.channel: "imessage"` and stable targets such as
`chat_id:171` or `chat_id:1`.
