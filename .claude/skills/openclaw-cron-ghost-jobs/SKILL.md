---
name: openclaw-cron-ghost-jobs
description: >-
  Diagnose and fix OpenClaw jobs that reappear or run again after removal,
  especially completed deleteAfterRun one-shots restored by dotfiles cron sync.
author: Codex
version: 2.0.0
date: 2026-06-21
---

# OpenClaw Cron Ghost Jobs

## Problem

A removed or completed OpenClaw cron job reappears and executes again. For
booking jobs, this can create duplicate reservations and calendar events.

## Trigger Conditions

- A `deleteAfterRun: true` job runs again after a successful execution.
- `openclaw cron rm` succeeds, but the job later returns.
- The live `~/.openclaw/cron/jobs.json` omits a job while the dotfiles copy
  still contains it.
- Runs cluster around daily or manual `dotfiles-pull` deployments.

## Root Cause

The repo file `~/dotfiles/openclaw/cron/jobs.json` is the canonical definition
source. `dotfiles-pull.command` invokes `sync-cron-jobs.sh deploy` daily at 6 AM
and whenever it is run manually. Before the 2026-06-21 hardening, deploy copied
every repo definition into the live file, including completed one-shots that
OpenClaw had removed from live state. A past-due `at` job then fired immediately.

Run files under `~/.openclaw/cron/runs/` are append-only history. Current
`sync-cron-jobs.sh deploy` also uses a successful one-shot record at or after
the scheduled timestamp as a tombstone and skips that completed definition.
Do not delete this history while the matching definition remains in the repo.

## Diagnosis

1. Compare repo and live job IDs:

```bash
ssh dylans-mac-mini 'python3 - <<"PY"
import json
from pathlib import Path

home = Path.home()
repo = json.loads((home / "dotfiles/openclaw/cron/jobs.json").read_text())
live = json.loads((home / ".openclaw/cron/jobs.json").read_text())
repo_ids = {job["id"] for job in repo["jobs"]}
live_ids = {job["id"] for job in live["jobs"]}
print("repo_only", sorted(repo_ids - live_ids))
print("live_only", sorted(live_ids - repo_ids))
PY'
```

2. Inspect the suspect history without deleting it:

```bash
ssh dylans-mac-mini 'tail -20 ~/.openclaw/cron/runs/<job-id>.jsonl'
```

3. Check whether execution times follow `dotfiles-pull`:

```bash
ssh dylans-mac-mini 'tail -50 ~/.openclaw/logs/dotfiles-pull.log'
```

## Solution

1. Remove the completed definition from the repo and commit it.
2. Remove it from gateway state for immediate effect:

```bash
openclaw cron rm <job-id>
```

3. Deploy the canonical file:

```bash
~/dotfiles/openclaw/sync-cron-jobs.sh deploy
```

4. Keep `runs/<job-id>.jsonl` as audit history and a tombstone.

If the repo and live files are both clean but a current-version gateway still
executes the job, stop the gateway and move the run file to a quarantine name
before restarting. Do this only after reproducing independent scheduling from
the orphan file; an orphan history file alone is not evidence of an active job.

## Prevention

- Give every side-effecting one-shot an idempotency preflight against both the
  external system and its matching calendar event.
- Use `delivery.mode: none`; have the agent send exactly one final status so a
  delivery failure cannot turn a successful booking into a cron retry.
- Keep `deleteAfterRun: true` and preserve successful run history.
- Remove completed definitions from the repo promptly for inventory clarity.

## Verification

```bash
openclaw cron list --json | grep <job-id>            # empty
grep <job-id> ~/.openclaw/cron/jobs.json             # empty
grep <job-id> ~/dotfiles/openclaw/cron/jobs.json     # empty
~/dotfiles/openclaw/sync-cron-jobs.sh deploy         # does not restore it
```

The historical run file may remain. Verify that no new line appears after a
scheduled or manual deployment.
