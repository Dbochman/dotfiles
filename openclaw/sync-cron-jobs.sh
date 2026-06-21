#!/usr/bin/env bash
# sync-cron-jobs.sh — Sync OpenClaw cron job definitions between dotfiles and live config.
#
# Usage:
#   sync-cron-jobs.sh save    — Strip state from live jobs.json, save definitions to dotfiles
#   sync-cron-jobs.sh deploy  — Merge definitions, preserve state, skip completed one-shots
#
# Files:
#   dotfiles/openclaw/cron/jobs.json  — Job definitions (no state), tracked in git
#   ~/.openclaw/cron/jobs.json        — Live file with runtime state, NOT tracked

set -euo pipefail

DOTFILES_JOBS="$HOME/dotfiles/openclaw/cron/jobs.json"
LIVE_JOBS="$HOME/.openclaw/cron/jobs.json"

usage() {
  echo "Usage: $0 {save|deploy}" >&2
  exit 1
}

[ $# -eq 1 ] || usage

case "$1" in
  save)
    # Strip state from live file → dotfiles
    [ -f "$LIVE_JOBS" ] || { echo "Error: $LIVE_JOBS not found" >&2; exit 1; }
    python3 -c "
import json, sys
with open('$LIVE_JOBS') as f:
    data = json.load(f)
for job in data['jobs']:
    job.pop('state', None)
with open('$DOTFILES_JOBS', 'w') as f:
    json.dump(data, f, indent=2)
    f.write('\n')
print(f'Saved {len(data[\"jobs\"])} job definitions to {\"$DOTFILES_JOBS\"} (state stripped)')
"
    ;;

  deploy)
    # Merge definitions from dotfiles into live file, preserving existing state
    [ -f "$DOTFILES_JOBS" ] || { echo "Error: $DOTFILES_JOBS not found" >&2; exit 1; }
    python3 -c "
import json, os, tempfile
from datetime import datetime

dotfiles_path = '$DOTFILES_JOBS'
live_path = '$LIVE_JOBS'
runs_dir = os.path.join(os.path.dirname(live_path), 'runs')

with open(dotfiles_path) as f:
    new_defs = json.load(f)


def completed_one_shot(job):
    schedule = job.get('schedule', {})
    if schedule.get('kind') != 'at' or not job.get('deleteAfterRun'):
        return False

    scheduled_at = schedule.get('at')
    if not isinstance(scheduled_at, str):
        return False
    try:
        scheduled_ms = int(
            datetime.fromisoformat(scheduled_at.replace('Z', '+00:00')).timestamp() * 1000
        )
    except ValueError:
        return False

    run_path = os.path.join(runs_dir, job['id'] + '.jsonl')
    if not os.path.isfile(run_path):
        return False

    with open(run_path) as run_file:
        for line in run_file:
            try:
                record = json.loads(line)
                run_at_ms = int(record.get('runAtMs', 0))
            except (json.JSONDecodeError, TypeError, ValueError):
                continue
            if record.get('status') == 'ok' and run_at_ms >= scheduled_ms:
                return True
    return False


# A successful deleteAfterRun job is absent from the live file by design. Keep
# its append-only run history as a tombstone so a later repo deploy cannot
# resurrect the completed definition and execute its side effects again.
completed_ids = [
    job['id'] for job in new_defs['jobs'] if completed_one_shot(job)
]
new_defs['jobs'] = [
    job for job in new_defs['jobs'] if job['id'] not in completed_ids
]

# Load existing state from live file (if it exists)
state_by_id = {}
if os.path.exists(live_path):
    with open(live_path) as f:
        live_data = json.load(f)
    for job in live_data['jobs']:
        if 'state' in job:
            state_by_id[job['id']] = job['state']

# Merge: definitions from dotfiles + state from live
for job in new_defs['jobs']:
    if job['id'] in state_by_id:
        job['state'] = state_by_id[job['id']]

# Atomic write: write to sibling tmp, fsync, rename. os.replace is a directory
# entry swap and doesn't need extra filesystem space, so tight-disk conditions
# don't silently wedge the sync (which happened 2026-04-14 → 2026-04-18 when
# the .bak copy_file failed with ENOSPC and the whole sync bailed).
target_dir = os.path.dirname(live_path) or '.'
fd, tmp_path = tempfile.mkstemp(prefix='.jobs.', suffix='.tmp', dir=target_dir)
try:
    with os.fdopen(fd, 'w') as f:
        json.dump(new_defs, f, indent=2)
        f.write('\n')
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp_path, live_path)
except Exception:
    if os.path.exists(tmp_path):
        os.unlink(tmp_path)
    raise

preserved = sum(1 for j in new_defs['jobs'] if 'state' in j)
completed_summary = ', '.join(completed_ids) if completed_ids else 'none'
print(
    f'Deployed {len(new_defs[\"jobs\"])} jobs to {live_path} '
    f'({preserved} with preserved state; completed one-shots skipped: {completed_summary})'
)
"
    ;;

  *)
    usage
    ;;
esac
