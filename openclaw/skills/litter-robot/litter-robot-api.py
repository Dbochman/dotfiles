#!/usr/bin/env python3
"""Guarded multi-device Litter-Robot API adapter for OpenClaw."""

from __future__ import annotations

import asyncio
from enum import Enum
import json
import os
from pathlib import Path
import stat
import sys
import tempfile
from typing import Any


CONFIG_DIR = Path(
    os.environ.get("LITTER_ROBOT_CONFIG_DIR", Path.home() / ".config/litter-robot")
).expanduser()
CONFIG_FILE = CONFIG_DIR / "config.yaml"
TOKEN_FILE = CONFIG_DIR / "token-cache.json"
BINDINGS_FILE = CONFIG_DIR / "bindings.json"
SCHEMA_VERSION = 1
SITES = ("crosstown", "cabin")
ALIASES = {site: f"{site}-litter-robot" for site in SITES}


class LitterRobotError(Exception):
    """Expected operator-safe failure."""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        non_retryable: bool = False,
        action_may_have_occurred: bool = False,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.non_retryable = non_retryable
        self.action_may_have_occurred = action_may_have_occurred

    def as_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "ok": False,
            "error": self.code,
            "message": self.message,
        }
        if self.non_retryable:
            payload["non_retryable"] = True
        if self.action_may_have_occurred:
            payload["action_may_have_occurred"] = True
        return payload


def _assert_owner_file(path: Path, *, label: str) -> None:
    """Require an owner-only regular file, rejecting symlinks."""
    try:
        info = path.lstat()
    except FileNotFoundError as exc:
        raise LitterRobotError(f"{label}_missing", f"{label.replace('_', ' ').title()} is missing.") from exc
    if (
        not stat.S_ISREG(info.st_mode)
        or info.st_uid != os.getuid()
        or stat.S_IMODE(info.st_mode) != 0o600
    ):
        raise LitterRobotError(
            f"{label}_unsafe",
            f"{label.replace('_', ' ').title()} must be an owner-only regular file (0600).",
        )


def _assert_config_dir() -> None:
    try:
        info = CONFIG_DIR.lstat()
    except FileNotFoundError:
        CONFIG_DIR.mkdir(parents=True, mode=0o700)
        return
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid():
        raise LitterRobotError(
            "config_directory_unsafe",
            "Litter-Robot config directory must be an owner-controlled directory.",
        )


def _atomic_write_json(path: Path, payload: object) -> None:
    _assert_config_dir()
    descriptor, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=CONFIG_DIR)
    temp_path = Path(temp_name)
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, separators=(",", ":"), sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_path, path)
    except Exception:
        try:
            os.close(descriptor)
        except OSError:
            pass
        try:
            temp_path.unlink()
        except FileNotFoundError:
            pass
        raise


def load_config() -> dict[str, str]:
    _assert_owner_file(CONFIG_FILE, label="config")
    config: dict[str, str] = {}
    try:
        lines = CONFIG_FILE.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise LitterRobotError("config_unreadable", "Litter-Robot config could not be read.") from exc
    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or ":" not in stripped:
            continue
        key, value = stripped.split(":", 1)
        config[key.strip()] = value.strip()
    if not config.get("email") or not config.get("password"):
        raise LitterRobotError(
            "config_invalid",
            "Litter-Robot config must contain non-empty email and password values.",
        )
    return config


def load_cached_tokens() -> dict[str, Any] | None:
    if not TOKEN_FILE.exists():
        return None
    _assert_owner_file(TOKEN_FILE, label="token_cache")
    try:
        cached = json.loads(TOKEN_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise LitterRobotError("token_cache_invalid", "Litter-Robot token cache is invalid.") from exc
    tokens = cached.get("tokens") if isinstance(cached, dict) else None
    return tokens if isinstance(tokens, dict) else None


def save_tokens(tokens: dict[str, Any] | None) -> None:
    if isinstance(tokens, dict):
        _atomic_write_json(TOKEN_FILE, {"tokens": tokens})


def _validate_bindings(payload: object) -> list[dict[str, str]]:
    if not isinstance(payload, dict) or payload.get("schema_version") != SCHEMA_VERSION:
        raise LitterRobotError("bindings_invalid", "Litter-Robot bindings have an unsupported schema.")
    robots = payload.get("robots")
    if not isinstance(robots, list):
        raise LitterRobotError("bindings_invalid", "Litter-Robot bindings must contain a robots list.")

    result: list[dict[str, str]] = []
    seen_aliases: set[str] = set()
    seen_sites: set[str] = set()
    seen_serials: set[str] = set()
    for item in robots:
        if not isinstance(item, dict):
            raise LitterRobotError("bindings_invalid", "Litter-Robot binding entries must be objects.")
        alias = item.get("alias")
        site = item.get("site")
        serial = item.get("serial")
        if (
            not isinstance(alias, str)
            or not isinstance(site, str)
            or not isinstance(serial, str)
            or site not in SITES
            or alias != ALIASES[site]
            or not serial
        ):
            raise LitterRobotError("bindings_invalid", "Litter-Robot binding entry is invalid.")
        if alias in seen_aliases or site in seen_sites or serial in seen_serials:
            raise LitterRobotError("bindings_invalid", "Litter-Robot bindings must be unique.")
        seen_aliases.add(alias)
        seen_sites.add(site)
        seen_serials.add(serial)
        result.append({"alias": alias, "site": site, "serial": serial})
    return sorted(result, key=lambda item: SITES.index(item["site"]))


def load_bindings(*, required: bool = True) -> list[dict[str, str]]:
    if not BINDINGS_FILE.exists():
        if required:
            raise LitterRobotError(
                "bindings_missing",
                "Litter-Robot devices are not enrolled. Run the attended enrollment helper.",
            )
        return []
    _assert_owner_file(BINDINGS_FILE, label="bindings")
    try:
        payload = json.loads(BINDINGS_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise LitterRobotError("bindings_invalid", "Litter-Robot bindings are invalid.") from exc
    return _validate_bindings(payload)


def save_bindings(bindings: list[dict[str, str]]) -> None:
    validated = _validate_bindings(
        {"schema_version": SCHEMA_VERSION, "robots": bindings}
    )
    _atomic_write_json(
        BINDINGS_FILE,
        {"schema_version": SCHEMA_VERSION, "robots": validated},
    )


async def connect_account(*, load_pets: bool = False):
    """Connect with cached tokens when possible and load current robots."""
    try:
        from pylitterbot import Account
    except ImportError as exc:
        raise LitterRobotError(
            "runtime_missing", "The locked Litter-Robot runtime is unavailable."
        ) from exc

    config = load_config()
    account = Account(
        token=load_cached_tokens(),
        token_update_callback=save_tokens,
    )
    try:
        await account.connect(
            username=config["email"],
            password=config["password"],
            load_robots=True,
            load_pets=load_pets,
        )
    except Exception as exc:
        try:
            await account.disconnect()
        except Exception:
            pass
        raise LitterRobotError(
            "connection_failed",
            "Could not authenticate with or reach the Whisker service.",
        ) from exc
    return account


def litter_robots(account: object) -> list[object]:
    robots = getattr(account, "robots", [])
    return [
        robot
        for robot in robots
        if str(getattr(robot, "model", "")).startswith("Litter-Robot")
    ]


def _enum_value(value: object) -> str | None:
    if value is None:
        return None
    if isinstance(value, Enum):
        return value.name
    text = str(value)
    return text.rsplit(".", 1)[-1] if text else None


def _robot_summary(binding: dict[str, str], robot: object | None) -> dict[str, Any]:
    summary: dict[str, Any] = {
        "alias": binding["alias"],
        "site": binding["site"],
    }
    if robot is None:
        summary.update({"is_online": False, "status": "NOT_FOUND", "error": "robot_not_found"})
        return summary

    status = getattr(robot, "status", None)
    summary.update(
        {
            "model": getattr(robot, "model", None),
            "is_online": bool(getattr(robot, "is_online", False)),
            "status": _enum_value(status),
            "status_text": getattr(status, "text", None),
            "waste_level_pct": getattr(robot, "waste_drawer_level", None),
            "night_light": bool(getattr(robot, "night_light_mode_enabled", False)),
            "panel_lock": bool(getattr(robot, "panel_lock_enabled", False)),
            "clean_wait_minutes": getattr(robot, "clean_cycle_wait_time_minutes", None),
            "cycle_count": getattr(robot, "cycle_count", None),
            "cycle_capacity": getattr(robot, "cycle_capacity", None),
            "waste_full": bool(getattr(robot, "is_waste_drawer_full", False)),
        }
    )
    litter_level = getattr(robot, "litter_level", None)
    if litter_level is not None:
        summary["litter_level_pct"] = litter_level
    return summary


async def _pet_summaries(account: object, *, history: bool) -> list[dict[str, Any]]:
    summaries: list[dict[str, Any]] = []
    for pet in getattr(account, "pets", []):
        summary: dict[str, Any] = {
            "name": getattr(pet, "name", None),
            "type": _enum_value(getattr(pet, "pet_type", None)),
            "weight_lbs": getattr(pet, "weight", None),
            "gender": _enum_value(getattr(pet, "gender", None)),
        }
        if history:
            try:
                weights = await pet.fetch_weight_history(limit=5)
            except Exception:
                weights = []
            summary["recent_weights"] = [
                {
                    "weight_lbs": getattr(weight, "weight", None),
                    "timestamp": (
                        getattr(weight, "timestamp", None).isoformat()
                        if hasattr(getattr(weight, "timestamp", None), "isoformat")
                        else str(getattr(weight, "timestamp", "")) or None
                    ),
                }
                for weight in weights
            ]
        summaries.append(summary)
    return summaries


def _binding_for_selector(bindings: list[dict[str, str]], selector: str) -> dict[str, str]:
    matches = [binding for binding in bindings if binding["alias"] == selector]
    if len(matches) != 1:
        raise LitterRobotError(
            "invalid_robot_selector",
            f"Use an exact enrolled alias: {', '.join(binding['alias'] for binding in bindings)}.",
            non_retryable=True,
        )
    return matches[0]


def _resolve_robot(account: object, binding: dict[str, str]) -> object:
    matches = [
        robot
        for robot in litter_robots(account)
        if getattr(robot, "serial", None) == binding["serial"]
    ]
    if len(matches) != 1:
        raise LitterRobotError(
            "robot_not_found",
            f"The enrolled {binding['alias']} was not found in the Whisker account.",
        )
    robot = matches[0]
    if not bool(getattr(robot, "is_online", False)):
        raise LitterRobotError(
            "robot_offline", f"The enrolled {binding['alias']} is offline."
        )
    return robot


async def command_status(selector: str | None = None) -> dict[str, Any]:
    bindings = load_bindings()
    if selector is not None:
        bindings = [_binding_for_selector(bindings, selector)]
    account = await connect_account(load_pets=True)
    try:
        by_serial = {
            getattr(robot, "serial", None): robot for robot in litter_robots(account)
        }
        summaries = [
            _robot_summary(binding, by_serial.get(binding["serial"]))
            for binding in bindings
        ]
        return {
            "ok": all(not item.get("error") for item in summaries),
            "robots": summaries,
            "pets": await _pet_summaries(account, history=False),
        }
    finally:
        await account.disconnect()


async def command_pets() -> dict[str, Any]:
    account = await connect_account(load_pets=True)
    try:
        return {"ok": True, "pets": await _pet_summaries(account, history=True)}
    finally:
        await account.disconnect()


async def command_history(selector: str, limit: int) -> dict[str, Any]:
    if limit < 1 or limit > 100:
        raise LitterRobotError(
            "invalid_limit", "History limit must be between 1 and 100.", non_retryable=True
        )
    binding = _binding_for_selector(load_bindings(), selector)
    account = await connect_account()
    try:
        robot = _resolve_robot(account, binding)
        try:
            history = await robot.get_activity_history(limit=limit)
        except Exception as exc:
            raise LitterRobotError(
                "history_failed", f"Could not retrieve history for {binding['alias']}."
            ) from exc
        entries = []
        for item in history:
            timestamp = getattr(item, "timestamp", None)
            action = getattr(item, "action", None)
            entries.append(
                {
                    "timestamp": timestamp.isoformat() if hasattr(timestamp, "isoformat") else str(timestamp),
                    "action": getattr(action, "text", None) or _enum_value(action),
                }
            )
        return {
            "ok": True,
            "alias": binding["alias"],
            "site": binding["site"],
            "history": entries,
        }
    finally:
        await account.disconnect()


async def command_mutation(command: str, selector: str, state: str | None = None) -> dict[str, Any]:
    binding = _binding_for_selector(load_bindings(), selector)
    account = await connect_account()
    try:
        robot = _resolve_robot(account, binding)
        action = command
        try:
            if command == "clean":
                accepted = await robot.start_cleaning()
                action = "clean_cycle_started"
            elif command == "reset":
                if not hasattr(robot, "reset"):
                    raise LitterRobotError(
                        "unsupported_action",
                        f"Robot reset is not supported for {binding['alias']}.",
                        non_retryable=True,
                    )
                accepted = await robot.reset()
                action = "robot_reset"
            elif command == "nightlight":
                if state not in {"on", "off"}:
                    raise LitterRobotError(
                        "invalid_state", "Night light state must be on or off.", non_retryable=True
                    )
                accepted = await robot.set_night_light(state == "on")
                action = f"night_light_{state}"
            else:
                raise LitterRobotError("unknown_command", "Unknown Litter-Robot command.")
        except LitterRobotError:
            raise
        except Exception as exc:
            raise LitterRobotError(
                "action_outcome_unknown",
                f"Whisker did not confirm the {command} outcome for {binding['alias']}; do not retry automatically.",
                non_retryable=True,
                action_may_have_occurred=True,
            ) from exc
        if accepted is not True:
            raise LitterRobotError(
                "action_rejected",
                f"Whisker did not accept the {command} command for {binding['alias']}.",
                non_retryable=True,
            )
        return {
            "ok": True,
            "alias": binding["alias"],
            "site": binding["site"],
            "action": action,
            "accepted": True,
        }
    finally:
        await account.disconnect()


async def command_enroll(site: str, *, name_contains: str | None, remaining: bool) -> dict[str, Any]:
    if os.environ.get("LITTER_ROBOT_ALLOW_ENROLL") != "1":
        raise LitterRobotError(
            "enrollment_not_allowed",
            "Set LITTER_ROBOT_ALLOW_ENROLL=1 for attended enrollment.",
            non_retryable=True,
        )
    if site not in SITES:
        raise LitterRobotError(
            "invalid_site", f"Site must be one of: {', '.join(SITES)}.", non_retryable=True
        )
    if bool(name_contains) == bool(remaining):
        raise LitterRobotError(
            "invalid_enrollment_selector",
            "Choose exactly one enrollment selector: --name-contains or --remaining.",
            non_retryable=True,
        )

    bindings = load_bindings(required=False)
    existing = next((item for item in bindings if item["site"] == site), None)
    account = await connect_account()
    try:
        robots = litter_robots(account)
        if existing is not None:
            if any(getattr(robot, "serial", None) == existing["serial"] for robot in robots):
                return {
                    "ok": True,
                    "enrolled": {"alias": existing["alias"], "site": existing["site"]},
                    "idempotent": True,
                }
            raise LitterRobotError(
                "site_already_bound",
                f"{site.title()} already has a protected binding that is not visible in the account.",
                non_retryable=True,
            )

        bound_serials = {item["serial"] for item in bindings}
        if remaining:
            candidates = [
                robot for robot in robots if getattr(robot, "serial", None) not in bound_serials
            ]
        else:
            needle = str(name_contains).strip().casefold()
            if not needle:
                raise LitterRobotError(
                    "invalid_enrollment_selector",
                    "Enrollment name fragment must not be empty.",
                    non_retryable=True,
                )
            candidates = [
                robot
                for robot in robots
                if needle in str(getattr(robot, "name", "")).casefold()
                and getattr(robot, "serial", None) not in bound_serials
            ]
        if len(candidates) != 1:
            raise LitterRobotError(
                "enrollment_ambiguous",
                f"Enrollment selector matched {len(candidates)} unbound robots; expected exactly one.",
                non_retryable=True,
            )
        robot = candidates[0]
        bindings.append(
            {
                "alias": ALIASES[site],
                "site": site,
                "serial": str(getattr(robot, "serial")),
            }
        )
        save_bindings(bindings)
        return {
            "ok": True,
            "enrolled": {
                "alias": ALIASES[site],
                "site": site,
                "model": getattr(robot, "model", None),
            },
            "binding_count": len(bindings),
        }
    finally:
        await account.disconnect()


def _parse_limit(value: str) -> int:
    try:
        return int(value)
    except ValueError as exc:
        raise LitterRobotError(
            "invalid_limit", "History limit must be an integer.", non_retryable=True
        ) from exc


async def dispatch(argv: list[str]) -> dict[str, Any]:
    if not argv:
        raise LitterRobotError("missing_command", "A Litter-Robot command is required.")
    command = argv[0]
    args = argv[1:]
    if command == "status" and len(args) <= 1:
        return await command_status(args[0] if args else None)
    if command == "pets" and not args:
        return await command_pets()
    if command == "history" and 1 <= len(args) <= 2:
        return await command_history(args[0], _parse_limit(args[1]) if len(args) == 2 else 10)
    if command in {"clean", "reset"} and len(args) == 1:
        return await command_mutation(command, args[0])
    if command == "nightlight" and len(args) == 2:
        return await command_mutation(command, args[0], args[1].casefold())
    if command == "enroll":
        if len(args) == 4 and args[0] == "--site" and args[2] == "--name-contains":
            return await command_enroll(args[1].casefold(), name_contains=args[3], remaining=False)
        if len(args) == 3 and args[0] == "--site" and args[2] == "--remaining":
            return await command_enroll(args[1].casefold(), name_contains=None, remaining=True)
        raise LitterRobotError(
            "invalid_enrollment_arguments",
            "Usage: enroll --site <crosstown|cabin> (--name-contains <text>|--remaining).",
            non_retryable=True,
        )
    raise LitterRobotError(
        "invalid_arguments", "Unknown command or invalid arguments.", non_retryable=True
    )


def main(argv: list[str] | None = None) -> int:
    try:
        payload = asyncio.run(dispatch(list(sys.argv[1:] if argv is None else argv)))
        code = 0
    except LitterRobotError as exc:
        payload = exc.as_dict()
        code = 1
    except KeyboardInterrupt:
        payload = {"ok": False, "error": "interrupted", "message": "Litter-Robot command interrupted."}
        code = 130
    except Exception:
        payload = {
            "ok": False,
            "error": "unexpected_error",
            "message": "Litter-Robot command failed unexpectedly.",
        }
        code = 1
    print(json.dumps(payload, separators=(",", ":"), sort_keys=True, default=str))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
