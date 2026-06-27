#!/usr/bin/env bash

set -euo pipefail

REPO_ROOT=$(cd "$(dirname "$0")/../.." && pwd)
TEST_HOME=$(mktemp -d)
trap 'rm -rf "$TEST_HOME"' EXIT

mkdir -p \
  "$TEST_HOME/dotfiles/openclaw/cron" \
  "$TEST_HOME/.openclaw/cron/runs"
cp "$REPO_ROOT/openclaw/sync-cron-jobs.sh" \
  "$TEST_HOME/dotfiles/openclaw/sync-cron-jobs.sh"
chmod +x "$TEST_HOME/dotfiles/openclaw/sync-cron-jobs.sh"

write_definitions() {
  printf '%s\n' '{
    "version": 1,
    "jobs": [
      {
        "id": "once",
        "enabled": true,
        "deleteAfterRun": true,
        "schedule": {"kind": "at", "at": "2020-01-01T00:00:00.000Z"}
      },
      {
        "id": "daily",
        "enabled": true,
        "schedule": {"kind": "cron", "expr": "0 7 * * *"}
      }
    ]
  }' > "$TEST_HOME/dotfiles/openclaw/cron/jobs.json"

  printf '%s\n' '{
    "version": 1,
    "jobs": [
      {
        "id": "daily",
        "enabled": true,
        "schedule": {"kind": "cron", "expr": "0 7 * * *"},
        "state": {"lastStatus": "ok", "nextRunAtMs": 1577862000000}
      }
    ]
  }' > "$TEST_HOME/.openclaw/cron/jobs.json"
}

assert_job_ids() {
  local expected="$1"
  HOME="$TEST_HOME" python3 - "$expected" <<'PY'
import json
import os
import sys

path = os.path.expanduser("~/.openclaw/cron/jobs.json")
with open(path) as jobs_file:
    jobs = json.load(jobs_file)["jobs"]
actual = ",".join(job["id"] for job in jobs)
if actual != sys.argv[1]:
    raise SystemExit(f"expected job ids {sys.argv[1]!r}, got {actual!r}")
daily = next(job for job in jobs if job["id"] == "daily")
if daily.get("state", {}).get("lastStatus") != "ok":
    raise SystemExit("recurring job state was not preserved")
PY
}

deploy() {
  HOME="$TEST_HOME" PATH="${TEST_PATH:-$PATH}" \
    "$TEST_HOME/dotfiles/openclaw/sync-cron-jobs.sh" deploy >/dev/null
}

write_definitions
printf '%s\n' \
  '{"status":"ok","runAtMs":1577836800000}' \
  > "$TEST_HOME/.openclaw/cron/runs/once.jsonl"
deploy
assert_job_ids "daily"

write_definitions
printf '%s\n' \
  '{"status":"error","runAtMs":1577836800000}' \
  > "$TEST_HOME/.openclaw/cron/runs/once.jsonl"
deploy
assert_job_ids "once,daily"

write_definitions
printf '%s\n' \
  'not-json' \
  '{"status":"ok","runAtMs":1577836799999}' \
  > "$TEST_HOME/.openclaw/cron/runs/once.jsonl"
deploy
assert_job_ids "once,daily"

write_definitions
python3 <<PY
import json

path = "$TEST_HOME/dotfiles/openclaw/cron/jobs.json"
with open(path) as source_file:
    data = json.load(source_file)
daily = next(job for job in data["jobs"] if job["id"] == "daily")
daily["schedule"]["expr"] = "0 8 * * *"
with open(path, "w") as source_file:
    json.dump(data, source_file, indent=2)
    source_file.write("\n")
PY
deploy
HOME="$TEST_HOME" python3 <<'PY'
import json
import os

path = os.path.expanduser("~/.openclaw/cron/jobs.json")
with open(path) as jobs_file:
    daily = next(job for job in json.load(jobs_file)["jobs"] if job["id"] == "daily")
if daily["schedule"]["expr"] != "0 8 * * *":
    raise SystemExit("changed schedule was not deployed")
if "nextRunAtMs" in daily.get("state", {}):
    raise SystemExit("changed schedule retained stale nextRunAtMs")
if daily.get("state", {}).get("lastStatus") != "ok":
    raise SystemExit("changed schedule did not preserve non-scheduling state")
PY

# SQLite-backed deployments must reconcile scheduling state through the live
# gateway before doctor normalizes the staged legacy file. Use a fake openclaw
# binary so this test never contacts the real gateway.
mkdir -p "$TEST_HOME/fake-bin" "$TEST_HOME/.openclaw/state"
OPENCLAW_CALL_LOG="$TEST_HOME/openclaw.calls"
export OPENCLAW_CALL_LOG
printf '%s\n' \
  '#!/bin/bash' \
  'printf "%s\n" "$*" >> "$OPENCLAW_CALL_LOG"' \
  'if [ "$1" = "doctor" ]; then echo "Cron store migrated"; fi' \
  > "$TEST_HOME/fake-bin/openclaw"
chmod +x "$TEST_HOME/fake-bin/openclaw"
TEST_PATH="$TEST_HOME/fake-bin:$PATH"

HOME="$TEST_HOME" python3 <<'PY'
import json
import os
import sqlite3

home = os.path.expanduser("~")
source_path = os.path.join(home, "dotfiles/openclaw/cron/jobs.json")
live_path = os.path.join(home, ".openclaw/cron/jobs.json")
sqlite_path = os.path.join(home, ".openclaw/state/openclaw.sqlite")
schedule = {"kind": "at", "at": "2030-01-01T00:00:00.000Z"}
job = {
    "id": "future",
    "name": "Future one-shot",
    "enabled": True,
    "deleteAfterRun": True,
    "schedule": schedule,
    "sessionTarget": "isolated",
    "wakeMode": "next-heartbeat",
    "payload": {"kind": "agentTurn", "message": "test"},
    "delivery": {"mode": "none"},
}
with open(source_path, "w") as source_file:
    json.dump({"version": 1, "jobs": [job]}, source_file, indent=2)
    source_file.write("\n")
try:
    os.unlink(live_path)
except FileNotFoundError:
    pass
try:
    os.unlink(sqlite_path)
except FileNotFoundError:
    pass
with sqlite3.connect(sqlite_path) as conn:
    conn.execute(
        """
        CREATE TABLE cron_jobs (
          store_key TEXT NOT NULL,
          job_id TEXT NOT NULL,
          state_json TEXT NOT NULL,
          job_json TEXT NOT NULL,
          next_run_at_ms INTEGER,
          last_run_at_ms INTEGER
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE cron_run_logs (
          store_key TEXT NOT NULL,
          job_id TEXT NOT NULL,
          entry_json TEXT NOT NULL
        )
        """
    )
    expected_ms = 1893456000000
    conn.execute(
        "INSERT INTO cron_jobs VALUES (?, ?, ?, ?, ?, ?)",
        (live_path, job["id"], json.dumps({"nextRunAtMs": expected_ms}), json.dumps(job), expected_ms, None),
    )
PY

: > "$OPENCLAW_CALL_LOG"
deploy
if grep -q 'cron.update' "$OPENCLAW_CALL_LOG"; then
  echo "matching SQLite schedule unexpectedly triggered cron.update" >&2
  exit 1
fi

HOME="$TEST_HOME" python3 <<'PY'
import os
import sqlite3

path = os.path.expanduser("~/.openclaw/state/openclaw.sqlite")
with sqlite3.connect(path) as conn:
    conn.execute(
        "UPDATE cron_jobs SET next_run_at_ms = ?, last_run_at_ms = ?, state_json = ? WHERE job_id = 'future'",
        (1893452400000, 1893456000000, '{"nextRunAtMs":1893452400000,"lastRunAtMs":1893456000000}'),
    )
PY

: > "$OPENCLAW_CALL_LOG"
deploy
if grep -q 'cron.update' "$OPENCLAW_CALL_LOG"; then
  echo "legitimate one-shot retry backoff unexpectedly triggered cron.update" >&2
  exit 1
fi

HOME="$TEST_HOME" python3 <<'PY'
import os
import sqlite3

path = os.path.expanduser("~/.openclaw/state/openclaw.sqlite")
with sqlite3.connect(path) as conn:
    conn.execute(
        "UPDATE cron_jobs SET last_run_at_ms = NULL, state_json = ? WHERE job_id = 'future'",
        ('{"nextRunAtMs":1893452400000}',),
    )
PY

: > "$OPENCLAW_CALL_LOG"
deploy
EXPECTED_UPDATE='gateway call cron.update --json --timeout 30000 --params {"id":"future","patch":{"enabled":true,"schedule":{"kind":"at","at":"2030-01-01T00:00:00.000Z"}}}'
if ! grep -Fxq "$EXPECTED_UPDATE" "$OPENCLAW_CALL_LOG"; then
  echo "stale SQLite next_run_at_ms did not produce the expected cron.update" >&2
  cat "$OPENCLAW_CALL_LOG" >&2
  exit 1
fi
if [ "$(sed -n '1p' "$OPENCLAW_CALL_LOG")" != "$EXPECTED_UPDATE" ] || \
   ! sed -n '2p' "$OPENCLAW_CALL_LOG" | grep -q '^doctor '; then
  echo "cron.update did not run before doctor normalization" >&2
  cat "$OPENCLAW_CALL_LOG" >&2
  exit 1
fi

HOME="$TEST_HOME" python3 <<'PY'
import json
import os
import sqlite3

path = os.path.expanduser("~/.openclaw/state/openclaw.sqlite")
with sqlite3.connect(path) as conn:
    raw_job = conn.execute(
        "SELECT job_json FROM cron_jobs WHERE job_id = 'future'"
    ).fetchone()[0]
    job = json.loads(raw_job)
    job["schedule"]["at"] = "2030-01-02T00:00:00.000Z"
    conn.execute(
        "UPDATE cron_jobs SET job_json = ?, next_run_at_ms = ?, state_json = ? WHERE job_id = 'future'",
        (json.dumps(job), 1893542400000, '{"nextRunAtMs":1893542400000}'),
    )
PY

: > "$OPENCLAW_CALL_LOG"
deploy
if [ "$(sed -n '1p' "$OPENCLAW_CALL_LOG")" != "$EXPECTED_UPDATE" ] || \
   ! sed -n '2p' "$OPENCLAW_CALL_LOG" | grep -q '^doctor '; then
  echo "changed SQLite schedule did not run cron.update before doctor" >&2
  cat "$OPENCLAW_CALL_LOG" >&2
  exit 1
fi

echo "test-sync-cron-jobs: PASS"
