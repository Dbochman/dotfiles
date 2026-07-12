#!/usr/bin/env python3
"""Focused tests for the durable, privacy-safe home-event bus core."""

from __future__ import annotations

import contextlib
import fcntl
import importlib.util
import io
import json
import os
from pathlib import Path
import sqlite3
import stat
import sys
import tempfile
import unittest
from unittest import mock


MODULE_PATH = Path(__file__).resolve().parents[1] / "bin" / "home_event_bus.py"
SPEC = importlib.util.spec_from_file_location("home_event_bus", MODULE_PATH)
assert SPEC and SPEC.loader
home_events = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = home_events
SPEC.loader.exec_module(home_events)


NOW = "2026-07-12T15:00:00Z"


def ring_payload(
    *,
    source_event_id: str = "private-ring-device:private-ring-event",
    event_type: str = "entry.person_detected",
    site: str = "crosstown",
    occurred_at: str = "2026-07-12T14:59:55Z",
    observed_at: str = "2026-07-12T14:59:56Z",
    schema: bool = True,
) -> dict:
    attributes = {}
    if event_type == "entry.person_detected":
        attributes = {"classification": "person"}
    elif event_type == "entry.motion_detected":
        attributes = {"classification": "motion"}
    value = {
        "source_event_id": source_event_id,
        "event_type": event_type,
        "site": site,
        "entity_kind": "doorbell",
        "entity_alias": "front_door",
        "occurred_at": occurred_at,
        "observed_at": observed_at,
        "time_precision": "source",
        "attributes": attributes,
    }
    if schema:
        value["schema_version"] = 1
    return value


def august_payload(*, source_event_id: str = "private-transition-id") -> dict:
    return {
        "source_event_id": source_event_id,
        "event_type": "lock.unlocked",
        "site": "crosstown",
        "entity_kind": "lock",
        "entity_alias": "front_door",
        "occurred_at": "2026-07-12T15:00:00Z",
        "observed_at": "2026-07-12T15:00:00Z",
        "time_precision": "observed_interval",
        "attributes": {
            "previous": "locked",
            "current": "unlocked",
            "not_before": "2026-07-12T14:55:00Z",
            "not_after": "2026-07-12T15:00:00Z",
        },
    }


def encode(value: object) -> bytes:
    return json.dumps(value, separators=(",", ":")).encode("utf-8")


class HomeEventTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name) / "home-events"
        self.store = home_events.initialize_runtime(self.root, clock=lambda: NOW)
        self.paths = home_events.RuntimePaths(self.root)

    def enqueue(self, value: dict, source: str = "ring"):
        return home_events.enqueue_event(
            self.root,
            source,
            encode(value),
            clock=lambda: NOW,
        )

    def ingest(self):
        return home_events.ingest_once(self.root, clock=lambda: NOW)

    def connection(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.paths.database)
        self.addCleanup(connection.close)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        return connection


class RuntimeSecurityTests(HomeEventTestCase):
    def test_init_builds_private_runtime_and_expected_schema(self) -> None:
        directories = [
            self.paths.root,
            self.paths.config,
            self.paths.spool,
            self.paths.state,
            *(self.paths.source_spool(source) for source in home_events.SOURCES),
        ]
        for directory in directories:
            with self.subTest(directory=directory):
                metadata = directory.lstat()
                self.assertTrue(stat.S_ISDIR(metadata.st_mode))
                self.assertEqual(stat.S_IMODE(metadata.st_mode), 0o700)
        for path in (
            self.paths.secret,
            self.paths.database,
            self.paths.ingest_lock,
            self.paths.status,
        ):
            with self.subTest(path=path):
                self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)

        self.store.check_schema()
        with contextlib.closing(self.store.connect()) as connection:
            self.assertEqual(
                connection.execute("PRAGMA journal_mode").fetchone()[0].lower(),
                "wal",
            )
            self.assertEqual(connection.execute("PRAGMA synchronous").fetchone()[0], 2)
            self.assertEqual(connection.execute("PRAGMA foreign_keys").fetchone()[0], 1)
            tables = {
                row[0]
                for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                )
            }
        self.assertTrue(home_events.EXPECTED_TABLES.issubset(tables))

    def test_insecure_directory_secret_and_database_symlink_fail_closed(self) -> None:
        os.chmod(self.paths.spool, 0o755)
        with self.assertRaisesRegex(home_events.ConfigError, "directory_permissions"):
            home_events.validate_runtime(self.root)
        os.chmod(self.paths.spool, 0o700)

        os.chmod(self.paths.secret, 0o644)
        with self.assertRaisesRegex(home_events.ConfigError, "private_file_permissions"):
            home_events.validate_runtime(self.root)
        os.chmod(self.paths.secret, 0o600)

        alternate = self.paths.state / "alternate.sqlite3"
        alternate.write_bytes(b"not a database")
        os.chmod(alternate, 0o600)
        self.paths.database.unlink()
        self.paths.database.symlink_to(alternate)
        with self.assertRaisesRegex(home_events.ConfigError, "private_file_unavailable"):
            home_events.validate_runtime(self.root)

    def test_schema_mismatch_and_relative_root_are_rejected(self) -> None:
        with self.connection() as connection:
            connection.execute("UPDATE schema_migrations SET version = 99")
        with self.assertRaisesRegex(home_events.ConfigError, "database_schema"):
            self.store.check_schema()
        with self.assertRaisesRegex(home_events.ConfigError, "root_not_absolute"):
            home_events.initialize_runtime(Path("relative"), clock=lambda: NOW)

    def test_status_projection_is_safe_and_mode_0600(self) -> None:
        self.enqueue(ring_payload())
        self.ingest()
        encoded = self.paths.status.read_text(encoding="utf-8")
        self.assertNotIn("private-ring-device", encoded)
        self.assertNotIn("private-ring-event", encoded)
        self.assertNotIn("front_door", encoded)
        self.assertEqual(stat.S_IMODE(self.paths.status.stat().st_mode), 0o600)
        status = json.loads(encoded)
        self.assertEqual(status["counts"]["events"], 1)
        self.assertEqual(status["sources"]["ring"]["accepted"], 1)
        self.assertEqual(status["sources"]["ring"]["health"], "ok")
        self.assertEqual(
            status["sources"]["ring"]["publisher"]["health"], "unknown"
        )
        self.assertEqual(status["consumers"]["correlator"]["pending"], 1)
        self.assertEqual(
            status["retention_days"], {"accepted": 30, "dead_letter": 90}
        )
        self.assertGreater(status["database_bytes"], 0)

    def test_ring_worker_health_projection_is_bounded_and_explicit(self) -> None:
        self.paths.ring_producer_status.write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "updated_at": NOW,
                    "health": "degraded",
                    "counters": {
                        "accepted": 4,
                        "published": 2,
                        "failed": 1,
                        "dropped": 1,
                        "quarantined": 0,
                    },
                }
            ),
            encoding="utf-8",
        )
        self.paths.ring_producer_status.chmod(0o600)

        status = self.store.status_snapshot()["sources"]["ring"]

        self.assertEqual(status["health"], "degraded")
        self.assertEqual(status["publisher"]["counters"]["dropped"], 1)
        self.assertIsNone(status["publisher"]["error_code"])


class EnqueueValidationTests(HomeEventTestCase):
    def test_raw_source_identifier_is_hmaced_before_spool_or_database(self) -> None:
        raw_id = "private-ring-device:private-ring-event"
        event = self.enqueue(ring_payload(source_event_id=raw_id))
        self.assertRegex(event.event_uid, r"^evt_[0-9a-f]{32}$")
        ready = list(self.paths.source_spool("ring").glob("*.ready"))
        self.assertEqual(len(ready), 1)
        spool_bytes = ready[0].read_bytes()
        self.assertNotIn(raw_id.encode(), spool_bytes)
        record = json.loads(spool_bytes)
        self.assertNotIn("source_event_id", record)
        self.assertRegex(record["dedupe_key"], r"^ded_[0-9a-f]{64}$")

        self.ingest()
        for path in self.root.rglob("*"):
            if path.is_file() and not path.is_symlink():
                self.assertNotIn(raw_id.encode(), path.read_bytes(), path)

    def test_schema_version_is_optional_but_unknown_fields_are_rejected(self) -> None:
        first = self.enqueue(ring_payload(schema=False))
        self.assertRegex(first.event_uid, r"^evt_")
        unknown = ring_payload()
        unknown["raw_payload"] = {"account": "secret"}
        with self.assertRaisesRegex(home_events.PayloadError, "invalid_event_fields"):
            self.enqueue(unknown)
        wrong = ring_payload()
        wrong["schema_version"] = 2
        with self.assertRaisesRegex(home_events.PayloadError, "invalid_event_schema"):
            self.enqueue(wrong)

    def test_source_specific_type_kind_precision_and_attributes_are_strict(self) -> None:
        mutations = {
            "event_type": "lock.unlocked",
            "entity_kind": "lock",
            "time_precision": "observed_interval",
            "site": "garage",
        }
        for key, value in mutations.items():
            with self.subTest(key=key):
                payload = ring_payload()
                payload[key] = value
                with self.assertRaises(home_events.PayloadError):
                    self.enqueue(payload)
        payload = ring_payload()
        payload["attributes"]["device_id"] = "private"
        with self.assertRaisesRegex(home_events.PayloadError, "unknown_attribute"):
            self.enqueue(payload)

        payload = ring_payload()
        payload["attributes"] = {}
        with self.assertRaisesRegex(home_events.PayloadError, "missing_attribute"):
            self.enqueue(payload)

        payload = ring_payload()
        payload["entity_alias"] = "provider_12345"
        with self.assertRaisesRegex(home_events.PayloadError, "unbound_entity_alias"):
            self.enqueue(payload)

        payload = ring_payload(event_type="entry.motion_detected")
        payload["attributes"]["classification"] = "person"
        with self.assertRaisesRegex(home_events.PayloadError, "classification_mismatch"):
            self.enqueue(payload)

        delayed = ring_payload(
            source_event_id="delayed-unmarked",
            occurred_at="2026-07-12T14:55:00Z",
            observed_at="2026-07-12T15:00:00Z",
        )
        with self.assertRaisesRegex(home_events.PayloadError, "unmarked_ring_backfill"):
            self.enqueue(delayed)

        delayed["source_event_id"] = "delayed-marked"
        delayed["time_precision"] = "backfill"
        delayed["attributes"]["backfill"] = True
        event = self.enqueue(delayed)
        self.assertEqual(event.time_precision, "backfill")

    def test_duplicate_keys_nonfinite_numbers_oversize_and_bad_time_are_rejected(self) -> None:
        duplicate = (
            b'{"schema_version":1,"schema_version":1,"source_event_id":"x",'
            b'"event_type":"entry.person_detected","site":"crosstown",'
            b'"entity_kind":"doorbell","entity_alias":"front_door",'
            b'"occurred_at":"2026-07-12T14:59:55Z",'
            b'"observed_at":"2026-07-12T14:59:56Z","time_precision":"source",'
            b'"attributes":{}}'
        )
        with self.assertRaisesRegex(home_events.PayloadError, "duplicate_json_key"):
            home_events.enqueue_event(
                self.root, "ring", duplicate, clock=lambda: NOW
            )
        with self.assertRaisesRegex(home_events.PayloadError, "event_too_large"):
            home_events.enqueue_event(
                self.root,
                "ring",
                b"{" + (b"x" * home_events.MAX_STDIN_BYTES),
                clock=lambda: NOW,
            )
        stale = ring_payload(
            occurred_at="2026-07-01T00:00:00Z",
            observed_at="2026-07-01T00:00:01Z",
        )
        with self.assertRaisesRegex(home_events.PayloadError, "observed_too_old"):
            self.enqueue(stale)
        future = ring_payload(
            occurred_at="2026-07-12T15:10:00Z",
            observed_at="2026-07-12T15:10:00Z",
        )
        with self.assertRaisesRegex(home_events.PayloadError, "observed_in_future"):
            self.enqueue(future)

    def test_august_contract_accepts_omitted_schema_version(self) -> None:
        event = self.enqueue(august_payload(), source="august")
        self.assertEqual(event.event_type, "lock.unlocked")
        self.assertEqual(event.time_precision, "observed_interval")

        missing = august_payload(source_event_id="missing-attributes")
        missing["attributes"] = {}
        with self.assertRaisesRegex(home_events.PayloadError, "missing_attribute"):
            self.enqueue(missing, source="august")

        reversed_interval = august_payload(source_event_id="reversed-interval")
        reversed_interval["attributes"]["not_before"] = "2026-07-12T15:01:00Z"
        with self.assertRaisesRegex(home_events.PayloadError, "invalid_observed_interval"):
            self.enqueue(reversed_interval, source="august")

        wrong_kind = august_payload(source_event_id="wrong-kind")
        wrong_kind["entity_kind"] = "door"
        with self.assertRaisesRegex(home_events.PayloadError, "event_entity_mismatch"):
            self.enqueue(wrong_kind, source="august")

        reversed_direction = august_payload(source_event_id="wrong-direction")
        reversed_direction["event_type"] = "lock.locked"
        with self.assertRaisesRegex(
            home_events.PayloadError, "transition_direction_mismatch"
        ):
            self.enqueue(reversed_direction, source="august")

    def test_presence_contract_accepts_only_canonical_safe_context(self) -> None:
        payload = {
            "schema_version": 1,
            "source_event_id": "presence_" + ("1" * 64),
            "event_type": "presence.occupancy_changed",
            "site": "cabin",
            "entity_kind": "site",
            "entity_alias": "cabin",
            "occurred_at": "2026-07-12T14:59:00Z",
            "observed_at": "2026-07-12T15:00:00Z",
            "time_precision": "evaluation",
            "attributes": {
                "previous": "occupied",
                "current": "confirmed_vacant",
                "confidence": "canonical",
                "evidence_at": "2026-07-12T14:58:00Z",
                "state_hash": "2" * 64,
            },
        }
        event = self.enqueue(payload, source="presence")
        self.assertEqual(event.attributes["current"], "confirmed_vacant")
        payload["source_event_id"] = "presence_" + ("3" * 64)
        payload["attributes"]["current"] = "occupied"
        with self.assertRaisesRegex(
            home_events.PayloadError, "invalid_presence_transition"
        ):
            self.enqueue(payload, source="presence")
        payload["attributes"]["current"] = "confirmed_vacant"
        payload["attributes"]["confidence"] = "positive_detection"
        with self.assertRaisesRegex(home_events.PayloadError, "confidence_mismatch"):
            self.enqueue(payload, source="presence")
        payload["attributes"]["confidence"] = "canonical"
        payload["attributes"]["state_hash"] = "private-state-value"
        with self.assertRaisesRegex(home_events.PayloadError, "invalid_state_hash"):
            self.enqueue(payload, source="presence")


class IngestDurabilityTests(HomeEventTestCase):
    def test_claim_returns_global_observation_order_not_ingest_order(self) -> None:
        later = ring_payload(
            source_event_id="later-ingested-first",
            occurred_at="2026-07-12T14:59:49Z",
            observed_at="2026-07-12T14:59:50Z",
        )
        earlier = ring_payload(
            source_event_id="earlier-ingested-second",
            occurred_at="2026-07-12T14:58:59Z",
            observed_at="2026-07-12T14:59:00Z",
        )
        self.enqueue(later)
        self.ingest()
        self.enqueue(earlier)
        self.ingest()

        claimed = self.store.claim_deliveries("correlator", limit=2)

        self.assertEqual(
            [delivery["observed_at"] for delivery in claimed["deliveries"]],
            ["2026-07-12T14:59:00Z", "2026-07-12T14:59:50Z"],
        )

    def test_ingest_commits_event_state_and_pending_consumer_atomically(self) -> None:
        self.enqueue(ring_payload())
        result = self.ingest()
        self.assertEqual(result, home_events.IngestResult(scanned=1, accepted=1))
        with self.connection() as connection:
            event = connection.execute("SELECT * FROM events").fetchone()
            inbox = connection.execute("SELECT * FROM producer_inbox").fetchone()
            state = connection.execute(
                "SELECT * FROM producer_state WHERE source='ring'"
            ).fetchone()
            delivery = connection.execute("SELECT * FROM consumer_deliveries").fetchone()
        self.assertEqual(inbox["outcome"], "accepted")
        self.assertEqual(event["producer_inbox_id"], inbox["id"])
        self.assertEqual(state["last_event_id"], event["id"])
        self.assertEqual(state["accepted_count"], 1)
        self.assertEqual(delivery["event_id"], event["id"])
        self.assertEqual(delivery["status"], "pending")
        self.assertEqual(list(self.paths.source_spool("ring").glob("*.ready")), [])

    def test_duplicate_source_event_across_distinct_spool_files_is_exactly_once(self) -> None:
        first = self.enqueue(ring_payload())
        second = self.enqueue(ring_payload())
        self.assertEqual(first.event_uid, second.event_uid)
        result = self.ingest()
        self.assertEqual(result.scanned, 2)
        self.assertEqual(result.accepted, 1)
        self.assertEqual(result.duplicate, 1)
        with self.connection() as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM events").fetchone()[0], 1)
            outcomes = [
                row[0]
                for row in connection.execute(
                    "SELECT outcome FROM producer_inbox ORDER BY id"
                )
            ]
        self.assertEqual(outcomes, ["accepted", "duplicate"])

    def test_crash_after_commit_before_unlink_replays_without_second_event(self) -> None:
        self.enqueue(ring_payload())
        real_unlink = home_events._durable_unlink
        with mock.patch.object(
            home_events,
            "_durable_unlink",
            side_effect=home_events.StateError("spool_cleanup_failed"),
        ):
            first = self.ingest()
        self.assertEqual(first.accepted, 1)
        self.assertEqual(first.cleanup_pending, 1)
        self.assertEqual(len(list(self.paths.source_spool("ring").glob("*.ready"))), 1)
        with mock.patch.object(home_events, "_durable_unlink", side_effect=real_unlink):
            second = self.ingest()
        self.assertEqual(second.duplicate, 1)
        with self.connection() as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM events").fetchone()[0], 1)
            self.assertEqual(
                connection.execute("SELECT COUNT(*) FROM producer_inbox").fetchone()[0],
                1,
            )

    def test_tampered_spool_becomes_metadata_only_dead_letter(self) -> None:
        event = self.enqueue(ring_payload())
        path = next(self.paths.source_spool("ring").glob("*.ready"))
        record = json.loads(path.read_text())
        record["event_uid"] = "evt_" + ("0" * 32)
        path.write_text(json.dumps(record), encoding="utf-8")
        os.chmod(path, 0o600)
        result = self.ingest()
        self.assertEqual(result.dead_letter, 1)
        with self.connection() as connection:
            inbox = connection.execute("SELECT * FROM producer_inbox").fetchone()
            count = connection.execute("SELECT COUNT(*) FROM events").fetchone()[0]
        self.assertEqual(inbox["outcome"], "dead_letter")
        self.assertEqual(inbox["error_code"], "spool_integrity")
        self.assertIsNone(inbox["event_uid"])
        self.assertEqual(count, 0)
        self.assertNotIn(event.dedupe_key, json.dumps(dict(inbox)))
        self.assertEqual(self.store.status_snapshot()["health"], "degraded")

    def test_source_directory_mismatch_is_dead_lettered(self) -> None:
        self.enqueue(ring_payload())
        original = next(self.paths.source_spool("ring").glob("*.ready"))
        moved = self.paths.source_spool("presence") / original.name
        original.replace(moved)
        result = self.ingest()
        self.assertEqual(result.dead_letter, 1)
        with self.connection() as connection:
            row = connection.execute("SELECT source, error_code FROM producer_inbox").fetchone()
        self.assertEqual(tuple(row), ("presence", "source_directory_mismatch"))

    def test_single_ingester_lock_preserves_ready_work(self) -> None:
        self.enqueue(ring_payload())
        descriptor = os.open(self.paths.ingest_lock, os.O_RDWR)
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            with self.assertRaisesRegex(home_events.StateError, "ingester_busy"):
                self.ingest()
        finally:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
            os.close(descriptor)
        self.assertEqual(len(list(self.paths.source_spool("ring").glob("*.ready"))), 1)
        self.assertEqual(self.store.status_snapshot()["counts"]["spool_ready"], 1)

    def test_status_projection_failure_does_not_undo_database_commit(self) -> None:
        self.enqueue(ring_payload())
        with mock.patch.object(home_events, "_atomic_write", side_effect=OSError("full")):
            result = self.ingest()
        self.assertEqual(result.accepted, 1)
        with self.connection() as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM events").fetchone()[0], 1)


class ConsumerAndQueryTests(HomeEventTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.event = self.enqueue(ring_payload())
        self.ingest()

    def test_claim_acknowledge_and_expired_reclaim_are_lease_safe(self) -> None:
        claimed = self.store.claim_deliveries("correlator", lease_seconds=120)
        self.assertEqual(len(claimed["deliveries"]), 1)
        delivery_id = claimed["deliveries"][0]["delivery_id"]
        with self.assertRaisesRegex(home_events.StateError, "delivery_lease_mismatch"):
            self.store.acknowledge_delivery(
                "correlator", delivery_id, "lease_" + ("0" * 32)
            )
        self.store.acknowledge_delivery(
            "correlator", delivery_id, claimed["lease_token"]
        )
        self.assertEqual(
            self.store.claim_deliveries("correlator")["deliveries"], []
        )

        second = self.enqueue(
            ring_payload(source_event_id="another-event", event_type="entry.doorbell_rang")
        )
        self.ingest()
        lease = self.store.claim_deliveries("correlator", lease_seconds=15)
        self.assertEqual(lease["deliveries"][0]["event_uid"], second.event_uid)
        blocked = self.store.claim_deliveries("correlator", lease_seconds=15)
        self.assertEqual(blocked["deliveries"], [])
        with self.connection() as connection:
            connection.execute(
                """
                UPDATE consumer_deliveries
                SET lease_until='2026-07-12T14:00:00Z'
                WHERE status='leased'
                """
            )
        reclaimed = self.store.claim_deliveries("correlator", lease_seconds=15)
        self.assertEqual(len(reclaimed["deliveries"]), 1)
        self.assertNotEqual(reclaimed["lease_token"], lease["lease_token"])

    def test_consumer_dead_letter_degrades_health(self) -> None:
        claimed = self.store.claim_deliveries("correlator")
        delivery_id = claimed["deliveries"][0]["delivery_id"]
        self.store.dead_letter_delivery(
            "correlator", delivery_id, claimed["lease_token"], "correlation_failed"
        )
        with self.connection() as connection:
            row = connection.execute(
                "SELECT status, error_code FROM consumer_deliveries"
            ).fetchone()
        self.assertEqual(tuple(row), ("dead_letter", "correlation_failed"))
        self.assertEqual(self.store.status_snapshot()["health"], "degraded")

    def test_august_health_transition_is_explicit_and_recovers(self) -> None:
        unavailable = {
            "source_event_id": "august-health-unavailable",
            "event_type": "source.unavailable",
            "site": "crosstown",
            "entity_kind": "adapter",
            "entity_alias": "front_door",
            "occurred_at": NOW,
            "observed_at": NOW,
            "time_precision": "observed_interval",
            "attributes": {"failure_count": 3, "reason_code": "observe_failed"},
        }
        self.enqueue(unavailable, source="august")
        self.ingest()
        status = self.store.status_snapshot()["sources"]["august"]
        self.assertEqual(status["health"], "degraded")
        self.assertEqual(status["consecutive_failures"], 3)
        self.assertEqual(status["last_error_code"], "observe_failed")

        recovered = {
            **unavailable,
            "source_event_id": "august-health-recovered",
            "event_type": "source.recovered",
            "attributes": {"outage_seconds": 600},
        }
        self.enqueue(recovered, source="august")
        self.ingest()
        status = self.store.status_snapshot()["sources"]["august"]
        self.assertEqual(status["health"], "ok")
        self.assertEqual(status["consecutive_failures"], 0)
        self.assertIsNone(status["last_error_code"])

    def test_recent_filters_and_exposes_only_normalized_fields(self) -> None:
        result = self.store.recent(
            since="2026-07-12T14:00:00Z",
            limit=20,
            site="crosstown",
            event_type="entry.person_detected",
        )
        self.assertEqual(len(result["events"]), 1)
        encoded = json.dumps(result)
        self.assertIn(self.event.event_uid, encoded)
        self.assertNotIn("dedupe_key", encoded)
        self.assertNotIn("private-ring", encoded)
        empty = self.store.recent(
            since="2026-07-12T14:00:00Z", limit=20, site="cabin"
        )
        self.assertEqual(empty["events"], [])

    def test_incident_list_and_explain_return_structured_evidence(self) -> None:
        with self.connection() as connection:
            event_id = connection.execute("SELECT id FROM events").fetchone()[0]
            cursor = connection.execute(
                """
                INSERT INTO incidents(
                    incident_uid, site, state, category, summary_code,
                    opened_at, updated_at
                ) VALUES (?, 'crosstown', 'open', 'entry_activity',
                          'unexplained_vacant_activity', ?, ?)
                """,
                ("inc_" + ("a" * 32), NOW, NOW),
            )
            connection.execute(
                """
                INSERT INTO incident_events(incident_id, event_id, relation, created_at)
                VALUES (?, ?, 'trigger', ?)
                """,
                (cursor.lastrowid, event_id, NOW),
            )
            connection.execute(
                """
                INSERT INTO incident_decisions(
                    incident_id, status, reason_code, created_at
                ) VALUES (?, 'shadowed', 'vacant_activity_shadowed', ?)
                """,
                (cursor.lastrowid, NOW),
            )
        listed = self.store.incidents(
            since="2026-07-12T14:00:00Z", state="open", site="crosstown"
        )
        self.assertEqual(listed["incidents"][0]["event_count"], 1)
        explained = self.store.explain("inc_" + ("a" * 32))
        self.assertEqual(explained["incident"]["summary_code"], "unexplained_vacant_activity")
        self.assertEqual(explained["events"][0]["relation"], "trigger")
        self.assertEqual(
            explained["decisions"],
            [
                {
                    "status": "shadowed",
                    "reason_code": "vacant_activity_shadowed",
                    "created_at": NOW,
                }
            ],
        )
        with self.assertRaisesRegex(home_events.StateError, "incident_not_found"):
            self.store.explain("inc_" + ("b" * 32))


class RetentionAndCliTests(HomeEventTestCase):
    def test_prune_keeps_pending_work_then_removes_acknowledged_old_event(self) -> None:
        self.enqueue(ring_payload())
        self.ingest()
        old = "2026-05-01T00:00:00Z"
        with self.connection() as connection:
            connection.execute("UPDATE events SET created_at = ?", (old,))
            connection.execute("UPDATE producer_inbox SET received_at = ?", (old,))
        self.store.prune(checkpoint=False)
        with self.connection() as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM events").fetchone()[0], 1)
            connection.execute(
                """
                UPDATE consumer_deliveries SET status='acknowledged', updated_at=?,
                    lease_token=NULL, lease_until=NULL
                """,
                (old,),
            )
        deleted = self.store.prune(checkpoint=False)
        self.assertEqual(deleted["consumer_deliveries"], 1)
        self.assertEqual(deleted["events"], 1)
        self.assertEqual(deleted["producer_inbox"], 1)

    def test_reserved_notification_retains_resolved_incident(self) -> None:
        old = "2026-01-01T00:00:00Z"
        with self.connection() as connection:
            cursor = connection.execute(
                """
                INSERT INTO incidents(
                    incident_uid, site, state, category, summary_code,
                    opened_at, updated_at
                ) VALUES (?, 'cabin', 'resolved', 'door', 'door_open', ?, ?)
                """,
                ("inc_" + ("c" * 32), old, old),
            )
            connection.execute(
                """
                INSERT INTO notification_outbox(
                    incident_id, site, status, created_at, updated_at
                ) VALUES (?, 'cabin', 'reserved', ?, ?)
                """,
                (cursor.lastrowid, old, old),
            )
        self.store.prune(checkpoint=False)
        with self.connection() as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM incidents").fetchone()[0], 1)
            self.assertEqual(
                connection.execute("SELECT COUNT(*) FROM notification_outbox").fetchone()[0],
                1,
            )

    def test_parser_keeps_root_override_operator_only_and_json_errors_safe(self) -> None:
        parser = home_events.build_parser()
        operator = parser.parse_args(
            ["operator", "--root", str(self.root), "status"]
        )
        self.assertEqual(operator.root, self.root)
        with contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit):
                parser.parse_args(["agent", "--root", str(self.root), "status"])

        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr):
            code = home_events.main(
                ["operator", "--root", "relative", "check-config"]
            )
        self.assertEqual(code, 2)
        self.assertEqual(json.loads(stderr.getvalue())["error"], "root_not_absolute")


if __name__ == "__main__":
    unittest.main()
