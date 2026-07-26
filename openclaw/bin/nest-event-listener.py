#!/usr/bin/env python3
"""Durable, privacy-safe Google Nest SDM Pub/Sub listener.

The deployed service deliberately supports only ``shadow`` mode.  It validates
and durably records camera event decisions, but it never captures an image or
sends a notification.  Runtime Pub/Sub imports are kept out of the parsing,
policy, and SQLite core so those pieces can be tested without Google packages.
"""

from __future__ import annotations

import argparse
import contextlib
import dataclasses
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import sqlite3
import stat
import sys
import threading
from typing import Any, Callable, Mapping, Sequence


SCHEMA_VERSION = 3
STATUS_SCHEMA_VERSION = 1
SERVICE_NAME = "nest-event-listener"
RETENTION_DAYS = 30
STREAMING_MAX_MESSAGES = 4
STREAMING_MAX_BYTES = 2 * 1024 * 1024
MAX_CONFIG_BYTES = 64 * 1024
MAX_PAYLOAD_BYTES = 512 * 1024
DEFAULT_ROOT = Path("~/.openclaw/nest-events").expanduser()
DEFAULT_CONFIG = DEFAULT_ROOT / "config" / "cameras.json"
DEFAULT_STATE_DIR = DEFAULT_ROOT / "state"
DB_FILENAME = "events.sqlite3"
STATUS_FILENAME = "status.json"

MOTION_EVENT = "sdm.devices.events.CameraMotion.Motion"
PERSON_EVENT = "sdm.devices.events.CameraPerson.Person"
SOUND_EVENT = "sdm.devices.events.CameraSound.Sound"
CHIME_EVENT = "sdm.devices.events.DoorbellChime.Chime"
CLIP_PREVIEW_EVENT = "sdm.devices.events.CameraClipPreview.ClipPreview"
EVENT_TYPES = {
    MOTION_EVENT: "motion",
    PERSON_EVENT: "person",
}
SAFE_EVENT_KINDS = {
    MOTION_EVENT: "camera_motion",
    PERSON_EVENT: "camera_person",
    SOUND_EVENT: "camera_sound",
    CHIME_EVENT: "doorbell_chime",
    CLIP_PREVIEW_EVENT: "camera_clip_preview",
}
SAFE_TRAIT_KINDS = {
    "sdm.devices.traits.CameraClipPreview": "camera_clip_preview_trait",
    "sdm.devices.traits.CameraLiveStream": "camera_live_stream_trait",
    "sdm.devices.traits.CameraMotion": "camera_motion_trait",
    "sdm.devices.traits.CameraPerson": "camera_person_trait",
    "sdm.devices.traits.CameraSound": "camera_sound_trait",
    "sdm.devices.traits.Connectivity": "connectivity_trait",
    "sdm.devices.traits.DoorbellChime": "doorbell_chime_trait",
    "sdm.devices.traits.Fan": "fan_trait",
    "sdm.devices.traits.Humidity": "humidity_trait",
    "sdm.devices.traits.Info": "info_trait",
    "sdm.devices.traits.Temperature": "temperature_trait",
    "sdm.devices.traits.ThermostatEco": "thermostat_eco_trait",
    "sdm.devices.traits.ThermostatHvac": "thermostat_hvac_trait",
    "sdm.devices.traits.ThermostatMode": "thermostat_mode_trait",
    "sdm.devices.traits.ThermostatTemperatureSetpoint": "thermostat_setpoint_trait",
}

# This exact policy is intentional.  It prevents a typo in a protected runtime
# config from silently applying the wrong capture capability or location.
REQUIRED_CAMERA_POLICIES = {
    "Kitchen": ("Cabin", "live"),
    "Living Room": ("Crosstown", "event"),
    "Living Room Wired": ("Crosstown", "event"),
}

RESOURCE_RE = re.compile(r"^enterprises/[^/\s]+/devices/[^/\s]+$")
FULL_SUBSCRIPTION_RE = re.compile(
    r"^projects/(?P<project>[^/\s]+)/subscriptions/(?P<name>[^/\s]+)$"
)
SHORT_SUBSCRIPTION_RE = re.compile(r"^[A-Za-z][A-Za-z0-9._~+%-]{2,254}$")
CONTROL_RE = re.compile(r"[\x00-\x1f\x7f]")


class ConfigError(Exception):
    """A safe-to-report configuration error."""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


class PayloadError(Exception):
    """A permanent payload error that should be tombstoned and acknowledged."""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


class RuntimeDependencyError(Exception):
    """A safe-to-report missing or unusable runtime dependency."""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


@dataclasses.dataclass(frozen=True)
class CameraPolicy:
    alias: str
    site: str
    resource: str
    capture: str


@dataclasses.dataclass(frozen=True)
class Settings:
    mode: str
    subscription: str
    config_path: Path
    state_dir: Path
    cameras: tuple[CameraPolicy, ...]

    @property
    def cameras_by_resource(self) -> dict[str, CameraPolicy]:
        return {camera.resource: camera for camera in self.cameras}

    @property
    def subscription_name(self) -> str:
        match = FULL_SUBSCRIPTION_RE.fullmatch(self.subscription)
        return match.group("name") if match else self.subscription


@dataclasses.dataclass(frozen=True)
class NormalizedCameraEvent:
    event_type: str


@dataclasses.dataclass(frozen=True)
class NormalizedEnvelope:
    event_id: str
    occurred_at: str
    update_kind: str
    resource: str | None
    thread_id: str | None
    thread_state: str | None
    camera_events: tuple[NormalizedCameraEvent, ...]
    event_kinds: tuple[str, ...]


@dataclasses.dataclass(frozen=True)
class PolicyDecision:
    policy: CameraPolicy | None
    events: tuple[NormalizedCameraEvent, ...]
    outcome: str


@dataclasses.dataclass(frozen=True)
class ProcessResult:
    outcome: str
    accepted_events: int
    duplicate_events: int
    alias: str | None = None
    site: str | None = None
    reason_code: str | None = None
    event_kinds: tuple[str, ...] = ()


def utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds").replace(
        "+00:00", "Z"
    )


def normalize_timestamp(value: Any, code: str = "invalid_timestamp") -> str:
    if not isinstance(value, str) or not value or len(value) > 64:
        raise PayloadError(code)
    candidate = value[:-1] + "+00:00" if value.endswith("Z") else value
    try:
        parsed = dt.datetime.fromisoformat(candidate)
    except ValueError as exc:
        raise PayloadError(code) from exc
    if parsed.tzinfo is None:
        raise PayloadError(code)
    parsed = parsed.astimezone(dt.timezone.utc)
    return parsed.isoformat(timespec="microseconds").replace("+00:00", "Z")


def normalize_optional_publish_time(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, dt.datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=dt.timezone.utc)
        return value.astimezone(dt.timezone.utc).isoformat(
            timespec="microseconds"
        ).replace("+00:00", "Z")
    to_datetime = getattr(value, "ToDatetime", None)
    if callable(to_datetime):
        try:
            return normalize_optional_publish_time(to_datetime())
        except Exception:
            return None
    if isinstance(value, str):
        try:
            return normalize_timestamp(value, "invalid_publish_timestamp")
        except PayloadError:
            return None
    return None


def retention_cutoff(now: str) -> str:
    normalized = normalize_timestamp(now, "invalid_clock")
    parsed = dt.datetime.fromisoformat(normalized.replace("Z", "+00:00"))
    return (parsed - dt.timedelta(days=RETENTION_DAYS)).isoformat(
        timespec="microseconds"
    ).replace("+00:00", "Z")


def _bounded_identifier(value: Any, code: str, *, maximum: int = 4096) -> str:
    if (
        not isinstance(value, str)
        or not value
        or len(value) > maximum
        or CONTROL_RE.search(value)
    ):
        raise PayloadError(code)
    return value


def opaque_key(namespace: str, value: str) -> str:
    """Return a one-way durable key without retaining the source identifier."""

    material = f"{namespace}\0{value}".encode("utf-8")
    return hashlib.sha256(material).hexdigest()


def parse_sdm_payload(data: bytes) -> NormalizedEnvelope:
    """Validate and normalize one already-decoded Pub/Sub message body.

    Pub/Sub client libraries expose ``message.data`` after base64 decoding.
    User IDs, resource groups, inner camera IDs, and the raw document are not
    copied into the normalized representation.
    """

    if not isinstance(data, bytes) or not data or len(data) > MAX_PAYLOAD_BYTES:
        raise PayloadError("invalid_payload_size")
    try:
        payload = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise PayloadError("invalid_json") from exc
    if not isinstance(payload, dict):
        raise PayloadError("invalid_envelope")

    event_id = _bounded_identifier(payload.get("eventId"), "invalid_event_id")
    occurred_at = normalize_timestamp(payload.get("timestamp"))

    thread_id_value = payload.get("eventThreadId")
    thread_id = None
    if thread_id_value is not None:
        thread_id = _bounded_identifier(
            thread_id_value, "invalid_event_thread_id", maximum=1024
        )
    thread_state_value = payload.get("eventThreadState")
    thread_state = None
    if thread_state_value is not None:
        if thread_state_value not in {"STARTED", "UPDATED", "ENDED"}:
            raise PayloadError("invalid_event_thread_state")
        if thread_id is None:
            raise PayloadError("thread_state_without_thread")
        thread_state = thread_state_value

    has_resource = "resourceUpdate" in payload
    has_relation = "relationUpdate" in payload
    if has_resource == has_relation:
        raise PayloadError("invalid_update_shape")

    if has_relation:
        relation = payload.get("relationUpdate")
        if not isinstance(relation, dict):
            raise PayloadError("invalid_relation_update")
        relation_type = relation.get("type")
        if relation_type not in {"CREATED", "UPDATED", "DELETED"}:
            raise PayloadError("invalid_relation_type")
        # Deliberately do not retain subject/object resource IDs.
        return NormalizedEnvelope(
            event_id=event_id,
            occurred_at=occurred_at,
            update_kind="relation",
            resource=None,
            thread_id=thread_id,
            thread_state=thread_state,
            camera_events=(),
            event_kinds=(f"relation_{relation_type.lower()}",),
        )

    update = payload.get("resourceUpdate")
    if not isinstance(update, dict):
        raise PayloadError("invalid_resource_update")
    resource = _bounded_identifier(
        update.get("name"), "invalid_resource_name", maximum=1024
    )

    events_value = update.get("events")
    if events_value is None:
        # Trait snapshots and changes are valid SDM resource events but are not
        # camera detections for this service.
        traits_value = update.get("traits")
        if isinstance(traits_value, dict) and traits_value:
            event_kinds = tuple(
                sorted(
                    {
                        SAFE_TRAIT_KINDS.get(trait_name, "other_trait")
                        for trait_name in traits_value
                        if isinstance(trait_name, str)
                    }
                )
            )
        else:
            event_kinds = ("trait_update",)
        return NormalizedEnvelope(
            event_id=event_id,
            occurred_at=occurred_at,
            update_kind="traits",
            resource=resource,
            thread_id=thread_id,
            thread_state=thread_state,
            camera_events=(),
            event_kinds=event_kinds or ("other_trait",),
        )
    if not isinstance(events_value, dict) or not events_value:
        raise PayloadError("invalid_events")

    camera_events: list[NormalizedCameraEvent] = []
    event_kinds: set[str] = set()
    for event_name, details in events_value.items():
        if not isinstance(event_name, str) or not isinstance(details, dict):
            raise PayloadError("invalid_event_entry")
        event_kinds.add(SAFE_EVENT_KINDS.get(event_name, "other_event"))
        normalized_type = EVENT_TYPES.get(event_name)
        if normalized_type is None:
            continue
        # These identifiers are device/event dependent.  Validate them when
        # present but never persist them.  A future active mode must consume an
        # image event ID in memory before its short deadline rather than place
        # it in the durable queue.
        if details.get("eventId") is not None:
            _bounded_identifier(
                details.get("eventId"), "invalid_camera_event_id", maximum=4096
            )
        if details.get("eventSessionId") is not None:
            _bounded_identifier(
                details.get("eventSessionId"),
                "invalid_camera_event_session_id",
                maximum=4096,
            )
        camera_events.append(NormalizedCameraEvent(event_type=normalized_type))

    return NormalizedEnvelope(
        event_id=event_id,
        occurred_at=occurred_at,
        update_kind="events",
        resource=resource,
        thread_id=thread_id,
        thread_state=thread_state,
        camera_events=tuple(camera_events),
        event_kinds=tuple(sorted(event_kinds)),
    )


def apply_policy(
    envelope: NormalizedEnvelope,
    cameras_by_resource: Mapping[str, CameraPolicy],
) -> PolicyDecision:
    """Apply the exact resource allowlist without retaining unknown resources."""

    if envelope.update_kind == "relation":
        return PolicyDecision(None, (), "ignored_relation")
    assert envelope.resource is not None
    policy = cameras_by_resource.get(envelope.resource)
    if policy is None:
        return PolicyDecision(None, (), "ignored_resource")
    if not envelope.camera_events:
        return PolicyDecision(policy, (), "ignored_event_type")
    return PolicyDecision(policy, envelope.camera_events, "accepted")


def ignored_resource_reason(
    envelope: NormalizedEnvelope,
    cameras_by_resource: Mapping[str, CameraPolicy],
) -> str | None:
    """Classify an unbound supported camera event without retaining its resource."""

    if (
        envelope.update_kind != "events"
        or envelope.resource is None
        or not envelope.camera_events
    ):
        return None
    event_enterprise = envelope.resource.rsplit("/devices/", 1)[0]
    configured_enterprises = {
        resource.rsplit("/devices/", 1)[0] for resource in cameras_by_resource
    }
    if event_enterprise in configured_enterprises:
        return "unbound_camera_same_enterprise"
    return "unbound_camera_other_enterprise"


def _assert_private_directory(path: Path, *, create: bool) -> None:
    if create:
        try:
            path.mkdir(mode=0o700, parents=True, exist_ok=True)
        except OSError as exc:
            raise ConfigError("state_directory_unavailable") from exc
    try:
        metadata = path.lstat()
    except OSError as exc:
        raise ConfigError("private_directory_missing") from exc
    if not stat.S_ISDIR(metadata.st_mode) or stat.S_ISLNK(metadata.st_mode):
        raise ConfigError("private_directory_invalid")
    if metadata.st_uid != os.geteuid() or stat.S_IMODE(metadata.st_mode) & 0o077:
        raise ConfigError("private_directory_permissions")


def _read_private_config(path: Path) -> Mapping[str, Any]:
    _assert_private_directory(path.parent, create=False)
    flags = os.O_RDONLY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise ConfigError("config_unavailable") from exc
    try:
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode):
            raise ConfigError("config_not_regular")
        if metadata.st_uid != os.geteuid() or stat.S_IMODE(metadata.st_mode) & 0o077:
            raise ConfigError("config_permissions")
        if metadata.st_size <= 0 or metadata.st_size > MAX_CONFIG_BYTES:
            raise ConfigError("config_size")
        chunks: list[bytes] = []
        remaining = MAX_CONFIG_BYTES + 1
        while remaining:
            chunk = os.read(descriptor, min(65536, remaining))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        raw = b"".join(chunks)
    finally:
        os.close(descriptor)
    if len(raw) > MAX_CONFIG_BYTES:
        raise ConfigError("config_size")
    try:
        parsed = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ConfigError("config_json") from exc
    if not isinstance(parsed, dict):
        raise ConfigError("config_shape")
    return parsed


def parse_camera_config(config: Mapping[str, Any]) -> tuple[CameraPolicy, ...]:
    if set(config) != {"version", "cameras"} or config.get("version") != 1:
        raise ConfigError("config_schema")
    cameras_value = config.get("cameras")
    if not isinstance(cameras_value, list) or len(cameras_value) != 3:
        raise ConfigError("camera_count")

    cameras: list[CameraPolicy] = []
    aliases: set[str] = set()
    resources: set[str] = set()
    for item in cameras_value:
        if not isinstance(item, dict) or set(item) != {
            "alias",
            "site",
            "resource",
            "capture",
        }:
            raise ConfigError("camera_schema")
        alias = item.get("alias")
        site = item.get("site")
        resource = item.get("resource")
        capture = item.get("capture")
        if not all(isinstance(value, str) for value in (alias, site, resource, capture)):
            raise ConfigError("camera_value_type")
        expected = REQUIRED_CAMERA_POLICIES.get(alias)
        if expected is None or (site, capture) != expected:
            raise ConfigError("camera_policy")
        if not RESOURCE_RE.fullmatch(resource):
            raise ConfigError("camera_resource")
        if alias in aliases or resource in resources:
            raise ConfigError("camera_duplicate")
        aliases.add(alias)
        resources.add(resource)
        cameras.append(CameraPolicy(alias, site, resource, capture))

    if aliases != set(REQUIRED_CAMERA_POLICIES):
        raise ConfigError("camera_aliases")
    cameras.sort(key=lambda camera: tuple(REQUIRED_CAMERA_POLICIES).index(camera.alias))
    return tuple(cameras)


def validate_subscription(value: str) -> str:
    if FULL_SUBSCRIPTION_RE.fullmatch(value) or SHORT_SUBSCRIPTION_RE.fullmatch(value):
        return value
    raise ConfigError("subscription_invalid")


def load_settings(environ: Mapping[str, str] | None = None) -> Settings:
    env = os.environ if environ is None else environ
    mode = env.get("NEST_EVENT_MODE", "shadow").strip().lower()
    if mode != "shadow":
        raise ConfigError("mode_not_supported")
    subscription_value = env.get("NEST_EVENT_SUBSCRIPTION", "").strip()
    if not subscription_value:
        raise ConfigError("subscription_missing")
    subscription = validate_subscription(subscription_value)
    config_path = Path(
        env.get("NEST_EVENT_CONFIG", str(DEFAULT_CONFIG))
    ).expanduser()
    state_dir = Path(
        env.get("NEST_EVENT_STATE_DIR", str(DEFAULT_STATE_DIR))
    ).expanduser()
    if not config_path.is_absolute() or not state_dir.is_absolute():
        raise ConfigError("path_not_absolute")
    cameras = parse_camera_config(_read_private_config(config_path))
    return Settings(mode, subscription, config_path, state_dir, cameras)


class StateStore:
    """SQLite-backed inbox, normalized event ledger, outbox, and status."""

    def __init__(self, settings: Settings, clock: Callable[[], str] = utc_now):
        self.settings = settings
        self.clock = clock
        self.state_dir = settings.state_dir
        self.db_path = self.state_dir / DB_FILENAME
        self.status_path = self.state_dir / STATUS_FILENAME
        _assert_private_directory(self.state_dir, create=True)
        self._prepare_database_file()
        self._initialize()

    def _prepare_database_file(self) -> None:
        flags = os.O_RDWR | os.O_CREAT
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        try:
            descriptor = os.open(self.db_path, flags, 0o600)
        except OSError as exc:
            raise ConfigError("database_unavailable") from exc
        try:
            metadata = os.fstat(descriptor)
            if not stat.S_ISREG(metadata.st_mode):
                raise ConfigError("database_not_regular")
            if metadata.st_uid != os.geteuid() or stat.S_IMODE(metadata.st_mode) & 0o077:
                raise ConfigError("database_permissions")
            os.fchmod(descriptor, 0o600)
        finally:
            os.close(descriptor)

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.db_path, timeout=15)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 15000")
        connection.execute("PRAGMA synchronous = FULL")
        return connection

    def _now(self) -> str:
        return normalize_timestamp(self.clock(), "invalid_clock")

    def _initialize(self) -> None:
        schema = """
        CREATE TABLE IF NOT EXISTS schema_meta (
            version INTEGER NOT NULL
        );
        CREATE TABLE IF NOT EXISTS inbox (
            id INTEGER PRIMARY KEY,
            message_key TEXT NOT NULL UNIQUE,
            received_at TEXT NOT NULL,
            publish_at TEXT,
            sdm_event_key TEXT,
            thread_key TEXT,
            event_at TEXT,
            alias TEXT,
            site TEXT,
            outcome TEXT NOT NULL,
            reason_code TEXT,
            event_kinds TEXT,
            normalized_event_count INTEGER NOT NULL DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS sdm_dedupe (
            event_key TEXT PRIMARY KEY,
            first_inbox_id INTEGER NOT NULL REFERENCES inbox(id),
            first_seen_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS event_records (
            id INTEGER PRIMARY KEY,
            inbox_id INTEGER NOT NULL REFERENCES inbox(id),
            dedupe_key TEXT NOT NULL UNIQUE,
            event_key TEXT NOT NULL,
            thread_key TEXT,
            thread_state TEXT,
            alias TEXT NOT NULL,
            site TEXT NOT NULL,
            event_type TEXT NOT NULL,
            first_occurred_at TEXT NOT NULL,
            last_occurred_at TEXT NOT NULL,
            capture_strategy TEXT NOT NULL,
            update_count INTEGER NOT NULL DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS outbox (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            event_record_id INTEGER NOT NULL UNIQUE REFERENCES event_records(id),
            alias TEXT NOT NULL,
            site TEXT NOT NULL,
            event_type TEXT NOT NULL,
            event_at TEXT NOT NULL,
            capture_strategy TEXT NOT NULL,
            status TEXT NOT NULL CHECK(status IN ('shadowed', 'pending', 'sent', 'failed')),
            created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS service_counters (
            name TEXT PRIMARY KEY,
            value INTEGER NOT NULL
        );
        CREATE TABLE IF NOT EXISTS runtime_status (
            singleton INTEGER PRIMARY KEY CHECK(singleton = 1),
            mode TEXT NOT NULL,
            health TEXT NOT NULL,
            started_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            last_message_at TEXT,
            last_accepted_event_at TEXT,
            last_error_at TEXT,
            last_error_code TEXT
        );
        CREATE INDEX IF NOT EXISTS inbox_received_at_idx
            ON inbox(received_at);
        CREATE INDEX IF NOT EXISTS event_records_inbox_id_idx
            ON event_records(inbox_id);
        CREATE INDEX IF NOT EXISTS sdm_dedupe_inbox_id_idx
            ON sdm_dedupe(first_inbox_id);
        """
        now = self._now()
        connection = self._connect()
        try:
            connection.executescript(schema)
            versions = connection.execute("SELECT version FROM schema_meta").fetchall()
            if not versions:
                connection.execute(
                    "INSERT INTO schema_meta(version) VALUES (?)", (SCHEMA_VERSION,)
                )
            elif len(versions) != 1:
                raise ConfigError("database_schema")
            else:
                version = versions[0]["version"]
                if version == 1:
                    self._migrate_v1_to_v2(connection)
                    version = 2
                if version == 2:
                    self._migrate_v2_to_v3(connection)
                    version = 3
                if version != SCHEMA_VERSION:
                    raise ConfigError("database_schema")
            connection.execute(
                """
                INSERT INTO runtime_status(
                    singleton, mode, health, started_at, updated_at
                ) VALUES (1, ?, 'starting', ?, ?)
                ON CONFLICT(singleton) DO UPDATE SET
                    mode = excluded.mode,
                    health = 'starting',
                    started_at = excluded.started_at,
                    updated_at = excluded.updated_at,
                    last_error_at = NULL,
                    last_error_code = NULL
                """,
                (self.settings.mode, now, now),
            )
            self._prune(connection, now)
            connection.commit()
        finally:
            connection.close()
        self._write_status_best_effort()

    @staticmethod
    def _migrate_v1_to_v2(connection: sqlite3.Connection) -> None:
        """Make outbox IDs durable high-water marks across full pruning.

        The home-event bridge consumes the listener outbox by increasing ID.
        SQLite may reuse low row IDs after an ordinary INTEGER PRIMARY KEY
        table becomes empty, so rebuild only that table with AUTOINCREMENT.
        Existing IDs and foreign-key relationships remain unchanged. The
        lifetime accepted-event counter seeds the sequence when legacy
        retention has already removed the previous highest row.
        """

        connection.execute("PRAGMA foreign_keys = OFF")
        try:
            connection.execute("BEGIN IMMEDIATE")
            maximum_row = connection.execute(
                "SELECT COALESCE(MAX(id), 0) FROM outbox"
            ).fetchone()
            accepted_row = connection.execute(
                "SELECT value FROM service_counters WHERE name = 'accepted_events'"
            ).fetchone()
            sequence_floor = maximum_row[0] if maximum_row is not None else 0
            accepted_events = accepted_row[0] if accepted_row is not None else 0
            if any(
                not isinstance(value, int) or isinstance(value, bool) or value < 0
                for value in (sequence_floor, accepted_events)
            ):
                raise ConfigError("database_migration_integrity")
            sequence_floor = max(sequence_floor, accepted_events)
            connection.execute(
                """
                CREATE TABLE outbox_v2 (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    event_record_id INTEGER NOT NULL UNIQUE REFERENCES event_records(id),
                    alias TEXT NOT NULL,
                    site TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    event_at TEXT NOT NULL,
                    capture_strategy TEXT NOT NULL,
                    status TEXT NOT NULL CHECK(status IN ('shadowed', 'pending', 'sent', 'failed')),
                    created_at TEXT NOT NULL
                )
                """
            )
            connection.execute(
                """
                INSERT INTO outbox_v2(
                    id, event_record_id, alias, site, event_type, event_at,
                    capture_strategy, status, created_at
                )
                SELECT id, event_record_id, alias, site, event_type, event_at,
                       capture_strategy, status, created_at
                FROM outbox
                ORDER BY id
                """
            )
            connection.execute("DROP TABLE outbox")
            connection.execute("ALTER TABLE outbox_v2 RENAME TO outbox")
            connection.execute("DELETE FROM sqlite_sequence WHERE name = 'outbox'")
            if sequence_floor:
                connection.execute(
                    "INSERT INTO sqlite_sequence(name, seq) VALUES ('outbox', ?)",
                    (sequence_floor,),
                )
            connection.execute(
                "UPDATE schema_meta SET version = ? WHERE version = 1",
                (2,),
            )
            if connection.execute("PRAGMA foreign_key_check").fetchone() is not None:
                raise ConfigError("database_migration_integrity")
            integrity = connection.execute("PRAGMA quick_check").fetchone()
            if integrity is None or integrity[0] != "ok":
                raise ConfigError("database_migration_integrity")
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.execute("PRAGMA foreign_keys = ON")
        if connection.execute("PRAGMA foreign_keys").fetchone()[0] != 1:
            raise ConfigError("database_foreign_keys")

    @staticmethod
    def _migrate_v2_to_v3(connection: sqlite3.Connection) -> None:
        """Add bounded event-kind telemetry without backfilling old payloads."""

        try:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute("ALTER TABLE inbox ADD COLUMN event_kinds TEXT")
            connection.execute(
                "UPDATE schema_meta SET version = 3 WHERE version = 2"
            )
            integrity = connection.execute("PRAGMA quick_check").fetchone()
            if integrity is None or integrity[0] != "ok":
                raise ConfigError("database_migration_integrity")
            connection.commit()
        except Exception:
            connection.rollback()
            raise

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
    def _prune(connection: sqlite3.Connection, now: str) -> None:
        """Remove protected event metadata older than the fixed retention window."""

        cutoff = retention_cutoff(now)
        # Foreign keys intentionally do not cascade.  Keep deletion order
        # explicit so a schema change cannot silently broaden retention work.
        connection.execute(
            """
            DELETE FROM outbox
            WHERE event_record_id IN (
                SELECT event_records.id
                FROM event_records
                JOIN inbox ON inbox.id = event_records.inbox_id
                WHERE inbox.received_at < ?
            )
            """,
            (cutoff,),
        )
        connection.execute(
            """
            DELETE FROM event_records
            WHERE inbox_id IN (
                SELECT id FROM inbox WHERE received_at < ?
            )
            """,
            (cutoff,),
        )
        connection.execute(
            """
            DELETE FROM sdm_dedupe
            WHERE first_inbox_id IN (
                SELECT id FROM inbox WHERE received_at < ?
            )
            """,
            (cutoff,),
        )
        connection.execute("DELETE FROM inbox WHERE received_at < ?", (cutoff,))

    def record_delivery(
        self,
        data: bytes,
        message_id: str,
        publish_time: Any = None,
    ) -> ProcessResult:
        """Commit a delivery and deterministic outbox decisions in one transaction."""

        if (
            not isinstance(message_id, str)
            or not message_id
            or len(message_id) > 1024
            or CONTROL_RE.search(message_id)
        ):
            raise PayloadError("invalid_message_id")
        message_key = opaque_key("pubsub-message", message_id)
        received_at = self._now()
        publish_at = normalize_optional_publish_time(publish_time)

        try:
            envelope = parse_sdm_payload(data)
            payload_error = None
        except PayloadError as exc:
            envelope = None
            payload_error = exc.code

        with contextlib.closing(self._connect()) as connection, connection:
            connection.execute("BEGIN IMMEDIATE")
            self._prune(connection, received_at)
            self._increment(connection, "deliveries_total")
            existing = connection.execute(
                "SELECT id FROM inbox WHERE message_key = ?", (message_key,)
            ).fetchone()
            if existing is not None:
                self._increment(connection, "duplicate_messages")
                self._touch_status(connection, received_at, health="ok")
                connection.commit()
                result = ProcessResult("duplicate_message", 0, 0)
            elif payload_error is not None:
                connection.execute(
                    """
                    INSERT INTO inbox(
                        message_key, received_at, publish_at, outcome, reason_code
                    ) VALUES (?, ?, ?, 'invalid', ?)
                    """,
                    (message_key, received_at, publish_at, payload_error),
                )
                self._increment(connection, "invalid_messages")
                self._touch_status(
                    connection,
                    received_at,
                    health="ok",
                    error_code=payload_error,
                )
                connection.commit()
                result = ProcessResult("invalid", 0, 0)
            else:
                assert envelope is not None
                result = self._record_valid_envelope(
                    connection,
                    envelope,
                    message_key,
                    received_at,
                    publish_at,
                )
                connection.commit()

        # The SQLite transaction above is the ack boundary.  The status JSON is
        # a protected operational projection of the same durable database.
        self._write_status_best_effort()
        return result

    def _record_valid_envelope(
        self,
        connection: sqlite3.Connection,
        envelope: NormalizedEnvelope,
        message_key: str,
        received_at: str,
        publish_at: str | None,
    ) -> ProcessResult:
        event_key = opaque_key("sdm-event", envelope.event_id)
        thread_key = (
            opaque_key("sdm-thread", envelope.thread_id)
            if envelope.thread_id is not None
            else None
        )
        cursor = connection.execute(
            """
            INSERT INTO inbox(
                message_key, received_at, publish_at, sdm_event_key,
                thread_key, event_at, outcome, event_kinds
            ) VALUES (?, ?, ?, ?, ?, ?, 'processing', ?)
            """,
            (
                message_key,
                received_at,
                publish_at,
                event_key,
                thread_key,
                envelope.occurred_at,
                json.dumps(
                    list(envelope.event_kinds),
                    separators=(",", ":"),
                    sort_keys=True,
                ),
            ),
        )
        inbox_id = int(cursor.lastrowid)
        dedupe_insert = connection.execute(
            """
            INSERT OR IGNORE INTO sdm_dedupe(event_key, first_inbox_id, first_seen_at)
            VALUES (?, ?, ?)
            """,
            (event_key, inbox_id, received_at),
        )
        if dedupe_insert.rowcount == 0:
            connection.execute(
                "UPDATE inbox SET outcome = 'duplicate_event' WHERE id = ?",
                (inbox_id,),
            )
            self._increment(connection, "duplicate_sdm_events")
            self._touch_status(connection, received_at, health="ok")
            return ProcessResult(
                "duplicate_event", 0, 0, event_kinds=envelope.event_kinds
            )

        decision = apply_policy(envelope, self.settings.cameras_by_resource)
        if decision.policy is None or not decision.events:
            reason_code = (
                ignored_resource_reason(
                    envelope, self.settings.cameras_by_resource
                )
                if decision.outcome == "ignored_resource"
                else None
            )
            connection.execute(
                "UPDATE inbox SET outcome = ?, reason_code = ? WHERE id = ?",
                (decision.outcome, reason_code, inbox_id),
            )
            self._increment(connection, "ignored_messages")
            self._touch_status(connection, received_at, health="ok")
            return ProcessResult(
                decision.outcome,
                0,
                0,
                reason_code=reason_code,
                event_kinds=envelope.event_kinds,
            )

        policy = decision.policy
        accepted = 0
        duplicates = 0
        for camera_event in decision.events:
            if thread_key:
                dedupe_source = (
                    f"thread\0{policy.alias}\0{camera_event.event_type}\0"
                    f"{envelope.thread_id}"
                )
            else:
                dedupe_source = (
                    f"event\0{policy.alias}\0{camera_event.event_type}\0"
                    f"{envelope.event_id}"
                )
            record_key = opaque_key("normalized-camera-event", dedupe_source)
            existing = connection.execute(
                "SELECT id FROM event_records WHERE dedupe_key = ?", (record_key,)
            ).fetchone()
            if existing is not None:
                connection.execute(
                    """
                    UPDATE event_records SET
                        thread_state = CASE
                            WHEN ? >= last_occurred_at THEN ?
                            ELSE thread_state
                        END,
                        last_occurred_at = MAX(last_occurred_at, ?),
                        update_count = update_count + 1
                    WHERE id = ?
                    """,
                    (
                        envelope.occurred_at,
                        envelope.thread_state,
                        envelope.occurred_at,
                        existing["id"],
                    ),
                )
                duplicates += 1
                continue

            event_cursor = connection.execute(
                """
                INSERT INTO event_records(
                    inbox_id, dedupe_key, event_key, thread_key, thread_state,
                    alias, site, event_type, first_occurred_at,
                    last_occurred_at, capture_strategy
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    inbox_id,
                    record_key,
                    event_key,
                    thread_key,
                    envelope.thread_state,
                    policy.alias,
                    policy.site,
                    camera_event.event_type,
                    envelope.occurred_at,
                    envelope.occurred_at,
                    policy.capture,
                ),
            )
            connection.execute(
                """
                INSERT INTO outbox(
                    event_record_id, alias, site, event_type, event_at,
                    capture_strategy, status, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, 'shadowed', ?)
                """,
                (
                    event_cursor.lastrowid,
                    policy.alias,
                    policy.site,
                    camera_event.event_type,
                    envelope.occurred_at,
                    policy.capture,
                    received_at,
                ),
            )
            accepted += 1

        outcome = "accepted" if accepted else "duplicate_thread"
        connection.execute(
            """
            UPDATE inbox SET
                alias = ?, site = ?, outcome = ?, normalized_event_count = ?
            WHERE id = ?
            """,
            (policy.alias, policy.site, outcome, accepted, inbox_id),
        )
        if accepted:
            self._increment(connection, "accepted_messages")
            self._increment(connection, "accepted_events", accepted)
        if duplicates:
            self._increment(connection, "duplicate_thread_events", duplicates)
        self._touch_status(
            connection,
            received_at,
            health="ok",
            accepted_at=envelope.occurred_at if accepted else None,
        )
        return ProcessResult(
            outcome,
            accepted,
            duplicates,
            policy.alias,
            policy.site,
            event_kinds=envelope.event_kinds,
        )

    @staticmethod
    def _touch_status(
        connection: sqlite3.Connection,
        now: str,
        *,
        health: str,
        accepted_at: str | None = None,
        error_code: str | None = None,
    ) -> None:
        connection.execute(
            """
            UPDATE runtime_status SET
                health = ?, updated_at = ?, last_message_at = ?,
                last_accepted_event_at = COALESCE(?, last_accepted_event_at),
                last_error_at = CASE WHEN ? IS NULL THEN last_error_at ELSE ? END,
                last_error_code = COALESCE(?, last_error_code)
            WHERE singleton = 1
            """,
            (health, now, now, accepted_at, error_code, now, error_code),
        )

    def mark_runtime_error(self, code: str) -> None:
        if not re.fullmatch(r"[a-z][a-z0-9_]{1,63}", code):
            code = "runtime_error"
        now = self._now()
        with contextlib.closing(self._connect()) as connection, connection:
            connection.execute(
                """
                UPDATE runtime_status SET
                    health = 'degraded', updated_at = ?,
                    last_error_at = ?, last_error_code = ?
                WHERE singleton = 1
                """,
                (now, now, code),
            )
        self._write_status_best_effort()

    def mark_running(self) -> None:
        now = self._now()
        with contextlib.closing(self._connect()) as connection, connection:
            connection.execute(
                """
                UPDATE runtime_status SET health = 'ok', updated_at = ?
                WHERE singleton = 1
                """,
                (now,),
            )
        self._write_status_best_effort()

    def status_snapshot(self) -> dict[str, Any]:
        with contextlib.closing(self._connect()) as connection, connection:
            status = connection.execute(
                "SELECT * FROM runtime_status WHERE singleton = 1"
            ).fetchone()
            counters = {
                row["name"]: row["value"]
                for row in connection.execute(
                    "SELECT name, value FROM service_counters"
                ).fetchall()
            }
            outbox = {
                row["status"]: row["count"]
                for row in connection.execute(
                    "SELECT status, COUNT(*) AS count FROM outbox GROUP BY status"
                ).fetchall()
            }
            camera_rows = connection.execute(
                """
                SELECT alias, site, COUNT(*) AS accepted_events,
                       MAX(last_occurred_at) AS last_event_at
                FROM event_records GROUP BY alias, site
                """
            ).fetchall()
        assert status is not None
        camera_stats = {
            row["alias"]: {
                "acceptedEvents": row["accepted_events"],
                "lastEventAt": row["last_event_at"],
            }
            for row in camera_rows
        }
        cameras = []
        for policy in self.settings.cameras:
            stats = camera_stats.get(
                policy.alias, {"acceptedEvents": 0, "lastEventAt": None}
            )
            cameras.append(
                {
                    "alias": policy.alias,
                    "site": policy.site,
                    "acceptedEvents": stats["acceptedEvents"],
                    "lastEventAt": stats["lastEventAt"],
                }
            )
        last_error = None
        if status["last_error_code"]:
            last_error = {
                "at": status["last_error_at"],
                "code": status["last_error_code"],
            }
        return {
            "schemaVersion": STATUS_SCHEMA_VERSION,
            "service": SERVICE_NAME,
            "mode": status["mode"],
            "health": status["health"],
            "subscriptionName": self.settings.subscription_name,
            "retentionDays": RETENTION_DAYS,
            "startedAt": status["started_at"],
            "updatedAt": status["updated_at"],
            "lastMessageAt": status["last_message_at"],
            "lastAcceptedEventAt": status["last_accepted_event_at"],
            "lastError": last_error,
            "counters": {
                "deliveries": counters.get("deliveries_total", 0),
                "acceptedMessages": counters.get("accepted_messages", 0),
                "acceptedEvents": counters.get("accepted_events", 0),
                "ignoredMessages": counters.get("ignored_messages", 0),
                "invalidMessages": counters.get("invalid_messages", 0),
                "duplicateMessages": counters.get("duplicate_messages", 0),
                "duplicateSdmEvents": counters.get("duplicate_sdm_events", 0),
                "duplicateThreadEvents": counters.get(
                    "duplicate_thread_events", 0
                ),
            },
            "outbox": {
                "pending": outbox.get("pending", 0),
                "shadowed": outbox.get("shadowed", 0),
                "sent": outbox.get("sent", 0),
                "failed": outbox.get("failed", 0),
            },
            "cameras": cameras,
        }

    def write_status(self) -> None:
        _atomic_write_json(self.status_path, self.status_snapshot())

    def _write_status_best_effort(self) -> bool:
        try:
            self.write_status()
        except Exception:
            emit_log(
                "error",
                "status_projection_failed",
                code="status_projection_failed",
            )
            return False
        return True


def _atomic_write_json(path: Path, value: Mapping[str, Any]) -> None:
    data = (json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n").encode(
        "utf-8"
    )
    temporary = path.parent / f".{path.name}.{secrets.token_hex(8)}.tmp"
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = os.open(temporary, flags, 0o600)
    try:
        view = memoryview(data)
        while view:
            written = os.write(descriptor, view)
            view = view[written:]
        os.fsync(descriptor)
        os.fchmod(descriptor, 0o600)
    except Exception:
        try:
            temporary.unlink()
        except OSError:
            pass
        raise
    finally:
        os.close(descriptor)
    os.replace(temporary, path)
    os.chmod(path, 0o600, follow_symlinks=False)
    try:
        directory_fd = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    except OSError:
        # Some filesystems do not support fsync on directories.  The file
        # itself has already been synced and atomically replaced.
        pass


class PubSubMessageProcessor:
    """Serialize callbacks and keep the ack boundary explicit."""

    def __init__(self, store: StateStore):
        self.store = store
        self._lock = threading.Lock()

    def __call__(self, message: Any) -> None:
        with self._lock:
            try:
                message_id = str(message.message_id)
                data = bytes(message.data)
                result = self.store.record_delivery(
                    data, message_id, getattr(message, "publish_time", None)
                )
            except PayloadError:
                # A missing/invalid Pub/Sub message ID is not a valid SDM
                # delivery to tombstone; retry rather than acknowledge it.
                self._safe_runtime_error("invalid_pubsub_message")
                message.nack()
                emit_log("error", "delivery_nacked", code="invalid_pubsub_message")
                return
            except Exception:
                self._safe_runtime_error("durable_commit_failed")
                message.nack()
                emit_log("error", "delivery_nacked", code="durable_commit_failed")
                return
            try:
                message.ack()
            except Exception:
                self._safe_runtime_error("ack_failed")
                emit_log("error", "ack_failed", code="ack_failed")
                return
            emit_log(
                "info",
                "delivery_committed",
                outcome=result.outcome,
                acceptedEvents=result.accepted_events,
                alias=result.alias,
                site=result.site,
                reasonCode=result.reason_code,
                eventKinds=result.event_kinds,
            )

    def _safe_runtime_error(self, code: str) -> None:
        try:
            self.store.mark_runtime_error(code)
        except Exception:
            pass


def emit_log(level: str, event: str, **fields: Any) -> None:
    safe_fields = {key: value for key, value in fields.items() if value is not None}
    record = {"level": level, "event": event, **safe_fields}
    print(json.dumps(record, sort_keys=True, separators=(",", ":")), flush=True)


def _build_flow_control(pubsub_v1: Any) -> Any:
    return pubsub_v1.types.FlowControl(
        max_messages=STREAMING_MAX_MESSAGES,
        max_bytes=STREAMING_MAX_BYTES,
    )


def _create_subscriber(settings: Settings) -> tuple[Any, str, Any]:
    """Import Google packages only at the live runtime boundary."""

    try:
        import google.auth  # type: ignore[import-not-found]
        from google.cloud import pubsub_v1  # type: ignore[import-not-found]
    except ImportError as exc:
        raise RuntimeDependencyError("google_pubsub_unavailable") from exc
    try:
        credentials, default_project = google.auth.default(
            scopes=["https://www.googleapis.com/auth/cloud-platform"]
        )
        subscriber = pubsub_v1.SubscriberClient(credentials=credentials)
        flow_control = _build_flow_control(pubsub_v1)
    except Exception as exc:
        raise RuntimeDependencyError("google_credentials_unavailable") from exc

    match = FULL_SUBSCRIPTION_RE.fullmatch(settings.subscription)
    if match:
        subscription_path = settings.subscription
    else:
        project = default_project or getattr(credentials, "project_id", None)
        if not isinstance(project, str) or not project:
            try:
                subscriber.close()
            except Exception:
                pass
            raise RuntimeDependencyError("google_project_unavailable")
        subscription_path = subscriber.subscription_path(
            project, settings.subscription
        )
    return subscriber, subscription_path, flow_control


def run_listener(settings: Settings, *, once: bool) -> int:
    store = StateStore(settings)
    processor = PubSubMessageProcessor(store)
    try:
        subscriber, subscription_path, flow_control = _create_subscriber(settings)
    except RuntimeDependencyError as exc:
        store.mark_runtime_error(exc.code)
        raise
    emit_log(
        "info",
        "listener_started",
        mode=settings.mode,
        subscriptionName=settings.subscription_name,
    )
    try:
        if once:
            store.mark_running()
            return _run_once(subscriber, subscription_path, processor, store)
        try:
            future = subscriber.subscribe(
                subscription_path,
                callback=processor,
                flow_control=flow_control,
            )
        except Exception:
            store.mark_runtime_error("subscriber_start_failed")
            emit_log(
                "error",
                "subscriber_start_failed",
                code="subscriber_start_failed",
            )
            return 1
        store.mark_running()
        try:
            future.result()
        except KeyboardInterrupt:
            future.cancel()
            try:
                future.result(timeout=10)
            except Exception:
                pass
            return 0
        except Exception:
            store.mark_runtime_error("subscriber_stopped")
            emit_log("error", "subscriber_stopped", code="subscriber_stopped")
            return 1
        return 0
    finally:
        try:
            subscriber.close()
        except Exception:
            pass


def _run_once(
    subscriber: Any,
    subscription_path: str,
    processor: PubSubMessageProcessor,
    store: StateStore,
) -> int:
    try:
        response = subscriber.pull(
            request={"subscription": subscription_path, "max_messages": 1},
            timeout=30,
        )
    except Exception:
        store.mark_runtime_error("pull_failed")
        emit_log("error", "pull_failed", code="pull_failed")
        return 1
    received = list(getattr(response, "received_messages", ()))
    if not received:
        emit_log("info", "pull_empty")
        return 0
    delivery = received[0]
    message = delivery.message
    try:
        result = store.record_delivery(
            bytes(message.data),
            str(message.message_id),
            getattr(message, "publish_time", None),
        )
    except Exception:
        try:
            subscriber.modify_ack_deadline(
                request={
                    "subscription": subscription_path,
                    "ack_ids": [delivery.ack_id],
                    "ack_deadline_seconds": 0,
                }
            )
        except Exception:
            pass
        store.mark_runtime_error("durable_commit_failed")
        emit_log("error", "delivery_nacked", code="durable_commit_failed")
        return 1
    try:
        subscriber.acknowledge(
            request={"subscription": subscription_path, "ack_ids": [delivery.ack_id]}
        )
    except Exception:
        store.mark_runtime_error("ack_failed")
        emit_log("error", "ack_failed", code="ack_failed")
        return 1
    emit_log(
        "info",
        "delivery_committed",
        outcome=result.outcome,
        acceptedEvents=result.accepted_events,
        alias=result.alias,
        site=result.site,
        reasonCode=result.reason_code,
        eventKinds=result.event_kinds,
    )
    return 0


def _safe_config_summary(settings: Settings) -> dict[str, Any]:
    return {
        "schemaVersion": 1,
        "valid": True,
        "mode": settings.mode,
        "subscriptionName": settings.subscription_name,
        "cameras": [
            {
                "alias": camera.alias,
                "site": camera.site,
                "capture": camera.capture,
            }
            for camera in settings.cameras
        ],
    }


def _read_status_command(state_dir: Path) -> int:
    try:
        _assert_private_directory(state_dir, create=False)
        status_path = state_dir / STATUS_FILENAME
        flags = os.O_RDONLY
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        descriptor = os.open(status_path, flags)
        try:
            metadata = os.fstat(descriptor)
            if (
                not stat.S_ISREG(metadata.st_mode)
                or metadata.st_uid != os.geteuid()
                or stat.S_IMODE(metadata.st_mode) & 0o077
                or metadata.st_size > MAX_CONFIG_BYTES
            ):
                raise ConfigError("status_permissions")
            raw = os.read(descriptor, MAX_CONFIG_BYTES + 1)
        finally:
            os.close(descriptor)
        status_value = json.loads(raw.decode("utf-8"))
        if not isinstance(status_value, dict):
            raise ConfigError("status_invalid")
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, ConfigError):
        print(
            json.dumps(
                {
                    "schemaVersion": STATUS_SCHEMA_VERSION,
                    "service": SERVICE_NAME,
                    "health": "not_initialized",
                },
                sort_keys=True,
                separators=(",", ":"),
            )
        )
        return 1
    print(json.dumps(status_value, sort_keys=True, separators=(",", ":")))
    return 0


def build_argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    run_parser = subparsers.add_parser("run", help="consume Pub/Sub deliveries")
    run_parser.add_argument(
        "--once", action="store_true", help="pull and process at most one delivery"
    )
    subparsers.add_parser("status", help="print protected operational status JSON")
    subparsers.add_parser(
        "check-config", help="validate config and print a redacted summary"
    )
    subparsers.add_parser(
        "migrate", help="initialize or migrate protected state without consuming events"
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    # The service creates only protected state.  Keep SQLite sidecars and any
    # dependency-created temporary files private as well.
    os.umask(0o077)
    args = build_argument_parser().parse_args(argv)
    if args.command == "status":
        state_dir = Path(
            os.environ.get("NEST_EVENT_STATE_DIR", str(DEFAULT_STATE_DIR))
        ).expanduser()
        if not state_dir.is_absolute():
            print(
                json.dumps(
                    {"level": "error", "event": "status_failed", "code": "path_not_absolute"},
                    sort_keys=True,
                    separators=(",", ":"),
                )
            )
            return 2
        return _read_status_command(state_dir)

    try:
        settings = load_settings()
    except ConfigError as exc:
        emit_log("error", "configuration_failed", code=exc.code)
        return 2
    if args.command == "check-config":
        print(
            json.dumps(
                _safe_config_summary(settings), sort_keys=True, separators=(",", ":")
            )
        )
        return 0
    try:
        if args.command == "migrate":
            StateStore(settings)
            print(
                json.dumps(
                    {
                        "schemaVersion": STATUS_SCHEMA_VERSION,
                        "service": SERVICE_NAME,
                        "status": "ready",
                        "databaseSchemaVersion": SCHEMA_VERSION,
                    },
                    sort_keys=True,
                    separators=(",", ":"),
                )
            )
            return 0
        return run_listener(settings, once=bool(args.once))
    except (ConfigError, RuntimeDependencyError) as exc:
        emit_log("error", "listener_failed", code=exc.code)
        return 2
    except Exception:
        emit_log("error", "listener_failed", code="runtime_error")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
