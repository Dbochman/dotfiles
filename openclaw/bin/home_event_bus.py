#!/usr/bin/env python3
"""Durable, privacy-safe local home-event journal and query interface.

The operator CLI accepts a single normalized event on stdin, replaces its raw
source identifier with keyed opaque identifiers, and durably commits a
protected spool file.  A separate single-writer ingest command drains those
files into SQLite.  The agent CLI is deliberately read-only.
"""

from __future__ import annotations

import argparse
import contextlib
import dataclasses
import datetime as dt
import fcntl
import hashlib
import hmac
import json
import os
from pathlib import Path
import re
import secrets
import sqlite3
import stat
import sys
from typing import Any, Callable, Dict, Iterable, Mapping, Optional, Sequence, Tuple


SCHEMA_VERSION = 2
STATUS_SCHEMA_VERSION = 1
EVENT_SCHEMA_VERSION = 1
SERVICE_NAME = "home-events"
DEFAULT_ROOT = Path("~/.openclaw/home-events").expanduser()
SOURCES = ("ring", "presence", "august", "nest")
SITES = ("cabin", "crosstown")
MAX_STDIN_BYTES = 64 * 1024
MAX_SPOOL_BYTES = 32 * 1024
MAX_ATTRIBUTES_BYTES = 2 * 1024
MAX_QUERY_LIMIT = 100
ACCEPTED_RETENTION_DAYS = 30
DEAD_LETTER_RETENTION_DAYS = 90
RING_LIVE_MAX_AGE = dt.timedelta(seconds=60)
RING_BACKFILL_MAX_AGE = dt.timedelta(minutes=15)

SAFE_NAME_RE = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
EVENT_UID_RE = re.compile(r"^evt_[0-9a-f]{32}$")
INCIDENT_UID_RE = re.compile(r"^inc_[0-9a-f]{32}$")
DEDUPE_KEY_RE = re.compile(r"^ded_[0-9a-f]{64}$")
RECORD_MAC_RE = re.compile(r"^mac_[0-9a-f]{64}$")
READY_NAME_RE = re.compile(r"^evt_[0-9a-f]{32}\.[0-9a-f]{32}\.ready$")
CONTROL_RE = re.compile(r"[\x00-\x1f\x7f]")
REASON_CODE_RE = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
DURATION_RE = re.compile(r"^([1-9][0-9]{0,5})([mhd])$")

EVENT_RULES: Mapping[str, Mapping[str, Tuple[str, ...]]] = {
    "ring": {
        "entry.doorbell_rang": ("backfill",),
        "entry.person_detected": ("classification", "backfill"),
        "entry.motion_detected": ("classification", "backfill"),
    },
    "presence": {
        "presence.occupancy_changed": (
            "previous",
            "current",
            "confidence",
            "evidence_at",
            "state_hash",
        ),
        "presence.person_relocated": (
            "person_alias",
            "from_site",
            "to_site",
            "confidence",
            "evidence_at",
            "state_hash",
        ),
    },
    "august": {
        "lock.locked": ("previous", "current", "not_before", "not_after"),
        "lock.unlocked": ("previous", "current", "not_before", "not_after"),
        "door.opened": ("previous", "current", "not_before", "not_after"),
        "door.closed": ("previous", "current", "not_before", "not_after"),
        "device.battery_low": (
            "battery_percent",
            "threshold",
            "not_before",
            "not_after",
        ),
        "device.battery_recovered": (
            "battery_percent",
            "threshold",
            "not_before",
            "not_after",
        ),
        "source.unavailable": ("failure_count", "reason_code"),
        "source.recovered": ("outage_seconds",),
    },
    "nest": {
        "camera.person_detected": ("classification",),
        "camera.motion_detected": ("classification",),
    },
}

EVENT_REQUIRED_ATTRIBUTES: Mapping[str, Mapping[str, frozenset[str]]] = {
    "ring": {
        "entry.doorbell_rang": frozenset(),
        "entry.person_detected": frozenset({"classification"}),
        "entry.motion_detected": frozenset({"classification"}),
    },
    "presence": {
        event_type: frozenset(attributes)
        for event_type, attributes in EVENT_RULES["presence"].items()
    },
    "august": {
        event_type: frozenset(attributes)
        for event_type, attributes in EVENT_RULES["august"].items()
    },
    "nest": {
        event_type: frozenset(attributes)
        for event_type, attributes in EVENT_RULES["nest"].items()
    },
}

EVENT_ENTITY_KIND: Mapping[str, Mapping[str, str]] = {
    "ring": {
        "entry.doorbell_rang": "doorbell",
        "entry.person_detected": "doorbell",
        "entry.motion_detected": "doorbell",
    },
    "presence": {
        "presence.occupancy_changed": "site",
        "presence.person_relocated": "person",
    },
    "august": {
        "lock.locked": "lock",
        "lock.unlocked": "lock",
        "door.opened": "door",
        "door.closed": "door",
        "device.battery_low": "battery",
        "device.battery_recovered": "battery",
        "source.unavailable": "adapter",
        "source.recovered": "adapter",
    },
    "nest": {
        "camera.person_detected": "camera",
        "camera.motion_detected": "camera",
    },
}

SAFE_DEVICE_ALIASES: Mapping[str, frozenset[str]] = {
    "ring": frozenset({"front_door"}),
    "august": frozenset({"front_door"}),
    "nest": frozenset({"kitchen", "living_room", "living_room_wired"}),
}
SAFE_PERSON_ALIASES = frozenset({"dylan", "julia"})

ENTITY_KINDS: Mapping[str, Tuple[str, ...]] = {
    "ring": ("doorbell",),
    "presence": ("site", "person"),
    "august": ("lock", "door", "battery", "adapter"),
    "nest": ("camera",),
}

TIME_PRECISIONS: Mapping[str, Tuple[str, ...]] = {
    "ring": ("source", "backfill"),
    "presence": ("evaluation",),
    "august": ("observed_interval",),
    "nest": ("source",),
}

INPUT_REQUIRED_FIELDS = frozenset(
    {
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
)
INPUT_ALLOWED_FIELDS = INPUT_REQUIRED_FIELDS | {"schema_version"}

SPOOL_FIELDS = frozenset(
    {
        "schema_version",
        "event_uid",
        "dedupe_key",
        "record_mac",
        "source",
        "event_type",
        "site",
        "entity_kind",
        "entity_alias",
        "occurred_at",
        "observed_at",
        "time_precision",
        "attributes",
    }
)


class HomeEventError(Exception):
    """Expected fail-closed error with a safe public code."""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


class ConfigError(HomeEventError):
    pass


class PayloadError(HomeEventError):
    pass


class StateError(HomeEventError):
    pass


@dataclasses.dataclass(frozen=True)
class RuntimePaths:
    root: Path

    @property
    def config(self) -> Path:
        return self.root / "config"

    @property
    def secret(self) -> Path:
        return self.config / "dedupe.key"

    @property
    def spool(self) -> Path:
        return self.root / "spool"

    def source_spool(self, source: str) -> Path:
        return self.spool / source

    @property
    def state(self) -> Path:
        return self.root / "state"

    @property
    def database(self) -> Path:
        return self.state / "events.sqlite3"

    @property
    def status(self) -> Path:
        return self.state / "status.json"

    @property
    def ingest_lock(self) -> Path:
        return self.state / "ingest.lock"

    @property
    def ring_producer_status(self) -> Path:
        return self.state / "ring-producer.json"


@dataclasses.dataclass(frozen=True)
class NormalizedEvent:
    event_uid: str
    dedupe_key: str
    source: str
    event_type: str
    site: str
    entity_kind: str
    entity_alias: str
    occurred_at: str
    observed_at: str
    time_precision: str
    attributes: Mapping[str, Any]

    def as_spool_record(self) -> Dict[str, Any]:
        return {
            "schema_version": EVENT_SCHEMA_VERSION,
            "event_uid": self.event_uid,
            "dedupe_key": self.dedupe_key,
            "source": self.source,
            "event_type": self.event_type,
            "site": self.site,
            "entity_kind": self.entity_kind,
            "entity_alias": self.entity_alias,
            "occurred_at": self.occurred_at,
            "observed_at": self.observed_at,
            "time_precision": self.time_precision,
            "attributes": dict(self.attributes),
        }


@dataclasses.dataclass(frozen=True)
class IngestResult:
    scanned: int = 0
    accepted: int = 0
    duplicate: int = 0
    dead_letter: int = 0
    cleanup_pending: int = 0

    def incremented(self, outcome: str, cleanup_pending: bool = False) -> "IngestResult":
        values = dataclasses.asdict(self)
        values["scanned"] += 1
        values[outcome] += 1
        if cleanup_pending:
            values["cleanup_pending"] += 1
        return IngestResult(**values)


def utc_now() -> str:
    return _format_timestamp(dt.datetime.now(dt.timezone.utc))


def _format_timestamp(value: dt.datetime) -> str:
    normalized = value.astimezone(dt.timezone.utc)
    if normalized.microsecond:
        return normalized.isoformat(timespec="microseconds").replace("+00:00", "Z")
    return normalized.isoformat(timespec="seconds").replace("+00:00", "Z")


def _parse_timestamp(value: Any, code: str) -> Tuple[str, dt.datetime]:
    if not isinstance(value, str) or not value or len(value) > 40 or CONTROL_RE.search(value):
        raise PayloadError(code)
    try:
        parsed = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise PayloadError(code) from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise PayloadError(code)
    normalized = parsed.astimezone(dt.timezone.utc)
    return _format_timestamp(normalized), normalized


def _parse_now(value: str) -> dt.datetime:
    try:
        parsed = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (TypeError, ValueError) as exc:
        raise StateError("invalid_clock") from exc
    if parsed.tzinfo is None:
        raise StateError("invalid_clock")
    return parsed.astimezone(dt.timezone.utc)


def _cutoff(now: str, days: int) -> str:
    return _format_timestamp(_parse_now(now) - dt.timedelta(days=days))


def _strict_object(pairs: Iterable[Tuple[str, Any]]) -> Dict[str, Any]:
    result: Dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise PayloadError("duplicate_json_key")
        result[key] = value
    return result


def _reject_constant(_value: str) -> None:
    raise PayloadError("invalid_json_number")


def _decode_json(data: bytes, *, max_bytes: int, code: str) -> Any:
    if not data or len(data) > max_bytes:
        raise PayloadError(code)
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise PayloadError("invalid_json_encoding") from exc
    try:
        return json.loads(
            text,
            object_pairs_hook=_strict_object,
            parse_constant=_reject_constant,
        )
    except PayloadError:
        raise
    except (json.JSONDecodeError, TypeError, ValueError) as exc:
        raise PayloadError("invalid_json") from exc


def _require_exact_fields(value: Any, fields: frozenset, code: str) -> Mapping[str, Any]:
    if not isinstance(value, dict) or frozenset(value) != fields:
        raise PayloadError(code)
    return value


def _validate_safe_name(value: Any, code: str) -> str:
    if not isinstance(value, str) or not SAFE_NAME_RE.fullmatch(value):
        raise PayloadError(code)
    return value


def _validate_attributes(source: str, event_type: str, value: Any) -> Dict[str, Any]:
    if not isinstance(value, dict):
        raise PayloadError("invalid_attributes")
    allowed = frozenset(EVENT_RULES[source][event_type])
    if not frozenset(value).issubset(allowed):
        raise PayloadError("unknown_attribute")
    if not EVENT_REQUIRED_ATTRIBUTES[source][event_type].issubset(value):
        raise PayloadError("missing_attribute")
    result: Dict[str, Any] = {}
    for key, item in value.items():
        if isinstance(item, bool) or item is None:
            result[key] = item
        elif isinstance(item, int) and not isinstance(item, bool):
            if item < 0 or item > 10_000_000:
                raise PayloadError("invalid_attribute_value")
            result[key] = item
        elif isinstance(item, str):
            if len(item) > 128 or CONTROL_RE.search(item):
                raise PayloadError("invalid_attribute_value")
            result[key] = item
        else:
            raise PayloadError("invalid_attribute_value")

    if "classification" in result and result["classification"] not in ("person", "motion"):
        raise PayloadError("invalid_classification")
    if "backfill" in result and not isinstance(result["backfill"], bool):
        raise PayloadError("invalid_backfill")
    if "battery_percent" in result and (
        not isinstance(result["battery_percent"], int)
        or isinstance(result["battery_percent"], bool)
        or not 0 <= result["battery_percent"] <= 100
    ):
        raise PayloadError("invalid_battery_percent")
    if "threshold" in result and (
        not isinstance(result["threshold"], int)
        or isinstance(result["threshold"], bool)
        or not 0 <= result["threshold"] <= 100
    ):
        raise PayloadError("invalid_threshold")
    if "failure_count" in result and (
        not isinstance(result["failure_count"], int)
        or isinstance(result["failure_count"], bool)
        or result["failure_count"] < 1
    ):
        raise PayloadError("invalid_failure_count")
    if "outage_seconds" in result and (
        not isinstance(result["outage_seconds"], int)
        or isinstance(result["outage_seconds"], bool)
    ):
        raise PayloadError("invalid_outage_seconds")
    if "reason_code" in result:
        if not isinstance(result["reason_code"], str) or not REASON_CODE_RE.fullmatch(
            result["reason_code"]
        ):
            raise PayloadError("invalid_reason_code")
    if "person_alias" in result and (
        not isinstance(result["person_alias"], str)
        or not SAFE_NAME_RE.fullmatch(result["person_alias"])
    ):
        raise PayloadError("invalid_person_alias")
    if "state_hash" in result and (
        not isinstance(result["state_hash"], str)
        or not re.fullmatch(r"[0-9a-f]{64}", result["state_hash"])
    ):
        raise PayloadError("invalid_state_hash")
    if "confidence" in result and result["confidence"] not in (
        "canonical",
        "positive_detection",
    ):
        raise PayloadError("invalid_confidence")
    for key in ("from_site", "to_site"):
        if key in result and result[key] is not None and result[key] not in SITES:
            raise PayloadError("invalid_attribute_site")
    for key in ("evidence_at", "not_before", "not_after"):
        if key in result and result[key] is not None:
            normalized, _ = _parse_timestamp(result[key], "invalid_attribute_time")
            result[key] = normalized
    if source == "presence" and event_type == "presence.occupancy_changed":
        occupancy_states = {
            "occupied",
            "confirmed_vacant",
            "possibly_vacant",
            "unknown",
        }
        if "previous" in result and result["previous"] not in occupancy_states:
            raise PayloadError("invalid_presence_state")
        if "current" in result and result["current"] not in occupancy_states:
            raise PayloadError("invalid_presence_state")
    if source == "august" and event_type.startswith("lock."):
        if "previous" in result and result["previous"] not in ("locked", "unlocked"):
            raise PayloadError("invalid_lock_state")
        if "current" in result and result["current"] not in ("locked", "unlocked"):
            raise PayloadError("invalid_lock_state")
    if source == "august" and event_type.startswith("door."):
        if "previous" in result and result["previous"] not in ("open", "closed"):
            raise PayloadError("invalid_door_state")
        if "current" in result and result["current"] not in ("open", "closed"):
            raise PayloadError("invalid_door_state")
    encoded = json.dumps(result, sort_keys=True, separators=(",", ":")).encode("utf-8")
    if len(encoded) > MAX_ATTRIBUTES_BYTES:
        raise PayloadError("attributes_too_large")
    return result


def _validate_event_contract(
    source: str,
    event_type: str,
    site: str,
    entity_kind: str,
    entity_alias: str,
    occurred_at: str,
    occurred: dt.datetime,
    observed_at: str,
    observed: dt.datetime,
    time_precision: str,
    attributes: Mapping[str, Any],
) -> None:
    if entity_kind != EVENT_ENTITY_KIND[source][event_type]:
        raise PayloadError("event_entity_mismatch")

    if source in SAFE_DEVICE_ALIASES and entity_alias not in SAFE_DEVICE_ALIASES[source]:
        raise PayloadError("unbound_entity_alias")
    if source == "august" and site != "crosstown":
        raise PayloadError("unbound_entity_site")

    if source == "ring":
        expected_classification = {
            "entry.person_detected": "person",
            "entry.motion_detected": "motion",
        }.get(event_type)
        if expected_classification is not None and attributes.get("classification") != expected_classification:
            raise PayloadError("classification_mismatch")
        backfill = attributes.get("backfill", False)
        expected_precision = "backfill" if backfill else "source"
        if time_precision != expected_precision:
            raise PayloadError("backfill_precision_mismatch")
        age = observed - occurred
        if backfill:
            if age <= RING_LIVE_MAX_AGE or age > RING_BACKFILL_MAX_AGE:
                raise PayloadError("invalid_ring_backfill_age")
        elif age > RING_LIVE_MAX_AGE:
            raise PayloadError("unmarked_ring_backfill")

    if source == "presence":
        evidence_at = attributes.get("evidence_at")
        if not isinstance(evidence_at, str):
            raise PayloadError("invalid_attribute_time")
        _, evidence = _parse_timestamp(evidence_at, "invalid_attribute_time")
        if evidence > observed + dt.timedelta(minutes=5):
            raise PayloadError("evidence_in_future")
        if event_type == "presence.occupancy_changed":
            if entity_alias != site:
                raise PayloadError("unbound_entity_alias")
            if attributes.get("confidence") != "canonical":
                raise PayloadError("confidence_mismatch")
            if attributes.get("previous") == attributes.get("current"):
                raise PayloadError("invalid_presence_transition")
        else:
            if entity_alias not in SAFE_PERSON_ALIASES:
                raise PayloadError("unbound_entity_alias")
            if attributes.get("person_alias") != entity_alias:
                raise PayloadError("person_alias_mismatch")
            if attributes.get("to_site") != site:
                raise PayloadError("relocation_site_mismatch")
            if attributes.get("confidence") != "positive_detection":
                raise PayloadError("confidence_mismatch")
            if attributes.get("from_site") == attributes.get("to_site"):
                raise PayloadError("invalid_relocation")

    if source == "august":
        if occurred_at != observed_at or occurred != observed:
            raise PayloadError("invalid_observed_interval")
        if event_type.startswith(("lock.", "door.", "device.")):
            not_before = attributes.get("not_before")
            not_after = attributes.get("not_after")
            if not isinstance(not_before, str) or not isinstance(not_after, str):
                raise PayloadError("invalid_observed_interval")
            normalized_before, before = _parse_timestamp(
                not_before, "invalid_attribute_time"
            )
            normalized_after, after = _parse_timestamp(
                not_after, "invalid_attribute_time"
            )
            if before > after or normalized_after != observed_at:
                raise PayloadError("invalid_observed_interval")
        expected_transition = {
            "lock.locked": ("unlocked", "locked"),
            "lock.unlocked": ("locked", "unlocked"),
            "door.opened": ("closed", "open"),
            "door.closed": ("open", "closed"),
        }.get(event_type)
        if expected_transition is not None and (
            attributes.get("previous"), attributes.get("current")
        ) != expected_transition:
            raise PayloadError("transition_direction_mismatch")
        if event_type == "device.battery_low" and (
            attributes.get("threshold") != 20
            or not isinstance(attributes.get("battery_percent"), int)
            or attributes["battery_percent"] > 20
        ):
            raise PayloadError("battery_transition_mismatch")
        if event_type == "device.battery_recovered" and (
            attributes.get("threshold") != 25
            or not isinstance(attributes.get("battery_percent"), int)
            or attributes["battery_percent"] < 25
        ):
            raise PayloadError("battery_transition_mismatch")

    if source == "nest":
        expected_classification = {
            "camera.person_detected": "person",
            "camera.motion_detected": "motion",
        }[event_type]
        if attributes.get("classification") != expected_classification:
            raise PayloadError("classification_mismatch")
        expected_site = {
            "kitchen": "cabin",
            "living_room": "crosstown",
            "living_room_wired": "crosstown",
        }[entity_alias]
        if site != expected_site:
            raise PayloadError("unbound_entity_site")


def _opaque_hmac(secret: bytes, namespace: str, value: str) -> str:
    material = (namespace + "\0" + value).encode("utf-8")
    return hmac.new(secret, material, hashlib.sha256).hexdigest()


def _record_mac(secret: bytes, value: Mapping[str, Any]) -> str:
    canonical = json.dumps(value, sort_keys=True, separators=(",", ":"))
    return "mac_" + _opaque_hmac(secret, "spool-record", canonical)


def normalize_input(
    source: str,
    value: Any,
    secret: bytes,
    *,
    clock: Callable[[], str] = utc_now,
) -> NormalizedEvent:
    if source not in SOURCES:
        raise PayloadError("invalid_source")
    if (
        not isinstance(value, dict)
        or not INPUT_REQUIRED_FIELDS.issubset(value)
        or not frozenset(value).issubset(INPUT_ALLOWED_FIELDS)
    ):
        raise PayloadError("invalid_event_fields")
    payload = value
    if payload.get("schema_version", EVENT_SCHEMA_VERSION) != EVENT_SCHEMA_VERSION:
        raise PayloadError("invalid_event_schema")

    source_event_id = payload["source_event_id"]
    if (
        not isinstance(source_event_id, str)
        or not source_event_id
        or len(source_event_id) > 1024
        or CONTROL_RE.search(source_event_id)
    ):
        raise PayloadError("invalid_source_event_id")

    event_type = payload["event_type"]
    if not isinstance(event_type, str) or event_type not in EVENT_RULES[source]:
        raise PayloadError("invalid_event_type")
    site = payload["site"]
    if site not in SITES:
        raise PayloadError("invalid_site")
    entity_kind = payload["entity_kind"]
    if entity_kind not in ENTITY_KINDS[source]:
        raise PayloadError("invalid_entity_kind")
    entity_alias = _validate_safe_name(payload["entity_alias"], "invalid_entity_alias")
    time_precision = payload["time_precision"]
    if time_precision not in TIME_PRECISIONS[source]:
        raise PayloadError("invalid_time_precision")

    occurred_at, occurred = _parse_timestamp(payload["occurred_at"], "invalid_occurred_at")
    observed_at, observed = _parse_timestamp(payload["observed_at"], "invalid_observed_at")
    _, now = _parse_timestamp(clock(), "invalid_clock")
    if occurred > observed + dt.timedelta(minutes=5):
        raise PayloadError("occurred_after_observed")
    if occurred < observed - dt.timedelta(days=7):
        raise PayloadError("occurred_too_old")
    if observed > now + dt.timedelta(minutes=5):
        raise PayloadError("observed_in_future")
    if observed < now - dt.timedelta(days=7):
        raise PayloadError("observed_too_old")

    attributes = _validate_attributes(source, event_type, payload["attributes"])
    _validate_event_contract(
        source,
        event_type,
        site,
        entity_kind,
        entity_alias,
        occurred_at,
        occurred,
        observed_at,
        observed,
        time_precision,
        attributes,
    )
    identity = "\0".join((source, site, entity_alias, event_type, source_event_id))
    dedupe_key = "ded_" + _opaque_hmac(secret, "source-event", identity)
    event_uid = "evt_" + _opaque_hmac(secret, "event-uid", dedupe_key)[:32]
    return NormalizedEvent(
        event_uid=event_uid,
        dedupe_key=dedupe_key,
        source=source,
        event_type=event_type,
        site=site,
        entity_kind=entity_kind,
        entity_alias=entity_alias,
        occurred_at=occurred_at,
        observed_at=observed_at,
        time_precision=time_precision,
        attributes=attributes,
    )


def validate_spool_record(value: Any, secret: bytes) -> NormalizedEvent:
    payload = _require_exact_fields(value, SPOOL_FIELDS, "invalid_spool_fields")
    if payload["schema_version"] != EVENT_SCHEMA_VERSION:
        raise PayloadError("invalid_spool_schema")
    source = payload["source"]
    if source not in SOURCES:
        raise PayloadError("invalid_source")
    event_uid = payload["event_uid"]
    dedupe_key = payload["dedupe_key"]
    if not isinstance(event_uid, str) or not EVENT_UID_RE.fullmatch(event_uid):
        raise PayloadError("invalid_event_uid")
    if not isinstance(dedupe_key, str) or not DEDUPE_KEY_RE.fullmatch(dedupe_key):
        raise PayloadError("invalid_dedupe_key")
    record_mac = payload["record_mac"]
    if not isinstance(record_mac, str) or not RECORD_MAC_RE.fullmatch(record_mac):
        raise PayloadError("invalid_record_mac")
    authenticated = dict(payload)
    del authenticated["record_mac"]
    if not hmac.compare_digest(record_mac, _record_mac(secret, authenticated)):
        raise PayloadError("spool_integrity")
    expected_uid = "evt_" + _opaque_hmac(secret, "event-uid", dedupe_key)[:32]
    if not hmac.compare_digest(event_uid, expected_uid):
        raise PayloadError("spool_integrity")

    event_type = payload["event_type"]
    if event_type not in EVENT_RULES[source]:
        raise PayloadError("invalid_event_type")
    site = payload["site"]
    if site not in SITES:
        raise PayloadError("invalid_site")
    entity_kind = payload["entity_kind"]
    if entity_kind not in ENTITY_KINDS[source]:
        raise PayloadError("invalid_entity_kind")
    entity_alias = _validate_safe_name(payload["entity_alias"], "invalid_entity_alias")
    time_precision = payload["time_precision"]
    if time_precision not in TIME_PRECISIONS[source]:
        raise PayloadError("invalid_time_precision")
    occurred_at, occurred = _parse_timestamp(
        payload["occurred_at"], "invalid_occurred_at"
    )
    observed_at, observed = _parse_timestamp(
        payload["observed_at"], "invalid_observed_at"
    )
    if occurred > observed + dt.timedelta(minutes=5):
        raise PayloadError("occurred_after_observed")
    if occurred < observed - dt.timedelta(days=7):
        raise PayloadError("occurred_too_old")
    attributes = _validate_attributes(source, event_type, payload["attributes"])
    _validate_event_contract(
        source,
        event_type,
        site,
        entity_kind,
        entity_alias,
        occurred_at,
        occurred,
        observed_at,
        observed,
        time_precision,
        attributes,
    )
    return NormalizedEvent(
        event_uid,
        dedupe_key,
        source,
        event_type,
        site,
        entity_kind,
        entity_alias,
        occurred_at,
        observed_at,
        time_precision,
        attributes,
    )


def _assert_absolute_root(root: Path) -> None:
    if not root.is_absolute():
        raise ConfigError("root_not_absolute")


def _assert_private_directory(path: Path, *, create: bool = False) -> None:
    if create:
        try:
            # The caller must provision the parent (normally ~/.openclaw).
            # Avoid creating an unchecked ancestor with the process umask.
            path.mkdir(mode=0o700, parents=False, exist_ok=True)
        except OSError as exc:
            raise ConfigError("directory_unavailable") from exc
    try:
        metadata = path.lstat()
    except OSError as exc:
        raise ConfigError("directory_unavailable") from exc
    if (
        stat.S_ISLNK(metadata.st_mode)
        or not stat.S_ISDIR(metadata.st_mode)
        or metadata.st_uid != os.geteuid()
        or stat.S_IMODE(metadata.st_mode) != 0o700
    ):
        raise ConfigError("directory_permissions")


def _open_private_regular(path: Path, flags: int, mode: int = 0o600) -> int:
    flags |= getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags, mode)
    except OSError as exc:
        raise ConfigError("private_file_unavailable") from exc
    try:
        metadata = os.fstat(descriptor)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_uid != os.geteuid()
            or stat.S_IMODE(metadata.st_mode) & 0o077
        ):
            raise ConfigError("private_file_permissions")
        os.fchmod(descriptor, 0o600)
    except Exception:
        os.close(descriptor)
        raise
    return descriptor


def _read_private_bytes(path: Path, maximum: int) -> bytes:
    descriptor = _open_private_regular(path, os.O_RDONLY)
    try:
        metadata = os.fstat(descriptor)
        if metadata.st_size <= 0 or metadata.st_size > maximum:
            raise ConfigError("private_file_size")
        data = b""
        while len(data) <= maximum:
            chunk = os.read(descriptor, min(8192, maximum + 1 - len(data)))
            if not chunk:
                break
            data += chunk
        if len(data) > maximum:
            raise ConfigError("private_file_size")
        return data
    finally:
        os.close(descriptor)


def _load_secret(paths: RuntimePaths) -> bytes:
    secret = _read_private_bytes(paths.secret, 64)
    if len(secret) != 32:
        raise ConfigError("dedupe_key_invalid")
    return secret


def _ring_producer_status(paths: RuntimePaths) -> Mapping[str, Any]:
    empty = {
        "health": "unknown",
        "updated_at": None,
        "error_code": None,
        "counters": {
            "accepted": 0,
            "published": 0,
            "failed": 0,
            "dropped": 0,
            "quarantined": 0,
        },
    }
    path = paths.ring_producer_status
    if not path.exists() and not path.is_symlink():
        return empty
    try:
        payload = _decode_json(
            _read_private_bytes(path, 16 * 1024),
            max_bytes=16 * 1024,
            code="ring_status_too_large",
        )
        if not isinstance(payload, dict) or set(payload) != {
            "schema_version",
            "updated_at",
            "health",
            "counters",
        }:
            raise PayloadError("invalid_ring_status")
        if payload["schema_version"] != 1 or payload["health"] not in {
            "ok",
            "degraded",
        }:
            raise PayloadError("invalid_ring_status")
        updated_at, _ = _parse_timestamp(
            payload["updated_at"], "invalid_ring_status_time"
        )
        counters = payload["counters"]
        if not isinstance(counters, dict) or set(counters) != set(empty["counters"]):
            raise PayloadError("invalid_ring_status")
        if any(
            not isinstance(value, int)
            or isinstance(value, bool)
            or value < 0
            or value > 10**12
            for value in counters.values()
        ):
            raise PayloadError("invalid_ring_status")
        return {
            "health": payload["health"],
            "updated_at": updated_at,
            "error_code": None,
            "counters": dict(counters),
        }
    except (HomeEventError, OSError, UnicodeError, ValueError):
        return {
            **empty,
            "health": "degraded",
            "error_code": "producer_status_invalid",
        }


def _fsync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _atomic_write(path: Path, data: bytes) -> None:
    _assert_private_directory(path.parent)
    if path.exists() and path.is_symlink():
        raise StateError("target_symlink")
    temporary = path.parent / ("." + path.name + "." + secrets.token_hex(16))
    descriptor = -1
    try:
        descriptor = _open_private_regular(
            temporary,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL,
        )
        view = memoryview(data)
        while view:
            written = os.write(descriptor, view)
            view = view[written:]
        os.fsync(descriptor)
        os.close(descriptor)
        descriptor = -1
        os.replace(temporary, path)
        _fsync_directory(path.parent)
    except OSError as exc:
        raise StateError("atomic_write_failed") from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def _durable_unlink(path: Path) -> None:
    try:
        path.unlink()
        _fsync_directory(path.parent)
    except FileNotFoundError:
        return
    except OSError as exc:
        raise StateError("spool_cleanup_failed") from exc


def _create_private_file(path: Path, data: bytes) -> None:
    descriptor = _open_private_regular(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL)
    try:
        view = memoryview(data)
        while view:
            written = os.write(descriptor, view)
            view = view[written:]
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    _fsync_directory(path.parent)


def initialize_runtime(root: Path, *, clock: Callable[[], str] = utc_now) -> "EventStore":
    _assert_absolute_root(root)
    paths = RuntimePaths(root)
    _assert_private_directory(paths.root, create=True)
    for directory in (paths.config, paths.spool, paths.state):
        _assert_private_directory(directory, create=True)
    for source in SOURCES:
        _assert_private_directory(paths.source_spool(source), create=True)
    if not paths.secret.exists():
        _create_private_file(paths.secret, secrets.token_bytes(32))
    _load_secret(paths)
    if not paths.ingest_lock.exists():
        _create_private_file(paths.ingest_lock, b"home-events-ingest\n")
    store = EventStore(paths, clock=clock)
    store.initialize()
    store.write_status_best_effort()
    return store


def validate_runtime(root: Path, *, require_status: bool = False) -> RuntimePaths:
    _assert_absolute_root(root)
    paths = RuntimePaths(root)
    for directory in (paths.root, paths.config, paths.spool, paths.state):
        _assert_private_directory(directory)
    for source in SOURCES:
        _assert_private_directory(paths.source_spool(source))
    _load_secret(paths)
    descriptor = _open_private_regular(paths.database, os.O_RDONLY)
    os.close(descriptor)
    for suffix in ("-wal", "-shm"):
        auxiliary = Path(str(paths.database) + suffix)
        if auxiliary.exists() or auxiliary.is_symlink():
            descriptor = _open_private_regular(auxiliary, os.O_RDONLY)
            os.close(descriptor)
    descriptor = _open_private_regular(paths.ingest_lock, os.O_RDONLY)
    os.close(descriptor)
    if require_status:
        descriptor = _open_private_regular(paths.status, os.O_RDONLY)
        os.close(descriptor)
    return paths


def enqueue_event(
    root: Path,
    source: str,
    data: bytes,
    *,
    clock: Callable[[], str] = utc_now,
) -> NormalizedEvent:
    paths = validate_runtime(root)
    secret = _load_secret(paths)
    parsed = _decode_json(data, max_bytes=MAX_STDIN_BYTES, code="event_too_large")
    event = normalize_input(source, parsed, secret, clock=clock)
    destination = paths.source_spool(source) / (
        event.event_uid + "." + secrets.token_hex(16) + ".ready"
    )
    record = event.as_spool_record()
    record["record_mac"] = _record_mac(secret, record)
    encoded = json.dumps(
        record,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8") + b"\n"
    if len(encoded) > MAX_SPOOL_BYTES:
        raise PayloadError("normalized_event_too_large")
    _atomic_write(destination, encoded)
    return event


SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS schema_migrations (
    version INTEGER PRIMARY KEY,
    applied_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS producer_inbox (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    receipt_uid TEXT NOT NULL UNIQUE,
    source TEXT NOT NULL CHECK(source IN ('ring', 'presence', 'august', 'nest')),
    event_uid TEXT,
    received_at TEXT NOT NULL,
    outcome TEXT NOT NULL CHECK(outcome IN ('accepted', 'duplicate', 'dead_letter')),
    error_code TEXT
);
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    producer_inbox_id INTEGER NOT NULL REFERENCES producer_inbox(id),
    event_uid TEXT NOT NULL UNIQUE,
    dedupe_key TEXT NOT NULL UNIQUE,
    source TEXT NOT NULL CHECK(source IN ('ring', 'presence', 'august', 'nest')),
    event_type TEXT NOT NULL,
    site TEXT NOT NULL CHECK(site IN ('cabin', 'crosstown')),
    entity_kind TEXT NOT NULL,
    entity_alias TEXT NOT NULL,
    occurred_at TEXT NOT NULL,
    observed_at TEXT NOT NULL,
    time_precision TEXT NOT NULL,
    attributes_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS producer_state (
    source TEXT PRIMARY KEY CHECK(source IN ('ring', 'presence', 'august', 'nest')),
    last_event_id INTEGER REFERENCES events(id) ON DELETE SET NULL,
    last_observed_at TEXT,
    last_ingested_at TEXT,
    accepted_count INTEGER NOT NULL DEFAULT 0,
    duplicate_count INTEGER NOT NULL DEFAULT 0,
    error_count INTEGER NOT NULL DEFAULT 0,
    health TEXT NOT NULL DEFAULT 'unknown'
        CHECK(health IN ('unknown', 'ok', 'degraded')),
    consecutive_failures INTEGER NOT NULL DEFAULT 0,
    last_error_code TEXT
);
CREATE TABLE IF NOT EXISTS consumers (
    name TEXT PRIMARY KEY,
    enabled INTEGER NOT NULL CHECK(enabled IN (0, 1)),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS consumer_deliveries (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    consumer_name TEXT NOT NULL REFERENCES consumers(name),
    event_id INTEGER NOT NULL REFERENCES events(id),
    status TEXT NOT NULL CHECK(status IN ('pending', 'leased', 'acknowledged', 'dead_letter')),
    lease_token TEXT,
    lease_until TEXT,
    attempts INTEGER NOT NULL DEFAULT 0,
    error_code TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(consumer_name, event_id)
);
CREATE TABLE IF NOT EXISTS incidents (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    incident_uid TEXT NOT NULL UNIQUE,
    site TEXT NOT NULL CHECK(site IN ('cabin', 'crosstown')),
    state TEXT NOT NULL CHECK(state IN ('open', 'resolved', 'expired_unresolved')),
    category TEXT NOT NULL,
    summary_code TEXT NOT NULL,
    opened_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    resolved_at TEXT
);
CREATE TABLE IF NOT EXISTS incident_events (
    incident_id INTEGER NOT NULL REFERENCES incidents(id),
    event_id INTEGER NOT NULL REFERENCES events(id),
    relation TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY(incident_id, event_id)
);
CREATE TABLE IF NOT EXISTS incident_decisions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    incident_id INTEGER NOT NULL REFERENCES incidents(id),
    status TEXT NOT NULL CHECK(status IN ('shadowed', 'suppressed', 'rate_limited')),
    reason_code TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE(incident_id, status, reason_code)
);
CREATE TABLE IF NOT EXISTS notification_outbox (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    incident_id INTEGER NOT NULL REFERENCES incidents(id),
    site TEXT NOT NULL CHECK(site IN ('cabin', 'crosstown')),
    status TEXT NOT NULL CHECK(status IN ('shadowed', 'reserved', 'sent', 'failed', 'unknown', 'burned')),
    reservation_token TEXT,
    reserved_until TEXT,
    attempt_count INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS service_counters (
    name TEXT PRIMARY KEY,
    value INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS runtime_status (
    singleton INTEGER PRIMARY KEY CHECK(singleton = 1),
    mode TEXT NOT NULL CHECK(mode = 'shadow'),
    health TEXT NOT NULL CHECK(health IN ('starting', 'ok', 'degraded')),
    started_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    last_ingest_at TEXT,
    last_accepted_at TEXT,
    last_error_at TEXT,
    last_error_code TEXT
);
CREATE INDEX IF NOT EXISTS producer_inbox_received_idx ON producer_inbox(received_at);
CREATE INDEX IF NOT EXISTS events_created_idx ON events(created_at);
CREATE INDEX IF NOT EXISTS events_site_occurred_idx ON events(site, occurred_at DESC);
CREATE INDEX IF NOT EXISTS events_type_occurred_idx ON events(event_type, occurred_at DESC);
CREATE INDEX IF NOT EXISTS deliveries_status_idx ON consumer_deliveries(status, id);
CREATE INDEX IF NOT EXISTS incidents_site_updated_idx ON incidents(site, updated_at DESC);
CREATE INDEX IF NOT EXISTS incident_decisions_incident_idx ON incident_decisions(incident_id, id);
CREATE INDEX IF NOT EXISTS notification_status_idx ON notification_outbox(status, id);
"""

EXPECTED_TABLES = frozenset(
    {
        "schema_migrations",
        "producer_inbox",
        "events",
        "producer_state",
        "consumers",
        "consumer_deliveries",
        "incidents",
        "incident_events",
        "incident_decisions",
        "notification_outbox",
        "service_counters",
        "runtime_status",
    }
)

EXPECTED_COLUMNS: Mapping[str, frozenset] = {
    "schema_migrations": frozenset({"version", "applied_at"}),
    "producer_inbox": frozenset(
        {"id", "receipt_uid", "source", "event_uid", "received_at", "outcome", "error_code"}
    ),
    "events": frozenset(
        {
            "id",
            "producer_inbox_id",
            "event_uid",
            "dedupe_key",
            "source",
            "event_type",
            "site",
            "entity_kind",
            "entity_alias",
            "occurred_at",
            "observed_at",
            "time_precision",
            "attributes_json",
            "created_at",
        }
    ),
    "producer_state": frozenset(
        {
            "source",
            "last_event_id",
            "last_observed_at",
            "last_ingested_at",
            "accepted_count",
            "duplicate_count",
            "error_count",
            "health",
            "consecutive_failures",
            "last_error_code",
        }
    ),
    "consumers": frozenset({"name", "enabled", "created_at", "updated_at"}),
    "consumer_deliveries": frozenset(
        {
            "id",
            "consumer_name",
            "event_id",
            "status",
            "lease_token",
            "lease_until",
            "attempts",
            "error_code",
            "created_at",
            "updated_at",
        }
    ),
    "incidents": frozenset(
        {
            "id",
            "incident_uid",
            "site",
            "state",
            "category",
            "summary_code",
            "opened_at",
            "updated_at",
            "resolved_at",
        }
    ),
    "incident_events": frozenset(
        {"incident_id", "event_id", "relation", "created_at"}
    ),
    "incident_decisions": frozenset(
        {"id", "incident_id", "status", "reason_code", "created_at"}
    ),
    "notification_outbox": frozenset(
        {
            "id",
            "incident_id",
            "site",
            "status",
            "reservation_token",
            "reserved_until",
            "attempt_count",
            "created_at",
            "updated_at",
        }
    ),
    "service_counters": frozenset({"name", "value"}),
    "runtime_status": frozenset(
        {
            "singleton",
            "mode",
            "health",
            "started_at",
            "updated_at",
            "last_ingest_at",
            "last_accepted_at",
            "last_error_at",
            "last_error_code",
        }
    ),
}


MIGRATION_V2_TABLE_SQL = (
    """
    CREATE TABLE producer_inbox_v2 (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        receipt_uid TEXT NOT NULL UNIQUE,
        source TEXT NOT NULL CHECK(source IN ('ring', 'presence', 'august', 'nest')),
        event_uid TEXT,
        received_at TEXT NOT NULL,
        outcome TEXT NOT NULL CHECK(outcome IN ('accepted', 'duplicate', 'dead_letter')),
        error_code TEXT
    )
    """,
    """
    CREATE TABLE events_v2 (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        producer_inbox_id INTEGER NOT NULL REFERENCES producer_inbox_v2(id),
        event_uid TEXT NOT NULL UNIQUE,
        dedupe_key TEXT NOT NULL UNIQUE,
        source TEXT NOT NULL CHECK(source IN ('ring', 'presence', 'august', 'nest')),
        event_type TEXT NOT NULL,
        site TEXT NOT NULL CHECK(site IN ('cabin', 'crosstown')),
        entity_kind TEXT NOT NULL,
        entity_alias TEXT NOT NULL,
        occurred_at TEXT NOT NULL,
        observed_at TEXT NOT NULL,
        time_precision TEXT NOT NULL,
        attributes_json TEXT NOT NULL,
        created_at TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE producer_state_v2 (
        source TEXT PRIMARY KEY CHECK(source IN ('ring', 'presence', 'august', 'nest')),
        last_event_id INTEGER REFERENCES events_v2(id) ON DELETE SET NULL,
        last_observed_at TEXT,
        last_ingested_at TEXT,
        accepted_count INTEGER NOT NULL DEFAULT 0,
        duplicate_count INTEGER NOT NULL DEFAULT 0,
        error_count INTEGER NOT NULL DEFAULT 0,
        health TEXT NOT NULL DEFAULT 'unknown'
            CHECK(health IN ('unknown', 'ok', 'degraded')),
        consecutive_failures INTEGER NOT NULL DEFAULT 0,
        last_error_code TEXT
    )
    """,
)

MIGRATION_V2_COPY_SQL = (
    "INSERT INTO producer_inbox_v2 SELECT * FROM producer_inbox",
    "INSERT INTO events_v2 SELECT * FROM events",
    "INSERT INTO producer_state_v2 SELECT * FROM producer_state",
)

MIGRATION_V2_INDEX_SQL = (
    "CREATE INDEX producer_inbox_received_idx ON producer_inbox(received_at)",
    "CREATE INDEX events_created_idx ON events(created_at)",
    "CREATE INDEX events_site_occurred_idx ON events(site, occurred_at DESC)",
    "CREATE INDEX events_type_occurred_idx ON events(event_type, occurred_at DESC)",
)


class EventStore:
    """SQLite store with one explicit writer boundary and safe read queries."""

    def __init__(self, paths: RuntimePaths, *, clock: Callable[[], str] = utc_now):
        self.paths = paths
        self.clock = clock

    def _now(self) -> str:
        value = self.clock()
        _parse_now(value)
        return _format_timestamp(_parse_now(value))

    def _prepare_database(self) -> None:
        descriptor = _open_private_regular(
            self.paths.database,
            os.O_RDWR | os.O_CREAT,
        )
        os.close(descriptor)

    def connect(self, *, read_only: bool = False) -> sqlite3.Connection:
        if read_only:
            descriptor = _open_private_regular(self.paths.database, os.O_RDONLY)
            os.close(descriptor)
            uri = "file:" + str(self.paths.database) + "?mode=ro"
            connection = sqlite3.connect(uri, uri=True, timeout=15)
            connection.execute("PRAGMA query_only = ON")
        else:
            self._prepare_database()
            connection = sqlite3.connect(self.paths.database, timeout=15)
            mode = connection.execute("PRAGMA journal_mode = WAL").fetchone()[0]
            if str(mode).lower() != "wal":
                connection.close()
                raise StateError("database_wal_unavailable")
            connection.execute("PRAGMA synchronous = FULL")
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 15000")
        return connection

    def initialize(self) -> None:
        now = self._now()
        with contextlib.closing(self.connect()) as connection:
            connection.executescript(SCHEMA_SQL)
            versions = connection.execute(
                "SELECT version FROM schema_migrations ORDER BY version"
            ).fetchall()
            if not versions:
                connection.execute(
                    "INSERT INTO schema_migrations(version, applied_at) VALUES (?, ?)",
                    (SCHEMA_VERSION, now),
                )
            elif [row["version"] for row in versions] == [1]:
                self._migrate_v1_to_v2(connection, now)
            elif [row["version"] for row in versions] != [SCHEMA_VERSION]:
                raise ConfigError("database_schema")
            for source in SOURCES:
                connection.execute(
                    "INSERT OR IGNORE INTO producer_state(source) VALUES (?)",
                    (source,),
                )
            connection.execute(
                """
                INSERT INTO consumers(name, enabled, created_at, updated_at)
                VALUES ('correlator', 1, ?, ?)
                ON CONFLICT(name) DO NOTHING
                """,
                (now, now),
            )
            connection.execute(
                """
                INSERT INTO runtime_status(singleton, mode, health, started_at, updated_at)
                VALUES (1, 'shadow', 'ok', ?, ?)
                ON CONFLICT(singleton) DO UPDATE SET updated_at = excluded.updated_at
                """,
                (now, now),
            )
            connection.commit()
        self.check_schema()

    @staticmethod
    def _migrate_v1_to_v2(connection: sqlite3.Connection, now: str) -> None:
        """Expand the source allowlist without changing durable event identity.

        SQLite cannot alter a CHECK constraint in place.  Rebuild only the
        three source-constrained tables, preserve their integer primary keys,
        then verify every existing foreign key before committing.
        """

        connection.execute("PRAGMA foreign_keys = OFF")
        try:
            connection.execute("BEGIN IMMEDIATE")
            for statement in MIGRATION_V2_TABLE_SQL:
                connection.execute(statement)
            for statement in MIGRATION_V2_COPY_SQL:
                connection.execute(statement)
            connection.execute("DROP TABLE producer_state")
            connection.execute("DROP TABLE events")
            connection.execute("DROP TABLE producer_inbox")
            connection.execute(
                "ALTER TABLE producer_inbox_v2 RENAME TO producer_inbox"
            )
            connection.execute("ALTER TABLE events_v2 RENAME TO events")
            connection.execute(
                "ALTER TABLE producer_state_v2 RENAME TO producer_state"
            )
            for statement in MIGRATION_V2_INDEX_SQL:
                connection.execute(statement)
            connection.execute(
                "UPDATE schema_migrations SET version = ?, applied_at = ? WHERE version = 1",
                (SCHEMA_VERSION, now),
            )
            if connection.execute("PRAGMA foreign_key_check").fetchone() is not None:
                raise ConfigError("database_migration_integrity")
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.execute("PRAGMA foreign_keys = ON")
        if connection.execute("PRAGMA foreign_keys").fetchone()[0] != 1:
            raise ConfigError("database_foreign_keys")

    def check_schema(self) -> None:
        with contextlib.closing(self.connect(read_only=True)) as connection:
            journal_mode = connection.execute("PRAGMA journal_mode").fetchone()[0]
            if str(journal_mode).lower() != "wal":
                raise ConfigError("database_journal_mode")
            versions = connection.execute(
                "SELECT version FROM schema_migrations ORDER BY version"
            ).fetchall()
            if [row["version"] for row in versions] != [SCHEMA_VERSION]:
                raise ConfigError("database_schema")
            tables = {
                row["name"]
                for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table'"
                )
            }
            if not EXPECTED_TABLES.issubset(tables):
                raise ConfigError("database_schema")
            for table, expected in EXPECTED_COLUMNS.items():
                actual = {
                    row["name"]
                    for row in connection.execute(
                        "PRAGMA table_info(" + table + ")"
                    )
                }
                if actual != expected:
                    raise ConfigError("database_schema")
            integrity = connection.execute("PRAGMA quick_check").fetchone()[0]
            if integrity != "ok":
                raise ConfigError("database_integrity")

    @staticmethod
    def _increment(connection: sqlite3.Connection, name: str, amount: int = 1) -> None:
        connection.execute(
            """
            INSERT INTO service_counters(name, value) VALUES (?, ?)
            ON CONFLICT(name) DO UPDATE SET value = value + excluded.value
            """,
            (name, amount),
        )

    @staticmethod
    def _touch_status(
        connection: sqlite3.Connection,
        now: str,
        *,
        health: str = "ok",
        accepted: bool = False,
        error_code: Optional[str] = None,
    ) -> None:
        connection.execute(
            """
            UPDATE runtime_status SET
                health = CASE WHEN health = 'degraded' OR ? = 'degraded'
                    THEN 'degraded' ELSE ? END,
                updated_at = ?,
                last_ingest_at = ?,
                last_accepted_at = CASE WHEN ? THEN ? ELSE last_accepted_at END,
                last_error_at = CASE WHEN ? IS NOT NULL THEN ? ELSE last_error_at END,
                last_error_code = CASE WHEN ? IS NOT NULL THEN ? ELSE last_error_code END
            WHERE singleton = 1
            """,
            (
                health,
                health,
                now,
                now,
                1 if accepted else 0,
                now,
                error_code,
                now,
                error_code,
                error_code,
            ),
        )

    def _record_dead_letter(
        self,
        receipt_uid: str,
        source: str,
        error_code: str,
        now: str,
    ) -> str:
        with contextlib.closing(self.connect()) as connection:
            connection.execute("BEGIN IMMEDIATE")
            existing = connection.execute(
                "SELECT outcome FROM producer_inbox WHERE receipt_uid = ?",
                (receipt_uid,),
            ).fetchone()
            if existing is None:
                connection.execute(
                    """
                    INSERT INTO producer_inbox(
                        receipt_uid, source, received_at, outcome, error_code
                    ) VALUES (?, ?, ?, 'dead_letter', ?)
                    """,
                    (receipt_uid, source, now, error_code),
                )
                connection.execute(
                    """
                    UPDATE producer_state SET
                        error_count = error_count + 1,
                        last_ingested_at = ?, health = 'degraded',
                        last_error_code = ?
                    WHERE source = ?
                    """,
                    (now, error_code, source),
                )
                self._increment(connection, "dead_letters")
                self._touch_status(
                    connection,
                    now,
                    health="degraded",
                    error_code=error_code,
                )
            connection.commit()
        return "dead_letter"

    def ingest_event(
        self,
        receipt_uid: str,
        source_directory: str,
        event: NormalizedEvent,
    ) -> str:
        now = self._now()
        if event.source != source_directory:
            return self._record_dead_letter(
                receipt_uid,
                source_directory,
                "source_directory_mismatch",
                now,
            )
        with contextlib.closing(self.connect()) as connection:
            connection.execute("BEGIN IMMEDIATE")
            existing_receipt = connection.execute(
                "SELECT outcome FROM producer_inbox WHERE receipt_uid = ?",
                (receipt_uid,),
            ).fetchone()
            if existing_receipt is not None:
                connection.commit()
                if existing_receipt["outcome"] == "dead_letter":
                    return "dead_letter"
                return "duplicate"

            existing_event = connection.execute(
                "SELECT id FROM events WHERE dedupe_key = ?",
                (event.dedupe_key,),
            ).fetchone()
            outcome = "duplicate" if existing_event is not None else "accepted"
            cursor = connection.execute(
                """
                INSERT INTO producer_inbox(
                    receipt_uid, source, event_uid, received_at, outcome
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (receipt_uid, event.source, event.event_uid, now, outcome),
            )
            inbox_id = int(cursor.lastrowid)
            if existing_event is not None:
                connection.execute(
                    """
                    UPDATE producer_state SET
                        duplicate_count = duplicate_count + 1,
                        last_ingested_at = ?
                    WHERE source = ?
                    """,
                    (now, event.source),
                )
                self._increment(connection, "duplicate_events")
                self._touch_status(connection, now)
            else:
                event_cursor = connection.execute(
                    """
                    INSERT INTO events(
                        producer_inbox_id, event_uid, dedupe_key, source, event_type,
                        site, entity_kind, entity_alias, occurred_at, observed_at,
                        time_precision, attributes_json, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        inbox_id,
                        event.event_uid,
                        event.dedupe_key,
                        event.source,
                        event.event_type,
                        event.site,
                        event.entity_kind,
                        event.entity_alias,
                        event.occurred_at,
                        event.observed_at,
                        event.time_precision,
                        json.dumps(event.attributes, sort_keys=True, separators=(",", ":")),
                        now,
                    ),
                )
                event_id = int(event_cursor.lastrowid)
                connection.execute(
                    """
                    UPDATE producer_state SET
                        last_event_id = ?,
                        last_observed_at = CASE
                            WHEN last_observed_at IS NULL OR last_observed_at < ?
                            THEN ? ELSE last_observed_at END,
                        last_ingested_at = ?,
                        accepted_count = accepted_count + 1
                    WHERE source = ?
                    """,
                    (
                        event_id,
                        event.observed_at,
                        event.observed_at,
                        now,
                        event.source,
                    ),
                )
                if event.event_type == "source.unavailable":
                    connection.execute(
                        """
                        UPDATE producer_state SET
                            health = 'degraded',
                            consecutive_failures = ?,
                            last_error_code = ?
                        WHERE source = ?
                        """,
                        (
                            int(event.attributes["failure_count"]),
                            str(event.attributes["reason_code"]),
                            event.source,
                        ),
                    )
                elif event.event_type == "source.recovered":
                    connection.execute(
                        """
                        UPDATE producer_state SET
                            health = 'ok', consecutive_failures = 0,
                            last_error_code = NULL
                        WHERE source = ?
                        """,
                        (event.source,),
                    )
                else:
                    connection.execute(
                        """
                        UPDATE producer_state SET
                            health = CASE WHEN health = 'unknown' THEN 'ok' ELSE health END
                        WHERE source = ?
                        """,
                        (event.source,),
                    )
                connection.execute(
                    """
                    INSERT INTO consumer_deliveries(
                        consumer_name, event_id, status, created_at, updated_at
                    )
                    SELECT name, ?, 'pending', ?, ? FROM consumers WHERE enabled = 1
                    """,
                    (event_id, now, now),
                )
                self._increment(connection, "accepted_events")
                self._touch_status(connection, now, accepted=True)
            connection.commit()
        return outcome

    def claim_deliveries(
        self,
        consumer: str,
        *,
        limit: int = 20,
        lease_seconds: int = 120,
    ) -> Mapping[str, Any]:
        """Atomically claim pending or expired work for one known consumer."""

        if not SAFE_NAME_RE.fullmatch(consumer):
            raise PayloadError("invalid_consumer")
        if limit < 1 or limit > 100:
            raise PayloadError("invalid_limit")
        if lease_seconds < 15 or lease_seconds > 3600:
            raise PayloadError("invalid_lease")
        now = self._now()
        lease_until = _format_timestamp(
            _parse_now(now) + dt.timedelta(seconds=lease_seconds)
        )
        lease_token = "lease_" + secrets.token_hex(16)
        with contextlib.closing(self.connect()) as connection:
            connection.execute("BEGIN IMMEDIATE")
            known = connection.execute(
                "SELECT enabled FROM consumers WHERE name = ?",
                (consumer,),
            ).fetchone()
            if known is None or not known["enabled"]:
                connection.rollback()
                raise StateError("consumer_unavailable")
            active_lease = connection.execute(
                """
                SELECT 1 FROM consumer_deliveries
                WHERE consumer_name = ? AND status = 'leased'
                  AND lease_until >= ?
                LIMIT 1
                """,
                (consumer, now),
            ).fetchone()
            rows = [] if active_lease is not None else connection.execute(
                """
                SELECT d.id FROM consumer_deliveries d
                JOIN events e ON e.id = d.event_id
                WHERE d.consumer_name = ?
                  AND (
                    d.status = 'pending'
                    OR (d.status = 'leased' AND d.lease_until < ?)
                  )
                ORDER BY e.observed_at, e.id, d.id
                LIMIT ?
                """,
                (consumer, now, limit),
            ).fetchall()
            ids = [int(row["id"]) for row in rows]
            if ids:
                placeholders = ",".join("?" for _ in ids)
                connection.execute(
                    """
                    UPDATE consumer_deliveries SET
                        status = 'leased', lease_token = ?, lease_until = ?,
                        attempts = attempts + 1, updated_at = ?
                    WHERE id IN ({ids})
                    """.format(ids=placeholders),
                    [lease_token, lease_until, now, *ids],
                )
                claimed = connection.execute(
                    """
                    SELECT d.id AS delivery_id, d.attempts,
                           e.id AS event_id, e.event_uid, e.source, e.event_type,
                           e.site, e.entity_kind, e.entity_alias, e.occurred_at,
                           e.observed_at, e.time_precision, e.attributes_json
                    FROM consumer_deliveries d
                    JOIN events e ON e.id = d.event_id
                    WHERE d.id IN ({ids})
                    ORDER BY e.observed_at, e.id, d.id
                    """.format(ids=placeholders),
                    ids,
                ).fetchall()
            else:
                claimed = []
            connection.commit()
        deliveries = []
        for row in claimed:
            item = dict(row)
            item["attributes"] = json.loads(item.pop("attributes_json"))
            deliveries.append(item)
        return {
            "lease_token": lease_token,
            "lease_until": lease_until,
            "deliveries": deliveries,
        }

    def acknowledge_delivery(
        self,
        consumer: str,
        delivery_id: int,
        lease_token: str,
    ) -> None:
        self._finish_delivery(
            consumer,
            delivery_id,
            lease_token,
            status="acknowledged",
            error_code=None,
        )

    def dead_letter_delivery(
        self,
        consumer: str,
        delivery_id: int,
        lease_token: str,
        error_code: str,
    ) -> None:
        if not REASON_CODE_RE.fullmatch(error_code):
            raise PayloadError("invalid_error_code")
        self._finish_delivery(
            consumer,
            delivery_id,
            lease_token,
            status="dead_letter",
            error_code=error_code,
        )

    def _finish_delivery(
        self,
        consumer: str,
        delivery_id: int,
        lease_token: str,
        *,
        status: str,
        error_code: Optional[str],
    ) -> None:
        if not SAFE_NAME_RE.fullmatch(consumer):
            raise PayloadError("invalid_consumer")
        if (
            not isinstance(delivery_id, int)
            or isinstance(delivery_id, bool)
            or delivery_id < 1
        ):
            raise PayloadError("invalid_delivery_id")
        if not isinstance(lease_token, str) or not re.fullmatch(
            r"lease_[0-9a-f]{32}", lease_token
        ):
            raise PayloadError("invalid_lease_token")
        now = self._now()
        with contextlib.closing(self.connect()) as connection:
            connection.execute("BEGIN IMMEDIATE")
            cursor = connection.execute(
                """
                UPDATE consumer_deliveries SET
                    status = ?, lease_token = NULL, lease_until = NULL,
                    error_code = ?, updated_at = ?
                WHERE id = ? AND consumer_name = ?
                  AND status = 'leased' AND lease_token = ?
                """,
                (status, error_code, now, delivery_id, consumer, lease_token),
            )
            if cursor.rowcount != 1:
                connection.rollback()
                raise StateError("delivery_lease_mismatch")
            if status == "dead_letter":
                self._increment(connection, "consumer_dead_letters")
                self._touch_status(
                    connection,
                    now,
                    health="degraded",
                    error_code=error_code,
                )
            connection.commit()

    def prune(self, *, checkpoint: bool = True) -> Mapping[str, int]:
        now = self._now()
        accepted_cutoff = _cutoff(now, ACCEPTED_RETENTION_DAYS)
        dead_cutoff = _cutoff(now, DEAD_LETTER_RETENTION_DAYS)
        deleted: Dict[str, int] = {}
        with contextlib.closing(self.connect()) as connection:
            connection.execute("BEGIN IMMEDIATE")
            cursor = connection.execute(
                """
                DELETE FROM notification_outbox
                WHERE status != 'reserved' AND updated_at < ?
                """,
                (accepted_cutoff,),
            )
            deleted["notification_outbox"] = cursor.rowcount
            cursor = connection.execute(
                """
                DELETE FROM consumer_deliveries
                WHERE (status = 'acknowledged' AND updated_at < ?)
                   OR (status = 'dead_letter' AND updated_at < ?)
                """,
                (accepted_cutoff, dead_cutoff),
            )
            deleted["consumer_deliveries"] = cursor.rowcount
            cursor = connection.execute(
                """
                DELETE FROM incident_events
                WHERE incident_id IN (
                    SELECT id FROM incidents
                    WHERE state != 'open' AND updated_at < ?
                      AND NOT EXISTS (
                        SELECT 1 FROM notification_outbox n
                        WHERE n.incident_id = incidents.id AND n.status = 'reserved'
                      )
                )
                """,
                (accepted_cutoff,),
            )
            deleted["incident_events"] = cursor.rowcount
            cursor = connection.execute(
                """
                DELETE FROM incident_decisions
                WHERE incident_id IN (
                    SELECT id FROM incidents
                    WHERE state != 'open' AND updated_at < ?
                      AND NOT EXISTS (
                        SELECT 1 FROM notification_outbox n
                        WHERE n.incident_id = incidents.id AND n.status = 'reserved'
                      )
                )
                """,
                (accepted_cutoff,),
            )
            deleted["incident_decisions"] = cursor.rowcount
            cursor = connection.execute(
                """
                DELETE FROM incidents
                WHERE state != 'open' AND updated_at < ?
                  AND NOT EXISTS (
                    SELECT 1 FROM notification_outbox n
                    WHERE n.incident_id = incidents.id AND n.status = 'reserved'
                  )
                """,
                (accepted_cutoff,),
            )
            deleted["incidents"] = cursor.rowcount
            cursor = connection.execute(
                """
                DELETE FROM events
                WHERE created_at < ?
                  AND NOT EXISTS (
                    SELECT 1 FROM consumer_deliveries d
                    WHERE d.event_id = events.id
                  )
                  AND NOT EXISTS (
                    SELECT 1 FROM incident_events ie
                    WHERE ie.event_id = events.id
                  )
                """,
                (accepted_cutoff,),
            )
            deleted["events"] = cursor.rowcount
            cursor = connection.execute(
                """
                DELETE FROM producer_inbox
                WHERE (
                    outcome = 'dead_letter' AND received_at < ?
                ) OR (
                    outcome != 'dead_letter' AND received_at < ?
                    AND NOT EXISTS (
                        SELECT 1 FROM events e
                        WHERE e.producer_inbox_id = producer_inbox.id
                    )
                )
                """,
                (dead_cutoff, accepted_cutoff),
            )
            deleted["producer_inbox"] = cursor.rowcount
            self._increment(connection, "prune_runs")
            connection.commit()
        if checkpoint:
            with contextlib.closing(self.connect()) as connection:
                connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        self.write_status_best_effort()
        return deleted

    def status_snapshot(self) -> Mapping[str, Any]:
        with contextlib.closing(self.connect(read_only=True)) as connection:
            runtime = connection.execute(
                "SELECT * FROM runtime_status WHERE singleton = 1"
            ).fetchone()
            if runtime is None:
                raise ConfigError("runtime_status_missing")
            event_count = connection.execute("SELECT COUNT(*) FROM events").fetchone()[0]
            open_count = connection.execute(
                "SELECT COUNT(*) FROM incidents WHERE state = 'open'"
            ).fetchone()[0]
            pending_count = connection.execute(
                "SELECT COUNT(*) FROM consumer_deliveries WHERE status = 'pending'"
            ).fetchone()[0]
            leased_count = connection.execute(
                "SELECT COUNT(*) FROM consumer_deliveries WHERE status = 'leased'"
            ).fetchone()[0]
            dead_count = connection.execute(
                "SELECT COUNT(*) FROM producer_inbox WHERE outcome = 'dead_letter'"
            ).fetchone()[0]
            consumer_rows = connection.execute(
                """
                SELECT c.name,
                       SUM(CASE WHEN d.status = 'pending' THEN 1 ELSE 0 END) AS pending,
                       SUM(CASE WHEN d.status = 'leased' THEN 1 ELSE 0 END) AS leased,
                       SUM(CASE WHEN d.status = 'dead_letter' THEN 1 ELSE 0 END) AS dead_letter,
                       MIN(CASE WHEN d.status IN ('pending', 'leased') THEN d.created_at END)
                           AS oldest_unfinished_at
                FROM consumers c
                LEFT JOIN consumer_deliveries d ON d.consumer_name = c.name
                WHERE c.enabled = 1
                GROUP BY c.name
                ORDER BY c.name
                """
            ).fetchall()
            sources: Dict[str, Any] = {}
            for row in connection.execute(
                """
                SELECT source, last_observed_at, last_ingested_at,
                       accepted_count, duplicate_count, error_count,
                       health, consecutive_failures, last_error_code
                FROM producer_state ORDER BY source
                """
            ):
                queued = sum(
                    1
                    for path in self.paths.source_spool(row["source"]).glob("*.ready")
                    if READY_NAME_RE.fullmatch(path.name)
                )
                sources[row["source"]] = {
                    "last_observed_at": row["last_observed_at"],
                    "last_ingested_at": row["last_ingested_at"],
                    "accepted": row["accepted_count"],
                    "duplicates": row["duplicate_count"],
                    "errors": row["error_count"],
                    "health": row["health"],
                    "consecutive_failures": row["consecutive_failures"],
                    "last_error_code": row["last_error_code"],
                    "queued": queued,
                }
            counters = {
                row["name"]: row["value"]
                for row in connection.execute(
                    "SELECT name, value FROM service_counters ORDER BY name"
                )
            }
            ring_publisher = _ring_producer_status(self.paths)
            sources["ring"]["publisher"] = ring_publisher
            if ring_publisher["health"] == "degraded":
                sources["ring"]["health"] = "degraded"
            return {
                "schema_version": STATUS_SCHEMA_VERSION,
                "mode": runtime["mode"],
                "health": runtime["health"],
                "started_at": runtime["started_at"],
                "updated_at": runtime["updated_at"],
                "last_ingest_at": runtime["last_ingest_at"],
                "last_accepted_at": runtime["last_accepted_at"],
                "last_error_at": runtime["last_error_at"],
                "last_error_code": runtime["last_error_code"],
                "counts": {
                    "events": event_count,
                    "open_incidents": open_count,
                    "pending_deliveries": pending_count,
                    "leased_deliveries": leased_count,
                    "dead_letters": dead_count,
                    "spool_ready": sum(
                        source["queued"] for source in sources.values()
                    ),
                },
                "sources": sources,
                "consumers": {
                    row["name"]: {
                        "pending": row["pending"],
                        "leased": row["leased"],
                        "dead_letter": row["dead_letter"],
                        "oldest_unfinished_at": row["oldest_unfinished_at"],
                    }
                    for row in consumer_rows
                },
                "retention_days": {
                    "accepted": ACCEPTED_RETENTION_DAYS,
                    "dead_letter": DEAD_LETTER_RETENTION_DAYS,
                },
                "database_bytes": self.paths.database.stat().st_size,
                "counters": counters,
            }

    def write_status_best_effort(self) -> bool:
        try:
            value = self.status_snapshot()
            encoded = (
                json.dumps(value, sort_keys=True, separators=(",", ":")).encode(
                    "utf-8"
                )
                + b"\n"
            )
            _atomic_write(self.paths.status, encoded)
            return True
        except (HomeEventError, OSError, sqlite3.Error):
            return False

    def recent(
        self,
        *,
        since: str,
        limit: int,
        site: Optional[str] = None,
        event_type: Optional[str] = None,
    ) -> Mapping[str, Any]:
        clauses = ["occurred_at >= ?"]
        parameters: list[Any] = [since]
        if site is not None:
            clauses.append("site = ?")
            parameters.append(site)
        if event_type is not None:
            clauses.append("event_type = ?")
            parameters.append(event_type)
        parameters.append(limit)
        query = """
            SELECT event_uid, source, event_type, site, entity_kind, entity_alias,
                   occurred_at, observed_at, time_precision, attributes_json
            FROM events
            WHERE {where}
            ORDER BY occurred_at DESC, id DESC
            LIMIT ?
        """.format(where=" AND ".join(clauses))
        with contextlib.closing(self.connect(read_only=True)) as connection:
            rows = connection.execute(query, parameters).fetchall()
        events = []
        for row in rows:
            events.append(
                {
                    "event_uid": row["event_uid"],
                    "source": row["source"],
                    "event_type": row["event_type"],
                    "site": row["site"],
                    "entity_kind": row["entity_kind"],
                    "entity_alias": row["entity_alias"],
                    "occurred_at": row["occurred_at"],
                    "observed_at": row["observed_at"],
                    "time_precision": row["time_precision"],
                    "attributes": json.loads(row["attributes_json"]),
                }
            )
        return {"schema_version": 1, "events": events}

    def incidents(
        self,
        *,
        since: str,
        state: str,
        site: Optional[str] = None,
    ) -> Mapping[str, Any]:
        clauses = ["i.updated_at >= ?"]
        parameters: list[Any] = [since]
        if state != "all":
            clauses.append("i.state = ?")
            parameters.append(state)
        if site is not None:
            clauses.append("i.site = ?")
            parameters.append(site)
        with contextlib.closing(self.connect(read_only=True)) as connection:
            rows = connection.execute(
                """
                SELECT i.incident_uid, i.site, i.state, i.category,
                       i.summary_code, i.opened_at, i.updated_at, i.resolved_at,
                       COUNT(ie.event_id) AS event_count
                FROM incidents i
                LEFT JOIN incident_events ie ON ie.incident_id = i.id
                WHERE {where}
                GROUP BY i.id
                ORDER BY i.updated_at DESC, i.id DESC
                LIMIT {limit}
                """.format(where=" AND ".join(clauses), limit=MAX_QUERY_LIMIT),
                parameters,
            ).fetchall()
        return {
            "schema_version": 1,
            "incidents": [dict(row) for row in rows],
        }

    def explain(self, incident_uid: str) -> Mapping[str, Any]:
        if not INCIDENT_UID_RE.fullmatch(incident_uid):
            raise PayloadError("invalid_incident_uid")
        with contextlib.closing(self.connect(read_only=True)) as connection:
            incident = connection.execute(
                """
                SELECT incident_uid, site, state, category, summary_code,
                       opened_at, updated_at, resolved_at
                FROM incidents WHERE incident_uid = ?
                """,
                (incident_uid,),
            ).fetchone()
            if incident is None:
                raise StateError("incident_not_found")
            rows = connection.execute(
                """
                SELECT e.event_uid, e.source, e.event_type, e.site,
                       e.entity_kind, e.entity_alias, e.occurred_at,
                       e.observed_at, e.time_precision, e.attributes_json,
                       ie.relation
                FROM incident_events ie
                JOIN events e ON e.id = ie.event_id
                JOIN incidents i ON i.id = ie.incident_id
                WHERE i.incident_uid = ?
                ORDER BY e.occurred_at, e.id
                """,
                (incident_uid,),
            ).fetchall()
            decision_rows = connection.execute(
                """
                SELECT status, reason_code, created_at
                FROM incident_decisions d
                JOIN incidents i ON i.id = d.incident_id
                WHERE i.incident_uid = ?
                ORDER BY d.created_at, d.id
                """,
                (incident_uid,),
            ).fetchall()
        events = []
        for row in rows:
            item = dict(row)
            item["attributes"] = json.loads(item.pop("attributes_json"))
            events.append(item)
        return {
            "schema_version": 1,
            "incident": dict(incident),
            "events": events,
            "decisions": [dict(row) for row in decision_rows],
        }


def _receipt_uid(secret: bytes, source: str, filename: str) -> str:
    return "rcp_" + _opaque_hmac(secret, "spool-receipt", source + "\0" + filename)[:32]


def _read_spool(path: Path) -> bytes:
    try:
        return _read_private_bytes(path, MAX_SPOOL_BYTES)
    except ConfigError as exc:
        raise PayloadError("invalid_spool_file") from exc


def ingest_once(
    root: Path,
    *,
    limit: int = 100,
    clock: Callable[[], str] = utc_now,
) -> IngestResult:
    if limit < 1 or limit > 1000:
        raise PayloadError("invalid_ingest_limit")
    paths = validate_runtime(root)
    secret = _load_secret(paths)
    store = EventStore(paths, clock=clock)
    store.check_schema()
    lock_descriptor = _open_private_regular(paths.ingest_lock, os.O_RDWR)
    try:
        try:
            fcntl.flock(lock_descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise StateError("ingester_busy") from exc
        candidates = []
        for source in SOURCES:
            for path in paths.source_spool(source).glob("*.ready"):
                try:
                    queued_at = path.lstat().st_mtime_ns
                except OSError:
                    continue
                candidates.append((queued_at, source, path))
        candidates.sort(key=lambda item: (item[0], item[2].name, item[1]))
        result = IngestResult()
        for _queued_at, source, path in candidates[:limit]:
            receipt = _receipt_uid(secret, source, path.name)
            try:
                if not READY_NAME_RE.fullmatch(path.name):
                    raise PayloadError("invalid_spool_name")
                parsed = _decode_json(
                    _read_spool(path),
                    max_bytes=MAX_SPOOL_BYTES,
                    code="spool_too_large",
                )
                event = validate_spool_record(parsed, secret)
                outcome = store.ingest_event(receipt, source, event)
            except PayloadError as exc:
                outcome = store._record_dead_letter(
                    receipt,
                    source,
                    exc.code,
                    store._now(),
                )
            cleanup_pending = False
            try:
                _durable_unlink(path)
            except StateError:
                cleanup_pending = True
            result = result.incremented(outcome, cleanup_pending)
        store.prune(checkpoint=False)
        store.write_status_best_effort()
        return result
    finally:
        try:
            fcntl.flock(lock_descriptor, fcntl.LOCK_UN)
        finally:
            os.close(lock_descriptor)


def _parse_duration(value: str, *, clock: Callable[[], str] = utc_now) -> str:
    match = DURATION_RE.fullmatch(value)
    if match is None:
        raise PayloadError("invalid_since")
    amount = int(match.group(1))
    unit = match.group(2)
    if unit == "m":
        delta = dt.timedelta(minutes=amount)
    elif unit == "h":
        delta = dt.timedelta(hours=amount)
    else:
        delta = dt.timedelta(days=amount)
    if delta > dt.timedelta(days=90):
        raise PayloadError("invalid_since")
    return _format_timestamp(_parse_now(clock()) - delta)


def _json_output(value: Mapping[str, Any], *, stream: Any = sys.stdout) -> None:
    stream.write(json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    modes = parser.add_subparsers(dest="interface", required=True)

    operator = modes.add_parser("operator", help="operator-only commands")
    operator.add_argument("--root", type=Path, default=DEFAULT_ROOT)
    operator_commands = operator.add_subparsers(dest="command", required=True)
    operator_commands.add_parser("init")
    operator_commands.add_parser("check-config")
    enqueue = operator_commands.add_parser("enqueue")
    enqueue.add_argument("--source", required=True, choices=SOURCES)
    ingest = operator_commands.add_parser("ingest-once")
    ingest.add_argument("--limit", type=int, default=100)
    operator_commands.add_parser("status")
    operator_commands.add_parser("prune")

    agent = modes.add_parser("agent", help="read-only agent commands")
    agent_commands = agent.add_subparsers(dest="command", required=True)
    status = agent_commands.add_parser("status")
    status.add_argument("--json", action="store_true")
    recent = agent_commands.add_parser("recent")
    recent.add_argument("--site", choices=SITES)
    recent.add_argument("--since", default="24h")
    recent.add_argument("--limit", type=int, default=20)
    recent.add_argument("--type", dest="event_type")
    recent.add_argument("--json", action="store_true")
    incidents = agent_commands.add_parser("incidents")
    incidents.add_argument("--site", choices=SITES)
    incidents.add_argument("--state", choices=("open", "resolved", "all"), default="open")
    incidents.add_argument("--since", default="24h")
    incidents.add_argument("--json", action="store_true")
    explain = agent_commands.add_parser("explain")
    explain.add_argument("incident_uid")
    explain.add_argument("--json", action="store_true")
    return parser


def run_operator(args: argparse.Namespace) -> Mapping[str, Any]:
    root = args.root.expanduser()
    _assert_absolute_root(root)
    if args.command == "init":
        store = initialize_runtime(root)
        return {"ok": True, "status": store.status_snapshot()}
    if args.command == "check-config":
        paths = validate_runtime(root, require_status=True)
        store = EventStore(paths)
        store.check_schema()
        return {"ok": True, "schema_version": SCHEMA_VERSION, "mode": "shadow"}
    if args.command == "enqueue":
        data = sys.stdin.buffer.read(MAX_STDIN_BYTES + 1)
        event = enqueue_event(root, args.source, data)
        return {"ok": True, "status": "enqueued", "event_uid": event.event_uid}
    paths = validate_runtime(root)
    store = EventStore(paths)
    store.check_schema()
    if args.command == "ingest-once":
        result = ingest_once(root, limit=args.limit)
        return {"ok": True, **dataclasses.asdict(result)}
    if args.command == "status":
        return store.status_snapshot()
    if args.command == "prune":
        return {"ok": True, "deleted": store.prune()}
    raise StateError("unknown_command")


def run_agent(args: argparse.Namespace) -> Mapping[str, Any]:
    # The agent interface intentionally has no root override.  It can read only
    # the fixed production database and cannot inspect an arbitrary SQLite file.
    paths = validate_runtime(DEFAULT_ROOT)
    store = EventStore(paths)
    store.check_schema()
    if args.command == "status":
        return store.status_snapshot()
    if args.command == "recent":
        if args.limit < 1 or args.limit > MAX_QUERY_LIMIT:
            raise PayloadError("invalid_limit")
        if args.event_type is not None and args.event_type not in {
            event_type
            for rules in EVENT_RULES.values()
            for event_type in rules
        }:
            raise PayloadError("invalid_event_type")
        return store.recent(
            since=_parse_duration(args.since),
            limit=args.limit,
            site=args.site,
            event_type=args.event_type,
        )
    if args.command == "incidents":
        return store.incidents(
            since=_parse_duration(args.since),
            state=args.state,
            site=args.site,
        )
    if args.command == "explain":
        return store.explain(args.incident_uid)
    raise StateError("unknown_command")


def main(argv: Optional[Sequence[str]] = None) -> int:
    os.umask(0o077)
    parser = build_parser()
    try:
        args = parser.parse_args(argv)
        value = run_operator(args) if args.interface == "operator" else run_agent(args)
        _json_output(value)
        return 0
    except HomeEventError as exc:
        _json_output({"ok": False, "error": exc.code}, stream=sys.stderr)
        return 2
    except (OSError, sqlite3.Error):
        _json_output({"ok": False, "error": "internal_error"}, stream=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
