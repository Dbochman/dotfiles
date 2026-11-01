#!/usr/bin/env python3
"""Read-only health summary for the nightly cabin public-site deploy.

The deploy job (ai.openclaw.cabin-public-deploy, scripts/deploy-public.sh in the
cabin repo) leaves a tiny private status file. This module turns it into at most
one short warning for Dylan's morning briefing and the weekly report. It never
runs the job, never reads logs, and treats anything it cannot verify as a
warning rather than as healthy.
"""

from __future__ import annotations

import json
import os
import re
import stat
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo


STATUS_PATH = Path.home() / ".local" / "state" / "cabin-public-deploy" / "status.json"
LOCAL_TZ = ZoneInfo("America/New_York")
# The job runs nightly at 03:40; a day and a half without any result means a missed run.
STALE_AFTER = timedelta(hours=36)
MAX_BYTES = 4096
RESULTS = {"deployed", "unchanged", "failed"}
EXPECTED_FIELDS = {"time", "result", "message"}
TIME_FORMAT = "%Y-%m-%dT%H:%M:%SZ"
CONTROL = re.compile(r"[\x00-\x1f\x7f]+")


def parse_time(value) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        return datetime.strptime(value, TIME_FORMAT).replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def clean_message(value: str) -> str:
    """Operational detail only: no control characters, bounded length."""
    return re.sub(r"\s+", " ", CONTROL.sub(" ", value)).strip()[:160]


def load_status(path: Path | None = None) -> tuple[dict | None, str | None]:
    """Return (status, None) when valid, or (None, "missing" | "invalid")."""
    target = STATUS_PATH if path is None else path
    try:
        metadata = target.lstat()
    except FileNotFoundError:
        return None, "missing"
    except OSError:
        return None, "invalid"
    try:
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_uid != os.getuid()
            or stat.S_IMODE(metadata.st_mode) != 0o600
            or metadata.st_nlink != 1
            or not 0 < metadata.st_size <= MAX_BYTES
        ):
            return None, "invalid"
        value = json.loads(target.read_text(encoding="utf-8"))
        if not isinstance(value, dict) or set(value) != EXPECTED_FIELDS:
            return None, "invalid"
        if value["result"] not in RESULTS or not isinstance(value["message"], str):
            return None, "invalid"
        when = parse_time(value["time"])
        if when is None:
            return None, "invalid"
        return {
            "result": value["result"],
            "message": clean_message(value["message"]),
            "_time": when,
        }, None
    except (OSError, TypeError, ValueError, UnicodeError, json.JSONDecodeError):
        return None, "invalid"


def monitoring_warning(now: datetime | None = None, path: Path | None = None) -> str | None:
    """One short sentence when the shared-magazine deploy needs attention, else None."""
    current = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    status, problem = load_status(path)
    if problem == "missing":
        return "Cabin public deploy has not reported yet; the shared magazine may not be updating."
    if problem:
        return "Cabin public deploy status is unreadable; the shared magazine may not be updating."
    when = status["_time"]
    if when > current + timedelta(minutes=5):
        return "Cabin public deploy status has an invalid time; the shared magazine may not be updating."
    label = when.astimezone(LOCAL_TZ).strftime("%a %m/%d %H:%M %Z")
    if status["result"] == "failed":
        detail = f": {status['message']}" if status["message"] else ""
        return f"Cabin public deploy failed {label}{detail}. The shared magazine was left as it was."
    if current - when > STALE_AFTER:
        return f"Cabin public deploy has not run since {label}; the shared magazine may be out of date."
    return None


def briefing_summary(now: datetime | None = None, path: Path | None = None) -> dict:
    warning = monitoring_warning(now=now, path=path)
    return {"status": "ok" if warning is None else "warning", "monitoringWarning": warning}


def main() -> int:
    print(json.dumps(briefing_summary()))
    return 0 if monitoring_warning() is None else 1


if __name__ == "__main__":
    raise SystemExit(main())
