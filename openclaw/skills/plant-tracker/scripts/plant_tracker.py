#!/usr/bin/env python3
"""Private, bounded household plant records for OpenClaw."""

from __future__ import annotations

import argparse
from contextlib import contextmanager
from datetime import date, datetime, timezone
import fcntl
import json
import os
from pathlib import Path
import re
import stat
import sys
import tempfile
from typing import Any, Iterator


VERSION = 2
LEGACY_VERSION = 1
MAX_DATABASE_BYTES = 2 * 1024 * 1024
MAX_PLANTS = 1_000
MAX_CARE_PER_PLANT = 5_000
MAX_NAME = 120
MAX_SPECIES = 200
MAX_LOCATION = 240
MAX_BED = 160
MAX_NOTES = 2_000
MAX_QUERY = 200
MAX_SEARCH_RESULTS = 100
MAX_CAMERA_VIEWS = 8
CONTROL_PATTERN = re.compile(r"[\x00-\x1f\x7f]")
TIMESTAMP_PATTERN = re.compile(
    r"\A[0-9]{4}-[0-9]{2}-[0-9]{2}T"
    r"[0-9]{2}:[0-9]{2}:[0-9]{2}Z\Z"
)
EXPORT_NAME_PATTERN = re.compile(
    r"\A[A-Za-z0-9][A-Za-z0-9._-]{0,126}\.md\Z"
)
VALID_ACTIONS = frozenset(
    (
        "water",
        "fertilize",
        "prune",
        "harvest",
        "repot",
        "plant",
        "pesticide",
        "inspect",
        "note",
    )
)
CAMERA_VIEWS = (
    "Flower Cam #1",
    "Flower Cam #2",
)
CAMERA_VIEW_SET = frozenset(CAMERA_VIEWS)
UNSET = object()

RUNTIME_DIRECTORY = Path.home() / ".openclaw" / "plant-tracker"
DATABASE_PATH = RUNTIME_DIRECTORY / "plants.json"
LOCK_PATH = RUNTIME_DIRECTORY / ".lock"
EXPORT_DIRECTORY = (
    Path.home()
    / ".openclaw"
    / "workspace"
    / "exports"
    / "plant-tracker"
)


class PublicError(RuntimeError):
    """An error with a fixed message safe for the invoking agent."""


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate key")
        result[key] = value
    return result


def _validate_text(
    value: Any,
    label: str,
    maximum: int,
    *,
    optional: bool = False,
) -> str | None:
    if value is None and optional:
        return None
    if (
        not isinstance(value, str)
        or not value
        or value != value.strip()
        or len(value) > maximum
        or CONTROL_PATTERN.search(value)
    ):
        raise PublicError(f"Plant {label} is invalid")
    try:
        value.encode("utf-8")
    except UnicodeEncodeError as error:
        raise PublicError(f"Plant {label} is invalid") from error
    return value


def _validate_camera_view(value: Any) -> str:
    if not isinstance(value, str) or value not in CAMERA_VIEW_SET:
        raise PublicError("Plant camera view is invalid")
    return value


def _validate_camera_views(value: Any) -> list[str]:
    if (
        not isinstance(value, (list, tuple))
        or len(value) > MAX_CAMERA_VIEWS
        or any(not isinstance(item, str) for item in value)
        or len(set(value)) != len(value)
        or any(item not in CAMERA_VIEW_SET for item in value)
    ):
        raise PublicError("Plant camera view is invalid")
    return [camera for camera in CAMERA_VIEWS if camera in value]


def _validate_filter(
    camera: str | None,
    bed: str | None,
) -> tuple[str | None, str | None]:
    validated_camera = (
        _validate_camera_view(camera)
        if camera is not None
        else None
    )
    validated_bed = _validate_text(
        bed,
        "bed",
        MAX_BED,
        optional=True,
    )
    return validated_camera, validated_bed


def _validate_date(value: Any, *, optional: bool = False) -> str | None:
    if value is None and optional:
        return None
    validated = _validate_text(value, "date", 10)
    try:
        parsed = date.fromisoformat(validated)
    except (TypeError, ValueError) as error:
        raise PublicError("Plant date is invalid") from error
    if parsed.isoformat() != validated:
        raise PublicError("Plant date is invalid")
    return validated


def _validate_timestamp(value: Any) -> str:
    validated = _validate_text(value, "timestamp", 20)
    if not TIMESTAMP_PATTERN.fullmatch(validated):
        raise PublicError("Plant database is invalid")
    try:
        datetime.strptime(validated, "%Y-%m-%dT%H:%M:%SZ")
    except ValueError as error:
        raise PublicError("Plant database is invalid") from error
    return validated


def _timestamp() -> str:
    return (
        datetime.now(timezone.utc)
        .isoformat(timespec="seconds")
        .replace("+00:00", "Z")
    )


def _ensure_private_directory(path: Path, label: str) -> None:
    try:
        if not path.exists():
            path.mkdir(mode=0o700, parents=False, exist_ok=False)
        metadata = path.lstat()
    except OSError as error:
        raise PublicError(f"{label} is unavailable") from error
    if (
        stat.S_ISLNK(metadata.st_mode)
        or not stat.S_ISDIR(metadata.st_mode)
        or metadata.st_uid != os.geteuid()
        or stat.S_IMODE(metadata.st_mode) != 0o700
    ):
        raise PublicError(f"{label} is unavailable")


def _ensure_runtime_directory() -> None:
    parent = RUNTIME_DIRECTORY.parent
    try:
        parent_metadata = parent.lstat()
    except OSError as error:
        raise PublicError("Plant storage is unavailable") from error
    if (
        stat.S_ISLNK(parent_metadata.st_mode)
        or not stat.S_ISDIR(parent_metadata.st_mode)
        or parent_metadata.st_uid != os.geteuid()
    ):
        raise PublicError("Plant storage is unavailable")
    _ensure_private_directory(RUNTIME_DIRECTORY, "Plant storage")


@contextmanager
def _database_lock(*, exclusive: bool) -> Iterator[None]:
    _ensure_runtime_directory()
    flags = os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(LOCK_PATH, flags, 0o600)
        os.fchmod(descriptor, 0o600)
        metadata = os.fstat(descriptor)
        named = LOCK_PATH.lstat()
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_uid != os.geteuid()
            or stat.S_IMODE(metadata.st_mode) != 0o600
            or metadata.st_nlink != 1
            or (metadata.st_dev, metadata.st_ino)
            != (named.st_dev, named.st_ino)
        ):
            raise PublicError("Plant storage is unavailable")
        fcntl.flock(
            descriptor,
            fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH,
        )
        yield
    except PublicError:
        raise
    except OSError as error:
        raise PublicError("Plant storage is unavailable") from error
    finally:
        if "descriptor" in locals():
            try:
                fcntl.flock(descriptor, fcntl.LOCK_UN)
            except OSError:
                pass
            os.close(descriptor)


def _empty_database() -> dict[str, Any]:
    return {"version": VERSION, "plants": []}


def _validate_database(value: Any) -> dict[str, Any]:
    if (
        not isinstance(value, dict)
        or set(value) != {"version", "plants"}
        or type(value.get("version")) is not int
        or value.get("version") != VERSION
        or not isinstance(value.get("plants"), list)
        or len(value["plants"]) > MAX_PLANTS
    ):
        raise PublicError("Plant database is invalid")

    seen: set[str] = set()
    for plant in value["plants"]:
        if (
            not isinstance(plant, dict)
            or set(plant)
            != {
                "name",
                "species",
                "location",
                "bed",
                "cameraViews",
                "planted",
                "notes",
                "createdAt",
                "careHistory",
            }
        ):
            raise PublicError("Plant database is invalid")
        name = _validate_text(plant.get("name"), "name", MAX_NAME)
        key = name.casefold()
        if key in seen:
            raise PublicError("Plant database is invalid")
        seen.add(key)
        _validate_text(
            plant.get("species"),
            "species",
            MAX_SPECIES,
            optional=True,
        )
        _validate_text(
            plant.get("location"),
            "location",
            MAX_LOCATION,
            optional=True,
        )
        _validate_text(
            plant.get("bed"),
            "bed",
            MAX_BED,
            optional=True,
        )
        camera_views = _validate_camera_views(plant.get("cameraViews"))
        if camera_views != plant["cameraViews"]:
            raise PublicError("Plant database is invalid")
        _validate_date(plant.get("planted"), optional=True)
        _validate_text(
            plant.get("notes"),
            "notes",
            MAX_NOTES,
            optional=True,
        )
        _validate_timestamp(plant.get("createdAt"))
        history = plant.get("careHistory")
        if (
            not isinstance(history, list)
            or len(history) > MAX_CARE_PER_PLANT
        ):
            raise PublicError("Plant database is invalid")
        for event in history:
            if (
                not isinstance(event, dict)
                or set(event) != {"action", "recordedAt", "notes"}
                or event.get("action") not in VALID_ACTIONS
            ):
                raise PublicError("Plant database is invalid")
            _validate_timestamp(event.get("recordedAt"))
            _validate_text(
                event.get("notes"),
                "notes",
                MAX_NOTES,
                optional=True,
            )
    return value


def _validate_legacy_database(value: Any) -> dict[str, Any]:
    if (
        not isinstance(value, dict)
        or set(value) != {"version", "plants"}
        or type(value.get("version")) is not int
        or value.get("version") != LEGACY_VERSION
        or not isinstance(value.get("plants"), list)
        or len(value["plants"]) > MAX_PLANTS
    ):
        raise PublicError("Plant database is invalid")
    seen: set[str] = set()
    for plant in value["plants"]:
        if (
            not isinstance(plant, dict)
            or set(plant)
            != {
                "name",
                "species",
                "location",
                "planted",
                "notes",
                "createdAt",
                "careHistory",
            }
        ):
            raise PublicError("Plant database is invalid")
        name = _validate_text(plant.get("name"), "name", MAX_NAME)
        key = name.casefold()
        if key in seen:
            raise PublicError("Plant database is invalid")
        seen.add(key)
        _validate_text(
            plant.get("species"),
            "species",
            MAX_SPECIES,
            optional=True,
        )
        _validate_text(
            plant.get("location"),
            "location",
            MAX_LOCATION,
            optional=True,
        )
        _validate_date(plant.get("planted"), optional=True)
        _validate_text(
            plant.get("notes"),
            "notes",
            MAX_NOTES,
            optional=True,
        )
        _validate_timestamp(plant.get("createdAt"))
        history = plant.get("careHistory")
        if (
            not isinstance(history, list)
            or len(history) > MAX_CARE_PER_PLANT
        ):
            raise PublicError("Plant database is invalid")
        for event in history:
            if (
                not isinstance(event, dict)
                or set(event) != {"action", "recordedAt", "notes"}
                or event.get("action") not in VALID_ACTIONS
            ):
                raise PublicError("Plant database is invalid")
            _validate_timestamp(event.get("recordedAt"))
            _validate_text(
                event.get("notes"),
                "notes",
                MAX_NOTES,
                optional=True,
            )
    return value


def _read_database_value_unlocked() -> Any:
    if not DATABASE_PATH.exists():
        if DATABASE_PATH.is_symlink():
            raise PublicError("Plant database is unavailable")
        return _empty_database()
    try:
        named = DATABASE_PATH.lstat()
        if (
            stat.S_ISLNK(named.st_mode)
            or not stat.S_ISREG(named.st_mode)
            or named.st_uid != os.geteuid()
            or stat.S_IMODE(named.st_mode) != 0o600
            or named.st_nlink != 1
            or not 0 < named.st_size <= MAX_DATABASE_BYTES
        ):
            raise PublicError("Plant database is unavailable")
        descriptor = os.open(
            DATABASE_PATH,
            os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0),
        )
        opened = os.fstat(descriptor)
        if (
            (opened.st_dev, opened.st_ino) != (named.st_dev, named.st_ino)
            or opened.st_size != named.st_size
        ):
            raise PublicError("Plant database is unavailable")
        chunks: list[bytes] = []
        remaining = MAX_DATABASE_BYTES + 1
        while remaining:
            chunk = os.read(descriptor, min(64 * 1024, remaining))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        payload = b"".join(chunks)
        after = os.fstat(descriptor)
        if (
            len(payload) != opened.st_size
            or after.st_size != opened.st_size
            or after.st_mtime_ns != opened.st_mtime_ns
        ):
            raise PublicError("Plant database is unavailable")
    except PublicError:
        raise
    except OSError as error:
        raise PublicError("Plant database is unavailable") from error
    finally:
        if "descriptor" in locals():
            os.close(descriptor)
    try:
        value = json.loads(
            payload.decode("utf-8"),
            object_pairs_hook=_reject_duplicate_keys,
        )
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as error:
        raise PublicError("Plant database is invalid") from error
    return value


def _read_database_unlocked() -> dict[str, Any]:
    return _validate_database(_read_database_value_unlocked())


def _write_database_unlocked(
    database: dict[str, Any],
    *,
    expected_existing_version: int = VERSION,
) -> None:
    _validate_database(database)
    if DATABASE_PATH.exists() or DATABASE_PATH.is_symlink():
        existing = _read_database_value_unlocked()
        if expected_existing_version == VERSION:
            _validate_database(existing)
        elif expected_existing_version == LEGACY_VERSION:
            _validate_legacy_database(existing)
        else:
            raise PublicError("Plant database is invalid")
    payload = (
        json.dumps(
            database,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n"
    ).encode("utf-8")
    if len(payload) > MAX_DATABASE_BYTES:
        raise PublicError("Plant database is too large")
    try:
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=".plants.",
            suffix=".tmp",
            dir=RUNTIME_DIRECTORY,
        )
        temporary = Path(temporary_name)
        os.fchmod(descriptor, 0o600)
        offset = 0
        while offset < len(payload):
            written = os.write(descriptor, payload[offset:])
            if written <= 0:
                raise OSError("short write")
            offset += written
        os.fsync(descriptor)
        os.close(descriptor)
        descriptor = -1
        os.replace(temporary, DATABASE_PATH)
        directory_descriptor = os.open(
            RUNTIME_DIRECTORY,
            os.O_RDONLY | getattr(os, "O_DIRECTORY", 0),
        )
        os.fsync(directory_descriptor)
        os.close(directory_descriptor)
    except OSError as error:
        raise PublicError("Plant database write failed") from error
    finally:
        if "descriptor" in locals() and descriptor >= 0:
            os.close(descriptor)
        if "temporary" in locals() and temporary.exists():
            try:
                temporary.unlink()
            except OSError:
                pass


def _find_plant(database: dict[str, Any], name: str) -> dict[str, Any]:
    key = _validate_text(name, "name", MAX_NAME).casefold()
    matches = [
        plant
        for plant in database["plants"]
        if plant["name"].casefold() == key
    ]
    if len(matches) != 1:
        raise PublicError("Plant was not found")
    return matches[0]


def _plant_matches(
    plant: dict[str, Any],
    *,
    camera: str | None,
    bed: str | None,
) -> bool:
    return (
        (camera is None or camera in plant["cameraViews"])
        and (
            bed is None
            or (
                plant["bed"] is not None
                and plant["bed"].casefold() == bed.casefold()
            )
        )
    )


def _plant_summary(plant: dict[str, Any]) -> dict[str, Any]:
    return {
        "name": plant["name"],
        "species": plant["species"],
        "location": plant["location"],
        "bed": plant["bed"],
        "cameraViews": plant["cameraViews"],
        "planted": plant["planted"],
        "careCount": len(plant["careHistory"]),
        "lastCare": (
            plant["careHistory"][-1]["action"]
            if plant["careHistory"]
            else None
        ),
    }


def migrate_database() -> dict[str, Any]:
    with _database_lock(exclusive=True):
        if not DATABASE_PATH.exists():
            database = _empty_database()
            _write_database_unlocked(database)
            return {
                "migrated": False,
                "fromVersion": VERSION,
                "toVersion": VERSION,
                "count": 0,
            }
        value = _read_database_value_unlocked()
        if isinstance(value, dict) and value.get("version") == VERSION:
            database = _validate_database(value)
            return {
                "migrated": False,
                "fromVersion": VERSION,
                "toVersion": VERSION,
                "count": len(database["plants"]),
            }
        legacy = _validate_legacy_database(value)
        migrated = {
            "version": VERSION,
            "plants": [
                {
                    **plant,
                    "bed": None,
                    "cameraViews": [],
                }
                for plant in legacy["plants"]
            ],
        }
        _write_database_unlocked(
            migrated,
            expected_existing_version=LEGACY_VERSION,
        )
        return {
            "migrated": True,
            "fromVersion": LEGACY_VERSION,
            "toVersion": VERSION,
            "count": len(migrated["plants"]),
        }


def initialize() -> dict[str, Any]:
    with _database_lock(exclusive=True):
        database = _read_database_unlocked()
        if not DATABASE_PATH.exists():
            _write_database_unlocked(database)
        return {"initialized": True, "count": len(database["plants"])}


def list_plants(
    *,
    camera: str | None = None,
    bed: str | None = None,
) -> dict[str, Any]:
    validated_camera, validated_bed = _validate_filter(camera, bed)
    with _database_lock(exclusive=False):
        database = _read_database_unlocked()
    plants = [
        _plant_summary(plant)
        for plant in sorted(
            database["plants"],
            key=lambda item: item["name"].casefold(),
        )
        if _plant_matches(
            plant,
            camera=validated_camera,
            bed=validated_bed,
        )
    ]
    return {
        "camera": validated_camera,
        "bed": validated_bed,
        "count": len(plants),
        "plants": plants,
    }


def show_plant(name: str) -> dict[str, Any]:
    with _database_lock(exclusive=False):
        plant = dict(_find_plant(_read_database_unlocked(), name))
    plant["careHistory"] = plant["careHistory"][-50:]
    return {"plant": plant}


def add_plant(
    name: str,
    *,
    species: str | None,
    location: str | None,
    bed: str | None,
    camera_views: list[str] | None,
    planted: str | None,
    notes: str | None,
) -> dict[str, Any]:
    validated_name = _validate_text(name, "name", MAX_NAME)
    validated_species = _validate_text(
        species,
        "species",
        MAX_SPECIES,
        optional=True,
    )
    validated_location = _validate_text(
        location,
        "location",
        MAX_LOCATION,
        optional=True,
    )
    validated_bed = _validate_text(
        bed,
        "bed",
        MAX_BED,
        optional=True,
    )
    validated_camera_views = _validate_camera_views(camera_views or [])
    validated_planted = _validate_date(planted, optional=True)
    validated_notes = _validate_text(
        notes,
        "notes",
        MAX_NOTES,
        optional=True,
    )
    with _database_lock(exclusive=True):
        database = _read_database_unlocked()
        if len(database["plants"]) >= MAX_PLANTS:
            raise PublicError("Plant database is full")
        if any(
            plant["name"].casefold() == validated_name.casefold()
            for plant in database["plants"]
        ):
            raise PublicError("Plant already exists")
        plant = {
            "name": validated_name,
            "species": validated_species,
            "location": validated_location,
            "bed": validated_bed,
            "cameraViews": validated_camera_views,
            "planted": validated_planted,
            "notes": validated_notes,
            "createdAt": _timestamp(),
            "careHistory": [],
        }
        database["plants"].append(plant)
        _write_database_unlocked(database)
    return {"added": True, "plant": plant}


def update_plant(
    name: str,
    *,
    new_name: Any = UNSET,
    species: Any = UNSET,
    location: Any = UNSET,
    bed: Any = UNSET,
    camera_views: Any = UNSET,
    planted: Any = UNSET,
    notes: Any = UNSET,
) -> dict[str, Any]:
    changes = {
        "name": new_name,
        "species": species,
        "location": location,
        "bed": bed,
        "cameraViews": camera_views,
        "planted": planted,
        "notes": notes,
    }
    if all(value is UNSET for value in changes.values()):
        raise PublicError("Plant update has no changes")
    validated: dict[str, Any] = {}
    if new_name is not UNSET:
        validated["name"] = _validate_text(new_name, "name", MAX_NAME)
    for key, value, label, maximum in (
        ("species", species, "species", MAX_SPECIES),
        ("location", location, "location", MAX_LOCATION),
        ("bed", bed, "bed", MAX_BED),
        ("notes", notes, "notes", MAX_NOTES),
    ):
        if value is not UNSET:
            validated[key] = _validate_text(
                value,
                label,
                maximum,
                optional=True,
            )
    if camera_views is not UNSET:
        validated["cameraViews"] = _validate_camera_views(camera_views)
    if planted is not UNSET:
        validated["planted"] = _validate_date(planted, optional=True)
    with _database_lock(exclusive=True):
        database = _read_database_unlocked()
        plant = _find_plant(database, name)
        if "name" in validated and any(
            candidate is not plant
            and candidate["name"].casefold() == validated["name"].casefold()
            for candidate in database["plants"]
        ):
            raise PublicError("Plant already exists")
        changed_fields = [
            key
            for key, value in validated.items()
            if plant[key] != value
        ]
        for key in changed_fields:
            plant[key] = validated[key]
        if changed_fields:
            _write_database_unlocked(database)
        result = dict(plant)
    return {
        "updated": bool(changed_fields),
        "changedFields": changed_fields,
        "plant": result,
    }


def record_care(
    name: str,
    *,
    action: str,
    notes: str | None,
) -> dict[str, Any]:
    if action not in VALID_ACTIONS:
        raise PublicError("Plant care action is invalid")
    validated_notes = _validate_text(
        notes,
        "notes",
        MAX_NOTES,
        optional=True,
    )
    with _database_lock(exclusive=True):
        database = _read_database_unlocked()
        plant = _find_plant(database, name)
        if len(plant["careHistory"]) >= MAX_CARE_PER_PLANT:
            raise PublicError("Plant care history is full")
        event = {
            "action": action,
            "recordedAt": _timestamp(),
            "notes": validated_notes,
        }
        plant["careHistory"].append(event)
        _write_database_unlocked(database)
    return {"recorded": True, "plant": plant["name"], "care": event}


def record_care_set(
    *,
    camera: str,
    bed: str | None,
    action: str,
    notes: str | None,
    confirm_count: int,
) -> dict[str, Any]:
    validated_camera, validated_bed = _validate_filter(camera, bed)
    if validated_camera is None:
        raise PublicError("Plant camera view is invalid")
    if (
        isinstance(confirm_count, bool)
        or not isinstance(confirm_count, int)
        or not 1 <= confirm_count <= MAX_PLANTS
    ):
        raise PublicError("Plant batch confirmation is invalid")
    if action not in VALID_ACTIONS:
        raise PublicError("Plant care action is invalid")
    validated_notes = _validate_text(
        notes,
        "notes",
        MAX_NOTES,
        optional=True,
    )
    with _database_lock(exclusive=True):
        database = _read_database_unlocked()
        plants = sorted(
            (
                plant
                for plant in database["plants"]
                if _plant_matches(
                    plant,
                    camera=validated_camera,
                    bed=validated_bed,
                )
            ),
            key=lambda item: item["name"].casefold(),
        )
        if len(plants) != confirm_count:
            raise PublicError("Plant batch confirmation did not match")
        if any(
            len(plant["careHistory"]) >= MAX_CARE_PER_PLANT
            for plant in plants
        ):
            raise PublicError("Plant care history is full")
        recorded_at = _timestamp()
        event = {
            "action": action,
            "recordedAt": recorded_at,
            "notes": validated_notes,
        }
        for plant in plants:
            plant["careHistory"].append(dict(event))
        _write_database_unlocked(database)
    return {
        "recorded": True,
        "camera": validated_camera,
        "bed": validated_bed,
        "count": len(plants),
        "plants": [plant["name"] for plant in plants],
        "care": event,
    }


def search_plants(
    query: str,
    *,
    camera: str | None = None,
    bed: str | None = None,
) -> dict[str, Any]:
    validated_query = _validate_text(query, "search query", MAX_QUERY)
    validated_camera, validated_bed = _validate_filter(camera, bed)
    needle = validated_query.casefold()
    with _database_lock(exclusive=False):
        database = _read_database_unlocked()
    results: list[dict[str, Any]] = []
    for plant in database["plants"]:
        haystacks = (
            plant["name"],
            plant["species"] or "",
            plant["location"] or "",
            plant["bed"] or "",
            *plant["cameraViews"],
            plant["notes"] or "",
            *(
                f"{event['action']} {event['notes'] or ''}"
                for event in plant["careHistory"]
            ),
        )
        if (
            _plant_matches(
                plant,
                camera=validated_camera,
                bed=validated_bed,
            )
            and any(needle in value.casefold() for value in haystacks)
        ):
            results.append(_plant_summary(plant))
        if len(results) >= MAX_SEARCH_RESULTS:
            break
    return {
        "query": validated_query,
        "camera": validated_camera,
        "bed": validated_bed,
        "count": len(results),
        "plants": results,
    }


def _markdown_text(value: str) -> str:
    escaped = value.replace("\\", "\\\\")
    for character in ("`", "*", "_", "{", "}", "[", "]", "<", ">", "#"):
        escaped = escaped.replace(character, f"\\{character}")
    return escaped


def _render_plant_markdown(
    plant: dict[str, Any],
    *,
    heading_level: int,
) -> list[str]:
    heading = "#" * heading_level
    lines = [f"{heading} {_markdown_text(plant['name'])}", ""]
    for label, key in (
        ("Species", "species"),
        ("Location", "location"),
        ("Bed", "bed"),
        ("Planted", "planted"),
        ("Notes", "notes"),
    ):
        if plant[key]:
            lines.append(f"- {label}: {_markdown_text(plant[key])}")
    if plant["cameraViews"]:
        lines.append(
            "- Camera views: "
            + ", ".join(
                _markdown_text(camera)
                for camera in plant["cameraViews"]
            )
        )
    if plant["careHistory"]:
        lines.extend(("", f"{heading}# Recent care", ""))
        for event in plant["careHistory"][-20:]:
            note = (
                f" — {_markdown_text(event['notes'])}"
                if event["notes"]
                else ""
            )
            lines.append(
                f"- {event['recordedAt']}: {event['action']}{note}"
            )
    lines.append("")
    return lines


def _render_markdown(
    plants: list[dict[str, Any]],
    *,
    camera: str | None,
    bed: str | None,
) -> str:
    lines = [
        "# Plant Collection",
        "",
        f"Total plants: {len(plants)}",
        "",
    ]
    if camera is not None:
        lines.extend((f"- Camera view: {_markdown_text(camera)}", ""))
    if bed is not None:
        lines.extend((f"- Bed: {_markdown_text(bed)}", ""))
    ordered = sorted(plants, key=lambda item: item["name"].casefold())
    if camera is not None:
        for plant in ordered:
            lines.extend(_render_plant_markdown(plant, heading_level=2))
    else:
        groups = [
            (
                view,
                [plant for plant in ordered if view in plant["cameraViews"]],
            )
            for view in CAMERA_VIEWS
        ]
        groups.append(
            (
                "No camera view",
                [plant for plant in ordered if not plant["cameraViews"]],
            )
        )
        for label, group in groups:
            if not group:
                continue
            lines.extend((f"## {_markdown_text(label)}", ""))
            for plant in group:
                lines.extend(
                    _render_plant_markdown(plant, heading_level=3)
                )
    return "\n".join(lines).rstrip() + "\n"


def export_plants(
    filename: str,
    *,
    overwrite: bool,
    camera: str | None = None,
    bed: str | None = None,
) -> dict[str, Any]:
    if (
        not isinstance(filename, str)
        or not EXPORT_NAME_PATTERN.fullmatch(filename)
    ):
        raise PublicError("Plant export filename is invalid")
    validated_camera, validated_bed = _validate_filter(camera, bed)
    workspace = EXPORT_DIRECTORY.parents[1]
    try:
        workspace_metadata = workspace.lstat()
    except OSError as error:
        raise PublicError("Plant export directory is unavailable") from error
    if (
        stat.S_ISLNK(workspace_metadata.st_mode)
        or not stat.S_ISDIR(workspace_metadata.st_mode)
        or workspace_metadata.st_uid != os.geteuid()
    ):
        raise PublicError("Plant export directory is unavailable")
    exports_parent = EXPORT_DIRECTORY.parent
    if not exports_parent.exists():
        try:
            exports_parent.mkdir(mode=0o700, parents=False, exist_ok=False)
        except OSError as error:
            raise PublicError("Plant export directory is unavailable") from error
    _ensure_private_directory(
        exports_parent,
        "Plant export directory",
    )
    _ensure_private_directory(
        EXPORT_DIRECTORY,
        "Plant export directory",
    )
    output = EXPORT_DIRECTORY / filename
    if output.exists() or output.is_symlink():
        try:
            metadata = output.lstat()
        except OSError as error:
            raise PublicError("Plant export is unavailable") from error
        if (
            not overwrite
            or stat.S_ISLNK(metadata.st_mode)
            or not stat.S_ISREG(metadata.st_mode)
            or metadata.st_uid != os.geteuid()
            or stat.S_IMODE(metadata.st_mode) != 0o600
            or metadata.st_nlink != 1
        ):
            raise PublicError("Plant export already exists")
    with _database_lock(exclusive=False):
        database = _read_database_unlocked()
    plants = [
        plant
        for plant in database["plants"]
        if _plant_matches(
            plant,
            camera=validated_camera,
            bed=validated_bed,
        )
    ]
    payload = _render_markdown(
        plants,
        camera=validated_camera,
        bed=validated_bed,
    ).encode("utf-8")
    try:
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{filename}.",
            suffix=".tmp",
            dir=EXPORT_DIRECTORY,
        )
        temporary = Path(temporary_name)
        os.fchmod(descriptor, 0o600)
        offset = 0
        while offset < len(payload):
            written = os.write(descriptor, payload[offset:])
            if written <= 0:
                raise OSError("short write")
            offset += written
        os.fsync(descriptor)
        os.close(descriptor)
        descriptor = -1
        os.replace(temporary, output)
    except OSError as error:
        raise PublicError("Plant export failed") from error
    finally:
        if "descriptor" in locals() and descriptor >= 0:
            os.close(descriptor)
        if "temporary" in locals() and temporary.exists():
            try:
                temporary.unlink()
            except OSError:
                pass
    return {
        "exported": True,
        "camera": validated_camera,
        "bed": validated_bed,
        "count": len(plants),
        "mediaPath": str(output),
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="plant-tracker",
        exit_on_error=False,
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("init")
    subparsers.add_parser("migrate")

    list_parser = subparsers.add_parser("list")
    list_parser.add_argument("--camera")
    list_parser.add_argument("--bed")

    show_parser = subparsers.add_parser("show")
    show_parser.add_argument("name")

    search_parser = subparsers.add_parser("search")
    search_parser.add_argument("query")
    search_parser.add_argument("--camera")
    search_parser.add_argument("--bed")

    add_parser = subparsers.add_parser("add")
    add_parser.add_argument("name")
    add_parser.add_argument("--species")
    add_parser.add_argument("--location")
    add_parser.add_argument("--bed")
    add_parser.add_argument("--camera", action="append", dest="camera_views")
    add_parser.add_argument("--planted")
    add_parser.add_argument("--notes")

    update_parser = subparsers.add_parser("update")
    update_parser.add_argument("name")
    update_parser.add_argument("--rename")
    for option in ("species", "location", "bed", "planted", "notes"):
        group = update_parser.add_mutually_exclusive_group()
        group.add_argument(f"--{option}")
        group.add_argument(
            f"--clear-{option}",
            action="store_true",
        )
    camera_group = update_parser.add_mutually_exclusive_group()
    camera_group.add_argument(
        "--camera",
        action="append",
        dest="camera_views",
    )
    camera_group.add_argument("--clear-cameras", action="store_true")

    care_parser = subparsers.add_parser("care")
    care_parser.add_argument("name")
    care_parser.add_argument("--action", required=True)
    care_parser.add_argument("--notes")

    care_set_parser = subparsers.add_parser("care-set")
    care_set_parser.add_argument("--camera", required=True)
    care_set_parser.add_argument("--bed")
    care_set_parser.add_argument("--action", required=True)
    care_set_parser.add_argument("--notes")
    care_set_parser.add_argument(
        "--confirm-count",
        required=True,
        type=int,
    )

    export_parser = subparsers.add_parser("export")
    export_parser.add_argument("filename")
    export_parser.add_argument("--camera")
    export_parser.add_argument("--bed")
    export_parser.add_argument("--overwrite", action="store_true")
    return parser


def _update_value(value: Any, clear: bool) -> Any:
    if clear:
        return None
    return value if value is not None else UNSET


def main(argv: list[str] | None = None) -> int:
    try:
        arguments = _parser().parse_args(argv)
        if arguments.command == "init":
            result = initialize()
        elif arguments.command == "migrate":
            result = migrate_database()
        elif arguments.command == "list":
            result = list_plants(
                camera=arguments.camera,
                bed=arguments.bed,
            )
        elif arguments.command == "show":
            result = show_plant(arguments.name)
        elif arguments.command == "search":
            result = search_plants(
                arguments.query,
                camera=arguments.camera,
                bed=arguments.bed,
            )
        elif arguments.command == "add":
            result = add_plant(
                arguments.name,
                species=arguments.species,
                location=arguments.location,
                bed=arguments.bed,
                camera_views=arguments.camera_views,
                planted=arguments.planted,
                notes=arguments.notes,
            )
        elif arguments.command == "update":
            camera_views: Any = UNSET
            if arguments.clear_cameras:
                camera_views = []
            elif arguments.camera_views is not None:
                camera_views = arguments.camera_views
            result = update_plant(
                arguments.name,
                new_name=(
                    arguments.rename
                    if arguments.rename is not None
                    else UNSET
                ),
                species=_update_value(
                    arguments.species,
                    arguments.clear_species,
                ),
                location=_update_value(
                    arguments.location,
                    arguments.clear_location,
                ),
                bed=_update_value(
                    arguments.bed,
                    arguments.clear_bed,
                ),
                camera_views=camera_views,
                planted=_update_value(
                    arguments.planted,
                    arguments.clear_planted,
                ),
                notes=_update_value(
                    arguments.notes,
                    arguments.clear_notes,
                ),
            )
        elif arguments.command == "care":
            result = record_care(
                arguments.name,
                action=arguments.action,
                notes=arguments.notes,
            )
        elif arguments.command == "care-set":
            result = record_care_set(
                camera=arguments.camera,
                bed=arguments.bed,
                action=arguments.action,
                notes=arguments.notes,
                confirm_count=arguments.confirm_count,
            )
        elif arguments.command == "export":
            result = export_plants(
                arguments.filename,
                overwrite=arguments.overwrite,
                camera=arguments.camera,
                bed=arguments.bed,
            )
        else:
            raise PublicError("Plant command is invalid")
        print(
            json.dumps(
                result,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
        )
        return 0
    except argparse.ArgumentError:
        print("Plant command is invalid", file=sys.stderr)
        return 1
    except PublicError as error:
        print(str(error), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
