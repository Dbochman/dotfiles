#!/usr/bin/env python3
"""Bridge normalized Nest camera events into the local home-events bus.

The bridge is deliberately one-way and disabled by default. It reads only the
privacy-safe Nest listener ledger, silently baselines existing rows the first
time it is enabled, and publishes later camera detections through
``home-eventctl``. No image, provider resource, or raw event identifier crosses
this boundary.
"""

from __future__ import annotations

import contextlib
import fcntl
import json
import os
from pathlib import Path
import re
import sqlite3
import stat
import subprocess
import sys
import tempfile
from typing import Any
from urllib.parse import quote


VERSION = 1
NEST_SCHEMA_VERSION = 2
MAX_BATCH_SIZE = 100
DEDUPE_KEY_RE = re.compile(r"^[0-9a-f]{64}$")

# Values on the left are the listener's protected policy aliases and sites.
# Values on the right are the only aliases and sites allowed onto the bus.
CAMERA_BINDINGS = {
    ("Kitchen", "Cabin"): ("kitchen", "cabin"),
    ("Living Room", "Crosstown"): ("living_room", "crosstown"),
    ("Living Room Wired", "Crosstown"): (
        "living_room_wired",
        "crosstown",
    ),
}
EVENT_BINDINGS = {
    "person": ("camera.person_detected", "person"),
    "motion": ("camera.motion_detected", "motion"),
}


class BridgeError(Exception):
    """Expected fail-closed error with a safe public code."""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


def _private_directory(path: Path) -> None:
    try:
        metadata = path.lstat()
    except OSError as exc:
        raise BridgeError("state_directory_unavailable") from exc
    if (
        path.is_symlink()
        or not stat.S_ISDIR(metadata.st_mode)
        or metadata.st_uid != os.geteuid()
        or stat.S_IMODE(metadata.st_mode) & 0o077
    ):
        raise BridgeError("unsafe_state_directory")


def _private_regular_file(path: Path, code: str) -> os.stat_result:
    try:
        metadata = path.lstat()
    except OSError as exc:
        raise BridgeError(code) from exc
    if (
        path.is_symlink()
        or not stat.S_ISREG(metadata.st_mode)
        or metadata.st_uid != os.geteuid()
        or stat.S_IMODE(metadata.st_mode) & 0o077
    ):
        raise BridgeError(code)
    return metadata


def _database_identity(path: Path) -> tuple[int, int]:
    if not path.is_absolute():
        raise BridgeError("database_path_not_absolute")
    _private_directory(path.parent)
    metadata = _private_regular_file(path, "unsafe_database")
    return metadata.st_dev, metadata.st_ino


def _fsync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _atomic_json(path: Path, value: dict[str, Any]) -> None:
    _private_directory(path.parent)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    temporary = Path(temporary_name)
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(value, handle, sort_keys=True, separators=(",", ":"))
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        os.chmod(path, 0o600, follow_symlinks=False)
        _fsync_directory(path.parent)
    finally:
        temporary.unlink(missing_ok=True)


def _cursor_value(identity: tuple[int, int], last_outbox_id: int) -> dict[str, Any]:
    return {
        "version": VERSION,
        "database_device": identity[0],
        "database_inode": identity[1],
        "last_outbox_id": last_outbox_id,
    }


def _load_cursor(path: Path) -> dict[str, Any] | None:
    if not path.exists() and not path.is_symlink():
        return None
    metadata = _private_regular_file(path, "unsafe_cursor")
    if metadata.st_size <= 0 or metadata.st_size > 4096:
        raise BridgeError("invalid_cursor")
    try:
        with path.open("r", encoding="utf-8") as handle:
            value = json.load(handle)
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise BridgeError("invalid_cursor") from exc
    if not isinstance(value, dict) or set(value) != {
        "version",
        "database_device",
        "database_inode",
        "last_outbox_id",
    }:
        raise BridgeError("invalid_cursor")
    if value.get("version") != VERSION:
        raise BridgeError("cursor_version")
    for key in ("database_device", "database_inode", "last_outbox_id"):
        item = value.get(key)
        if not isinstance(item, int) or isinstance(item, bool) or item < 0:
            raise BridgeError("invalid_cursor")
    if value["database_inode"] == 0:
        raise BridgeError("invalid_cursor")
    return value


def _open_database(path: Path) -> sqlite3.Connection:
    uri = f"file:{quote(str(path), safe='/')}?mode=ro"
    try:
        connection = sqlite3.connect(uri, uri=True, timeout=10)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA query_only = ON")
        connection.execute("PRAGMA busy_timeout = 10000")
    except sqlite3.Error as exc:
        raise BridgeError("database_unavailable") from exc
    return connection


def _check_schema(connection: sqlite3.Connection) -> None:
    try:
        versions = connection.execute(
            "SELECT version FROM schema_meta"
        ).fetchall()
    except sqlite3.Error as exc:
        raise BridgeError("database_schema") from exc
    if len(versions) != 1 or versions[0]["version"] != NEST_SCHEMA_VERSION:
        raise BridgeError("database_schema")


def _maximum_outbox_id(connection: sqlite3.Connection) -> int:
    try:
        row = connection.execute(
            """
            SELECT COALESCE(MAX(id), 0) AS maximum_row,
                   COALESCE(
                       (SELECT seq FROM sqlite_sequence WHERE name = 'outbox'),
                       0
                   ) AS sequence
            FROM outbox
            """
        ).fetchone()
    except sqlite3.Error as exc:
        raise BridgeError("database_schema") from exc
    if row is None:
        raise BridgeError("database_schema")
    values = (row["maximum_row"], row["sequence"])
    if any(
        not isinstance(value, int) or isinstance(value, bool) or value < 0
        for value in values
    ):
        raise BridgeError("database_schema")
    return max(values)


def _future_rows(
    connection: sqlite3.Connection, last_outbox_id: int
) -> list[sqlite3.Row]:
    try:
        return connection.execute(
            """
            SELECT outbox.id,
                   outbox.alias AS outbox_alias,
                   outbox.site AS outbox_site,
                   outbox.event_type AS outbox_event_type,
                   outbox.event_at,
                   outbox.created_at,
                   event_records.alias AS record_alias,
                   event_records.site AS record_site,
                   event_records.event_type AS record_event_type,
                   event_records.first_occurred_at,
                   event_records.dedupe_key
            FROM outbox
            JOIN event_records
              ON event_records.id = outbox.event_record_id
            WHERE outbox.id > ?
            ORDER BY outbox.id ASC
            LIMIT ?
            """,
            (last_outbox_id, MAX_BATCH_SIZE),
        ).fetchall()
    except sqlite3.Error as exc:
        raise BridgeError("database_schema") from exc


def _event_from_row(row: sqlite3.Row, previous_id: int) -> dict[str, Any]:
    row_id = row["id"]
    if (
        not isinstance(row_id, int)
        or isinstance(row_id, bool)
        or row_id <= previous_id
    ):
        raise BridgeError("invalid_outbox_row")
    if (
        row["outbox_alias"] != row["record_alias"]
        or row["outbox_site"] != row["record_site"]
        or row["outbox_event_type"] != row["record_event_type"]
        or row["event_at"] != row["first_occurred_at"]
    ):
        raise BridgeError("inconsistent_outbox_row")

    camera = CAMERA_BINDINGS.get((row["outbox_alias"], row["outbox_site"]))
    event = EVENT_BINDINGS.get(row["outbox_event_type"])
    if camera is None or event is None:
        raise BridgeError("unbound_outbox_row")
    dedupe_key = row["dedupe_key"]
    if not isinstance(dedupe_key, str) or not DEDUPE_KEY_RE.fullmatch(dedupe_key):
        raise BridgeError("invalid_dedupe_key")
    event_at = row["event_at"]
    created_at = row["created_at"]
    if not isinstance(event_at, str) or not isinstance(created_at, str):
        raise BridgeError("invalid_outbox_time")

    entity_alias, site = camera
    event_type, classification = event
    return {
        "source_event_id": dedupe_key,
        "event_type": event_type,
        "site": site,
        "entity_kind": "camera",
        "entity_alias": entity_alias,
        "occurred_at": event_at,
        "observed_at": created_at,
        "time_precision": "source",
        "attributes": {"classification": classification},
    }


def _publish(home_eventctl: str, event: dict[str, Any]) -> None:
    try:
        result = subprocess.run(
            [home_eventctl, "enqueue", "--source", "nest"],
            input=json.dumps(event, sort_keys=True, separators=(",", ":")),
            text=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise BridgeError("publisher_unavailable") from exc
    if result.returncode != 0:
        raise BridgeError("publisher_failed")


def _acquire_lock(path: Path) -> int | None:
    try:
        descriptor = os.open(
            path,
            os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0),
            0o600,
        )
    except OSError as exc:
        raise BridgeError("unsafe_lock_file") from exc
    metadata = os.fstat(descriptor)
    if (
        not stat.S_ISREG(metadata.st_mode)
        or metadata.st_uid != os.geteuid()
        or stat.S_IMODE(metadata.st_mode) & 0o077
    ):
        os.close(descriptor)
        raise BridgeError("unsafe_lock_file")
    os.fchmod(descriptor, 0o600)
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        os.close(descriptor)
        return None
    return descriptor


def run_once() -> dict[str, Any]:
    enabled = os.environ.get("HOME_EVENTS_NEST_ENABLED", "0")
    if enabled == "0":
        return {"ok": True, "mode": "disabled"}
    if enabled != "1":
        raise BridgeError("invalid_enable_flag")

    home_events_root = Path(
        os.environ.get(
            "HOME_EVENTS_ROOT", str(Path.home() / ".openclaw/home-events")
        )
    ).expanduser()
    database_path = Path(
        os.environ.get(
            "NEST_EVENT_DATABASE",
            str(Path.home() / ".openclaw/nest-events/state/events.sqlite3"),
        )
    ).expanduser()
    home_eventctl = os.environ.get(
        "HOME_EVENTCTL", str(Path.home() / ".openclaw/bin/home-eventctl")
    )
    if not home_events_root.is_absolute():
        raise BridgeError("state_path_not_absolute")
    _private_directory(home_events_root)
    state_directory = home_events_root / "state"
    _private_directory(state_directory)
    cursor_path = state_directory / "nest-bridge.json"
    lock_path = state_directory / "nest-bridge.lock"

    lock_descriptor = _acquire_lock(lock_path)
    if lock_descriptor is None:
        return {"ok": True, "mode": "already_running"}
    try:
        identity = _database_identity(database_path)
        cursor = _load_cursor(cursor_path)
        with contextlib.closing(_open_database(database_path)) as connection:
            _check_schema(connection)
            maximum = _maximum_outbox_id(connection)
            if _database_identity(database_path) != identity:
                raise BridgeError("database_changed")

            if cursor is None:
                _atomic_json(cursor_path, _cursor_value(identity, maximum))
                return {
                    "ok": True,
                    "mode": "baseline",
                    "event_count": 0,
                    "last_outbox_id": maximum,
                }

            cursor_identity = (
                cursor["database_device"],
                cursor["database_inode"],
            )
            if cursor_identity != identity:
                raise BridgeError("database_replaced")
            if cursor["last_outbox_id"] > maximum:
                raise BridgeError("database_rewound")

            last_outbox_id = cursor["last_outbox_id"]
            rows = _future_rows(connection, last_outbox_id)
            if _database_identity(database_path) != identity:
                raise BridgeError("database_changed")
            published = 0
            for row in rows:
                event = _event_from_row(row, last_outbox_id)
                _publish(home_eventctl, event)
                last_outbox_id = row["id"]
                _atomic_json(
                    cursor_path, _cursor_value(identity, last_outbox_id)
                )
                published += 1
        return {
            "ok": True,
            "mode": "published",
            "event_count": published,
            "last_outbox_id": last_outbox_id,
        }
    finally:
        try:
            fcntl.flock(lock_descriptor, fcntl.LOCK_UN)
        finally:
            os.close(lock_descriptor)


def main() -> int:
    os.umask(0o077)
    try:
        result = run_once()
    except BridgeError as exc:
        result = {"ok": False, "error_code": exc.code}
        status = 2
    except (OSError, sqlite3.Error):
        result = {"ok": False, "error_code": "internal_error"}
        status = 1
    else:
        status = 0
    print(json.dumps(result, sort_keys=True, separators=(",", ":")))
    return status


if __name__ == "__main__":
    raise SystemExit(main())
