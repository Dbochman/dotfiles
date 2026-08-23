#!/usr/bin/env python3
"""Publish new protected vacancy-action journal records to home-events."""

from __future__ import annotations

import fcntl
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from typing import Any, Callable


SCHEMA_VERSION = 1
MAX_FILE_BYTES = 256 * 1024
MAX_CURSOR_BYTES = 512 * 1024
MAX_RUNS = 512
SITES = ("cabin", "crosstown")
ID_RE = re.compile(r"^(?:cycle|run|attempt)_[0-9a-f]{32}$")
HASH_RE = re.compile(r"^[0-9a-f]{64}$")
OUTCOME_EVENTS = {
    "state_confirmed": "automation.action_state_confirmed",
    "command_accepted": "automation.action_command_accepted",
    "failed": "automation.action_failed",
    "skipped": "automation.action_skipped",
    "outcome_unknown": "automation.action_outcome_unknown",
}


class AdapterError(Exception):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace(
        "+00:00", "Z"
    )


def _private_directory(path: Path, code: str) -> None:
    try:
        metadata = path.lstat()
    except OSError as exc:
        raise AdapterError(code) from exc
    if (
        stat.S_ISLNK(metadata.st_mode)
        or not stat.S_ISDIR(metadata.st_mode)
        or metadata.st_uid != os.geteuid()
        or stat.S_IMODE(metadata.st_mode) != 0o700
    ):
        raise AdapterError(code)


def _read_json(path: Path, maximum: int, code: str) -> dict[str, Any]:
    try:
        metadata = path.lstat()
    except OSError as exc:
        raise AdapterError(code) from exc
    if (
        stat.S_ISLNK(metadata.st_mode)
        or not stat.S_ISREG(metadata.st_mode)
        or metadata.st_uid != os.geteuid()
        or stat.S_IMODE(metadata.st_mode) != 0o600
        or metadata.st_nlink != 1
        or metadata.st_size <= 0
        or metadata.st_size > maximum
    ):
        raise AdapterError(code)
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise AdapterError(code) from exc
    if not isinstance(value, dict):
        raise AdapterError(code)
    return value


def _atomic_json(path: Path, value: dict[str, Any]) -> None:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":")).encode() + b"\n"
    if len(encoded) > MAX_CURSOR_BYTES:
        raise AdapterError("cursor_capacity_exhausted")
    descriptor, name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary = Path(name)
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "wb", closefd=True) as handle:
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        directory = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        temporary.unlink(missing_ok=True)


def _empty_cursor() -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "sites": {site: {"baselined": False, "runs": {}} for site in SITES},
    }


def _load_cursor(path: Path) -> dict[str, Any]:
    if not path.exists() and not path.is_symlink():
        return _empty_cursor()
    value = _read_json(path, MAX_CURSOR_BYTES, "cursor_invalid")
    if set(value) != {"schema_version", "sites"} or value["schema_version"] != 1:
        raise AdapterError("cursor_invalid")
    if not isinstance(value["sites"], dict) or set(value["sites"]) != set(SITES):
        raise AdapterError("cursor_invalid")
    for site in SITES:
        item = value["sites"][site]
        if (
            not isinstance(item, dict)
            or set(item) != {"baselined", "runs"}
            or not isinstance(item["baselined"], bool)
            or not isinstance(item["runs"], dict)
            or len(item["runs"]) > MAX_RUNS
        ):
            raise AdapterError("cursor_invalid")
        for run_id, published in item["runs"].items():
            if (
                not isinstance(run_id, str)
                or ID_RE.fullmatch(run_id) is None
                or not run_id.startswith("run_")
                or not isinstance(published, int)
                or isinstance(published, bool)
                or published < -1
                or published > 34
            ):
                raise AdapterError("cursor_invalid")
    return value


def _validate_run(value: dict[str, Any]) -> dict[str, Any]:
    if set(value) != {
        "schema_version",
        "site",
        "cycle_id",
        "run_id",
        "trigger_state_hash",
        "triggered_at",
        "started_at",
        "completed_at",
        "state",
        "marker_committed",
        "actions",
    }:
        raise AdapterError("run_invalid")
    if (
        value["schema_version"] != 1
        or value["site"] not in SITES
        or not isinstance(value["run_id"], str)
        or ID_RE.fullmatch(value["run_id"]) is None
        or not value["run_id"].startswith("run_")
        or not isinstance(value["cycle_id"], str)
        or ID_RE.fullmatch(value["cycle_id"]) is None
        or not value["cycle_id"].startswith("cycle_")
        or not isinstance(value["trigger_state_hash"], str)
        or HASH_RE.fullmatch(value["trigger_state_hash"]) is None
        or value["state"] not in {"in_progress", "complete", "interrupted"}
        or not isinstance(value["actions"], list)
        or len(value["actions"]) > 32
    ):
        raise AdapterError("run_invalid")
    if value["state"] == "in_progress":
        return value
    if not isinstance(value["completed_at"], str):
        raise AdapterError("run_invalid")
    for action in value["actions"]:
        if (
            not isinstance(action, dict)
            or set(action)
            != {
                "attempt_id",
                "target",
                "action",
                "state",
                "outcome",
                "verification",
                "reason_code",
                "not_before",
                "not_after",
            }
            or action["state"] != "terminal"
            or action["outcome"] not in OUTCOME_EVENTS
            or not isinstance(action["attempt_id"], str)
            or ID_RE.fullmatch(action["attempt_id"]) is None
        ):
            raise AdapterError("run_invalid")
    return value


def _events(run: dict[str, Any], observed_at: str) -> list[dict[str, Any]]:
    common = {
        "cycle_id": run["cycle_id"],
        "run_id": run["run_id"],
        "trigger_state_hash": run["trigger_state_hash"],
    }
    events = [
        {
            "source_event_id": run["run_id"] + ":started",
            "event_type": "automation.vacancy_run_started",
            "site": run["site"],
            "entity_kind": "workflow",
            "entity_alias": "vacancy",
            "occurred_at": run["started_at"],
            "observed_at": observed_at,
            "time_precision": "journal",
            "attributes": {**common, "triggered_at": run["triggered_at"]},
        }
    ]
    counts = {
        "state_confirmed": 0,
        "command_accepted": 0,
        "failed": 0,
        "skipped": 0,
        "outcome_unknown": 0,
    }
    for action in run["actions"]:
        counts[action["outcome"]] += 1
        events.append(
            {
                "source_event_id": action["attempt_id"],
                "event_type": OUTCOME_EVENTS[action["outcome"]],
                "site": run["site"],
                "entity_kind": "automation_target",
                "entity_alias": action["target"],
                "occurred_at": action["not_after"],
                "observed_at": observed_at,
                "time_precision": "journal",
                "attributes": {
                    **common,
                    "workflow": "vacancy",
                    "action": action["action"],
                    "verification": action["verification"],
                    "reason_code": action["reason_code"],
                    "not_before": action["not_before"],
                    "not_after": action["not_after"],
                },
            }
        )
    events.append(
        {
            "source_event_id": run["run_id"] + ":" + run["state"],
            "event_type": (
                "automation.vacancy_run_completed"
                if run["state"] == "complete"
                else "automation.vacancy_run_interrupted"
            ),
            "site": run["site"],
            "entity_kind": "workflow",
            "entity_alias": "vacancy",
            "occurred_at": run["completed_at"],
            "observed_at": observed_at,
            "time_precision": "journal",
            "attributes": {
                **common,
                "confirmed_count": counts["state_confirmed"],
                "accepted_count": counts["command_accepted"],
                "failed_count": counts["failed"],
                "skipped_count": counts["skipped"],
                "unknown_count": counts["outcome_unknown"],
            },
        }
    )
    return events


def _publish(home_eventctl: str, event: dict[str, Any]) -> None:
    try:
        result = subprocess.run(
            [home_eventctl, "enqueue", "--source", "vacancy"],
            input=json.dumps(event, sort_keys=True, separators=(",", ":")),
            text=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise AdapterError("publisher_unavailable") from exc
    if result.returncode != 0:
        raise AdapterError("publisher_failed")


def run_once(clock: Callable[[], str] = utc_now) -> dict[str, Any]:
    enabled = {
        "cabin": os.environ.get("HOME_EVENTS_VACANCY_CABIN_ENABLED", "0"),
        "crosstown": os.environ.get("HOME_EVENTS_VACANCY_CROSSTOWN_ENABLED", "0"),
    }
    if any(value not in {"0", "1"} for value in enabled.values()):
        raise AdapterError("enable_flag_invalid")
    active = [site for site, value in enabled.items() if value == "1"]
    if not active:
        return {"ok": True, "mode": "disabled"}

    root = Path(
        os.environ.get("HOME_EVENTS_ROOT", str(Path.home() / ".openclaw/home-events"))
    ).expanduser()
    journal_root = Path(
        os.environ.get(
            "VACANCY_JOURNAL_ROOT",
            str(Path.home() / ".openclaw/vacancy-actions/journal"),
        )
    ).expanduser()
    home_eventctl = os.environ.get(
        "HOME_EVENTCTL", str(Path.home() / ".openclaw/bin/home-eventctl")
    )
    _private_directory(root, "runtime_unsafe")
    state_dir = root / "state"
    _private_directory(state_dir, "runtime_unsafe")
    _private_directory(journal_root, "journal_unsafe")
    runs_dir = journal_root / "runs"
    _private_directory(runs_dir, "journal_unsafe")
    cursor_path = state_dir / "vacancy-adapter.json"
    lock_path = state_dir / "vacancy-adapter.lock"
    descriptor = os.open(
        lock_path,
        os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0),
        0o600,
    )
    try:
        os.fchmod(descriptor, 0o600)
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_uid != os.geteuid():
            raise AdapterError("lock_unsafe")
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return {"ok": True, "mode": "already_running"}
        cursor = _load_cursor(cursor_path)
        run_paths = sorted(runs_dir.glob("run_*.json"))
        if len(run_paths) > MAX_RUNS:
            raise AdapterError("journal_capacity_invalid")
        runs = [_validate_run(_read_json(path, MAX_FILE_BYTES, "run_invalid")) for path in run_paths]
        baselined = 0
        published = 0
        for site in active:
            site_cursor = cursor["sites"][site]
            site_runs = [run for run in runs if run["site"] == site]
            if not site_cursor["baselined"]:
                for run in site_runs:
                    site_cursor["runs"][run["run_id"]] = -1
                site_cursor["baselined"] = True
                baselined += len(site_runs)
                _atomic_json(cursor_path, cursor)
                continue
            for run in site_runs:
                if run["state"] == "in_progress":
                    continue
                events = _events(run, clock())
                index = site_cursor["runs"].get(run["run_id"], 0)
                if index == -1:
                    continue
                if index > len(events):
                    raise AdapterError("cursor_invalid")
                for event in events[index:]:
                    _publish(home_eventctl, event)
                    index += 1
                    site_cursor["runs"][run["run_id"]] = index
                    _atomic_json(cursor_path, cursor)
                    published += 1
        return {
            "ok": True,
            "mode": "baseline" if baselined and not published else "published",
            "baselined_runs": baselined,
            "event_count": published,
        }
    finally:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
        finally:
            os.close(descriptor)


def main() -> int:
    os.umask(0o077)
    try:
        result = run_once()
    except (AdapterError, OSError) as exc:
        code = exc.code if isinstance(exc, AdapterError) else "internal_error"
        print(json.dumps({"ok": False, "error_code": code}, sort_keys=True))
        return 2
    print(json.dumps(result, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
