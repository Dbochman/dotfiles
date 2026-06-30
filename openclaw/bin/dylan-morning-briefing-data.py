#!/usr/bin/env python3
"""Collect bounded, read-only Calendar and Gmail data for Dylan's briefing."""

from __future__ import annotations

import json
import os
import signal
import subprocess
import time
from collections import Counter
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Callable


ACCOUNT = "dylanbochman@gmail.com"
GWS_BIN = os.environ.get("GWS_BIN", "/opt/homebrew/bin/gws")
COMMAND_TIMEOUT_SECONDS = 30.0
OVERALL_TIMEOUT_SECONDS = 150.0
TOKEN_RETRY_SECONDS = 5.0
TOKEN_RACE_TEXT = "failed to get token"
HEADER_LIMITS = {"from": 320, "subject": 500, "date": 160}
CALENDAR_EVENT_LIMIT = 100
PROCESS_GROUP_GRACE_SECONDS = 1.0

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


def collect_calendar(
    *, deadline: float, clock: Clock, runner: Runner, sleeper: Sleeper
) -> dict[str, object]:
    args = [
        GWS_BIN,
        "calendar",
        "+agenda",
        "--days",
        "7",
        "--format",
        "json",
        "--account",
        ACCOUNT,
    ]
    result = run_with_token_retry(
        args,
        os.environ.copy(),
        deadline=deadline,
        clock=clock,
        runner=runner,
        sleeper=sleeper,
    )
    payload, error = parse_object(result)
    if payload is None:
        return {"status": "unavailable", "reason": error or "command_error"}
    raw_events = payload.get("events") or []
    if not isinstance(raw_events, list):
        return {"status": "unavailable", "reason": "invalid_response"}

    events: list[dict[str, str]] = []
    for raw_event in raw_events[:CALENDAR_EVENT_LIMIT]:
        if not isinstance(raw_event, dict):
            continue
        events.append(
            {
                "start": clean_text(raw_event.get("start", ""), 80),
                "end": clean_text(raw_event.get("end", ""), 80),
                "summary": clean_text(raw_event.get("summary", ""), 500),
                "location": clean_text(raw_event.get("location", ""), 500),
            }
        )
    count = payload.get("count")
    event_count = count if isinstance(count, int) else len(raw_events)
    return {
        "status": "ok",
        "count": event_count,
        "truncated": event_count > len(events),
        "events": events,
    }


def gmail_environment() -> dict[str, str]:
    env = os.environ.copy()
    env["GOOGLE_WORKSPACE_CLI_ACCOUNT"] = ACCOUNT
    return env


def gmail_call(
    resource: str,
    params: dict[str, object],
    *,
    deadline: float,
    clock: Clock,
    runner: Runner,
    sleeper: Sleeper,
) -> tuple[dict[str, object] | None, str | None]:
    args = [
        GWS_BIN,
        "gmail",
        "users",
        "messages",
        resource,
        "--params",
        json.dumps(params, separators=(",", ":")),
    ]
    result = run_with_token_retry(
        args,
        gmail_environment(),
        deadline=deadline,
        clock=clock,
        runner=runner,
        sleeper=sleeper,
    )
    return parse_object(result)


def clean_text(value: object, limit: int) -> str:
    normalized = " ".join(str(value).split())
    if len(normalized) <= limit:
        return normalized
    return f"{normalized[: limit - 1]}…"


def header_summary(payload: dict[str, object]) -> dict[str, str] | None:
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
    return selected if any(selected.values()) else None


def collect_inbox(
    *, deadline: float, clock: Clock, runner: Runner, sleeper: Sleeper
) -> dict[str, object]:
    listing, error = gmail_call(
        "list",
        {"userId": "me", "q": "newer_than:1d", "maxResults": 25},
        deadline=deadline,
        clock=clock,
        runner=runner,
        sleeper=sleeper,
    )
    if listing is None:
        return {
            "status": "unavailable",
            "reason": error or "command_error",
            "count": 0,
            "messages": [],
        }

    raw_messages = listing.get("messages") or []
    if not isinstance(raw_messages, list):
        return {
            "status": "unavailable",
            "reason": "invalid_response",
            "count": 0,
            "messages": [],
        }

    message_ids = [
        item.get("id")
        for item in raw_messages
        if isinstance(item, dict) and isinstance(item.get("id"), str)
    ][:25]
    summaries: list[dict[str, str]] = []
    errors: Counter[str] = Counter()
    for message_id in message_ids:
        # GWS 0.4.4 omits payload.headers when metadataHeaders is supplied.
        # Request metadata without that parameter, then retain only three
        # headers locally so snippets, IDs, and other metadata are not emitted.
        message, fetch_error = gmail_call(
            "get",
            {"userId": "me", "id": message_id, "format": "metadata"},
            deadline=deadline,
            clock=clock,
            runner=runner,
            sleeper=sleeper,
        )
        if message is None:
            errors[fetch_error or "command_error"] += 1
            continue
        summary = header_summary(message)
        if summary is None:
            errors["missing_headers"] += 1
            continue
        summaries.append(summary)

    estimate = listing.get("resultSizeEstimate")
    estimated_count = estimate if isinstance(estimate, int) else len(message_ids)
    result: dict[str, object] = {
        "status": "partial" if errors else "ok",
        "count": len(message_ids),
        "estimatedCount": estimated_count,
        "truncated": estimated_count > len(message_ids),
        "messages": summaries,
        "failedCount": sum(errors.values()),
    }
    if errors:
        result["errorCounts"] = dict(sorted(errors.items()))
    return result


def collect_data(
    *,
    runner: Runner = run_command,
    sleeper: Sleeper = time.sleep,
    clock: Clock = time.monotonic,
) -> dict[str, object]:
    deadline = clock() + OVERALL_TIMEOUT_SECONDS
    return {
        "schemaVersion": 1,
        "calendar": collect_calendar(
            deadline=deadline, clock=clock, runner=runner, sleeper=sleeper
        ),
        "inbox": collect_inbox(
            deadline=deadline, clock=clock, runner=runner, sleeper=sleeper
        ),
    }


def main() -> int:
    try:
        with termination_signal_handlers():
            payload = collect_data()
    except Exception:
        payload = {
            "schemaVersion": 1,
            "calendar": {"status": "unavailable", "reason": "internal_error"},
            "inbox": {
                "status": "unavailable",
                "reason": "internal_error",
                "count": 0,
                "messages": [],
            },
        }
    print(json.dumps(payload, separators=(",", ":"), ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
