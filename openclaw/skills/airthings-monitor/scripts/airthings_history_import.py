#!/usr/bin/env python3
"""Safely import an attended Airthings dashboard CSV into climate history."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import os
import shutil
import stat
import tempfile
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable


IMPORT_CONTRACT = "airthings_csv_v1"
SITE_STRUCTURE = "Philly"
ROOM = "Living Room"
SOURCE = "airthings"
MAX_ROWS = 200_000


class ImportErrorSafe(Exception):
    """Operational error whose message is safe to show publicly."""

    def __init__(self, category: str, exit_code: int = 12) -> None:
        super().__init__(category)
        self.category = category
        self.exit_code = exit_code


def _normalized_header(value: str) -> str:
    return "".join(char for char in value.lower() if char.isalnum())


def _finite(value: str, category: str) -> float:
    try:
        result = float(value.strip())
    except (AttributeError, TypeError, ValueError) as exc:
        raise ImportErrorSafe(category) from exc
    if not math.isfinite(result):
        raise ImportErrorSafe(category)
    return result


def _bounded(value: float, low: float, high: float, category: str) -> float:
    if not low <= value <= high:
        raise ImportErrorSafe(category)
    return value


def _rounded(value: float, digits: int = 1) -> float | int:
    rounded = round(value, digits)
    return int(rounded) if digits == 0 else rounded


def _level(value: float, good_max: float, fair_max: float) -> str:
    if value < good_max:
        return "good"
    if value < fair_max:
        return "fair"
    return "poor"


def _humidity_level(value: float) -> str:
    if 30 <= value < 60:
        return "good"
    if 25 <= value < 70:
        return "fair"
    return "poor"


def _overall_level(*levels: str) -> str:
    rank = {"good": 0, "fair": 1, "poor": 2}
    return max(levels, key=rank.get)


def _parse_timestamp(value: str) -> datetime:
    raw = value.strip()
    if not raw:
        raise ImportErrorSafe("invalid_timestamp")
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ImportErrorSafe("invalid_timestamp") from exc
    # Airthings documents dashboard-export timestamps as UTC. Their current CSV
    # omits an explicit suffix, so naive values are intentionally treated as UTC.
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc).replace(microsecond=0)


def _safe_regular_file(path: Path, category: str) -> None:
    try:
        metadata = path.lstat()
    except FileNotFoundError as exc:
        raise ImportErrorSafe(category) from exc
    if (
        stat.S_ISLNK(metadata.st_mode)
        or not stat.S_ISREG(metadata.st_mode)
        or metadata.st_uid != os.getuid()
    ):
        raise ImportErrorSafe(category)


def _safe_directory(path: Path, create: bool = False) -> None:
    if not path.exists():
        if not create:
            return
        path.mkdir(parents=True, mode=0o700)
        os.chmod(path, 0o700)
        return
    metadata = path.lstat()
    if (
        stat.S_ISLNK(metadata.st_mode)
        or not stat.S_ISDIR(metadata.st_mode)
        or metadata.st_uid != os.getuid()
    ):
        raise ImportErrorSafe("unsafe_history_directory")


def _header_map(fieldnames: Iterable[str] | None) -> dict[str, str]:
    result = {_normalized_header(value): value for value in fieldnames or []}
    required = {"recorded", "co2ppm", "vocppb"}
    if not required.issubset(result):
        raise ImportErrorSafe("unsupported_csv_schema")
    return result


def _optional_value(
    row: dict[str, str], headers: dict[str, str], key: str
) -> str | None:
    header = headers.get(key)
    if not header:
        return None
    value = row.get(header, "").strip()
    return value or None


def _row_to_sample(row: dict[str, str], headers: dict[str, str]) -> dict[str, Any]:
    recorded = _parse_timestamp(row[headers["recorded"]])
    co2 = _bounded(
        _finite(row[headers["co2ppm"]], "invalid_co2"), 0, 100_000, "invalid_co2"
    )
    voc = _bounded(
        _finite(row[headers["vocppb"]], "invalid_voc"), 0, 100_000, "invalid_voc"
    )

    humidity_raw = _optional_value(row, headers, "humidity")
    temp_f_raw = _optional_value(row, headers, "tempf")
    pressure_inhg_raw = _optional_value(row, headers, "pressureinhg")
    noise_raw = _optional_value(row, headers, "soundleveladbspl")
    light_raw = _optional_value(row, headers, "luxlux")

    humidity = (
        _bounded(_finite(humidity_raw, "invalid_humidity"), 0, 100, "invalid_humidity")
        if humidity_raw is not None
        else None
    )
    temp_f = (
        _bounded(_finite(temp_f_raw, "invalid_temperature"), -100, 212, "invalid_temperature")
        if temp_f_raw is not None
        else None
    )
    pressure_hpa = (
        _bounded(
            _finite(pressure_inhg_raw, "invalid_pressure") * 33.8638866667,
            300,
            1200,
            "invalid_pressure",
        )
        if pressure_inhg_raw is not None
        else None
    )
    noise = (
        _bounded(_finite(noise_raw, "invalid_noise"), 0, 180, "invalid_noise")
        if noise_raw is not None
        else None
    )
    light = (
        _bounded(_finite(light_raw, "invalid_light"), 0, 500_000, "invalid_light")
        if light_raw is not None
        else None
    )

    co2_level = _level(co2, 800, 1000)
    voc_level = _level(voc, 250, 2000)
    humidity_level = _humidity_level(humidity) if humidity is not None else "good"
    room = {
        "structure": SITE_STRUCTURE,
        "room": ROOM,
        "temp_c": _rounded((temp_f - 32) * 5 / 9, 2) if temp_f is not None else None,
        "temp_f": _rounded(temp_f, 1) if temp_f is not None else None,
        "humidity": _rounded(humidity, 1) if humidity is not None else None,
        "mode": "sensor",
        "hvac": None,
        "eco": "OFF",
        "setpoint_c": None,
        "setpoint_f": None,
        "connectivity": "ONLINE",
        "co2_ppm": _rounded(co2, 0),
        "voc_ppb": _rounded(voc, 0),
        "pressure_hpa": _rounded(pressure_hpa, 1) if pressure_hpa is not None else None,
        "noise_dba": _rounded(noise, 1) if noise is not None else None,
        "light_lux": _rounded(light, 1) if light is not None else None,
        "battery_percent": None,
        "air_quality": {
            "overall": _overall_level(co2_level, voc_level, humidity_level),
            "co2": co2_level,
            "voc": voc_level,
            "humidity": _humidity_level(humidity) if humidity is not None else "unknown",
        },
        "cached": False,
        "error": None,
        "source": SOURCE,
        "history_origin": IMPORT_CONTRACT,
    }
    timestamp = recorded.isoformat(timespec="seconds").replace("+00:00", "Z")
    return {
        "timestamp": timestamp,
        "rooms": [room],
        "history_origin": IMPORT_CONTRACT,
    }


def load_export(path: Path) -> list[dict[str, Any]]:
    _safe_regular_file(path, "unsafe_csv")
    try:
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            prefix = handle.read(4096)
            handle.seek(0)
            try:
                dialect = csv.Sniffer().sniff(prefix, delimiters=";,\t,")
            except csv.Error as exc:
                raise ImportErrorSafe("unsupported_csv_schema") from exc
            reader = csv.DictReader(handle, dialect=dialect)
            headers = _header_map(reader.fieldnames)
            samples: dict[str, dict[str, Any]] = {}
            for index, row in enumerate(reader, start=1):
                if index > MAX_ROWS:
                    raise ImportErrorSafe("csv_too_large")
                if not any((value or "").strip() for value in row.values()):
                    continue
                sample = _row_to_sample(row, headers)
                timestamp = sample["timestamp"]
                previous = samples.get(timestamp)
                if previous is not None and previous != sample:
                    raise ImportErrorSafe("conflicting_duplicate_timestamp")
                samples[timestamp] = sample
    except UnicodeError as exc:
        raise ImportErrorSafe("invalid_csv_encoding") from exc
    except OSError as exc:
        raise ImportErrorSafe("csv_read_failed") from exc
    if not samples:
        raise ImportErrorSafe("empty_csv")
    return [samples[key] for key in sorted(samples)]


def _load_history_file(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    _safe_regular_file(path, "unsafe_history_file")
    records: list[dict[str, Any]] = []
    try:
        with path.open("r", encoding="utf-8") as handle:
            for line in handle:
                if not line.strip():
                    continue
                value = json.loads(line)
                if not isinstance(value, dict) or not isinstance(value.get("timestamp"), str):
                    raise ImportErrorSafe("invalid_history_file")
                records.append(value)
    except json.JSONDecodeError as exc:
        raise ImportErrorSafe("invalid_history_file") from exc
    except OSError as exc:
        raise ImportErrorSafe("history_read_failed") from exc
    return records


def _existing_keys(records: Iterable[dict[str, Any]]) -> set[str]:
    result: set[str] = set()
    for record in records:
        for room in record.get("rooms", []):
            if (
                isinstance(room, dict)
                and room.get("source") == SOURCE
                and room.get("structure") == SITE_STRUCTURE
                and room.get("room") == ROOM
            ):
                result.add(record["timestamp"])
                break
    return result


def prepare_import(
    samples: list[dict[str, Any]], history_dir: Path
) -> tuple[dict[str, list[dict[str, Any]]], dict[str, int]]:
    _safe_directory(history_dir)
    by_day: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for sample in samples:
        by_day[sample["timestamp"][:10]].append(sample)

    merged: dict[str, list[dict[str, Any]]] = {}
    duplicates = 0
    added = 0
    for day, day_samples in sorted(by_day.items()):
        path = history_dir / f"{day}.jsonl"
        existing = _load_history_file(path)
        existing_keys = _existing_keys(existing)
        additions = [item for item in day_samples if item["timestamp"] not in existing_keys]
        duplicates += len(day_samples) - len(additions)
        added += len(additions)
        if additions:
            merged[day] = sorted(existing + additions, key=lambda item: item["timestamp"])
    return merged, {
        "samples": len(samples),
        "added": added,
        "duplicates": duplicates,
        "files": len(merged),
    }


def _atomic_write_jsonl(path: Path, records: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            for record in records:
                json.dump(record, handle, separators=(",", ":"), sort_keys=True)
                handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        os.chmod(path, 0o600)
    except Exception:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
        raise


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def apply_import(
    merged: dict[str, list[dict[str, Any]]],
    history_dir: Path,
    backup_root: Path,
    source_path: Path,
    summary: dict[str, Any],
) -> Path | None:
    if not merged:
        return None
    if os.environ.get("AIRTHINGS_ALLOW_HISTORY_IMPORT") != "1":
        raise ImportErrorSafe("import_not_authorized", 13)
    _safe_directory(history_dir, create=True)
    _safe_directory(backup_root, create=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    run_dir = backup_root / f"{stamp}-{os.getpid()}"
    run_dir.mkdir(mode=0o700)
    os.chmod(run_dir, 0o700)
    existing_days: set[str] = set()
    try:
        for day in merged:
            target = history_dir / f"{day}.jsonl"
            if target.exists():
                _safe_regular_file(target, "unsafe_history_file")
                backup = run_dir / target.name
                shutil.copy2(target, backup, follow_symlinks=False)
                os.chmod(backup, 0o600)
                existing_days.add(day)
        manifest = {
            "contract": IMPORT_CONTRACT,
            "created_at": datetime.now(timezone.utc)
            .isoformat(timespec="seconds")
            .replace("+00:00", "Z"),
            "source_sha256": _sha256(source_path),
            "range": [summary["first_timestamp"], summary["last_timestamp"]],
            "samples": summary["samples"],
            "files": sorted(merged),
        }
        manifest_path = run_dir / "manifest.json"
        manifest_path.write_text(
            json.dumps(manifest, separators=(",", ":"), sort_keys=True) + "\n",
            encoding="utf-8",
        )
        os.chmod(manifest_path, 0o600)

        written: list[str] = []
        try:
            for day, records in merged.items():
                _atomic_write_jsonl(history_dir / f"{day}.jsonl", records)
                written.append(day)
        except Exception:
            for day in written:
                target = history_dir / f"{day}.jsonl"
                backup = run_dir / target.name
                if day in existing_days:
                    _atomic_write_jsonl(target, _load_history_file(backup))
                elif target.exists():
                    target.unlink()
            raise
    except ImportErrorSafe:
        raise
    except OSError as exc:
        raise ImportErrorSafe("history_write_failed") from exc
    return run_dir


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="airthings-history-import")
    parser.add_argument("csv_file", type=Path)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--json", action="store_true")
    parser.add_argument(
        "--history-dir",
        type=Path,
        default=Path.home() / ".openclaw" / "nest-history",
        help=argparse.SUPPRESS,
    )
    parser.add_argument(
        "--backup-root",
        type=Path,
        default=Path.home() / ".openclaw" / "airthings" / "backfill-backups",
        help=argparse.SUPPRESS,
    )
    return parser


def main() -> int:
    args = build_parser().parse_args()
    try:
        samples = load_export(args.csv_file.expanduser())
        merged, counts = prepare_import(samples, args.history_dir.expanduser())
        summary: dict[str, Any] = {
            "ok": True,
            "mode": "apply" if args.apply else "dry_run",
            **counts,
            "first_timestamp": samples[0]["timestamp"],
            "last_timestamp": samples[-1]["timestamp"],
        }
        if args.apply:
            backup = apply_import(
                merged,
                args.history_dir.expanduser(),
                args.backup_root.expanduser(),
                args.csv_file.expanduser(),
                summary,
            )
            summary["backup_created"] = backup is not None
        if args.json:
            print(json.dumps(summary, separators=(",", ":"), sort_keys=True))
        else:
            action = "Would add" if not args.apply else "Added"
            print(
                f"{action} {summary['added']} Airthings samples across "
                f"{summary['files']} files; {summary['duplicates']} duplicates skipped."
            )
        return 0
    except ImportErrorSafe as exc:
        print(
            json.dumps({"error": exc.category, "ok": False}, separators=(",", ":"))
        )
        return exc.exit_code


if __name__ == "__main__":
    raise SystemExit(main())
