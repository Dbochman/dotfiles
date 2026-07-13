#!/usr/bin/env python3
"""Collect bounded, read-only data for Julia's morning briefing."""

from __future__ import annotations

import json
import math
import os
import re
import signal
import sqlite3
import subprocess
import time
import urllib.error
import urllib.request
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, time as datetime_time, timedelta
from pathlib import Path
from typing import Callable
from zoneinfo import ZoneInfo


ACCOUNT = os.environ.get("JULIA_EMAIL", "")
GWS_BIN = os.environ.get("GWS_BIN", "/opt/homebrew/bin/gws")
STATE_DB = Path(
    os.environ.get(
        "OPENCLAW_STATE_DB", str(Path.home() / ".openclaw/state/openclaw.sqlite")
    )
)
CRON_STORE_KEY = str(Path.home() / ".openclaw/cron/jobs.json")
SLEEP_SNAPSHOT = Path(
    os.environ.get("JULIA_SLEEP_SNAPSHOT", "/tmp/8sleep-julia-latest.txt")
)
TRIAGE_JOB_ID = "gws-julia-morning-triage-0001"
TIME_ZONE = ZoneInfo("America/New_York")
NET_WORTH_URL = "http://127.0.0.1:8586/api/household-net-worth"
FIRE_URL = "http://127.0.0.1:8585/api/fire"

COMMAND_TIMEOUT_SECONDS = 30.0
OVERALL_TIMEOUT_SECONDS = 150.0
TOKEN_RETRY_SECONDS = 5.0
TOKEN_RACE_TEXT = "failed to get token"
HTTP_TIMEOUT_SECONDS = 5.0
PROCESS_GROUP_GRACE_SECONDS = 1.0
CALENDAR_EVENT_LIMIT = 100
NEW_ARRIVAL_DETAIL_LIMIT = 25
SLEEP_TEXT_LIMIT = 1200
HEADER_LIMITS = {"from": 320, "subject": 500, "date": 160}

_ACTIVE_PROCESS: subprocess.Popen[str] | None = None
_SPAWNING_PROCESS = False
_DEFERRED_TERMINATION_SIGNAL: int | None = None


@dataclass(frozen=True)
class CommandResult:
    returncode: int
    stdout: str
    stderr: str


Runner = Callable[[list[str], dict[str, str], float], CommandResult]
Sleeper = Callable[[float], None]
Clock = Callable[[], float]
HttpGetter = Callable[[str, float], dict[str, object]]


def clean_text(value: object, limit: int) -> str:
    normalized = " ".join(str(value).split())
    if len(normalized) <= limit:
        return normalized
    return f"{normalized[: limit - 1]}…"


def run_command(
    args: list[str], env: dict[str, str], timeout: float
) -> CommandResult:
    global _ACTIVE_PROCESS, _SPAWNING_PROCESS

    process: subprocess.Popen[str] | None = None
    try:
        _SPAWNING_PROCESS = True
        try:
            process = subprocess.Popen(
                args,
                env=env,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                start_new_session=True,
            )
            _ACTIVE_PROCESS = process
        finally:
            _SPAWNING_PROCESS = False
            raise_deferred_termination()
        stdout, stderr = process.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        stop_process_group(process)
        return CommandResult(124, "", "command timeout")
    except OSError:
        stop_process_group(process)
        return CommandResult(127, "", "command unavailable")
    except BaseException:
        stop_process_group(process)
        raise
    finally:
        if _ACTIVE_PROCESS is process:
            _ACTIVE_PROCESS = None
    return CommandResult(process.returncode, stdout or "", stderr or "")


def signal_process_group(
    process: subprocess.Popen[str] | None, signum: signal.Signals
) -> None:
    if process is None:
        return
    try:
        os.killpg(process.pid, signum)
    except (ProcessLookupError, PermissionError):
        pass


def stop_process_group(process: subprocess.Popen[str] | None) -> None:
    if process is None:
        return
    signal_process_group(process, signal.SIGTERM)
    try:
        process.communicate(timeout=PROCESS_GROUP_GRACE_SECONDS)
    except subprocess.TimeoutExpired:
        signal_process_group(process, signal.SIGKILL)
        try:
            process.communicate()
        except (OSError, ValueError):
            pass
    except (OSError, ValueError):
        pass


def termination_handler(signum: int, _frame: object) -> None:
    global _DEFERRED_TERMINATION_SIGNAL

    signal_process_group(_ACTIVE_PROCESS, signal.SIGTERM)
    if _SPAWNING_PROCESS:
        if _DEFERRED_TERMINATION_SIGNAL is None:
            _DEFERRED_TERMINATION_SIGNAL = signum
        return
    raise SystemExit(128 + signum)


def raise_deferred_termination() -> None:
    global _DEFERRED_TERMINATION_SIGNAL

    if _DEFERRED_TERMINATION_SIGNAL is None:
        return
    signum = _DEFERRED_TERMINATION_SIGNAL
    _DEFERRED_TERMINATION_SIGNAL = None
    raise SystemExit(128 + signum)


@contextmanager
def termination_signal_handlers():
    previous: dict[int, object] = {}
    handled = (signal.SIGHUP, signal.SIGINT, signal.SIGTERM)
    try:
        for signum in handled:
            previous[signum] = signal.getsignal(signum)
            signal.signal(signum, termination_handler)
        yield
    finally:
        for signum, handler in previous.items():
            signal.signal(signum, handler)


def has_token_race(result: CommandResult) -> bool:
    if result.returncode != 0:
        return TOKEN_RACE_TEXT in f"{result.stdout}\n{result.stderr}".lower()
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError:
        return False
    if not isinstance(payload, dict) or payload.get("error") is None:
        return False
    return TOKEN_RACE_TEXT in json.dumps(payload["error"]).lower()


def failure_reason(result: CommandResult) -> str:
    combined = f"{result.stdout}\n{result.stderr}".lower()
    if TOKEN_RACE_TEXT in combined:
        return "token_error"
    if "no credentials provided" in combined or "auth login" in combined:
        return "auth_error"
    if result.returncode == 124:
        return "timeout"
    if result.returncode == 127:
        return "command_unavailable"
    return "command_error"


def run_with_token_retry(
    args: list[str],
    env: dict[str, str],
    *,
    deadline: float,
    clock: Clock,
    runner: Runner,
    sleeper: Sleeper,
) -> CommandResult:
    remaining = deadline - clock()
    if remaining <= 0:
        return CommandResult(124, "", "overall deadline")
    result = runner(args, env, min(COMMAND_TIMEOUT_SECONDS, remaining))
    if has_token_race(result):
        remaining = deadline - clock()
        if remaining <= TOKEN_RETRY_SECONDS:
            return CommandResult(124, "", "overall deadline")
        sleeper(TOKEN_RETRY_SECONDS)
        remaining = deadline - clock()
        if remaining <= 0:
            return CommandResult(124, "", "overall deadline")
        result = runner(args, env, min(COMMAND_TIMEOUT_SECONDS, remaining))
    return result


def parse_object(result: CommandResult) -> tuple[dict[str, object] | None, str | None]:
    if result.returncode != 0:
        return None, failure_reason(result)
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError:
        return None, "invalid_response"
    if not isinstance(payload, dict):
        return None, "invalid_response"
    if payload.get("error") is not None:
        return None, "api_error"
    return payload, None


def gws_environment() -> dict[str, str]:
    env = os.environ.copy()
    env["GOOGLE_WORKSPACE_CLI_ACCOUNT"] = ACCOUNT
    return env


def gws_call(
    service_args: list[str],
    params: dict[str, object],
    *,
    deadline: float,
    clock: Clock,
    runner: Runner,
    sleeper: Sleeper,
) -> tuple[dict[str, object] | None, str | None]:
    args = [
        GWS_BIN,
        *service_args,
        "--params",
        json.dumps(params, separators=(",", ":")),
    ]
    result = run_with_token_retry(
        args,
        gws_environment(),
        deadline=deadline,
        clock=clock,
        runner=runner,
        sleeper=sleeper,
    )
    return parse_object(result)


def strip_json_fence(value: str) -> str:
    text = value.strip()
    match = re.fullmatch(r"```(?:json)?\s*(.*?)\s*```", text, flags=re.DOTALL)
    return match.group(1).strip() if match else text


def safe_nonnegative_int(value: object) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else 0


def load_triage_handoff(
    today: datetime,
    *,
    db_path: Path = STATE_DB,
) -> tuple[dict[str, object], set[str] | None]:
    try:
        connection = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        try:
            rows = connection.execute(
                """SELECT run_at_ms, summary FROM cron_run_logs
                   WHERE store_key = ? AND job_id = ? AND status = 'ok'
                   ORDER BY run_at_ms DESC LIMIT 50""",
                (CRON_STORE_KEY, TRIAGE_JOB_ID),
            ).fetchall()
        finally:
            connection.close()
    except (OSError, sqlite3.Error):
        return {"status": "unavailable", "reason": "database_unavailable"}, None

    today_date = today.date()
    for run_at_ms, summary in rows:
        if not isinstance(run_at_ms, int) or not isinstance(summary, str):
            continue
        run_date = datetime.fromtimestamp(run_at_ms / 1000, TIME_ZONE).date()
        if run_date != today_date:
            if run_date < today_date:
                break
            continue
        try:
            payload = json.loads(strip_json_fence(summary))
        except json.JSONDecodeError:
            continue
        if not isinstance(payload, dict):
            continue
        if payload.get("schemaVersion") != 1 or payload.get("date") != str(today_date):
            continue

        unread_raw = payload.get("unreadAfter")
        if not isinstance(unread_raw, list) or not all(
            isinstance(item, str) for item in unread_raw
        ):
            continue
        attention_raw = payload.get("attention")
        if not isinstance(attention_raw, list):
            continue

        attention: list[dict[str, str]] = []
        for item in attention_raw:
            if not isinstance(item, dict):
                continue
            attention.append(
                {
                    "from": clean_text(item.get("from", ""), 320),
                    "subject": clean_text(item.get("subject", ""), 500),
                    "reason": clean_text(item.get("reason", ""), 700),
                    "deadline": clean_text(item.get("deadline", ""), 160),
                    "draftStatus": clean_text(item.get("draftStatus", "none"), 16),
                }
            )

        errors = payload.get("errors")
        error_count = len(errors) if isinstance(errors, list) else 0
        return (
            {
                "status": "ok",
                "handoffStatus": clean_text(payload.get("status", "unknown"), 24),
                "processed": safe_nonnegative_int(payload.get("processed")),
                "markedRead": safe_nonnegative_int(payload.get("markedRead")),
                "leftUnread": safe_nonnegative_int(payload.get("leftUnread")),
                "draftsCreated": safe_nonnegative_int(payload.get("draftsCreated")),
                "draftsExisting": safe_nonnegative_int(payload.get("draftsExisting")),
                "archived": safe_nonnegative_int(payload.get("archived")),
                "trashed": safe_nonnegative_int(payload.get("trashed")),
                "errorCount": error_count,
                "attention": attention,
            },
            set(unread_raw),
        )
    return {"status": "unavailable", "reason": "same_day_handoff_missing"}, None


def collect_calendar(
    today: datetime,
    *,
    deadline: float,
    clock: Clock,
    runner: Runner,
    sleeper: Sleeper,
) -> dict[str, object]:
    if not ACCOUNT.strip():
        return {"status": "unavailable", "reason": "missing_account"}

    local_date = today.date()
    start = datetime.combine(local_date, datetime_time.min, TIME_ZONE)
    end = datetime.combine(local_date + timedelta(days=1), datetime_time.min, TIME_ZONE)
    params: dict[str, object] = {
        "calendarId": "primary",
        "timeMin": start.isoformat(),
        "timeMax": end.isoformat(),
        "singleEvents": True,
        "orderBy": "startTime",
        "maxResults": 2500,
    }
    raw_events: list[object] = []
    while True:
        page, error = gws_call(
            ["calendar", "events", "list"],
            params,
            deadline=deadline,
            clock=clock,
            runner=runner,
            sleeper=sleeper,
        )
        if page is None:
            return {"status": "unavailable", "reason": error or "command_error"}
        items = page.get("items") or []
        if not isinstance(items, list):
            return {"status": "unavailable", "reason": "invalid_response"}
        raw_events.extend(items)
        page_token = page.get("nextPageToken")
        if not isinstance(page_token, str) or not page_token:
            break
        params["pageToken"] = page_token

    events: list[dict[str, object]] = []
    for raw_event in raw_events[:CALENDAR_EVENT_LIMIT]:
        if not isinstance(raw_event, dict):
            continue
        start_value = raw_event.get("start")
        end_value = raw_event.get("end")
        events.append(
            {
                "start": start_value if isinstance(start_value, dict) else {},
                "end": end_value if isinstance(end_value, dict) else {},
                "summary": clean_text(raw_event.get("summary", ""), 500),
                "location": clean_text(raw_event.get("location", ""), 500),
                "description": clean_text(raw_event.get("description", ""), 700),
            }
        )
    return {
        "status": "ok",
        "count": len(raw_events),
        "truncated": len(raw_events) > len(events),
        "events": events,
    }


def list_unread_ids(
    *,
    deadline: float,
    clock: Clock,
    runner: Runner,
    sleeper: Sleeper,
) -> tuple[list[str] | None, str | None]:
    params: dict[str, object] = {
        "userId": "me",
        "q": "is:unread in:inbox",
        "maxResults": 100,
    }
    ids: list[str] = []
    while True:
        page, error = gws_call(
            ["gmail", "users", "messages", "list"],
            params,
            deadline=deadline,
            clock=clock,
            runner=runner,
            sleeper=sleeper,
        )
        if page is None:
            return None, error
        messages = page.get("messages") or []
        if not isinstance(messages, list):
            return None, "invalid_response"
        ids.extend(
            item["id"]
            for item in messages
            if isinstance(item, dict) and isinstance(item.get("id"), str)
        )
        page_token = page.get("nextPageToken")
        if not isinstance(page_token, str) or not page_token:
            return ids, None
        params["pageToken"] = page_token


def message_summary(payload: dict[str, object]) -> dict[str, str] | None:
    message_payload = payload.get("payload")
    if not isinstance(message_payload, dict):
        return None
    headers = message_payload.get("headers")
    if not isinstance(headers, list):
        return None
    selected = {"from": "", "subject": "", "date": ""}
    for header in headers:
        if not isinstance(header, dict):
            continue
        name = str(header.get("name", "")).lower()
        if name in selected:
            selected[name] = clean_text(header.get("value", ""), HEADER_LIMITS[name])
    selected["snippet"] = clean_text(payload.get("snippet", ""), 700)
    return selected if any(selected.values()) else None


def collect_post_triage_arrivals(
    triage_unread_ids: set[str] | None,
    *,
    deadline: float,
    clock: Clock,
    runner: Runner,
    sleeper: Sleeper,
) -> dict[str, object]:
    if triage_unread_ids is None:
        return {"status": "skipped", "reason": "triage_unavailable"}
    if not ACCOUNT.strip():
        return {"status": "unavailable", "reason": "missing_account"}

    current_ids, error = list_unread_ids(
        deadline=deadline,
        clock=clock,
        runner=runner,
        sleeper=sleeper,
    )
    if current_ids is None:
        return {"status": "unavailable", "reason": error or "command_error"}

    new_ids = [message_id for message_id in current_ids if message_id not in triage_unread_ids]
    arrivals: list[dict[str, str]] = []
    failed_count = 0
    for message_id in new_ids[:NEW_ARRIVAL_DETAIL_LIMIT]:
        message, fetch_error = gws_call(
            ["gmail", "users", "messages", "get"],
            {"userId": "me", "id": message_id, "format": "full"},
            deadline=deadline,
            clock=clock,
            runner=runner,
            sleeper=sleeper,
        )
        if message is None:
            failed_count += 1
            continue
        summary = message_summary(message)
        if summary is None:
            failed_count += 1
            continue
        arrivals.append(summary)

    return {
        "status": "partial" if failed_count else "ok",
        "count": len(new_ids),
        "truncated": len(new_ids) > NEW_ARRIVAL_DETAIL_LIMIT,
        "failedCount": failed_count,
        "messages": arrivals,
    }


def collect_sleep(today: datetime, *, snapshot_path: Path = SLEEP_SNAPSHOT) -> dict[str, object]:
    try:
        raw = snapshot_path.read_text(encoding="utf-8")[:32768]
    except OSError:
        return {"status": "unavailable", "reason": "missing"}
    lowered = raw.casefold()
    if "unavailable" in lowered:
        return {"status": "unavailable", "reason": "marked_unavailable"}
    header = "\n".join(raw.splitlines()[:4])
    date_tokens = {
        today.date().isoformat(),
        today.strftime("%B %-d, %Y"),
        today.strftime("%m/%d/%Y"),
    }
    if not any(token in header for token in date_tokens):
        return {"status": "unavailable", "reason": "stale"}
    text = clean_text(raw, SLEEP_TEXT_LIMIT)
    if not text:
        return {"status": "unavailable", "reason": "empty"}
    return {"status": "ok", "text": text}


def default_http_getter(url: str, timeout: float) -> dict[str, object]:
    request = urllib.request.Request(url, method="GET")
    with urllib.request.urlopen(request, timeout=timeout) as response:
        payload = json.load(response)
    if not isinstance(payload, dict):
        raise ValueError("response is not an object")
    return payload


def finite_number(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    numeric = float(value)
    return numeric if math.isfinite(numeric) else None


def collect_finances(*, http_getter: HttpGetter = default_http_getter) -> dict[str, object]:
    result: dict[str, object] = {"status": "unavailable"}
    try:
        net_worth_payload = http_getter(NET_WORTH_URL, HTTP_TIMEOUT_SECONDS)
        known_value = finite_number(net_worth_payload.get("known_value"))
        complete = net_worth_payload.get("complete")
        as_of = net_worth_payload.get("as_of")
        if known_value is not None and isinstance(complete, bool) and isinstance(as_of, str):
            result["netWorth"] = {
                "status": "ok",
                "knownValue": known_value,
                "complete": complete,
                "asOf": clean_text(as_of, 40),
            }
        else:
            result["netWorth"] = {"status": "unavailable"}
    except (OSError, ValueError, json.JSONDecodeError, urllib.error.URLError):
        result["netWorth"] = {"status": "unavailable"}

    try:
        fire_payload = http_getter(FIRE_URL, HTTP_TIMEOUT_SECONDS)
        progress = finite_number(fire_payload.get("progress_pct"))
        target = finite_number(fire_payload.get("fire_target"))
        if progress is not None and target is not None:
            result["fire"] = {
                "status": "ok",
                "progressPct": progress,
                "target": target,
            }
        else:
            result["fire"] = {"status": "unavailable"}
    except (OSError, ValueError, json.JSONDecodeError, urllib.error.URLError):
        result["fire"] = {"status": "unavailable"}

    if any(
        isinstance(result.get(key), dict) and result[key].get("status") == "ok"
        for key in ("netWorth", "fire")
    ):
        result["status"] = "ok"
    return result


def collect_data(
    *,
    now: datetime | None = None,
    runner: Runner = run_command,
    sleeper: Sleeper = time.sleep,
    clock: Clock = time.monotonic,
    http_getter: HttpGetter = default_http_getter,
    db_path: Path = STATE_DB,
    sleep_path: Path = SLEEP_SNAPSHOT,
) -> dict[str, object]:
    local_now = now.astimezone(TIME_ZONE) if now else datetime.now(TIME_ZONE)
    deadline = clock() + OVERALL_TIMEOUT_SECONDS
    triage, triage_unread_ids = load_triage_handoff(local_now, db_path=db_path)
    return {
        "schemaVersion": 1,
        "date": local_now.date().isoformat(),
        "triage": triage,
        "calendar": collect_calendar(
            local_now,
            deadline=deadline,
            clock=clock,
            runner=runner,
            sleeper=sleeper,
        ),
        "sleep": collect_sleep(local_now, snapshot_path=sleep_path),
        "finances": collect_finances(http_getter=http_getter),
        "postTriage": collect_post_triage_arrivals(
            triage_unread_ids,
            deadline=deadline,
            clock=clock,
            runner=runner,
            sleeper=sleeper,
        ),
    }


def unavailable_payload() -> dict[str, object]:
    return {
        "schemaVersion": 1,
        "date": datetime.now(TIME_ZONE).date().isoformat(),
        "triage": {"status": "unavailable", "reason": "internal_error"},
        "calendar": {"status": "unavailable", "reason": "internal_error"},
        "sleep": {"status": "unavailable", "reason": "internal_error"},
        "finances": {"status": "unavailable"},
        "postTriage": {"status": "unavailable", "reason": "internal_error"},
    }


def main() -> int:
    try:
        with termination_signal_handlers():
            payload = collect_data()
    except Exception:
        payload = unavailable_payload()
    print(json.dumps(payload, separators=(",", ":"), ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
