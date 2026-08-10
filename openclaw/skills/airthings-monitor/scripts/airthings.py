#!/usr/bin/env python3
"""Read an exactly enrolled Airthings Wave Enhance over local BLE."""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import copy
import fcntl
import json
import logging
import math
import os
import stat
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

SCHEMA_VERSION = 1
DEFAULT_ALIAS = "cabin-living-room-airthings"
DEFAULT_CACHE_TTL_SECONDS = 300
DEFAULT_SCAN_SECONDS = 15.0
AIRTHINGS_MANUFACTURER_ID = 820
SUPPORTED_MODEL = "Wave Enhance"
SITES = {"cabin", "crosstown"}


class AirthingsError(Exception):
    """Safe operational failure with a stable public category."""

    def __init__(self, category: str, exit_code: int = 10) -> None:
        super().__init__(category)
        self.category = category
        self.exit_code = exit_code


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace(
        "+00:00", "Z"
    )


def _finite(value: Any, digits: int = 1) -> float | int | None:
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number):
        return None
    rounded = round(number, digits)
    return int(rounded) if digits == 0 else rounded


def _level(value: float | int | None, good_max: float, fair_max: float) -> str:
    if value is None:
        return "unknown"
    if value < good_max:
        return "good"
    if value < fair_max:
        return "fair"
    return "poor"


def _humidity_level(value: float | int | None) -> str:
    if value is None:
        return "unknown"
    if 30 <= value < 60:
        return "good"
    if 25 <= value < 70:
        return "fair"
    return "poor"


def _overall_level(*levels: str) -> str:
    rank = {"unknown": -1, "good": 0, "fair": 1, "poor": 2}
    known = [value for value in levels if value != "unknown"]
    return max(known, key=rank.get) if known else "unknown"


def normalize_reading(binding: dict[str, Any], device: Any) -> dict[str, Any]:
    """Convert library data to the stable, privacy-safe CLI schema."""
    sensors = device.sensors if isinstance(getattr(device, "sensors", None), dict) else {}
    temp_c = _finite(sensors.get("temperature"), 2)
    humidity = _finite(sensors.get("humidity"), 1)
    co2 = _finite(sensors.get("co2"), 0)
    voc = _finite(sensors.get("voc"), 0)
    pressure = _finite(sensors.get("pressure"), 1)
    noise = _finite(sensors.get("noise"), 1)
    light = _finite(sensors.get("lux"), 1)
    battery = _finite(sensors.get("battery"), 0)
    co2_level = _level(co2, 800, 1000)
    voc_level = _level(voc, 250, 2000)
    humidity_level = _humidity_level(humidity)
    return {
        "alias": binding["alias"],
        "site": binding["site"],
        "room": binding["room"],
        "model": SUPPORTED_MODEL,
        "online": True,
        "cached": False,
        "read_at": _utc_now(),
        "temperature_c": temp_c,
        "temperature_f": (
            round(float(temp_c) * 9 / 5 + 32, 1) if temp_c is not None else None
        ),
        "humidity_percent": humidity,
        "co2_ppm": co2,
        "voc_ppb": voc,
        "pressure_hpa": pressure,
        "noise_dba": noise,
        "light_lux": light,
        "battery_percent": battery,
        "air_quality": {
            "overall": _overall_level(co2_level, voc_level, humidity_level),
            "co2": co2_level,
            "voc": voc_level,
            "humidity": humidity_level,
        },
    }


def offline_reading(binding: dict[str, Any], category: str) -> dict[str, Any]:
    return {
        "alias": binding["alias"],
        "site": binding["site"],
        "room": binding["room"],
        "model": SUPPORTED_MODEL,
        "online": False,
        "cached": False,
        "read_at": _utc_now(),
        "error": category,
    }


def _private_file(path: Path) -> None:
    try:
        metadata = path.lstat()
    except FileNotFoundError as exc:
        raise AirthingsError("not_configured", 12) from exc
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
        raise AirthingsError("unsafe_config", 12)
    if metadata.st_uid != os.getuid() or stat.S_IMODE(metadata.st_mode) != 0o600:
        raise AirthingsError("unsafe_config", 12)


def _private_dir(path: Path) -> None:
    if path.exists():
        metadata = path.lstat()
        if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode):
            raise AirthingsError("unsafe_state_directory", 12)
        if metadata.st_uid != os.getuid():
            raise AirthingsError("unsafe_state_directory", 12)
        os.chmod(path, 0o700)
        return
    path.mkdir(parents=True, mode=0o700)
    os.chmod(path, 0o700)


def _atomic_json(path: Path, payload: dict[str, Any]) -> None:
    _private_dir(path.parent)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, separators=(",", ":"), sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        os.chmod(path, 0o600)
    except Exception:
        with contextlib.suppress(FileNotFoundError):
            os.unlink(temporary)
        raise


def _validate_text(value: Any, category: str, limit: int = 80) -> str:
    if not isinstance(value, str):
        raise AirthingsError(category, 12)
    cleaned = value.strip()
    if not cleaned or len(cleaned) > limit or any(ord(char) < 32 for char in cleaned):
        raise AirthingsError(category, 12)
    return cleaned


def validate_binding(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise AirthingsError("invalid_config", 12)
    binding = {
        "alias": _validate_text(raw.get("alias"), "invalid_alias"),
        "site": _validate_text(raw.get("site"), "invalid_site").lower(),
        "room": _validate_text(raw.get("room"), "invalid_room"),
        "model": _validate_text(raw.get("model"), "invalid_model"),
        "address": _validate_text(raw.get("address"), "invalid_address", 128),
    }
    if binding["site"] not in SITES or binding["model"] != SUPPORTED_MODEL:
        raise AirthingsError("invalid_config", 12)
    identifier = raw.get("identifier")
    if identifier is not None:
        binding["identifier"] = _validate_text(identifier, "invalid_identifier", 128)
    return binding


class AirthingsMonitor:
    def __init__(
        self,
        config_file: Path | None = None,
        state_dir: Path | None = None,
        cache_ttl_seconds: int = DEFAULT_CACHE_TTL_SECONDS,
    ) -> None:
        home = Path.home()
        self.config_file = config_file or Path(
            os.environ.get(
                "AIRTHINGS_CONFIG_FILE",
                home / ".openclaw" / "airthings" / "config.json",
            )
        )
        self.state_dir = state_dir or Path(
            os.environ.get(
                "AIRTHINGS_STATE_DIR",
                home / ".openclaw" / "airthings" / "state",
            )
        )
        self.cache_file = self.state_dir / "status-cache.json"
        self.lock_file = self.state_dir / "ble.lock"
        self.cache_ttl_seconds = cache_ttl_seconds

    def load_config(self) -> list[dict[str, Any]]:
        _private_file(self.config_file)
        try:
            payload = json.loads(self.config_file.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise AirthingsError("invalid_config", 12) from exc
        if not isinstance(payload, dict) or payload.get("schema_version") != SCHEMA_VERSION:
            raise AirthingsError("invalid_config", 12)
        raw_devices = payload.get("devices")
        if not isinstance(raw_devices, list) or not raw_devices or len(raw_devices) > 16:
            raise AirthingsError("invalid_config", 12)
        devices = [validate_binding(item) for item in raw_devices]
        aliases = [item["alias"] for item in devices]
        if len(aliases) != len(set(aliases)):
            raise AirthingsError("invalid_config", 12)
        return devices

    def _load_cache(self) -> dict[str, Any]:
        if not self.cache_file.exists():
            return {}
        try:
            _private_file(self.cache_file)
            payload = json.loads(self.cache_file.read_text(encoding="utf-8"))
        except (AirthingsError, OSError, json.JSONDecodeError):
            return {}
        if not isinstance(payload, dict) or payload.get("schema_version") != SCHEMA_VERSION:
            return {}
        devices = payload.get("devices")
        return devices if isinstance(devices, dict) else {}

    def _fresh_cached(self, alias: str) -> dict[str, Any] | None:
        reading = self._load_cache().get(alias)
        if not isinstance(reading, dict) or reading.get("online") is not True:
            return None
        try:
            read_time = datetime.fromisoformat(
                str(reading["read_at"]).replace("Z", "+00:00")
            ).timestamp()
        except (KeyError, TypeError, ValueError):
            return None
        age = max(0, int(time.time() - read_time))
        if age > self.cache_ttl_seconds:
            return None
        cached = copy.deepcopy(reading)
        cached["cached"] = True
        cached["cache_age_seconds"] = age
        return cached

    def _save_cache(self, readings: list[dict[str, Any]]) -> None:
        existing = self._load_cache()
        for reading in readings:
            if reading.get("online") is True:
                stored = copy.deepcopy(reading)
                stored.pop("cache_age_seconds", None)
                stored["cached"] = False
                existing[reading["alias"]] = stored
        _atomic_json(
            self.cache_file,
            {"schema_version": SCHEMA_VERSION, "devices": existing},
        )

    @contextlib.contextmanager
    def ble_lock(self) -> Iterator[None]:
        _private_dir(self.state_dir)
        descriptor = os.open(self.lock_file, os.O_CREAT | os.O_RDWR, 0o600)
        try:
            os.fchmod(descriptor, 0o600)
            try:
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise AirthingsError("bluetooth_busy", 11) from exc
            yield
        finally:
            os.close(descriptor)

    @staticmethod
    def _ble_error_category(exc: Exception) -> str:
        message = str(exc).lower()
        if "not authorized" in message or "denied" in message:
            return "bluetooth_unauthorized"
        if "powered off" in message or "not available" in message:
            return "bluetooth_unavailable"
        if "not found" in message:
            return "device_not_found"
        return "read_failed"

    async def _read_address(self, binding: dict[str, Any]) -> dict[str, Any]:
        try:
            from airthings_ble import AirthingsBluetoothDeviceData
            from bleak import BleakScanner
        except ImportError as exc:
            raise AirthingsError("runtime_unavailable", 12) from exc

        try:
            device = await BleakScanner.find_device_by_address(
                binding["address"], timeout=DEFAULT_SCAN_SECONDS
            )
            if device is None:
                return offline_reading(binding, "device_not_found")
            reader = AirthingsBluetoothDeviceData(logging.getLogger("airthings"))
            result = await reader.update_device(device)
            if result.model.product_name != SUPPORTED_MODEL:
                return offline_reading(binding, "model_mismatch")
            return normalize_reading(binding, result)
        except Exception as exc:  # BLE libraries expose backend-specific errors.
            return offline_reading(binding, self._ble_error_category(exc))

    async def _read_with_timeout(self, binding: dict[str, Any]) -> dict[str, Any]:
        try:
            return await asyncio.wait_for(
                self._read_address(binding), timeout=DEFAULT_SCAN_SECONDS + 10
            )
        except TimeoutError:
            return offline_reading(binding, "bluetooth_timeout")

    async def status(
        self, alias: str | None, refresh: bool, cache_only: bool = False
    ) -> dict[str, Any]:
        bindings = self.load_config()
        if alias:
            bindings = [item for item in bindings if item["alias"] == alias]
            if not bindings:
                raise AirthingsError("unknown_alias", 2)

        readings: list[dict[str, Any]] = []
        pending: list[dict[str, Any]] = []
        if not refresh:
            for binding in bindings:
                cached = self._fresh_cached(binding["alias"])
                if cached is None:
                    pending.append(binding)
                else:
                    readings.append(cached)
        else:
            pending = bindings

        if pending and cache_only:
            readings.extend(
                offline_reading(item, "cache_unavailable") for item in pending
            )
            pending = []

        if pending:
            try:
                with self.ble_lock():
                    fresh = [await self._read_with_timeout(binding) for binding in pending]
                    self._save_cache(fresh)
                    readings.extend(fresh)
            except AirthingsError as exc:
                readings.extend(offline_reading(item, exc.category) for item in pending)

        order = {item["alias"]: index for index, item in enumerate(bindings)}
        readings.sort(key=lambda item: order[item["alias"]])
        return {"ok": True, "devices": readings}

    def devices(self) -> dict[str, Any]:
        devices = [
            {
                "alias": item["alias"],
                "site": item["site"],
                "room": item["room"],
                "model": item["model"],
            }
            for item in self.load_config()
        ]
        return {"ok": True, "devices": devices}

    async def enroll(
        self, alias: str, site: str, room: str, replace: bool
    ) -> dict[str, Any]:
        if os.environ.get("AIRTHINGS_ALLOW_ENROLL") != "1":
            raise AirthingsError("enrollment_not_authorized", 13)
        alias = _validate_text(alias, "invalid_alias")
        site = _validate_text(site, "invalid_site").lower()
        room = _validate_text(room, "invalid_room")
        if site not in SITES:
            raise AirthingsError("invalid_site", 2)
        if self.config_file.exists() and not replace:
            raise AirthingsError("binding_exists", 13)

        try:
            from airthings_ble import AirthingsBluetoothDeviceData
            from bleak import BleakScanner
        except ImportError as exc:
            raise AirthingsError("runtime_unavailable", 12) from exc

        candidates: list[dict[str, Any]] = []
        try:
            with self.ble_lock():
                discovered = await BleakScanner.discover(
                    timeout=DEFAULT_SCAN_SECONDS, return_adv=True
                )
                for address, pair in discovered.items():
                    ble_device, advertisement = pair
                    name = (ble_device.name or advertisement.local_name or "").lower()
                    if (
                        AIRTHINGS_MANUFACTURER_ID not in advertisement.manufacturer_data
                        and "airthings" not in name
                        and "wave" not in name
                        and "enhance" not in name
                    ):
                        continue
                    try:
                        reader = AirthingsBluetoothDeviceData(
                            logging.getLogger("airthings")
                        )
                        result = await reader.update_device(ble_device)
                    except Exception:
                        continue
                    if result.model.product_name != SUPPORTED_MODEL:
                        continue
                    candidates.append(
                        {
                            "address": address,
                            "identifier": getattr(result, "identifier", "") or None,
                        }
                    )
        except AirthingsError:
            raise
        except Exception as exc:
            raise AirthingsError(self._ble_error_category(exc), 11) from exc

        if not candidates:
            raise AirthingsError("device_not_found", 11)
        if len(candidates) != 1:
            raise AirthingsError("multiple_wave_enhance_devices", 13)
        binding = {
            "alias": alias,
            "site": site,
            "room": room,
            "model": SUPPORTED_MODEL,
            **candidates[0],
        }
        if binding["identifier"] is None:
            binding.pop("identifier")
        _atomic_json(
            self.config_file,
            {"schema_version": SCHEMA_VERSION, "devices": [binding]},
        )
        return {
            "ok": True,
            "enrolled": [
                {key: binding[key] for key in ("alias", "site", "room", "model")}
            ],
        }


def _print_human(payload: dict[str, Any]) -> None:
    for device in payload.get("devices", []):
        label = f"{device.get('site', '?').title()} {device.get('room', '?')}"
        print(f"{label} — {device.get('model', SUPPORTED_MODEL)}")
        if not device.get("online"):
            print(f"  Unavailable: {device.get('error', 'unknown')}")
            continue
        print(
            "  "
            f"{device.get('temperature_f', '?')}°F · "
            f"{device.get('humidity_percent', '?')}% RH · "
            f"CO2 {device.get('co2_ppm', '?')} ppm · "
            f"VOC {device.get('voc_ppb', '?')} ppb"
        )
        print(
            "  "
            f"Pressure {device.get('pressure_hpa', '?')} hPa · "
            f"Noise {device.get('noise_dba', '?')} dBA · "
            f"Light {device.get('light_lux', '?')} lux · "
            f"Battery {device.get('battery_percent', '?')}%"
        )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="airthings")
    commands = parser.add_subparsers(dest="command", required=True)

    status = commands.add_parser("status")
    status.add_argument("alias", nargs="?")
    status_mode = status.add_mutually_exclusive_group()
    status_mode.add_argument("--refresh", action="store_true")
    status_mode.add_argument("--cache-only", action="store_true", help=argparse.SUPPRESS)
    status.add_argument("--json", action="store_true")

    devices = commands.add_parser("devices")
    devices.add_argument("--json", action="store_true")

    enroll = commands.add_parser("enroll")
    enroll.add_argument("--alias", default=DEFAULT_ALIAS)
    enroll.add_argument("--site", required=True)
    enroll.add_argument("--room", required=True)
    enroll.add_argument("--replace", action="store_true")
    enroll.add_argument("--json", action="store_true")
    return parser


def main() -> int:
    logging.disable(logging.CRITICAL)
    args = build_parser().parse_args()
    monitor = AirthingsMonitor()
    try:
        if args.command == "status":
            payload = asyncio.run(
                monitor.status(args.alias, args.refresh, args.cache_only)
            )
        elif args.command == "devices":
            payload = monitor.devices()
        else:
            payload = asyncio.run(
                monitor.enroll(args.alias, args.site, args.room, args.replace)
            )
        if getattr(args, "json", False):
            print(json.dumps(payload, separators=(",", ":"), sort_keys=True))
        elif args.command == "devices":
            for device in payload["devices"]:
                print(
                    f"{device['alias']} — {device['site'].title()} "
                    f"{device['room']} ({device['model']})"
                )
        elif args.command == "enroll":
            enrolled = payload["enrolled"][0]
            print(
                f"Enrolled {enrolled['alias']} — "
                f"{enrolled['site'].title()} {enrolled['room']}"
            )
        else:
            _print_human(payload)
        return 0
    except AirthingsError as exc:
        print(
            json.dumps(
                {"error": exc.category, "ok": False},
                separators=(",", ":"),
                sort_keys=True,
            )
        )
        return exc.exit_code


if __name__ == "__main__":
    raise SystemExit(main())
