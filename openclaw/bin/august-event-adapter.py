#!/usr/bin/env python3
"""Read-only August observation adapter for the local home-events bus."""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import re
import secrets
import stat
import subprocess
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any


VERSION = 1
SAFE_ALIAS_RE = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
FAILURE_BACKOFF_SECONDS = (60, 120, 300, 600, 900)
NORMAL_POLL_SECONDS = 300
NORMAL_POLL_JITTER_SECONDS = 30
OBSERVE_TIMEOUT_SECONDS = 20
MAX_OUTPUT_BYTES = 4096


class AdapterError(Exception):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def timestamp(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def normal_poll_delay() -> int:
    """Spread normal polls across the scheduler window without weakening backoff."""

    width = (NORMAL_POLL_JITTER_SECONDS * 2) + 1
    return NORMAL_POLL_SECONDS + secrets.randbelow(width) - NORMAL_POLL_JITTER_SECONDS


def parse_timestamp(value: Any) -> datetime:
    if not isinstance(value, str) or len(value) > 64:
        raise AdapterError("invalid_observation_time")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise AdapterError("invalid_observation_time") from exc
    if parsed.tzinfo is None:
        raise AdapterError("invalid_observation_time")
    return parsed.astimezone(timezone.utc)


def protected_directory(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    stat_result = path.lstat()
    if path.is_symlink() or not path.is_dir() or stat_result.st_uid != os.getuid():
        raise AdapterError("unsafe_state_directory")
    if stat_result.st_mode & 0o077:
        os.chmod(path, 0o700)


def fsync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def atomic_json(path: Path, value: dict[str, Any]) -> None:
    protected_directory(path.parent)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    temporary = Path(temporary_name)
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(value, handle, separators=(",", ":"), sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        fsync_directory(path.parent)
    finally:
        temporary.unlink(missing_ok=True)


def load_json(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    stat_result = path.lstat()
    if path.is_symlink() or not path.is_file() or stat_result.st_uid != os.getuid():
        raise AdapterError("unsafe_state_file")
    if stat_result.st_mode & 0o077:
        raise AdapterError("unsafe_state_file")
    if stat_result.st_size <= 0 or stat_result.st_size > 1024 * 1024:
        raise AdapterError("invalid_state_file")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise AdapterError("invalid_state_file") from exc
    if not isinstance(value, dict):
        raise AdapterError("invalid_state_file")
    return value


def initial_state() -> dict[str, Any]:
    return {
        "version": VERSION,
        "observation": None,
        "last_good_at": None,
        "consecutive_failures": 0,
        "first_failure_at": None,
        "last_error_code": None,
        "offline_emitted": False,
        "next_poll_at": None,
    }


def validate_state(value: dict[str, Any] | None) -> dict[str, Any]:
    if value is None:
        return initial_state()
    expected = {
        "version",
        "observation",
        "last_good_at",
        "consecutive_failures",
        "first_failure_at",
        "last_error_code",
        "offline_emitted",
        "next_poll_at",
    }
    if set(value) != expected or value.get("version") != VERSION:
        raise AdapterError("state_version")
    failures = value.get("consecutive_failures")
    if (
        not isinstance(failures, int)
        or isinstance(failures, bool)
        or not 0 <= failures <= 1_000_000
    ):
        raise AdapterError("invalid_state_file")
    if not isinstance(value.get("offline_emitted"), bool):
        raise AdapterError("invalid_state_file")
    for key in ("last_good_at", "first_failure_at", "next_poll_at"):
        if value.get(key) is not None:
            value[key] = timestamp(parse_timestamp(value[key]))
    error_code = value.get("last_error_code")
    if error_code is not None and (
        not isinstance(error_code, str) or not SAFE_ALIAS_RE.fullmatch(error_code)
    ):
        raise AdapterError("invalid_state_file")
    observation = value.get("observation")
    if observation is not None:
        value["observation"] = validate_observation(
            {"ok": True, **observation}, allow_old=True
        )
    return value


def validate_observation(value: Any, *, allow_old: bool = False) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise AdapterError("invalid_observation")
    allowed = {
        "ok",
        "alias",
        "observed_at",
        "lock_state",
        "door_state",
        "battery_percent",
        "battery_zone",
    }
    if set(value) - allowed or value.get("ok") is not True:
        raise AdapterError("invalid_observation")
    alias = value.get("alias")
    if not isinstance(alias, str) or not SAFE_ALIAS_RE.fullmatch(alias):
        raise AdapterError("invalid_observation")
    if value.get("lock_state") not in {"locked", "unlocked"}:
        raise AdapterError("invalid_observation")
    if value.get("door_state") not in {"open", "closed"}:
        raise AdapterError("invalid_observation")
    observed_at = parse_timestamp(value.get("observed_at"))
    if not allow_old and observed_at > utc_now() + timedelta(minutes=5):
        raise AdapterError("invalid_observation_time")
    battery = value.get("battery_percent")
    if battery is not None and (not isinstance(battery, int) or isinstance(battery, bool) or not 0 <= battery <= 100):
        raise AdapterError("invalid_observation")
    zone = value.get("battery_zone")
    if zone is not None and zone not in {"low", "normal"}:
        raise AdapterError("invalid_observation")
    return {
        "alias": alias,
        "observed_at": timestamp(observed_at),
        "lock_state": value["lock_state"],
        "door_state": value["door_state"],
        **({"battery_percent": battery} if battery is not None else {}),
        **({"battery_zone": zone} if zone is not None else {}),
    }


def event_key(*parts: Any) -> str:
    material = "\x1f".join(str(part) for part in parts)
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def make_event(
    observation: dict[str, Any],
    event_type: str,
    entity_kind: str,
    from_state: Any,
    to_state: Any,
    previous_good_at: str | None,
    *,
    attributes: dict[str, Any] | None = None,
) -> dict[str, Any]:
    observed_at = observation["observed_at"]
    return {
        "source_event_id": event_key(
            "august", observation["alias"], event_type, from_state, to_state, observed_at
        ),
        "event_type": event_type,
        "site": "crosstown",
        "entity_kind": entity_kind,
        "entity_alias": observation["alias"],
        "occurred_at": observed_at,
        "observed_at": observed_at,
        "time_precision": "observed_interval",
        "attributes": attributes
        if attributes is not None
        else {
            "previous": from_state,
            "current": to_state,
            "not_before": previous_good_at,
            "not_after": observed_at,
        },
    }


def battery_zone(observation: dict[str, Any], previous_zone: str | None) -> str | None:
    battery = observation.get("battery_percent")
    if battery is None:
        return previous_zone
    if battery <= 20:
        return "low"
    if battery >= 25:
        return "normal"
    return previous_zone or "normal"


def transition_events(
    previous: dict[str, Any], observation: dict[str, Any], state: dict[str, Any]
) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    previous_good_at = state.get("last_good_at")
    if state.get("offline_emitted"):
        outage_seconds = 0
        if previous_good_at:
            outage_seconds = max(
                0,
                int(
                    (
                        parse_timestamp(observation["observed_at"])
                        - parse_timestamp(previous_good_at)
                    ).total_seconds()
                ),
            )
        events.append(
            make_event(
                observation,
                "source.recovered",
                "adapter",
                "unavailable",
                "available",
                previous_good_at,
                attributes={"outage_seconds": outage_seconds},
            )
        )
    if previous["lock_state"] != observation["lock_state"]:
        events.append(
            make_event(
                observation,
                f"lock.{observation['lock_state']}",
                "lock",
                previous["lock_state"],
                observation["lock_state"],
                previous_good_at,
            )
        )
    if previous["door_state"] != observation["door_state"]:
        event_type = "door.opened" if observation["door_state"] == "open" else "door.closed"
        events.append(
            make_event(
                observation,
                event_type,
                "door",
                previous["door_state"],
                observation["door_state"],
                previous_good_at,
            )
        )
    prior_zone = previous.get("battery_zone")
    next_zone = battery_zone(observation, prior_zone)
    observation["battery_zone"] = next_zone
    if prior_zone is not None and next_zone != prior_zone:
        events.append(
            make_event(
                observation,
                "device.battery_low" if next_zone == "low" else "device.battery_recovered",
                "battery",
                prior_zone,
                next_zone,
                previous_good_at,
                attributes={
                    "battery_percent": observation.get("battery_percent", 0),
                    "threshold": 20 if next_zone == "low" else 25,
                    "not_before": previous_good_at,
                    "not_after": observation["observed_at"],
                },
            )
        )
    return events


def publish(home_eventctl: str, event: dict[str, Any]) -> None:
    try:
        result = subprocess.run(
            [home_eventctl, "enqueue", "--source", "august"],
            input=json.dumps(event, separators=(",", ":")),
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


def publish_pending(
    pending_path: Path, state_path: Path, home_eventctl: str
) -> dict[str, Any] | None:
    pending = load_json(pending_path)
    if pending is None:
        return None
    if (
        set(pending) != {"version", "events", "state_after"}
        or pending.get("version") != VERSION
        or not isinstance(pending.get("events"), list)
        or len(pending["events"]) > 16
    ):
        raise AdapterError("invalid_pending_state")
    state_after = pending.get("state_after")
    if not isinstance(state_after, dict):
        raise AdapterError("invalid_pending_state")
    state_after = validate_state(state_after)
    for event in pending["events"]:
        if not isinstance(event, dict):
            raise AdapterError("invalid_pending_state")
        publish(home_eventctl, event)
    atomic_json(state_path, state_after)
    pending_path.unlink()
    fsync_directory(pending_path.parent)
    return state_after


def observe(august_bin: str) -> dict[str, Any]:
    try:
        result = subprocess.run(
            [august_bin, "observe"],
            capture_output=True,
            text=True,
            timeout=OBSERVE_TIMEOUT_SECONDS,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise AdapterError("observe_timeout") from exc
    except OSError as exc:
        raise AdapterError("observe_unavailable") from exc
    if result.returncode != 0:
        raise AdapterError("observe_failed")
    encoded = result.stdout.encode("utf-8", errors="replace")
    if not encoded or len(encoded) > MAX_OUTPUT_BYTES:
        raise AdapterError("invalid_observation")
    try:
        raw = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise AdapterError("invalid_observation") from exc
    return validate_observation(raw)


def failure_state(
    state: dict[str, Any], now: datetime, code: str
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    failures = int(state.get("consecutive_failures", 0)) + 1
    first_failure_at = state.get("first_failure_at") or timestamp(now)
    first_failure = parse_timestamp(first_failure_at)
    offline = bool(state.get("offline_emitted"))
    should_emit = not offline and (
        failures >= 3 or (now - first_failure).total_seconds() >= 600
    )
    backoff = FAILURE_BACKOFF_SECONDS[min(failures - 1, len(FAILURE_BACKOFF_SECONDS) - 1)]
    updated = {
        **state,
        "consecutive_failures": failures,
        "first_failure_at": first_failure_at,
        "last_error_code": code,
        "offline_emitted": offline or should_emit,
        "next_poll_at": timestamp(now + timedelta(seconds=backoff)),
    }
    events: list[dict[str, Any]] = []
    prior = state.get("observation")
    if should_emit and isinstance(prior, dict):
        synthetic = {**prior, "observed_at": timestamp(now)}
        events.append(
            make_event(
                synthetic,
                "source.unavailable",
                "adapter",
                "available",
                "unavailable",
                state.get("last_good_at"),
                attributes={
                    "failure_count": failures,
                    "reason_code": code,
                },
            )
        )
    return updated, events


def run_once() -> dict[str, Any]:
    if os.environ.get("HOME_EVENTS_AUGUST_ENABLED", "0") != "1":
        return {"ok": True, "mode": "disabled"}

    root = Path(
        os.environ.get("HOME_EVENTS_ROOT", str(Path.home() / ".openclaw/home-events"))
    ).expanduser()
    state_dir = root / "state"
    protected_directory(root)
    protected_directory(state_dir)
    state_path = state_dir / "august-adapter.json"
    pending_path = state_dir / "august-adapter.pending.json"
    lock_path = state_dir / "august-adapter.lock"
    august_bin = os.environ.get("AUGUST_BIN", str(Path.home() / ".openclaw/bin/august"))
    home_eventctl = os.environ.get(
        "HOME_EVENTCTL", str(Path.home() / ".openclaw/bin/home-eventctl")
    )

    try:
        descriptor = os.open(
            lock_path,
            os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0),
            0o600,
        )
    except OSError as exc:
        raise AdapterError("unsafe_lock_file") from exc
    lock_metadata = os.fstat(descriptor)
    if (
        not stat.S_ISREG(lock_metadata.st_mode)
        or lock_metadata.st_uid != os.getuid()
        or lock_metadata.st_mode & 0o077
    ):
        os.close(descriptor)
        raise AdapterError("unsafe_lock_file")
    os.fchmod(descriptor, 0o600)
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        os.close(descriptor)
        return {"ok": True, "mode": "already_running"}

    try:
        state = publish_pending(pending_path, state_path, home_eventctl)
        if state is None:
            state = validate_state(load_json(state_path))
        now = utc_now()
        next_poll = state.get("next_poll_at")
        if next_poll and parse_timestamp(next_poll) > now:
            return {"ok": True, "mode": "not_due", "next_poll_at": next_poll}

        try:
            current = observe(august_bin)
        except AdapterError as exc:
            updated, events = failure_state(state, now, exc.code)
            if events:
                atomic_json(
                    pending_path,
                    {"version": VERSION, "events": events, "state_after": updated},
                )
                publish_pending(pending_path, state_path, home_eventctl)
            else:
                atomic_json(state_path, updated)
            return {
                "ok": False,
                "mode": "failure",
                "error_code": exc.code,
                "consecutive_failures": updated["consecutive_failures"],
            }

        current["battery_zone"] = battery_zone(
            current,
            (state.get("observation") or {}).get("battery_zone")
            if isinstance(state.get("observation"), dict)
            else None,
        )
        previous = state.get("observation")
        updated = {
            **state,
            "observation": current,
            "last_good_at": current["observed_at"],
            "consecutive_failures": 0,
            "first_failure_at": None,
            "last_error_code": None,
            "offline_emitted": False,
            "next_poll_at": timestamp(now + timedelta(seconds=normal_poll_delay())),
        }
        if not isinstance(previous, dict):
            atomic_json(state_path, updated)
            return {"ok": True, "mode": "baseline", "event_count": 0}

        events = transition_events(previous, current, state)
        updated["observation"] = current
        if events:
            atomic_json(
                pending_path,
                {"version": VERSION, "events": events, "state_after": updated},
            )
            publish_pending(pending_path, state_path, home_eventctl)
        else:
            atomic_json(state_path, updated)
        return {"ok": True, "mode": "observed", "event_count": len(events)}
    finally:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
        finally:
            os.close(descriptor)


def main() -> int:
    try:
        result = run_once()
    except AdapterError as exc:
        result = {"ok": False, "mode": "fatal", "error_code": exc.code}
    print(json.dumps(result, separators=(",", ":"), sort_keys=True))
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())
