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


VERSION = 1
MAX_DATABASE_BYTES = 2 * 1024 * 1024
MAX_PLANTS = 1_000
MAX_CARE_PER_PLANT = 5_000
MAX_NAME = 120
MAX_SPECIES = 200
MAX_LOCATION = 240
MAX_NOTES = 2_000
MAX_QUERY = 200
MAX_SEARCH_RESULTS = 100
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


def _read_database_unlocked() -> dict[str, Any]:
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
    return _validate_database(value)


def _write_database_unlocked(database: dict[str, Any]) -> None:
    _validate_database(database)
    if DATABASE_PATH.exists() or DATABASE_PATH.is_symlink():
        _read_database_unlocked()
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


def initialize() -> dict[str, Any]:
    with _database_lock(exclusive=True):
        database = _read_database_unlocked()
        if not DATABASE_PATH.exists():
            _write_database_unlocked(database)
        return {"initialized": True, "count": len(database["plants"])}


def list_plants() -> dict[str, Any]:
    with _database_lock(exclusive=False):
        database = _read_database_unlocked()
    plants = [
        {
            "name": plant["name"],
            "species": plant["species"],
            "location": plant["location"],
            "planted": plant["planted"],
            "careCount": len(plant["careHistory"]),
            "lastCare": (
                plant["careHistory"][-1]["action"]
                if plant["careHistory"]
                else None
            ),
        }
        for plant in sorted(
            database["plants"],
            key=lambda item: item["name"].casefold(),
        )
    ]
    return {"count": len(plants), "plants": plants}


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
            "planted": validated_planted,
            "notes": validated_notes,
            "createdAt": _timestamp(),
            "careHistory": [],
        }
        database["plants"].append(plant)
        _write_database_unlocked(database)
    return {"added": True, "plant": plant}


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


def search_plants(query: str) -> dict[str, Any]:
    validated_query = _validate_text(query, "search query", MAX_QUERY)
    needle = validated_query.casefold()
    with _database_lock(exclusive=False):
        database = _read_database_unlocked()
    results: list[dict[str, Any]] = []
    for plant in database["plants"]:
        haystacks = (
            plant["name"],
            plant["species"] or "",
            plant["location"] or "",
            plant["notes"] or "",
            *(
                f"{event['action']} {event['notes'] or ''}"
                for event in plant["careHistory"]
            ),
        )
        if any(needle in value.casefold() for value in haystacks):
            results.append(
                {
                    "name": plant["name"],
                    "species": plant["species"],
                    "location": plant["location"],
                }
            )
        if len(results) >= MAX_SEARCH_RESULTS:
            break
    return {"query": validated_query, "count": len(results), "plants": results}


def _markdown_text(value: str) -> str:
    escaped = value.replace("\\", "\\\\")
    for character in ("`", "*", "_", "{", "}", "[", "]", "<", ">", "#"):
        escaped = escaped.replace(character, f"\\{character}")
    return escaped


def _render_markdown(database: dict[str, Any]) -> str:
    lines = [
        "# Plant Collection",
        "",
        f"Total plants: {len(database['plants'])}",
        "",
    ]
    for plant in sorted(
        database["plants"],
        key=lambda item: item["name"].casefold(),
    ):
        lines.extend((f"## {_markdown_text(plant['name'])}", ""))
        for label, key in (
            ("Species", "species"),
            ("Location", "location"),
            ("Planted", "planted"),
            ("Notes", "notes"),
        ):
            if plant[key]:
                lines.append(f"- {label}: {_markdown_text(plant[key])}")
        if plant["careHistory"]:
            lines.extend(("", "### Recent care", ""))
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
    return "\n".join(lines).rstrip() + "\n"


def export_plants(filename: str, *, overwrite: bool) -> dict[str, Any]:
    if (
        not isinstance(filename, str)
        or not EXPORT_NAME_PATTERN.fullmatch(filename)
    ):
        raise PublicError("Plant export filename is invalid")
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
    payload = _render_markdown(database).encode("utf-8")
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
        "count": len(database["plants"]),
        "mediaPath": str(output),
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="plant-tracker",
        exit_on_error=False,
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("init")
    subparsers.add_parser("list")

    show_parser = subparsers.add_parser("show")
    show_parser.add_argument("name")

    search_parser = subparsers.add_parser("search")
    search_parser.add_argument("query")

    add_parser = subparsers.add_parser("add")
    add_parser.add_argument("name")
    add_parser.add_argument("--species")
    add_parser.add_argument("--location")
    add_parser.add_argument("--planted")
    add_parser.add_argument("--notes")

    care_parser = subparsers.add_parser("care")
    care_parser.add_argument("name")
    care_parser.add_argument("--action", required=True)
    care_parser.add_argument("--notes")

    export_parser = subparsers.add_parser("export")
    export_parser.add_argument("filename")
    export_parser.add_argument("--overwrite", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    try:
        arguments = _parser().parse_args(argv)
        if arguments.command == "init":
            result = initialize()
        elif arguments.command == "list":
            result = list_plants()
        elif arguments.command == "show":
            result = show_plant(arguments.name)
        elif arguments.command == "search":
            result = search_plants(arguments.query)
        elif arguments.command == "add":
            result = add_plant(
                arguments.name,
                species=arguments.species,
                location=arguments.location,
                planted=arguments.planted,
                notes=arguments.notes,
            )
        elif arguments.command == "care":
            result = record_care(
                arguments.name,
                action=arguments.action,
                notes=arguments.notes,
            )
        elif arguments.command == "export":
            result = export_plants(
                arguments.filename,
                overwrite=arguments.overwrite,
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
