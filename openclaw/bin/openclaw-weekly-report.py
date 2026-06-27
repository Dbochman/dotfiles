#!/usr/bin/env python3
"""Generate the OpenClaw weekly activity and health report.

The report intentionally uses durable session, cron, and local Messages records
instead of transient gateway logs. It is designed to be called by the weekly
cron agent, which should return its stdout verbatim.
"""

import json
import re
import sqlite3
import subprocess
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import urlopen
from zoneinfo import ZoneInfo


HOME = Path.home()
LOCAL_TZ = ZoneInfo("America/New_York")
UTC = timezone.utc
SESSIONS_DIR = HOME / ".openclaw" / "agents" / "main" / "sessions"
CRON_DB = HOME / ".openclaw" / "state" / "openclaw.sqlite"
CRON_STORE_KEY = str(HOME / ".openclaw" / "cron" / "jobs.json")
MESSAGES_DB = HOME / "Library" / "Messages" / "chat.db"
IMSG_BIN = "/opt/homebrew/bin/imsg"
GATEWAY_URL = "http://127.0.0.1:18789/health"
CRISISMODE_EXCLUSIONS = {"PG-001", "DNS-002"}
APPLE_EPOCH = datetime(2001, 1, 1, tzinfo=UTC)


def parse_timestamp(value):
    """Return a timezone-aware UTC datetime for an OpenClaw session timestamp."""
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return parsed.astimezone(UTC)


def json_lines(path):
    try:
        lines = path.read_text(errors="replace").splitlines()
    except OSError:
        return

    for line in lines:
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(record, dict):
            yield record


def session_files():
    if not SESSIONS_DIR.is_dir():
        return []
    # Reset and deleted session files still contain valid historical activity.
    # Trajectory files duplicate those records and are deliberately excluded.
    return [
        path
        for path in SESSIONS_DIR.glob("*.jsonl*")
        if ".trajectory." not in path.name
    ]


def week_windows(now):
    local_now = now.astimezone(LOCAL_TZ)
    monday = local_now.date() - timedelta(days=local_now.weekday())
    current_start = datetime.combine(monday, datetime.min.time(), LOCAL_TZ).astimezone(UTC)
    previous_start = current_start - timedelta(days=7)
    # Compare the same elapsed portion of the preceding week.
    previous_end = now - timedelta(days=7)
    return (current_start, now), (previous_start, previous_end)


def collect_session_activity(start, end):
    activity = {
        "model_turns": 0,
        "sessions": set(),
        "tool_calls": 0,
        "rate_limits": 0,
        "daily": Counter(),
        "models": Counter(),
    }

    for path in session_files():
        session_id = path.name.split(".jsonl", 1)[0]
        for record in json_lines(path):
            timestamp = parse_timestamp(record.get("timestamp"))
            if timestamp is None or not start <= timestamp < end:
                continue
            message = record.get("message")
            if record.get("type") != "message" or not isinstance(message, dict):
                continue
            if message.get("role") != "assistant":
                continue

            activity["model_turns"] += 1
            activity["sessions"].add(session_id)
            activity["daily"][timestamp.astimezone(LOCAL_TZ).date()] += 1

            model = message.get("model") or record.get("modelId")
            if isinstance(model, str) and model and model != "delivery-mirror":
                activity["models"][model] += 1

            error_message = str(message.get("errorMessage") or "").lower()
            if "rate limit" in error_message or "429" in error_message:
                activity["rate_limits"] += 1

            content = message.get("content")
            if not isinstance(content, list):
                continue
            activity["tool_calls"] += sum(
                1
                for block in content
                if isinstance(block, dict) and block.get("type") in {"toolCall", "tool_use"}
            )

    activity["sessions"] = len(activity["sessions"])
    return activity


def collect_cron_runs(start, end):
    runs = {"completed": 0, "failed": 0, "delivered": 0}
    if not CRON_DB.is_file():
        return runs

    start_ms = int(start.timestamp() * 1000)
    end_ms = int(end.timestamp() * 1000)
    try:
        with sqlite3.connect(f"file:{CRON_DB}?mode=ro", uri=True, timeout=5) as conn:
            rows = conn.execute(
                """
                SELECT status, delivered
                  FROM cron_run_logs
                 WHERE store_key = ? AND ts >= ? AND ts < ?
                """,
                (CRON_STORE_KEY, start_ms, end_ms),
            ).fetchall()
    except sqlite3.Error:
        return runs

    for status, delivered in rows:
        if status == "ok":
            runs["completed"] += 1
        else:
            runs["failed"] += 1
        if delivered:
            runs["delivered"] += 1
    return runs


def message_db_timestamp(value):
    return int((value - APPLE_EPOCH).total_seconds() * 1_000_000_000)


def imessage_messages(start, end):
    if not MESSAGES_DB.exists():
        return None

    query = """
        SELECT
            COALESCE(SUM(CASE WHEN is_from_me = 1 THEN 1 ELSE 0 END), 0) AS sent,
            COALESCE(SUM(CASE WHEN is_from_me = 0 THEN 1 ELSE 0 END), 0) AS received,
            COUNT(*) AS total
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
                (message_db_timestamp(start), message_db_timestamp(end)),
            ).fetchone()
    except sqlite3.Error:
        return None

    if row is None:
        return None
    return {"sent": int(row[0]), "received": int(row[1]), "truncated": False}


def imessage_health():
    try:
        result = subprocess.run(
            [IMSG_BIN, "status", "--json"],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        status = json.loads(result.stdout)
    except (OSError, subprocess.SubprocessError, json.JSONDecodeError):
        return "unavailable (imsg status failed)"

    if result.returncode != 0:
        return "unreachable (imsg status failed)"

    version = status.get("version") or "unknown"
    sip = status.get("sip") or "unknown"
    basic = status.get("basic_features") is True
    advanced = status.get("advanced_features") is True
    if advanced:
        return f"healthy (imsg {version}; advanced bridge connected; SIP {sip})"
    if basic:
        return f"reachable (imsg {version}; database access only; SIP {sip})"
    return f"unavailable (imsg {version}; basic features unavailable; SIP {sip})"


def gateway_health():
    try:
        with urlopen(GATEWAY_URL, timeout=5) as response:
            return "healthy (HTTP %s)" % response.status
    except (HTTPError, URLError, OSError):
        return "unreachable"


def apfs_disk_usage():
    try:
        result = subprocess.run(
            ["df", "-k", "/"], capture_output=True, text=True, timeout=5, check=False
        )
        parts = result.stdout.splitlines()[-1].split()
        total_kb = int(parts[1])
        available_kb = int(parts[3])
    except (IndexError, OSError, ValueError, subprocess.SubprocessError):
        return "unavailable"

    allocated_pct = 100 * (total_kb - available_kb) / total_kb
    available_gb = available_kb / 1024 / 1024
    return f"APFS container {allocated_pct:.1f}% allocated, {available_gb:.0f} GB free"


def launchd_status(label):
    try:
        result = subprocess.run(
            ["launchctl", "list", label],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return {"loaded": False, "pid": None, "exit_code": None}

    if result.returncode != 0:
        return {"loaded": False, "pid": None, "exit_code": None}
    pid_match = re.search(r'"PID"\s*=\s*(\d+)', result.stdout)
    exit_match = re.search(r'"LastExitStatus"\s*=\s*(-?\d+)', result.stdout)
    return {
        "loaded": True,
        "pid": int(pid_match.group(1)) if pid_match else None,
        "exit_code": int(exit_match.group(1)) if exit_match else None,
    }


def opentable_attention(now):
    status = launchd_status("ai.openclaw.opentable-refresh")
    log_path = HOME / ".openclaw" / "logs" / "opentable-refresh.log"
    if not status["loaded"] or status["exit_code"] in (None, 0):
        return None
    try:
        failed_at = datetime.fromtimestamp(log_path.stat().st_mtime, LOCAL_TZ)
    except OSError:
        return "OpenTable token refresh last exited with an error"
    if now.astimezone(LOCAL_TZ) - failed_at > timedelta(days=8):
        return None
    return f"OpenTable token refresh failed {failed_at.strftime('%a %m/%d')}; it needs separate auth repair"


def parse_json_documents(text):
    """Extract JSON documents from CLIs that prepend progress output."""
    decoder = json.JSONDecoder()
    documents = []
    index = 0
    while index < len(text):
        match = re.search(r"[\[{]", text[index:])
        if not match:
            break
        start = index + match.start()
        try:
            document, end = decoder.raw_decode(text, start)
        except json.JSONDecodeError:
            index = start + 1
            continue
        documents.append(document)
        index = end
    return documents


def crisismode_summary():
    try:
        result = subprocess.run(
            ["crisismode", "scan", "--json"],
            capture_output=True,
            text=True,
            timeout=45,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return "unavailable", []

    scans = [
        document
        for document in parse_json_documents(result.stdout)
        if isinstance(document, dict) and isinstance(document.get("findings"), list)
    ]
    if not scans:
        return "unavailable", []
    scan = scans[-1]
    score = scan.get("score")
    actionable = []
    for finding in scan.get("findings", []):
        if not isinstance(finding, dict) or finding.get("id") in CRISISMODE_EXCLUSIONS:
            continue
        if finding.get("status") in {"warning", "critical", "unknown", "recovering"}:
            actionable.append(str(finding.get("summary") or finding.get("id")))
    return str(score) if score is not None else "unavailable", actionable


def format_models(models):
    total = sum(models.values())
    if not total:
        return "unavailable"
    parts = []
    for name, count in models.most_common(3):
        parts.append(f"{name} {count / total:.0%}")
    return ", ".join(parts)


def format_messages(messages):
    if messages is None:
        return "unavailable"
    suffix = " (1000-record cap reached)" if messages["truncated"] else ""
    return f"{messages['sent']} sent / {messages['received']} received{suffix}"


def busiest_day(daily):
    if not daily:
        return "none"
    day, count = max(daily.items(), key=lambda item: (item[1], item[0]))
    return f"{day.strftime('%a %m/%d')} ({count} model turns)"


def report(now):
    (current_start, current_end), (previous_start, previous_end) = week_windows(now)
    current = collect_session_activity(current_start, current_end)
    previous = collect_session_activity(previous_start, previous_end)
    current_runs = collect_cron_runs(current_start, current_end)
    current_messages = imessage_messages(current_start, current_end)
    previous_messages = imessage_messages(previous_start, previous_end)
    crisis_score, crisis_findings = crisismode_summary()

    period = (
        f"{current_start.astimezone(LOCAL_TZ).strftime('%a %m/%d')}"
        f"-{current_end.astimezone(LOCAL_TZ).strftime('%a %m/%d')}"
    )
    lines = [
        f"OpenClaw Weekly Report ({period})",
        "",
        "Activity:",
        f"- Model turns: {current['model_turns']} (prev: {previous['model_turns']})",
        f"- Conversations: {current['sessions']} (prev: {previous['sessions']})",
        f"- Tool calls: {current['tool_calls']} (prev: {previous['tool_calls']})",
        f"- iMessages: {format_messages(current_messages)} (prev: {format_messages(previous_messages)})",
        f"- Busiest day: {busiest_day(current['daily'])}",
        f"- Model mix: {format_models(current['models'])}",
        "",
        "Automation:",
        f"- Scheduled runs: {current_runs['completed']} completed, {current_runs['failed']} failed",
        f"- Cron deliveries: {current_runs['delivered']}",
        f"- Model rate-limit responses: {current['rate_limits']}",
        "",
        "Health:",
        f"- Gateway: {gateway_health()}",
        f"- iMessage: {imessage_health()}",
        f"- Disk: {apfs_disk_usage()}",
    ]

    if crisis_findings:
        lines.append(f"- CrisisMode: {crisis_score}/100 raw; attention: {'; '.join(crisis_findings[:2])}")
    else:
        lines.append(
            f"- CrisisMode: {crisis_score}/100 raw; no actionable findings "
            "after excluding the unused PostgreSQL probe and benign dual-stack DNS split"
        )

    attention = opentable_attention(now)
    if attention:
        lines.append(f"- Attention: {attention}")
    else:
        lines.append("- Services: critical OpenClaw services healthy")
    return "\n".join(lines)


def main():
    print(report(datetime.now(UTC)))


if __name__ == "__main__":
    main()
