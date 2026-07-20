#!/usr/bin/env bash
# sync-cron-jobs.sh — Sync OpenClaw cron job definitions between dotfiles and live config.
#
# Usage:
#   sync-cron-jobs.sh save    — Save SQLite definitions to dotfiles (legacy JSON fallback)
#   sync-cron-jobs.sh deploy  — Reconcile definitions, preserve state, skip completed one-shots
#
# Files:
#   dotfiles/openclaw/cron/jobs.json  — Job definitions (no state), tracked in git
#   ~/.openclaw/state/openclaw.sqlite — Live definitions, runtime state, and run history
#   ~/.openclaw/cron/jobs.json        — Temporary legacy import bridge, archived by doctor

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
  if [ ! -f "$SECRETS_CACHE" ] || [ -L "$SECRETS_CACHE" ]; then
    echo "Error: protected cron identity cache must be a regular non-symlink file" >&2
    return 1
  fi
  local owner mode key value
  if stat -f '%u %Lp' "$SECRETS_CACHE" >/dev/null 2>&1; then
    read -r owner mode < <(stat -f '%u %Lp' "$SECRETS_CACHE")
  else
    read -r owner mode < <(stat -c '%u %a' "$SECRETS_CACHE")
  fi
  if [ "$owner" != "$(id -u)" ] || [ "$mode" != "600" ]; then
    echo "Error: protected cron identity cache must be owner-owned mode 0600" >&2
    return 1
  fi
  set -a
  # shellcheck disable=SC1090
  if ! . "$SECRETS_CACHE" >/dev/null 2>&1; then
    set +a
    echo "Error: protected cron identity cache could not be loaded" >&2
    return 1
  fi
  set +a
  for key in DYLAN_EMAIL JULIA_EMAIL HOUSEHOLD_CHAT_ID JULIA_CHAT_ID DYLAN_CHAT_ID; do
    value="${!key:-}"
    if [ -z "$value" ]; then
      echo "Error: protected cron identity $key is missing" >&2
      return 1
    fi
  done
  for key in HOUSEHOLD_CHAT_ID JULIA_CHAT_ID DYLAN_CHAT_ID; do
    value="${!key}"
    if [[ ! "$value" =~ ^[0-9]+$ ]]; then
      echo "Error: protected cron identity $key must be numeric" >&2
      return 1
    fi
  done
}

[ $# -eq 1 ] || usage

case "$1" in
  save)
    source_openclaw_secrets
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


PRIVATE_KEYS = (
    "DYLAN_EMAIL",
    "JULIA_EMAIL",
    "HOUSEHOLD_CHAT_ID",
    "JULIA_CHAT_ID",
    "DYLAN_CHAT_ID",
)


def redact_private_values(value):
    if isinstance(value, dict):
        return {key: redact_private_values(item) for key, item in value.items()}
    if isinstance(value, list):
        return [redact_private_values(item) for item in value]
    if isinstance(value, str):
        for key in ("DYLAN_EMAIL", "JULIA_EMAIL"):
            value = value.replace(os.environ[key], "${" + key + "}")
        value = value.replace(
            "chat-id " + os.environ["HOUSEHOLD_CHAT_ID"],
            "chat-id ${HOUSEHOLD_CHAT_ID}",
        )
        value = value.replace(
            "chat_id:" + os.environ["JULIA_CHAT_ID"],
            "chat_id:${JULIA_CHAT_ID}",
        )
        value = value.replace(
            "chat_id:" + os.environ["DYLAN_CHAT_ID"],
            "chat_id:${DYLAN_CHAT_ID}",
        )
    return value


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
data = redact_private_values(data)
with open(dotfiles_path, "w") as f:
    json.dump(data, f, indent=2)
    f.write("\n")
print(f"Saved {len(data['jobs'])} job definitions to {dotfiles_path} from {source} (state stripped)")
PY
    ;;

  deploy)
    # Stage definitions for the SQLite migration bridge, preserving safe state.
    [ -f "$DOTFILES_JOBS" ] || { echo "Error: $DOTFILES_JOBS not found" >&2; exit 1; }
    if ! source_openclaw_secrets; then
      echo "Error: protected identities are not ready; live cron definitions were not deployed" >&2
      exit 1
    fi
    CRON_UPDATE_PLAN=$(mktemp "${TMPDIR:-/tmp}/openclaw-cron-update.XXXXXX")
    CRON_GATEWAY_SNAPSHOT=$(mktemp "${TMPDIR:-/tmp}/openclaw-cron-list.XXXXXX")
    CRON_EXPECTED_SNAPSHOT=$(mktemp "${TMPDIR:-/tmp}/openclaw-cron-expected.XXXXXX")
    chmod 600 "$CRON_UPDATE_PLAN" "$CRON_GATEWAY_SNAPSHOT" "$CRON_EXPECTED_SNAPSHOT"
    export CRON_UPDATE_PLAN CRON_GATEWAY_SNAPSHOT CRON_EXPECTED_SNAPSHOT
    trap 'rm -f "$CRON_UPDATE_PLAN" "$CRON_GATEWAY_SNAPSHOT" "$CRON_EXPECTED_SNAPSHOT"' EXIT
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

PRIVATE_KEYS = (
    "DYLAN_EMAIL",
    "JULIA_EMAIL",
    "HOUSEHOLD_CHAT_ID",
    "JULIA_CHAT_ID",
    "DYLAN_CHAT_ID",
)


def expand_private_values(value):
    if isinstance(value, dict):
        return {key: expand_private_values(item) for key, item in value.items()}
    if isinstance(value, list):
        return [expand_private_values(item) for item in value]
    if isinstance(value, str):
        for key in PRIVATE_KEYS:
            value = value.replace("${" + key + "}", os.environ[key])
    return value


new_defs = expand_private_values(new_defs)


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

# Load existing definitions and state from the legacy bridge (if it exists),
# then let SQLite override them because it is the executable source of truth.
state_by_id = {}
live_jobs_by_id = {}
sqlite_job_ids = set()
next_run_by_id = {}
last_run_by_id = {}


def effective_delete_after_run(job):
    if isinstance(job.get('deleteAfterRun'), bool):
        return job['deleteAfterRun']
    return job.get('schedule', {}).get('kind') == 'at'


def schedules_equal(desired, live):
    if not isinstance(desired, dict) or not isinstance(live, dict):
        return desired == live
    if desired.get('kind') != live.get('kind'):
        return False
    if desired.get('kind') == 'at':
        return desired.get('at') == live.get('at')
    if desired.get('kind') == 'every':
        if desired.get('everyMs') != live.get('everyMs'):
            return False
        return 'anchorMs' not in desired or desired.get('anchorMs') == live.get('anchorMs')
    if desired.get('kind') == 'cron':
        if desired.get('expr') != live.get('expr') or desired.get('tz') != live.get('tz'):
            return False
        # OpenClaw may persist its computed default stagger. An omitted
        # canonical stagger delegates to that default and is not drift.
        return 'staggerMs' not in desired or desired.get('staggerMs') == live.get('staggerMs')
    return desired == live


DELIVERY_NULLABLE_FIELDS = (
    'channel',
    'to',
    'threadId',
    'accountId',
    'completionDestination',
    'failureDestination',
)


def deliveries_equal(desired, live):
    """Compare delivery policy after normalizing the false default."""
    if desired == live:
        return True
    if not isinstance(desired, dict) or not isinstance(live, dict):
        return False
    desired = dict(desired)
    live = dict(live)
    if desired.get('bestEffort') is False:
        desired.pop('bestEffort')
    if live.get('bestEffort') is False:
        live.pop('bestEffort')
    return desired == live


def delivery_patch(desired, live):
    """Build a merge-safe patch that removes omitted stale destinations."""
    patch = dict(desired)
    if not isinstance(live, dict):
        return patch
    for key in DELIVERY_NULLABLE_FIELDS:
        if key not in desired and key in live:
            patch[key] = None
    if 'bestEffort' not in desired and live.get('bestEffort') is True:
        # OpenClaw's public update schema cannot null-clear this boolean.
        patch['bestEffort'] = False
    return patch


def definition_patch(desired, live):
    """Return only changed public cron.update fields.

    Omitting unchanged schedule/enabled fields is important: their mere
    presence makes the gateway recompute nextRunAtMs and would erase a valid
    failed-run retry backoff. Nested payload and delivery values are supplied
    in full when either differs so their canonical content wins.
    """
    patch = {}

    for key in ('name', 'agentId', 'sessionKey', 'description'):
        desired_value = desired.get(key)
        live_value = live.get(key)
        if desired_value == live_value:
            continue
        if key in ('agentId', 'sessionKey'):
            patch[key] = desired_value
        elif key == 'description' and desired_value is None:
            # The update schema accepts an empty description, which the
            # gateway normalizes back to an absent optional value.
            patch[key] = ''
        elif desired_value is not None:
            patch[key] = desired_value

    desired_enabled = desired.get('enabled', True)
    if desired_enabled != live.get('enabled', True):
        patch['enabled'] = desired_enabled

    desired_delete = effective_delete_after_run(desired)
    if desired_delete != effective_delete_after_run(live):
        patch['deleteAfterRun'] = desired_delete

    if not schedules_equal(desired.get('schedule'), live.get('schedule')):
        patch['schedule'] = desired.get('schedule')

    for key in ('sessionTarget', 'wakeMode', 'payload'):
        if desired.get(key) != live.get(key) and key in desired:
            patch[key] = desired[key]

    desired_delivery = desired.get('delivery')
    live_delivery = live.get('delivery')
    if 'delivery' in desired and not deliveries_equal(desired_delivery, live_delivery):
        patch['delivery'] = (
            delivery_patch(desired_delivery, live_delivery)
            if isinstance(desired_delivery, dict)
            else desired_delivery
        )

    desired_failure_alert = desired.get('failureAlert')
    live_failure_alert = live.get('failureAlert')
    failure_alert_matches = desired_failure_alert == live_failure_alert or (
        desired_failure_alert is None and live_failure_alert is False
    )
    if not failure_alert_matches:
        # `false` is the public update contract's explicit disabled value.
        patch['failureAlert'] = (
            desired_failure_alert if desired_failure_alert is not None else False
        )

    return patch


def scheduled_at_ms(schedule):
    if not isinstance(schedule, dict) or schedule.get('kind') != 'at':
        return None
    value = schedule.get('at')
    if not isinstance(value, str):
        return None
    try:
        return int(datetime.fromisoformat(value.replace('Z', '+00:00')).timestamp() * 1000)
    except ValueError:
        return None


if os.path.exists(live_path):
    with open(live_path) as f:
        live_data = json.load(f)
    for job in live_data.get('jobs', []):
        if not isinstance(job, dict) or not isinstance(job.get('id'), str):
            continue
        live_jobs_by_id[job['id']] = job
        next_run_by_id[job['id']] = job.get('state', {}).get('nextRunAtMs')
        last_run_by_id[job['id']] = job.get('state', {}).get('lastRunAtMs')
        if 'state' in job:
            state_by_id[job['id']] = job['state']
if os.path.isfile(sqlite_path):
    try:
        with sqlite3.connect(sqlite_path) as conn:
            rows = conn.execute(
                """
                SELECT job_id, state_json, job_json, next_run_at_ms, last_run_at_ms
                  FROM cron_jobs
                 WHERE store_key = ?
                """,
                (live_path,),
            ).fetchall()
            if not rows:
                rows = conn.execute(
                    """
                    SELECT job_id, state_json, job_json, next_run_at_ms, last_run_at_ms
                      FROM cron_jobs
                    """
                ).fetchall()
    except sqlite3.Error:
        rows = []
    for job_id, raw_state, raw_job, next_run_at_ms, last_run_at_ms in rows:
        try:
            state = json.loads(raw_state or '{}')
        except json.JSONDecodeError:
            state = {}
        if isinstance(state, dict):
            state_by_id[job_id] = state
        try:
            live_job = json.loads(raw_job or '{}')
        except json.JSONDecodeError:
            live_job = {}
        if isinstance(live_job, dict):
            live_jobs_by_id[job_id] = live_job
            sqlite_job_ids.add(job_id)
        next_run_by_id[job_id] = next_run_at_ms
        last_run_by_id[job_id] = last_run_at_ms

# Merge definitions from dotfiles with non-scheduling runtime state. Existing
# SQLite definitions are reconciled through the gateway before doctor imports
# newly staged IDs. A changed schedule or enabled flag must never retain
# nextRunAtMs/runningAtMs from the previous schedule.
cron_updates = []
for job in new_defs['jobs']:
    job_id = job['id']
    live_job = live_jobs_by_id.get(job_id)
    patch = definition_patch(job, live_job) if live_job is not None else {}
    scheduling_changed = 'enabled' in patch or 'schedule' in patch
    expected_at_ms = scheduled_at_ms(job.get('schedule'))
    stale_at_runtime = (
        live_job is not None
        and job.get('enabled', True)
        and expected_at_ms is not None
        and next_run_by_id.get(job_id) != expected_at_ms
        # A failed one-shot legitimately moves nextRunAtMs to retry backoff.
        # Repair only never-run jobs; explicit schedule changes always repair.
        and last_run_by_id.get(job_id) is None
    )

    if job_id in state_by_id:
        state = dict(state_by_id[job_id])
        if scheduling_changed or stale_at_runtime:
            state.pop('nextRunAtMs', None)
            state.pop('runningAtMs', None)
        job['state'] = state

    if stale_at_runtime and 'enabled' not in patch and 'schedule' not in patch:
        patch['schedule'] = job.get('schedule')

    # Doctor imports newly staged IDs. Only IDs already present in SQLite can
    # be updated through cron.update without an "id not found" failure.
    if patch and job_id in sqlite_job_ids:
        cron_updates.append({
            'id': job_id,
            'patch': patch,
        })

with open(os.environ['CRON_UPDATE_PLAN'], 'w') as update_file:
    for params in cron_updates:
        update_file.write(
            params['id'] + '\t' + json.dumps(params, separators=(',', ':')) + '\n'
        )

# Doctor may archive the legacy bridge after importing it. Keep an owner-only
# expected-definition snapshot for the mandatory post-deploy parity read-back.
with open(os.environ['CRON_EXPECTED_SNAPSHOT'], 'w') as expected_file:
    json.dump(new_defs, expected_file, separators=(',', ':'))
    expected_file.write('\n')
    expected_file.flush()
    os.fsync(expected_file.fileno())

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
update_summary = ', '.join(item['id'] for item in cron_updates) if cron_updates else 'none'
print(
    f'Deployed {len(new_defs["jobs"])} jobs to {live_path} '
    f'({preserved} with preserved state; completed one-shots skipped: {completed_summary}; '
    f'gateway reconciliations planned: {update_summary})'
)
PY
    if [ -s "$CRON_UPDATE_PLAN" ] && [ -f "$SQLITE_DB" ]; then
      command -v openclaw >/dev/null 2>&1 || {
        echo "Error: cron definition reconciliation required but openclaw is not on PATH" >&2
        exit 1
      }
      source_openclaw_secrets
      while IFS=$'\t' read -r JOB_ID UPDATE_PARAMS; do
        if UPDATE_OUT=$(openclaw gateway call cron.update --json --timeout 30000 \
          --params "$UPDATE_PARAMS" 2>&1); then
          echo "Reconciled cron definition through gateway: $JOB_ID"
        else
          echo "Error: could not reconcile cron definition for $JOB_ID:" >&2
          printf '%s\n' "$UPDATE_OUT" >&2
          exit 1
        fi
      done < "$CRON_UPDATE_PLAN"
    fi
    if [ -f "$SQLITE_DB" ]; then
      command -v openclaw >/dev/null 2>&1 || {
        echo "Error: OpenClaw SQLite cron store exists but openclaw is not on PATH" >&2
        exit 1
      }
      source_openclaw_secrets
      # Doctor evaluates every skill requirement and persists enabled=false for
      # unavailable skills. Scope the gateway's managed-wrapper path to this
      # broad repair subprocess so cron migration cannot disable otherwise
      # healthy skills, without changing command resolution for the rest of
      # reconciliation.
      if DOCTOR_OUT=$(PATH="$HOME/.openclaw/bin:$PATH" \
        openclaw doctor --fix --non-interactive --yes 2>&1); then
        if printf '%s\n' "$DOCTOR_OUT" | grep -q "Cron store migrated"; then
          echo "Normalized cron store through OpenClaw doctor (SQLite)"
        else
          echo "OpenClaw doctor completed; SQLite cron store already normalized"
        fi
      else
        echo "Error: OpenClaw doctor could not normalize cron store:" >&2
        printf '%s\n' "$DOCTOR_OUT" >&2
        exit 1
      fi

      # Exit status alone cannot prove that a gateway update or doctor import
      # changed the executable store. Read both SQLite and the active Gateway
      # back and compare their exact ID sets and every canonical definition
      # field before reporting a successful deployment.
      if ! openclaw cron list --all --json > "$CRON_GATEWAY_SNAPSHOT" 2>/dev/null; then
        echo "Error: could not read the active Gateway cron definitions after deployment" >&2
        exit 1
      fi
      if ! python3 <<'PY'
import json
import os
import sqlite3
import sys

live_path = os.environ["LIVE_JOBS"]
expected_path = os.environ["CRON_EXPECTED_SNAPSHOT"]
sqlite_path = os.environ["SQLITE_DB"]
gateway_path = os.environ["CRON_GATEWAY_SNAPSHOT"]


def load_jobs(path):
    with open(path, encoding="utf-8") as handle:
        payload = json.load(handle)
    jobs = payload.get("jobs") if isinstance(payload, dict) else None
    if not isinstance(jobs, list):
        raise ValueError("jobs list is missing")
    return jobs


def by_id(jobs):
    result = {}
    for job in jobs:
        if not isinstance(job, dict) or not isinstance(job.get("id"), str):
            raise ValueError("job definition is malformed")
        if job["id"] in result:
            raise ValueError("duplicate job definition")
        result[job["id"]] = job
    return result


def schedules_equal(desired, actual):
    if not isinstance(desired, dict) or not isinstance(actual, dict):
        return desired == actual
    if desired.get("kind") != actual.get("kind"):
        return False
    if desired.get("kind") == "at":
        return desired.get("at") == actual.get("at")
    if desired.get("kind") == "every":
        return desired.get("everyMs") == actual.get("everyMs") and (
            "anchorMs" not in desired
            or desired.get("anchorMs") == actual.get("anchorMs")
        )
    if desired.get("kind") == "cron":
        return (
            desired.get("expr") == actual.get("expr")
            and desired.get("tz") == actual.get("tz")
            and (
                "staggerMs" not in desired
                or desired.get("staggerMs") == actual.get("staggerMs")
            )
        )
    return desired == actual


def effective_delete_after_run(job):
    if isinstance(job.get("deleteAfterRun"), bool):
        return job["deleteAfterRun"]
    return isinstance(job.get("schedule"), dict) and job["schedule"].get("kind") == "at"


def deliveries_equal(desired, actual):
    if desired == actual:
        return True
    if not isinstance(desired, dict) or not isinstance(actual, dict):
        return False
    desired = dict(desired)
    actual = dict(actual)
    if desired.get("bestEffort") is False:
        desired.pop("bestEffort")
    if actual.get("bestEffort") is False:
        actual.pop("bestEffort")
    return desired == actual


def definitions_equal(desired, actual):
    if desired.get("id") != actual.get("id"):
        return False
    for key in ("name", "agentId", "sessionKey"):
        if desired.get(key) != actual.get(key):
            return False
    desired_description = desired.get("description") or None
    actual_description = actual.get("description") or None
    if desired_description != actual_description:
        return False
    if desired.get("enabled", True) != actual.get("enabled", True):
        return False
    if effective_delete_after_run(desired) != effective_delete_after_run(actual):
        return False
    if not schedules_equal(desired.get("schedule"), actual.get("schedule")):
        return False
    for key in ("sessionTarget", "wakeMode", "payload"):
        if desired.get(key) != actual.get(key):
            return False
    if not deliveries_equal(desired.get("delivery"), actual.get("delivery")):
        return False
    desired_alert = desired.get("failureAlert")
    actual_alert = actual.get("failureAlert")
    if not (
        desired_alert == actual_alert
        or desired_alert is None and actual_alert is False
    ):
        return False
    return True


try:
    desired = by_id(load_jobs(expected_path))
    gateway = by_id(load_jobs(gateway_path))
    uri = "file:" + sqlite_path + "?mode=ro"
    with sqlite3.connect(uri, uri=True) as connection:
        rows = connection.execute(
            "SELECT job_id, job_json FROM cron_jobs WHERE store_key = ?",
            (live_path,),
        ).fetchall()
        if not rows:
            rows = connection.execute(
                "SELECT job_id, job_json FROM cron_jobs"
            ).fetchall()
    sqlite_jobs = []
    for job_id, raw_job in rows:
        job = json.loads(raw_job)
        if not isinstance(job, dict) or job.get("id") != job_id:
            raise ValueError("SQLite job definition is malformed")
        sqlite_jobs.append(job)
    sqlite_defs = by_id(sqlite_jobs)
except (OSError, ValueError, json.JSONDecodeError, sqlite3.Error) as exc:
    print("Error: post-deploy cron definition verification could not read a valid store", file=sys.stderr)
    raise SystemExit(1) from exc

desired_ids = set(desired)
id_sets_match = True
for label, actual in (("SQLite", sqlite_defs), ("Gateway", gateway)):
    actual_ids = set(actual)
    missing = sorted(desired_ids - actual_ids)
    extra = sorted(actual_ids - desired_ids)
    if missing or extra:
        print(
            f"Error: post-deploy {label} cron ID set mismatch: "
            f"missing={missing}; extra={extra}",
            file=sys.stderr,
        )
        id_sets_match = False
if not id_sets_match:
    raise SystemExit(1)

for job_id, definition in desired.items():
    sqlite_definition = sqlite_defs.get(job_id)
    gateway_definition = gateway.get(job_id)
    if sqlite_definition is None or not definitions_equal(definition, sqlite_definition):
        print(f"Error: post-deploy SQLite cron definition mismatch: {job_id}", file=sys.stderr)
        raise SystemExit(1)
    if gateway_definition is None or not definitions_equal(definition, gateway_definition):
        print(f"Error: post-deploy Gateway cron definition mismatch: {job_id}", file=sys.stderr)
        raise SystemExit(1)

print(f"Verified exact parity for {len(desired)} cron definitions in SQLite and the active Gateway")
PY
      then
        exit 1
      fi
    fi
    ;;

  *)
    usage
    ;;
esac
