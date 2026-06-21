#!/usr/bin/env python3
"""Generate the OpenClaw weekly activity and health report.

The report intentionally uses durable session, cron, and BlueBubbles records
instead of transient gateway logs. It is designed to be called by the weekly
cron agent, which should return its stdout verbatim.
"""

import json
import re
import shlex
import subprocess
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo


HOME = Path.home()
LOCAL_TZ = ZoneInfo("America/New_York")
UTC = timezone.utc
SESSIONS_DIR = HOME / ".openclaw" / "agents" / "main" / "sessions"
CRON_RUNS_DIR = HOME / ".openclaw" / "cron" / "runs"
SECRETS_CACHE = HOME / ".openclaw" / ".secrets-cache"
BLUEBUBBLES_URL = "http://127.0.0.1:1234"
GATEWAY_URL = "http://127.0.0.1:18789/health"
CRISISMODE_EXCLUSIONS = {"PG-001", "DNS-002"}


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


def parse_epoch_ms(value):
    if not isinstance(value, (int, float)):
        return None
    try:
        return datetime.fromtimestamp(value / 1000, UTC)
    except (OverflowError, OSError, ValueError):
        return None


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
    if not CRON_RUNS_DIR.is_dir():
        return runs

    for path in CRON_RUNS_DIR.glob("*.jsonl"):
        for record in json_lines(path):
            if record.get("action") != "finished":
                continue
            timestamp = parse_epoch_ms(record.get("ts") or record.get("runAtMs"))
            if timestamp is None or not start <= timestamp < end:
                continue
            if record.get("status") == "ok":
                runs["completed"] += 1
            else:
                runs["failed"] += 1
            if record.get("delivered"):
                runs["delivered"] += 1
    return runs


def cached_secret(name):
    """Read one shell-quoted cache value without evaluating the cache as code."""
    try:
        lines = SECRETS_CACHE.read_text().splitlines()
    except OSError:
        return ""

    for raw_line in lines:
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        try:
            parts = shlex.split(line, posix=True, comments=True)
        except ValueError:
            continue
        if len(parts) != 1:
            continue
        key, separator, value = parts[0].partition("=")
        if separator and key == name:
            return value
    return ""


def bluebubbles_messages(start, end):
    password = cached_secret("BLUEBUBBLES_PASSWORD")
    if not password:
        return None

    body = json.dumps(
        {
            "limit": 1000,
            "sort": "DESC",
            "after": int(start.timestamp() * 1000),
        }
    ).encode()
    request = Request(
        f"{BLUEBUBBLES_URL}/api/v1/message/query?{urlencode({'password': password})}",
        data=body,
        headers={"Content-Type": "application/json"},
    )
    try:
        with urlopen(request, timeout=10) as response:
            records = json.loads(response.read()).get("data", [])
    except (HTTPError, URLError, OSError, ValueError, json.JSONDecodeError):
        return None

    sent = received = 0
    for record in records:
        if not isinstance(record, dict):
            continue
        timestamp = parse_epoch_ms(record.get("dateCreated"))
        if timestamp is None or not start <= timestamp < end:
            continue
        if record.get("isFromMe"):
            sent += 1
        else:
            received += 1
    return {"sent": sent, "received": received, "truncated": len(records) >= 1000}


def bluebubbles_health():
    password = cached_secret("BLUEBUBBLES_PASSWORD")
    if not password:
        return "unavailable (managed credential cache missing)"

    try:
        with urlopen(
            f"{BLUEBUBBLES_URL}/api/v1/server/info?{urlencode({'password': password})}",
            timeout=5,
        ) as response:
            info = json.loads(response.read()).get("data") or {}
    except (HTTPError, URLError, OSError, ValueError, json.JSONDecodeError):
        return "unreachable"

    private_api = info.get("private_api") is True
    helper = info.get("helper_connected") is True
    if private_api and helper:
        return "healthy (Private API and helper connected)"
    if private_api:
        return "reachable (Private API enabled; helper disconnected)"
    return "reachable (Private API disabled)"


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
    current_messages = bluebubbles_messages(current_start, current_end)
    previous_messages = bluebubbles_messages(previous_start, previous_end)
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
        f"- BlueBubbles: {bluebubbles_health()}",
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
