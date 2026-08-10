#!/usr/bin/env python3
"""Collect one exact Airthings BLE sample into shared climate history."""

from __future__ import annotations

import json
import math
import os
import stat
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ALIAS = "cabin-living-room-airthings"
SITE = "cabin"
STRUCTURE = "Philly"
ROOM = "Living Room"
HISTORY_ORIGIN = "airthings_ble_sampler_v1"


class SnapshotError(Exception):
    """Operational error whose category is safe to persist and print."""

    def __init__(self, category: str) -> None:
        super().__init__(category)
        self.category = category


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace(
        "+00:00", "Z"
    )


def _finite(value: Any, digits: int = 1) -> float | int | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number):
        return None
    rounded = round(number, digits)
    return int(rounded) if digits == 0 else rounded


def _validated_timestamp(value: Any) -> str:
    if not isinstance(value, str):
        raise SnapshotError("missing_read_timestamp")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise SnapshotError("invalid_read_timestamp") from exc
    if parsed.tzinfo is None:
        raise SnapshotError("invalid_read_timestamp")
    age = (datetime.now(timezone.utc) - parsed.astimezone(timezone.utc)).total_seconds()
    if age < -60 or age > 600:
        raise SnapshotError("stale_reading")
    return parsed.astimezone(timezone.utc).isoformat(timespec="seconds").replace(
        "+00:00", "Z"
    )


def build_history_record(payload: dict[str, Any]) -> dict[str, Any]:
    if payload.get("ok") is not True or not isinstance(payload.get("devices"), list):
        raise SnapshotError("invalid_status")
    matches = [
        item
        for item in payload["devices"]
        if isinstance(item, dict) and item.get("alias") == ALIAS
    ]
    if len(matches) != 1:
        raise SnapshotError("binding_mismatch")
    device = matches[0]
    if device.get("site") != SITE or device.get("room") != ROOM:
        raise SnapshotError("binding_mismatch")
    if device.get("online") is not True:
        raise SnapshotError("device_unavailable")
    timestamp = _validated_timestamp(device.get("read_at"))
    temperature_f = _finite(device.get("temperature_f"))
    temperature_c = _finite(device.get("temperature_c"), 2)
    humidity = _finite(device.get("humidity_percent"))
    co2 = _finite(device.get("co2_ppm"), 0)
    voc = _finite(device.get("voc_ppb"), 0)
    if None in (temperature_f, temperature_c, humidity, co2, voc):
        raise SnapshotError("incomplete_reading")
    air_quality = device.get("air_quality")
    if not isinstance(air_quality, dict):
        raise SnapshotError("incomplete_reading")
    quality = {
        key: air_quality.get(key)
        for key in ("overall", "co2", "voc", "humidity")
    }
    allowed_levels = {"good", "fair", "poor", "unknown"}
    if any(value not in allowed_levels for value in quality.values()):
        raise SnapshotError("incomplete_reading")
    room = {
        "structure": STRUCTURE,
        "room": ROOM,
        "temp_c": temperature_c,
        "temp_f": temperature_f,
        "humidity": humidity,
        "mode": "sensor",
        "hvac": None,
        "eco": "OFF",
        "setpoint_c": None,
        "setpoint_f": None,
        "connectivity": "ONLINE",
        "co2_ppm": co2,
        "voc_ppb": voc,
        "pressure_hpa": _finite(device.get("pressure_hpa")),
        "noise_dba": _finite(device.get("noise_dba")),
        "light_lux": _finite(device.get("light_lux")),
        "battery_percent": _finite(device.get("battery_percent"), 0),
        "air_quality": quality,
        "cached": bool(device.get("cached")),
        "error": None,
        "source": "airthings",
        "history_origin": HISTORY_ORIGIN,
    }
    return {
        "timestamp": timestamp,
        "rooms": [room],
        "history_origin": HISTORY_ORIGIN,
    }


def _safe_status_parent(path: Path) -> None:
    parent = path.parent
    if not parent.exists():
        parent.mkdir(parents=True, mode=0o700)
        os.chmod(parent, 0o700)
    metadata = parent.lstat()
    if (
        stat.S_ISLNK(metadata.st_mode)
        or not stat.S_ISDIR(metadata.st_mode)
        or metadata.st_uid != os.getuid()
    ):
        raise SnapshotError("unsafe_status_directory")


def _load_last_success(path: Path) -> str | None:
    if not path.exists():
        return None
    metadata = path.lstat()
    if (
        stat.S_ISLNK(metadata.st_mode)
        or not stat.S_ISREG(metadata.st_mode)
        or metadata.st_uid != os.getuid()
        or metadata.st_nlink != 1
        or stat.S_IMODE(metadata.st_mode) & 0o077
    ):
        raise SnapshotError("unsafe_status_file")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SnapshotError("invalid_status_file") from exc
    value = payload.get("last_success_at") if isinstance(payload, dict) else None
    return value if isinstance(value, str) else None


def _write_status(path: Path, payload: dict[str, Any]) -> None:
    _safe_status_parent(path)
    if path.exists() and path.is_symlink():
        raise SnapshotError("unsafe_status_file")
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, separators=(",", ":"), sort_keys=True)
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


def _command_path(env_name: str, default: Path) -> Path:
    path = Path(os.environ.get(env_name, str(default))).expanduser()
    if not path.is_file() or path.is_symlink() or not os.access(path, os.X_OK):
        raise SnapshotError(f"{env_name.lower()}_unavailable")
    return path


def collect() -> tuple[str, str]:
    home = Path.home()
    airthings = _command_path(
        "AIRTHINGS_BIN", home / ".openclaw" / "bin" / "airthings"
    )
    appender = _command_path(
        "NEST_HISTORY_APPEND_BIN",
        home / ".openclaw" / "bin" / "nest-history-append",
    )
    history_dir = Path(
        os.environ.get(
            "NEST_HISTORY_DIR", str(home / ".openclaw" / "nest-history")
        )
    ).expanduser()
    try:
        status = subprocess.run(
            [str(airthings), "status", ALIAS, "--refresh", "--json"],
            check=False,
            capture_output=True,
            text=True,
            timeout=50,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise SnapshotError("airthings_read_failed") from exc
    if status.returncode != 0:
        raise SnapshotError("airthings_read_failed")
    try:
        payload = json.loads(status.stdout)
    except json.JSONDecodeError as exc:
        raise SnapshotError("invalid_status") from exc
    if not isinstance(payload, dict):
        raise SnapshotError("invalid_status")
    record = build_history_record(payload)
    try:
        appended = subprocess.run(
            [
                str(appender),
                "--history-dir",
                str(history_dir),
                "--dedupe-source",
                "airthings",
                "--dedupe-structure",
                STRUCTURE,
                "--dedupe-room",
                ROOM,
            ],
            input=json.dumps(record, separators=(",", ":"), sort_keys=True),
            check=False,
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise SnapshotError("history_append_failed") from exc
    if appended.returncode != 0:
        raise SnapshotError("history_append_failed")
    try:
        result = json.loads(appended.stdout)
    except json.JSONDecodeError as exc:
        raise SnapshotError("history_append_failed") from exc
    outcome = result.get("outcome") if isinstance(result, dict) else None
    if (
        not isinstance(result, dict)
        or result.get("ok") is not True
        or outcome not in {"appended", "duplicate"}
    ):
        raise SnapshotError("history_append_failed")
    return outcome, record["timestamp"]


def main() -> int:
    status_path = Path(
        os.environ.get(
            "AIRTHINGS_SNAPSHOT_STATUS_FILE",
            str(Path.home() / ".openclaw" / "airthings" / "snapshot-status.json"),
        )
    ).expanduser()
    checked_at = _utc_now()
    try:
        previous_success = _load_last_success(status_path)
        outcome, sample_timestamp = collect()
        health = {
            "ok": True,
            "checked_at": checked_at,
            "last_success_at": checked_at,
            "outcome": outcome,
            "sample_timestamp": sample_timestamp,
        }
        _write_status(status_path, health)
        print(json.dumps(health, separators=(",", ":"), sort_keys=True))
        return 0
    except SnapshotError as exc:
        health = {
            "ok": False,
            "checked_at": checked_at,
            "last_success_at": locals().get("previous_success"),
            "error": exc.category,
        }
        try:
            _write_status(status_path, health)
        except (OSError, SnapshotError):
            pass
        print(json.dumps(health, separators=(",", ":"), sort_keys=True), file=sys.stderr)
        return 12
    except OSError:
        print('{"error":"status_write_failed","ok":false}', file=sys.stderr)
        return 12


if __name__ == "__main__":
    raise SystemExit(main())
