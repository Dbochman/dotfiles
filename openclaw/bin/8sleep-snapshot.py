#!/usr/bin/env python3
"""Capture bounded, private, user-scoped sleep summaries without Pod controls."""

import fcntl
import json
import math
import os
from pathlib import Path
import shlex
import stat
import subprocess
import sys
import tempfile
from datetime import datetime
from zoneinfo import ZoneInfo

TIME_ZONE = ZoneInfo("America/New_York")
OWNERS = ("dylan", "julia")
KEYS = ("EIGHTSLEEP_CLIENT_ID", "EIGHTSLEEP_CLIENT_SECRET", "EIGHTSLEEP_DYLAN_USER_ID", "EIGHTSLEEP_JULIA_USER_ID")
LIMIT = 131072


def load_environment(cache):
    descriptor = os.open(cache, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(descriptor, "r") as handle:
        metadata = os.fstat(handle.fileno())
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_uid != os.getuid() or stat.S_IMODE(metadata.st_mode) != 0o600 or metadata.st_size > LIMIT:
            raise ValueError("unsafe_cache")
        content = handle.read(LIMIT + 1)
    environment = {"HOME": str(Path.home()), "PATH": "/usr/bin:/bin"}
    for line in content.splitlines():
        line = line.strip().removeprefix("export ")
        key, separator, value = line.partition("=")
        if separator and key in KEYS:
            tokens = shlex.split(value)
            if key in environment or len(tokens) != 1 or not tokens[0]:
                raise ValueError("invalid_cache")
            environment[key] = tokens[0]
    if not all(environment.get(key) for key in KEYS):
        raise ValueError("missing_credentials")
    if environment[KEYS[2]] == environment[KEYS[3]]:
        raise ValueError("ambiguous_owner")
    return environment


def valid_number(value, maximum):
    return type(value) in (int, float) and math.isfinite(value) and 0 <= value <= maximum


def summarize(payload, owner, date):
    if not isinstance(payload, dict) or "error" in payload or payload.get("side") != owner:
        return "unavailable", "invalid_response", None
    days = payload.get("days")
    if days == []:
        return "no_data", "no_sleep_data", None
    if not isinstance(days, list) or len(days) != 1 or not isinstance(days[0], dict):
        return "unavailable", "invalid_response", None
    day = days[0]
    if day.get("day") != date:
        return "unavailable", "stale_response", None
    duration = day.get("sleepDuration")
    if not valid_number(duration, 86400) or duration == 0:
        return "unavailable", "incomplete_sleep_data", None
    minutes = round(duration / 60)
    lines = [f"{owner.title()} — {date}", f"Duration: {minutes // 60}h {minutes % 60}m"]
    score = day.get("score")
    if valid_number(score, 100):
        lines.append(f"Sleep score: {score}")
    for key, label in (("remPercent", "REM"), ("deepPercent", "Deep")):
        if valid_number(day.get(key), 1):
            lines.append(f"{label}: {round(day[key] * 100)}%")
    return "ok", "verified", "\n".join(lines)


def collect(owner, date, environment, api_script, runner=subprocess.run):
    with tempfile.TemporaryFile() as output:
        try:
            result = runner(
                ["/usr/bin/python3", str(api_script), "sleep", owner, date],
                env=environment, stdout=output, stderr=subprocess.DEVNULL,
                timeout=60, check=False,
            )
        except subprocess.TimeoutExpired:
            return "unavailable", "timeout", None
        except OSError:
            return "unavailable", "api_unavailable", None
        if result.returncode:
            return "unavailable", "api_failed", None
        output.seek(0)
        raw = output.read(LIMIT + 1)
    if len(raw) > LIMIT:
        return "unavailable", "oversized_response", None
    try:
        return summarize(json.loads(raw), owner, date)
    except (ValueError, UnicodeError):
        return "unavailable", "invalid_response", None


def prepare_root(root):
    root.mkdir(mode=0o700, exist_ok=True)
    metadata = root.lstat()
    if not stat.S_ISDIR(metadata.st_mode) or metadata.st_uid != os.getuid() or stat.S_IMODE(metadata.st_mode) != 0o700:
        raise ValueError("unsafe_snapshot_directory")


def write_snapshot(root, owner, result, now):
    status, reason, text = result
    payload = {"schemaVersion": 1, "owner": owner, "date": now.date().isoformat(), "capturedAt": now.isoformat(), "status": status, "reason": reason}
    if text is not None:
        payload["text"] = text
    descriptor, name = tempfile.mkstemp(prefix=".snapshot-", dir=root)
    try:
        with os.fdopen(descriptor, "w") as handle:
            json.dump(payload, handle)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(name, root / f"{owner}.json")
    finally:
        if os.path.exists(name):
            os.unlink(name)


def main():
    os.umask(0o077)
    root = Path.home() / ".openclaw/8sleep-snapshots"
    try:
        prepare_root(root)
        descriptor = os.open(root / ".lock", os.O_CREAT | os.O_WRONLY | os.O_NOFOLLOW, 0o600)
        with os.fdopen(descriptor, "w") as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                print("sleep_snapshot: already_running")
                return 1
            try:
                environment = load_environment(Path.home() / ".openclaw/.secrets-cache")
            except (OSError, ValueError, UnicodeError):
                environment = None
            failed = False
            for owner in OWNERS:
                now = datetime.now(TIME_ZONE)
                result = collect(owner, now.date().isoformat(), environment, Path.home() / ".openclaw/skills/8sleep/8sleep-api.py") if environment else ("unavailable", "credentials_unavailable", None)
                write_snapshot(root, owner, result, now)
                print(f"sleep_snapshot: {owner} {result[0]} {result[1]}")
                failed = failed or result[0] == "unavailable"
            return int(failed)
    except (OSError, ValueError):
        print("sleep_snapshot: protected_storage_unavailable", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
