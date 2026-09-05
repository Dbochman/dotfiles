#!/usr/bin/env python3
"""Safe Petlibro API wrapper used by the OpenClaw Petlibro skill."""

from __future__ import annotations

from datetime import datetime, timezone
import fcntl
import hashlib
import json
import os
from pathlib import Path
import secrets
import socket
import stat
import sys
import tempfile
import time
import urllib.error
import urllib.request


CONFIG_DIR = Path(os.environ.get("PETLIBRO_CONFIG_DIR", Path.home() / ".config" / "petlibro"))
CONFIG_FILE = CONFIG_DIR / "config.yaml"
TOKEN_FILE = CONFIG_DIR / "token-cache.json"
FEED_STATE_DIR = Path(
    os.environ.get(
        "PETLIBRO_FEED_STATE_DIR",
        Path.home() / ".cache" / "openclaw-gateway" / "petlibro",
    )
)
FEED_STATE_FILE = FEED_STATE_DIR / "feed-state.json"
FEED_LOCK_FILE = FEED_STATE_DIR / ".feed.lock"
SCHEDULE_STATE_FILE = FEED_STATE_DIR / "schedule-state.json"
SCHEDULE_LOCK_FILE = FEED_STATE_DIR / ".schedule.lock"

BASE_URL = "https://api.us.petlibro.com"
APPID = 1
TOKEN_EXPIRY_BUFFER = 300
TOKEN_LIFETIME_SECONDS = 3600
DEFAULT_MIN_PORTIONS = 1
DEFAULT_MAX_PORTIONS = 3
HARD_MAX_PORTIONS = 3
DEFAULT_FEED_COOLDOWN_SECONDS = 300
SCHEDULE_VERIFY_ATTEMPTS = 3
SCHEDULE_VERIFY_INTERVAL_SECONDS = 1
FEEDING_HISTORY_DAYS = 30
DEFAULT_FEEDING_HISTORY_LIMIT = 14
MAX_FEEDING_HISTORY_LIMIT = 25
MAX_SCHEDULED_PORTIONS = 48

DEVICE_SELECTORS = {
    "crosstown-feeder": ("device_crosstown_feeder", "feeder", "crosstown"),
    "crosstown-fountain": ("device_crosstown_fountain", "fountain", "crosstown"),
    "cabin-feeder": ("device_cabin_feeder", "feeder", "cabin"),
    "cabin-fountain": ("device_cabin_fountain", "fountain", "cabin"),
}


class PetlibroError(Exception):
    """A safe, structured CLI failure."""

    def __init__(self, code: str, message: str, **fields: object) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.fields = fields

    def payload(self) -> dict:
        return {
            "success": False,
            "error": self.code,
            "message": self.message,
            **self.fields,
        }


def ensure_private_dir(path: Path) -> None:
    if not path.exists():
        path.mkdir(mode=0o700, parents=True)
    info = path.lstat()
    if not stat.S_ISDIR(info.st_mode) or stat.S_ISLNK(info.st_mode):
        raise PetlibroError("state_path_unsafe", "Protected state path is not a directory")
    if info.st_uid != os.getuid():
        raise PetlibroError("state_path_unsafe", "Protected state path has the wrong owner")
    os.chmod(path, 0o700)


def atomic_write_json(path: Path, payload: dict) -> None:
    ensure_private_dir(path.parent)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, separators=(",", ":"), sort_keys=True)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        directory_fd = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass


def load_config() -> dict[str, str]:
    try:
        info = CONFIG_FILE.lstat()
    except FileNotFoundError:
        raise PetlibroError("config_missing", "Petlibro configuration is unavailable")
    if (
        not stat.S_ISREG(info.st_mode)
        or stat.S_ISLNK(info.st_mode)
        or info.st_uid != os.getuid()
        or stat.S_IMODE(info.st_mode) != 0o600
    ):
        raise PetlibroError(
            "config_unsafe",
            "Petlibro configuration must be an owner-only mode-0600 regular file",
        )

    config: dict[str, str] = {}
    try:
        lines = CONFIG_FILE.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeDecodeError):
        raise PetlibroError("config_unreadable", "Petlibro configuration could not be read")
    for line_number, raw_line in enumerate(lines, 1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if ":" not in line or raw_line[:1].isspace():
            raise PetlibroError(
                "config_invalid",
                "Petlibro configuration must use flat key/value entries",
                line=line_number,
            )
        key, value = line.split(":", 1)
        key = key.strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
            value = value[1:-1]
        if not key or key in config:
            raise PetlibroError("config_invalid", "Petlibro configuration contains an invalid key")
        config[key] = value

    missing = [key for key in ("email", "password") if not config.get(key)]
    if missing:
        raise PetlibroError(
            "config_missing_fields",
            "Petlibro configuration is missing required credentials",
            fields=missing,
        )
    return config


def require_appsn() -> str:
    appsn = os.environ.get("PETLIBRO_APPSN", "").strip()
    if not appsn:
        raise PetlibroError(
            "environment_missing",
            "PETLIBRO_APPSN is required for Petlibro authentication",
        )
    return appsn


def api_post(endpoint: str, body: dict | None = None, token: str | None = None) -> dict:
    headers = {
        "Content-Type": "application/json",
        "source": "ANDROID",
        "language": "EN",
        "version": "1.3.45",
    }
    if token:
        headers["token"] = token
    request = urllib.request.Request(
        f"{BASE_URL}{endpoint}",
        data=json.dumps(body or {}, separators=(",", ":")).encode("utf-8"),
        headers=headers,
    )
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            raw = response.read()
    except urllib.error.HTTPError as error:
        raise PetlibroError(
            "http_error",
            "Petlibro returned an HTTP error",
            http_status=error.code,
        )
    except (urllib.error.URLError, socket.timeout, TimeoutError, OSError):
        raise PetlibroError("network_error", "Petlibro could not be reached")

    try:
        result = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise PetlibroError("invalid_response", "Petlibro returned invalid JSON")
    if not isinstance(result, dict):
        raise PetlibroError("invalid_response", "Petlibro returned an unexpected response")
    return result


def require_api_success(result: dict, operation: str, *, require_data: bool = True) -> object:
    code = result.get("code")
    if code != 0:
        fields: dict[str, object] = {}
        if isinstance(code, (int, str)):
            fields["api_code"] = code
        raise PetlibroError("api_error", f"Petlibro rejected the {operation} request", **fields)
    if require_data and "data" not in result:
        raise PetlibroError("invalid_response", f"Petlibro omitted {operation} response data")
    return result.get("data")


def read_token_cache() -> str | None:
    try:
        info = TOKEN_FILE.lstat()
    except FileNotFoundError:
        return None
    if (
        not stat.S_ISREG(info.st_mode)
        or stat.S_ISLNK(info.st_mode)
        or info.st_uid != os.getuid()
        or info.st_mode & 0o077
    ):
        return None
    try:
        cached = json.loads(TOKEN_FILE.read_text(encoding="utf-8"))
        token = cached.get("token")
        cached_at = float(cached.get("cached_at", 0))
    except (OSError, ValueError, TypeError, json.JSONDecodeError, AttributeError):
        return None
    if not isinstance(token, str) or not token:
        return None
    if time.time() - cached_at >= TOKEN_LIFETIME_SECONDS - TOKEN_EXPIRY_BUFFER:
        return None
    return token


def authenticate(config: dict[str, str]) -> str:
    md5_password = hashlib.md5(config["password"].encode("utf-8")).hexdigest()
    result = api_post(
        "/member/auth/login",
        {
            "appId": APPID,
            "appSn": require_appsn(),
            "country": config.get("region", "US"),
            "email": config["email"],
            "password": md5_password,
            "timezone": config.get("timezone", "America/New_York"),
        },
    )
    try:
        token = result["data"]["token"] if result.get("code") == 0 else None
    except (KeyError, TypeError):
        token = None
    if not isinstance(token, str) or not token:
        code = result.get("code")
        fields = {"api_code": code} if isinstance(code, (int, str)) else {}
        raise PetlibroError("auth_failed", "Petlibro authentication failed", **fields)

    atomic_write_json(TOKEN_FILE, {"token": token, "cached_at": time.time()})
    return token


def get_token_and_devices() -> tuple[dict[str, str], str, list[dict]]:
    config = load_config()
    token = read_token_cache() or authenticate(config)
    result = api_post("/device/device/list", {}, token)
    if result.get("code") == 1009:
        token = authenticate(config)
        result = api_post("/device/device/list", {}, token)
    data = require_api_success(result, "device list")
    if not isinstance(data, list) or not all(isinstance(item, dict) for item in data):
        raise PetlibroError("invalid_response", "Petlibro returned an invalid device list")
    return config, token, data


def device_type(device: dict) -> str:
    product = str(device.get("productName", "")).casefold()
    identifier = str(device.get("productIdentifier", "")).upper()
    if "feeder" in product or identifier.startswith("PLAF"):
        return "feeder"
    if "fountain" in product or identifier.startswith("PLWF"):
        return "fountain"
    return "unknown"


def device_online(device: dict) -> bool:
    return device.get("online") in (True, 1, "1", "true", "online")


def resolve_device(
    config: dict[str, str],
    devices: list[dict],
    selector: str,
    expected_type: str,
) -> tuple[dict, str]:
    definition = DEVICE_SELECTORS.get(selector)
    if definition is None:
        raise PetlibroError(
            "invalid_device_selector",
            "Use an exact location-specific Petlibro device selector",
            allowed=sorted(DEVICE_SELECTORS),
        )
    config_key, selector_type, location = definition
    if selector_type != expected_type:
        raise PetlibroError(
            "wrong_device_type",
            f"{selector} is not a {expected_type}",
        )
    identity = config.get(config_key, "").strip()
    if not identity:
        raise PetlibroError(
            "device_mapping_missing",
            f"No configured Petlibro identity exists for {selector}",
        )

    matches = [
        device
        for device in devices
        if str(device.get("deviceSn", "")) == identity
        or str(device.get("name", "")).casefold() == identity.casefold()
    ]
    if not matches:
        raise PetlibroError("device_not_found", f"Configured {selector} device was not found")
    if len(matches) != 1:
        raise PetlibroError("device_ambiguous", f"Configured {selector} identity is not unique")
    device = matches[0]
    if device_type(device) != expected_type:
        raise PetlibroError("wrong_device_type", f"Configured {selector} is not a {expected_type}")
    if not device_online(device):
        raise PetlibroError("device_offline", f"{selector} is offline")
    if not str(device.get("deviceSn", "")).strip():
        raise PetlibroError("device_identity_missing", f"{selector} has no device serial")
    return device, location


def integer_env(name: str, default: int, minimum: int, maximum: int) -> int:
    raw = os.environ.get(name, str(default))
    try:
        value = int(raw)
    except ValueError:
        raise PetlibroError("environment_invalid", f"{name} must be an integer")
    if value < minimum or value > maximum:
        raise PetlibroError(
            "environment_invalid",
            f"{name} must be between {minimum} and {maximum}",
        )
    return value


def validate_portions(value: str) -> int:
    minimum = integer_env("PETLIBRO_MIN_PORTIONS", DEFAULT_MIN_PORTIONS, 1, HARD_MAX_PORTIONS)
    maximum = integer_env("PETLIBRO_MAX_PORTIONS", DEFAULT_MAX_PORTIONS, 1, HARD_MAX_PORTIONS)
    if minimum > maximum:
        raise PetlibroError(
            "environment_invalid",
            "PETLIBRO_MIN_PORTIONS cannot exceed PETLIBRO_MAX_PORTIONS",
        )
    try:
        portions = int(value)
    except ValueError:
        raise PetlibroError("invalid_portions", "Portions must be an integer")
    if portions < minimum or portions > maximum:
        raise PetlibroError(
            "invalid_portions",
            f"Portions must be between {minimum} and {maximum}",
            minimum=minimum,
            maximum=maximum,
        )
    return portions


def validate_scheduled_portions(value: str) -> int:
    try:
        portions = int(value)
    except ValueError:
        raise PetlibroError(
            "invalid_scheduled_portions",
            f"Scheduled portions must be between 1 and {MAX_SCHEDULED_PORTIONS}",
        )
    if not 1 <= portions <= MAX_SCHEDULED_PORTIONS:
        raise PetlibroError(
            "invalid_scheduled_portions",
            f"Scheduled portions must be between 1 and {MAX_SCHEDULED_PORTIONS}",
            minimum=1,
            maximum=MAX_SCHEDULED_PORTIONS,
        )
    return portions


def validate_scheduled_time(value: str) -> str:
    parts = value.split(":")
    if (
        len(parts) != 2
        or len(parts[0]) != 2
        or len(parts[1]) != 2
        or not all(part.isdigit() for part in parts)
        or not 0 <= int(parts[0]) <= 23
        or not 0 <= int(parts[1]) <= 59
    ):
        raise PetlibroError(
            "invalid_scheduled_time",
            "Scheduled time must use 24-hour HH:MM format",
        )
    return value


def load_feed_state() -> dict:
    try:
        info = FEED_STATE_FILE.lstat()
    except FileNotFoundError:
        return {"version": 1, "feeds": {}}
    if (
        not stat.S_ISREG(info.st_mode)
        or stat.S_ISLNK(info.st_mode)
        or info.st_uid != os.getuid()
        or info.st_mode & 0o077
    ):
        raise PetlibroError("feed_state_unsafe", "Manual-feed state is not protected")
    try:
        state = json.loads(FEED_STATE_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        raise PetlibroError("feed_state_invalid", "Manual-feed state is invalid")
    if not isinstance(state, dict) or state.get("version") != 1:
        raise PetlibroError("feed_state_invalid", "Manual-feed state is invalid")
    if not isinstance(state.get("feeds", {}), dict):
        raise PetlibroError("feed_state_invalid", "Manual-feed state is invalid")
    return state


class FeedAttempt:
    """Hold an exclusive feed lock from guard check through API outcome."""

    def __init__(
        self,
        lock_fd: int,
        state: dict,
        device_key: str,
        selector: str,
        request_id: str,
    ) -> None:
        self.lock_fd = lock_fd
        self.state = state
        self.device_key = device_key
        self.selector = selector
        self.request_id = request_id
        self.closed = False

    @classmethod
    def begin(cls, selector: str, portions: int, device_serial: str) -> "FeedAttempt":
        ensure_private_dir(FEED_STATE_DIR)
        flags = os.O_CREAT | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0)
        try:
            lock_fd = os.open(FEED_LOCK_FILE, flags, 0o600)
        except OSError:
            raise PetlibroError("feed_state_unavailable", "Manual-feed lock is unavailable")
        os.fchmod(lock_fd, 0o600)
        fcntl.flock(lock_fd, fcntl.LOCK_EX)
        try:
            state = load_feed_state()
            cooldown = integer_env(
                "PETLIBRO_FEED_COOLDOWN_SECONDS",
                DEFAULT_FEED_COOLDOWN_SECONDS,
                30,
                3600,
            )
            now = int(time.time())
            device_key = hashlib.sha256(device_serial.encode("utf-8")).hexdigest()
            previous = state.get("feeds", {}).get(device_key, {})
            attempted_at = int(previous.get("attempted_at", 0)) if isinstance(previous, dict) else 0
            remaining = cooldown - (now - attempted_at)
            if remaining > 0:
                raise PetlibroError(
                    "feed_cooldown",
                    f"A recent {selector} feed attempt is still within cooldown",
                    retry_after_seconds=remaining,
                    non_retryable=True,
                )
            request_id = secrets.token_urlsafe(12)
            state.setdefault("feeds", {})[device_key] = {
                "selector": selector,
                "request_id": request_id,
                "attempted_at": now,
                "portions": portions,
                "status": "attempting",
            }
            atomic_write_json(FEED_STATE_FILE, state)
            return cls(lock_fd, state, device_key, selector, request_id)
        except Exception:
            fcntl.flock(lock_fd, fcntl.LOCK_UN)
            os.close(lock_fd)
            raise

    def finish(self, status_value: str) -> None:
        if self.closed:
            return
        try:
            record = self.state["feeds"][self.device_key]
            record["status"] = status_value
            record["completed_at"] = int(time.time())
            atomic_write_json(FEED_STATE_FILE, self.state)
        finally:
            fcntl.flock(self.lock_fd, fcntl.LOCK_UN)
            os.close(self.lock_fd)
            self.closed = True

    def close(self) -> None:
        if not self.closed:
            fcntl.flock(self.lock_fd, fcntl.LOCK_UN)
            os.close(self.lock_fd)
            self.closed = True


class ScheduleAttempt:
    """Serialize whole-schedule mutations and durably record their outcome."""

    def __init__(self, lock_fd: int) -> None:
        self.lock_fd = lock_fd
        self.request_id: str | None = None
        self.record: dict[str, object] | None = None
        self.closed = False

    @classmethod
    def begin(cls) -> "ScheduleAttempt":
        ensure_private_dir(FEED_STATE_DIR)
        flags = os.O_CREAT | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0)
        try:
            lock_fd = os.open(SCHEDULE_LOCK_FILE, flags, 0o600)
        except OSError:
            raise PetlibroError(
                "schedule_state_unavailable",
                "Scheduled-feeding lock is unavailable",
            )
        os.fchmod(lock_fd, 0o600)
        fcntl.flock(lock_fd, fcntl.LOCK_EX)
        return cls(lock_fd)

    def mark_attempting(
        self,
        selector: str,
        requested_enabled: bool,
        device_serial: str,
    ) -> None:
        self.request_id = secrets.token_urlsafe(12)
        self.record = {
            "schema_version": 1,
            "selector": selector,
            "device_key": hashlib.sha256(device_serial.encode("utf-8")).hexdigest(),
            "request_id": self.request_id,
            "requested_enabled": requested_enabled,
            "attempted_at": int(time.time()),
            "status": "attempting",
        }
        atomic_write_json(SCHEDULE_STATE_FILE, self.record)

    def mark_portion_attempting(
        self,
        selector: str,
        requested_portions: int,
        device_serial: str,
    ) -> None:
        self.request_id = secrets.token_urlsafe(12)
        self.record = {
            "schema_version": 1,
            "action": "feeding_plan_portions_update",
            "selector": selector,
            "device_key": hashlib.sha256(device_serial.encode("utf-8")).hexdigest(),
            "request_id": self.request_id,
            "requested_portions": requested_portions,
            "attempted_at": int(time.time()),
            "status": "attempting",
        }
        atomic_write_json(SCHEDULE_STATE_FILE, self.record)

    def finish(
        self,
        status_value: str,
        *,
        observed_enabled: bool | None = None,
        observed_portions: int | None = None,
    ) -> None:
        if self.record is None:
            return
        self.record["status"] = status_value
        self.record["completed_at"] = int(time.time())
        if observed_enabled is not None:
            self.record["observed_enabled"] = observed_enabled
        if observed_portions is not None:
            self.record["observed_portions"] = observed_portions
        atomic_write_json(SCHEDULE_STATE_FILE, self.record)

    def close(self) -> None:
        if not self.closed:
            fcntl.flock(self.lock_fd, fcntl.LOCK_UN)
            os.close(self.lock_fd)
            self.closed = True


def alias_for_device(config: dict[str, str], device: dict) -> str | None:
    serial = str(device.get("deviceSn", ""))
    name = str(device.get("name", "")).casefold()
    aliases = []
    for selector, (config_key, _, _) in DEVICE_SELECTORS.items():
        identity = config.get(config_key, "").strip()
        if identity and (identity == serial or identity.casefold() == name):
            aliases.append(selector)
    return aliases[0] if len(aliases) == 1 else None


def read_feeding_schedule_state(token: str, device: dict) -> bool:
    result = api_post(
        "/device/device/baseInfo",
        {"deviceSn": device["deviceSn"], "id": device["deviceSn"]},
        token,
    )
    data = require_api_success(result, "scheduled-feeding state")
    if not isinstance(data, dict) or not isinstance(data.get("enableFeedingPlan"), bool):
        raise PetlibroError(
            "invalid_response",
            "Petlibro returned an invalid scheduled-feeding state",
        )
    return data["enableFeedingPlan"]


def _feeding_plan_records(value: object) -> list[dict]:
    """Return explicit provider meal records; reject ambiguous shapes."""
    if isinstance(value, list):
        records = value
    elif isinstance(value, dict):
        candidates = [
            value.get(key)
            for key in ("feedingPlanList", "planList", "plans", "list")
            if key in value
        ]
        if len(candidates) != 1 or not isinstance(candidates[0], list):
            raise PetlibroError(
                "invalid_response",
                "Petlibro returned an unrecognized feeding-schedule shape",
            )
        records = candidates[0]
    else:
        raise PetlibroError(
            "invalid_response",
            "Petlibro returned an invalid feeding schedule",
        )
    for record in records:
        if not isinstance(record, dict):
            raise PetlibroError(
                "invalid_response",
                "Petlibro returned an invalid feeding-schedule entry",
            )
    return records


def _enabled_meal_count(value: object) -> int:
    """Count only explicit provider meal records; reject ambiguous shapes."""
    records = _feeding_plan_records(value)
    count = 0
    for record in records:
        enabled_values = [
            record[key] for key in ("enable", "enabled", "isEnabled") if key in record
        ]
        if len(enabled_values) > 1 or any(not isinstance(item, bool) for item in enabled_values):
            raise PetlibroError(
                "invalid_response",
                "Petlibro returned an ambiguous feeding-schedule entry",
            )
        if not enabled_values or enabled_values[0]:
            count += 1
    return count


def read_single_feeding_plan(
    token: str,
    device: dict,
    *,
    scheduled_time: str | None = None,
) -> dict[str, object]:
    result = api_post(
        "/device/feedingPlan/list",
        {"deviceSn": device["deviceSn"], "id": device["deviceSn"]},
        token,
    )
    records = _feeding_plan_records(require_api_success(result, "feeding schedule"))
    if scheduled_time is not None:
        records = [
            record
            for record in records
            if record.get("executionTime") == scheduled_time
        ]
    if len(records) != 1:
        message = (
            f"The feeder must have exactly one saved meal at {scheduled_time}"
            if scheduled_time is not None
            else "The feeder must have exactly one saved meal before its portions can be changed"
        )
        raise PetlibroError(
            "schedule_plan_ambiguous",
            message,
            plan_count=len(records),
        )
    plan = records[0]
    identifiers = [plan[key] for key in ("id", "planId") if key in plan]
    if (
        len(identifiers) != 1
        or isinstance(identifiers[0], bool)
        or not isinstance(identifiers[0], (int, str))
        or not str(identifiers[0]).strip()
    ):
        raise PetlibroError(
            "invalid_response",
            "Petlibro returned an invalid feeding-plan identity",
        )
    execution_time = plan.get("executionTime")
    if not isinstance(execution_time, str) or not execution_time.strip():
        raise PetlibroError(
            "invalid_response",
            "Petlibro returned an invalid feeding-plan time",
        )
    current_portions = plan.get("grainNum")
    if (
        not isinstance(current_portions, int)
        or isinstance(current_portions, bool)
        or not 1 <= current_portions <= MAX_SCHEDULED_PORTIONS
    ):
        raise PetlibroError(
            "invalid_response",
            "Petlibro returned invalid scheduled portions",
        )

    repeat_day = plan.get("repeatDay", "[]")
    if isinstance(repeat_day, list) and all(
        isinstance(day, int) and not isinstance(day, bool) for day in repeat_day
    ):
        repeat_day = "[" + ",".join(str(day) for day in repeat_day) + "]"
    if not isinstance(repeat_day, str):
        raise PetlibroError(
            "invalid_response",
            "Petlibro returned invalid feeding-plan repeat days",
        )

    label = plan.get("label", "")
    enabled = plan.get("enable", True)
    enable_audio = plan.get("enableAudio", False)
    audio_times = plan.get("audioTimes", 2)
    if (
        not isinstance(label, str)
        or not isinstance(enabled, bool)
        or not isinstance(enable_audio, bool)
        or not isinstance(audio_times, int)
        or isinstance(audio_times, bool)
    ):
        raise PetlibroError(
            "invalid_response",
            "Petlibro returned invalid feeding-plan settings",
        )
    return {
        "id": identifiers[0],
        "executionTime": execution_time,
        "repeatDay": repeat_day,
        "label": label,
        "enable": enabled,
        "enableAudio": enable_audio,
        "audioTimes": audio_times,
        "grainNum": current_portions,
    }


def cmd_schedule_state(selector: str) -> dict:
    config, token, devices = get_token_and_devices()
    device, location = resolve_device(config, devices, selector, "feeder")
    enabled = read_feeding_schedule_state(token, device)
    enabled_meal_count = 0
    if enabled:
        result = api_post(
            "/device/feedingPlan/list",
            {"deviceSn": device["deviceSn"], "id": device["deviceSn"]},
            token,
        )
        schedule = require_api_success(result, "feeding schedule")
        enabled_meal_count = _enabled_meal_count(schedule)
    return {
        "success": True,
        "selector": selector,
        "site": location,
        "online": True,
        "scheduleEnabled": enabled,
        "enabledMealCount": enabled_meal_count,
        "observedAt": datetime.now(timezone.utc)
        .isoformat(timespec="seconds")
        .replace("+00:00", "Z"),
    }


def cmd_status() -> list[dict]:
    config, token, devices = get_token_and_devices()
    output = []
    for device in devices:
        kind = device_type(device)
        selector = alias_for_device(config, device) or "unmapped"
        item = {
            "selector": selector,
            "name": device.get("name", "?"),
            "model": device.get("productIdentifier", "?"),
            "type": kind,
            "online": device_online(device),
            "wifi": device.get("wifiRssiLevel", "?"),
        }
        if kind == "feeder":
            item.update(
                {
                    "foodLevel": device.get("warehouseSurplusGrain", "?"),
                    "nextFeedTime": device.get("nextFeedingTime", "?"),
                    "nextFeedPortions": device.get("nextFeedingQuantity", "?"),
                    "bowlMode": device.get("bowlMode"),
                    "scheduleEnabled": None,
                    "scheduleState": "unavailable",
                }
            )
            if item["online"] and selector != "unmapped":
                try:
                    schedule_enabled = read_feeding_schedule_state(token, device)
                except PetlibroError:
                    pass
                else:
                    item["scheduleEnabled"] = schedule_enabled
                    item["scheduleState"] = "enabled" if schedule_enabled else "disabled"
        elif kind == "fountain":
            item.update(
                {
                    "waterWeight": device.get("weight", "?"),
                    "waterPercent": device.get("weightPercent", "?"),
                    "todayDrinkMl": device.get("todayTotalMl", "?"),
                    "battery": device.get("electricQuantity", "?"),
                    "batteryState": device.get("batteryState", "?"),
                    "filterDaysRemaining": device.get("remainingReplacementDays", "?"),
                }
            )
        output.append(item)
    return output


def cmd_devices() -> list[dict]:
    config, _, devices = get_token_and_devices()
    return [
        {
            "selector": alias_for_device(config, device) or "unmapped",
            "name": device.get("name", "?"),
            "model": device.get("productIdentifier", "?"),
            "productName": device.get("productName", "?"),
            "type": device_type(device),
            "online": device_online(device),
            "firmware": device.get("softwareVersion", "?"),
        }
        for device in devices
    ]


def cmd_feed(selector: str, portions: int) -> dict:
    config, token, devices = get_token_and_devices()
    device, location = resolve_device(config, devices, selector, "feeder")
    attempt = FeedAttempt.begin(selector, portions, str(device["deviceSn"]))
    try:
        try:
            result = api_post(
                "/device/device/manualFeeding",
                {"deviceSn": device["deviceSn"], "grainNum": portions},
                token,
            )
            require_api_success(result, "manual feeding", require_data=False)
        except PetlibroError as error:
            try:
                attempt.finish("unknown")
            except Exception:
                attempt.close()
            raise PetlibroError(
                "feed_outcome_unknown",
                "The manual-feed outcome is uncertain; do not retry during cooldown",
                cause=error.code,
                non_retryable=True,
                feed_may_have_occurred=True,
                request_id=attempt.request_id,
            )
        try:
            attempt.finish("success")
        except Exception:
            attempt.close()
            raise PetlibroError(
                "feed_outcome_unknown",
                "Feeding may have succeeded but local outcome state could not be saved",
                non_retryable=True,
                feed_may_have_occurred=True,
                request_id=attempt.request_id,
            )
    finally:
        attempt.close()
    return {
        "success": True,
        "device": selector,
        "location": location,
        "portions": portions,
        "request_id": attempt.request_id,
    }


def cmd_water(selector: str) -> object:
    config, token, devices = get_token_and_devices()
    device, _ = resolve_device(config, devices, selector, "fountain")
    result = api_post(
        "/data/deviceDrinkWater/todayDrinkData",
        {"deviceSn": device["deviceSn"]},
        token,
    )
    return require_api_success(result, "water data")


def cmd_schedule(selector: str) -> object:
    config, token, devices = get_token_and_devices()
    device, _ = resolve_device(config, devices, selector, "feeder")
    result = api_post(
        "/device/feedingPlan/todayNew",
        {"deviceSn": device["deviceSn"], "id": device["deviceSn"]},
        token,
    )
    return require_api_success(result, "feeding schedule")


def validate_history_limit(value: str) -> int:
    try:
        limit = int(value)
    except ValueError:
        raise PetlibroError(
            "invalid_limit",
            f"Feeding history limit must be between 1 and {MAX_FEEDING_HISTORY_LIMIT}",
        )
    if not 1 <= limit <= MAX_FEEDING_HISTORY_LIMIT:
        raise PetlibroError(
            "invalid_limit",
            f"Feeding history limit must be between 1 and {MAX_FEEDING_HISTORY_LIMIT}",
        )
    return limit


def _scheduled_feeding_records(value: object, *, limit: int) -> list[dict[str, object]]:
    """Reduce provider work records to successful scheduled dispenses only."""
    if not isinstance(value, list):
        raise PetlibroError(
            "invalid_response", "Petlibro returned an invalid feeding history"
        )

    feedings: list[dict[str, object]] = []
    for day in value:
        if not isinstance(day, dict) or not isinstance(day.get("workRecords"), list):
            raise PetlibroError(
                "invalid_response", "Petlibro returned an invalid feeding-history day"
            )
        for record in day["workRecords"]:
            if not isinstance(record, dict):
                raise PetlibroError(
                    "invalid_response",
                    "Petlibro returned an invalid feeding-history entry",
                )
            if (
                record.get("type") != "GRAIN_OUTPUT_SUCCESS"
                or record.get("eventType") != "FEEDING_PLAN_SUCCESS"
            ):
                continue
            occurred_at_ms = record.get("recordTime")
            portions = record.get("actualGrainNum")
            if (
                not isinstance(occurred_at_ms, int)
                or isinstance(occurred_at_ms, bool)
                or not isinstance(portions, int)
                or isinstance(portions, bool)
                or not 1 <= portions <= MAX_SCHEDULED_PORTIONS
            ):
                raise PetlibroError(
                    "invalid_response",
                    "Petlibro returned an invalid scheduled-feeding record",
                )
            try:
                occurred_at = datetime.fromtimestamp(
                    occurred_at_ms / 1000, timezone.utc
                ).isoformat(timespec="seconds").replace("+00:00", "Z")
            except (OSError, OverflowError, ValueError):
                raise PetlibroError(
                    "invalid_response",
                    "Petlibro returned an invalid scheduled-feeding timestamp",
                )
            feedings.append({"occurredAt": occurred_at, "portions": portions})

    feedings.sort(key=lambda item: str(item["occurredAt"]), reverse=True)
    deduplicated: list[dict[str, object]] = []
    seen: set[tuple[object, object]] = set()
    for feeding in feedings:
        key = (feeding["occurredAt"], feeding["portions"])
        if key in seen:
            continue
        seen.add(key)
        deduplicated.append(feeding)
        if len(deduplicated) >= limit:
            break
    return deduplicated


def cmd_feeding_history(selector: str, limit: int) -> dict[str, object]:
    config, token, devices = get_token_and_devices()
    device, location = resolve_device(config, devices, selector, "feeder")
    now_ms = int(time.time() * 1000)
    result = api_post(
        "/device/workRecord/list",
        {
            "deviceSn": device["deviceSn"],
            "startTime": now_ms - FEEDING_HISTORY_DAYS * 24 * 60 * 60 * 1000,
            "endTime": now_ms,
            "size": MAX_FEEDING_HISTORY_LIMIT,
            "type": ["GRAIN_OUTPUT_SUCCESS"],
        },
        token,
    )
    history = require_api_success(result, "feeding history")
    return {
        "success": True,
        "selector": selector,
        "site": location,
        "feedings": _scheduled_feeding_records(history, limit=limit),
        "observedAt": datetime.now(timezone.utc)
        .isoformat(timespec="seconds")
        .replace("+00:00", "Z"),
    }


def validate_schedule_state(value: str) -> bool:
    if value == "on":
        return True
    if value == "off":
        return False
    raise PetlibroError(
        "invalid_schedule_state",
        "Scheduled feeding state must be on or off",
        allowed=["on", "off"],
    )


def cmd_schedule_set(selector: str, requested_enabled: bool) -> dict:
    config, token, devices = get_token_and_devices()
    device, location = resolve_device(config, devices, selector, "feeder")
    attempt = ScheduleAttempt.begin()
    try:
        before_enabled = read_feeding_schedule_state(token, device)
        if before_enabled == requested_enabled:
            return {
                "success": True,
                "device": selector,
                "location": location,
                "scheduleEnabled": requested_enabled,
                "action": "feeding_schedule_enabled" if requested_enabled else "feeding_schedule_disabled",
                "accepted": True,
                "verified": True,
                "mutation_attempted": False,
            }

        attempt.mark_attempting(selector, requested_enabled, str(device["deviceSn"]))
        try:
            result = api_post(
                "/device/setting/updateFeedingPlanSwitch",
                {"deviceSn": device["deviceSn"], "enable": requested_enabled},
                token,
            )
            require_api_success(result, "scheduled-feeding update", require_data=False)
        except PetlibroError as error:
            try:
                attempt.finish("unknown")
            except Exception:
                pass
            raise PetlibroError(
                "schedule_outcome_unknown",
                "The scheduled-feeding outcome is uncertain; inspect status before another change",
                cause=error.code,
                non_retryable=True,
                schedule_may_have_changed=True,
                request_id=attempt.request_id,
            )

        observed_enabled: bool | None = None
        verification_error: PetlibroError | None = None
        for verification_attempt in range(SCHEDULE_VERIFY_ATTEMPTS):
            try:
                observed_enabled = read_feeding_schedule_state(token, device)
                verification_error = None
            except PetlibroError as error:
                verification_error = error
            if observed_enabled == requested_enabled:
                break
            if verification_attempt + 1 < SCHEDULE_VERIFY_ATTEMPTS:
                time.sleep(SCHEDULE_VERIFY_INTERVAL_SECONDS)

        if observed_enabled != requested_enabled:
            try:
                attempt.finish("unknown", observed_enabled=observed_enabled)
            except Exception:
                pass
            fields: dict[str, object] = {
                "non_retryable": True,
                "schedule_may_have_changed": True,
                "request_id": attempt.request_id,
            }
            if verification_error is not None:
                fields["cause"] = verification_error.code
            raise PetlibroError(
                "schedule_outcome_unknown",
                "The scheduled-feeding update could not be verified; inspect status before another change",
                **fields,
            )

        try:
            attempt.finish("verified", observed_enabled=observed_enabled)
        except Exception:
            raise PetlibroError(
                "schedule_outcome_unknown",
                "The scheduled-feeding update was verified but its local audit record could not be saved",
                non_retryable=True,
                schedule_may_have_changed=True,
                request_id=attempt.request_id,
            )
        return {
            "success": True,
            "device": selector,
            "location": location,
            "scheduleEnabled": requested_enabled,
            "action": "feeding_schedule_enabled" if requested_enabled else "feeding_schedule_disabled",
            "accepted": True,
            "verified": True,
            "mutation_attempted": True,
            "request_id": attempt.request_id,
        }
    finally:
        attempt.close()


def cmd_schedule_portions_set(
    selector: str,
    requested_portions: int,
    *,
    scheduled_time: str | None = None,
) -> dict:
    config, token, devices = get_token_and_devices()
    device, location = resolve_device(config, devices, selector, "feeder")
    attempt = ScheduleAttempt.begin()
    try:
        before = read_single_feeding_plan(
            token,
            device,
            scheduled_time=scheduled_time,
        )
        if before["grainNum"] == requested_portions:
            return {
                "success": True,
                "device": selector,
                "location": location,
                "scheduledPortions": requested_portions,
                "scheduledTime": before["executionTime"],
                "accepted": True,
                "verified": True,
                "mutation_attempted": False,
            }

        attempt.mark_portion_attempting(
            selector,
            requested_portions,
            str(device["deviceSn"]),
        )
        payload = {
            **before,
            "deviceSn": device["deviceSn"],
            "grainNum": requested_portions,
            "petIds": [],
        }
        try:
            result = api_post("/device/feedingPlan/update", payload, token)
            require_api_success(result, "feeding-plan portion update", require_data=False)
        except PetlibroError as error:
            try:
                attempt.finish("unknown")
            except Exception:
                pass
            raise PetlibroError(
                "schedule_portions_outcome_unknown",
                "The scheduled-portion outcome is uncertain; inspect the schedule before another change",
                cause=error.code,
                non_retryable=True,
                schedule_may_have_changed=True,
                request_id=attempt.request_id,
            )

        observed: dict[str, object] | None = None
        verification_error: PetlibroError | None = None
        for verification_attempt in range(SCHEDULE_VERIFY_ATTEMPTS):
            try:
                observed = read_single_feeding_plan(
                    token,
                    device,
                    scheduled_time=scheduled_time,
                )
                verification_error = None
            except PetlibroError as error:
                verification_error = error
            if (
                observed is not None
                and observed["id"] == before["id"]
                and observed["grainNum"] == requested_portions
            ):
                break
            if verification_attempt + 1 < SCHEDULE_VERIFY_ATTEMPTS:
                time.sleep(SCHEDULE_VERIFY_INTERVAL_SECONDS)

        if (
            observed is None
            or observed["id"] != before["id"]
            or observed["grainNum"] != requested_portions
        ):
            observed_portions = (
                observed.get("grainNum") if isinstance(observed, dict) else None
            )
            try:
                attempt.finish(
                    "unknown",
                    observed_portions=(
                        observed_portions
                        if isinstance(observed_portions, int)
                        else None
                    ),
                )
            except Exception:
                pass
            fields: dict[str, object] = {
                "non_retryable": True,
                "schedule_may_have_changed": True,
                "request_id": attempt.request_id,
            }
            if verification_error is not None:
                fields["cause"] = verification_error.code
            raise PetlibroError(
                "schedule_portions_outcome_unknown",
                "The scheduled-portion update could not be verified; inspect the schedule before another change",
                **fields,
            )

        try:
            attempt.finish("verified", observed_portions=requested_portions)
        except Exception:
            raise PetlibroError(
                "schedule_portions_outcome_unknown",
                "The portion update was verified but its local audit record could not be saved",
                non_retryable=True,
                schedule_may_have_changed=True,
                request_id=attempt.request_id,
            )
        return {
            "success": True,
            "device": selector,
            "location": location,
            "scheduledPortions": requested_portions,
            "scheduledTime": observed["executionTime"],
            "accepted": True,
            "verified": True,
            "mutation_attempted": True,
            "request_id": attempt.request_id,
        }
    finally:
        attempt.close()


def require_arg_count(args: list[str], minimum: int, maximum: int, usage: str) -> None:
    if len(args) < minimum or len(args) > maximum:
        raise PetlibroError("invalid_arguments", usage)


def dispatch(argv: list[str]) -> object:
    if not argv:
        raise PetlibroError(
            "missing_command",
            "Usage: petlibro-api.py <status|feed|water|schedule|schedule-state|feeding-history|schedule-set|devices>",
        )
    command, args = argv[0], argv[1:]
    if command == "status":
        require_arg_count(args, 0, 0, "Usage: petlibro-api.py status")
        return cmd_status()
    if command == "devices":
        require_arg_count(args, 0, 0, "Usage: petlibro-api.py devices")
        return cmd_devices()
    if command == "feed":
        require_arg_count(
            args,
            1,
            2,
            "Usage: petlibro-api.py feed <location-feeder> [portions]",
        )
        return cmd_feed(args[0], validate_portions(args[1] if len(args) == 2 else "1"))
    if command == "water":
        require_arg_count(args, 1, 1, "Usage: petlibro-api.py water <location-fountain>")
        return cmd_water(args[0])
    if command == "schedule":
        require_arg_count(args, 1, 1, "Usage: petlibro-api.py schedule <location-feeder>")
        return cmd_schedule(args[0])
    if command == "schedule-state":
        require_arg_count(
            args,
            1,
            1,
            "Usage: petlibro-api.py schedule-state <location-feeder>",
        )
        return cmd_schedule_state(args[0])
    if command == "feeding-history":
        require_arg_count(
            args,
            1,
            2,
            "Usage: petlibro-api.py feeding-history <location-feeder> [limit]",
        )
        return cmd_feeding_history(
            args[0],
            validate_history_limit(
                args[1] if len(args) == 2 else str(DEFAULT_FEEDING_HISTORY_LIMIT)
            ),
        )
    if command == "schedule-set":
        require_arg_count(
            args,
            2,
            2,
            "Usage: petlibro-api.py schedule-set <location-feeder> <on|off>",
        )
        return cmd_schedule_set(args[0], validate_schedule_state(args[1]))
    if command == "schedule-portions-set":
        require_arg_count(
            args,
            2,
            3,
            "Usage: petlibro-api.py schedule-portions-set <location-feeder> [HH:MM] <1-48>",
        )
        return cmd_schedule_portions_set(
            args[0],
            validate_scheduled_portions(args[-1]),
            scheduled_time=(validate_scheduled_time(args[1]) if len(args) == 3 else None),
        )
    raise PetlibroError("unknown_command", f"Unknown Petlibro command: {command}")


def main(argv: list[str] | None = None) -> int:
    try:
        result = dispatch(list(sys.argv[1:] if argv is None else argv))
    except PetlibroError as error:
        print(json.dumps(error.payload(), separators=(",", ":")))
        return 1
    except Exception:
        print(
            json.dumps(
                {
                    "success": False,
                    "error": "internal_error",
                    "message": "Petlibro command failed safely",
                },
                separators=(",", ":"),
            )
        )
        return 1
    print(json.dumps(result, separators=(",", ":"), default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
