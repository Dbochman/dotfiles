#!/bin/bash
# usage-snapshot.sh — Collect OpenClaw usage metrics and append to JSONL.
# Runs every 15 minutes via LaunchAgent.
# Delegates all logic to an inline Python script for robustness.

set -euo pipefail

exec python3 - "$@" <<'PYTHON_SCRIPT'
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from urllib.request import Request, urlopen
from urllib.error import URLError

HISTORY_DIR = Path.home() / ".openclaw" / "usage-history"
STATE_FILE = HISTORY_DIR / ".snapshot-state"
OAUTH_CACHE = Path.home() / ".openclaw" / ".anthropic-oauth-cache"
CRON_RUNS_DIR = Path.home() / ".openclaw" / "cron" / "runs"
LOG_DIR = Path("/tmp/openclaw")

HISTORY_DIR.mkdir(parents=True, exist_ok=True)

now = datetime.now(timezone.utc)
today = now.strftime("%Y-%m-%d")
now_iso = now.strftime("%Y-%m-%dT%H:%M:%SZ")
output_file = HISTORY_DIR / f"{today}.jsonl"


# ── Load state ──────────────────────────────────────────────────────────

def load_state():
    state = {"log_offset": 0, "last_ts": "", "cron_offsets": {}}
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
            elif key.startswith("cron_offset:"):
                fname = key[len("cron_offset:"):]
                state["cron_offsets"][fname] = int(val)
    except (OSError, ValueError):
        pass
    return state


def save_state(state):
    lines = [
        f"last_ts={now_iso}",
        f"log_offset={state['log_offset']}",
    ]
    for fname, off in state.get("cron_offsets", {}).items():
        lines.append(f"cron_offset:{fname}={off}")
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


# ── 3. Parse cron run JSONL ─────────────────────────────────────────────

def parse_cron_runs(last_offsets):
    result = {
        "input_tokens": 0,
        "output_tokens": 0,
        "total_tokens": 0,
        "cron_runs": 0,
        "jobs": [],
        "new_offsets": {},
    }

    if not CRON_RUNS_DIR.is_dir():
        return result

    for fpath in CRON_RUNS_DIR.iterdir():
        if not fpath.name.endswith(".jsonl"):
            continue

        fname = fpath.name
        try:
            fsize = fpath.stat().st_size
        except OSError:
            continue

        last_off = last_offsets.get(fname, 0)
        result["new_offsets"][fname] = fsize

        if fsize <= last_off:
            continue

        try:
            with open(fpath, "rb") as f:
                f.seek(last_off)
                new_bytes = f.read()
        except OSError:
            continue

        for line in new_bytes.decode("utf-8", errors="replace").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue

            if rec.get("action") != "finished":
                continue

            result["cron_runs"] += 1
            # Token counts live under "usage" dict in newer OpenClaw versions
            usage = rec.get("usage", {})
            ot = usage.get("output_tokens", 0) or rec.get("outputTokens", rec.get("output_tokens", 0)) or 0
            tt = usage.get("total_tokens", 0) or rec.get("totalTokens", rec.get("total_tokens", 0)) or 0
            # input_tokens from OpenClaw is misleadingly small (counts turns, not tokens).
            # Derive actual input consumption as total - output.
            it = max(0, tt - ot)
            result["input_tokens"] += it
            result["output_tokens"] += ot
            result["total_tokens"] += tt
            result["jobs"].append({
                "job_id": rec.get("jobId", rec.get("job_id", fname.replace(".jsonl", ""))),
                "status": rec.get("status", "ok"),
                "duration_ms": rec.get("durationMs", rec.get("duration_ms", 0)) or 0,
                "input_tokens": it,
                "output_tokens": ot,
                "total_tokens": tt,
                "model": rec.get("model", ""),
                "run_at": rec.get("runAtMs", rec.get("ts", 0)) or 0,
                "delivered": rec.get("delivered", False),
            })

    return result


# ── 4. Query BlueBubbles message counts ───────────────────────────────

SECRETS_CACHE = Path.home() / ".openclaw" / ".secrets-cache"
BB_BASE = "http://localhost:1234/api/v1"


def get_bb_password():
    if not SECRETS_CACHE.exists():
        return None
    try:
        for line in SECRETS_CACHE.read_text().splitlines():
            if line.startswith("BLUEBUBBLES_PASSWORD="):
                return line.split("=", 1)[1].strip().strip("'\"")
    except OSError:
        pass
    return None


def fetch_bb_messages(last_ts):
    """Count sent/received iMessages since last snapshot via BlueBubbles API."""
    result = {"messages_sent": 0, "messages_received": 0}
    pw = get_bb_password()
    if not pw:
        return result

    # Convert last_ts ISO string to epoch ms for BB 'after' param
    after_ms = 0
    if last_ts:
        try:
            dt = datetime.fromisoformat(last_ts.replace("Z", "+00:00"))
            after_ms = int(dt.timestamp() * 1000)
        except (ValueError, AttributeError):
            pass

    try:
        url = f"{BB_BASE}/message/query?password={pw}"
        body = json.dumps({
            "limit": 500,
            "sort": "DESC",
            "after": after_ms,
        }).encode()
        req = Request(url, data=body, headers={"Content-Type": "application/json"})
        with urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read())
        for m in data.get("data", []):
            if m.get("isFromMe"):
                result["messages_sent"] += 1
            else:
                result["messages_received"] += 1
    except (URLError, json.JSONDecodeError, OSError):
        pass

    return result


# ── Build and write snapshot ────────────────────────────────────────────

state = load_state()

utilization = fetch_utilization()
log_data = parse_runtime_log(state["log_offset"])
cron_data = parse_cron_runs(state["cron_offsets"])
bb_data = fetch_bb_messages(state["last_ts"])

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
        "messages_sent": bb_data["messages_sent"],
        "messages_received": bb_data["messages_received"],
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
state["cron_offsets"] = cron_data["new_offsets"]
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
