#!/usr/bin/env python3
"""Publish future-only, privacy-bounded Litter-Robot activity to home-events."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
import tempfile
from typing import Any, Callable


SCHEMA_VERSION = 1
SITES = ("cabin", "crosstown")
SELECTORS = {
    "cabin": ("cabin-litter-robot", "cabin_litter_robot"),
    "crosstown": ("crosstown-litter-robot", "crosstown_litter_robot"),
}
CLASSIFICATIONS = frozenset({"cat_detected", "cat_sensor_interrupted"})
HASH_RE = re.compile(r"^[0-9a-f]{64}$")
MAX_OUTPUT_BYTES = 256 * 1024
MAX_STATE_BYTES = 512 * 1024
MAX_FINGERPRINTS = 256
OBSERVE_LIMIT = 100
OBSERVE_TIMEOUT_SECONDS = 30
FUTURE_TOLERANCE = timedelta(minutes=5)
MAX_ACTIVITY_AGE = timedelta(days=30)
HEALTH_STATES = frozenset(
    {"disabled", "baseline_required", "ok", "provider_unavailable", "history_gap"}
)
ERROR_CODES = frozenset(
    {
        "observer_unavailable",
        "observer_failed",
        "observer_output_invalid",
        "history_gap",
        "publisher_unavailable",
        "publisher_failed",
    }
)


class AdapterError(Exception):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace(
        "+00:00", "Z"
    )


def parse_time(value: object, code: str = "observer_output_invalid") -> datetime:
    if not isinstance(value, str) or not value or len(value) > 64:
        raise AdapterError(code)
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise AdapterError(code) from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise AdapterError(code)
    return parsed.astimezone(timezone.utc)


def format_time(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat(timespec="seconds").replace(
        "+00:00", "Z"
    )


def private_directory(path: Path, code: str) -> None:
    try:
        metadata = path.lstat()
    except OSError as exc:
        raise AdapterError(code) from exc
    if (
        stat.S_ISLNK(metadata.st_mode)
        or not stat.S_ISDIR(metadata.st_mode)
        or metadata.st_uid != os.geteuid()
        or stat.S_IMODE(metadata.st_mode) != 0o700
    ):
        raise AdapterError(code)


def atomic_json(path: Path, value: dict[str, Any]) -> None:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":")).encode() + b"\n"
    if len(encoded) > MAX_STATE_BYTES:
        raise AdapterError("state_capacity_exhausted")
    descriptor, name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary = Path(name)
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "wb", closefd=True) as handle:
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        directory = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        temporary.unlink(missing_ok=True)


def empty_state() -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "sites": {
            site: {
                "enabled": False,
                "baselined": False,
                "health": "disabled",
                "coverage_start": None,
                "last_successful_poll": None,
                "anchor": None,
                "fingerprints": [],
                "last_error": None,
            }
            for site in SITES
        },
    }


def validate_state(value: object) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != {"schema_version", "sites"}:
        raise AdapterError("state_invalid")
    if value["schema_version"] != SCHEMA_VERSION or not isinstance(value["sites"], dict):
        raise AdapterError("state_invalid")
    if set(value["sites"]) != set(SITES):
        raise AdapterError("state_invalid")
    normalized = empty_state()
    for site in SITES:
        record = value["sites"][site]
        if not isinstance(record, dict) or set(record) != set(normalized["sites"][site]):
            raise AdapterError("state_invalid")
        if (
            not isinstance(record["enabled"], bool)
            or not isinstance(record["baselined"], bool)
            or record["health"] not in HEALTH_STATES
            or record["last_error"] is not None
            and record["last_error"] not in ERROR_CODES
        ):
            raise AdapterError("state_invalid")
        for key in ("coverage_start", "last_successful_poll"):
            if record[key] is not None:
                parse_time(record[key], "state_invalid")
        if record["anchor"] is not None and (
            not isinstance(record["anchor"], str)
            or HASH_RE.fullmatch(record["anchor"]) is None
        ):
            raise AdapterError("state_invalid")
        fingerprints = record["fingerprints"]
        if (
            not isinstance(fingerprints, list)
            or len(fingerprints) > MAX_FINGERPRINTS
            or len(set(fingerprints)) != len(fingerprints)
            or any(not isinstance(item, str) or HASH_RE.fullmatch(item) is None for item in fingerprints)
        ):
            raise AdapterError("state_invalid")
        if record["baselined"] and (
            record["coverage_start"] is None or record["last_successful_poll"] is None
        ):
            raise AdapterError("state_invalid")
        normalized["sites"][site] = dict(record)
    return normalized


def load_state(path: Path) -> dict[str, Any]:
    if not path.exists() and not path.is_symlink():
        return empty_state()
    try:
        metadata = path.lstat()
    except OSError as exc:
        raise AdapterError("state_invalid") from exc
    if (
        stat.S_ISLNK(metadata.st_mode)
        or not stat.S_ISREG(metadata.st_mode)
        or metadata.st_uid != os.geteuid()
        or stat.S_IMODE(metadata.st_mode) != 0o600
        or metadata.st_nlink != 1
        or not 0 < metadata.st_size <= MAX_STATE_BYTES
    ):
        raise AdapterError("state_invalid")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise AdapterError("state_invalid") from exc
    return validate_state(value)


def quarantine_corrupt_state(path: Path) -> bool:
    """Preserve a structurally safe corrupt state file without auto-adopting it."""
    try:
        metadata = path.lstat()
    except OSError:
        return False
    if (
        not stat.S_ISREG(metadata.st_mode)
        or metadata.st_uid != os.geteuid()
        or stat.S_IMODE(metadata.st_mode) != 0o600
        or metadata.st_nlink != 1
        or not 0 < metadata.st_size <= MAX_STATE_BYTES
    ):
        return False
    try:
        digest = hashlib.sha256(path.read_bytes()).hexdigest()[:16]
        quarantine = path.with_name(f".{path.name}.invalid.{digest}")
        os.link(path, quarantine, follow_symlinks=False)
        path.unlink()
        directory = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
        return True
    except (FileExistsError, OSError):
        return False


def activity_fingerprint(site: str, activity: dict[str, str]) -> str:
    material = "\x1f".join(
        (site, activity["occurredAt"], activity["classification"])
    )
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def validate_observation(value: object, now: datetime) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != {"ok", "observedAt", "robots"}:
        raise AdapterError("observer_output_invalid")
    if value["ok"] is not True or not isinstance(value["robots"], list):
        raise AdapterError("observer_output_invalid")
    observed = parse_time(value["observedAt"])
    if observed > now + FUTURE_TOLERANCE or now - observed > timedelta(minutes=10):
        raise AdapterError("observer_output_invalid")
    if len(value["robots"]) != len(SITES):
        raise AdapterError("observer_output_invalid")
    robots: dict[str, Any] = {}
    for item in value["robots"]:
        if not isinstance(item, dict) or set(item) != {
            "selector",
            "site",
            "historyExhausted",
            "activities",
        }:
            raise AdapterError("observer_output_invalid")
        site = item["site"]
        if site not in SITES or site in robots or item["selector"] != SELECTORS[site][0]:
            raise AdapterError("observer_output_invalid")
        if not isinstance(item["historyExhausted"], bool) or not isinstance(
            item["activities"], list
        ):
            raise AdapterError("observer_output_invalid")
        if len(item["activities"]) > OBSERVE_LIMIT:
            raise AdapterError("observer_output_invalid")
        activities: list[dict[str, str]] = []
        previous: datetime | None = None
        seen: set[tuple[str, str]] = set()
        for activity in item["activities"]:
            if not isinstance(activity, dict) or set(activity) != {
                "occurredAt",
                "classification",
            }:
                raise AdapterError("observer_output_invalid")
            if activity["classification"] not in CLASSIFICATIONS:
                raise AdapterError("observer_output_invalid")
            occurred = parse_time(activity["occurredAt"])
            if occurred > observed + FUTURE_TOLERANCE or observed - occurred > MAX_ACTIVITY_AGE:
                raise AdapterError("observer_output_invalid")
            normalized = {
                "occurredAt": format_time(occurred),
                "classification": activity["classification"],
            }
            identity = (normalized["occurredAt"], normalized["classification"])
            if identity in seen or previous is not None and occurred > previous:
                raise AdapterError("observer_output_invalid")
            seen.add(identity)
            previous = occurred
            activities.append(normalized)
        robots[site] = {
            "historyExhausted": item["historyExhausted"],
            "activities": activities,
        }
    if set(robots) != set(SITES):
        raise AdapterError("observer_output_invalid")
    return {"observedAt": format_time(observed), "robots": robots}


def observe(binary: str, now: datetime) -> dict[str, Any]:
    try:
        result = subprocess.run(
            [binary, "--json", "observe", str(OBSERVE_LIMIT)],
            capture_output=True,
            text=True,
            timeout=OBSERVE_TIMEOUT_SECONDS,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise AdapterError("observer_unavailable") from exc
    if result.returncode != 0:
        raise AdapterError("observer_failed")
    if not result.stdout or len(result.stdout.encode("utf-8")) > MAX_OUTPUT_BYTES:
        raise AdapterError("observer_output_invalid")
    try:
        value = json.loads(result.stdout)
    except (json.JSONDecodeError, UnicodeError) as exc:
        raise AdapterError("observer_output_invalid") from exc
    return validate_observation(value, now)


def publish(binary: str, event: dict[str, Any]) -> None:
    try:
        result = subprocess.run(
            [binary, "enqueue", "--source", "whisker"],
            input=json.dumps(event, sort_keys=True, separators=(",", ":")),
            text=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise AdapterError("publisher_unavailable") from exc
    if result.returncode != 0:
        raise AdapterError("publisher_failed")


def run_once(clock: Callable[[], str] = utc_now) -> dict[str, Any]:
    enabled = {
        "cabin": os.environ.get("HOME_EVENTS_WHISKER_CABIN_ENABLED", "0"),
        "crosstown": os.environ.get("HOME_EVENTS_WHISKER_CROSSTOWN_ENABLED", "0"),
    }
    if any(value not in {"0", "1"} for value in enabled.values()):
        raise AdapterError("enable_flag_invalid")
    if not any(value == "1" for value in enabled.values()):
        return {"ok": True, "mode": "disabled"}
    root = Path(
        os.environ.get("HOME_EVENTS_ROOT", str(Path.home() / ".openclaw/home-events"))
    ).expanduser()
    state_dir = root / "state"
    private_directory(root, "runtime_unsafe")
    private_directory(state_dir, "runtime_unsafe")
    state_path = state_dir / "whisker-adapter.json"
    lock_path = state_dir / "whisker-adapter.lock"
    descriptor = os.open(
        lock_path,
        os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0),
        0o600,
    )
    try:
        os.fchmod(descriptor, 0o600)
        metadata = os.fstat(descriptor)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_uid != os.geteuid()
            or metadata.st_nlink != 1
        ):
            raise AdapterError("lock_unsafe")
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return {"ok": True, "mode": "already_running"}
        try:
            state = load_state(state_path)
        except AdapterError as exc:
            if exc.code == "state_invalid":
                quarantine_corrupt_state(state_path)
            raise
        for site in SITES:
            is_enabled = enabled[site] == "1"
            state["sites"][site]["enabled"] = is_enabled
            if not is_enabled:
                state["sites"][site]["health"] = "disabled"
                state["sites"][site]["last_error"] = None
        now = parse_time(clock(), "clock_invalid")
        try:
            observation = observe(
                os.environ.get(
                    "LITTER_ROBOT_BIN", str(Path.home() / ".openclaw/bin/litter-robot")
                ),
                now,
            )
        except AdapterError as exc:
            for site in SITES:
                if enabled[site] == "1":
                    state["sites"][site]["health"] = "provider_unavailable"
                    state["sites"][site]["last_error"] = exc.code
            atomic_json(state_path, state)
            raise

        baselined = 0
        published = 0
        home_eventctl = os.environ.get(
            "HOME_EVENTCTL", str(Path.home() / ".openclaw/bin/home-eventctl")
        )
        fingerprints_by_site = {
            site: [
                activity_fingerprint(site, item)
                for item in observation["robots"][site]["activities"]
            ]
            for site in SITES
            if enabled[site] == "1"
        }
        history_gap = any(
            state["sites"][site]["baselined"]
            and state["sites"][site]["anchor"] is not None
            and state["sites"][site]["anchor"] not in fingerprints_by_site[site]
            and not observation["robots"][site]["historyExhausted"]
            for site in fingerprints_by_site
        )
        if history_gap:
            for site in fingerprints_by_site:
                state["sites"][site]["health"] = "history_gap"
                state["sites"][site]["last_error"] = "history_gap"
            atomic_json(state_path, state)
            raise AdapterError("history_gap")
        for site in SITES:
            if enabled[site] != "1":
                continue
            record = state["sites"][site]
            robot = observation["robots"][site]
            activities = robot["activities"]
            fingerprints = fingerprints_by_site[site]
            if not record["baselined"]:
                record.update(
                    {
                        "baselined": True,
                        "health": "ok",
                        "coverage_start": observation["observedAt"],
                        "last_successful_poll": observation["observedAt"],
                        "anchor": fingerprints[0] if fingerprints else None,
                        "fingerprints": fingerprints[:MAX_FINGERPRINTS],
                        "last_error": None,
                    }
                )
                baselined += 1
                atomic_json(state_path, state)
                continue
            anchor = record["anchor"]
            known = set(record["fingerprints"])
            for activity, fingerprint in reversed(list(zip(activities, fingerprints))):
                if fingerprint in known:
                    continue
                event = {
                    "source_event_id": "v1:" + fingerprint,
                    "event_type": "pet.litter_box_activity",
                    "site": site,
                    "entity_kind": "litter_box",
                    "entity_alias": SELECTORS[site][1],
                    "occurred_at": activity["occurredAt"],
                    "observed_at": observation["observedAt"],
                    "time_precision": "source",
                    "attributes": {"classification": activity["classification"]},
                }
                publish(home_eventctl, event)
                known.add(fingerprint)
                record["fingerprints"] = ([fingerprint] + record["fingerprints"])[
                    :MAX_FINGERPRINTS
                ]
                atomic_json(state_path, state)
                published += 1
            record.update(
                {
                    "health": "ok",
                    "last_successful_poll": observation["observedAt"],
                    "anchor": fingerprints[0] if fingerprints else anchor,
                    "fingerprints": list(dict.fromkeys(fingerprints + record["fingerprints"]))[
                        :MAX_FINGERPRINTS
                    ],
                    "last_error": None,
                }
            )
            atomic_json(state_path, state)
        return {
            "ok": True,
            "mode": "baseline" if baselined else "observing",
            "baselined": baselined,
            "published": published,
        }
    finally:
        os.close(descriptor)


def main() -> int:
    try:
        result = run_once()
    except (AdapterError, OSError) as exc:
        code = exc.code if isinstance(exc, AdapterError) else "adapter_failed"
        print(json.dumps({"ok": False, "error_code": code}, separators=(",", ":")))
        return 1
    print(json.dumps(result, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    sys.exit(main())
