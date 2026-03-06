---
name: openclaw-cron-ghost-jobs
description: |
  Fix OpenClaw cron jobs running after being removed from jobs.json. Use when:
  (1) A cron job keeps executing even though it was deleted from jobs.json,
  (2) Duplicate deliveries (e.g., Julia gets morning briefing twice),
  (3) Old/legacy jobs firing alongside their replacements after a migration.
  Root cause is orphan run state files in ~/.openclaw/cron/runs/ that persist
  nextRunAtMs independently of the job definition.
author: Claude Code
version: 1.0.0
date: 2026-03-06
---

# OpenClaw Cron Ghost Jobs

## Problem
After removing a cron job from `~/.openclaw/cron/jobs.json`, the job continues to
execute on its previous schedule. This causes duplicate deliveries when a new
replacement job is added alongside the still-running old one.

## Context / Trigger Conditions

- A cron job was removed from `jobs.json` but still fires
- Duplicate messages delivered to the same target (e.g., two morning briefings)
- Old/legacy jobs run alongside their replacements after a migration
- `ls ~/.openclaw/cron/runs/` shows JSONL files for job IDs not in `jobs.json`

## Root Cause

OpenClaw's cron subsystem persists run state in per-job JSONL files at
`~/.openclaw/cron/runs/<job-id>.jsonl`. Each run appends a JSON line with
`nextRunAtMs` indicating when to fire next. The cron scheduler reads these
files independently — if a run file exists with a future `nextRunAtMs`, the
job will execute even if its definition is gone from `jobs.json`.

Removing a job from `jobs.json` does NOT automatically clean up its run file.

## Diagnosis

### Step 1: Identify orphan run files
```bash
ssh dylans-mac-mini 'python3 -c "
import json, os
with open(\"/Users/dbochman/.openclaw/cron/jobs.json\") as f:
    job_ids = {j[\"id\"] for j in json.load(f)[\"jobs\"]}
for f in sorted(os.listdir(\"/Users/dbochman/.openclaw/cron/runs/\")):
    run_id = f.replace(\".jsonl\", \"\")
    if run_id not in job_ids:
        print(f\"ORPHAN: {f}\")
"'
```

### Step 2: Check which orphans have future runs scheduled
```bash
ssh dylans-mac-mini 'python3 -c "
import json, os, time
runs_dir = \"/Users/dbochman/.openclaw/cron/runs/\"
now_ms = int(time.time() * 1000)
for f in sorted(os.listdir(runs_dir)):
    with open(os.path.join(runs_dir, f)) as fh:
        lines = fh.readlines()
    if lines:
        last = json.loads(lines[-1])
        next_run = last.get(\"nextRunAtMs\", 0)
        if next_run > now_ms:
            print(f\"ACTIVE GHOST: {f} nextRun={next_run}\")
"'
```

## Solution

Delete the orphan run files:
```bash
ssh dylans-mac-mini 'trash ~/.openclaw/cron/runs/<orphan-job-id>.jsonl'
```

No gateway restart is needed — the cron subsystem will simply not find the
run file on its next timer tick.

## Prevention

When removing cron jobs from `jobs.json`, always also delete their run files:
```bash
# Remove from jobs.json (edit the file)
# Then clean up the run file
trash ~/.openclaw/cron/runs/<job-id>.jsonl
```

## Verification

After cleanup:
```bash
# Only active job IDs should have run files
ls ~/.openclaw/cron/runs/
# Compare against:
python3 -c "import json; [print(j['id']) for j in json.load(open('/Users/dbochman/.openclaw/cron/jobs.json'))['jobs']]"
```

## Notes

- Run files are append-only JSONL — each line is a run record with status, summary, usage, nextRunAtMs
- One-shot jobs (`deleteAfterRun: true`) clean up their own run files after execution
- Recurring cron jobs (`kind: "cron"`) accumulate run history indefinitely
- The gateway hot-reloads `jobs.json` but does NOT scan for orphan run files
