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
        "state": {"lastStatus": "ok"}
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
  HOME="$TEST_HOME" \
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

echo "test-sync-cron-jobs: PASS"
