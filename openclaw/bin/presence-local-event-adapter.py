#!/usr/bin/env python3
"""Derive shadow-only local arrival and departure events from safe presence scans."""

from __future__ import annotations

import copy
import fcntl
import hashlib
import json
import os
import re
import stat
import subprocess
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable


VERSION = 1
SITES = ("cabin", "crosstown")
TRACKED_PEOPLE = {"Dylan": "dylan", "Julia": "julia"}
SAFE_PEOPLE = frozenset(TRACKED_PEOPLE.values())
PERSON_STATES = frozenset(
    {"uninitialized", "present", "departure_candidate", "locally_away"}
)
SCAN_MAX_AGE = timedelta(minutes=30)
FUTURE_TOLERANCE = timedelta(minutes=5)
RECENT_POSITIVE_MAX_AGE = timedelta(minutes=45)
MAX_NEGATIVE_SCAN_GAP = timedelta(minutes=25)
MIN_DEPARTURE_OBSERVATIONS = 3
MIN_DEPARTURE_SPAN_SECONDS = 30 * 60
MAX_INPUT_BYTES = 1024 * 1024
MAX_PENDING_EVENTS = 16
EVENT_ID_RE = re.compile(r"^presence_[0-9a-f]{64}$")
EXCURSION_ID_RE = re.compile(r"^exc_[0-9a-f]{32}$")
HASH_RE = re.compile(r"^[0-9a-f]{64}$")


class AdapterError(Exception):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def format_timestamp(value: datetime) -> str:
    return (
        value.astimezone(timezone.utc)
        .isoformat(timespec="seconds")
        .replace("+00:00", "Z")
    )


def parse_timestamp(value: Any, code: str = "invalid_timestamp") -> datetime:
    if not isinstance(value, str) or not value or len(value) > 64:
        raise AdapterError(code)
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise AdapterError(code) from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise AdapterError(code)
    return parsed.astimezone(timezone.utc)


def stable_json(value: Any) -> str:
    return json.dumps(value, separators=(",", ":"), sort_keys=True)


def digest(*parts: Any) -> str:
    material = "\x1f".join(stable_json(part) for part in parts)
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def state_digest(state: dict[str, Any]) -> str:
    return hashlib.sha256(stable_json(state).encode("utf-8")).hexdigest()


def protected_directory(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    metadata = path.lstat()
    if (
        path.is_symlink()
        or not path.is_dir()
        or metadata.st_uid != os.getuid()
    ):
        raise AdapterError("unsafe_state_directory")
    if metadata.st_mode & 0o077:
        os.chmod(path, 0o700)


def require_private_parent(path: Path) -> None:
    try:
        metadata = path.parent.lstat()
    except OSError as exc:
        raise AdapterError("unsafe_input_directory") from exc
    if (
        path.parent.is_symlink()
        or not path.parent.is_dir()
        or metadata.st_uid != os.getuid()
        or metadata.st_mode & 0o077
    ):
        raise AdapterError("unsafe_input_directory")


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


def strict_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise AdapterError("duplicate_json_key")
        result[key] = value
    return result


def read_private_json(path: Path, code: str) -> dict[str, Any]:
    require_private_parent(path)
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise AdapterError(code) from exc
    try:
        metadata = os.fstat(descriptor)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_uid != os.getuid()
            or metadata.st_nlink != 1
            or metadata.st_mode & 0o077
            or metadata.st_size <= 0
            or metadata.st_size > MAX_INPUT_BYTES
        ):
            raise AdapterError(code)
        try:
            data = b""
            while len(data) <= MAX_INPUT_BYTES:
                chunk = os.read(
                    descriptor, min(65536, MAX_INPUT_BYTES + 1 - len(data))
                )
                if not chunk:
                    break
                data += chunk
            if not data or len(data) > MAX_INPUT_BYTES:
                raise AdapterError(code)
        except OSError as exc:
            raise AdapterError(code) from exc
    finally:
        os.close(descriptor)
    try:
        value = json.loads(
            data.decode("utf-8"),
            object_pairs_hook=strict_object,
            parse_constant=lambda _value: (_ for _ in ()).throw(
                AdapterError("invalid_json_number")
            ),
        )
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        raise AdapterError(code) from exc
    if not isinstance(value, dict):
        raise AdapterError(code)
    return value


def load_optional_state(path: Path) -> dict[str, Any] | None:
    try:
        path.lstat()
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise AdapterError("invalid_state_file") from exc
    return read_private_json(path, "invalid_state_file")


def env_flag(name: str) -> bool:
    value = os.environ.get(name, "0")
    if value not in {"0", "1"}:
        raise AdapterError("invalid_enable_flag")
    return value == "1"


def initial_person_state(present: bool, observed_at: str) -> dict[str, Any]:
    return {
        "status": "present" if present else "uninitialized",
        "last_positive_at": observed_at if present else None,
        "candidate_first_negative_at": None,
        "candidate_last_negative_at": None,
        "negative_observations": 0,
        "locally_away_at": None,
    }


def reset_uninitialized() -> dict[str, Any]:
    return initial_person_state(False, "")


def present_state(observed_at: str) -> dict[str, Any]:
    return initial_person_state(True, observed_at)


def validate_person_state(value: Any) -> dict[str, Any]:
    expected = {
        "status",
        "last_positive_at",
        "candidate_first_negative_at",
        "candidate_last_negative_at",
        "negative_observations",
        "locally_away_at",
    }
    if not isinstance(value, dict) or set(value) != expected:
        raise AdapterError("invalid_state_file")
    status_value = value.get("status")
    count = value.get("negative_observations")
    if (
        status_value not in PERSON_STATES
        or not isinstance(count, int)
        or isinstance(count, bool)
        or count < 0
        or count > 1_000_000
    ):
        raise AdapterError("invalid_state_file")

    normalized = dict(value)
    for key in (
        "last_positive_at",
        "candidate_first_negative_at",
        "candidate_last_negative_at",
        "locally_away_at",
    ):
        if normalized[key] is not None:
            normalized[key] = format_timestamp(
                parse_timestamp(normalized[key], "invalid_state_file")
            )

    positive = normalized["last_positive_at"]
    first_negative = normalized["candidate_first_negative_at"]
    last_negative = normalized["candidate_last_negative_at"]
    away_at = normalized["locally_away_at"]
    if status_value == "uninitialized":
        if any(item is not None for item in (positive, first_negative, last_negative, away_at)) or count:
            raise AdapterError("invalid_state_file")
    elif status_value == "present":
        if positive is None or any(
            item is not None for item in (first_negative, last_negative, away_at)
        ) or count:
            raise AdapterError("invalid_state_file")
    elif status_value == "departure_candidate":
        if (
            positive is None
            or first_negative is None
            or last_negative is None
            or away_at is not None
            or count < 1
            or parse_timestamp(first_negative) > parse_timestamp(last_negative)
        ):
            raise AdapterError("invalid_state_file")
    elif (
        positive is None
        or away_at is None
        or first_negative is not None
        or last_negative is not None
        or count
    ):
        raise AdapterError("invalid_state_file")
    return normalized


def validate_excursion(value: Any) -> dict[str, Any] | None:
    if value is None:
        return None
    expected = {
        "excursion_id",
        "started_at",
        "people_count",
        "participants",
    }
    if not isinstance(value, dict) or set(value) != expected:
        raise AdapterError("invalid_state_file")
    excursion_id = value.get("excursion_id")
    people_count = value.get("people_count")
    participants = value.get("participants")
    if (
        not isinstance(excursion_id, str)
        or not EXCURSION_ID_RE.fullmatch(excursion_id)
        or not isinstance(people_count, int)
        or isinstance(people_count, bool)
        or not 1 <= people_count <= len(SAFE_PEOPLE)
        or not isinstance(participants, list)
        or len(participants) != people_count
        or participants != sorted(set(participants))
        or any(person not in SAFE_PEOPLE for person in participants)
    ):
        raise AdapterError("invalid_state_file")
    return {
        "excursion_id": excursion_id,
        "started_at": format_timestamp(
            parse_timestamp(value.get("started_at"), "invalid_state_file")
        ),
        "people_count": people_count,
        "participants": list(participants),
    }


def validate_site_state(value: Any) -> dict[str, Any]:
    expected = {
        "last_scan_at",
        "canonical_at",
        "canonical_people",
        "canonical_locations",
        "people",
        "excursion",
    }
    if not isinstance(value, dict) or set(value) != expected:
        raise AdapterError("invalid_state_file")
    last_scan_at = format_timestamp(
        parse_timestamp(value.get("last_scan_at"), "invalid_state_file")
    )
    canonical_at = value.get("canonical_at")
    if canonical_at is not None:
        canonical_at = format_timestamp(
            parse_timestamp(canonical_at, "invalid_state_file")
        )
    canonical_people = value.get("canonical_people")
    if (
        not isinstance(canonical_people, list)
        or canonical_people != sorted(set(canonical_people))
        or any(person not in SAFE_PEOPLE for person in canonical_people)
    ):
        raise AdapterError("invalid_state_file")
    canonical_locations = value.get("canonical_locations")
    if (
        not isinstance(canonical_locations, dict)
        or set(canonical_locations) != SAFE_PEOPLE
        or any(
            location not in {*SITES, "unknown"}
            for location in canonical_locations.values()
        )
    ):
        raise AdapterError("invalid_state_file")
    people = value.get("people")
    if not isinstance(people, dict) or set(people) != SAFE_PEOPLE:
        raise AdapterError("invalid_state_file")
    return {
        "last_scan_at": last_scan_at,
        "canonical_at": canonical_at,
        "canonical_people": list(canonical_people),
        "canonical_locations": {
            person: canonical_locations[person] for person in sorted(SAFE_PEOPLE)
        },
        "people": {
            person: validate_person_state(people[person])
            for person in sorted(SAFE_PEOPLE)
        },
        "excursion": validate_excursion(value.get("excursion")),
    }


def validate_state(value: dict[str, Any] | None) -> dict[str, Any]:
    if value is None:
        return {"version": VERSION, "sites": {}}
    if (
        set(value) != {"version", "sites"}
        or value.get("version") != VERSION
        or not isinstance(value.get("sites"), dict)
        or any(site not in SITES for site in value["sites"])
    ):
        raise AdapterError("state_version")
    return {
        "version": VERSION,
        "sites": {
            site: validate_site_state(site_state)
            for site, site_state in sorted(value["sites"].items())
        },
    }


def sanitize_scan(path: Path, site: str, now: datetime) -> dict[str, Any]:
    raw = read_private_json(path, "invalid_scan")
    if "error" in raw or raw.get("location") != site:
        raise AdapterError("invalid_scan")
    observed = parse_timestamp(raw.get("timestamp"), "invalid_scan")
    age = now - observed
    if age < -FUTURE_TOLERANCE or age > SCAN_MAX_AGE:
        raise AdapterError("invalid_scan_time")
    presence = raw.get("presence")
    if not isinstance(presence, dict):
        raise AdapterError("invalid_scan")
    people: dict[str, bool] = {}
    for source_name, alias in TRACKED_PEOPLE.items():
        entry = presence.get(source_name)
        if (
            not isinstance(entry, dict)
            or type(entry.get("present")) is not bool
        ):
            raise AdapterError("invalid_scan")
        people[alias] = entry["present"]
    return {
        "site": site,
        "observed_at": format_timestamp(observed),
        "people": people,
    }


def sanitize_canonical(path: Path, now: datetime) -> dict[str, Any]:
    raw = read_private_json(path, "invalid_canonical_state")
    if "error" in raw:
        raise AdapterError("invalid_canonical_state")
    observed = parse_timestamp(raw.get("timestamp"), "invalid_canonical_state")
    age = now - observed
    if age < -FUTURE_TOLERANCE or age > SCAN_MAX_AGE:
        raise AdapterError("invalid_canonical_time")
    raw_people = raw.get("people")
    if not isinstance(raw_people, dict):
        raise AdapterError("invalid_canonical_state")
    locations: dict[str, str] = {}
    for source_name, alias in TRACKED_PEOPLE.items():
        entry = raw_people.get(source_name)
        location = entry.get("location") if isinstance(entry, dict) else None
        if location not in {*SITES, "unknown"}:
            raise AdapterError("invalid_canonical_state")
        locations[alias] = location
    return {
        "observed_at": format_timestamp(observed),
        "locations": locations,
    }


def event_draft(
    *,
    event_type: str,
    site: str,
    entity_kind: str,
    entity_alias: str,
    observed_at: str,
    adapter_time: str,
    attributes: dict[str, Any],
) -> dict[str, Any]:
    normalized_observed_at = format_timestamp(
        max(parse_timestamp(adapter_time), parse_timestamp(observed_at))
    )
    return {
        "schema_version": 1,
        "event_type": event_type,
        "site": site,
        "entity_kind": entity_kind,
        "entity_alias": entity_alias,
        "occurred_at": observed_at,
        "observed_at": normalized_observed_at,
        "time_precision": "observed_interval",
        "attributes": attributes,
    }


def person_event(
    *,
    event_type: str,
    site: str,
    person: str,
    evidence_at: str,
    adapter_time: str,
    not_before: str,
    distinct_observations: int,
    observation_span_seconds: int,
) -> dict[str, Any]:
    confidence = (
        "network_inference"
        if event_type == "presence.local_departure_inferred"
        else "positive_detection"
    )
    return event_draft(
        event_type=event_type,
        site=site,
        entity_kind="person",
        entity_alias=person,
        observed_at=evidence_at,
        adapter_time=adapter_time,
        attributes={
            "person_alias": person,
            "confidence": confidence,
            "evidence_at": evidence_at,
            "not_before": not_before,
            "not_after": evidence_at,
            "distinct_observations": distinct_observations,
            "observation_span_seconds": observation_span_seconds,
        },
    )


def household_event(
    *,
    event_type: str,
    site: str,
    evidence_at: str,
    adapter_time: str,
    not_before: str,
    people_count: int,
    excursion_id: str,
    outcome: str | None = None,
) -> dict[str, Any]:
    if outcome == "residence_relocated":
        confidence = "canonical"
    elif outcome == "resident_returned":
        confidence = "positive_detection"
    else:
        confidence = "network_inference"
    attributes: dict[str, Any] = {
        "confidence": confidence,
        "evidence_at": evidence_at,
        "not_before": not_before,
        "not_after": evidence_at,
        "people_count": people_count,
        "excursion_id": excursion_id,
    }
    if outcome is not None:
        attributes["outcome"] = outcome
    return event_draft(
        event_type=event_type,
        site=site,
        entity_kind="site",
        entity_alias=site,
        observed_at=evidence_at,
        adapter_time=adapter_time,
        attributes=attributes,
    )


def validate_event(value: Any) -> dict[str, Any]:
    envelope = {
        "schema_version",
        "source_event_id",
        "event_type",
        "site",
        "entity_kind",
        "entity_alias",
        "occurred_at",
        "observed_at",
        "time_precision",
        "attributes",
    }
    if (
        not isinstance(value, dict)
        or set(value) != envelope
        or value.get("schema_version") != 1
        or not isinstance(value.get("source_event_id"), str)
        or not EVENT_ID_RE.fullmatch(value["source_event_id"])
        or value.get("site") not in SITES
        or value.get("time_precision") != "observed_interval"
        or not isinstance(value.get("attributes"), dict)
    ):
        raise AdapterError("invalid_pending_state")
    occurred = format_timestamp(
        parse_timestamp(value.get("occurred_at"), "invalid_pending_state")
    )
    observed = format_timestamp(
        parse_timestamp(value.get("observed_at"), "invalid_pending_state")
    )
    attributes = value["attributes"]
    common = {
        "confidence",
        "evidence_at",
        "not_before",
        "not_after",
        "state_hash",
    }
    event_type = value.get("event_type")
    if event_type in {
        "presence.local_departure_inferred",
        "presence.local_arrival_observed",
    }:
        if (
            set(attributes)
            != common
            | {
                "person_alias",
                "distinct_observations",
                "observation_span_seconds",
            }
            or value.get("entity_kind") != "person"
            or value.get("entity_alias") not in SAFE_PEOPLE
            or attributes.get("person_alias") != value.get("entity_alias")
        ):
            raise AdapterError("invalid_pending_state")
        if event_type.endswith("departure_inferred"):
            valid_measurement = (
                attributes.get("confidence") == "network_inference"
                and isinstance(attributes.get("distinct_observations"), int)
                and not isinstance(attributes.get("distinct_observations"), bool)
                and attributes["distinct_observations"]
                >= MIN_DEPARTURE_OBSERVATIONS
                and isinstance(attributes.get("observation_span_seconds"), int)
                and not isinstance(attributes.get("observation_span_seconds"), bool)
                and attributes["observation_span_seconds"]
                >= MIN_DEPARTURE_SPAN_SECONDS
            )
        else:
            valid_measurement = (
                attributes.get("confidence") == "positive_detection"
                and attributes.get("distinct_observations") == 1
                and attributes.get("observation_span_seconds") == 0
            )
        if not valid_measurement:
            raise AdapterError("invalid_pending_state")
    elif event_type in {
        "presence.household_excursion_started",
        "presence.household_excursion_ended",
    }:
        required = common | {"people_count", "excursion_id"}
        if event_type.endswith("_ended"):
            required |= {"outcome"}
        if (
            set(attributes) != required
            or value.get("entity_kind") != "site"
            or value.get("entity_alias") != value.get("site")
            or not isinstance(attributes.get("people_count"), int)
            or isinstance(attributes.get("people_count"), bool)
            or not 1 <= attributes["people_count"] <= len(SAFE_PEOPLE)
            or not isinstance(attributes.get("excursion_id"), str)
            or not EXCURSION_ID_RE.fullmatch(attributes["excursion_id"])
        ):
            raise AdapterError("invalid_pending_state")
        if event_type.endswith("_started"):
            if attributes.get("confidence") != "network_inference":
                raise AdapterError("invalid_pending_state")
        elif (
            attributes.get("outcome") == "resident_returned"
            and attributes.get("confidence") != "positive_detection"
        ) or (
            attributes.get("outcome") == "residence_relocated"
            and attributes.get("confidence") != "canonical"
        ) or attributes.get("outcome") not in {
            "resident_returned",
            "residence_relocated",
        }:
            raise AdapterError("invalid_pending_state")
    else:
        raise AdapterError("invalid_pending_state")

    evidence = format_timestamp(
        parse_timestamp(attributes.get("evidence_at"), "invalid_pending_state")
    )
    not_before = parse_timestamp(
        attributes.get("not_before"), "invalid_pending_state"
    )
    not_after = format_timestamp(
        parse_timestamp(attributes.get("not_after"), "invalid_pending_state")
    )
    if (
        occurred != evidence
        or occurred != not_after
        or not_before > parse_timestamp(not_after)
        or parse_timestamp(observed) < parse_timestamp(occurred)
        or not isinstance(attributes.get("state_hash"), str)
        or not HASH_RE.fullmatch(attributes["state_hash"])
    ):
        raise AdapterError("invalid_pending_state")
    return value


def finalize_events(
    drafts: list[dict[str, Any]], state_after: dict[str, Any]
) -> list[dict[str, Any]]:
    state_hash = state_digest(state_after)
    events: list[dict[str, Any]] = []
    for draft in drafts:
        event = copy.deepcopy(draft)
        event["attributes"]["state_hash"] = state_hash
        identity = {
            "event_type": event["event_type"],
            "site": event["site"],
            "entity_kind": event["entity_kind"],
            "entity_alias": event["entity_alias"],
            "occurred_at": event["occurred_at"],
            "attributes": event["attributes"],
        }
        event["source_event_id"] = "presence_" + digest(
            "presence-local-event-v1", identity
        )
        events.append(validate_event(event))
    return events


def apply_canonical(
    site: str,
    site_state: dict[str, Any],
    canonical: dict[str, Any] | None,
    adapter_time: str,
    drafts: list[dict[str, Any]],
) -> tuple[bool, set[str], bool]:
    if canonical is None:
        return False, set(), False
    canonical_time = canonical["observed_at"]
    prior_time = site_state.get("canonical_at")
    if prior_time is not None and parse_timestamp(canonical_time) <= parse_timestamp(
        prior_time
    ):
        return False, set(), False
    locations = dict(canonical["locations"])
    roster = sorted(
        person
        for person, location in locations.items()
        if location == site
    )
    prior_roster = list(site_state.get("canonical_people", []))
    prior_locations = dict(site_state.get("canonical_locations", {}))
    baseline = prior_time is None
    changed_people = (
        {
            person
            for person in SAFE_PEOPLE
            if prior_locations.get(person) != locations[person]
        }
        if not baseline
        else set()
    )
    roster_changed = not baseline and roster != prior_roster

    site_state["canonical_at"] = canonical_time
    site_state["canonical_people"] = roster
    site_state["canonical_locations"] = locations
    if changed_people:
        for person in changed_people:
            if site_state["people"][person]["status"] == "departure_candidate":
                site_state["people"][person] = reset_uninitialized()
        excursion = site_state.get("excursion")
        other_site = "crosstown" if site == "cabin" else "cabin"
        relocated_participant = (
            excursion is not None
            and any(
                person in excursion["participants"]
                and locations[person] == other_site
                and prior_locations.get(person) != other_site
                for person in changed_people
            )
        )
        if relocated_participant:
            drafts.append(
                household_event(
                    event_type="presence.household_excursion_ended",
                    site=site,
                    evidence_at=canonical_time,
                    adapter_time=adapter_time,
                    not_before=excursion["started_at"],
                    people_count=excursion["people_count"],
                    excursion_id=excursion["excursion_id"],
                    outcome="residence_relocated",
                )
            )
            site_state["excursion"] = None
    return True, changed_people, roster_changed


def advance_person(
    *,
    site: str,
    person: str,
    value: bool,
    observed_at: str,
    adapter_time: str,
    current: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any] | None]:
    status_value = current["status"]
    if value:
        if status_value == "locally_away":
            event = person_event(
                event_type="presence.local_arrival_observed",
                site=site,
                person=person,
                evidence_at=observed_at,
                adapter_time=adapter_time,
                not_before=current["locally_away_at"],
                distinct_observations=1,
                observation_span_seconds=0,
            )
            return present_state(observed_at), event
        return present_state(observed_at), None

    if status_value in {"uninitialized", "locally_away"}:
        return current, None
    if status_value == "present":
        last_positive = parse_timestamp(current["last_positive_at"])
        first_negative = parse_timestamp(observed_at)
        if (
            first_negative <= last_positive
            or first_negative - last_positive > RECENT_POSITIVE_MAX_AGE
        ):
            return reset_uninitialized(), None
        return {
            **current,
            "status": "departure_candidate",
            "candidate_first_negative_at": observed_at,
            "candidate_last_negative_at": observed_at,
            "negative_observations": 1,
        }, None

    first_negative = parse_timestamp(current["candidate_first_negative_at"])
    last_negative = parse_timestamp(current["candidate_last_negative_at"])
    next_negative = parse_timestamp(observed_at)
    if next_negative <= last_negative:
        return current, None
    if next_negative - last_negative > MAX_NEGATIVE_SCAN_GAP:
        last_positive = parse_timestamp(current["last_positive_at"])
        if (
            next_negative > last_positive
            and next_negative - last_positive <= RECENT_POSITIVE_MAX_AGE
        ):
            return {
                **current,
                "candidate_first_negative_at": observed_at,
                "candidate_last_negative_at": observed_at,
                "negative_observations": 1,
            }, None
        return reset_uninitialized(), None
    count = current["negative_observations"] + 1
    span = int((next_negative - first_negative).total_seconds())
    if count >= MIN_DEPARTURE_OBSERVATIONS and span >= MIN_DEPARTURE_SPAN_SECONDS:
        event = person_event(
            event_type="presence.local_departure_inferred",
            site=site,
            person=person,
            evidence_at=observed_at,
            adapter_time=adapter_time,
            not_before=current["last_positive_at"],
            distinct_observations=count,
            observation_span_seconds=span,
        )
        return {
            "status": "locally_away",
            "last_positive_at": current["last_positive_at"],
            "candidate_first_negative_at": None,
            "candidate_last_negative_at": None,
            "negative_observations": 0,
            "locally_away_at": observed_at,
        }, event
    return {
        **current,
        "candidate_last_negative_at": observed_at,
        "negative_observations": count,
    }, None


def initialize_site(
    scan: dict[str, Any], canonical: dict[str, Any] | None
) -> dict[str, Any]:
    canonical_time = canonical["observed_at"] if canonical is not None else None
    roster = (
        sorted(
            person
            for person, location in canonical["locations"].items()
            if location == scan["site"]
        )
        if canonical is not None
        else []
    )
    return {
        "last_scan_at": scan["observed_at"],
        "canonical_at": canonical_time,
        "canonical_people": roster,
        "canonical_locations": (
            dict(canonical["locations"])
            if canonical is not None
            else {person: "unknown" for person in sorted(SAFE_PEOPLE)}
        ),
        "people": {
            person: initial_person_state(present, scan["observed_at"])
            for person, present in sorted(scan["people"].items())
        },
        "excursion": None,
    }


def advance_site(
    *,
    site: str,
    site_state: dict[str, Any],
    scan: dict[str, Any] | None,
    canonical: dict[str, Any] | None,
    adapter_time: str,
    drafts: list[dict[str, Any]],
) -> tuple[bool, bool]:
    changed, relocated_people, roster_changed = apply_canonical(
        site, site_state, canonical, adapter_time, drafts
    )
    if scan is None or parse_timestamp(scan["observed_at"]) <= parse_timestamp(
        site_state["last_scan_at"]
    ):
        return changed, False

    local_events: list[dict[str, Any]] = []
    scan_time = parse_timestamp(scan["observed_at"])
    canonical_time = (
        parse_timestamp(site_state["canonical_at"])
        if site_state.get("canonical_at") is not None
        else None
    )
    for person in sorted(SAFE_PEOPLE):
        if (
            person in relocated_people
            and canonical_time is not None
            and scan_time <= canonical_time
        ):
            continue
        next_state, event = advance_person(
            site=site,
            person=person,
            value=scan["people"][person],
            observed_at=scan["observed_at"],
            adapter_time=adapter_time,
            current=site_state["people"][person],
        )
        site_state["people"][person] = next_state
        if event is not None:
            local_events.append(event)
    site_state["last_scan_at"] = scan["observed_at"]
    changed = True

    excursion = site_state.get("excursion")
    arrivals = [
        event
        for event in local_events
        if event["event_type"] == "presence.local_arrival_observed"
    ]
    if excursion is not None and any(
        event["entity_alias"] in excursion["participants"] for event in arrivals
    ):
        return_event = sorted(
            arrivals, key=lambda event: (event["occurred_at"], event["entity_alias"])
        )[0]
        drafts.extend(local_events)
        drafts.append(
            household_event(
                event_type="presence.household_excursion_ended",
                site=site,
                evidence_at=return_event["occurred_at"],
                adapter_time=adapter_time,
                not_before=excursion["started_at"],
                people_count=excursion["people_count"],
                excursion_id=excursion["excursion_id"],
                outcome="resident_returned",
            )
        )
        site_state["excursion"] = None
        return changed, True

    drafts.extend(local_events)
    departures = [
        event
        for event in local_events
        if event["event_type"] == "presence.local_departure_inferred"
    ]
    roster = site_state.get("canonical_people", [])
    if (
        site_state.get("excursion") is None
        and not roster_changed
        and roster
        and departures
        and all(
            site_state["people"][person]["status"] == "locally_away"
            for person in roster
        )
    ):
        final_departure = sorted(
            departures,
            key=lambda event: (
                event["occurred_at"],
                event["attributes"]["not_before"],
                event["entity_alias"],
            ),
        )[-1]
        excursion_id = "exc_" + digest(
            "presence-excursion-v1",
            site,
            final_departure["occurred_at"],
            roster,
        )[:32]
        site_state["excursion"] = {
            "excursion_id": excursion_id,
            "started_at": final_departure["occurred_at"],
            "people_count": len(roster),
            "participants": list(roster),
        }
        drafts.append(
            household_event(
                event_type="presence.household_excursion_started",
                site=site,
                evidence_at=final_departure["occurred_at"],
                adapter_time=adapter_time,
                not_before=final_departure["attributes"]["not_before"],
                people_count=len(roster),
                excursion_id=excursion_id,
            )
        )
    return changed, bool(local_events)


def publish(home_eventctl: str, event: dict[str, Any]) -> None:
    try:
        result = subprocess.run(
            [home_eventctl, "enqueue", "--source", "presence"],
            input=stable_json(event),
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


def validate_pending(value: dict[str, Any]) -> dict[str, Any]:
    if (
        set(value) != {"version", "events", "state_after", "affected_sites"}
        or value.get("version") != VERSION
        or not isinstance(value.get("events"), list)
        or not 1 <= len(value["events"]) <= MAX_PENDING_EVENTS
        or not isinstance(value.get("affected_sites"), list)
        or value["affected_sites"] != sorted(set(value["affected_sites"]))
        or any(site not in SITES for site in value["affected_sites"])
    ):
        raise AdapterError("invalid_pending_state")
    state_after = validate_state(value.get("state_after"))
    events = [validate_event(event) for event in value["events"]]
    event_sites = sorted({event["site"] for event in events})
    if value["affected_sites"] != event_sites:
        raise AdapterError("invalid_pending_state")
    expected_hash = state_digest(state_after)
    if any(event["attributes"]["state_hash"] != expected_hash for event in events):
        raise AdapterError("invalid_pending_state")
    return {
        "version": VERSION,
        "events": events,
        "state_after": state_after,
        "affected_sites": list(value["affected_sites"]),
    }


def publish_pending(
    pending_path: Path,
    state_path: Path,
    home_eventctl: str,
) -> dict[str, Any] | None:
    raw = load_optional_state(pending_path)
    if raw is None:
        return None
    pending = validate_pending(raw)
    for event in pending["events"]:
        publish(home_eventctl, event)
    atomic_json(state_path, pending["state_after"])
    pending_path.unlink()
    fsync_directory(pending_path.parent)
    return pending["state_after"]


def acquire_lock(path: Path) -> int:
    try:
        descriptor = os.open(
            path,
            os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0),
            0o600,
        )
    except OSError as exc:
        raise AdapterError("unsafe_lock_file") from exc
    metadata = os.fstat(descriptor)
    if (
        not stat.S_ISREG(metadata.st_mode)
        or metadata.st_uid != os.getuid()
        or metadata.st_mode & 0o077
    ):
        os.close(descriptor)
        raise AdapterError("unsafe_lock_file")
    os.fchmod(descriptor, 0o600)
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        os.close(descriptor)
        return -1
    return descriptor


def run_once() -> dict[str, Any]:
    flags = {
        "cabin": env_flag("HOME_EVENTS_LOCAL_PRESENCE_CABIN_ENABLED"),
        "crosstown": env_flag("HOME_EVENTS_LOCAL_PRESENCE_CROSSTOWN_ENABLED"),
    }
    enabled_sites = {site for site, enabled in flags.items() if enabled}
    if not enabled_sites:
        return {"ok": True, "mode": "disabled"}

    home = Path.home()
    root = Path(
        os.environ.get(
            "HOME_EVENTS_ROOT", str(home / ".openclaw/home-events")
        )
    ).expanduser()
    state_dir = root / "state"
    protected_directory(root)
    protected_directory(state_dir)
    state_path = state_dir / "presence-local-adapter.json"
    pending_path = state_dir / "presence-local-adapter.pending.json"
    lock_path = state_dir / "presence-local-adapter.lock"
    scan_paths = {
        "cabin": Path(
            os.environ.get(
                "HOME_EVENTS_CABIN_SCAN",
                str(home / ".openclaw/presence/cabin-scan.json"),
            )
        ).expanduser(),
        "crosstown": Path(
            os.environ.get(
                "HOME_EVENTS_CROSSTOWN_SCAN",
                str(home / ".openclaw/presence/crosstown-scan.json"),
            )
        ).expanduser(),
    }
    canonical_path = Path(
        os.environ.get(
            "HOME_EVENTS_PRESENCE_STATE",
            str(home / ".openclaw/presence/state.json"),
        )
    ).expanduser()
    home_eventctl = os.environ.get(
        "HOME_EVENTCTL", str(home / ".openclaw/bin/home-eventctl")
    )

    descriptor = acquire_lock(lock_path)
    if descriptor < 0:
        return {"ok": True, "mode": "already_running"}
    try:
        state = publish_pending(
            pending_path, state_path, home_eventctl
        )
        if state is None:
            state = validate_state(load_optional_state(state_path))
        before = copy.deepcopy(state)
        now = utc_now()
        adapter_time = format_timestamp(now)
        try:
            canonical = sanitize_canonical(canonical_path, now)
        except AdapterError:
            return {
                "ok": False,
                "mode": "failure",
                "error_code": "canonical_state_unavailable",
            }

        valid_scans: dict[str, dict[str, Any]] = {}
        invalid_sites = 0
        for site in sorted(enabled_sites):
            try:
                valid_scans[site] = sanitize_scan(scan_paths[site], site, now)
            except AdapterError:
                invalid_sites += 1

        if not valid_scans:
            return {
                "ok": False,
                "mode": "failure",
                "error_code": "observations_unavailable",
            }

        drafts: list[dict[str, Any]] = []
        changed_sites: set[str] = set()
        initialized_sites = 0
        for site in sorted(enabled_sites):
            scan = valid_scans.get(site)
            existing = state["sites"].get(site)
            if existing is None:
                if scan is None:
                    continue
                state["sites"][site] = initialize_site(scan, canonical)
                changed_sites.add(site)
                initialized_sites += 1
                continue
            changed, _had_local_event = advance_site(
                site=site,
                site_state=existing,
                scan=scan,
                canonical=canonical,
                adapter_time=adapter_time,
                drafts=drafts,
            )
            if changed:
                changed_sites.add(site)

        if state == before:
            if invalid_sites == len(enabled_sites):
                return {
                    "ok": False,
                    "mode": "failure",
                    "error_code": "observations_unavailable",
                }
            return {
                "ok": True,
                "mode": "no_new_observation",
                "event_count": 0,
                "skipped_sites": invalid_sites,
            }

        events = finalize_events(drafts, state)
        if events:
            pending = {
                "version": VERSION,
                "events": events,
                "state_after": state,
                "affected_sites": sorted({event["site"] for event in events}),
            }
            atomic_json(pending_path, pending)
            publish_pending(
                pending_path, state_path, home_eventctl
            )
        else:
            atomic_json(state_path, state)
        return {
            "ok": True,
            "mode": "baseline" if initialized_sites else "observed",
            "event_count": len(events),
            "skipped_sites": invalid_sites,
        }
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
