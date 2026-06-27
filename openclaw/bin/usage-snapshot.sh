#!/bin/bash
# usage-snapshot.sh — Collect OpenClaw usage metrics and append to JSONL.
# Runs every 15 minutes via LaunchAgent.
# Delegates all logic to an inline Python script for robustness.

set -euo pipefail

exec python3 - "$@" <<'PYTHON_SCRIPT'
import json
import os
import re
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path
from urllib.request import Request, urlopen
from urllib.error import URLError

HISTORY_DIR = Path.home() / ".openclaw" / "usage-history"
STATE_FILE = HISTORY_DIR / ".snapshot-state"
OAUTH_CACHE = Path.home() / ".openclaw" / ".anthropic-oauth-cache"
CRON_DB = Path.home() / ".openclaw" / "state" / "openclaw.sqlite"
CRON_STORE_KEY = str(Path.home() / ".openclaw" / "cron" / "jobs.json")
LOG_DIR = Path("/tmp/openclaw")
MESSAGES_DB = Path.home() / "Library" / "Messages" / "chat.db"
APPLE_EPOCH = datetime(2001, 1, 1, tzinfo=timezone.utc)

HISTORY_DIR.mkdir(parents=True, exist_ok=True)

now = datetime.now(timezone.utc)
today = now.strftime("%Y-%m-%d")
now_iso = now.strftime("%Y-%m-%dT%H:%M:%SZ")
output_file = HISTORY_DIR / f"{today}.jsonl"


# ── Load state ──────────────────────────────────────────────────────────

def load_state():
    state = {
        "log_offset": 0,
        "last_ts": "",
        "cron_cursor": {"created_at": 0, "job_id": "", "seq": 0},
    }
    if not STATE_FILE.exists():
        return state
    try:
        for line in STATE_FILE.read_text().splitlines():
            if "=" not in line:
                continue
            key, val = line.split("=", 1)
            if key == "log_offset":
                state["log_offset"] = int(val)
            elif key == "last_ts":
                state["last_ts"] = val
            elif key == "cron_created_at":
                state["cron_cursor"]["created_at"] = int(val)
            elif key == "cron_job_id":
                state["cron_cursor"]["job_id"] = val
            elif key == "cron_seq":
                state["cron_cursor"]["seq"] = int(val)
    except (OSError, ValueError):
        pass
    return state


def save_state(state):
    lines = [
        f"last_ts={now_iso}",
        f"log_offset={state['log_offset']}",
        f"cron_created_at={state['cron_cursor']['created_at']}",
        f"cron_job_id={state['cron_cursor']['job_id']}",
        f"cron_seq={state['cron_cursor']['seq']}",
    ]
    STATE_FILE.write_text("\n".join(lines) + "\n")


# ── 1. Anthropic Usage API ──────────────────────────────────────────────

def fetch_utilization():
    if not OAUTH_CACHE.exists():
        return None
    try:
        oauth_data = json.loads(OAUTH_CACHE.read_text())
    except (json.JSONDecodeError, OSError):
        return None

    # Handle nested format: {"claudeAiOauth": {"accessToken": "..."}}
    if "claudeAiOauth" in oauth_data:
        oauth_data = oauth_data["claudeAiOauth"]
    token = oauth_data.get("accessToken") or oauth_data.get("access_token", "")
    if not token:
        return None

    try:
        req = Request(
            "https://api.anthropic.com/api/oauth/usage",
            headers={
                "Authorization": f"Bearer {token}",
                "anthropic-beta": "oauth-2025-04-20",
                "Content-Type": "application/json",
                "User-Agent": "claude-code/2.1",
            },
        )
        with urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read())
    except (URLError, json.JSONDecodeError, OSError):
        return None

    # Response is flat: {five_hour: {utilization, resets_at}, seven_day: {...}, ...}
    out = {}
    for key in ["five_hour", "seven_day", "seven_day_opus", "seven_day_sonnet"]:
        val = data.get(key)
        if val and isinstance(val, dict):
            out[key] = {
                "utilization": val.get("utilization"),
                "resets_at": val.get("resets_at"),
            }
        else:
            out[key] = None

    eu = data.get("extra_usage")
    if eu and isinstance(eu, dict):
        out["extra_usage"] = {
            "is_enabled": eu.get("is_enabled", False),
            "monthly_limit": eu.get("monthly_limit"),
            "used_credits": eu.get("used_credits", 0.0),
        }
    else:
        out["extra_usage"] = None

    return out


# ── 2. Parse runtime log ────────────────────────────────────────────────

def parse_runtime_log(last_offset):
    log_file = LOG_DIR / f"openclaw-{today}.log"
    result = {
        "agent_runs": 0,
        "messages_sent": 0,
        "errors": 0,
        "gateway_restarts": 0,
        "response_times_ms": [],
        "new_offset": last_offset,
    }

    if not log_file.exists():
        result["new_offset"] = 0
        return result

    try:
        file_size = log_file.stat().st_size
    except OSError:
        return result

    if file_size <= last_offset:
        result["new_offset"] = file_size
        return result

    try:
        with open(log_file, "rb") as f:
            f.seek(last_offset)
            new_bytes = f.read()
    except OSError:
        return result

    result["new_offset"] = file_size

    for line in new_bytes.decode("utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue

        # OpenClaw uses tslog format: messages in "0"/"1"/"2", level in _meta
        meta = rec.get("_meta", {})
        level = meta.get("logLevelName", "").upper()
        # Combine all positional message fields
        msg_parts = []
        for k in ("0", "1", "2"):
            v = rec.get(k)
            if v is not None:
                msg_parts.append(str(v) if not isinstance(v, str) else v)
        msg = " ".join(msg_parts).lower()

        if "agent" in msg and ("started" in msg or "run" in msg):
            result["agent_runs"] += 1
        if "res ✓ send" in msg or "delivered" in msg:
            result["messages_sent"] += 1
        if level in ("ERROR", "FATAL"):
            result["errors"] += 1
        if "sigterm" in msg or "shutting down" in msg:
            result["gateway_restarts"] += 1

        # Extract response times from gateway/ws lines like "res ✓ send 743ms"
        rt_match = re.search(r"res [✓✗]\s+\S+\s+(\d+)ms", msg)
        if rt_match:
            try:
                result["response_times_ms"].append(int(rt_match.group(1)))
            except (ValueError, TypeError):
                pass

    return result


# ── 3. Parse SQLite cron run history ────────────────────────────────────

def parse_cron_runs(last_cursor, last_ts):
    result = {
        "input_tokens": 0,
        "output_tokens": 0,
        "total_tokens": 0,
        "cron_runs": 0,
        "jobs": [],
        "new_cursor": dict(last_cursor),
    }

    if not CRON_DB.is_file():
        return result

    cursor = dict(last_cursor)
    if not cursor.get("created_at"):
        # The first SQLite-aware snapshot should continue from the previous
        # snapshot timestamp instead of backfilling old runs into one interval.
        try:
            start = datetime.fromisoformat(last_ts.replace("Z", "+00:00"))
            cursor["created_at"] = int(start.timestamp() * 1000)
            result["new_cursor"] = dict(cursor)
        except (ValueError, AttributeError):
            # On a fresh install, establish a high-water mark without turning
            # all retained history into current-period usage.
            try:
                with sqlite3.connect(f"file:{CRON_DB}?mode=ro", uri=True, timeout=5) as conn:
                    newest = conn.execute(
                        """
                        SELECT created_at, job_id, seq
                          FROM cron_run_logs
                         WHERE store_key = ?
                         ORDER BY created_at DESC, job_id DESC, seq DESC
                         LIMIT 1
                        """,
                        (CRON_STORE_KEY,),
                    ).fetchone()
            except sqlite3.Error:
                return result
            if newest:
                result["new_cursor"] = {
                    "created_at": int(newest[0]),
                    "job_id": newest[1],
                    "seq": int(newest[2]),
                }
            return result

    created_at = int(cursor.get("created_at", 0))
    job_id = str(cursor.get("job_id", ""))
    seq = int(cursor.get("seq", 0))
    try:
        with sqlite3.connect(f"file:{CRON_DB}?mode=ro", uri=True, timeout=5) as conn:
            rows = conn.execute(
                """
                SELECT job_id, seq, ts, status, delivered, run_at_ms,
                       duration_ms, model, total_tokens, entry_json, created_at
                  FROM cron_run_logs
                 WHERE store_key = ?
                   AND (
                        created_at > ?
                        OR (created_at = ? AND job_id > ?)
                        OR (created_at = ? AND job_id = ? AND seq > ?)
                   )
                 ORDER BY created_at, job_id, seq
                """,
                (
                    CRON_STORE_KEY,
                    created_at,
                    created_at, job_id,
                    created_at, job_id, seq,
                ),
            ).fetchall()
    except sqlite3.Error:
        return result

    for (
        row_job_id, row_seq, ts, status, delivered, run_at_ms,
        duration_ms, model, total_tokens, entry_json, row_created_at,
    ) in rows:
        result["new_cursor"] = {
            "created_at": int(row_created_at),
            "job_id": row_job_id,
            "seq": int(row_seq),
        }
        try:
            rec = json.loads(entry_json)
        except (TypeError, json.JSONDecodeError):
            rec = {}
        if rec.get("action", "finished") != "finished":
            continue

        usage = rec.get("usage", {}) if isinstance(rec.get("usage"), dict) else {}
        output_tokens = int(usage.get("output_tokens", 0) or 0)
        total = int(total_tokens or usage.get("total_tokens", 0) or 0)
        input_tokens = max(0, total - output_tokens)

        result["cron_runs"] += 1
        result["input_tokens"] += input_tokens
        result["output_tokens"] += output_tokens
        result["total_tokens"] += total
        result["jobs"].append({
            "job_id": row_job_id,
            "status": status or rec.get("status", "ok"),
            "duration_ms": int(duration_ms or 0),
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "total_tokens": total,
            "model": model or rec.get("model", ""),
            "run_at": int(run_at_ms or ts or 0),
            "delivered": bool(delivered),
        })
    return result


# ── 4. Query local Messages counts ─────────────────────────────────────

def message_db_timestamp(value):
    return int((value - APPLE_EPOCH).total_seconds() * 1_000_000_000)


def fetch_imessage_messages(last_ts):
    """Count sent/received iMessages since last snapshot via chat.db."""
    result = {"messages_sent": 0, "messages_received": 0}
    if not last_ts or not MESSAGES_DB.exists():
        return result

    try:
        start = datetime.fromisoformat(last_ts.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return result

    query = """
        SELECT
            COALESCE(SUM(CASE WHEN is_from_me = 1 THEN 1 ELSE 0 END), 0) AS sent,
            COALESCE(SUM(CASE WHEN is_from_me = 0 THEN 1 ELSE 0 END), 0) AS received
        FROM message
        WHERE date >= ?
          AND date < ?
          AND COALESCE(is_system_message, 0) = 0
          AND COALESCE(item_type, 0) = 0
          AND COALESCE(is_empty, 0) = 0
          AND service = 'iMessage'
    """
    try:
        with sqlite3.connect(f"file:{MESSAGES_DB}?mode=ro", uri=True, timeout=5) as conn:
            row = conn.execute(
                query,
                (message_db_timestamp(start), message_db_timestamp(now)),
            ).fetchone()
    except sqlite3.Error:
        return result

    if row:
        result["messages_sent"] = int(row[0])
        result["messages_received"] = int(row[1])

    return result


# ── Build and write snapshot ────────────────────────────────────────────

state = load_state()

utilization = fetch_utilization()
log_data = parse_runtime_log(state["log_offset"])
cron_data = parse_cron_runs(state["cron_cursor"], state["last_ts"])
imessage_data = fetch_imessage_messages(state["last_ts"])

snapshot = {
    "timestamp": now_iso,
    "utilization": utilization,
    "tokens": {
        "input": cron_data["input_tokens"],
        "output": cron_data["output_tokens"],
        "total": cron_data["total_tokens"],
    },
    "activity": {
        "agent_runs": log_data["agent_runs"],
        "messages_sent": imessage_data["messages_sent"],
        "messages_received": imessage_data["messages_received"],
        "cron_runs": cron_data["cron_runs"],
        "errors": log_data["errors"],
        "gateway_restarts": log_data["gateway_restarts"],
    },
    "response_times_ms": log_data["response_times_ms"],
    "cron_jobs": cron_data["jobs"],
}

with open(output_file, "a") as f:
    f.write(json.dumps(snapshot, separators=(",", ":")) + "\n")

# Update state
state["log_offset"] = log_data["new_offset"]
state["cron_cursor"] = cron_data["new_cursor"]
save_state(state)

# Prune old files (>90 days)
from datetime import timedelta

cutoff = now - timedelta(days=90)
for p in HISTORY_DIR.glob("*.jsonl"):
    try:
        file_date = datetime.strptime(p.stem, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        if file_date < cutoff:
            p.unlink()
    except (ValueError, OSError):
        pass

print(f"Snapshot written: {now_iso}", file=sys.stderr)
PYTHON_SCRIPT
