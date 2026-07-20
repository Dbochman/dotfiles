#!/usr/bin/env bash

set -euo pipefail

REPO_ROOT=$(cd "$(dirname "$0")/../.." && pwd)
TEST_HOME=$(mktemp -d)
trap 'rm -rf "$TEST_HOME"' EXIT

mkdir -p \
  "$TEST_HOME/dotfiles/openclaw/cron" \
  "$TEST_HOME/.openclaw/cron/runs"
printf '%s\n' \
  "DYLAN_EMAIL=dylan@example.invalid" \
  "JULIA_EMAIL=julia@example.invalid" \
  "HOUSEHOLD_CHAT_ID=170" \
  "JULIA_CHAT_ID=1" \
  "DYLAN_CHAT_ID=2" \
  > "$TEST_HOME/.openclaw/.secrets-cache"
chmod 600 "$TEST_HOME/.openclaw/.secrets-cache"
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
OPENCLAW_DOCTOR_PATH_LOG="$TEST_HOME/openclaw-doctor.path"
FAKE_OPENCLAW_DRIVER="$TEST_HOME/fake-openclaw.py"
export OPENCLAW_CALL_LOG OPENCLAW_DOCTOR_PATH_LOG FAKE_OPENCLAW_DRIVER
cat > "$FAKE_OPENCLAW_DRIVER" <<'PY'
#!/usr/bin/env python3
import datetime
import json
import os
import sqlite3
import sys


def load_live_jobs():
    with open(os.environ["LIVE_JOBS"], encoding="utf-8") as handle:
        return json.load(handle)["jobs"]


def scheduled_ms(job):
    schedule = job.get("schedule", {})
    if schedule.get("kind") != "at" or not job.get("enabled", True):
        return None
    return int(
        datetime.datetime.fromisoformat(
            schedule["at"].replace("Z", "+00:00")
        ).timestamp()
        * 1000
    )


args = sys.argv[1:]
database = os.environ["SQLITE_DB"]
if args[:3] == ["gateway", "call", "cron.update"]:
    params = json.loads(args[args.index("--params") + 1])
    with sqlite3.connect(database) as connection:
        row = connection.execute(
            "SELECT job_json, state_json, next_run_at_ms, last_run_at_ms "
            "FROM cron_jobs WHERE job_id = ?",
            (params["id"],),
        ).fetchone()
        if row is None:
            raise SystemExit(1)
        job = json.loads(row[0])
        patch = dict(params["patch"])
        delivery_patch = patch.pop("delivery", None)
        job.update(patch)
        if delivery_patch is not None:
            delivery = dict(job.get("delivery") or {})
            for key, value in delivery_patch.items():
                if value is None:
                    delivery.pop(key, None)
                else:
                    delivery[key] = value
            job["delivery"] = delivery
        next_run = scheduled_ms(job) if "schedule" in params["patch"] or "enabled" in params["patch"] else row[2]
        connection.execute(
            "UPDATE cron_jobs SET job_json = ?, next_run_at_ms = ? WHERE job_id = ?",
            (json.dumps(job), next_run, params["id"]),
        )
    print("{}")
elif args and args[0] == "doctor":
    with sqlite3.connect(database) as connection:
        for source in load_live_jobs():
            job = dict(source)
            state = job.pop("state", {})
            exists = connection.execute(
                "SELECT 1 FROM cron_jobs WHERE job_id = ?", (job["id"],)
            ).fetchone()
            if exists is None:
                connection.execute(
                    "INSERT INTO cron_jobs VALUES (?, ?, ?, ?, ?, ?)",
                    (
                        os.environ["LIVE_JOBS"],
                        job["id"],
                        json.dumps(state),
                        json.dumps(job),
                        scheduled_ms(job),
                        None,
                    ),
                )
    print("Cron store migrated")
elif args[:2] == ["cron", "list"]:
    with sqlite3.connect(database) as connection:
        rows = connection.execute(
            "SELECT job_json FROM cron_jobs WHERE store_key = ? ORDER BY job_id",
            (os.environ["LIVE_JOBS"],),
        ).fetchall()
        if not rows:
            rows = connection.execute(
                "SELECT job_json FROM cron_jobs ORDER BY job_id"
            ).fetchall()
    jobs = [json.loads(row[0]) for row in rows]
    hidden_job_id = os.environ.get("FAKE_GATEWAY_HIDE_JOB_ID")
    if hidden_job_id:
        jobs = [job for job in jobs if job.get("id") != hidden_job_id]
    extra_job_id = os.environ.get("FAKE_GATEWAY_EXTRA_JOB_ID")
    if extra_job_id:
        jobs.append({
            "id": extra_job_id,
            "name": "Gateway-only extra job",
            "enabled": True,
            "schedule": {"kind": "cron", "expr": "0 0 * * *"},
            "sessionTarget": "isolated",
            "wakeMode": "next-heartbeat",
            "payload": {"kind": "agentTurn", "message": "extra"},
            "delivery": {"mode": "none"},
        })
    print(json.dumps({"jobs": jobs}))
else:
    raise SystemExit(1)
PY
chmod +x "$FAKE_OPENCLAW_DRIVER"

write_fake_openclaw() {
  printf '%s\n' \
    '#!/bin/bash' \
    'printf "%s\n" "$*" >> "$OPENCLAW_CALL_LOG"' \
    'if [ "$1" = "doctor" ]; then printf "%s\n" "$PATH" > "$OPENCLAW_DOCTOR_PATH_LOG"; fi' \
    'exec python3 "$FAKE_OPENCLAW_DRIVER" "$@"' \
    > "$TEST_HOME/fake-bin/openclaw"
  chmod +x "$TEST_HOME/fake-bin/openclaw"
}

write_fake_openclaw
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
case "$(cat "$OPENCLAW_DOCTOR_PATH_LOG")" in
  "$TEST_HOME/.openclaw/bin:"*) ;;
  *)
    echo "cron doctor did not inherit the managed OpenClaw wrapper path" >&2
    exit 1
    ;;
esac
EXPECTED_UPDATE='gateway call cron.update --json --timeout 30000 --params {"id":"future","patch":{"schedule":{"kind":"at","at":"2030-01-01T00:00:00.000Z"}}}'
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

# Definition drift must also flow through cron.update. A payload-only repair
# must omit schedule/enabled so a failed one-shot's retry backoff is preserved.
HOME="$TEST_HOME" python3 <<'PY'
import json
import os
import sqlite3

home = os.path.expanduser("~")
source_path = os.path.join(home, "dotfiles/openclaw/cron/jobs.json")
sqlite_path = os.path.join(home, ".openclaw/state/openclaw.sqlite")
with open(source_path) as source_file:
    desired = json.load(source_file)["jobs"][0]
live = dict(desired)
live["payload"] = {"kind": "agentTurn", "message": "stale payload"}
with sqlite3.connect(sqlite_path) as conn:
    conn.execute(
        "UPDATE cron_jobs SET job_json = ?, next_run_at_ms = ?, last_run_at_ms = ?, state_json = ? WHERE job_id = 'future'",
        (
            json.dumps(live),
            1893452400000,
            1893456000000,
            '{"nextRunAtMs":1893452400000,"lastRunAtMs":1893456000000}',
        ),
    )
PY

: > "$OPENCLAW_CALL_LOG"
deploy
EXPECTED_PAYLOAD_UPDATE='gateway call cron.update --json --timeout 30000 --params {"id":"future","patch":{"payload":{"kind":"agentTurn","message":"test"}}}'
if [ "$(sed -n '1p' "$OPENCLAW_CALL_LOG")" != "$EXPECTED_PAYLOAD_UPDATE" ] || \
   ! sed -n '2p' "$OPENCLAW_CALL_LOG" | grep -q '^doctor '; then
  echo "payload drift did not run the smallest cron.update before doctor" >&2
  cat "$OPENCLAW_CALL_LOG" >&2
  exit 1
fi
HOME="$TEST_HOME" python3 <<'PY'
import json
import os

path = os.path.expanduser("~/.openclaw/cron/jobs.json")
with open(path) as jobs_file:
    job = json.load(jobs_file)["jobs"][0]
state = job.get("state", {})
if state.get("nextRunAtMs") != 1893452400000:
    raise SystemExit("payload-only drift discarded the retry backoff")
if state.get("lastRunAtMs") != 1893456000000:
    raise SystemExit("payload-only drift discarded last-run state")
PY

# Exercise the rest of the canonical fields as one exact patch. The matching
# schedule must stay out of the patch; its independent assertions remain above.
HOME="$TEST_HOME" python3 <<'PY'
import json
import os
import sqlite3

home = os.path.expanduser("~")
source_path = os.path.join(home, "dotfiles/openclaw/cron/jobs.json")
sqlite_path = os.path.join(home, ".openclaw/state/openclaw.sqlite")
with open(source_path) as source_file:
    desired = json.load(source_file)["jobs"][0]
live = dict(desired)
live.update({
    "name": "Stale name",
    "enabled": False,
    "deleteAfterRun": False,
    "sessionTarget": "current",
    "wakeMode": "now",
    "payload": {"kind": "agentTurn", "message": "stale payload"},
    "delivery": {"mode": "announce"},
})
with sqlite3.connect(sqlite_path) as conn:
    conn.execute(
        "UPDATE cron_jobs SET job_json = ?, next_run_at_ms = ?, last_run_at_ms = NULL, state_json = ? WHERE job_id = 'future'",
        (json.dumps(live), 1893542400000, '{"nextRunAtMs":1893542400000}'),
    )
PY

: > "$OPENCLAW_CALL_LOG"
deploy
EXPECTED_DEFINITION_UPDATE='gateway call cron.update --json --timeout 30000 --params {"id":"future","patch":{"name":"Future one-shot","enabled":true,"deleteAfterRun":true,"sessionTarget":"isolated","wakeMode":"next-heartbeat","payload":{"kind":"agentTurn","message":"test"},"delivery":{"mode":"none"}}}'
if [ "$(sed -n '1p' "$OPENCLAW_CALL_LOG")" != "$EXPECTED_DEFINITION_UPDATE" ] || \
   ! sed -n '2p' "$OPENCLAW_CALL_LOG" | grep -q '^doctor '; then
  echo "canonical definition drift did not run the expected cron.update before doctor" >&2
  cat "$OPENCLAW_CALL_LOG" >&2
  exit 1
fi

# OpenClaw merges nested delivery patches. Moving an old announcing job to
# delivery.mode=none must explicitly clear its stale route and best-effort
# flag, or the latter suppresses the canonical job-level failure alert.
HOME="$TEST_HOME" python3 <<'PY'
import json
import os
import sqlite3

home = os.path.expanduser("~")
source_path = os.path.join(home, "dotfiles/openclaw/cron/jobs.json")
sqlite_path = os.path.join(home, ".openclaw/state/openclaw.sqlite")
with open(source_path) as source_file:
    desired = json.load(source_file)["jobs"][0]
live = dict(desired)
live["delivery"] = {
    "mode": "none",
    "channel": "imessage",
    "to": "chat_id:2",
    "threadId": "stale-thread",
    "accountId": "stale-account",
    "bestEffort": True,
    "completionDestination": {
        "mode": "webhook",
        "to": "https://example.invalid/completion",
    },
    "failureDestination": {
        "channel": "imessage",
        "to": "chat_id:2",
    },
}
with sqlite3.connect(sqlite_path) as conn:
    conn.execute(
        "UPDATE cron_jobs SET job_json = ?, next_run_at_ms = ?, last_run_at_ms = ?, state_json = ? WHERE job_id = 'future'",
        (
            json.dumps(live),
            1893452400000,
            1893456000000,
            '{"nextRunAtMs":1893452400000,"lastRunAtMs":1893456000000}',
        ),
    )
PY

: > "$OPENCLAW_CALL_LOG"
deploy
EXPECTED_DELIVERY_UPDATE='gateway call cron.update --json --timeout 30000 --params {"id":"future","patch":{"delivery":{"mode":"none","channel":null,"to":null,"threadId":null,"accountId":null,"completionDestination":null,"failureDestination":null,"bestEffort":false}}}'
if [ "$(sed -n '1p' "$OPENCLAW_CALL_LOG")" != "$EXPECTED_DELIVERY_UPDATE" ] || \
   ! sed -n '2p' "$OPENCLAW_CALL_LOG" | grep -q '^doctor '; then
  echo "stale delivery fields did not produce the exact clearing cron.update" >&2
  cat "$OPENCLAW_CALL_LOG" >&2
  exit 1
fi
HOME="$TEST_HOME" python3 <<'PY'
import json
import os
import sqlite3

path = os.path.expanduser("~/.openclaw/state/openclaw.sqlite")
with sqlite3.connect(path) as conn:
    raw_job, raw_state = conn.execute(
        "SELECT job_json, state_json FROM cron_jobs WHERE job_id = 'future'"
    ).fetchone()
job = json.loads(raw_job)
state = json.loads(raw_state)
if job.get("delivery") != {"mode": "none", "bestEffort": False}:
    raise SystemExit("stale delivery routing survived the canonical patch")
if state.get("nextRunAtMs") != 1893452400000:
    raise SystemExit("delivery-only drift discarded the retry backoff")
if state.get("lastRunAtMs") != 1893456000000:
    raise SystemExit("delivery-only drift discarded last-run state")
PY

# A failed SQLite migration/normalization must fail the deployment. Returning
# success here would let callers report a rollout even though SQLite could
# still contain stale or missing cron definitions.
printf '%s\n' \
  '#!/bin/bash' \
  'printf "%s\n" "$*" >> "$OPENCLAW_CALL_LOG"' \
  'if [ "$1" = "doctor" ]; then echo "doctor failed" >&2; exit 1; fi' \
  > "$TEST_HOME/fake-bin/openclaw"
chmod +x "$TEST_HOME/fake-bin/openclaw"
if deploy 2>/dev/null; then
  echo "cron deployment reported success after OpenClaw doctor failed" >&2
  exit 1
fi
write_fake_openclaw

# Exact parity must reject an extra SQLite definition even when the active
# Gateway does not expose it.
HOME="$TEST_HOME" python3 <<'PY'
import json
import os
import sqlite3

live_path = os.path.expanduser("~/.openclaw/cron/jobs.json")
sqlite_path = os.path.expanduser("~/.openclaw/state/openclaw.sqlite")
job = {
    "id": "extra-sqlite",
    "name": "SQLite-only extra job",
    "enabled": True,
    "schedule": {"kind": "cron", "expr": "0 0 * * *"},
    "sessionTarget": "isolated",
    "wakeMode": "next-heartbeat",
    "payload": {"kind": "agentTurn", "message": "extra"},
    "delivery": {"mode": "none"},
}
with sqlite3.connect(sqlite_path) as connection:
    connection.execute(
        "INSERT INTO cron_jobs VALUES (?, ?, ?, ?, ?, ?)",
        (live_path, job["id"], "{}", json.dumps(job), None, None),
    )
PY
export FAKE_GATEWAY_HIDE_JOB_ID=extra-sqlite
if PARITY_OUT=$(deploy 2>&1); then
  echo "cron deployment accepted an extra SQLite definition" >&2
  exit 1
fi
unset FAKE_GATEWAY_HIDE_JOB_ID
if ! printf '%s\n' "$PARITY_OUT" | grep -q 'post-deploy SQLite cron ID set mismatch' || \
   ! printf '%s\n' "$PARITY_OUT" | grep -q 'extra-sqlite'; then
  echo "extra SQLite definition did not produce a useful parity error" >&2
  printf '%s\n' "$PARITY_OUT" >&2
  exit 1
fi
HOME="$TEST_HOME" python3 <<'PY'
import os
import sqlite3

path = os.path.expanduser("~/.openclaw/state/openclaw.sqlite")
with sqlite3.connect(path) as connection:
    connection.execute("DELETE FROM cron_jobs WHERE job_id = 'extra-sqlite'")
PY

# Check the Gateway set independently; it may contain a stale in-memory job
# that is absent from SQLite.
export FAKE_GATEWAY_EXTRA_JOB_ID=extra-gateway
if PARITY_OUT=$(deploy 2>&1); then
  echo "cron deployment accepted an extra Gateway definition" >&2
  exit 1
fi
unset FAKE_GATEWAY_EXTRA_JOB_ID
if ! printf '%s\n' "$PARITY_OUT" | grep -q 'post-deploy Gateway cron ID set mismatch' || \
   ! printf '%s\n' "$PARITY_OUT" | grep -q 'extra-gateway'; then
  echo "extra Gateway definition did not produce a useful parity error" >&2
  printf '%s\n' "$PARITY_OUT" >&2
  exit 1
fi

# Successful exit codes are not enough: a no-op gateway/doctor pair must fail
# the post-deploy SQLite/Gateway parity read-back.
HOME="$TEST_HOME" python3 <<'PY'
import json
import os

path = os.path.expanduser("~/dotfiles/openclaw/cron/jobs.json")
with open(path, encoding="utf-8") as handle:
    data = json.load(handle)
data["jobs"][0]["payload"]["message"] = "new canonical payload"
with open(path, "w", encoding="utf-8") as handle:
    json.dump(data, handle, indent=2)
    handle.write("\n")
PY
printf '%s\n' \
  '#!/bin/bash' \
  'printf "%s\n" "$*" >> "$OPENCLAW_CALL_LOG"' \
  'if [ "$1" = "doctor" ]; then echo "Cron store migrated"; exit 0; fi' \
  'if [ "$1" = "cron" ] && [ "$2" = "list" ]; then exec python3 "$FAKE_OPENCLAW_DRIVER" "$@"; fi' \
  'exit 0' \
  > "$TEST_HOME/fake-bin/openclaw"
chmod +x "$TEST_HOME/fake-bin/openclaw"
if deploy 2>/dev/null; then
  echo "cron deployment reported success after no-op gateway reconciliation" >&2
  exit 1
fi
write_fake_openclaw

# Private identities remain placeholders in dotfiles, expand only into the
# machine-local live store, and are re-redacted by save.
PRIVATE_DOTFILES="$TEST_HOME/private-jobs.json"
PRIVATE_LIVE="$TEST_HOME/private-live/jobs.json"
PRIVATE_SQLITE="$TEST_HOME/private-live/missing.sqlite"
mkdir -p "$(dirname "$PRIVATE_LIVE")"
printf '%s\n' '{
  "version": 1,
  "jobs": [{
    "id": "private-routing",
    "enabled": true,
    "schedule": {"kind": "cron", "expr": "0 7 1 2 *"},
    "payload": {"message": "account=${DYLAN_EMAIL}"},
    "delivery": {"to": "chat_id:${DYLAN_CHAT_ID}"}
  }]
}' > "$PRIVATE_DOTFILES"
HOME="$TEST_HOME" \
DOTFILES_JOBS="$PRIVATE_DOTFILES" \
LIVE_JOBS="$PRIVATE_LIVE" \
SQLITE_DB="$PRIVATE_SQLITE" \
  "$TEST_HOME/dotfiles/openclaw/sync-cron-jobs.sh" deploy >/dev/null
python3 - "$PRIVATE_DOTFILES" "$PRIVATE_LIVE" <<'PY'
import json
import sys

tracked = open(sys.argv[1], encoding="utf-8").read()
live = json.load(open(sys.argv[2], encoding="utf-8"))["jobs"][0]
assert "${DYLAN_EMAIL}" in tracked
assert "${DYLAN_CHAT_ID}" in tracked
assert '"expr": "0 7 1 2 *"' in tracked
assert live["payload"]["message"] == "account=dylan@example.invalid"
assert live["delivery"]["to"] == "chat_id:2"
PY
HOME="$TEST_HOME" \
DOTFILES_JOBS="$PRIVATE_DOTFILES" \
LIVE_JOBS="$PRIVATE_LIVE" \
SQLITE_DB="$PRIVATE_SQLITE" \
  "$TEST_HOME/dotfiles/openclaw/sync-cron-jobs.sh" save >/dev/null
grep -Fq '${DYLAN_EMAIL}' "$PRIVATE_DOTFILES"
grep -Fq '${DYLAN_CHAT_ID}' "$PRIVATE_DOTFILES"
if grep -Fq 'dylan@example.invalid' "$PRIVATE_DOTFILES"; then
  echo "save leaked a machine-local identity into tracked definitions" >&2
  exit 1
fi

chmod 644 "$TEST_HOME/.openclaw/.secrets-cache"
before_insecure=$(cksum "$PRIVATE_LIVE")
if insecure_output=$(HOME="$TEST_HOME" \
    DOTFILES_JOBS="$PRIVATE_DOTFILES" \
    LIVE_JOBS="$PRIVATE_LIVE" \
    SQLITE_DB="$PRIVATE_SQLITE" \
      "$TEST_HOME/dotfiles/openclaw/sync-cron-jobs.sh" deploy 2>&1); then
  echo "cron deployment reported success with an insecure identity cache" >&2
  exit 1
fi
after_insecure=$(cksum "$PRIVATE_LIVE")
if [[ "$before_insecure" != "$after_insecure" ]]; then
  echo "cron deploy changed live definitions with an insecure identity cache" >&2
  exit 1
fi
grep -q 'live cron definitions were not deployed' <<< "$insecure_output"
chmod 600 "$TEST_HOME/.openclaw/.secrets-cache"

echo "test-sync-cron-jobs: PASS"
