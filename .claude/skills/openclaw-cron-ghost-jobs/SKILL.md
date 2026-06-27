---
name: openclaw-cron-ghost-jobs
description: Diagnose and remove OpenClaw cron jobs that reappear, remain scheduled after removal, or rerun after a completed one-shot. Use when gateway state, the canonical dotfiles definitions, and SQLite cron state disagree, especially around dotfiles deployment or deleteAfterRun tombstones.
---

# Fix OpenClaw Cron Ghost Jobs

## State model

Treat these as separate planes:

- Canonical intent: `~/dotfiles/openclaw/cron/jobs.json`
- Live definitions and timers: gateway plus `cron_jobs` in
  `~/.openclaw/state/openclaw.sqlite`
- Durable run history/tombstones: `cron_run_logs` in the same SQLite database

Legacy `~/.openclaw/cron/jobs.json*` and `cron/runs/*.jsonl*` files are migration
artifacts, not executable state. Never restore or delete them as a ghost-job
repair. Never edit the SQLite database directly.

## Diagnose

Source the cached gateway token, then inspect the supported live view:

```bash
set -a
source ~/.openclaw/.secrets-cache
set +a

openclaw cron list --all --json
openclaw cron runs --id <job-id> --limit 20
jq '.jobs[] | select(.id == "<job-id>")' \
  ~/dotfiles/openclaw/cron/jobs.json
```

Use SQLite only for read-only confirmation:

```bash
sqlite3 -readonly ~/.openclaw/state/openclaw.sqlite \
  "SELECT job_id, enabled, next_run_at_ms, last_run_status
     FROM cron_jobs WHERE job_id = '<job-id>';"

sqlite3 -readonly ~/.openclaw/state/openclaw.sqlite \
  "SELECT seq, ts, status, delivered, run_id
     FROM cron_run_logs WHERE job_id = '<job-id>' ORDER BY ts DESC LIMIT 20;"
```

Correlate an unexpected reappearance with
`~/.openclaw/logs/dotfiles-pull.log` and the relevant gateway log. A retained
`cron_run_logs` row is audit history and may be the tombstone preventing a
completed one-shot from being recreated; it is not evidence that the job is
still scheduled.

## Remove a live ghost

1. Remove the definition through the supported gateway:

   ```bash
   openclaw cron rm <job-id>
   ```

2. Remove the matching object from the canonical repo JSON and commit it.
3. Run `~/dotfiles/openclaw/sync-cron-jobs.sh deploy`.
4. Verify the ID is absent from `openclaw cron list --all --json`, read-only
   `cron_jobs`, and the repo definition.
5. Preserve `cron_run_logs` history.

If all three definition views are clean but the current gateway still appears
to hold a timer, preserve the database and logs, restart the gateway once, and
verify again. Do not quarantine or delete history. Escalate a reproducible
post-restart execution as a scheduler defect with the retained evidence.

## Prevent recurrence

- Keep repo and live gateway edits in the same change.
- Remove completed one-shot definitions promptly; retain their SQLite history.
- Give side-effecting jobs external-system and calendar idempotency checks.
- Use `delivery.mode: "none"` for side-effecting one-shots and send one
  agent-managed status so a delivery failure cannot rerun the action.
- Keep `deleteAfterRun: true`; it is cleanup behavior, not idempotency.
