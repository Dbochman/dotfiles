#!/usr/bin/env python3
"""Bounded local status, control, and attended enrollment for Midea ACs."""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import re
import stat
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Mapping, Sequence


SCHEMA_VERSION = 1
CONFIG_LIMIT_BYTES = 65_536
DEFAULT_CONFIG = Path("~/.openclaw/midea-ac/bindings.json").expanduser()
VALID_SITES = frozenset({"cabin", "crosstown"})
CLOUD_NAMES = ("SmartHome", "NetHome Plus", "Midea Air", "Ariston Clima", "美的美居")
ALIAS_PATTERN = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*")
HEX_PATTERN = re.compile(r"[0-9a-f]+")
MODE_VALUES = {"auto": 1, "cool": 2, "dry": 3, "heat": 4, "fan": 5}
MODE_NAMES = {value: name for name, value in MODE_VALUES.items()}
FAN_VALUES = {
    "silent": 20,
    "low": 40,
    "medium": 60,
    "high": 80,
    "full": 100,
    "auto": 102,
}
FAN_NAMES = {value: name for name, value in FAN_VALUES.items()}
SWING_VALUES = {
    "off": (False, False),
    "vertical": (True, False),
    "horizontal": (False, True),
    "both": (True, True),
}
FEATURE_COMMANDS = frozenset({"eco", "boost", "sleep", "display"})
FEATURE_ATTRIBUTES = {
    "eco": "eco_mode",
    "boost": "boost_mode",
    "sleep": "sleep_mode",
    "display": "screen_display",
}
DEVICE_KEYS = frozenset(
    {
        "alias",
        "site",
        "device_id",
        "type",
        "protocol",
        "model",
        "subtype",
        "token",
        "key",
    }
)


class MideaACError(Exception):
    """Return a stable safe error code."""

    def __init__(self, code: str, *, exit_code: int = 1) -> None:
        super().__init__(code)
        self.code = code
        self.exit_code = exit_code


def config_path() -> Path:
    override = os.environ.get("MIDEA_AC_CONFIG")
    return Path(override).expanduser() if override else DEFAULT_CONFIG


def owner_mode(path: Path) -> tuple[int, int]:
    info = path.stat(follow_symlinks=False)
    return info.st_uid, stat.S_IMODE(info.st_mode)


def validate_private_parent(path: Path, *, create: bool = False) -> None:
    parent = path.parent
    if create and not parent.exists():
        parent.mkdir(parents=True, mode=0o700)
    try:
        info = parent.stat(follow_symlinks=False)
    except OSError as exc:
        raise MideaACError("config_parent_unavailable") from exc
    if (
        parent.is_symlink()
        or not stat.S_ISDIR(info.st_mode)
        or info.st_uid != os.getuid()
        or stat.S_IMODE(info.st_mode) != 0o700
    ):
        raise MideaACError("config_parent_unsafe")


def validate_device(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict) or frozenset(value) != DEVICE_KEYS:
        raise MideaACError("config_device_invalid")
    alias = value.get("alias")
    site = value.get("site")
    device_id = value.get("device_id")
    device_type = value.get("type")
    protocol = value.get("protocol")
    model = value.get("model")
    subtype = value.get("subtype")
    token = value.get("token")
    key = value.get("key")
    if not isinstance(alias, str) or ALIAS_PATTERN.fullmatch(alias) is None:
        raise MideaACError("config_alias_invalid")
    if site not in VALID_SITES or not alias.startswith(site + "-"):
        raise MideaACError("config_site_invalid")
    if not isinstance(device_id, str) or not device_id.isdigit():
        raise MideaACError("config_device_id_invalid")
    if device_type != 0xAC or protocol not in {1, 2, 3}:
        raise MideaACError("config_protocol_invalid")
    if not isinstance(model, str) or not 1 <= len(model) <= 64:
        raise MideaACError("config_model_invalid")
    if not isinstance(subtype, int) or not 0 <= subtype <= 65_535:
        raise MideaACError("config_subtype_invalid")
    for name, secret, minimum, maximum in (
        ("token", token, 64, 256),
        ("key", key, 32, 128),
    ):
        if (
            not isinstance(secret, str)
            or not minimum <= len(secret) <= maximum
            or len(secret) % 2
            or HEX_PATTERN.fullmatch(secret) is None
        ):
            raise MideaACError("config_" + name + "_invalid")
    return dict(value)


def validate_config(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict) or frozenset(value) != {
        "schema_version",
        "devices",
    }:
        raise MideaACError("config_invalid")
    if value.get("schema_version") != SCHEMA_VERSION:
        raise MideaACError("config_schema_invalid")
    raw_devices = value.get("devices")
    if not isinstance(raw_devices, list) or not 1 <= len(raw_devices) <= 32:
        raise MideaACError("config_devices_invalid")
    devices = [validate_device(device) for device in raw_devices]
    aliases = [device["alias"] for device in devices]
    identifiers = [device["device_id"] for device in devices]
    if len(set(aliases)) != len(aliases) or len(set(identifiers)) != len(identifiers):
        raise MideaACError("config_duplicate_binding")
    return {"schema_version": SCHEMA_VERSION, "devices": devices}


def load_config() -> dict[str, Any]:
    path = config_path()
    if not path.parent.exists() and not path.exists():
        raise MideaACError("config_missing")
    validate_private_parent(path)
    try:
        info = path.stat(follow_symlinks=False)
    except FileNotFoundError as exc:
        raise MideaACError("config_missing") from exc
    except OSError as exc:
        raise MideaACError("config_unavailable") from exc
    if (
        path.is_symlink()
        or not stat.S_ISREG(info.st_mode)
        or info.st_uid != os.getuid()
        or stat.S_IMODE(info.st_mode) != 0o600
        or info.st_size > CONFIG_LIMIT_BYTES
    ):
        raise MideaACError("config_unsafe")
    try:
        raw = path.read_bytes()
        return validate_config(json.loads(raw.decode("utf-8")))
    except MideaACError:
        raise
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise MideaACError("config_invalid") from exc


def write_config(value: Mapping[str, Any]) -> None:
    validated = validate_config(value)
    path = config_path()
    validate_private_parent(path, create=True)
    encoded = (json.dumps(validated, indent=2, sort_keys=True) + "\n").encode()
    if len(encoded) > CONFIG_LIMIT_BYTES:
        raise MideaACError("config_too_large")
    descriptor, temporary_name = tempfile.mkstemp(prefix=".bindings.", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "wb", closefd=True) as handle:
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
        temporary.replace(path)
        directory = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    except Exception:
        try:
            os.close(descriptor)
        except OSError:
            pass
        temporary.unlink(missing_ok=True)
        raise


def resolve_binding(config: Mapping[str, Any], alias: str) -> dict[str, Any]:
    matches = [device for device in config["devices"] if device["alias"] == alias]
    if not matches:
        raise MideaACError("device_not_found")
    return dict(matches[0])


def import_midea() -> tuple[Any, Any]:
    try:
        from midealocal.devices import device_selector
        from midealocal.discover import discover
    except ImportError as exc:
        raise MideaACError("runtime_unavailable") from exc
    return discover, device_selector


def discover_local(*, attempts: int = 1) -> list[dict[str, Any]]:
    discover, _ = import_midea()
    logging.disable(logging.CRITICAL)
    devices: dict[int, dict[str, Any]] = {}
    for _ in range(attempts):
        try:
            devices.update(discover(discover_type=[0xAC]))
        except Exception as exc:
            raise MideaACError("discovery_failed") from exc
    return sorted(devices.values(), key=lambda item: int(item["device_id"]))


def build_device(binding: Mapping[str, Any], discovered: Mapping[str, Any]) -> Any:
    _, selector = import_midea()
    try:
        return selector(
            name=binding["alias"],
            device_id=int(binding["device_id"]),
            device_type=discovered["type"],
            ip_address=discovered["ip_address"],
            port=discovered["port"],
            token=binding["token"],
            key=binding["key"],
            device_protocol=discovered["protocol"],
            model=discovered["model"],
            subtype=binding["subtype"],
            customize="",
            mac=discovered.get("mac"),
            serial_number=discovered.get("sn"),
        )
    except Exception as exc:
        raise MideaACError("binding_invalid") from exc


def refresh_device(binding: Mapping[str, Any], discovered: Mapping[str, Any]) -> Any:
    device = build_device(binding, discovered)
    if device is None:
        raise MideaACError("device_unsupported")
    try:
        if not device.connect():
            raise MideaACError("device_auth_failed")
        device.refresh_status(True)
        return device
    except MideaACError:
        device.close_socket()
        raise
    except Exception as exc:
        device.close_socket()
        raise MideaACError("device_unavailable") from exc


def c_to_f(value: Any) -> float | None:
    if value is None or isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return round(float(value) * 9 / 5 + 32, 1)


def normalized_fan(value: Any) -> str | int | None:
    if value is None:
        return None
    if value in FAN_NAMES:
        return FAN_NAMES[value]
    if value == 127:
        return "auto"
    return int(value) if isinstance(value, (int, float)) else str(value)


def status_from_attributes(binding: Mapping[str, Any], attributes: Mapping[Any, Any]) -> dict[str, Any]:
    error_code = attributes.get("error_code")
    return {
        "alias": binding["alias"],
        "site": binding["site"],
        "online": True,
        "model": binding["model"],
        "power": bool(attributes.get("power")),
        "mode": MODE_NAMES.get(attributes.get("mode"), attributes.get("mode")),
        "target_temperature_f": c_to_f(attributes.get("target_temperature")),
        "indoor_temperature_f": c_to_f(attributes.get("indoor_temperature")),
        "outdoor_temperature_f": c_to_f(attributes.get("outdoor_temperature")),
        "humidity_percent": attributes.get("indoor_humidity"),
        "fan": normalized_fan(attributes.get("fan_speed")),
        "swing": {
            "vertical": bool(attributes.get("swing_vertical")),
            "horizontal": bool(attributes.get("swing_horizontal")),
        },
        "eco": bool(attributes.get("eco_mode")),
        "boost": bool(attributes.get("boost_mode")),
        "sleep": bool(attributes.get("sleep_mode")),
        "display": bool(attributes.get("screen_display")),
        "error_code": int(error_code) if isinstance(error_code, (int, float)) else None,
        "energy": {
            "realtime_power_w": attributes.get("realtime_power"),
            "current_kwh": attributes.get("current_energy_consumption"),
            "total_kwh": attributes.get("total_energy_consumption"),
        },
    }


def offline_status(binding: Mapping[str, Any], code: str) -> dict[str, Any]:
    return {
        "alias": binding["alias"],
        "site": binding["site"],
        "online": False,
        "model": binding["model"],
        "error": code,
    }


def collect_status(alias: str | None = None) -> list[dict[str, Any]]:
    config = load_config()
    bindings = (
        [resolve_binding(config, alias)] if alias else list(config["devices"])
    )
    discovered = {
        str(item["device_id"]): item for item in discover_local(attempts=2)
    }
    results: list[dict[str, Any]] = []
    for binding in bindings:
        local = discovered.get(binding["device_id"])
        if local is None:
            results.append(offline_status(binding, "not_discovered"))
            continue
        device = None
        try:
            device = refresh_device(binding, local)
            results.append(status_from_attributes(binding, device.attributes))
        except MideaACError as exc:
            results.append(offline_status(binding, exc.code))
        finally:
            if device is not None:
                device.close_socket()
    return results


def f_to_c(value: float) -> float:
    return round(((value - 32) * 5 / 9) * 2) / 2


def intended_value(command: str, value: Any) -> tuple[str, Any]:
    if command in {"on", "off"}:
        return "power", command == "on"
    if command == "temperature":
        return "target_temperature", f_to_c(float(value))
    if command == "mode":
        return "mode", MODE_VALUES[str(value)]
    if command == "fan":
        return "fan_speed", FAN_VALUES[str(value)]
    if command == "swing":
        return "swing", SWING_VALUES[str(value)]
    if command in FEATURE_COMMANDS:
        return FEATURE_ATTRIBUTES[command], value == "on"
    raise MideaACError("command_invalid")


def value_matches(attributes: Mapping[Any, Any], attribute: str, intended: Any) -> bool:
    if attribute == "swing":
        vertical, horizontal = intended
        return bool(attributes.get("swing_vertical")) == vertical and bool(
            attributes.get("swing_horizontal")
        ) == horizontal
    current = attributes.get(attribute)
    if attribute == "target_temperature":
        return isinstance(current, (int, float)) and abs(float(current) - intended) <= 0.26
    if attribute == "fan_speed" and intended == 102 and current == 127:
        return True
    return current == intended


def send_control(command: str, alias: str, value: Any = None) -> tuple[dict[str, Any], int]:
    config = load_config()
    binding = resolve_binding(config, alias)
    local = {
        str(item["device_id"]): item for item in discover_local(attempts=2)
    }.get(binding["device_id"])
    if local is None:
        raise MideaACError("device_unavailable")
    device = refresh_device(binding, local)
    attribute, intended = intended_value(command, value)
    try:
        before = device.attributes
        if value_matches(before, attribute, intended):
            return {
                "ok": True,
                "alias": alias,
                "command": command,
                "changed": False,
                "verified": True,
                "status": status_from_attributes(binding, before),
            }, 0
        if command in {"on", "off"}:
            device.set_attribute("power", intended)
        elif command == "temperature":
            device.set_target_temperature(intended, None)
        elif command in {"mode", "fan"} or command in FEATURE_COMMANDS:
            device.set_attribute(attribute, intended)
        elif command == "swing":
            device.set_swing(*intended)
        else:
            raise MideaACError("command_invalid")
        time.sleep(1)
        device.refresh_status(True)
        verified = value_matches(device.attributes, attribute, intended)
        result = {
            "ok": verified,
            "alias": alias,
            "command": command,
            "changed": True,
            "verified": verified,
            "status": status_from_attributes(binding, device.attributes),
        }
        if not verified:
            result["error"] = "outcome_unknown"
        return result, 0 if verified else 2
    except MideaACError:
        raise
    except Exception as exc:
        raise MideaACError("outcome_unknown", exit_code=2) from exc
    finally:
        device.close_socket()


def safe_candidate(index: int, item: Mapping[str, Any], name: str | None = None) -> dict[str, Any]:
    candidate = {
        "candidate": f"candidate-{index}",
        "model": str(item.get("model") or "unknown").strip(),
        "protocol": f"v{item.get('protocol')}",
    }
    if name:
        candidate["cloud_name"] = name[:80]
    return candidate


def slugify(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")


def cloud_credentials() -> tuple[str, str]:
    username = os.environ.pop("MIDEA_USERNAME", "")
    password = os.environ.pop("MIDEA_PASSWORD", "")
    if not username or not password:
        raise MideaACError("cloud_credentials_missing")
    return username, password


async def cloud_inventory(cloud_name: str) -> tuple[list[dict[str, Any]], dict[int, dict[str, Any]], Any]:
    try:
        import aiohttp
        from midealocal.cloud import get_midea_cloud
    except ImportError as exc:
        raise MideaACError("runtime_unavailable") from exc
    username, password = cloud_credentials()
    session = aiohttp.ClientSession()
    try:
        cloud = get_midea_cloud(cloud_name, session, username, password)
        if not await cloud.login():
            raise MideaACError("cloud_auth_failed")
        appliances = await cloud.list_appliances(home_id=None)
        if not appliances:
            raise MideaACError("cloud_devices_unavailable")
        return discover_local(attempts=3), appliances, (cloud, session)
    except MideaACError:
        await session.close()
        raise
    except Exception as exc:
        await session.close()
        raise MideaACError("cloud_request_failed") from exc


async def working_binding(
    cloud: Any,
    local: Mapping[str, Any],
    alias: str,
    site: str,
) -> dict[str, Any]:
    try:
        from midealocal.cloud import DEFAULT_KEYS
    except ImportError as exc:
        raise MideaACError("runtime_unavailable") from exc
    cloud_keys = await cloud.get_cloud_keys(int(local["device_id"]))
    candidates = [*cloud_keys.values(), *DEFAULT_KEYS.values()]
    for candidate in candidates:
        binding = {
            "alias": alias,
            "site": site,
            "device_id": str(local["device_id"]),
            "type": int(local["type"]),
            "protocol": int(local["protocol"]),
            "model": str(local["model"]).strip(),
            "subtype": 0,
            "token": str(candidate.get("token", "")).lower(),
            "key": str(candidate.get("key", "")).lower(),
        }
        try:
            validate_device(binding)
            device = refresh_device(binding, local)
            try:
                binding["subtype"] = int(device.subtype)
                return binding
            finally:
                device.close_socket()
        except MideaACError:
            continue
    raise MideaACError("device_auth_failed")


async def inspect_cloud(cloud_name: str) -> list[dict[str, Any]]:
    local, appliances, state = await cloud_inventory(cloud_name)
    _, session = state
    try:
        output = []
        for index, item in enumerate(local, 1):
            appliance = appliances.get(int(item["device_id"]), {})
            name = appliance.get("name") if isinstance(appliance, dict) else None
            output.append(safe_candidate(index, item, name if isinstance(name, str) else None))
        return output
    finally:
        await session.close()


def parse_maps(values: Sequence[str]) -> dict[str, str]:
    mappings: dict[str, str] = {}
    for value in values:
        if "=" not in value:
            raise MideaACError("mapping_invalid")
        candidate, alias = value.split("=", 1)
        if not re.fullmatch(r"candidate-[1-9][0-9]*", candidate):
            raise MideaACError("mapping_candidate_invalid")
        if ALIAS_PATTERN.fullmatch(alias) is None:
            raise MideaACError("mapping_alias_invalid")
        if candidate in mappings or alias in mappings.values():
            raise MideaACError("mapping_duplicate")
        mappings[candidate] = alias
    return mappings


async def enroll(
    cloud_name: str,
    site: str,
    map_values: Sequence[str],
    expect_count: int,
) -> list[dict[str, Any]]:
    if site not in VALID_SITES:
        raise MideaACError("site_invalid")
    local, appliances, state = await cloud_inventory(cloud_name)
    cloud, session = state
    try:
        if len(local) != expect_count:
            raise MideaACError("discovery_count_mismatch")
        mappings = parse_maps(map_values)
        handles = {f"candidate-{index}" for index in range(1, len(local) + 1)}
        if mappings and frozenset(mappings) != handles:
            raise MideaACError("mapping_incomplete")
        selected: list[tuple[dict[str, Any], str]] = []
        for index, item in enumerate(local, 1):
            handle = f"candidate-{index}"
            appliance = appliances.get(int(item["device_id"]), {})
            cloud_name_value = appliance.get("name") if isinstance(appliance, dict) else None
            if mappings:
                alias = mappings[handle]
            elif isinstance(cloud_name_value, str) and slugify(cloud_name_value):
                alias = f"{site}-{slugify(cloud_name_value)}"
            else:
                raise MideaACError("aliases_required")
            if not alias.startswith(site + "-"):
                raise MideaACError("mapping_site_mismatch")
            selected.append((item, alias))
        aliases = [alias for _, alias in selected]
        if len(set(aliases)) != len(aliases):
            raise MideaACError("aliases_required")
        bindings = [
            await working_binding(cloud, item, alias, site) for item, alias in selected
        ]
        write_config({"schema_version": SCHEMA_VERSION, "devices": bindings})
        return [
            {"alias": binding["alias"], "site": binding["site"], "model": binding["model"]}
            for binding in bindings
        ]
    finally:
        await session.close()


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(prog="midea-ac")
    commands = root.add_subparsers(dest="command", required=True)
    for command in ("status", "devices"):
        item = commands.add_parser(command)
        if command == "status":
            item.add_argument("alias", nargs="?")
        item.add_argument("--json", action="store_true")
    discover = commands.add_parser("discover")
    discover.add_argument("--json", action="store_true")
    for command in ("on", "off"):
        item = commands.add_parser(command)
        item.add_argument("alias")
        item.add_argument("--json", action="store_true")
    temperature = commands.add_parser("temperature")
    temperature.add_argument("alias")
    temperature.add_argument("value", type=float)
    temperature.add_argument("--json", action="store_true")
    mode = commands.add_parser("mode")
    mode.add_argument("alias")
    mode.add_argument("value", choices=tuple(MODE_VALUES))
    mode.add_argument("--json", action="store_true")
    fan = commands.add_parser("fan")
    fan.add_argument("alias")
    fan.add_argument("value", choices=tuple(FAN_VALUES))
    fan.add_argument("--json", action="store_true")
    swing = commands.add_parser("swing")
    swing.add_argument("alias")
    swing.add_argument("value", choices=tuple(SWING_VALUES))
    swing.add_argument("--json", action="store_true")
    for command in sorted(FEATURE_COMMANDS):
        item = commands.add_parser(command)
        item.add_argument("alias")
        item.add_argument("value", choices=("on", "off"))
        item.add_argument("--json", action="store_true")
    inspect = commands.add_parser("operator-inspect")
    inspect.add_argument("--cloud", default="SmartHome", choices=CLOUD_NAMES)
    inspect.add_argument("--json", action="store_true")
    enroll_parser = commands.add_parser("operator-enroll")
    enroll_parser.add_argument("--cloud", default="SmartHome", choices=CLOUD_NAMES)
    enroll_parser.add_argument("--site", required=True, choices=tuple(sorted(VALID_SITES)))
    enroll_parser.add_argument("--expect-count", required=True, type=int, choices=range(1, 33))
    enroll_parser.add_argument("--map", action="append", default=[])
    enroll_parser.add_argument("--json", action="store_true")
    return root


def emit(value: Any, *, as_json: bool) -> None:
    if as_json:
        print(json.dumps(value, separators=(",", ":"), sort_keys=True))
        return
    if isinstance(value, dict) and "devices" in value:
        for device in value["devices"]:
            if device.get("online"):
                print(
                    f"{device['alias']}: {'on' if device['power'] else 'off'}, "
                    f"{device['mode']}, target {device['target_temperature_f']} F, "
                    f"room {device['indoor_temperature_f']} F"
                )
            else:
                print(f"{device['alias']}: unavailable ({device['error']})")
        return
    print(json.dumps(value, indent=2, sort_keys=True))


def run(args: argparse.Namespace) -> int:
    command = args.command
    if command.startswith("operator-") and os.environ.pop("MIDEA_AC_OPERATOR", "") != "1":
        raise MideaACError("operator_only")
    if command == "discover":
        local = discover_local(attempts=3)
        emit(
            {
                "ok": True,
                "count": len(local),
                "devices": [safe_candidate(index, item) for index, item in enumerate(local, 1)],
            },
            as_json=args.json,
        )
        return 0
    if command == "status":
        emit({"ok": True, "devices": collect_status(args.alias)}, as_json=args.json)
        return 0
    if command == "devices":
        config = load_config()
        statuses = collect_status()
        by_alias = {item["alias"]: item for item in statuses}
        devices = [
            {
                "alias": item["alias"],
                "site": item["site"],
                "model": item["model"],
                "online": bool(by_alias[item["alias"]]["online"]),
            }
            for item in config["devices"]
        ]
        emit({"ok": True, "devices": devices}, as_json=args.json)
        return 0
    if command == "operator-inspect":
        devices = asyncio.run(inspect_cloud(args.cloud))
        emit({"ok": True, "devices": devices}, as_json=args.json)
        return 0
    if command == "operator-enroll":
        devices = asyncio.run(
            enroll(args.cloud, args.site, args.map, args.expect_count)
        )
        emit({"ok": True, "enrolled": devices}, as_json=args.json)
        return 0
    if command == "temperature" and not 60 <= args.value <= 86:
        raise MideaACError("temperature_out_of_range")
    value = getattr(args, "value", None)
    result, exit_code = send_control(command, args.alias, value)
    emit(result, as_json=args.json)
    return exit_code


def main(argv: Sequence[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        return run(args)
    except MideaACError as exc:
        emit({"ok": False, "error": exc.code}, as_json=getattr(args, "json", False))
        return exc.exit_code
    except KeyboardInterrupt:
        emit({"ok": False, "error": "interrupted"}, as_json=getattr(args, "json", False))
        return 130
    except Exception:
        emit({"ok": False, "error": "internal_error"}, as_json=getattr(args, "json", False))
        return 1


if __name__ == "__main__":
    sys.exit(main())
