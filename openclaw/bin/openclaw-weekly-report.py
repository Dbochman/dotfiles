#!/usr/bin/env python3
"""Generate the OpenClaw weekly activity and health report.

The report intentionally uses durable session, cron, and local Messages records
instead of transient gateway logs. It is designed to be called by the weekly
cron agent, which should return its stdout verbatim.
"""

import json
import os
import re
import sqlite3
import stat
import subprocess
import sys
from contextlib import closing
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import urlopen
from zoneinfo import ZoneInfo

try:
    from compression import zstd
except ImportError:
    zstd = None

sys.path.insert(0, str(Path(__file__).resolve().parent))
from openclaw_cron_sqlite import connect as connect_cron


HOME = Path.home()
LOCAL_TZ = ZoneInfo("America/New_York")
UTC = timezone.utc
SESSIONS_DIR = HOME / ".openclaw" / "agents" / "main" / "sessions"
SESSION_DB = HOME / ".openclaw" / "agents" / "main" / "agent" / "openclaw-agent.sqlite"
MAX_TRANSCRIPT_BYTES = 32 * 1024 * 1024
CRON_DB = HOME / ".openclaw" / "state" / "openclaw.sqlite"
CRON_STORE_KEY = str(HOME / ".openclaw" / "cron" / "jobs.json")
MESSAGES_DB = HOME / "Library" / "Messages" / "chat.db"
IMSG_BIN = "/opt/homebrew/bin/imsg"
GATEWAY_URL = "http://127.0.0.1:18789/health"
CRISISMODE_CONFIG = HOME / ".crisismode" / "crisismode.yaml"
CRISISMODE_SERVICES = {
    "dns (local-dns)", "disk (local-disk)",
    "plugin (check-launchd-services)", "plugin (check-openclaw-gateway)",
}
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


def session_files():
    if not SESSIONS_DIR.is_dir():
        return []
    # Reset and deleted session files still contain valid historical activity.
    # Trajectory files duplicate those records and are deliberately excluded.
    return [
        path
        for path in SESSIONS_DIR.glob("*.jsonl*")
        if ".trajectory." not in path.name
        and (path.name.endswith(".jsonl") or ".jsonl.reset." in path.name
             or ".jsonl.deleted." in path.name)
        and not any(marker in path.name for marker in (".migrated", ".bak", ".zst"))
    ]


class ActivityUnavailable(Exception):
    pass


def decode_transcript(payload, compressed=False):
    if compressed:
        if zstd is None:
            raise ActivityUnavailable("compressed transcripts require Python 3.14")
        decoder = zstd.ZstdDecompressor()
        payload = decoder.decompress(payload, max_length=MAX_TRANSCRIPT_BYTES + 1)
        if not decoder.eof or decoder.unused_data:
            raise ActivityUnavailable("invalid or oversized compressed transcript")
    if len(payload) > MAX_TRANSCRIPT_BYTES:
        raise ActivityUnavailable("oversized transcript")
    return payload


def session_records():
    """Read canonical events, not active-context projections or migrated copies.

    Reset/deleted archives retain historical activity; event IDs deduplicate
    overlapping generations. Never fall back to stale files after a DB error.
    No transcript content is written or included in the report.
    """
    if SESSION_DB.exists():
        with closing(sqlite3.connect(SESSION_DB.as_uri() + "?mode=ro", uri=True, timeout=5)) as conn:
            conn.execute("PRAGMA query_only=ON")
            conn.execute("BEGIN")
            tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if "transcript_events" not in tables:
                raise ActivityUnavailable("unsupported session database schema")
            columns = {row[1] for row in conn.execute("PRAGMA table_info(transcript_events)")}
            blob_column = "event_zstd" if "event_zstd" in columns else "NULL"
            for session_id, plain, blob in conn.execute(
                f"SELECT session_id, event_json, {blob_column} FROM transcript_events"
            ):
                yield session_id, json.loads(decode_transcript(blob if blob is not None else plain, blob is not None))
            if "session_transcript_archives" in tables:
                for session_id, encoding, blob in conn.execute(
                    "SELECT session_id, encoding, archive_blob FROM session_transcript_archives"
                ):
                    if encoding not in {"zstd", "jsonl"}:
                        raise ActivityUnavailable("unsupported archive encoding")
                    for line in decode_transcript(blob, encoding == "zstd").splitlines():
                        if line.strip():
                            yield session_id, json.loads(line)
            if "session_transcript_cold_archives" in tables and conn.execute(
                "SELECT 1 FROM session_transcript_cold_archives LIMIT 1"
            ).fetchone():
                raise ActivityUnavailable("cold archive coverage requires reader support")
        return
    paths = session_files()
    if not paths:
        raise ActivityUnavailable("session source missing")
    for path in paths:
        for line in path.read_text().splitlines():
            if line.strip():
                yield path.name.split(".jsonl", 1)[0], json.loads(line)


def week_windows(now):
    local_now = now.astimezone(LOCAL_TZ)
    monday = local_now.date() - timedelta(days=local_now.weekday())
    current_start = datetime.combine(monday, datetime.min.time(), LOCAL_TZ).astimezone(UTC)
    previous_start = (datetime.combine(monday, datetime.min.time(), LOCAL_TZ) - timedelta(days=7)).astimezone(UTC)
    # Compare the same local weekday/time, including weeks with a DST change.
    previous_end = (local_now - timedelta(days=7)).astimezone(UTC)
    return (current_start, now), (previous_start, previous_end)


def collect_session_activity(start, end):
    activity = {
        "model_turns": 0,
        "sessions": set(),
        "tool_calls": 0,
        "rate_limits": 0,
        "daily": Counter(),
        "models": Counter(),
        "available": True,
        "error": None,
    }

    seen = set()
    latest_timestamp = None
    try:
        for session_id, record in session_records():
            if not isinstance(record, dict):
                raise ActivityUnavailable("malformed transcript record")
            timestamp = parse_timestamp(record.get("timestamp"))
            if timestamp is not None:
                latest_timestamp = max(latest_timestamp or timestamp, timestamp)
            message = record.get("message")
            if record.get("type") != "message" or not isinstance(message, dict):
                continue
            if message.get("role") != "assistant":
                continue
            if (message.get("model") == "delivery-mirror" or message.get("openclawDelivery")
                    or message.get("api") == "openclaw-transcript"):
                continue
            if timestamp is None:
                raise ActivityUnavailable("assistant event timestamp missing or invalid")
            if not start <= timestamp < end:
                continue
            event_id = record.get("id")
            if not isinstance(event_id, str) or not event_id:
                raise ActivityUnavailable("assistant event identity missing")
            identity = (session_id, event_id)
            if identity in seen:
                continue
            seen.add(identity)

            activity["model_turns"] += 1
            activity["sessions"].add(session_id)
            activity["daily"][timestamp.astimezone(LOCAL_TZ).date()] += 1

            model = message.get("model") or record.get("modelId")
            if isinstance(model, str) and model and model != "delivery-mirror":
                activity["models"][model] += 1
            else:
                activity["models"]["unknown"] += 1

            error_message = str(message.get("errorMessage") or "").lower()
            if re.search(r"rate[ _-]?limit|\b429\b", error_message):
                activity["rate_limits"] += 1

            content = message.get("content")
            if not isinstance(content, list):
                continue
            activity["tool_calls"] += sum(
                1
                for block in content
                if isinstance(block, dict) and block.get("type") in {"toolCall", "tool_use"}
            )
        if not SESSION_DB.exists() and (latest_timestamp is None or latest_timestamp < start):
            raise ActivityUnavailable("legacy transcripts do not establish coverage for this week")
    except Exception as exc:
        # Never expose exception text containing database/transcript content.
        activity["available"] = False
        activity["error"] = str(exc) if isinstance(exc, ActivityUnavailable) else "session source unreadable"

    activity["sessions"] = len(activity["sessions"])
    return activity


def collect_cron_runs(start, end):
    runs = {"completed": 0, "failed": 0, "skipped": 0, "delivered": 0, "available": False}
    if not CRON_DB.is_file():
        return runs

    start_ms = int(start.timestamp() * 1000)
    end_ms = int(end.timestamp() * 1000)
    try:
        with closing(connect_cron(CRON_DB)) as conn:
            rows = conn.execute(
                """
                SELECT status, delivered
                  FROM cron_run_logs
                 WHERE store_key = ? AND ts >= ? AND ts < ?
                """,
                (CRON_STORE_KEY, start_ms, end_ms),
            ).fetchall()
    except (OSError, sqlite3.Error):
        return runs

    for status, delivered in rows:
        if status == "ok":
            runs["completed"] += 1
        elif status == "error":
            runs["failed"] += 1
        elif status == "skipped":
            runs["skipped"] += 1
        else:
            return runs
        if delivered:
            runs["delivered"] += 1
    runs["available"] = True
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


def read_opentable_refresh_status():
    path = HOME / ".openclaw" / "run" / "opentable-refresh-status.json"
    expected_fields = {
        "schema_version",
        "attempt_kind",
        "attempt_number",
        "outcome",
        "stage",
        "reason_code",
        "started_at",
        "completed_at",
        "last_success_at",
    }
    try:
        metadata = path.lstat()
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_uid != os.getuid()
            or stat.S_IMODE(metadata.st_mode) != 0o600
            or metadata.st_nlink != 1
            or not 0 < metadata.st_size <= 4096
        ):
            return None
        value = json.loads(path.read_text(encoding="utf-8"))
        if (set(value) != expected_fields or type(value["schema_version"]) is not int
                or value["schema_version"] != 1):
            return None
        if value["attempt_kind"] not in {"manual", "scheduled"}:
            return None
        attempt_number = value["attempt_number"]
        if (
            not isinstance(attempt_number, int)
            or isinstance(attempt_number, bool)
            or attempt_number not in {1, 2}
            or value["outcome"] not in {"success", "failed"}
        ):
            return None
        code = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
        if not code.fullmatch(value["stage"]) or not code.fullmatch(value["reason_code"]):
            return None
        for field in ("started_at", "completed_at"):
            parsed = parse_timestamp(value[field])
            if parsed is None:
                return None
            value[f"_{field}"] = parsed
        if value["last_success_at"] is not None and parse_timestamp(value["last_success_at"]) is None:
            return None
        if value["outcome"] == "success" and parse_timestamp(value["last_success_at"]) != value["_completed_at"]:
            return None
    except (OSError, TypeError, ValueError, UnicodeError, json.JSONDecodeError):
        return None
    return value


def opentable_attention(now):
    status = launchd_status("ai.openclaw.opentable-refresh")
    log_path = HOME / ".openclaw" / "logs" / "opentable-refresh.log"
    if not status["loaded"]:
        return "OpenTable refresh job is not loaded; scheduled refresh unavailable"
    refresh = read_opentable_refresh_status()
    try:
        failed_at = datetime.fromtimestamp(log_path.stat().st_mtime, LOCAL_TZ)
    except OSError:
        failed_at = None
    if refresh:
        completed = refresh["_completed_at"]
        if completed > now or refresh["_started_at"] > completed:
            return "OpenTable refresh verification unavailable (invalid status time)"
        if now - completed > timedelta(days=8):
            return "OpenTable refresh verification is stale; current authentication unknown"
        # A manual recovery does not reset launchd's old LastExitStatus. Use
        # its protected success receipt when it supersedes that old failure.
        newer_launch_failure = (
            status["exit_code"] not in (None, 0) and failed_at is not None
            and failed_at.astimezone(UTC) > completed + timedelta(seconds=5)
        )
        if refresh["outcome"] == "success" and not newer_launch_failure:
            if status["exit_code"] not in (None, 0) and failed_at is None:
                return "OpenTable refresh succeeded, but the launchd failure time is unknown; review scheduling"
            return None
        if not newer_launch_failure:
            attempt = (f"scheduled attempt {refresh['attempt_number']}/2"
                       if refresh["attempt_kind"] == "scheduled" else "a manual attempt")
            return (
                f"OpenTable token refresh failed {completed.astimezone(LOCAL_TZ).strftime('%a %m/%d')} "
                f"after {attempt} at {refresh['stage']} ({refresh['reason_code']}); recovery not yet verified"
            )
    if status["exit_code"] not in (None, 0):
        when = f" {failed_at.strftime('%a %m/%d')}" if failed_at else ""
        return f"OpenTable refresh job last failed{when}; current authentication not verified"
    return "OpenTable refresh verification unavailable; current authentication unknown"


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
            ["crisismode", "scan", "--config", str(CRISISMODE_CONFIG), "--json"],
            cwd=CRISISMODE_CONFIG.parent,
            # The deterministic report does not need an external AI summary.
            env={**os.environ, "ANTHROPIC_API_KEY": ""},
            capture_output=True,
            text=True,
            timeout=45,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return "unavailable", [], []

    if result.returncode != 0:
        return "unavailable", [], []

    scans = [
        document
        for document in parse_json_documents(result.stdout)
        if isinstance(document, dict) and isinstance(document.get("findings"), list)
    ]
    if not scans:
        return "unavailable", [], []
    return summarize_crisismode_scan(scans[-1])


def summarize_crisismode_scan(scan):
    """Use current evidence, never positional IDs, to describe local health."""
    score = scan.get("score")
    if type(score) not in (int, float) or not 0 <= score <= 100:
        return "unavailable", [], []
    findings = scan.get("findings")
    if not isinstance(findings, list) or not findings or any(
        not isinstance(finding, dict) for finding in findings
    ):
        return "unavailable", [], []
    actionable = []
    notes = []
    for finding in findings:
        service = str(finding.get("service") or "unidentified check")
        signals = finding.get("signals")
        if not isinstance(signals, list) or any(not isinstance(s, dict) for s in signals):
            actionable.append(f"{service}: invalid signal evidence")
            continue
        bad_signals = [s for s in signals if s.get("status") not in ("healthy", "info")]
        if finding.get("status") != "healthy" or bad_signals:
            evidence = {s.get("source"): s for s in signals if isinstance(s.get("source"), str)}
            if (service == "dns (local-dns)" and finding.get("status") == "recovering"
                    and len(bad_signals) == 1
                    and evidence.get("resolver_reachability", {}).get("status") == "healthy"
                    and evidence.get("resolution_correctness", {}).get("status") == "healthy"
                    and bad_signals[0].get("source") == "zone_consistency"
                    and bad_signals[0].get("detail") ==
                    "Split-brain detected: resolvers returning different answers for the same hostname."):
                # Explain the evidence without claiming differing answers are benign.
                summary = "answer sets differ; reachability and lookup checks passed; cause unverified"
            else:
                summary = str(finding.get("summary") or "health not verified")
                if bad_signals:
                    sources = ", ".join(str(s.get("source") or "unknown signal") for s in bad_signals)
                    summary += f" (signals: {sources})"
            actionable.append(f"{service}: {summary}")

    seen = {f.get("service") for f in findings if isinstance(f.get("service"), str)}
    for service in sorted(CRISISMODE_SERVICES - seen):
        actionable.append(f"coverage missing: {service}")
    best_effort = sum(f.get("bestEffort") is True for f in findings)
    if best_effort:
        notes.append(f"{best_effort} checks labeled best-effort by upstream")
    triage = scan.get("triage")
    if not isinstance(triage, dict) or not isinstance(triage.get("layers"), list) or not triage["layers"]:
        actionable.append("network triage unavailable")
    else:
        incomplete = [l for l in triage["layers"] if not isinstance(l, dict)
                      or l.get("status") not in ("pass", "skipped")]
        if triage.get("verdict") != "healthy" or incomplete:
            actionable.append(f"network triage: {triage.get('verdict') or 'unknown'}; "
                              f"{len(incomplete)} failed or unverified layers")
    visibility = scan.get("visibility")
    if not isinstance(visibility, dict) or any(
        not isinstance(visibility.get(key), list) for key in ("watching", "blocked", "invisible")
    ):
        actionable.append("scan coverage unavailable")
    else:
        if visibility["blocked"]:
            actionable.append(f"coverage blocked: {len(visibility['blocked'])} detected services")
        if visibility["invisible"]:
            notes.append(f"{len(visibility['invisible'])} documented visibility limits")
    return str(score), actionable, notes


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
    crisis_score, crisis_findings, crisis_notes = crisismode_summary()

    period = (
        f"{current_start.astimezone(LOCAL_TZ).strftime('%a %m/%d')}"
        f"-{current_end.astimezone(LOCAL_TZ).strftime('%a %m/%d')}"
    )
    lines = [
        f"OpenClaw Weekly Report ({period})",
        f"Week to date through {now.astimezone(LOCAL_TZ).strftime('%a %H:%M %Z')}; prev compares the same local weekday/time.",
        "",
        "Activity (main agent, including automation):",
        f"- Model turns: {activity_metric(current, 'model_turns')} (prev: {activity_metric(previous, 'model_turns')})",
        f"- Active sessions: {activity_metric(current, 'sessions')} (prev: {activity_metric(previous, 'sessions')})",
        f"- Tool calls: {activity_metric(current, 'tool_calls')} (prev: {activity_metric(previous, 'tool_calls')})",
        f"- iMessages: {format_messages(current_messages)} (prev: {format_messages(previous_messages)})",
        f"- Busiest day: {busiest_day(current['daily']) if current['available'] else 'unavailable'}",
        f"- Model mix: {format_models(current['models']) if current['available'] else 'unavailable'}",
        "",
        "Automation:",
        (f"- Scheduled runs: {current_runs['completed']} completed, {current_runs['failed']} failed, {current_runs['skipped']} skipped"
         if current_runs['available'] else "- Scheduled runs: unavailable (cron source unreadable)"),
        f"- Cron deliveries: {current_runs['delivered'] if current_runs['available'] else 'unavailable'}",
        f"- Model rate-limit responses: {activity_metric(current, 'rate_limits')}",
        "",
        "Health (Mac Mini):",
        f"- Gateway: {gateway_health()}",
        f"- iMessage: {imessage_health()}",
        f"- Disk: {apfs_disk_usage()}",
    ]

    if crisis_score == "unavailable":
        lines.append("- CrisisMode: unavailable (scan not verified)")
    elif crisis_findings:
        detail = '; '.join(crisis_findings[:2])
        if len(crisis_findings) > 2:
            detail += f"; {len(crisis_findings) - 2} additional findings (run crisismode scan --json)"
        lines.append(f"- CrisisMode: {crisis_score}/100 raw; attention: {detail}")
    else:
        lines.append(
            f"- CrisisMode: {crisis_score}/100 raw; no actionable findings in checked Mini services"
        )
    if crisis_notes:
        lines.append(f"- CrisisMode coverage: {'; '.join(crisis_notes)}; Mini scope only")

    attention = opentable_attention(now)
    if attention:
        lines.append(f"- Attention: {attention}")
    else:
        refresh = read_opentable_refresh_status()
        if refresh:
            when = refresh['_completed_at'].astimezone(LOCAL_TZ).strftime('%a %m/%d %H:%M %Z')
            lines.append(f"- OpenTable: last verified refresh succeeded {when}")
    for label, activity in (("current", current), ("previous", previous)):
        if not activity["available"]:
            lines.append(f"- Data quality: {label} activity unavailable ({activity['error']})")
    return "\n".join(lines)


def activity_metric(activity, name):
    return str(activity[name]) if activity["available"] else "unavailable"


def main():
    print(report(datetime.now(UTC)))


if __name__ == "__main__":
    main()
