#!/usr/bin/env python3
"""Exact, fail-closed vacancy action reservations and Hue canary worker."""

from __future__ import annotations

import argparse
import contextlib
from datetime import datetime, timedelta, timezone
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import stat
import subprocess
import sys
import tempfile
import time
from typing import Any, Callable, Mapping, Sequence


sys.path.insert(0, str(Path(__file__).resolve().parent))
from home_event_bus import EventStore, RuntimePaths, validate_runtime  # noqa: E402


DEFAULT_ROOT = Path("~/.openclaw/home-events").expanduser()
DEFAULT_PRESENCE_STATE = Path("~/.openclaw/presence/state.json").expanduser()
DEFAULT_PRODUCER_STATE = Path(
    "~/.openclaw/presence/home-events-outbox/producer-state.json"
).expanduser()
DEFAULT_JOURNAL_ROOT = Path("~/.openclaw/vacancy-actions/journal").expanduser()
MAX_POLICY_BYTES = 16 * 1024
MAX_INPUT_BYTES = 1024 * 1024
PRESENCE_MAX_AGE = timedelta(minutes=30)
FUTURE_TOLERANCE = timedelta(minutes=5)
ID_RE = re.compile(r"^(?:cycle|run)_[0-9a-f]{32}$")
HASH_RE = re.compile(r"^[0-9a-f]{64}$")
SAFE_CODE_RE = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
RESERVATION_RE = re.compile(r"^act_[0-9a-f]{32}$")
AUTOMATION_NAME_RE = re.compile(r"^[^\x00-\x1f\x7f]{1,128}$")
SITES = frozenset({"cabin", "crosstown"})
TARGETS: Mapping[str, Mapping[str, tuple[str, str]]] = {
    "cabin": {
        "all_lights": ("turn_off", "all_off"),
        "feeding_schedule": ("suspend_restore", "vacant_disabled"),
    },
    "crosstown": {
        "all_lights": ("turn_off", "all_off"),
        "daily_automations": ("suspend_restore", "vacancy_suspended"),
        "feeding_schedule": ("suspend_restore", "vacant_disabled"),
    },
}
FEEDER_SELECTORS = {
    "cabin": "cabin-feeder",
    "crosstown": "crosstown-feeder",
}
OTHER_SITE = {"cabin": "crosstown", "crosstown": "cabin"}
WHISKER_ALIASES = {
    "cabin": "cabin_litter_robot",
    "crosstown": "crosstown_litter_robot",
}
WHISKER_MAX_POLL_AGE = timedelta(minutes=5)
CAT_TRANSFER_RETRYABLE_NO_COMMAND_REASONS = frozenset(
    {
        "destination_schedule_manually_disabled",
        "destination_schedule_unavailable",
    }
)
FEEDER_WAITING_REASONS = frozenset({"cat_transfer_not_settled"})


class ActionError(Exception):
    def __init__(self, code: str, *, command_attempted: bool = False):
        super().__init__(code)
        self.code = code
        self.command_attempted = command_attempted


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace(
        "+00:00", "Z"
    )


def parse_time(value: object, code: str = "timestamp_invalid") -> datetime:
    if not isinstance(value, str) or not value or len(value) > 64:
        raise ActionError(code)
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ActionError(code) from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ActionError(code)
    return parsed.astimezone(timezone.utc)


def format_time(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat(timespec="seconds").replace(
        "+00:00", "Z"
    )


def canonical_json(value: object) -> bytes:
    try:
        return json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise ActionError("json_invalid") from exc


def state_hash(value: object) -> str:
    return hashlib.sha256(canonical_json(value)).hexdigest()


def _verified_presence_snapshot(
    site: str,
    *,
    state_path: Path,
    producer_path: Path,
    now: datetime,
) -> tuple[dict[str, Any], dict[str, Any], str]:
    if site not in SITES:
        raise ActionError("site_invalid")
    canonical = _read_json(state_path, MAX_INPUT_BYTES, "presence_state_invalid")
    producer = _read_json(producer_path, 4096, "producer_state_invalid")
    if set(producer) != {
        "schema_version",
        "sequence",
        "observation_id",
        "state_hash",
        "evaluated_at",
    }:
        raise ActionError("producer_state_invalid")
    if (
        producer["schema_version"] != 1
        or not isinstance(producer["state_hash"], str)
        or HASH_RE.fullmatch(producer["state_hash"]) is None
        or canonical.get("timestamp") != producer["evaluated_at"]
        or state_hash(canonical) != producer["state_hash"]
    ):
        raise ActionError("presence_state_mismatch")
    evaluated = parse_time(producer["evaluated_at"], "presence_time_invalid")
    if evaluated > now + FUTURE_TOLERANCE or now - evaluated > PRESENCE_MAX_AGE:
        raise ActionError("presence_state_stale")
    site_state = canonical.get(site)
    if not isinstance(site_state, dict) or site_state.get("fresh") is not True:
        raise ActionError("presence_state_invalid")
    parse_time(site_state.get("stateChangedAt"), "presence_time_invalid")
    return canonical, site_state, str(producer["state_hash"])


def _private_regular(path: Path, maximum: int, code: str) -> os.stat_result:
    try:
        metadata = path.lstat()
    except OSError as exc:
        raise ActionError(code) from exc
    if (
        stat.S_ISLNK(metadata.st_mode)
        or not stat.S_ISREG(metadata.st_mode)
        or metadata.st_uid != os.geteuid()
        or stat.S_IMODE(metadata.st_mode) != 0o600
        or metadata.st_nlink != 1
        or metadata.st_size <= 0
        or metadata.st_size > maximum
    ):
        raise ActionError(code)
    return metadata


def _read_json(path: Path, maximum: int, code: str) -> dict[str, Any]:
    _private_regular(path, maximum, code)
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ActionError(code) from exc
    if not isinstance(value, dict):
        raise ActionError(code)
    return value


def _fsync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _atomic_private(path: Path, data: bytes) -> None:
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    temporary = Path(temporary_name)
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "wb", closefd=True) as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        os.chmod(path, 0o600, follow_symlinks=False)
        _fsync_directory(path.parent)
    finally:
        temporary.unlink(missing_ok=True)


def policy_path(root: Path) -> Path:
    return root / "config" / "action-policy.json"


def validate_policy(value: object) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != {
        "schema_version",
        "active",
        "targets",
    }:
        raise ActionError("action_policy_invalid")
    schema_version = value["schema_version"]
    if schema_version not in {2, 3} or not isinstance(value["active"], bool):
        raise ActionError("action_policy_invalid")
    targets = value["targets"]
    if not isinstance(targets, dict) or set(targets) != SITES:
        raise ActionError("action_policy_invalid")
    normalized: dict[str, Any] = {
        "schema_version": 3,
        "active": value["active"],
        "targets": {},
    }
    for site in sorted(SITES):
        site_targets = targets[site]
        if not isinstance(site_targets, dict) or not set(site_targets).issubset(
            TARGETS[site]
        ):
            raise ActionError("action_policy_invalid")
        normalized["targets"][site] = {}
        for target, entry in sorted(site_targets.items()):
            expected_keys = {
                "owner",
                "action",
                "desired_state",
                "expiry_seconds",
                "settle_seconds",
            }
            if target == "daily_automations":
                expected_keys.add("automations")
            if schema_version == 3:
                expected_keys.update({"mode", "trigger"})
            if target == "feeding_schedule":
                if schema_version != 3:
                    raise ActionError("action_policy_invalid")
                expected_keys.update(
                    {
                        "selector",
                        "destination_site",
                        "destination_selector",
                        "evidence_settle_seconds",
                    }
                )
            if not isinstance(entry, dict) or set(entry) != expected_keys:
                raise ActionError("action_policy_invalid")
            expected_action, expected_state = TARGETS[site][target]
            mode = entry.get("mode", "active")
            trigger = entry.get("trigger", "vacancy")
            if (
                entry["owner"] not in {"legacy", "bus"}
                or mode not in {"disabled", "shadow", "active"}
                or trigger not in {"vacancy", "cat_transfer"}
                or entry["action"] != expected_action
                or entry["desired_state"] != expected_state
                or not isinstance(entry["expiry_seconds"], int)
                or isinstance(entry["expiry_seconds"], bool)
                or not 60 <= entry["expiry_seconds"] <= 900
                or not isinstance(entry["settle_seconds"], int)
                or isinstance(entry["settle_seconds"], bool)
                or not 1 <= entry["settle_seconds"] <= 30
            ):
                raise ActionError("action_policy_invalid")
            if target == "feeding_schedule":
                destination = OTHER_SITE[site]
                if (
                    entry["owner"] != "bus"
                    or trigger != "cat_transfer"
                    or entry["selector"] != FEEDER_SELECTORS[site]
                    or entry["destination_site"] != destination
                    or entry["destination_selector"] != FEEDER_SELECTORS[destination]
                    or not isinstance(entry["evidence_settle_seconds"], int)
                    or isinstance(entry["evidence_settle_seconds"], bool)
                    or not 300 <= entry["evidence_settle_seconds"] <= 7200
                ):
                    raise ActionError("action_policy_invalid")
            elif trigger != "vacancy" or mode != "active":
                raise ActionError("action_policy_invalid")
            if target == "daily_automations":
                automations = entry["automations"]
                if (
                    not isinstance(automations, list)
                    or not 1 <= len(automations) <= 16
                    or len(set(automations)) != len(automations)
                    or any(
                        not isinstance(name, str)
                        or AUTOMATION_NAME_RE.fullmatch(name) is None
                        for name in automations
                    )
                ):
                    raise ActionError("action_policy_invalid")
            normalized_entry = dict(entry)
            normalized_entry["mode"] = mode
            normalized_entry["trigger"] = trigger
            normalized["targets"][site][target] = normalized_entry
    return normalized


def load_policy(root: Path, *, allow_missing: bool = False) -> tuple[dict[str, Any], str] | None:
    path = policy_path(root)
    if allow_missing and not path.exists() and not path.is_symlink():
        return None
    value = validate_policy(_read_json(path, MAX_POLICY_BYTES, "action_policy_invalid"))
    return value, hashlib.sha256(canonical_json(value)).hexdigest()


def install_policy(root: Path, data: bytes) -> dict[str, Any]:
    if not data or len(data) > MAX_POLICY_BYTES:
        raise ActionError("action_policy_invalid")
    try:
        value = json.loads(data.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise ActionError("action_policy_invalid") from exc
    policy = validate_policy(value)
    paths = validate_runtime(root)
    _atomic_private(policy_path(paths.root), canonical_json(policy) + b"\n")
    bus_targets = sum(
        entry["owner"] == "bus" and entry["mode"] == "active"
        for site in SITES
        for entry in policy["targets"][site].values()
    )
    return {"ok": True, "active": policy["active"], "bus_targets": bus_targets}


def ownership(root: Path, site: str, target: str) -> str:
    if site not in SITES or target not in TARGETS[site]:
        raise ActionError("target_invalid")
    loaded = load_policy(root, allow_missing=True)
    if loaded is None:
        return "legacy"
    policy, _ = loaded
    if not policy["active"]:
        return "legacy"
    entry = policy["targets"][site].get(target)
    return (
        "legacy"
        if entry is None or entry["mode"] != "active"
        else str(entry["owner"])
    )


def validate_presence(
    site: str,
    cycle_id: str,
    *,
    state_path: Path,
    producer_path: Path,
    journal_root: Path,
    now: datetime,
) -> str:
    if site not in SITES or ID_RE.fullmatch(cycle_id) is None or not cycle_id.startswith(
        "cycle_"
    ):
        raise ActionError("vacancy_cycle_invalid")
    canonical, site_state, producer_hash = _verified_presence_snapshot(
        site,
        state_path=state_path,
        producer_path=producer_path,
        now=now,
    )
    if site_state.get("occupancy") != "confirmed_vacant":
        raise ActionError("site_not_confirmed_vacant")
    changed_at = site_state.get("stateChangedAt")
    cycle = _read_json(
        journal_root / "cycles" / f"{site}.json",
        4096,
        "vacancy_cycle_invalid",
    )
    if (
        set(cycle) != {"schema_version", "site", "state_changed_at", "cycle_id"}
        or cycle["schema_version"] != 1
        or cycle["site"] != site
        or cycle["state_changed_at"] != changed_at
        or cycle["cycle_id"] != cycle_id
    ):
        raise ActionError("vacancy_cycle_mismatch")
    return producer_hash


def reserve_action(
    connection: sqlite3.Connection,
    *,
    root: Path,
    state_path: Path,
    producer_path: Path,
    journal_root: Path,
    site: str,
    target: str,
    cycle_id: str,
    trigger_state_hash: str,
    trigger_event_id: int | None,
    clock: Callable[[], str] = utc_now,
) -> dict[str, Any]:
    loaded = load_policy(root)
    assert loaded is not None
    policy, policy_hash = loaded
    entry = policy["targets"].get(site, {}).get(target)
    if (
        not policy["active"]
        or entry is None
        or entry["owner"] != "bus"
        or entry["mode"] != "active"
    ):
        return {"status": "disabled"}
    now_text = clock()
    now = parse_time(now_text, "clock_invalid")
    reserved_state_hash = validate_presence(
        site,
        cycle_id,
        state_path=state_path,
        producer_path=producer_path,
        journal_root=journal_root,
        now=now,
    )
    if not isinstance(trigger_state_hash, str) or HASH_RE.fullmatch(trigger_state_hash) is None:
        raise ActionError("trigger_state_hash_invalid")
    reservation_uid = "act_" + os.urandom(16).hex()
    expires_at = format_time(now + timedelta(seconds=entry["expiry_seconds"]))
    cursor = connection.execute(
        """
        INSERT OR IGNORE INTO action_reservations(
            reservation_uid, trigger_event_id, site, target_alias, action,
            vacancy_cycle_id, trigger_state_hash, reserved_state_hash,
            policy_hash, status, reserved_at, expires_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?, ?)
        """,
        (
            reservation_uid,
            trigger_event_id,
            site,
            target,
            entry["action"],
            cycle_id,
            trigger_state_hash,
            reserved_state_hash,
            policy_hash,
            format_time(now),
            expires_at,
        ),
    )
    return {
        "status": "reserved" if cursor.rowcount == 1 else "duplicate",
        "reservation_uid": reservation_uid if cursor.rowcount == 1 else None,
    }


def reserve_from_vacancy_event(
    connection: sqlite3.Connection,
    *,
    root: Path,
    state_path: Path,
    producer_path: Path,
    journal_root: Path,
    event_id: int,
    site: str,
    attributes: Mapping[str, Any],
    clock: Callable[[], str] = utc_now,
) -> dict[str, int | str]:
    loaded = load_policy(root, allow_missing=True)
    if loaded is None or not loaded[0]["active"]:
        return {"status": "disabled", "reserved": 0}
    policy = loaded[0]
    reserved = 0
    duplicates = 0
    for target, entry in sorted(policy["targets"].get(site, {}).items()):
        if (
            entry["owner"] != "bus"
            or entry["mode"] != "active"
            or entry["trigger"] != "vacancy"
        ):
            continue
        result = reserve_action(
            connection,
            root=root,
            state_path=state_path,
            producer_path=producer_path,
            journal_root=journal_root,
            site=site,
            target=target,
            cycle_id=str(attributes["cycle_id"]),
            trigger_state_hash=str(attributes["trigger_state_hash"]),
            trigger_event_id=event_id,
            clock=clock,
        )
        reserved += result["status"] == "reserved"
        duplicates += result["status"] == "duplicate"
    return {"status": "ok", "reserved": reserved, "duplicates": duplicates}


def _load_whisker_state(root: Path) -> dict[str, Any]:
    value = _read_json(
        root / "state" / "whisker-adapter.json",
        MAX_INPUT_BYTES,
        "whisker_state_invalid",
    )
    if (
        set(value) != {"schema_version", "sites"}
        or value["schema_version"] != 1
        or not isinstance(value["sites"], dict)
        or set(value["sites"]) != SITES
    ):
        raise ActionError("whisker_state_invalid")
    for site in SITES:
        record = value["sites"][site]
        if (
            not isinstance(record, dict)
            or set(record)
            != {
                "enabled",
                "baselined",
                "health",
                "coverage_start",
                "last_successful_poll",
                "anchor",
                "fingerprints",
                "last_error",
            }
            or record["enabled"] is not True
            or record["baselined"] is not True
            or record["health"] != "ok"
        ):
            raise ActionError("whisker_coverage_unavailable")
        parse_time(record["coverage_start"], "whisker_state_invalid")
        parse_time(record["last_successful_poll"], "whisker_state_invalid")
    return value


def _cat_transfer_evidence(
    connection: sqlite3.Connection,
    *,
    root: Path,
    state_path: Path,
    producer_path: Path,
    journal_root: Path,
    origin_site: str,
    entry: Mapping[str, Any],
    clock: Callable[[], str],
) -> dict[str, Any]:
    if origin_site not in SITES or entry.get("trigger") != "cat_transfer":
        raise ActionError("cat_transfer_invalid")
    destination = OTHER_SITE[origin_site]
    now = parse_time(clock(), "clock_invalid")
    canonical, origin_state, producer_hash = _verified_presence_snapshot(
        origin_site,
        state_path=state_path,
        producer_path=producer_path,
        now=now,
    )
    destination_state = canonical.get(destination)
    if origin_state.get("occupancy") != "confirmed_vacant":
        raise ActionError("site_not_confirmed_vacant")
    if (
        not isinstance(destination_state, dict)
        or destination_state.get("fresh") is not True
        or destination_state.get("occupancy") != "occupied"
        or not _occupied_by_sticky_resident(canonical, destination)
    ):
        raise ActionError("destination_not_occupied")
    cycle = _read_json(
        journal_root / "cycles" / f"{origin_site}.json",
        4096,
        "vacancy_cycle_invalid",
    )
    if (
        set(cycle) != {"schema_version", "site", "state_changed_at", "cycle_id"}
        or cycle["schema_version"] != 1
        or cycle["site"] != origin_site
        or cycle["state_changed_at"] != origin_state.get("stateChangedAt")
        or not isinstance(cycle["cycle_id"], str)
        or ID_RE.fullmatch(cycle["cycle_id"]) is None
    ):
        raise ActionError("vacancy_cycle_mismatch")
    cycle_started = parse_time(cycle["state_changed_at"], "vacancy_cycle_invalid")
    validate_presence(
        origin_site,
        cycle["cycle_id"],
        state_path=state_path,
        producer_path=producer_path,
        journal_root=journal_root,
        now=now,
    )
    whisker = _load_whisker_state(root)
    for site in SITES:
        record = whisker["sites"][site]
        coverage_start = parse_time(record["coverage_start"], "whisker_state_invalid")
        last_poll = parse_time(
            record["last_successful_poll"], "whisker_state_invalid"
        )
        if coverage_start > cycle_started:
            raise ActionError("whisker_coverage_incomplete")
        if last_poll > now + FUTURE_TOLERANCE or now - last_poll > WHISKER_MAX_POLL_AGE:
            raise ActionError("whisker_coverage_stale")
    origin_event = connection.execute(
        """
        SELECT id FROM events
        WHERE source='whisker' AND event_type='pet.litter_box_activity'
          AND site=? AND entity_alias=? AND occurred_at >= ?
        ORDER BY occurred_at, id LIMIT 1
        """,
        (origin_site, WHISKER_ALIASES[origin_site], format_time(cycle_started)),
    ).fetchone()
    if origin_event is not None:
        raise ActionError("origin_litter_activity_observed")
    settled_before = now - timedelta(seconds=int(entry["evidence_settle_seconds"]))
    candidate = connection.execute(
        """
        SELECT id, occurred_at FROM events
        WHERE source='whisker' AND event_type='pet.litter_box_activity'
          AND site=? AND entity_alias=? AND occurred_at >= ?
        ORDER BY occurred_at DESC, id DESC LIMIT 1
        """,
        (
            destination,
            WHISKER_ALIASES[destination],
            format_time(cycle_started),
        ),
    ).fetchone()
    if (
        candidate is None
        or parse_time(candidate["occurred_at"], "whisker_event_invalid")
        > settled_before
    ):
        raise ActionError("cat_transfer_not_settled")
    return {
        "origin_site": origin_site,
        "destination_site": destination,
        "cycle_id": cycle["cycle_id"],
        "trigger_state_hash": producer_hash,
        "event_id": int(candidate["id"]),
        "occurred_at": candidate["occurred_at"],
    }


def reserve_cat_transfers(
    connection: sqlite3.Connection,
    *,
    root: Path,
    state_path: Path,
    producer_path: Path,
    journal_root: Path,
    clock: Callable[[], str] = utc_now,
) -> dict[str, int | str]:
    loaded = load_policy(root, allow_missing=True)
    if loaded is None or not loaded[0]["active"]:
        return {"status": "disabled", "reserved": 0, "retries": 0, "shadowed": 0}
    policy = loaded[0]
    reserved = 0
    retries = 0
    duplicates = 0
    shadowed = 0
    for origin_site in sorted(SITES):
        entry = policy["targets"].get(origin_site, {}).get("feeding_schedule")
        if (
            entry is None
            or entry["owner"] != "bus"
            or entry["mode"] == "disabled"
            or entry["trigger"] != "cat_transfer"
        ):
            continue
        try:
            evidence = _cat_transfer_evidence(
                connection,
                root=root,
                state_path=state_path,
                producer_path=producer_path,
                journal_root=journal_root,
                origin_site=origin_site,
                entry=entry,
                clock=clock,
            )
        except ActionError:
            continue
        if entry["mode"] == "shadow":
            connection.execute(
                """
                INSERT INTO service_counters(name, value) VALUES (?, ?)
                ON CONFLICT(name) DO UPDATE SET value=excluded.value
                """,
                (f"cat_transfer_shadow_{origin_site}", evidence["event_id"]),
            )
            shadowed += 1
            continue
        prior = connection.execute(
            """
            SELECT r.trigger_event_id, r.status, r.reason_code,
                   o.outcome, o.command_attempted
            FROM action_reservations r
            LEFT JOIN action_outcomes o ON o.reservation_id = r.id
            WHERE r.site=? AND r.target_alias='feeding_schedule'
              AND r.vacancy_cycle_id=?
            ORDER BY r.id
            """,
            (origin_site, evidence["cycle_id"]),
        ).fetchall()
        retry = False
        if prior:
            if any(
                row["trigger_event_id"] == evidence["event_id"]
                or row["status"] in {"pending", "claimed", "outcome_unknown"}
                or row["outcome"] in {"state_confirmed", "outcome_unknown"}
                or row["command_attempted"] == 1
                for row in prior
            ):
                duplicates += 1
                continue
            latest = prior[-1]
            retry = bool(
                latest["status"] == "complete"
                and latest["outcome"] == "failed"
                and latest["command_attempted"] == 0
                and latest["reason_code"]
                in CAT_TRANSFER_RETRYABLE_NO_COMMAND_REASONS
            )
            if not retry:
                duplicates += 1
                continue
        result = reserve_action(
            connection,
            root=root,
            state_path=state_path,
            producer_path=producer_path,
            journal_root=journal_root,
            site=origin_site,
            target="feeding_schedule",
            cycle_id=str(evidence["cycle_id"]),
            trigger_state_hash=str(evidence["trigger_state_hash"]),
            trigger_event_id=int(evidence["event_id"]),
            clock=clock,
        )
        reserved += result["status"] == "reserved"
        retries += retry and result["status"] == "reserved"
        duplicates += result["status"] == "duplicate"
    return {
        "status": "ok",
        "reserved": reserved,
        "retries": retries,
        "duplicates": duplicates,
        "shadowed": shadowed,
    }


def _finish(
    connection: sqlite3.Connection,
    reservation_id: int,
    *,
    status: str,
    outcome: str,
    verification: str,
    reason_code: str,
    command_attempted: bool,
    now: str,
) -> None:
    if SAFE_CODE_RE.fullmatch(reason_code) is None:
        raise ActionError("reason_code_invalid")
    connection.execute(
        """
        INSERT INTO action_outcomes(
            reservation_id, outcome, verification, reason_code,
            command_attempted, observed_at
        ) VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            reservation_id,
            outcome,
            verification,
            reason_code,
            1 if command_attempted else 0,
            now,
        ),
    )
    connection.execute(
        """
        UPDATE action_reservations
        SET status = ?, completed_at = ?, reason_code = ?
        WHERE id = ? AND status = 'claimed'
        """,
        (status, now, reason_code, reservation_id),
    )


def _hue_all_off(hue_bin: str, site: str, timeout: int = 15) -> bool:
    try:
        result = subprocess.run(
            [hue_bin, f"--{site}", "raw", "/groups/0"],
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ActionError("hue_readback_unavailable") from exc
    if result.returncode != 0 or len(result.stdout.encode("utf-8")) > MAX_INPUT_BYTES:
        raise ActionError("hue_readback_unavailable")
    try:
        value = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise ActionError("hue_readback_invalid") from exc
    any_on = value.get("state", {}).get("any_on") if isinstance(value, dict) else None
    if not isinstance(any_on, bool):
        raise ActionError("hue_readback_invalid")
    return not any_on


def _suspension_path(root: Path) -> Path:
    return root / "state" / "hue-automation-suspensions.json"


def _validate_suspensions(value: object) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != {"schema_version", "sites", "latest"}:
        raise ActionError("automation_suspension_invalid")
    if value["schema_version"] != 1 or not isinstance(value["sites"], dict):
        raise ActionError("automation_suspension_invalid")
    if not set(value["sites"]).issubset(SITES):
        raise ActionError("automation_suspension_invalid")
    normalized: dict[str, Any] = {"schema_version": 1, "sites": {}, "latest": value["latest"]}
    for site, record in value["sites"].items():
        if not isinstance(record, dict) or set(record) != {
            "cycle_id",
            "automations",
            "restore",
            "phase",
            "updated_at",
            "last_error",
        }:
            raise ActionError("automation_suspension_invalid")
        if (
            not isinstance(record["cycle_id"], str)
            or ID_RE.fullmatch(record["cycle_id"]) is None
            or not record["cycle_id"].startswith("cycle_")
            or record["phase"] not in {"suspending", "suspended", "restoring"}
            or record["last_error"] is not None
            and (
                not isinstance(record["last_error"], str)
                or SAFE_CODE_RE.fullmatch(record["last_error"]) is None
            )
        ):
            raise ActionError("automation_suspension_invalid")
        parse_time(record["updated_at"], "automation_suspension_invalid")
        for key in ("automations", "restore"):
            names = record[key]
            if (
                not isinstance(names, list)
                or len(names) > 16
                or len(set(names)) != len(names)
                or any(
                    not isinstance(name, str)
                    or AUTOMATION_NAME_RE.fullmatch(name) is None
                    for name in names
                )
            ):
                raise ActionError("automation_suspension_invalid")
        if not set(record["restore"]).issubset(record["automations"]):
            raise ActionError("automation_suspension_invalid")
        normalized["sites"][site] = dict(record)
    latest = value["latest"]
    if latest is not None:
        if (
            not isinstance(latest, dict)
            or set(latest) != {"site", "outcome", "count", "at"}
            or latest["site"] not in SITES
            or latest["outcome"] not in {"suspended", "restored"}
            or not isinstance(latest["count"], int)
            or isinstance(latest["count"], bool)
            or not 0 <= latest["count"] <= 16
        ):
            raise ActionError("automation_suspension_invalid")
        parse_time(latest["at"], "automation_suspension_invalid")
        normalized["latest"] = dict(latest)
    return normalized


def _load_suspensions(root: Path) -> dict[str, Any]:
    path = _suspension_path(root)
    if not path.exists() and not path.is_symlink():
        return {"schema_version": 1, "sites": {}, "latest": None}
    return _validate_suspensions(
        _read_json(path, MAX_POLICY_BYTES, "automation_suspension_invalid")
    )


def _write_suspensions(root: Path, value: dict[str, Any]) -> None:
    normalized = _validate_suspensions(value)
    _atomic_private(_suspension_path(root), canonical_json(normalized) + b"\n")


def _hue_automation_inventory(hue_bin: str, site: str) -> dict[str, bool]:
    try:
        result = subprocess.run(
            [hue_bin, f"--{site}", "automations", "--json"],
            capture_output=True,
            text=True,
            timeout=20,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ActionError("automation_readback_unavailable") from exc
    if result.returncode != 0 or len(result.stdout.encode("utf-8")) > MAX_INPUT_BYTES:
        raise ActionError("automation_readback_unavailable")
    try:
        value = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise ActionError("automation_readback_invalid") from exc
    if (
        not isinstance(value, dict)
        or value.get("ok") is not True
        or value.get("site") != site
        or not isinstance(value.get("automations"), list)
    ):
        raise ActionError("automation_readback_invalid")
    inventory: dict[str, bool] = {}
    for item in value["automations"]:
        if (
            not isinstance(item, dict)
            or not isinstance(item.get("name"), str)
            or AUTOMATION_NAME_RE.fullmatch(item["name"]) is None
            or not isinstance(item.get("enabled"), bool)
            or item["name"] in inventory
        ):
            raise ActionError("automation_readback_invalid")
        inventory[item["name"]] = item["enabled"]
    return inventory


def _hue_automation_set(hue_bin: str, site: str, name: str, enabled: bool) -> bool:
    action = "enable" if enabled else "disable"
    try:
        result = subprocess.run(
            [hue_bin, f"--{site}", "automation", action, name],
            capture_output=True,
            text=True,
            timeout=20,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ActionError("automation_command_failed") from exc
    if result.returncode != 0 or len(result.stdout.encode("utf-8")) > 4096:
        raise ActionError("automation_command_failed")
    try:
        value = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise ActionError("automation_command_invalid") from exc
    if (
        not isinstance(value, dict)
        or value.get("ok") is not True
        or value.get("site") != site
        or value.get("name") != name
        or value.get("enabled") is not enabled
        or not isinstance(value.get("changed"), bool)
    ):
        raise ActionError("automation_command_invalid")
    return bool(value["changed"])


def _suspend_automations(
    root: Path,
    *,
    site: str,
    cycle_id: str,
    names: Sequence[str],
    hue_bin: str,
    clock: Callable[[], str],
) -> tuple[bool, str]:
    state = _load_suspensions(root)
    existing = state["sites"].get(site)
    if existing is not None and existing["cycle_id"] != cycle_id:
        raise ActionError("automation_suspension_cycle_conflict")
    inventory = _hue_automation_inventory(hue_bin, site)
    if any(name not in inventory for name in names):
        raise ActionError("automation_binding_missing")
    if existing is None:
        restore = [name for name in names if inventory[name]]
        existing = {
            "cycle_id": cycle_id,
            "automations": list(names),
            "restore": restore,
            "phase": "suspending",
            "updated_at": clock(),
            "last_error": None,
        }
        state["sites"][site] = existing
        _write_suspensions(root, state)
    attempted = False
    for name in existing["automations"]:
        if inventory[name]:
            attempted = _hue_automation_set(hue_bin, site, name, False) or attempted
    confirmed = _hue_automation_inventory(hue_bin, site)
    if any(confirmed.get(name) is not False for name in existing["automations"]):
        raise ActionError("automation_verification_failed")
    existing["phase"] = "suspended"
    existing["updated_at"] = clock()
    existing["last_error"] = None
    state["latest"] = {
        "site": site,
        "outcome": "suspended",
        "count": len(existing["restore"]),
        "at": existing["updated_at"],
    }
    _write_suspensions(root, state)
    return attempted, "completed" if existing["restore"] else "already_satisfied"


def _occupied_by_sticky_resident(canonical: Mapping[str, Any], site: str) -> bool:
    people = canonical.get("people")
    return isinstance(people, dict) and any(
        isinstance(person, dict) and person.get("location") == site
        for person in people.values()
    )


def _feeder_suspension_path(root: Path) -> Path:
    return root / "state" / "feeder-schedule-suspensions.json"


def _empty_feeder_suspensions() -> dict[str, Any]:
    return {"schema_version": 2, "sites": {}, "latest": None}


def _validate_feeder_suspensions(value: object) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != {"schema_version", "sites", "latest"}:
        raise ActionError("feeder_suspension_invalid")
    schema_version = value["schema_version"]
    if schema_version not in {1, 2} or not isinstance(value["sites"], dict):
        raise ActionError("feeder_suspension_invalid")
    if not set(value["sites"]).issubset(SITES):
        raise ActionError("feeder_suspension_invalid")
    normalized = _empty_feeder_suspensions()
    for site, record in value["sites"].items():
        legacy_keys = {
            "selector",
            "cycle_id",
            "phase",
            "restore_owned",
            "updated_at",
            "last_error",
        }
        expected_keys = (
            legacy_keys
            if schema_version == 1
            else legacy_keys | {"occupancy_context"}
        )
        if not isinstance(record, dict) or set(record) != expected_keys:
            raise ActionError("feeder_suspension_invalid")
        if (
            record["selector"] != FEEDER_SELECTORS[site]
            or not isinstance(record["cycle_id"], str)
            or ID_RE.fullmatch(record["cycle_id"]) is None
            or not record["cycle_id"].startswith("cycle_")
            or record["phase"] not in {"suspending", "suspended", "restoring"}
            or record["restore_owned"] is not True
            or record["last_error"] is not None
            and (
                not isinstance(record["last_error"], str)
                or SAFE_CODE_RE.fullmatch(record["last_error"]) is None
            )
            or schema_version == 2
            and record["occupancy_context"]
            not in {"origin_vacant", "split_household"}
        ):
            raise ActionError("feeder_suspension_invalid")
        parse_time(record["updated_at"], "feeder_suspension_invalid")
        normalized["sites"][site] = {
            **record,
            "occupancy_context": (
                record["occupancy_context"]
                if schema_version == 2
                else "origin_vacant"
            ),
        }
    latest = value["latest"]
    if latest is not None:
        if (
            not isinstance(latest, dict)
            or set(latest) != {"site", "outcome", "at"}
            or latest["site"] not in SITES
            or latest["outcome"] not in {
                "suspended",
                "restored",
                "already_satisfied_manual",
            }
        ):
            raise ActionError("feeder_suspension_invalid")
        parse_time(latest["at"], "feeder_suspension_invalid")
        normalized["latest"] = dict(latest)
    return normalized


def _load_feeder_suspensions(root: Path) -> dict[str, Any]:
    path = _feeder_suspension_path(root)
    if not path.exists() and not path.is_symlink():
        return _empty_feeder_suspensions()
    return _validate_feeder_suspensions(
        _read_json(path, MAX_POLICY_BYTES, "feeder_suspension_invalid")
    )


def _write_feeder_suspensions(root: Path, value: dict[str, Any]) -> None:
    normalized = _validate_feeder_suspensions(value)
    _atomic_private(
        _feeder_suspension_path(root), canonical_json(normalized) + b"\n"
    )


def _petlibro_schedule_state(
    petlibro_bin: str, selector: str, site: str, *, now: datetime
) -> dict[str, Any]:
    try:
        result = subprocess.run(
            [petlibro_bin, "--json", "schedule-state", selector],
            capture_output=True,
            text=True,
            timeout=20,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ActionError("feeder_readback_unavailable") from exc
    if result.returncode != 0 or not result.stdout or len(result.stdout.encode("utf-8")) > 4096:
        raise ActionError("feeder_readback_unavailable")
    try:
        value = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise ActionError("feeder_readback_invalid") from exc
    if (
        not isinstance(value, dict)
        or set(value)
        != {
            "success",
            "selector",
            "site",
            "online",
            "scheduleEnabled",
            "enabledMealCount",
            "observedAt",
        }
        or value["success"] is not True
        or value["selector"] != selector
        or value["site"] != site
        or value["online"] is not True
        or not isinstance(value["scheduleEnabled"], bool)
        or not isinstance(value["enabledMealCount"], int)
        or isinstance(value["enabledMealCount"], bool)
        or not 0 <= value["enabledMealCount"] <= 64
    ):
        raise ActionError("feeder_readback_invalid")
    observed = parse_time(value["observedAt"], "feeder_readback_invalid")
    if observed > now + FUTURE_TOLERANCE or now - observed > timedelta(minutes=5):
        raise ActionError("feeder_readback_stale")
    return value


def _petlibro_schedule_set(
    petlibro_bin: str, selector: str, site: str, enabled: bool
) -> bool:
    try:
        result = subprocess.run(
            [petlibro_bin, "--json", "schedule-set", selector, "on" if enabled else "off"],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ActionError("feeder_outcome_unknown", command_attempted=True) from exc
    if result.returncode != 0 or not result.stdout or len(result.stdout.encode("utf-8")) > 4096:
        raise ActionError("feeder_outcome_unknown", command_attempted=True)
    try:
        value = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise ActionError("feeder_outcome_unknown", command_attempted=True) from exc
    required = {
        "success",
        "device",
        "location",
        "scheduleEnabled",
        "action",
        "accepted",
        "verified",
        "mutation_attempted",
    }
    if (
        not isinstance(value, dict)
        or not required.issubset(value)
        or set(value) - required - {"request_id"}
        or value["success"] is not True
        or value["device"] != selector
        or value["location"] != site
        or value["scheduleEnabled"] is not enabled
        or value["accepted"] is not True
        or value["verified"] is not True
        or not isinstance(value["mutation_attempted"], bool)
    ):
        raise ActionError("feeder_outcome_unknown", command_attempted=True)
    return bool(value["mutation_attempted"])


def _restore_owned_feeder(
    root: Path,
    state: dict[str, Any],
    *,
    site: str,
    petlibro_bin: str,
    clock: Callable[[], str],
) -> bool:
    record = state["sites"].get(site)
    if record is None:
        return False
    now = parse_time(clock(), "clock_invalid")
    observed = _petlibro_schedule_state(
        petlibro_bin, FEEDER_SELECTORS[site], site, now=now
    )
    attempted = False
    if not observed["scheduleEnabled"]:
        record["phase"] = "restoring"
        record["updated_at"] = clock()
        record["last_error"] = None
        _write_feeder_suspensions(root, state)
        attempted = _petlibro_schedule_set(
            petlibro_bin, FEEDER_SELECTORS[site], site, True
        )
        try:
            observed = _petlibro_schedule_state(
                petlibro_bin,
                FEEDER_SELECTORS[site],
                site,
                now=parse_time(clock(), "clock_invalid"),
            )
        except ActionError as exc:
            raise ActionError(
                exc.code,
                command_attempted=attempted or exc.command_attempted,
            ) from exc
    if not observed["scheduleEnabled"] or observed["enabledMealCount"] < 1:
        raise ActionError("destination_schedule_unavailable")
    del state["sites"][site]
    state["latest"] = {"site": site, "outcome": "restored", "at": clock()}
    _write_feeder_suspensions(root, state)
    return attempted


def _transfer_feeding_schedule(
    connection: sqlite3.Connection,
    root: Path,
    *,
    origin_site: str,
    cycle_id: str,
    entry: Mapping[str, Any],
    state_path: Path,
    producer_path: Path,
    journal_root: Path,
    petlibro_bin: str,
    clock: Callable[[], str],
) -> tuple[bool, str]:
    evidence = _cat_transfer_evidence(
        connection,
        root=root,
        state_path=state_path,
        producer_path=producer_path,
        journal_root=journal_root,
        origin_site=origin_site,
        entry=entry,
        clock=clock,
    )
    if evidence["cycle_id"] != cycle_id:
        raise ActionError("vacancy_cycle_mismatch")
    destination = OTHER_SITE[origin_site]
    state = _load_feeder_suspensions(root)
    attempted = False
    destination_state = _petlibro_schedule_state(
        petlibro_bin,
        FEEDER_SELECTORS[destination],
        destination,
        now=parse_time(clock(), "clock_invalid"),
    )
    if not destination_state["scheduleEnabled"]:
        if destination not in state["sites"]:
            raise ActionError("destination_schedule_manually_disabled")
        attempted = _restore_owned_feeder(
            root,
            state,
            site=destination,
            petlibro_bin=petlibro_bin,
            clock=clock,
        ) or attempted
        state = _load_feeder_suspensions(root)
        destination_state = _petlibro_schedule_state(
            petlibro_bin,
            FEEDER_SELECTORS[destination],
            destination,
            now=parse_time(clock(), "clock_invalid"),
        )
    elif destination in state["sites"]:
        attempted = _restore_owned_feeder(
            root,
            state,
            site=destination,
            petlibro_bin=petlibro_bin,
            clock=clock,
        ) or attempted
        state = _load_feeder_suspensions(root)
    try:
        if (
            not destination_state["scheduleEnabled"]
            or destination_state["enabledMealCount"] < 1
        ):
            raise ActionError("destination_schedule_unavailable")
        _cat_transfer_evidence(
            connection,
            root=root,
            state_path=state_path,
            producer_path=producer_path,
            journal_root=journal_root,
            origin_site=origin_site,
            entry=entry,
            clock=clock,
        )
        origin_observed = _petlibro_schedule_state(
            petlibro_bin,
            FEEDER_SELECTORS[origin_site],
            origin_site,
            now=parse_time(clock(), "clock_invalid"),
        )
    except ActionError as exc:
        raise ActionError(
            exc.code,
            command_attempted=attempted or exc.command_attempted,
        ) from exc
    existing = state["sites"].get(origin_site)
    if not origin_observed["scheduleEnabled"]:
        if existing is not None and existing["cycle_id"] == cycle_id:
            existing["phase"] = "suspended"
            existing["updated_at"] = clock()
            existing["last_error"] = None
            state["latest"] = {
                "site": origin_site,
                "outcome": "suspended",
                "at": existing["updated_at"],
            }
            _write_feeder_suspensions(root, state)
            return attempted, "already_satisfied"
        state["latest"] = {
            "site": origin_site,
            "outcome": "already_satisfied_manual",
            "at": clock(),
        }
        _write_feeder_suspensions(root, state)
        return attempted, "already_satisfied_manual"
    if existing is not None and existing["cycle_id"] != cycle_id:
        raise ActionError("feeder_suspension_cycle_conflict")
    if existing is None:
        state["sites"][origin_site] = {
            "selector": FEEDER_SELECTORS[origin_site],
            "cycle_id": cycle_id,
            "phase": "suspending",
            "restore_owned": True,
            "occupancy_context": "origin_vacant",
            "updated_at": clock(),
            "last_error": None,
        }
        _write_feeder_suspensions(root, state)
    attempted = _petlibro_schedule_set(
        petlibro_bin, FEEDER_SELECTORS[origin_site], origin_site, False
    ) or attempted
    confirmed = _petlibro_schedule_state(
        petlibro_bin,
        FEEDER_SELECTORS[origin_site],
        origin_site,
        now=parse_time(clock(), "clock_invalid"),
    )
    if confirmed["scheduleEnabled"]:
        raise ActionError("feeder_outcome_unknown", command_attempted=True)
    state = _load_feeder_suspensions(root)
    record = state["sites"][origin_site]
    record["phase"] = "suspended"
    record["updated_at"] = clock()
    record["last_error"] = None
    state["latest"] = {
        "site": origin_site,
        "outcome": "suspended",
        "at": record["updated_at"],
    }
    _write_feeder_suspensions(root, state)
    return attempted, "completed"


def _reconcile_automation_suspensions(
    root: Path,
    *,
    state_path: Path,
    producer_path: Path,
    hue_bin: str,
    clock: Callable[[], str],
) -> dict[str, Any]:
    state = _load_suspensions(root)
    if not state["sites"]:
        return {"mode": "none", "changed": 0}
    changed = 0
    deferred = 0
    for site in sorted(list(state["sites"])):
        record = state["sites"][site]
        try:
            canonical, site_state, _producer_hash = _verified_presence_snapshot(
                site,
                state_path=state_path,
                producer_path=producer_path,
                now=parse_time(clock(), "clock_invalid"),
            )
        except ActionError:
            deferred += 1
            continue
        occupancy = site_state.get("occupancy")
        if occupancy == "confirmed_vacant":
            try:
                inventory = _hue_automation_inventory(hue_bin, site)
                if any(name not in inventory for name in record["automations"]):
                    raise ActionError("automation_binding_missing")
                for name in record["automations"]:
                    if inventory[name]:
                        changed += _hue_automation_set(hue_bin, site, name, False)
                confirmed = _hue_automation_inventory(hue_bin, site)
                if any(confirmed.get(name) is not False for name in record["automations"]):
                    raise ActionError("automation_verification_failed")
            except ActionError as exc:
                record["last_error"] = exc.code
                record["updated_at"] = clock()
                _write_suspensions(root, state)
                deferred += 1
            continue
        if occupancy != "occupied" or not _occupied_by_sticky_resident(canonical, site):
            deferred += 1
            continue
        record["phase"] = "restoring"
        record["updated_at"] = clock()
        _write_suspensions(root, state)
        try:
            inventory = _hue_automation_inventory(hue_bin, site)
            if any(name not in inventory for name in record["restore"]):
                raise ActionError("automation_binding_missing")
            for name in record["restore"]:
                if not inventory[name]:
                    changed += _hue_automation_set(hue_bin, site, name, True)
            confirmed = _hue_automation_inventory(hue_bin, site)
            if any(confirmed.get(name) is not True for name in record["restore"]):
                raise ActionError("automation_verification_failed")
        except ActionError as exc:
            record["last_error"] = exc.code
            record["updated_at"] = clock()
            _write_suspensions(root, state)
            deferred += 1
            continue
        restored_count = len(record["restore"])
        del state["sites"][site]
        state["latest"] = {
            "site": site,
            "outcome": "restored",
            "count": restored_count,
            "at": clock(),
        }
        _write_suspensions(root, state)
    return {
        "mode": "reconciled" if changed else "deferred" if deferred else "verified",
        "changed": changed,
        "deferred": deferred,
    }


def _reconcile_feeder_suspensions(
    root: Path,
    *,
    state_path: Path,
    producer_path: Path,
    journal_root: Path,
    petlibro_bin: str,
    clock: Callable[[], str],
) -> dict[str, Any]:
    state = _load_feeder_suspensions(root)
    if not state["sites"]:
        return {
            "mode": "none",
            "changed": 0,
            "deferred": 0,
            "outcome_unknown": 0,
        }
    loaded = load_policy(root, allow_missing=True)
    if loaded is None or not loaded[0]["active"]:
        return {
            "mode": "disabled",
            "changed": 0,
            "deferred": len(state["sites"]),
            "outcome_unknown": 0,
        }
    policy = loaded[0]
    paths = validate_runtime(root)
    store = EventStore(paths, clock=clock)
    changed = 0
    deferred = 0
    outcome_unknown = 0
    with contextlib.closing(store.connect(read_only=True)) as connection:
        for site in sorted(list(state["sites"])):
            record = state["sites"][site]
            entry = policy["targets"].get(site, {}).get("feeding_schedule")
            if (
                entry is None
                or entry["owner"] != "bus"
                or entry["mode"] != "active"
            ):
                deferred += 1
                continue
            try:
                observed = _petlibro_schedule_state(
                    petlibro_bin,
                    FEEDER_SELECTORS[site],
                    site,
                    now=parse_time(clock(), "clock_invalid"),
                )
                if record["phase"] == "suspending":
                    if observed["scheduleEnabled"]:
                        record["last_error"] = "feeder_outcome_unknown"
                        record["updated_at"] = clock()
                        _write_feeder_suspensions(root, state)
                        deferred += 1
                        continue
                    record["phase"] = "suspended"
                    record["last_error"] = None
                    record["updated_at"] = clock()
                    _write_feeder_suspensions(root, state)
                elif record["phase"] == "restoring":
                    if observed["scheduleEnabled"] and observed["enabledMealCount"] >= 1:
                        del state["sites"][site]
                        state["latest"] = {
                            "site": site,
                            "outcome": "restored",
                            "at": clock(),
                        }
                        _write_feeder_suspensions(root, state)
                        changed += 1
                    else:
                        record["last_error"] = "feeder_outcome_unknown"
                        record["updated_at"] = clock()
                        _write_feeder_suspensions(root, state)
                        deferred += 1
                    continue
                canonical, site_state, _ = _verified_presence_snapshot(
                    site,
                    state_path=state_path,
                    producer_path=producer_path,
                    now=parse_time(clock(), "clock_invalid"),
                )
                if site_state.get("occupancy") == "confirmed_vacant":
                    _cat_transfer_evidence(
                        connection,
                        root=root,
                        state_path=state_path,
                        producer_path=producer_path,
                        journal_root=journal_root,
                        origin_site=site,
                        entry=entry,
                        clock=clock,
                    )
                    if observed["scheduleEnabled"]:
                        changed += _petlibro_schedule_set(
                            petlibro_bin, FEEDER_SELECTORS[site], site, False
                        )
                        confirmed = _petlibro_schedule_state(
                            petlibro_bin,
                            FEEDER_SELECTORS[site],
                            site,
                            now=parse_time(clock(), "clock_invalid"),
                        )
                        if confirmed["scheduleEnabled"]:
                            raise ActionError(
                                "feeder_outcome_unknown", command_attempted=True
                            )
                    if (
                        record["last_error"] is not None
                        or record["occupancy_context"] != "origin_vacant"
                    ):
                        record["last_error"] = None
                        record["occupancy_context"] = "origin_vacant"
                        record["updated_at"] = clock()
                        _write_feeder_suspensions(root, state)
                    continue
                if (
                    site_state.get("occupancy") != "occupied"
                    or not _occupied_by_sticky_resident(canonical, site)
                ):
                    deferred += 1
                    continue
                return_origin = OTHER_SITE[site]
                return_site_state = canonical.get(return_origin)
                if (
                    isinstance(return_site_state, dict)
                    and return_site_state.get("occupancy") == "occupied"
                    and _occupied_by_sticky_resident(canonical, return_origin)
                ):
                    if observed["scheduleEnabled"]:
                        record["last_error"] = "split_feeder_unexpectedly_enabled"
                        record["updated_at"] = clock()
                        _write_feeder_suspensions(root, state)
                        deferred += 1
                        continue
                    changed_context = (
                        record["occupancy_context"] != "split_household"
                    )
                    recovered_context = (
                        record["last_error"] == "site_not_confirmed_vacant"
                    )
                    if changed_context or recovered_context:
                        record["occupancy_context"] = "split_household"
                        if recovered_context:
                            record["last_error"] = None
                        record["updated_at"] = clock()
                        _write_feeder_suspensions(root, state)
                    continue
                return_entry = policy["targets"].get(return_origin, {}).get(
                    "feeding_schedule"
                )
                if return_entry is None:
                    deferred += 1
                    continue
                _cat_transfer_evidence(
                    connection,
                    root=root,
                    state_path=state_path,
                    producer_path=producer_path,
                    journal_root=journal_root,
                    origin_site=return_origin,
                    entry=return_entry,
                    clock=clock,
                )
                changed += _restore_owned_feeder(
                    root,
                    state,
                    site=site,
                    petlibro_bin=petlibro_bin,
                    clock=clock,
                )
            except ActionError as exc:
                current = state["sites"].get(site)
                unresolved_phase = bool(
                    current is not None
                    and current["phase"] in {"suspending", "restoring"}
                )
                if current is not None:
                    current["last_error"] = exc.code
                    current["updated_at"] = clock()
                    _write_feeder_suspensions(root, state)
                outcome_unknown += int(exc.command_attempted or unresolved_phase)
                deferred += 1
    return {
        "mode": "reconciled" if changed else "deferred" if deferred else "verified",
        "changed": changed,
        "deferred": deferred,
        "outcome_unknown": outcome_unknown,
    }


def _run_worker_once_locked(
    root: Path,
    *,
    state_path: Path = DEFAULT_PRESENCE_STATE,
    producer_path: Path = DEFAULT_PRODUCER_STATE,
    journal_root: Path = DEFAULT_JOURNAL_ROOT,
    hue_bin: str = "/opt/homebrew/bin/hue",
    petlibro_bin: str = str(Path("~/.openclaw/bin/petlibro").expanduser()),
    clock: Callable[[], str] = utc_now,
    sleeper: Callable[[float], None] = time.sleep,
) -> dict[str, Any]:
    paths = validate_runtime(root)
    store = EventStore(paths, clock=clock)
    store.check_schema()
    automation_reconcile = _reconcile_automation_suspensions(
        root,
        state_path=state_path,
        producer_path=producer_path,
        hue_bin=hue_bin,
        clock=clock,
    )
    feeder_reconcile = _reconcile_feeder_suspensions(
        root,
        state_path=state_path,
        producer_path=producer_path,
        journal_root=journal_root,
        petlibro_bin=petlibro_bin,
        clock=clock,
    )
    if feeder_reconcile["outcome_unknown"]:
        store.write_status_best_effort()
        return {
            "ok": True,
            "mode": "deferred",
            "recovered": 0,
            "expired": 0,
            "automation_reconcile": automation_reconcile,
            "feeder_reconcile": feeder_reconcile,
        }
    now_text = clock()
    now = parse_time(now_text, "clock_invalid")
    recovered = 0
    expired = 0
    with contextlib.closing(store.connect()) as connection:
        connection.execute("BEGIN IMMEDIATE")
        for row in connection.execute(
            "SELECT id FROM action_reservations WHERE status = 'claimed' ORDER BY id"
        ).fetchall():
            _finish(
                connection,
                int(row["id"]),
                status="outcome_unknown",
                outcome="outcome_unknown",
                verification="none",
                reason_code="interrupted",
                command_attempted=True,
                now=format_time(now),
            )
            recovered += 1
        for row in connection.execute(
            """
            SELECT id FROM action_reservations
            WHERE status = 'pending' AND expires_at <= ? ORDER BY id
            """,
            (format_time(now),),
        ).fetchall():
            connection.execute(
                "UPDATE action_reservations SET status='claimed', claimed_at=? WHERE id=?",
                (format_time(now), row["id"]),
            )
            _finish(
                connection,
                int(row["id"]),
                status="cancelled",
                outcome="cancelled",
                verification="none",
                reason_code="reservation_expired",
                command_attempted=False,
                now=format_time(now),
            )
            expired += 1
        reservation = connection.execute(
            """
            SELECT * FROM action_reservations
            WHERE status = 'pending' AND expires_at > ?
            ORDER BY id LIMIT 1
            """,
            (format_time(now),),
        ).fetchone()
        if reservation is None:
            connection.commit()
            store.write_status_best_effort()
            return {
                "ok": True,
                "mode": "idle",
                "recovered": recovered,
                "expired": expired,
                "automation_reconcile": automation_reconcile,
                "feeder_reconcile": feeder_reconcile,
            }
        changed = connection.execute(
            """
            UPDATE action_reservations
            SET status='claimed', attempt_count=1, claimed_at=?
            WHERE id=? AND status='pending' AND attempt_count=0
            """,
            (format_time(now), reservation["id"]),
        )
        if changed.rowcount != 1:
            raise ActionError("reservation_claim_failed")
        connection.commit()

    reservation_id = int(reservation["id"])
    command_attempted = False
    try:
        loaded = load_policy(root)
        assert loaded is not None
        policy, current_policy_hash = loaded
        entry = policy["targets"].get(reservation["site"], {}).get(
            reservation["target_alias"]
        )
        if (
            not policy["active"]
            or entry is None
            or entry["owner"] != "bus"
            or entry["mode"] != "active"
            or current_policy_hash != reservation["policy_hash"]
        ):
            raise ActionError("policy_changed")
        validate_presence(
            str(reservation["site"]),
            str(reservation["vacancy_cycle_id"]),
            state_path=state_path,
            producer_path=producer_path,
            journal_root=journal_root,
            now=parse_time(clock(), "clock_invalid"),
        )
        target = str(reservation["target_alias"])
        if target == "all_lights":
            if _hue_all_off(hue_bin, str(reservation["site"])):
                status, outcome, verification, reason = (
                    "complete",
                    "state_confirmed",
                    "state_confirmed",
                    "already_satisfied",
                )
            else:
                command_attempted = True
                try:
                    command = subprocess.run(
                        [hue_bin, f"--{reservation['site']}", "all-off"],
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                        timeout=15,
                        check=False,
                    )
                except (OSError, subprocess.TimeoutExpired) as exc:
                    raise ActionError("command_failed") from exc
                if command.returncode != 0:
                    raise ActionError("command_failed")
                deadline = time.monotonic() + int(entry["settle_seconds"])
                confirmed = False
                while True:
                    try:
                        confirmed = _hue_all_off(hue_bin, str(reservation["site"]))
                    except ActionError:
                        confirmed = False
                    if confirmed or time.monotonic() >= deadline:
                        break
                    sleeper(1)
                if confirmed:
                    status, outcome, verification, reason = (
                        "complete",
                        "state_confirmed",
                        "state_confirmed",
                        "completed",
                    )
                else:
                    status, outcome, verification, reason = (
                        "outcome_unknown",
                        "outcome_unknown",
                        "none",
                        "verification_failed",
                    )
        elif target == "daily_automations":
            command_attempted = True
            attempted, reason = _suspend_automations(
                root,
                site=str(reservation["site"]),
                cycle_id=str(reservation["vacancy_cycle_id"]),
                names=entry["automations"],
                hue_bin=hue_bin,
                clock=clock,
            )
            command_attempted = attempted
            status, outcome, verification = (
                "complete",
                "state_confirmed",
                "state_confirmed",
            )
        elif target == "feeding_schedule":
            with contextlib.closing(store.connect(read_only=True)) as connection:
                attempted, reason = _transfer_feeding_schedule(
                    connection,
                    root,
                    origin_site=str(reservation["site"]),
                    cycle_id=str(reservation["vacancy_cycle_id"]),
                    entry=entry,
                    state_path=state_path,
                    producer_path=producer_path,
                    journal_root=journal_root,
                    petlibro_bin=petlibro_bin,
                    clock=clock,
                )
            command_attempted = attempted
            status, outcome, verification = (
                "complete",
                "state_confirmed",
                "state_confirmed",
            )
        else:
            raise ActionError("target_invalid")
    except ActionError as exc:
        command_attempted = command_attempted or exc.command_attempted
        if exc.code in {
            "policy_changed",
            "presence_state_invalid",
            "producer_state_invalid",
            "presence_state_mismatch",
            "presence_state_stale",
            "site_not_confirmed_vacant",
            "vacancy_cycle_invalid",
            "vacancy_cycle_mismatch",
            "presence_time_invalid",
        }:
            status, outcome, verification, reason = (
                "cancelled",
                "cancelled",
                "none",
                exc.code,
            )
        elif exc.code == "command_failed":
            status, outcome, verification, reason = (
                "complete",
                "failed",
                "command_exit",
                exc.code,
            )
        elif command_attempted:
            status, outcome, verification, reason = (
                "outcome_unknown",
                "outcome_unknown",
                "none",
                exc.code,
            )
        else:
            status, outcome, verification, reason = (
                "complete",
                "failed",
                "none",
                exc.code,
            )

    completed_at = clock()
    with contextlib.closing(store.connect()) as connection:
        connection.execute("BEGIN IMMEDIATE")
        _finish(
            connection,
            reservation_id,
            status=status,
            outcome=outcome,
            verification=verification,
            reason_code=reason,
            command_attempted=command_attempted,
            now=completed_at,
        )
        connection.commit()
    store.write_status_best_effort()
    return {
        "ok": True,
        "mode": "processed",
        "site": reservation["site"],
        "target": reservation["target_alias"],
        "outcome": outcome,
        "reason_code": reason,
        "command_attempted": command_attempted,
        "automation_reconcile": automation_reconcile,
        "feeder_reconcile": feeder_reconcile,
    }


def run_worker_once(
    root: Path,
    *,
    state_path: Path = DEFAULT_PRESENCE_STATE,
    producer_path: Path = DEFAULT_PRODUCER_STATE,
    journal_root: Path = DEFAULT_JOURNAL_ROOT,
    hue_bin: str = "/opt/homebrew/bin/hue",
    petlibro_bin: str = str(Path("~/.openclaw/bin/petlibro").expanduser()),
    clock: Callable[[], str] = utc_now,
    sleeper: Callable[[float], None] = time.sleep,
) -> dict[str, Any]:
    paths = validate_runtime(root)
    descriptor = os.open(paths.action_lock, os.O_RDWR | os.O_NOFOLLOW)
    try:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return {"ok": True, "mode": "already_running"}
        return _run_worker_once_locked(
            root,
            state_path=state_path,
            producer_path=producer_path,
            journal_root=journal_root,
            hue_bin=hue_bin,
            petlibro_bin=petlibro_bin,
            clock=clock,
            sleeper=sleeper,
        )
    finally:
        os.close(descriptor)


def reserve_current_canary(
    root: Path,
    site: str,
    target: str,
    *,
    state_path: Path = DEFAULT_PRESENCE_STATE,
    producer_path: Path = DEFAULT_PRODUCER_STATE,
    journal_root: Path = DEFAULT_JOURNAL_ROOT,
    clock: Callable[[], str] = utc_now,
) -> dict[str, Any]:
    cycle = _read_json(
        journal_root / "cycles" / f"{site}.json", 4096, "vacancy_cycle_invalid"
    )
    cycle_id = cycle.get("cycle_id")
    trigger_hash = validate_presence(
        site,
        cycle_id,
        state_path=state_path,
        producer_path=producer_path,
        journal_root=journal_root,
        now=parse_time(clock(), "clock_invalid"),
    )
    paths = validate_runtime(root)
    store = EventStore(paths, clock=clock)
    store.check_schema()
    with contextlib.closing(store.connect()) as connection:
        connection.execute("BEGIN IMMEDIATE")
        result = reserve_action(
            connection,
            root=root,
            state_path=state_path,
            producer_path=producer_path,
            journal_root=journal_root,
            site=site,
            target=target,
            cycle_id=cycle_id,
            trigger_state_hash=trigger_hash,
            trigger_event_id=None,
            clock=clock,
        )
        connection.commit()
    store.write_status_best_effort()
    return {"ok": True, **result}


def safe_status(root: Path) -> dict[str, Any]:
    paths = validate_runtime(root)
    store = EventStore(paths)
    store.check_schema()
    loaded = load_policy(root, allow_missing=True)
    suspensions = _load_suspensions(root)
    feeder_suspensions = _load_feeder_suspensions(root)
    with contextlib.closing(store.connect(read_only=True)) as connection:
        counts = {
            row["status"]: row["count"]
            for row in connection.execute(
                "SELECT status, COUNT(*) AS count FROM action_reservations GROUP BY status"
            )
        }
        latest = connection.execute(
            """
            SELECT site, target_alias, status, reason_code, completed_at
            FROM action_reservations ORDER BY id DESC LIMIT 1
            """
        ).fetchone()
        recent_cat_transfers = connection.execute(
            """
            SELECT r.site AS origin_site, r.completed_at AS occurred_at,
                   o.command_attempted
            FROM action_reservations r
            JOIN action_outcomes o ON o.reservation_id = r.id
            WHERE r.target_alias = 'feeding_schedule'
              AND r.status = 'complete'
              AND o.outcome = 'state_confirmed'
              AND r.completed_at IS NOT NULL
            ORDER BY r.id DESC
            LIMIT 8
            """
        ).fetchall()
    return {
        "ok": True,
        "policy": {
            "configured": loaded is not None,
            "active": loaded[0]["active"] if loaded is not None else False,
            "schema_version": loaded[0]["schema_version"] if loaded is not None else None,
            "bus_targets": (
                sum(
                    entry["owner"] == "bus" and entry["mode"] == "active"
                    for site in SITES
                    for entry in loaded[0]["targets"][site].values()
                )
                if loaded is not None
                else 0
            ),
        },
        "automation_suspensions": {
            "active_sites": sorted(suspensions["sites"]),
            "active_count": len(suspensions["sites"]),
            "latest": suspensions["latest"],
        },
        "feeder_suspensions": {
            "active_sites": sorted(feeder_suspensions["sites"]),
            "active_count": len(feeder_suspensions["sites"]),
            "sites": {
                site: {
                    "selector": record["selector"],
                    "phase": record["phase"],
                    "occupancy_context": record["occupancy_context"],
                    "attention": (
                        record["last_error"] is not None
                        and record["last_error"] not in FEEDER_WAITING_REASONS
                    ),
                    "waiting_reason": (
                        record["last_error"]
                        if record["last_error"] in FEEDER_WAITING_REASONS
                        else None
                    ),
                    "last_error": record["last_error"],
                }
                for site, record in sorted(feeder_suspensions["sites"].items())
            },
            "latest": feeder_suspensions["latest"],
        },
        "cat_transfers": {
            "recent": [
                {
                    "origin_site": row["origin_site"],
                    "destination_site": OTHER_SITE[row["origin_site"]],
                    "occurred_at": row["occurred_at"],
                    "schedule_changed": bool(row["command_attempted"]),
                }
                for row in recent_cat_transfers
            ]
        },
        "counts": {
            status: counts.get(status, 0)
            for status in (
                "pending",
                "claimed",
                "complete",
                "cancelled",
                "outcome_unknown",
            )
        },
        "latest": dict(latest) if latest is not None else None,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=DEFAULT_ROOT)
    commands = parser.add_subparsers(dest="command", required=True)
    install = commands.add_parser("install-policy")
    install.set_defaults(read_stdin=True)
    own = commands.add_parser("ownership")
    own.add_argument("--site", required=True, choices=sorted(SITES))
    own.add_argument("--target", required=True)
    commands.add_parser("run-once")
    canary = commands.add_parser("canary")
    canary.add_argument("--site", required=True, choices=sorted(SITES))
    canary.add_argument("--target", required=True)
    commands.add_parser("status")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    os.umask(0o077)
    args = build_parser().parse_args(argv)
    root = args.root.expanduser()
    if not root.is_absolute():
        print(json.dumps({"ok": False, "error": "root_not_absolute"}))
        return 2
    try:
        if args.command == "install-policy":
            result = install_policy(root, sys.stdin.buffer.read(MAX_POLICY_BYTES + 1))
        elif args.command == "ownership":
            result = {"ok": True, "owner": ownership(root, args.site, args.target)}
        elif args.command == "run-once":
            result = run_worker_once(root)
        elif args.command == "canary":
            result = reserve_current_canary(root, args.site, args.target)
        else:
            result = safe_status(root)
    except (ActionError, sqlite3.Error, OSError) as exc:
        code = exc.code if isinstance(exc, ActionError) else "internal_error"
        print(json.dumps({"ok": False, "error": code}, sort_keys=True))
        return 2
    print(json.dumps(result, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
