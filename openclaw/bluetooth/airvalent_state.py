"""Private Airvalent cache and public, freshness-aware projections (stdlib only)."""
import json
import os
from pathlib import Path
import stat
import tempfile
from datetime import datetime, timezone

ALIAS = "crosstown-living-room-airvalent"
ORIGIN = "airvalent_ble_sampler_v1"
ROOM = "19Crosstown Living Room"
STRUCTURE = "19Crosstown"
STALE_SECONDS = 900
ERRORS = {"not_collected", "invalid_cache", "binding_invalid", "bridge_unavailable",
          "sensor_unavailable", "bond_unavailable", "reauth_required", "read_failed",
          "invalid_reading", "collection_timeout", "ble_connection_timeout", "collector_failed"}


def now_iso():
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def state_dir():
    return Path(os.environ.get("OPENCLAW_HOME", str(Path.home() / ".openclaw"))) / "airvalent"


def private_json(path):
    fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077 or info.st_size > 131072:
            raise ValueError("unsafe private file")
        with os.fdopen(fd, "r") as stream:
            fd = None
            value = json.load(stream)
        if not isinstance(value, dict):
            raise ValueError("invalid private object")
        return value
    finally:
        if fd is not None:
            os.close(fd)


def write_private(path, value):
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    info = path.parent.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
        raise ValueError("unsafe private directory")
    if path.is_symlink():
        raise ValueError("unsafe private file")
    fd, temporary = tempfile.mkstemp(prefix=".airvalent-", dir=path.parent)
    try:
        with os.fdopen(fd, "w") as stream:
            os.fchmod(stream.fileno(), 0o600)
            json.dump(value, stream, separators=(",", ":"), sort_keys=True)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def project_status(cache, now=None):
    now = now or datetime.now(timezone.utc)
    sample = cache.get("sample") or {}
    value = sample.get("co2_ppm") if isinstance(sample, dict) else None
    read_at = sample.get("read_at") if isinstance(sample, dict) else None
    age = None
    try:
        timestamp = datetime.fromisoformat(read_at.replace("Z", "+00:00"))
        if timestamp.tzinfo is not None:
            age = (now - timestamp).total_seconds()
    except (AttributeError, TypeError, ValueError):
        pass
    valid = type(value) is int and 250 <= value <= 10000 and age is not None and age >= -60
    stale = not valid or age > STALE_SECONDS
    error = cache.get("last_error")
    if error is not None and error not in ERRORS:
        error = "collector_failed"
    if not valid:
        error = error or "not_collected"
    online = valid and not stale and error is None
    return {"ok": online, "alias": ALIAS, "site": "crosstown", "room": "Living Room",
            "online": online, "stale": stale, "co2_ppm": value if online else None,
            "last_known_co2_ppm": value if valid else None,
            "read_at": read_at if valid else None, "measurement_at": None,
            "age_seconds": max(0, int(age)) if valid else None,
            "sample_interval_seconds": sample.get("sample_interval_seconds") if valid else None,
            "error": error, "history_ok": cache.get("history_error") is None}


def cached_status(path=None, now=None):
    try:
        cache = private_json(path or state_dir() / "status.json")
    except FileNotFoundError:
        cache = {"last_error": "not_collected"}
    except (OSError, ValueError):
        cache = {"last_error": "invalid_cache"}
    return project_status(cache, now)


def dashboard_room(status):
    return {"structure": STRUCTURE, "room": ROOM, "source": "airvalent", "mode": "sensor",
            "temp_f": None, "temp_c": None, "humidity": None, "hvac": None,
            "setpoint_f": None, "setpoint_c": None,
            "connectivity": "ONLINE" if status["online"] else "OFFLINE",
            "co2_ppm": status["co2_ppm"], "last_known_co2_ppm": status["last_known_co2_ppm"],
            "read_at": status["read_at"], "measurement_at": None,
            "stale": status["stale"], "age_seconds": status["age_seconds"], "error": status["error"]}
