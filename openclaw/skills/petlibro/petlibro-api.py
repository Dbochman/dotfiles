#!/usr/bin/env python3
"""Safe Petlibro API wrapper used by the OpenClaw Petlibro skill."""

from __future__ import annotations

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

    def finish(self, status_value: str, *, observed_enabled: bool | None = None) -> None:
        if self.record is None:
            return
        self.record["status"] = status_value
        self.record["completed_at"] = int(time.time())
        if observed_enabled is not None:
            self.record["observed_enabled"] = observed_enabled
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
        {"deviceSn": device["deviceSn"]},
        token,
    )
    return require_api_success(result, "feeding schedule")


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


def require_arg_count(args: list[str], minimum: int, maximum: int, usage: str) -> None:
    if len(args) < minimum or len(args) > maximum:
        raise PetlibroError("invalid_arguments", usage)


def dispatch(argv: list[str]) -> object:
    if not argv:
        raise PetlibroError(
            "missing_command",
            "Usage: petlibro-api.py <status|feed|water|schedule|schedule-set|devices>",
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
    if command == "schedule-set":
        require_arg_count(
            args,
            2,
            2,
            "Usage: petlibro-api.py schedule-set <location-feeder> <on|off>",
        )
        return cmd_schedule_set(args[0], validate_schedule_state(args[1]))
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
