#!/usr/bin/env python3
"""Integration tests for the shadow-only home-event correlator."""

from __future__ import annotations

import importlib.util
import json
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
BIN_DIR = REPO_ROOT / "openclaw" / "bin"


def load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


bus = load_module("home_event_bus", BIN_DIR / "home_event_bus.py")
correlator = load_module(
    "home_event_correlator", BIN_DIR / "home-event-correlator.py"
)

LOCAL_PRESENCE_EVENT_TYPES = (
    "presence.local_departure_inferred",
    "presence.local_arrival_observed",
    "presence.household_excursion_started",
    "presence.household_excursion_ended",
)
LOCAL_PRESENCE_COUNTERS = {
    event_type: event_type.removeprefix("presence.").replace(".", "_") + "_shadowed"
    for event_type in LOCAL_PRESENCE_EVENT_TYPES
}


class HomeEventCorrelatorTests(unittest.TestCase):
    NOW = "2026-07-12T15:00:00Z"

    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tempdir.cleanup)
        self.root = Path(self.tempdir.name) / "home-events"
        self.presence = Path(self.tempdir.name) / "presence-state.json"
        self.clock = lambda: self.NOW
        bus.initialize_runtime(self.root, clock=self.clock)
        self.write_presence("confirmed_vacant", "confirmed_vacant")

    def write_presence(self, cabin: str, crosstown: str, *, fresh: bool = True) -> None:
        self.presence.write_text(
            json.dumps(
                {
                    "timestamp": self.NOW,
                    "cabin": {"occupancy": cabin, "fresh": fresh},
                    "crosstown": {"occupancy": crosstown, "fresh": fresh},
                }
            ),
            encoding="utf-8",
        )
        self.presence.chmod(0o600)

    def enqueue(
        self,
        source: str,
        event_type: str,
        *,
        site: str = "cabin",
        occurred_at: str | None = None,
        observed_at: str = "2026-07-12T14:58:00Z",
        attributes: dict | None = None,
        sequence: str = "1",
        precision: str | None = None,
    ) -> None:
        entity_kind = {
            "ring": "doorbell",
            "presence": "person"
            if event_type
            in {
                "presence.person_relocated",
                "presence.local_departure_inferred",
                "presence.local_arrival_observed",
            }
            else "site",
            "august": "door"
            if event_type.startswith("door.")
            else "battery"
            if event_type.startswith("device.")
            else "adapter"
            if event_type.startswith("source.")
            else "lock",
            "nest": "camera",
            "whisker": "litter_box",
        }[source]
        time_precision = {
            "ring": "source",
            "presence": "evaluation",
            "august": "observed_interval",
            "nest": "source",
            "whisker": "source",
        }
        payload = {
            "schema_version": 1,
            "source_event_id": f"fixture-{source}-{event_type}-{site}-{sequence}",
            "event_type": event_type,
            "site": site,
            "entity_kind": entity_kind,
            "entity_alias": "kitchen"
            if source == "nest"
            else f"{site}_litter_robot"
            if source == "whisker"
            else "front_door"
            if source != "presence"
            else "dylan"
            if entity_kind == "person"
            else site,
            "occurred_at": occurred_at or observed_at,
            "observed_at": observed_at,
            "time_precision": precision or time_precision[source],
            "attributes": attributes
            if attributes is not None
            else (
                {"classification": "person"}
                if event_type in {"entry.person_detected", "camera.person_detected"}
                else {"classification": "motion"}
                if event_type in {"entry.motion_detected", "camera.motion_detected"}
                else {"classification": "cat_detected"}
                if event_type == "pet.litter_box_activity"
                else {}
            ),
        }
        bus.enqueue_event(
            self.root,
            source,
            json.dumps(payload).encode("utf-8"),
            clock=self.clock,
        )

    def local_presence_attributes(
        self, event_type: str, sequence: str
    ) -> dict:
        common = {
            "evidence_at": "2026-07-12T14:59:45Z",
            "not_before": "2026-07-12T14:20:00Z",
            "not_after": "2026-07-12T14:59:45Z",
            "state_hash": sequence[0] * 64,
        }
        if event_type == "presence.local_departure_inferred":
            return {
                **common,
                "person_alias": "dylan",
                "confidence": "network_inference",
                "distinct_observations": 3,
                "observation_span_seconds": 2385,
            }
        if event_type == "presence.local_arrival_observed":
            return {
                **common,
                "not_before": common["not_after"],
                "person_alias": "dylan",
                "confidence": "positive_detection",
                "distinct_observations": 1,
                "observation_span_seconds": 0,
            }
        excursion = {
            **common,
            "people_count": 2,
            "excursion_id": f"exc_{sequence[0] * 32}",
        }
        if event_type == "presence.household_excursion_started":
            return {**excursion, "confidence": "network_inference"}
        if event_type == "presence.household_excursion_ended":
            return {
                **excursion,
                "confidence": "positive_detection",
                "outcome": "resident_returned",
            }
        raise AssertionError(f"unsupported fixture event type: {event_type}")

    def ingest(self) -> None:
        bus.ingest_once(self.root, clock=self.clock)

    def run_correlator(self):
        return correlator.ShadowCorrelator(
            self.root, self.presence, clock=self.clock
        ).run_once()

    def enable_limited_delivery(self, *, camera: bool = False) -> None:
        policy = {
            "schema_version": 3 if camera else 1,
            "active": True,
            "sites": ["cabin", "crosstown"],
            "incident_classes": [
                "person_activity",
                "access_activity",
                "person_and_access",
            ],
            "recipient_routes": ["dylan"],
            "arrival_grace_seconds": 900,
            "cooldown_seconds": 3600,
            "reservation_ttl_seconds": 300,
            "unresolved_access_escalation_seconds": 1800,
            "camera_enabled": camera,
        }
        if camera:
            policy.update(
                {
                    "camera_bindings": {
                        "nest": {
                            "cabin": "Kitchen",
                            "crosstown": "Living Room Wired",
                        },
                        "ring": {
                            "cabin": ["driveway", "front_door"],
                            "crosstown": ["front_door"],
                        },
                    },
                    "camera_snapshot_offsets_seconds": [30, 60],
                    "camera_result_mode": "structured_text",
                }
            )
        paths = bus.RuntimePaths(self.root)
        bus.install_delivery_policy(paths, json.dumps(policy).encode("utf-8"))
        bus.EventStore(paths, clock=self.clock).set_runtime_mode("limited_delivery")

    def rows(self, query: str) -> list[sqlite3.Row]:
        connection = sqlite3.connect(self.root / "state" / "events.sqlite3")
        connection.row_factory = sqlite3.Row
        try:
            return connection.execute(query).fetchall()
        finally:
            connection.close()

    def execute(self, script: str) -> None:
        connection = sqlite3.connect(self.root / "state" / "events.sqlite3")
        try:
            connection.executescript(script)
            connection.commit()
        finally:
            connection.close()

    def incident_projection(self) -> dict[str, list[tuple]]:
        return {
            "incidents": [
                tuple(row) for row in self.rows("SELECT * FROM incidents ORDER BY id")
            ],
            "incident_events": [
                tuple(row)
                for row in self.rows(
                    "SELECT * FROM incident_events ORDER BY incident_id, event_id"
                )
            ],
            "incident_decisions": [
                tuple(row)
                for row in self.rows("SELECT * FROM incident_decisions ORDER BY id")
            ],
            "notification_outbox": [
                tuple(row)
                for row in self.rows("SELECT * FROM notification_outbox ORDER BY id")
            ],
        }

    def test_vacant_ring_activity_waits_fifteen_minutes_for_arrival(self) -> None:
        self.enqueue("ring", "entry.person_detected")
        self.ingest()

        first = self.run_correlator()
        before_grace = self.run_correlator()
        self.NOW = "2026-07-12T15:13:00Z"
        self.write_presence("confirmed_vacant", "confirmed_vacant")
        after_grace = self.run_correlator()

        self.assertEqual(first["acknowledged"], 1)
        self.assertEqual(first["shadow_decisions"], 0)
        self.assertEqual(before_grace["claimed"], 0)
        self.assertEqual(before_grace["shadow_decisions"], 0)
        self.assertEqual(after_grace["shadow_decisions"], 1)
        incidents = self.rows("SELECT * FROM incidents")
        self.assertEqual(len(incidents), 1)
        self.assertEqual(incidents[0]["summary_code"], "vacant_activity_shadowed")
        self.assertEqual(
            len(self.rows("SELECT * FROM notification_outbox WHERE status='shadowed'")),
            1,
        )

    def test_limited_mode_reserves_owner_only_person_alert_after_grace(self) -> None:
        self.enable_limited_delivery()
        self.enqueue("ring", "entry.person_detected")
        self.ingest()

        first = self.run_correlator()
        self.NOW = "2026-07-12T15:13:00Z"
        self.write_presence("confirmed_vacant", "confirmed_vacant")
        after_grace = self.run_correlator()

        self.assertEqual(first["reservations"], 0)
        self.assertEqual(after_grace["mode"], "limited_delivery")
        self.assertEqual(after_grace["reservations"], 1)
        rows = self.rows(
            """
            SELECT status, recipient_route, template_code, attempt_count,
                   reservation_token, reserved_until
            FROM notification_outbox
            """
        )
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["status"], "reserved")
        self.assertEqual(rows[0]["recipient_route"], "dylan")
        self.assertEqual(rows[0]["template_code"], "person_activity")
        self.assertEqual(rows[0]["attempt_count"], 0)
        self.assertRegex(rows[0]["reservation_token"], r"^res_[0-9a-f]{32}$")
        self.assertEqual(rows[0]["reserved_until"], "2026-07-12T15:18:00Z")

    def test_camera_evidence_is_scheduled_early_and_attached_only_as_context(self) -> None:
        self.enable_limited_delivery(camera=True)
        self.enqueue(
            "ring",
            "entry.person_detected",
            occurred_at=self.NOW,
            observed_at=self.NOW,
        )
        self.ingest()

        opened = self.run_correlator()
        self.assertEqual(opened["reservations"], 0)
        evaluations = self.rows("SELECT * FROM camera_evaluations")
        self.assertEqual(len(evaluations), 1)
        self.assertEqual(evaluations[0]["camera_alias"], "Kitchen")
        self.assertEqual(evaluations[0]["due_30_at"], "2026-07-12T15:00:30Z")
        self.assertEqual(evaluations[0]["due_60_at"], "2026-07-12T15:01:00Z")
        self.assertEqual(self.rows("SELECT * FROM notification_outbox"), [])

        self.execute(
            """
            UPDATE camera_evaluations
            SET state='complete', snapshot_30_result='clear',
                snapshot_60_result='clear', result='no_person_visible',
                completed_at='2026-07-12T15:01:00Z',
                updated_at='2026-07-12T15:01:00Z';
            """
        )
        self.NOW = "2026-07-12T15:15:01Z"
        self.write_presence("confirmed_vacant", "confirmed_vacant")
        reserved = self.run_correlator()

        self.assertEqual(reserved["reservations"], 1)
        outbox = self.rows("SELECT * FROM notification_outbox")[0]
        self.assertEqual(outbox["camera_evaluation_id"], evaluations[0]["id"])
        self.assertEqual(outbox["camera_result"], "no_person_visible")

    def test_camera_never_schedules_without_confirmed_vacancy(self) -> None:
        self.enable_limited_delivery(camera=True)
        self.write_presence("occupied", "confirmed_vacant")
        self.enqueue(
            "ring",
            "entry.person_detected",
            occurred_at=self.NOW,
            observed_at=self.NOW,
        )
        self.ingest()
        self.run_correlator()
        self.assertEqual(self.rows("SELECT * FROM camera_evaluations"), [])

    def test_arrival_during_fifteen_minute_grace_resolves_silently(self) -> None:
        self.enqueue("ring", "entry.person_detected", sequence="ring")
        self.ingest()
        opened = self.run_correlator()
        self.assertEqual(opened["shadow_decisions"], 0)

        self.NOW = "2026-07-12T15:06:00Z"
        self.write_presence("occupied", "confirmed_vacant")
        self.enqueue(
            "presence",
            "presence.occupancy_changed",
            observed_at="2026-07-12T15:06:00Z",
            attributes={
                "previous": "confirmed_vacant",
                "current": "occupied",
                "confidence": "canonical",
                "evidence_at": "2026-07-12T15:06:00Z",
                "state_hash": "d" * 64,
            },
            sequence="arrival",
        )
        self.ingest()

        arrived = self.run_correlator()

        self.assertEqual(arrived["shadow_decisions"], 0)
        incident = self.rows("SELECT * FROM incidents")[0]
        self.assertEqual(incident["state"], "resolved")
        self.assertEqual(incident["summary_code"], "resident_arrival_silent")
        self.assertEqual(self.rows("SELECT * FROM notification_outbox"), [])

    def test_fresh_arrival_within_batch_resolves_silently(self) -> None:
        self.write_presence("occupied", "confirmed_vacant")
        self.enqueue("ring", "entry.person_detected", sequence="ring")
        self.enqueue(
            "presence",
            "presence.occupancy_changed",
            observed_at="2026-07-12T14:59:00Z",
            attributes={
                "previous": "confirmed_vacant",
                "current": "occupied",
                "confidence": "canonical",
                "evidence_at": "2026-07-12T14:59:00Z",
                "state_hash": "a" * 64,
            },
            sequence="presence",
        )
        self.ingest()

        result = self.run_correlator()

        self.assertEqual(result["acknowledged"], 2)
        incident = self.rows("SELECT * FROM incidents")[0]
        self.assertEqual(incident["state"], "resolved")
        self.assertEqual(incident["summary_code"], "resident_arrival_silent")
        self.assertEqual(self.rows("SELECT * FROM notification_outbox"), [])

    def test_uncertain_presence_never_creates_shadow_delivery(self) -> None:
        self.write_presence("possibly_vacant", "confirmed_vacant")
        self.enqueue("ring", "entry.doorbell_rang")
        self.ingest()

        result = self.run_correlator()

        self.assertEqual(result["shadow_decisions"], 0)
        incident = self.rows("SELECT * FROM incidents")[0]
        self.assertEqual(incident["summary_code"], "presence_uncertain_shadowed")
        self.assertEqual(self.rows("SELECT * FROM notification_outbox"), [])

    def test_occupied_decision_remains_terminal_after_site_becomes_vacant(self) -> None:
        self.write_presence("occupied", "confirmed_vacant")
        self.enqueue("ring", "entry.person_detected")
        self.ingest()

        first = self.run_correlator()
        self.write_presence("confirmed_vacant", "confirmed_vacant")
        second = self.run_correlator()

        self.assertEqual(first["shadow_decisions"], 0)
        self.assertEqual(second["shadow_decisions"], 0)
        incident = self.rows("SELECT * FROM incidents")[0]
        self.assertEqual(incident["summary_code"], "occupied_activity_shadowed")
        decisions = self.rows(
            "SELECT status, reason_code FROM incident_decisions ORDER BY id"
        )
        self.assertEqual(
            [tuple(row) for row in decisions],
            [("suppressed", "occupied_activity_shadowed")],
        )
        self.assertEqual(self.rows("SELECT * FROM notification_outbox"), [])

    def test_vacancy_reopens_unresolved_access_under_a_fresh_grace(self) -> None:
        self.enable_limited_delivery()
        self.write_presence("confirmed_vacant", "occupied")
        self.enqueue(
            "august",
            "lock.unlocked",
            site="crosstown",
            observed_at=self.NOW,
            attributes={
                "previous": "locked",
                "current": "unlocked",
                "not_before": "2026-07-12T14:55:00Z",
                "not_after": self.NOW,
            },
            sequence="occupied-unlock",
        )
        self.ingest()
        self.run_correlator()
        self.assertEqual(
            [tuple(row) for row in self.rows(
                "SELECT status, reason_code FROM incident_decisions ORDER BY id"
            )],
            [("suppressed", "occupied_activity_shadowed")],
        )

        self.NOW = "2026-07-12T15:05:00Z"
        self.write_presence("confirmed_vacant", "confirmed_vacant")
        self.enqueue(
            "presence",
            "presence.occupancy_changed",
            site="crosstown",
            observed_at=self.NOW,
            attributes={
                "previous": "occupied",
                "current": "confirmed_vacant",
                "confidence": "canonical",
                "evidence_at": self.NOW,
                "state_hash": "e" * 64,
            },
            sequence="vacancy",
        )
        self.ingest()
        transitioned = self.run_correlator()

        self.assertEqual(transitioned["reservations"], 0)
        incidents = self.rows("SELECT * FROM incidents ORDER BY id")
        self.assertEqual(len(incidents), 2)
        self.assertEqual(incidents[0]["state"], "resolved")
        self.assertEqual(incidents[0]["summary_code"], "access_carried_into_vacancy")
        self.assertEqual(incidents[1]["state"], "open")
        self.assertEqual(incidents[1]["summary_code"], "vacant_access_pending")

        self.NOW = "2026-07-12T15:20:00Z"
        self.write_presence("confirmed_vacant", "confirmed_vacant")
        alerted = self.run_correlator()

        self.assertEqual(alerted["reservations"], 1)
        outbox = self.rows("SELECT * FROM notification_outbox")
        self.assertEqual(len(outbox), 1)
        self.assertEqual(outbox[0]["incident_id"], incidents[1]["id"])
        self.assertEqual(outbox[0]["template_code"], "access_activity")
        counter = self.rows(
            "SELECT value FROM service_counters WHERE name='vacancy_access_reopened'"
        )
        self.assertEqual([row["value"] for row in counter], [1])

    def test_locking_carried_access_during_grace_resolves_without_alert(self) -> None:
        self.enable_limited_delivery()
        self.write_presence("confirmed_vacant", "occupied")
        self.enqueue(
            "august",
            "lock.unlocked",
            site="crosstown",
            observed_at=self.NOW,
            attributes={
                "previous": "locked",
                "current": "unlocked",
                "not_before": "2026-07-12T14:55:00Z",
                "not_after": self.NOW,
            },
            sequence="occupied-unlock",
        )
        self.ingest()
        self.run_correlator()

        self.NOW = "2026-07-12T15:05:00Z"
        self.write_presence("confirmed_vacant", "confirmed_vacant")
        self.enqueue(
            "presence",
            "presence.occupancy_changed",
            site="crosstown",
            observed_at=self.NOW,
            attributes={
                "previous": "occupied",
                "current": "confirmed_vacant",
                "confidence": "canonical",
                "evidence_at": self.NOW,
                "state_hash": "f" * 64,
            },
            sequence="vacancy",
        )
        self.ingest()
        self.run_correlator()

        self.NOW = "2026-07-12T15:10:00Z"
        self.write_presence("confirmed_vacant", "confirmed_vacant")
        self.enqueue(
            "august",
            "lock.locked",
            site="crosstown",
            observed_at=self.NOW,
            attributes={
                "previous": "unlocked",
                "current": "locked",
                "not_before": "2026-07-12T15:05:00Z",
                "not_after": self.NOW,
            },
            sequence="locked",
        )
        self.ingest()
        self.run_correlator()

        self.NOW = "2026-07-12T15:20:00Z"
        self.write_presence("confirmed_vacant", "confirmed_vacant")
        final = self.run_correlator()
        incidents = self.rows("SELECT * FROM incidents ORDER BY id")

        self.assertEqual(final["reservations"], 0)
        self.assertEqual(incidents[1]["state"], "resolved")
        self.assertEqual(incidents[1]["summary_code"], "access_resolved_silently")
        self.assertEqual(self.rows("SELECT * FROM notification_outbox"), [])

    def test_uncertain_decision_remains_terminal_after_site_becomes_vacant(self) -> None:
        self.write_presence("possibly_vacant", "confirmed_vacant")
        self.enqueue("ring", "entry.doorbell_rang")
        self.ingest()

        first = self.run_correlator()
        self.write_presence("confirmed_vacant", "confirmed_vacant")
        second = self.run_correlator()

        self.assertEqual(first["shadow_decisions"], 0)
        self.assertEqual(second["shadow_decisions"], 0)
        incident = self.rows("SELECT * FROM incidents")[0]
        self.assertEqual(incident["summary_code"], "presence_uncertain_shadowed")
        decisions = self.rows(
            "SELECT status, reason_code FROM incident_decisions ORDER BY id"
        )
        self.assertEqual(
            [tuple(row) for row in decisions],
            [("suppressed", "presence_uncertain_shadowed")],
        )
        self.assertEqual(self.rows("SELECT * FROM notification_outbox"), [])

    def test_rate_limited_decision_remains_terminal_after_window_expires(self) -> None:
        self.enqueue(
            "ring",
            "entry.person_detected",
            site="crosstown",
            sequence="first",
        )
        self.ingest()
        self.run_correlator()

        self.NOW = "2026-07-12T15:13:00Z"
        self.write_presence("confirmed_vacant", "confirmed_vacant")
        first_shadow = self.run_correlator()
        self.assertEqual(first_shadow["shadow_decisions"], 1)

        self.write_presence("confirmed_vacant", "occupied")
        self.enqueue(
            "presence",
            "presence.occupancy_changed",
            site="crosstown",
            observed_at="2026-07-12T15:13:00Z",
            attributes={
                "previous": "confirmed_vacant",
                "current": "occupied",
                "confidence": "canonical",
                "evidence_at": "2026-07-12T15:13:00Z",
                "state_hash": "c" * 64,
            },
            sequence="arrival",
        )
        self.ingest()
        self.run_correlator()

        self.NOW = "2026-07-12T15:15:00Z"
        self.write_presence("confirmed_vacant", "confirmed_vacant")
        self.enqueue(
            "august",
            "lock.unlocked",
            site="crosstown",
            observed_at="2026-07-12T15:15:00Z",
            attributes={
                "previous": "locked",
                "current": "unlocked",
                "not_before": "2026-07-12T15:13:00Z",
                "not_after": "2026-07-12T15:15:00Z",
            },
            sequence="second",
        )
        self.ingest()
        pending_grace = self.run_correlator()

        self.NOW = "2026-07-12T15:30:00Z"
        self.write_presence("confirmed_vacant", "confirmed_vacant")
        rate_limited = self.run_correlator()

        self.NOW = "2026-07-12T16:30:00Z"
        self.write_presence("confirmed_vacant", "confirmed_vacant")
        after_window = self.run_correlator()

        self.assertEqual(pending_grace["shadow_decisions"], 0)
        self.assertEqual(rate_limited["shadow_decisions"], 0)
        self.assertEqual(after_window["shadow_decisions"], 0)
        incidents = self.rows("SELECT * FROM incidents ORDER BY id")
        self.assertEqual(incidents[1]["summary_code"], "rate_limited_shadowed")
        decisions = self.rows(
            """
            SELECT status, reason_code FROM incident_decisions
            WHERE status = 'rate_limited' ORDER BY id
            """
        )
        self.assertEqual(
            [tuple(row) for row in decisions],
            [("rate_limited", "rate_limited_shadowed")],
        )
        self.assertEqual(len(self.rows("SELECT * FROM notification_outbox")), 1)

    def test_generic_ring_motion_is_stored_but_not_actionable(self) -> None:
        self.enqueue("ring", "entry.motion_detected")
        self.ingest()

        result = self.run_correlator()

        self.assertEqual(result["acknowledged"], 1)
        self.assertEqual(self.rows("SELECT * FROM incidents"), [])
        self.assertEqual(self.rows("SELECT * FROM notification_outbox"), [])

    def test_whisker_litter_activity_is_acknowledged_as_transfer_evidence(self) -> None:
        self.enqueue(
            "whisker",
            "pet.litter_box_activity",
            site="crosstown",
        )
        self.ingest()

        result = self.run_correlator()

        self.assertEqual(result["acknowledged"], 1)
        self.assertEqual(result["dead_lettered"], 0)
        self.assertEqual(self.rows("SELECT * FROM incidents"), [])
        counter = self.rows(
            "SELECT value FROM service_counters "
            "WHERE name='whisker_litter_events_observed'"
        )
        self.assertEqual([row["value"] for row in counter], [1])

    def test_repaired_whisker_dead_letter_replay_clears_sticky_bus_fault(self) -> None:
        self.enqueue(
            "whisker",
            "pet.litter_box_activity",
            site="crosstown",
        )
        self.ingest()
        store = bus.EventStore(bus.validate_runtime(self.root), clock=self.clock)
        claimed = store.claim_deliveries("correlator")
        delivery_id = claimed["deliveries"][0]["delivery_id"]
        store.dead_letter_delivery(
            "correlator",
            delivery_id,
            claimed["lease_token"],
            "correlation_failed",
        )
        store.retry_consumer_dead_letter("correlator", delivery_id)

        result = self.run_correlator()

        self.assertEqual(result["acknowledged"], 1)
        status = store.status_snapshot()
        self.assertEqual(status["health"], "ok")
        self.assertIsNone(status["last_error_code"])
        self.assertEqual(status["counts"]["dead_letters"], 0)

    def test_local_presence_events_are_journal_only_without_incident(self) -> None:
        for index, event_type in enumerate(LOCAL_PRESENCE_EVENT_TYPES, start=1):
            with self.subTest(event_type=event_type):
                sequence = str(index)
                before = self.incident_projection()
                self.enqueue(
                    "presence",
                    event_type,
                    observed_at="2026-07-12T14:59:45Z",
                    attributes=self.local_presence_attributes(event_type, sequence),
                    sequence=sequence,
                    precision="observed_interval",
                )
                self.ingest()

                result = self.run_correlator()

                self.assertEqual(result["acknowledged"], 1)
                self.assertEqual(result["shadow_decisions"], 0)
                self.assertEqual(self.incident_projection(), before)
                counter = self.rows(
                    "SELECT value FROM service_counters WHERE name = "
                    f"'{LOCAL_PRESENCE_COUNTERS[event_type]}'"
                )
                self.assertEqual([row["value"] for row in counter], [1])

    def test_local_presence_events_do_not_change_open_activity_incident(self) -> None:
        self.write_presence("occupied", "confirmed_vacant")
        self.enqueue(
            "ring",
            "entry.person_detected",
            observed_at="2026-07-12T14:59:30Z",
            sequence="open-incident",
        )
        self.ingest()
        opened = self.run_correlator()
        self.assertEqual(opened["acknowledged"], 1)
        self.assertEqual(self.rows("SELECT state FROM incidents")[0]["state"], "open")

        for index, event_type in enumerate(LOCAL_PRESENCE_EVENT_TYPES, start=5):
            with self.subTest(event_type=event_type):
                sequence = str(index)
                before = self.incident_projection()
                self.enqueue(
                    "presence",
                    event_type,
                    observed_at="2026-07-12T14:59:45Z",
                    attributes=self.local_presence_attributes(event_type, sequence),
                    sequence=sequence,
                    precision="observed_interval",
                )
                self.ingest()

                result = self.run_correlator()

                self.assertEqual(result["acknowledged"], 1)
                self.assertEqual(result["shadow_decisions"], 0)
                self.assertEqual(self.incident_projection(), before)
                counter = self.rows(
                    "SELECT value FROM service_counters WHERE name = "
                    f"'{LOCAL_PRESENCE_COUNTERS[event_type]}'"
                )
                self.assertEqual([row["value"] for row in counter], [1])

    def test_nest_person_joins_ring_site_activity_incident(self) -> None:
        self.enqueue("ring", "entry.person_detected", sequence="ring")
        self.enqueue("nest", "camera.person_detected", sequence="nest")
        self.ingest()

        result = self.run_correlator()
        self.NOW = "2026-07-12T15:13:00Z"
        self.write_presence("confirmed_vacant", "confirmed_vacant")
        after_grace = self.run_correlator()

        self.assertEqual(result["acknowledged"], 2)
        self.assertEqual(result["shadow_decisions"], 0)
        self.assertEqual(after_grace["shadow_decisions"], 1)
        incidents = self.rows("SELECT * FROM incidents")
        self.assertEqual(len(incidents), 1)
        self.assertEqual(
            len(self.rows("SELECT * FROM incident_events WHERE incident_id = 1")),
            2,
        )

    def test_nest_motion_is_journaled_but_not_actionable(self) -> None:
        self.enqueue("nest", "camera.motion_detected")
        self.ingest()

        result = self.run_correlator()

        self.assertEqual(result["acknowledged"], 1)
        self.assertEqual(self.rows("SELECT * FROM incidents"), [])
        self.assertEqual(self.rows("SELECT * FROM notification_outbox"), [])

    def test_ring_backfill_is_acknowledged_without_opening_an_incident(self) -> None:
        self.enqueue(
            "ring",
            "entry.person_detected",
            occurred_at="2026-07-12T14:50:00Z",
            observed_at="2026-07-12T14:58:00Z",
            attributes={"classification": "person", "backfill": True},
            precision="backfill",
        )
        self.ingest()

        result = self.run_correlator()

        self.assertEqual(result["acknowledged"], 1)
        self.assertEqual(self.rows("SELECT * FROM incidents"), [])

    def test_stale_arrival_event_cannot_override_current_vacancy(self) -> None:
        self.enqueue(
            "ring",
            "entry.person_detected",
            observed_at="2026-07-12T14:57:00Z",
            sequence="ring",
        )
        self.enqueue(
            "presence",
            "presence.occupancy_changed",
            observed_at="2026-07-12T14:58:00Z",
            attributes={
                "previous": "confirmed_vacant",
                "current": "occupied",
                "confidence": "canonical",
                "evidence_at": "2026-07-12T14:58:00Z",
                "state_hash": "b" * 64,
            },
            sequence="stale-arrival",
        )
        self.ingest()

        result = self.run_correlator()
        self.NOW = "2026-07-12T15:12:00Z"
        self.write_presence("confirmed_vacant", "confirmed_vacant")
        after_grace = self.run_correlator()

        self.assertEqual(result["acknowledged"], 2)
        self.assertEqual(result["shadow_decisions"], 0)
        self.assertEqual(after_grace["shadow_decisions"], 1)
        incident = self.rows("SELECT * FROM incidents")[0]
        self.assertEqual(incident["state"], "open")
        self.assertEqual(incident["summary_code"], "vacant_activity_shadowed")
        self.assertEqual(len(self.rows("SELECT * FROM notification_outbox")), 1)

    def test_lock_close_resolves_existing_activity_without_second_incident(self) -> None:
        self.enqueue(
            "august",
            "lock.unlocked",
            site="crosstown",
            attributes={
                "previous": "locked",
                "current": "unlocked",
                "not_before": "2026-07-12T14:55:00Z",
                "not_after": "2026-07-12T14:58:00Z",
            },
            sequence="unlock",
        )
        self.enqueue(
            "august",
            "lock.locked",
            site="crosstown",
            observed_at="2026-07-12T14:59:00Z",
            attributes={
                "previous": "unlocked",
                "current": "locked",
                "not_before": "2026-07-12T14:58:00Z",
                "not_after": "2026-07-12T14:59:00Z",
            },
            sequence="lock",
        )
        self.ingest()

        result = self.run_correlator()

        self.assertEqual(result["acknowledged"], 2)
        incidents = self.rows("SELECT * FROM incidents")
        self.assertEqual(len(incidents), 1)
        self.assertEqual(incidents[0]["state"], "resolved")
        self.assertEqual(incidents[0]["summary_code"], "access_resolved_silently")

    def test_door_and_lock_conditions_must_both_clear(self) -> None:
        self.enqueue(
            "august",
            "lock.unlocked",
            site="crosstown",
            attributes={
                "previous": "locked",
                "current": "unlocked",
                "not_before": "2026-07-12T14:55:00Z",
                "not_after": "2026-07-12T14:58:00Z",
            },
            sequence="unlock",
        )
        self.enqueue(
            "august",
            "door.opened",
            site="crosstown",
            observed_at="2026-07-12T14:59:00Z",
            attributes={
                "previous": "closed",
                "current": "open",
                "not_before": "2026-07-12T14:58:00Z",
                "not_after": "2026-07-12T14:59:00Z",
            },
            sequence="open",
        )
        self.ingest()
        self.run_correlator()

        self.enqueue(
            "august",
            "door.closed",
            site="crosstown",
            observed_at="2026-07-12T14:59:30Z",
            attributes={
                "previous": "open",
                "current": "closed",
                "not_before": "2026-07-12T14:59:00Z",
                "not_after": "2026-07-12T14:59:30Z",
            },
            sequence="close",
        )
        self.ingest()
        self.run_correlator()

        incident = self.rows("SELECT * FROM incidents")[0]
        self.assertEqual(incident["state"], "open")
        self.assertEqual(incident["summary_code"], "access_still_open_shadowed")

        self.enqueue(
            "august",
            "lock.locked",
            site="crosstown",
            observed_at="2026-07-12T15:00:00Z",
            attributes={
                "previous": "unlocked",
                "current": "locked",
                "not_before": "2026-07-12T14:59:30Z",
                "not_after": "2026-07-12T15:00:00Z",
            },
            sequence="lock",
        )
        self.ingest()
        self.run_correlator()

        incident = self.rows("SELECT * FROM incidents")[0]
        self.assertEqual(incident["state"], "resolved")
        self.assertEqual(incident["summary_code"], "access_resolved_silently")

    def test_queued_close_resolves_before_access_expiry(self) -> None:
        self.enqueue(
            "august",
            "door.opened",
            site="crosstown",
            observed_at="2026-07-12T14:58:00Z",
            attributes={
                "previous": "closed",
                "current": "open",
                "not_before": "2026-07-12T14:55:00Z",
                "not_after": "2026-07-12T14:58:00Z",
            },
            sequence="open",
        )
        self.ingest()
        self.run_correlator()

        self.NOW = "2026-07-13T15:01:00Z"
        self.write_presence("confirmed_vacant", "confirmed_vacant")
        self.enqueue(
            "august",
            "door.closed",
            site="crosstown",
            observed_at="2026-07-13T15:00:00Z",
            attributes={
                "previous": "open",
                "current": "closed",
                "not_before": "2026-07-13T14:55:00Z",
                "not_after": "2026-07-13T15:00:00Z",
            },
            sequence="close-next-day",
        )
        self.ingest()

        result = self.run_correlator()

        self.assertEqual(result["expired"], 0)
        incident = self.rows("SELECT * FROM incidents")[0]
        self.assertEqual(incident["state"], "resolved")
        self.assertEqual(incident["summary_code"], "access_resolved_silently")

    def test_access_expiry_waits_for_resolution_beyond_claim_limit(self) -> None:
        self.enqueue(
            "august",
            "door.opened",
            site="crosstown",
            observed_at="2026-07-12T14:58:00Z",
            attributes={
                "previous": "closed",
                "current": "open",
                "not_before": "2026-07-12T14:55:00Z",
                "not_after": "2026-07-12T14:58:00Z",
            },
            sequence="open",
        )
        self.ingest()
        self.run_correlator()

        self.NOW = "2026-07-13T15:01:00Z"
        self.write_presence("confirmed_vacant", "confirmed_vacant")
        for index in range(20):
            self.enqueue(
                "ring",
                "entry.motion_detected",
                site="crosstown",
                observed_at=f"2026-07-13T14:59:{index:02d}Z",
                sequence=f"motion-{index}",
            )
        self.enqueue(
            "august",
            "door.closed",
            site="crosstown",
            observed_at="2026-07-13T15:00:00Z",
            attributes={
                "previous": "open",
                "current": "closed",
                "not_before": "2026-07-13T14:55:00Z",
                "not_after": "2026-07-13T15:00:00Z",
            },
            sequence="close-after-backlog",
        )
        self.ingest()

        first = self.run_correlator()

        self.assertEqual(first["claimed"], 20)
        self.assertEqual(first["expired"], 0)
        self.assertEqual(self.rows("SELECT state FROM incidents")[0]["state"], "open")

        second = self.run_correlator()

        self.assertEqual(second["acknowledged"], 1)
        incident = self.rows("SELECT * FROM incidents")[0]
        self.assertEqual(incident["state"], "resolved")
        self.assertEqual(incident["summary_code"], "access_resolved_silently")

    def test_active_lease_blocks_later_resolution_until_order_recovers(self) -> None:
        self.enqueue(
            "august",
            "door.opened",
            site="crosstown",
            observed_at="2026-07-12T14:50:00Z",
            attributes={
                "previous": "closed",
                "current": "open",
                "not_before": "2026-07-12T14:45:00Z",
                "not_after": "2026-07-12T14:50:00Z",
            },
            sequence="open-before-crash",
        )
        for index in range(19):
            self.enqueue(
                "ring",
                "entry.motion_detected",
                site="crosstown",
                observed_at=f"2026-07-12T14:51:{index:02d}Z",
                sequence=f"leased-motion-{index}",
            )
        self.ingest()
        store = bus.EventStore(bus.validate_runtime(self.root), clock=self.clock)
        abandoned = store.claim_deliveries("correlator", limit=20)
        self.assertEqual(len(abandoned["deliveries"]), 20)

        self.enqueue(
            "august",
            "door.closed",
            site="crosstown",
            observed_at="2026-07-12T14:59:00Z",
            attributes={
                "previous": "open",
                "current": "closed",
                "not_before": "2026-07-12T14:50:00Z",
                "not_after": "2026-07-12T14:59:00Z",
            },
            sequence="close-after-crash",
        )
        self.ingest()

        blocked = self.run_correlator()

        self.assertEqual(blocked["claimed"], 0)
        self.assertEqual(self.rows("SELECT * FROM incidents"), [])
        self.execute(
            """
            UPDATE consumer_deliveries
            SET lease_until = '2000-01-01T00:00:00Z'
            WHERE status = 'leased';
            """
        )

        recovered_head = self.run_correlator()
        self.assertEqual(recovered_head["acknowledged"], 20)
        self.assertEqual(self.rows("SELECT state FROM incidents")[0]["state"], "open")

        recovered_tail = self.run_correlator()
        self.assertEqual(recovered_tail["acknowledged"], 1)
        incident = self.rows("SELECT * FROM incidents")[0]
        self.assertEqual(incident["state"], "resolved")
        self.assertEqual(incident["summary_code"], "access_resolved_silently")

    def test_cross_site_activity_never_correlates(self) -> None:
        self.enqueue("ring", "entry.person_detected", site="cabin", sequence="a")
        self.enqueue("ring", "entry.person_detected", site="crosstown", sequence="b")
        self.ingest()

        self.run_correlator()

        incidents = self.rows("SELECT site FROM incidents ORDER BY site")
        self.assertEqual([row["site"] for row in incidents], ["cabin", "crosstown"])

    def test_unresolved_access_expires_into_separate_operator_attention(self) -> None:
        self.enqueue(
            "august",
            "door.opened",
            site="crosstown",
            observed_at="2026-07-11T13:00:00Z",
            attributes={
                "previous": "closed",
                "current": "open",
                "not_before": "2026-07-11T12:55:00Z",
                "not_after": "2026-07-11T13:00:00Z",
            },
            sequence="old-open",
        )
        self.ingest()
        result = self.run_correlator()

        self.assertEqual(result["expired"], 1)
        incident = self.rows("SELECT * FROM incidents")[0]
        self.assertEqual(incident["state"], "expired_unresolved")
        status = bus.EventStore(bus.RuntimePaths(self.root)).status_snapshot()
        self.assertEqual(status["health"], "ok")
        self.assertIsNone(status["last_error_code"])
        self.assertEqual(
            status["attention"],
            {
                "required": True,
                "expired_unresolved": 1,
                "reviewed": 0,
                "pending": 1,
                "latest_at": "2026-07-12T15:00:00Z",
                "last_reviewed_at": None,
                "delivery_unknown_unreviewed": 0,
            },
        )

    def test_projection_rolls_back_if_delivery_acknowledgement_fails(self) -> None:
        self.enqueue("ring", "entry.person_detected")
        self.ingest()
        self.execute(
            """
            CREATE TRIGGER fail_correlator_ack
            BEFORE UPDATE OF status ON consumer_deliveries
            WHEN NEW.status = 'acknowledged'
            BEGIN
                SELECT RAISE(ABORT, 'injected_ack_failure');
            END;
            """
        )

        failed = self.run_correlator()

        self.assertEqual(failed["acknowledged"], 0)
        self.assertEqual(self.rows("SELECT * FROM incidents"), [])
        self.execute(
            """
            DROP TRIGGER fail_correlator_ack;
            UPDATE consumer_deliveries
            SET lease_until = '2000-01-01T00:00:00Z'
            WHERE status = 'leased';
            """
        )

        retried = self.run_correlator()

        self.assertEqual(retried["acknowledged"], 1)
        self.assertEqual(len(self.rows("SELECT * FROM incidents")), 1)


if __name__ == "__main__":
    unittest.main()
