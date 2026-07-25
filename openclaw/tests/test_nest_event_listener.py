#!/usr/bin/env python3
"""Focused tests for the durable Nest SDM event listener core."""

from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import os
from pathlib import Path
import sqlite3
import sys
import tempfile
import types
import unittest
from unittest import mock


MODULE_PATH = Path(__file__).resolve().parents[1] / "bin" / "nest-event-listener.py"
SPEC = importlib.util.spec_from_file_location("nest_event_listener", MODULE_PATH)
assert SPEC and SPEC.loader
nest_events = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = nest_events
SPEC.loader.exec_module(nest_events)


CAMERA_RESOURCES = {
    "Kitchen": "enterprises/sdm-project/devices/kitchen-device-secret",
    "Living Room": "enterprises/sdm-project/devices/living-device-secret",
    "Living Room Wired": "enterprises/sdm-project/devices/laundry-device-secret",
}


V1_SCHEMA = """
CREATE TABLE schema_meta (
    version INTEGER NOT NULL
);
CREATE TABLE inbox (
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
    normalized_event_count INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE sdm_dedupe (
    event_key TEXT PRIMARY KEY,
    first_inbox_id INTEGER NOT NULL REFERENCES inbox(id),
    first_seen_at TEXT NOT NULL
);
CREATE TABLE event_records (
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
CREATE TABLE outbox (
    id INTEGER PRIMARY KEY,
    event_record_id INTEGER NOT NULL UNIQUE REFERENCES event_records(id),
    alias TEXT NOT NULL,
    site TEXT NOT NULL,
    event_type TEXT NOT NULL,
    event_at TEXT NOT NULL,
    capture_strategy TEXT NOT NULL,
    status TEXT NOT NULL CHECK(status IN ('shadowed', 'pending', 'sent', 'failed')),
    created_at TEXT NOT NULL
);
CREATE TABLE service_counters (
    name TEXT PRIMARY KEY,
    value INTEGER NOT NULL
);
CREATE TABLE runtime_status (
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
CREATE INDEX inbox_received_at_idx ON inbox(received_at);
CREATE INDEX event_records_inbox_id_idx ON event_records(inbox_id);
CREATE INDEX sdm_dedupe_inbox_id_idx ON sdm_dedupe(first_inbox_id);
"""


def camera_config() -> dict:
    return {
        "version": 1,
        "cameras": [
            {
                "alias": "Kitchen",
                "site": "Cabin",
                "resource": CAMERA_RESOURCES["Kitchen"],
                "capture": "live",
            },
            {
                "alias": "Living Room",
                "site": "Crosstown",
                "resource": CAMERA_RESOURCES["Living Room"],
                "capture": "event",
            },
            {
                "alias": "Living Room Wired",
                "site": "Crosstown",
                "resource": CAMERA_RESOURCES["Living Room Wired"],
                "capture": "event",
            },
        ],
    }


def event_payload(
    *,
    resource: str = CAMERA_RESOURCES["Kitchen"],
    top_event_id: str = "top-event-secret-1",
    timestamp: str = "2026-07-11T18:00:00Z",
    event_names: tuple[str, ...] = (nest_events.PERSON_EVENT,),
    details: dict | None = None,
    thread_id: str | None = None,
    thread_state: str | None = None,
) -> bytes:
    if details is None:
        details = {
            "eventId": "inner-image-event-secret",
            "eventSessionId": "inner-session-secret",
        }
    payload = {
        "eventId": top_event_id,
        "timestamp": timestamp,
        "resourceUpdate": {
            "name": resource,
            "events": {event_name: dict(details) for event_name in event_names},
        },
        "userId": "private-user-id",
        "resourceGroup": [resource],
    }
    if thread_id is not None:
        payload["eventThreadId"] = thread_id
    if thread_state is not None:
        payload["eventThreadState"] = thread_state
    return json.dumps(payload).encode("utf-8")


class NestEventTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name) / "nest-events"
        self.config_dir = self.root / "config"
        self.config_dir.mkdir(parents=True, mode=0o700)
        os.chmod(self.root, 0o700)
        os.chmod(self.config_dir, 0o700)
        self.config_path = self.config_dir / "cameras.json"
        self.config_path.write_text(json.dumps(camera_config()), encoding="utf-8")
        os.chmod(self.config_path, 0o600)
        self.state_dir = self.root / "state"
        self.environ = {
            "NEST_EVENT_MODE": "shadow",
            "NEST_EVENT_SUBSCRIPTION": "openclaw-nest-events",
            "NEST_EVENT_CONFIG": str(self.config_path),
            "NEST_EVENT_STATE_DIR": str(self.state_dir),
        }
        self.settings = nest_events.load_settings(self.environ)

    def store(self) -> "nest_events.StateStore":
        return nest_events.StateStore(
            self.settings, clock=lambda: "2026-07-11T19:00:00Z"
        )


class ConfigurationTests(NestEventTestCase):
    def test_exact_three_camera_policy_and_redacted_summary(self) -> None:
        policies = {
            camera.alias: (camera.site, camera.capture)
            for camera in self.settings.cameras
        }
        self.assertEqual(
            policies,
            {
                "Kitchen": ("Cabin", "live"),
                "Living Room": ("Crosstown", "event"),
                "Living Room Wired": ("Crosstown", "event"),
            },
        )
        summary = nest_events._safe_config_summary(self.settings)
        encoded = json.dumps(summary)
        self.assertNotIn("enterprises/", encoded)
        self.assertNotIn("sdm-project", encoded)

    def test_config_rejects_wrong_capability_site_or_alias_set(self) -> None:
        for mutation in ("capture", "site", "alias"):
            with self.subTest(mutation=mutation):
                config = camera_config()
                if mutation == "capture":
                    config["cameras"][0]["capture"] = "event"
                elif mutation == "site":
                    config["cameras"][1]["site"] = "Cabin"
                else:
                    config["cameras"][2]["alias"] = "Garage"
                with self.assertRaises(nest_events.ConfigError):
                    nest_events.parse_camera_config(config)

    def test_non_shadow_mode_and_insecure_config_fail_closed(self) -> None:
        active = dict(self.environ, NEST_EVENT_MODE="active")
        with self.assertRaisesRegex(nest_events.ConfigError, "mode_not_supported"):
            nest_events.load_settings(active)

        os.chmod(self.config_path, 0o644)
        with self.assertRaisesRegex(nest_events.ConfigError, "config_permissions"):
            nest_events.load_settings(self.environ)

    def test_short_and_full_subscription_names_are_supported(self) -> None:
        self.assertEqual(
            nest_events.validate_subscription("openclaw-nest-events"),
            "openclaw-nest-events",
        )
        full = "projects/cloud-project/subscriptions/openclaw-nest-events"
        self.assertEqual(nest_events.validate_subscription(full), full)
        with self.assertRaises(nest_events.ConfigError):
            nest_events.validate_subscription("../not-a-subscription")

    def test_migrate_command_upgrades_state_without_starting_pubsub(self) -> None:
        output = io.StringIO()
        with (
            mock.patch.dict(os.environ, self.environ, clear=True),
            mock.patch.object(nest_events, "_create_subscriber") as subscriber,
            contextlib.redirect_stdout(output),
        ):
            status = nest_events.main(["migrate"])

        self.assertEqual(status, 0)
        subscriber.assert_not_called()
        result = json.loads(output.getvalue())
        self.assertEqual(
            result,
            {
                "databaseSchemaVersion": nest_events.SCHEMA_VERSION,
                "schemaVersion": nest_events.STATUS_SCHEMA_VERSION,
                "service": nest_events.SERVICE_NAME,
                "status": "ready",
            },
        )
        self.assertNotIn("enterprises/", output.getvalue())
        self.assertNotIn(str(self.root), output.getvalue())
        connection = sqlite3.connect(self.state_dir / nest_events.DB_FILENAME)
        try:
            self.assertEqual(
                connection.execute("SELECT version FROM schema_meta").fetchone(),
                (nest_events.SCHEMA_VERSION,),
            )
        finally:
            connection.close()


class ParsingAndPolicyTests(NestEventTestCase):
    def test_combined_events_normalize_without_user_or_inner_ids(self) -> None:
        envelope = nest_events.parse_sdm_payload(
            event_payload(
                event_names=(nest_events.MOTION_EVENT, nest_events.PERSON_EVENT),
                thread_id="private-thread-id",
                thread_state="STARTED",
            )
        )
        self.assertEqual(
            tuple(event.event_type for event in envelope.camera_events),
            ("motion", "person"),
        )
        normalized = repr(envelope)
        self.assertNotIn("private-user-id", normalized)
        self.assertNotIn("inner-image-event-secret", normalized)
        self.assertNotIn("inner-session-secret", normalized)

    def test_inner_camera_identifiers_are_optional_but_validated_if_present(self) -> None:
        no_identifiers = nest_events.parse_sdm_payload(event_payload(details={}))
        self.assertEqual(no_identifiers.camera_events[0].event_type, "person")

        one_identifier = nest_events.parse_sdm_payload(
            event_payload(details={"eventSessionId": "session-only"})
        )
        self.assertEqual(one_identifier.camera_events[0].event_type, "person")

        with self.assertRaisesRegex(
            nest_events.PayloadError, "invalid_camera_event_id"
        ):
            nest_events.parse_sdm_payload(event_payload(details={"eventId": ""}))

    def test_relation_traits_unknown_resource_and_unknown_type_are_ignored(self) -> None:
        relation = {
            "eventId": "relation-secret",
            "timestamp": "2026-07-11T18:00:00Z",
            "relationUpdate": {
                "type": "CREATED",
                "subject": "enterprises/private/structures/private",
                "object": CAMERA_RESOURCES["Kitchen"],
            },
            "userId": "private-user-id",
        }
        relation_envelope = nest_events.parse_sdm_payload(
            json.dumps(relation).encode("utf-8")
        )
        self.assertEqual(
            nest_events.apply_policy(
                relation_envelope, self.settings.cameras_by_resource
            ).outcome,
            "ignored_relation",
        )

        unknown = nest_events.parse_sdm_payload(
            event_payload(resource="enterprises/other/devices/not-allowlisted")
        )
        self.assertEqual(
            nest_events.apply_policy(
                unknown, self.settings.cameras_by_resource
            ).outcome,
            "ignored_resource",
        )

        other_event = json.loads(event_payload())
        other_event["resourceUpdate"]["events"] = {
            "sdm.devices.events.CameraSound.Sound": {}
        }
        other_envelope = nest_events.parse_sdm_payload(
            json.dumps(other_event).encode("utf-8")
        )
        self.assertEqual(
            nest_events.apply_policy(
                other_envelope, self.settings.cameras_by_resource
            ).outcome,
            "ignored_event_type",
        )

    def test_unbound_camera_event_records_only_safe_scope_diagnostic(self) -> None:
        store = self.store()
        same_enterprise = store.record_delivery(
            event_payload(
                resource="enterprises/sdm-project/devices/replaced-camera",
                top_event_id="same-enterprise-event",
            ),
            "same-enterprise-message",
        )
        other_enterprise = store.record_delivery(
            event_payload(
                resource="enterprises/other-project/devices/other-camera",
                top_event_id="other-enterprise-event",
            ),
            "other-enterprise-message",
        )

        self.assertEqual(
            same_enterprise.reason_code, "unbound_camera_same_enterprise"
        )
        self.assertEqual(
            other_enterprise.reason_code, "unbound_camera_other_enterprise"
        )
        with sqlite3.connect(store.db_path) as connection:
            rows = connection.execute(
                "SELECT outcome, reason_code FROM inbox ORDER BY id"
            ).fetchall()
        self.assertEqual(
            rows,
            [
                ("ignored_resource", "unbound_camera_same_enterprise"),
                ("ignored_resource", "unbound_camera_other_enterprise"),
            ],
        )
        combined = json.dumps(rows)
        self.assertNotIn("replaced-camera", combined)
        self.assertNotIn("other-camera", combined)
        self.assertNotIn("sdm-project", combined)
        self.assertNotIn("other-project", combined)


class DurabilityTests(NestEventTestCase):
    def test_v1_empty_outbox_seeds_sequence_from_lifetime_event_count(self) -> None:
        self.state_dir.mkdir(parents=True, mode=0o700)
        os.chmod(self.state_dir, 0o700)
        database = self.state_dir / nest_events.DB_FILENAME
        connection = sqlite3.connect(database)
        try:
            connection.executescript(V1_SCHEMA)
            connection.execute("INSERT INTO schema_meta(version) VALUES (1)")
            connection.execute(
                "INSERT INTO service_counters(name, value) VALUES ('accepted_events', 17)"
            )
            connection.commit()
        finally:
            connection.close()
        os.chmod(database, 0o600)

        store = self.store()
        connection = sqlite3.connect(store.db_path)
        try:
            self.assertEqual(
                connection.execute(
                    "SELECT seq FROM sqlite_sequence WHERE name = 'outbox'"
                ).fetchone(),
                (17,),
            )
        finally:
            connection.close()

        result = store.record_delivery(event_payload(), "new-message")
        self.assertEqual(result.outcome, "accepted")
        connection = sqlite3.connect(store.db_path)
        try:
            self.assertEqual(
                connection.execute("SELECT id FROM outbox").fetchone(),
                (18,),
            )
        finally:
            connection.close()

    def test_v1_production_schema_migrates_outbox_without_changing_rows(self) -> None:
        self.state_dir.mkdir(parents=True, mode=0o700)
        os.chmod(self.state_dir, 0o700)
        database = self.state_dir / nest_events.DB_FILENAME
        connection = sqlite3.connect(database)
        try:
            connection.execute("PRAGMA foreign_keys = ON")
            connection.executescript(V1_SCHEMA)
            connection.execute("INSERT INTO schema_meta(version) VALUES (1)")
            connection.execute(
                """
                INSERT INTO inbox(
                    id, message_key, received_at, publish_at, sdm_event_key,
                    thread_key, event_at, alias, site, outcome,
                    normalized_event_count
                ) VALUES (7, 'message-key', '2026-07-11T18:00:00.000000Z',
                          '2026-07-11T17:59:59.000000Z', 'event-key',
                          'thread-key', '2026-07-11T18:00:00.000000Z',
                          'Kitchen', 'Cabin', 'accepted', 1)
                """
            )
            connection.execute(
                """
                INSERT INTO sdm_dedupe(event_key, first_inbox_id, first_seen_at)
                VALUES ('event-key', 7, '2026-07-11T18:00:00.000000Z')
                """
            )
            connection.execute(
                """
                INSERT INTO event_records(
                    id, inbox_id, dedupe_key, event_key, thread_key,
                    thread_state, alias, site, event_type, first_occurred_at,
                    last_occurred_at, capture_strategy, update_count
                ) VALUES (
                    23, 7, 'record-key', 'event-key', 'thread-key', 'STARTED',
                    'Kitchen', 'Cabin', 'person',
                    '2026-07-11T18:00:00.000000Z',
                    '2026-07-11T18:00:00.000000Z', 'live', 0
                )
                """
            )
            connection.execute(
                """
                INSERT INTO outbox(
                    id, event_record_id, alias, site, event_type, event_at,
                    capture_strategy, status, created_at
                ) VALUES (
                    41, 23, 'Kitchen', 'Cabin', 'person',
                    '2026-07-11T18:00:00.000000Z', 'live', 'shadowed',
                    '2026-07-11T18:00:01.000000Z'
                )
                """
            )
            connection.execute(
                "INSERT INTO service_counters(name, value) VALUES ('accepted_events', 1)"
            )
            connection.execute(
                """
                INSERT INTO runtime_status(
                    singleton, mode, health, started_at, updated_at,
                    last_message_at, last_accepted_event_at
                ) VALUES (
                    1, 'shadow', 'ok', '2026-07-11T17:00:00.000000Z',
                    '2026-07-11T18:00:01.000000Z',
                    '2026-07-11T18:00:01.000000Z',
                    '2026-07-11T18:00:00.000000Z'
                )
                """
            )
            connection.commit()
        finally:
            connection.close()
        os.chmod(database, 0o600)

        store = self.store()

        connection = sqlite3.connect(store.db_path)
        try:
            self.assertEqual(
                connection.execute("SELECT version FROM schema_meta").fetchall(),
                [(nest_events.SCHEMA_VERSION,)],
            )
            self.assertEqual(
                connection.execute(
                    """
                    SELECT id, event_record_id, alias, site, event_type,
                           event_at, capture_strategy, status, created_at
                    FROM outbox
                    """
                ).fetchall(),
                [
                    (
                        41,
                        23,
                        "Kitchen",
                        "Cabin",
                        "person",
                        "2026-07-11T18:00:00.000000Z",
                        "live",
                        "shadowed",
                        "2026-07-11T18:00:01.000000Z",
                    )
                ],
            )
            outbox_sql = connection.execute(
                "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'outbox'"
            ).fetchone()[0]
            self.assertIn("AUTOINCREMENT", outbox_sql.upper())
            self.assertEqual(
                connection.execute(
                    "SELECT seq FROM sqlite_sequence WHERE name = 'outbox'"
                ).fetchone(),
                (41,),
            )
            self.assertEqual(connection.execute("PRAGMA foreign_key_check").fetchall(), [])
            self.assertEqual(connection.execute("PRAGMA quick_check").fetchone(), ("ok",))
            self.assertEqual(
                connection.execute(
                    "SELECT value FROM service_counters WHERE name = 'accepted_events'"
                ).fetchone(),
                (1,),
            )
        finally:
            connection.close()

        result = store.record_delivery(
            event_payload(
                top_event_id="post-migration-event",
                timestamp="2026-07-11T18:30:00Z",
            ),
            "post-migration-message",
        )
        self.assertEqual(result.outcome, "accepted")
        connection = sqlite3.connect(store.db_path)
        try:
            self.assertEqual(
                connection.execute("SELECT id FROM outbox ORDER BY id").fetchall(),
                [(41,), (42,)],
            )
        finally:
            connection.close()

    def test_shadow_inbox_outbox_status_are_durable_and_privacy_safe(self) -> None:
        store = self.store()
        store.mark_running()
        result = store.record_delivery(
            event_payload(
                event_names=(nest_events.MOTION_EVENT, nest_events.PERSON_EVENT),
                thread_id="top-thread-secret",
                thread_state="STARTED",
            ),
            "pubsub-message-id-secret",
            "2026-07-11T18:00:01Z",
        )
        self.assertEqual(result.outcome, "accepted")
        self.assertEqual(result.accepted_events, 2)

        with sqlite3.connect(store.db_path) as connection:
            inbox = connection.execute(
                "SELECT alias, site, outcome, normalized_event_count FROM inbox"
            ).fetchone()
            self.assertEqual(inbox, ("Kitchen", "Cabin", "accepted", 2))
            outbox = connection.execute(
                "SELECT alias, site, event_type, capture_strategy, status FROM outbox ORDER BY event_type"
            ).fetchall()
            self.assertEqual(
                outbox,
                [
                    ("Kitchen", "Cabin", "motion", "live", "shadowed"),
                    ("Kitchen", "Cabin", "person", "live", "shadowed"),
                ],
            )

        status = json.loads(store.status_path.read_text(encoding="utf-8"))
        self.assertEqual(status["health"], "ok")
        self.assertEqual(status["outbox"]["shadowed"], 2)
        self.assertEqual(status["cameras"][0]["site"], "Cabin")
        self.assertEqual(stat_mode(store.db_path), 0o600)
        self.assertEqual(stat_mode(store.status_path), 0o600)
        self.assertEqual(stat_mode(store.state_dir), 0o700)

        durable_bytes = store.db_path.read_bytes() + store.status_path.read_bytes()
        for private_value in (
            b"private-user-id",
            b"sdm-project",
            b"kitchen-device-secret",
            b"top-event-secret-1",
            b"top-thread-secret",
            b"inner-image-event-secret",
            b"inner-session-secret",
            b"pubsub-message-id-secret",
        ):
            self.assertNotIn(private_value, durable_bytes)

    def test_crosstown_living_room_cameras_plan_event_images_without_dispatch(self) -> None:
        store = self.store()
        for index, alias in enumerate(("Living Room", "Living Room Wired"), start=1):
            result = store.record_delivery(
                event_payload(
                    resource=CAMERA_RESOURCES[alias],
                    top_event_id=f"top-{index}",
                    details={},
                ),
                f"message-{index}",
            )
            self.assertEqual(result.outcome, "accepted")
        with sqlite3.connect(store.db_path) as connection:
            rows = connection.execute(
                "SELECT alias, site, capture_strategy, status FROM outbox ORDER BY alias"
            ).fetchall()
        self.assertEqual(
            rows,
            [
                ("Living Room", "Crosstown", "event", "shadowed"),
                ("Living Room Wired", "Crosstown", "event", "shadowed"),
            ],
        )

    def test_deduplicates_message_top_event_and_thread_across_restarts(self) -> None:
        first_store = self.store()
        first = first_store.record_delivery(
            event_payload(
                top_event_id="top-one",
                thread_id="same-thread",
                thread_state="STARTED",
            ),
            "message-one",
        )
        self.assertEqual(first.outcome, "accepted")

        # A new store instance exercises persistent, not in-memory, dedupe.
        second_store = self.store()
        duplicate_message = second_store.record_delivery(
            event_payload(
                top_event_id="top-one",
                thread_id="same-thread",
                thread_state="STARTED",
            ),
            "message-one",
        )
        self.assertEqual(duplicate_message.outcome, "duplicate_message")

        duplicate_event = second_store.record_delivery(
            event_payload(
                top_event_id="top-one",
                thread_id="same-thread",
                thread_state="STARTED",
            ),
            "message-two",
        )
        self.assertEqual(duplicate_event.outcome, "duplicate_event")

        duplicate_thread = second_store.record_delivery(
            event_payload(
                top_event_id="top-two",
                timestamp="2026-07-11T18:01:00Z",
                thread_id="same-thread",
                thread_state="UPDATED",
            ),
            "message-three",
        )
        self.assertEqual(duplicate_thread.outcome, "duplicate_thread")
        with sqlite3.connect(second_store.db_path) as connection:
            self.assertEqual(
                connection.execute("SELECT COUNT(*) FROM outbox").fetchone()[0], 1
            )
            record = connection.execute(
                "SELECT thread_state, update_count FROM event_records"
            ).fetchone()
        self.assertEqual(record, ("UPDATED", 1))

    def test_out_of_order_thread_update_does_not_regress_timestamp_or_state(self) -> None:
        store = self.store()
        store.record_delivery(
            event_payload(
                top_event_id="newer-top",
                timestamp="2026-07-11T18:05:00Z",
                thread_id="unordered-thread",
                thread_state="ENDED",
            ),
            "newer-message",
        )
        result = store.record_delivery(
            event_payload(
                top_event_id="older-top",
                timestamp="2026-07-11T18:01:00Z",
                thread_id="unordered-thread",
                thread_state="STARTED",
            ),
            "older-message",
        )
        self.assertEqual(result.outcome, "duplicate_thread")
        with sqlite3.connect(store.db_path) as connection:
            record = connection.execute(
                "SELECT last_occurred_at, thread_state, update_count FROM event_records"
            ).fetchone()
        self.assertEqual(record[0], "2026-07-11T18:05:00.000000Z")
        self.assertEqual(record[1:], ("ENDED", 1))

    def test_malformed_payload_is_tombstoned_for_ack_not_retried(self) -> None:
        store = self.store()
        result = store.record_delivery(b"not-json", "malformed-message")
        self.assertEqual(result.outcome, "invalid")
        with sqlite3.connect(store.db_path) as connection:
            row = connection.execute(
                "SELECT outcome, reason_code FROM inbox"
            ).fetchone()
        self.assertEqual(row, ("invalid", "invalid_json"))

    def test_fixed_retention_prunes_related_metadata_across_restart(self) -> None:
        class MutableClock:
            value = "2026-01-01T12:00:00Z"

            def __call__(self):
                return self.value

        clock = MutableClock()
        first_store = nest_events.StateStore(self.settings, clock=clock)
        first_store.record_delivery(
            event_payload(
                top_event_id="aged-top-event",
                timestamp="2026-01-01T11:59:00Z",
                thread_id="aged-thread",
                thread_state="STARTED",
            ),
            "aged-message",
        )
        with sqlite3.connect(first_store.db_path) as connection:
            for table in ("inbox", "sdm_dedupe", "event_records", "outbox"):
                self.assertEqual(
                    connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0],
                    1,
                )

        clock.value = "2026-02-01T12:00:01Z"
        restarted_store = nest_events.StateStore(self.settings, clock=clock)
        with sqlite3.connect(restarted_store.db_path) as connection:
            for table in ("outbox", "event_records", "sdm_dedupe", "inbox"):
                self.assertEqual(
                    connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0],
                    0,
                )
            self.assertEqual(connection.execute("PRAGMA foreign_key_check").fetchall(), [])
            self.assertEqual(
                connection.execute(
                    "SELECT value FROM service_counters WHERE name = 'accepted_events'"
                ).fetchone()[0],
                1,
            )

        # Expired dedupe keys leave with their inbox row, so a later redelivery
        # is a new retained observation rather than a permanent false duplicate.
        result = restarted_store.record_delivery(
            event_payload(
                top_event_id="aged-top-event",
                timestamp="2026-01-01T11:59:00Z",
                thread_id="aged-thread",
                thread_state="STARTED",
            ),
            "aged-message",
        )
        self.assertEqual(result.outcome, "accepted")
        connection = sqlite3.connect(restarted_store.db_path)
        try:
            self.assertEqual(
                connection.execute("SELECT id FROM outbox").fetchall(),
                [(2,)],
            )
            self.assertEqual(
                connection.execute(
                    "SELECT seq FROM sqlite_sequence WHERE name = 'outbox'"
                ).fetchone(),
                (2,),
            )
        finally:
            connection.close()
        status = restarted_store.status_snapshot()
        self.assertEqual(status["retentionDays"], 30)


class AckBoundaryTests(NestEventTestCase):
    def test_callback_acks_only_after_store_returns_and_nacks_transient_failure(self) -> None:
        calls: list[str] = []

        class SuccessfulStore:
            def record_delivery(self, data, message_id, publish_time):
                calls.append("committed")
                return nest_events.ProcessResult("accepted", 1, 0, "Kitchen", "Cabin")

            def mark_runtime_error(self, code):
                calls.append(f"error:{code}")

        class Message:
            message_id = "message"
            data = b"{}"
            publish_time = None

            def ack(self):
                self.assert_committed()
                calls.append("ack")

            def nack(self):
                calls.append("nack")

            @staticmethod
            def assert_committed():
                if calls != ["committed"]:
                    raise AssertionError("ack occurred before durable return")

        nest_events.PubSubMessageProcessor(SuccessfulStore())(Message())
        self.assertEqual(calls, ["committed", "ack"])

        calls.clear()

        class FailingStore(SuccessfulStore):
            def record_delivery(self, data, message_id, publish_time):
                raise sqlite3.OperationalError("transient")

        nest_events.PubSubMessageProcessor(FailingStore())(Message())
        self.assertEqual(calls, ["error:durable_commit_failed", "nack"])

    def test_status_projection_failure_after_commit_still_acks(self) -> None:
        store = self.store()

        def fail_projection():
            raise OSError("simulated projection failure")

        store.write_status = fail_projection

        class Message:
            message_id = "projection-message"
            data = event_payload(top_event_id="projection-top-event")
            publish_time = None
            acked = False
            nacked = False

            def ack(self):
                self.acked = True

            def nack(self):
                self.nacked = True

        message = Message()
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            nest_events.PubSubMessageProcessor(store)(message)
        self.assertTrue(message.acked)
        self.assertFalse(message.nacked)
        with sqlite3.connect(store.db_path) as connection:
            self.assertEqual(
                connection.execute("SELECT COUNT(*) FROM inbox").fetchone()[0], 1
            )
        records = [json.loads(line) for line in output.getvalue().splitlines()]
        self.assertIn(
            "status_projection_failed", {record["event"] for record in records}
        )


class StreamingRuntimeTests(NestEventTestCase):
    def test_household_flow_control_is_explicit_and_passed_to_subscribe(self) -> None:
        class FlowControl:
            def __init__(self, **values):
                self.values = values

        pubsub_module = types.SimpleNamespace(
            types=types.SimpleNamespace(FlowControl=FlowControl)
        )
        flow_control = nest_events._build_flow_control(pubsub_module)
        self.assertEqual(
            flow_control.values,
            {"max_messages": 4, "max_bytes": 2 * 1024 * 1024},
        )

        class Store:
            def mark_running(self):
                pass

            def mark_runtime_error(self, code):
                raise AssertionError(f"unexpected runtime error: {code}")

        class Future:
            stopped = False

            def result(self, timeout=None):
                if timeout is None and not self.stopped:
                    self.stopped = True
                    raise KeyboardInterrupt
                raise RuntimeError("cancelled")

            def cancel(self):
                self.stopped = True

        class Subscriber:
            kwargs = None

            def subscribe(self, path, **kwargs):
                self.path = path
                self.kwargs = kwargs
                return Future()

            def close(self):
                pass

        subscriber = Subscriber()
        with (
            mock.patch.object(nest_events, "StateStore", return_value=Store()),
            mock.patch.object(
                nest_events,
                "_create_subscriber",
                return_value=(subscriber, "projects/project/subscriptions/events", flow_control),
            ),
            contextlib.redirect_stdout(io.StringIO()),
        ):
            result = nest_events.run_listener(self.settings, once=False)
        self.assertEqual(result, 0)
        self.assertIs(subscriber.kwargs["flow_control"], flow_control)
        self.assertTrue(callable(subscriber.kwargs["callback"]))


def stat_mode(path: Path) -> int:
    return path.stat().st_mode & 0o777


if __name__ == "__main__":
    unittest.main()
