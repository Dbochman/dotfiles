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

DOTFILES_JOBS="${DOTFILES_JOBS:-$HOME/dotfiles/openclaw/cron/jobs.json}"
LIVE_JOBS="${LIVE_JOBS:-$HOME/.openclaw/cron/jobs.json}"
SQLITE_DB="${SQLITE_DB:-$HOME/.openclaw/state/openclaw.sqlite}"
SECRETS_CACHE="${OPENCLAW_SECRETS_CACHE:-$HOME/.openclaw/.secrets-cache}"
export DOTFILES_JOBS LIVE_JOBS SQLITE_DB

usage() {
  echo "Usage: $0 {save|deploy}" >&2
  exit 1
}

source_openclaw_secrets() {
  [ -f "$SECRETS_CACHE" ] || return 0
  set -a
  # shellcheck disable=SC1090
  . "$SECRETS_CACHE"
  set +a
}

[ $# -eq 1 ] || usage

case "$1" in
  save)
    # Strip state from live file → dotfiles. OpenClaw 2026.6 migrates cron
    # jobs into SQLite and archives the legacy JSON file, so prefer SQLite
    # when the live JSON store is gone.
    python3 <<'PY'
import json
import os
import sqlite3
import sys

dotfiles_path = os.environ["DOTFILES_JOBS"]
live_path = os.environ["LIVE_JOBS"]
sqlite_path = os.environ["SQLITE_DB"]


def strip_state(job):
    job = dict(job)
    job.pop("state", None)
    return job


def load_from_legacy_json():
    if not os.path.isfile(live_path):
        return None
    with open(live_path) as f:
        data = json.load(f)
    jobs = [strip_state(job) for job in data.get("jobs", []) if isinstance(job, dict)]
    return {"version": data.get("version", 1), "jobs": jobs}, "legacy JSON"


def load_from_sqlite():
    if not os.path.isfile(sqlite_path):
        return None
    with sqlite3.connect(sqlite_path) as conn:
        rows = conn.execute(
            """
            SELECT job_json
              FROM cron_jobs
             WHERE store_key = ?
             ORDER BY sort_order ASC, updated_at ASC, job_id ASC
            """,
            (live_path,),
        ).fetchall()
        if not rows:
            rows = conn.execute(
                """
                SELECT job_json
                  FROM cron_jobs
                 ORDER BY store_key ASC, sort_order ASC, updated_at ASC, job_id ASC
                """
            ).fetchall()
    jobs = []
    for (raw_job,) in rows:
        try:
            job = json.loads(raw_job)
        except (TypeError, json.JSONDecodeError):
            continue
        if isinstance(job, dict):
            jobs.append(strip_state(job))
    if not jobs:
        return None
    return {"version": 1, "jobs": jobs}, "SQLite"


loaded = load_from_legacy_json() or load_from_sqlite()
if not loaded:
    print(f"Error: no cron jobs found at {live_path} or {sqlite_path}", file=sys.stderr)
    sys.exit(1)

data, source = loaded
with open(dotfiles_path, "w") as f:
    json.dump(data, f, indent=2)
    f.write("\n")
print(f"Saved {len(data['jobs'])} job definitions to {dotfiles_path} from {source} (state stripped)")
PY
    ;;

  deploy)
    # Merge definitions from dotfiles into live file, preserving existing state
    [ -f "$DOTFILES_JOBS" ] || { echo "Error: $DOTFILES_JOBS not found" >&2; exit 1; }
    python3 <<'PY'
import json
import os
import sqlite3
import tempfile
from datetime import datetime

dotfiles_path = os.environ["DOTFILES_JOBS"]
live_path = os.environ["LIVE_JOBS"]
sqlite_path = os.environ["SQLITE_DB"]
runs_dir = os.path.join(os.path.dirname(live_path), 'runs')

with open(dotfiles_path) as f:
    new_defs = json.load(f)

if not isinstance(new_defs.get('jobs'), list):
    raise SystemExit(f'Error: {dotfiles_path} is missing jobs[]')


def iter_jsonl(path):
    if not os.path.isfile(path):
        return
    with open(path) as run_file:
        for line in run_file:
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(record, dict):
                yield record


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
    for record in iter_jsonl(run_path) or []:
        try:
            run_at_ms = int(record.get('runAtMs', 0))
        except (TypeError, ValueError):
            continue
        if record.get('status') == 'ok' and run_at_ms >= scheduled_ms:
            return True

    if not os.path.isfile(sqlite_path):
        return False
    try:
        with sqlite3.connect(sqlite_path) as conn:
            rows = conn.execute(
                """
                SELECT entry_json
                  FROM cron_run_logs
                 WHERE job_id = ?
                   AND (store_key = ? OR ? NOT IN (SELECT DISTINCT store_key FROM cron_run_logs))
                """,
                (job['id'], live_path, live_path),
            ).fetchall()
    except sqlite3.Error:
        return False
    for (raw_record,) in rows:
        try:
            record = json.loads(raw_record)
            run_at_ms = int(record.get('runAtMs', 0))
        except (TypeError, ValueError, json.JSONDecodeError):
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
    for job in live_data.get('jobs', []):
        if 'state' in job:
            state_by_id[job['id']] = job['state']
if os.path.isfile(sqlite_path):
    try:
        with sqlite3.connect(sqlite_path) as conn:
            rows = conn.execute(
                """
                SELECT job_id, state_json
                  FROM cron_jobs
                 WHERE store_key = ?
                """,
                (live_path,),
            ).fetchall()
            if not rows:
                rows = conn.execute(
                    """
                    SELECT job_id, state_json
                      FROM cron_jobs
                    """
                ).fetchall()
    except sqlite3.Error:
        rows = []
    for job_id, raw_state in rows:
        try:
            state = json.loads(raw_state or '{}')
        except json.JSONDecodeError:
            continue
        if isinstance(state, dict):
            state_by_id[job_id] = state

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
    f'Deployed {len(new_defs["jobs"])} jobs to {live_path} '
    f'({preserved} with preserved state; completed one-shots skipped: {completed_summary})'
)
PY
    if [ -f "$SQLITE_DB" ] && command -v openclaw >/dev/null 2>&1; then
      source_openclaw_secrets
      if DOCTOR_OUT=$(openclaw doctor --fix --non-interactive --yes 2>&1); then
        if printf '%s\n' "$DOCTOR_OUT" | grep -q "Cron store migrated"; then
          echo "Normalized cron store through OpenClaw doctor (SQLite)"
        else
          echo "OpenClaw doctor completed; SQLite cron store already normalized"
        fi
      else
        echo "WARNING: OpenClaw doctor could not normalize cron store:" >&2
        printf '%s\n' "$DOCTOR_OUT" >&2
      fi
    fi
    ;;

  *)
    usage
    ;;
esac
