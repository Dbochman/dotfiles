#!/usr/bin/env python3
"""Stage and run tightly preauthorized restaurant reservation snipes.

The stage command only writes reviewable artifacts to a non-live directory.
The run command is intended for a per-user LaunchAgent after those artifacts
have been reviewed and explicitly deployed. Platform modules are loaded in a
cache-only environment and their 1Password fallback is disabled.
"""

from __future__ import annotations

import argparse
import contextlib
import fcntl
import hashlib
import importlib.machinery
import importlib.util
import json
import os
import plistlib
import re
import subprocess
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable


SCHEMA_VERSION = 1
PLATFORMS = {"opentable", "resy"}
MAX_GUARD_AGE_SECONDS = 15 * 60
ACTIVE_STATUSES = {"active", "booked", "confirmed", "pending", "reserved", "success"}
INACTIVE_STATUSES = {"cancelled", "canceled", "completed", "expired", "finished", "past"}
CONFIRMATION_KEYS = {
    "bookingid",
    "confirmationcode",
    "confirmationnumber",
    "reservationid",
    "resytoken",
}
CONFIRMED_STATUS_VALUES = {"booked", "confirmed", "reserved", "success", "succeeded"}
SLUG_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
TIME_RE = re.compile(r"^(?:[01]\d|2[0-3]):[0-5]\d$")


class SnipeError(ValueError):
    """A safe, user-facing validation or runtime error."""


def parse_timestamp(value: str, field: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise SnipeError(f"{field} must be an ISO-8601 timestamp") from exc
    if parsed.tzinfo is None:
        raise SnipeError(f"{field} must include a timezone")
    return parsed.astimezone(timezone.utc)


def timestamp(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def parse_date(value: str) -> str:
    try:
        return datetime.strptime(value, "%Y-%m-%d").date().isoformat()
    except ValueError as exc:
        raise SnipeError(f"invalid date: {value}") from exc


def minutes(value: str) -> int:
    if not TIME_RE.fullmatch(value):
        raise SnipeError(f"invalid time: {value}; expected HH:MM")
    hour, minute = (int(part) for part in value.split(":"))
    return hour * 60 + minute


def normalize_slot_start(value: Any) -> tuple[str, str] | None:
    if not isinstance(value, str):
        return None
    match = re.search(r"(\d{4}-\d{2}-\d{2})[T ](\d{2}:\d{2})", value)
    if not match:
        return None
    try:
        return parse_date(match.group(1)), f"{minutes(match.group(2)) // 60:02d}:{minutes(match.group(2)) % 60:02d}"
    except SnipeError:
        return None


def read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SnipeError(f"could not read valid JSON from {path}") from exc


def fsync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def ensure_directory(path: Path) -> None:
    missing: list[Path] = []
    cursor = path
    while not cursor.exists():
        missing.append(cursor)
        if cursor.parent == cursor:
            break
        cursor = cursor.parent
    path.mkdir(parents=True, exist_ok=True)
    for created in reversed(missing):
        fsync_directory(created.parent)


def durable_unlink(path: Path, *, missing_ok: bool = True) -> bool:
    try:
        path.unlink()
    except FileNotFoundError:
        if missing_ok:
            return False
        raise
    fsync_directory(path.parent)
    return True


def atomic_json(path: Path, data: Any, mode: int = 0o600) -> None:
    ensure_directory(path.parent)
    fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(data, handle, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fchmod(handle.fileno(), mode)
            os.fsync(handle.fileno())
        os.replace(temp_name, path)
        fsync_directory(path.parent)
    finally:
        with contextlib.suppress(FileNotFoundError):
            os.unlink(temp_name)


def atomic_bytes(path: Path, data: bytes, mode: int) -> None:
    ensure_directory(path.parent)
    fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fchmod(handle.fileno(), mode)
            os.fsync(handle.fileno())
        os.replace(temp_name, path)
        fsync_directory(path.parent)
    finally:
        with contextlib.suppress(FileNotFoundError):
            os.unlink(temp_name)


def safe_summary(payload: dict[str, Any], status: str, **extra: Any) -> dict[str, Any]:
    result = {
        "status": status,
        "job_id": payload.get("job_id"),
        "platform": payload.get("platform"),
        "venue_id": payload.get("venue", {}).get("id"),
    }
    result.update(extra)
    return result


def normalize_existing_reservations(raw: Any) -> list[dict[str, Any]]:
    if isinstance(raw, dict):
        raw = raw.get("reservations")
    if not isinstance(raw, list):
        raise SnipeError("existing reservations JSON must be a list or contain a reservations list")

    normalized: list[dict[str, Any]] = []
    for index, item in enumerate(raw):
        if not isinstance(item, dict):
            raise SnipeError(f"existing reservation {index} must be an object")
        date = parse_date(str(item.get("date", "")))
        time_value = str(item.get("time", ""))
        if time_value and not TIME_RE.fullmatch(time_value):
            raise SnipeError(f"existing reservation {index} has invalid time")
        platform = str(item.get("platform", "unknown")).lower()
        if platform not in PLATFORMS | {"unknown"}:
            raise SnipeError(f"existing reservation {index} has invalid platform")
        normalized.append(
            {
                "platform": platform,
                "venue_id": str(item.get("venue_id", "")),
                "date": date,
                "time": time_value,
                "party_size": int(item.get("party_size", 0) or 0),
                "status": str(item.get("status", "confirmed")).lower(),
            }
        )
    return normalized


def reservation_conflicts(
    reservations: Iterable[dict[str, Any]],
    dates: set[str],
    window_start: int,
    window_end: int,
) -> bool:
    for reservation in reservations:
        if reservation.get("date") not in dates:
            continue
        status = str(reservation.get("status", "confirmed")).lower()
        if status in INACTIVE_STATUSES:
            continue
        if status and status not in ACTIVE_STATUSES:
            # Unknown status is not safe to ignore.
            return True
        time_value = reservation.get("time")
        if not time_value:
            return True
        try:
            slot_minutes = minutes(str(time_value))
        except SnipeError:
            return True
        if window_start <= slot_minutes <= window_end:
            return True
    return False


def matching_existing_reservation(
    payload: dict[str, Any],
    reservations: Iterable[dict[str, Any]],
    expected_date: str | None = None,
    expected_time: str | None = None,
) -> dict[str, Any] | None:
    window_start = minutes(payload["time"]["window_start"])
    window_end = minutes(payload["time"]["window_end"])
    for item in reservations:
        status = str(item.get("status", "confirmed")).lower()
        if status in INACTIVE_STATUSES:
            continue
        if item.get("platform") not in (payload["platform"], "unknown"):
            continue
        if str(item.get("venue_id", "")) != payload["venue"]["id"]:
            continue
        if int(item.get("party_size", 0) or 0) != payload["party_size"]:
            continue
        if item.get("date") not in set(payload["dates"]):
            continue
        if expected_date is not None and item.get("date") != expected_date:
            continue
        time_value = item.get("time")
        if not isinstance(time_value, str) or not TIME_RE.fullmatch(time_value):
            continue
        if expected_time is not None and time_value != expected_time:
            continue
        if window_start <= minutes(time_value) <= window_end:
            return item
    return None


def job_fingerprint(payload: dict[str, Any]) -> str:
    core = {
        "platform": payload["platform"],
        "venue_id": payload["venue"]["id"],
        "dates": payload["dates"],
        "party_size": payload["party_size"],
        "time": payload["time"],
    }
    encoded = json.dumps(core, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()[:20]


def authorization_digest(payload: dict[str, Any]) -> str:
    canonical = {key: value for key, value in payload.items() if key != "authorization_digest"}
    encoded = json.dumps(canonical, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def validate_runtime_paths(payload: dict[str, Any]) -> None:
    home = Path(payload["runtime"]["home"]).resolve()
    slug = payload["slug"]
    label = payload["runtime"]["label"]
    expected_config = home / ".openclaw" / "restaurant-snipes" / "jobs" / slug / "authorization.json"
    expected_plist = home / "Library" / "LaunchAgents" / f"{label}.plist"
    expected_state = home / ".openclaw" / "restaurant-snipes" / "state" / payload["job_id"]
    if Path(payload["runtime"]["config"]).resolve() != expected_config.resolve():
        raise SnipeError("runtime config path is outside the managed job directory")
    if Path(payload["runtime"]["plist"]).resolve() != expected_plist.resolve():
        raise SnipeError("runtime plist path is outside the per-user LaunchAgents directory")
    if Path(payload["runtime"]["state_dir"]).resolve() != expected_state.resolve():
        raise SnipeError("runtime state path is outside the managed state directory")


def validate_payload(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict) or payload.get("schema_version") != SCHEMA_VERSION:
        raise SnipeError("unsupported authorization schema")
    if payload.get("platform") not in PLATFORMS:
        raise SnipeError("unsupported platform")
    if not SLUG_RE.fullmatch(str(payload.get("slug", ""))):
        raise SnipeError("invalid slug")
    if not payload.get("authorization_id") or not payload.get("approved_by"):
        raise SnipeError("explicit authorization metadata is required")
    dates = payload.get("dates")
    if not isinstance(dates, list) or not dates:
        raise SnipeError("at least one authorized date is required")
    normalized_dates = [parse_date(str(value)) for value in dates]
    if normalized_dates != sorted(set(normalized_dates)):
        raise SnipeError("authorized dates must be unique and sorted")
    party_size = payload.get("party_size")
    if not isinstance(party_size, int) or not 1 <= party_size <= 20:
        raise SnipeError("party size must be 1-20")

    time_policy = payload.get("time", {})
    target = minutes(str(time_policy.get("target", "")))
    window_start = minutes(str(time_policy.get("window_start", "")))
    window_end = minutes(str(time_policy.get("window_end", "")))
    max_delta = time_policy.get("max_delta_minutes")
    if window_end < window_start:
        raise SnipeError("time windows may not cross midnight")
    if not window_start <= target <= window_end:
        raise SnipeError("target time must fall inside the authorized window")
    if not isinstance(max_delta, int) or not 0 <= max_delta <= 180:
        raise SnipeError("max delta must be 0-180 minutes")

    monitoring = payload.get("monitoring", {})
    duration = monitoring.get("duration_seconds")
    poll_interval = monitoring.get("poll_interval_seconds")
    authorized_at = parse_timestamp(str(payload.get("authorized_at", "")), "authorized_at")
    expires_at = parse_timestamp(str(monitoring.get("expires_at", "")), "expires_at")
    if any(value < authorized_at.date().isoformat() for value in normalized_dates):
        raise SnipeError("authorized reservation dates may not precede authorization")
    if not isinstance(duration, int) or duration < 300 or duration > 7 * 24 * 3600:
        raise SnipeError("duration must be between 300 seconds and 7 days")
    if not isinstance(poll_interval, int) or poll_interval < 300 or poll_interval > 3600:
        raise SnipeError("poll interval must be 300-3600 seconds")
    if expires_at != authorized_at + timedelta(seconds=duration):
        raise SnipeError("expiry must equal authorized_at plus duration_seconds")
    if not payload.get("notification", {}).get("target"):
        raise SnipeError("an explicit notification target is required")

    guard = payload.get("existing_reservation_check", {})
    checked_at = parse_timestamp(str(guard.get("checked_at", "")), "existing reservation checked_at")
    if not guard.get("source") or not isinstance(guard.get("reservations"), list):
        raise SnipeError("existing reservation check source and normalized reservations are required")
    normalized_reservations = normalize_existing_reservations(guard["reservations"])
    if normalized_reservations != guard["reservations"]:
        raise SnipeError("existing reservation snapshot is not normalized")
    if checked_at > authorized_at + timedelta(minutes=1):
        raise SnipeError("existing reservation check cannot postdate authorization")
    if (authorized_at - checked_at).total_seconds() > MAX_GUARD_AGE_SECONDS:
        raise SnipeError("existing reservation check must be no more than 15 minutes old")
    if reservation_conflicts(guard["reservations"], set(normalized_dates), window_start, window_end):
        raise SnipeError("an existing reservation conflicts with the requested time window")

    venue = payload.get("venue", {})
    if not venue.get("id") or not venue.get("name"):
        raise SnipeError("venue id and verified venue name are required")
    if payload.get("job_id") != job_fingerprint(payload):
        raise SnipeError("job id does not match the authorized booking scope")
    validate_runtime_paths(payload)
    if payload.get("authorization_digest") != authorization_digest(payload):
        raise SnipeError("authorization digest does not match the staged manifest")
    return payload


def build_payload(args: argparse.Namespace) -> dict[str, Any]:
    authorized_at = parse_timestamp(args.authorized_at, "authorized_at")
    checked_at = parse_timestamp(args.existing_reservation_checked_at, "existing reservation checked_at")
    expires_at = parse_timestamp(args.expires_at, "expires_at")
    dates = sorted(set(parse_date(value) for value in args.date))
    reservations = normalize_existing_reservations(read_json(Path(args.existing_reservations_json)))
    home = Path(args.home).expanduser().resolve()
    label = f"ai.openclaw.restaurant-snipe.{args.slug}"
    runtime_config = home / ".openclaw" / "restaurant-snipes" / "jobs" / args.slug / "authorization.json"
    runtime_plist = home / "Library" / "LaunchAgents" / f"{label}.plist"

    payload: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "slug": args.slug,
        "authorization_id": args.authorization_id,
        "approved_by": args.approved_by,
        "authorized_at": timestamp(authorized_at),
        "platform": args.platform,
        "venue": {"id": str(args.venue_id), "name": args.venue_name},
        "dates": dates,
        "party_size": args.party_size,
        "time": {
            "target": args.target_time,
            "window_start": args.window_start,
            "window_end": args.window_end,
            "max_delta_minutes": args.max_delta_minutes,
        },
        "monitoring": {
            "duration_seconds": args.duration_seconds,
            "expires_at": timestamp(expires_at),
            "poll_interval_seconds": args.poll_interval_seconds,
        },
        "notification": {"target": args.notification_target},
        "existing_reservation_check": {
            "checked_at": timestamp(checked_at),
            "source": args.existing_reservation_source,
            "reservations": reservations,
        },
        "runtime": {
            "home": str(home),
            "label": label,
            "config": str(runtime_config),
            "plist": str(runtime_plist),
            "state_dir": "",
            "python": str(Path(args.python_path).expanduser()),
            "runner": str(Path(args.runner_path).expanduser()),
            "platform_module": str(Path(args.platform_module or f"/opt/homebrew/bin/{args.platform}").expanduser()),
            "launchctl": str(Path(args.launchctl_path).expanduser()),
            "imsg": str(Path(args.imsg_path).expanduser()),
        },
    }
    payload["job_id"] = job_fingerprint(payload)
    payload["runtime"]["state_dir"] = str(
        home / ".openclaw" / "restaurant-snipes" / "state" / payload["job_id"]
    )
    payload["authorization_digest"] = authorization_digest(payload)
    return validate_payload(payload)


def plist_for(payload: dict[str, Any]) -> bytes:
    runtime = payload["runtime"]
    home = runtime["home"]
    log_path = str(Path(home) / ".openclaw" / "logs" / f"{runtime['label']}.log")
    data = {
        "Label": runtime["label"],
        "ProgramArguments": [
            runtime["python"],
            runtime["runner"],
            "run",
            "--config",
            runtime["config"],
        ],
        "EnvironmentVariables": {
            "HOME": home,
            # The platform modules are loaded by absolute path. Excluding
            # Homebrew here keeps `timeout` and `op` unavailable.
            "PATH": "/usr/bin:/bin",
        },
        "StartInterval": payload["monitoring"]["poll_interval_seconds"],
        "RunAtLoad": True,
        "ProcessType": "Background",
        "StandardOutPath": log_path,
        "StandardErrorPath": log_path,
    }
    return plistlib.dumps(data, sort_keys=True)


def is_within(path: Path, parent: Path) -> bool:
    try:
        path.resolve().relative_to(parent.resolve())
        return True
    except ValueError:
        return False


def stage(args: argparse.Namespace) -> dict[str, Any]:
    if not SLUG_RE.fullmatch(args.slug):
        raise SnipeError("slug must contain lowercase letters, numbers, and single hyphens")
    output_dir = Path(args.output_dir).expanduser().resolve()
    home = Path(args.home).expanduser().resolve()
    if is_within(output_dir, home / ".openclaw") or is_within(output_dir, home / "Library" / "LaunchAgents"):
        raise SnipeError("stage output must not be a live OpenClaw or LaunchAgents directory")

    payload = build_payload(args)
    now = datetime.now(timezone.utc)
    authorized_at = parse_timestamp(payload["authorized_at"], "authorized_at")
    expires_at = parse_timestamp(payload["monitoring"]["expires_at"], "expires_at")
    if authorized_at < now - timedelta(minutes=5) or authorized_at > now + timedelta(minutes=1):
        raise SnipeError("authorization timestamp must reflect a fresh explicit approval")
    if expires_at <= now:
        raise SnipeError("authorization is already expired")
    config_path = output_dir / "authorization.json"
    plist_path = output_dir / f"{payload['runtime']['label']}.plist"
    if not args.replace_staged and (config_path.exists() or plist_path.exists()):
        raise SnipeError("staged artifacts already exist; use a new directory or --replace-staged")

    atomic_json(config_path, payload)
    atomic_bytes(plist_path, plist_for(payload), 0o600)
    return safe_summary(
        payload,
        "staged",
        authorization_path=str(config_path),
        plist_path=str(plist_path),
        deployed=False,
    )


def load_platform(payload: dict[str, Any]) -> tuple[Any, Any]:
    runtime = payload["runtime"]
    module_path = Path(runtime["platform_module"])
    if not module_path.is_absolute() or not module_path.is_file():
        raise SnipeError("platform module is unavailable")

    # Defense in depth: no `op` environment and no Homebrew `timeout` on PATH.
    os.environ.pop("OP_SERVICE_ACCOUNT_TOKEN", None)
    os.environ["PATH"] = "/usr/bin:/bin"
    module_name = f"restaurant_snipe_{payload['platform']}_{payload['job_id']}"
    loader = importlib.machinery.SourceFileLoader(module_name, str(module_path))
    spec = importlib.util.spec_from_loader(module_name, loader)
    if spec is None:
        raise SnipeError("could not load platform module")
    module = importlib.util.module_from_spec(spec)
    with open(os.devnull, "w", encoding="utf-8") as devnull:
        with contextlib.redirect_stdout(devnull), contextlib.redirect_stderr(devnull):
            loader.exec_module(module)

    credential_name = "OpenTableCredentials" if payload["platform"] == "opentable" else "ResyCredentials"
    api_name = "OpenTableAPI" if payload["platform"] == "opentable" else "ResyAPI"
    credential_class = getattr(module, credential_name, None)
    api_class = getattr(module, api_name, None)
    if credential_class is None or api_class is None or not hasattr(credential_class, "_op_read"):
        raise SnipeError("platform module does not expose the cache-only adapter contract")

    # Never let the LaunchAgent fall back to 1Password. Missing/expired cache
    # entries must fail closed and be refreshed interactively outside the job.
    credential_class._op_read = lambda self, field: None
    with open(os.devnull, "w", encoding="utf-8") as devnull:
        with contextlib.redirect_stdout(devnull), contextlib.redirect_stderr(devnull):
            api = api_class()
    return module, api


def quiet_call(function: Any, *args: Any) -> Any:
    with open(os.devnull, "w", encoding="utf-8") as devnull:
        with contextlib.redirect_stdout(devnull), contextlib.redirect_stderr(devnull):
            return function(*args)


def normalized_resy_reservations(raw: Any) -> list[dict[str, Any]]:
    reservations = raw if isinstance(raw, list) else raw.get("reservations", []) if isinstance(raw, dict) else []
    normalized = []
    for item in reservations:
        if not isinstance(item, dict):
            continue
        status_value = item.get("status", {})
        finished = status_value.get("finished", 0) if isinstance(status_value, dict) else 0
        normalized.append(
            {
                "platform": "resy",
                "venue_id": str(item.get("venue", {}).get("id", "")),
                "date": str(item.get("day", "")),
                "time": str(item.get("time_slot", ""))[:5],
                "party_size": int(item.get("num_seats", 0) or 0),
                "status": "finished" if finished else "confirmed",
            }
        )
    return normalized


def available_slots(platform: str, raw: Any) -> list[dict[str, Any]]:
    slots: list[dict[str, Any]] = []
    if not isinstance(raw, dict):
        return slots
    if platform == "opentable":
        for day in raw.get("suggestedAvailability", []):
            if not isinstance(day, dict):
                continue
            for slot in day.get("timeslots", []):
                if not isinstance(slot, dict) or not slot.get("available", False):
                    continue
                normalized = normalize_slot_start(slot.get("dateTime"))
                if normalized:
                    slots.append({"date": normalized[0], "time": normalized[1], "raw": slot})
    else:
        for venue in raw.get("results", {}).get("venues", []):
            if not isinstance(venue, dict):
                continue
            for slot in venue.get("slots", []):
                if not isinstance(slot, dict):
                    continue
                normalized = normalize_slot_start(slot.get("date", {}).get("start"))
                if normalized:
                    slots.append({"date": normalized[0], "time": normalized[1], "raw": slot})
    return slots


def choose_slot(payload: dict[str, Any], slots: Iterable[dict[str, Any]]) -> dict[str, Any] | None:
    target = minutes(payload["time"]["target"])
    window_start = minutes(payload["time"]["window_start"])
    window_end = minutes(payload["time"]["window_end"])
    max_delta = payload["time"]["max_delta_minutes"]
    dates = set(payload["dates"])
    eligible = []
    for slot in slots:
        if slot.get("date") not in dates:
            continue
        try:
            slot_minutes = minutes(str(slot.get("time", "")))
        except SnipeError:
            continue
        delta = abs(slot_minutes - target)
        if window_start <= slot_minutes <= window_end and delta <= max_delta:
            eligible.append((delta, slot["date"], slot_minutes, slot))
    return min(eligible, default=(None, None, None, None))[-1]


def nested_items(value: Any, parent: str = "") -> Iterable[tuple[str, Any]]:
    if isinstance(value, dict):
        for key, child in value.items():
            normalized = re.sub(r"[^a-z0-9]", "", str(key).lower())
            path = f"{parent}.{normalized}" if parent else normalized
            yield path, child
            yield from nested_items(child, path)
    elif isinstance(value, list):
        for child in value:
            yield from nested_items(child, parent)


def booking_confirmed(result: Any) -> bool:
    if not isinstance(result, dict):
        return False
    if result.get("error") or result.get("errors"):
        return False
    has_confirmation = False
    confirmed_status = False
    success_flag = result.get("success") is True or result.get("ok") is True
    for path, value in nested_items(result):
        key = path.rsplit(".", 1)[-1]
        if key in CONFIRMATION_KEYS and isinstance(value, (str, int)) and str(value).strip():
            has_confirmation = True
        if key == "id" and any(part in path.split(".") for part in ("booking", "reservation")):
            if isinstance(value, (str, int)) and str(value).strip():
                has_confirmation = True
        if key in {"status", "reservationstatus", "bookingstatus"}:
            confirmed_status = str(value).lower() in CONFIRMED_STATUS_VALUES
    return has_confirmation or (success_flag and confirmed_status)


def local_receipt_conflict(payload: dict[str, Any]) -> bool:
    state_dir = Path(payload["runtime"]["state_dir"])
    state_root = state_dir.parent
    if not state_root.is_dir():
        return False
    dates = set(payload["dates"])
    start = minutes(payload["time"]["window_start"])
    end = minutes(payload["time"]["window_end"])
    for receipt_path in state_root.glob("*/confirmed.json"):
        try:
            receipt = read_json(receipt_path)
        except SnipeError:
            continue
        if not isinstance(receipt, dict) or receipt.get("date") not in dates:
            continue
        time_value = receipt.get("time")
        if not isinstance(time_value, str) or not TIME_RE.fullmatch(time_value):
            return True
        if start <= minutes(time_value) <= end:
            return True
    return False


def write_receipt(payload: dict[str, Any], source: str, date: str, time_value: str, now: datetime) -> Path:
    receipt = {
        "schema_version": SCHEMA_VERSION,
        "job_id": payload["job_id"],
        "source": source,
        "confirmed_at": timestamp(now),
        "platform": payload["platform"],
        "venue_id": payload["venue"]["id"],
        "date": date,
        "time": time_value,
        "party_size": payload["party_size"],
    }
    path = Path(payload["runtime"]["state_dir"]) / "confirmed.json"
    atomic_json(path, receipt)
    return path


def notify(payload: dict[str, Any], date: str, time_value: str, source: str) -> bool:
    runtime = payload["runtime"]
    message = (
        f"Reservation confirmed: {payload['venue']['name']} on {date} at {time_value} "
        f"for {payload['party_size']} via {payload['platform']} ({source})."
    )
    command = [runtime["imsg"], "send", "--chat-id", payload["notification"]["target"], "--text", message, "--json"]
    try:
        with open(os.devnull, "wb") as devnull:
            completed = subprocess.run(
                command,
                stdin=subprocess.DEVNULL,
                stdout=devnull,
                stderr=devnull,
                timeout=20,
                check=False,
                env={"HOME": runtime["home"], "PATH": "/usr/bin:/bin"},
            )
        return completed.returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def cleanup_after_confirmation(payload: dict[str, Any]) -> bool:
    runtime = payload["runtime"]
    try:
        with open(os.devnull, "wb") as devnull:
            completed = subprocess.run(
                [runtime["launchctl"], "bootout", f"gui/{os.getuid()}/{runtime['label']}"],
                stdin=subprocess.DEVNULL,
                stdout=devnull,
                stderr=devnull,
                timeout=20,
                check=False,
                env={"HOME": runtime["home"], "PATH": "/usr/bin:/bin"},
            )
    except (OSError, subprocess.SubprocessError):
        return False
    if completed.returncode != 0:
        return False
    cleanup_complete = True
    # Paths were validated against exact job-specific managed locations.
    for value in (runtime["plist"], runtime["config"]):
        try:
            durable_unlink(Path(value))
        except OSError:
            cleanup_complete = False
    return cleanup_complete


def runtime_existing_reservations(payload: dict[str, Any], api: Any) -> list[dict[str, Any]]:
    if payload["platform"] != "resy" or not hasattr(api, "get_reservations"):
        return []
    return normalized_resy_reservations(quiet_call(api.get_reservations))


def platform_availability(payload: dict[str, Any], api: Any) -> list[dict[str, Any]]:
    all_slots: list[dict[str, Any]] = []
    for date in payload["dates"]:
        if payload["platform"] == "opentable":
            raw = quiet_call(
                api.find_availability,
                payload["venue"]["id"],
                date,
                payload["time"]["target"],
                payload["party_size"],
            )
        else:
            raw = quiet_call(api.find_availability, payload["venue"]["id"], date, payload["party_size"])
        all_slots.extend(available_slots(payload["platform"], raw))
    return all_slots


def book_exact_slot(payload: dict[str, Any], api: Any, slot: dict[str, Any]) -> Any:
    raw = slot["raw"]
    if payload["platform"] == "opentable":
        config_token = raw.get("token")
        slot_hash = raw.get("slotHash")
        slot_start = raw.get("dateTime")
        areas = raw.get("diningAreas", [])
        dining_area_id = areas[0].get("id") if areas and isinstance(areas[0], dict) else None
        if not config_token or not slot_hash or not slot_start:
            raise SnipeError("matching slot lacked required structured booking fields")
        return quiet_call(
            api.book,
            payload["venue"]["id"],
            config_token,
            slot_hash,
            slot_start,
            payload["party_size"],
            dining_area_id,
        )

    config_token = raw.get("config", {}).get("token")
    if not config_token:
        raise SnipeError("matching slot lacked required structured booking fields")
    details = quiet_call(api.get_details, config_token, slot["date"], payload["party_size"])
    if not isinstance(details, dict):
        raise SnipeError("booking details were not structured JSON")
    book_token = details.get("book_token", {}).get("value")
    payment_id = quiet_call(api.creds.get, "payment_id")
    if not book_token or not payment_id:
        raise SnipeError("cache-only booking credentials are unavailable")
    return quiet_call(api.book, book_token, payment_id)


def run_job(config_path: Path, now: datetime | None = None) -> dict[str, Any]:
    payload = validate_payload(read_json(config_path))
    if config_path.resolve() != Path(payload["runtime"]["config"]).resolve():
        raise SnipeError("run must use the explicitly deployed runtime authorization path")
    now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    state_dir = Path(payload["runtime"]["state_dir"])
    ensure_directory(state_dir)

    with (state_dir / "runner.lock").open("a+") as lock_handle:
        try:
            fcntl.flock(lock_handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return safe_summary(payload, "busy")

        receipt_path = state_dir / "confirmed.json"
        attempt_path = state_dir / "booking-attempt.json"
        if receipt_path.exists():
            cleaned = cleanup_after_confirmation(payload)
            return safe_summary(payload, "already_confirmed", cleanup_complete=cleaned)
        if attempt_path.exists():
            return safe_summary(payload, "manual_review_required", reason="prior_booking_attempt_unresolved")
        if now >= parse_timestamp(payload["monitoring"]["expires_at"], "expires_at"):
            return safe_summary(payload, "expired", cleanup_complete=False)
        if local_receipt_conflict(payload):
            return safe_summary(payload, "blocked_existing_reservation", source="local_receipt")

        try:
            _, api = load_platform(payload)
            existing = runtime_existing_reservations(payload, api)
        except (SnipeError, SystemExit, OSError, Exception):
            # Do not include platform exception text; it may contain sensitive data.
            return safe_summary(payload, "platform_unavailable")

        window_start = minutes(payload["time"]["window_start"])
        window_end = minutes(payload["time"]["window_end"])
        if reservation_conflicts(existing, set(payload["dates"]), window_start, window_end):
            matching = matching_existing_reservation(payload, existing)
            if matching:
                write_receipt(payload, "platform_existing_reservation", matching["date"], matching["time"], now)
                notified = notify(payload, matching["date"], matching["time"], "existing reservation")
                cleaned = cleanup_after_confirmation(payload)
                return safe_summary(
                    payload,
                    "existing_reservation_confirmed",
                    notification_sent=notified,
                    cleanup_complete=cleaned,
                )
            return safe_summary(payload, "blocked_existing_reservation", source="platform")

        try:
            slot = choose_slot(payload, platform_availability(payload, api))
        except (SnipeError, SystemExit, OSError, Exception):
            return safe_summary(payload, "platform_unavailable")
        if slot is None:
            return safe_summary(payload, "no_match")

        try:
            atomic_json(
                attempt_path,
                {
                    "schema_version": SCHEMA_VERSION,
                    "job_id": payload["job_id"],
                    "started_at": timestamp(now),
                    "date": slot["date"],
                    "time": slot["time"],
                },
            )
        except OSError:
            return safe_summary(payload, "manual_review_required", reason="booking_marker_not_durable")
        try:
            result = book_exact_slot(payload, api, slot)
        except (SnipeError, SystemExit, OSError, Exception):
            return safe_summary(payload, "manual_review_required", reason="booking_attempt_unresolved")
        confirmed = booking_confirmed(result)
        if confirmed and payload["platform"] == "resy":
            try:
                post_booking = runtime_existing_reservations(payload, api)
                confirmed = matching_existing_reservation(
                    payload,
                    post_booking,
                    expected_date=slot["date"],
                    expected_time=slot["time"],
                ) is not None
            except (SnipeError, SystemExit, OSError, Exception):
                confirmed = False
        if not confirmed:
            return safe_summary(payload, "manual_review_required", reason="unconfirmed_structured_result")

        try:
            write_receipt(payload, "booking", slot["date"], slot["time"], now)
        except OSError:
            return safe_summary(payload, "manual_review_required", reason="receipt_not_durable")
        marker_cleanup_complete = True
        try:
            durable_unlink(attempt_path)
        except OSError:
            marker_cleanup_complete = False
        notified = notify(payload, slot["date"], slot["time"], "auto-book")
        cleaned = cleanup_after_confirmation(payload) and marker_cleanup_complete
        return safe_summary(
            payload,
            "booking_confirmed",
            date=slot["date"],
            time=slot["time"],
            notification_sent=notified,
            cleanup_complete=cleaned,
        )


def stage_parser(subparsers: Any) -> None:
    parser = subparsers.add_parser("stage", help="write reviewable authorization and plist artifacts")
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--slug", required=True)
    parser.add_argument("--authorization-id", required=True)
    parser.add_argument("--approved-by", required=True)
    parser.add_argument("--authorized-at", required=True)
    parser.add_argument("--platform", choices=sorted(PLATFORMS), required=True)
    parser.add_argument("--venue-id", required=True)
    parser.add_argument("--venue-name", required=True)
    parser.add_argument("--date", action="append", required=True)
    parser.add_argument("--party-size", type=int, required=True)
    parser.add_argument("--target-time", required=True)
    parser.add_argument("--window-start", required=True)
    parser.add_argument("--window-end", required=True)
    parser.add_argument("--max-delta-minutes", type=int, required=True)
    parser.add_argument("--duration-seconds", type=int, required=True)
    parser.add_argument("--expires-at", required=True)
    parser.add_argument("--poll-interval-seconds", type=int, required=True)
    parser.add_argument("--notification-target", required=True)
    parser.add_argument("--existing-reservations-json", required=True)
    parser.add_argument("--existing-reservation-checked-at", required=True)
    parser.add_argument("--existing-reservation-source", required=True)
    parser.add_argument("--home", default=str(Path.home()))
    parser.add_argument("--python-path", default="/opt/homebrew/bin/python3", help=argparse.SUPPRESS)
    parser.add_argument(
        "--runner-path",
        default=str(Path.home() / ".openclaw" / "skills" / "restaurant-snipe" / "scripts" / "restaurant_snipe.py"),
        help=argparse.SUPPRESS,
    )
    parser.add_argument("--platform-module", help=argparse.SUPPRESS)
    parser.add_argument("--launchctl-path", default="/bin/launchctl", help=argparse.SUPPRESS)
    parser.add_argument(
        "--imsg-path",
        default=str(Path.home() / ".openclaw" / "bin" / "imsg"),
        help=argparse.SUPPRESS,
    )
    parser.add_argument("--replace-staged", action="store_true")


def make_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    stage_parser(subparsers)
    run_parser = subparsers.add_parser("run", help="run one authorized poll; intended for LaunchAgent use")
    run_parser.add_argument("--config", required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = make_parser().parse_args(argv)
    try:
        if args.command == "stage":
            result = stage(args)
        else:
            result = run_job(Path(args.config))
    except SnipeError as exc:
        print(json.dumps({"status": "error", "error": str(exc)}, sort_keys=True))
        return 2
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
