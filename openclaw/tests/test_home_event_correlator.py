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
            if event_type == "presence.person_relocated"
            else "site",
            "august": "door"
            if event_type.startswith("door.")
            else "battery"
            if event_type.startswith("device.")
            else "adapter"
            if event_type.startswith("source.")
            else "lock",
            "nest": "camera",
        }[source]
        time_precision = {
            "ring": "source",
            "presence": "evaluation",
            "august": "observed_interval",
            "nest": "source",
        }
        payload = {
            "schema_version": 1,
            "source_event_id": f"fixture-{source}-{event_type}-{site}-{sequence}",
            "event_type": event_type,
            "site": site,
            "entity_kind": entity_kind,
            "entity_alias": "kitchen"
            if source == "nest"
            else "front_door"
            if source != "presence"
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
                else {}
            ),
        }
        bus.enqueue_event(
            self.root,
            source,
            json.dumps(payload).encode("utf-8"),
            clock=self.clock,
        )

    def ingest(self) -> None:
        bus.ingest_once(self.root, clock=self.clock)

    def run_correlator(self):
        return correlator.ShadowCorrelator(
            self.root, self.presence, clock=self.clock
        ).run_once()

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

    def test_vacant_ring_activity_creates_one_shadow_decision(self) -> None:
        self.enqueue("ring", "entry.person_detected")
        self.ingest()

        first = self.run_correlator()
        second = self.run_correlator()

        self.assertEqual(first["acknowledged"], 1)
        self.assertEqual(first["shadow_decisions"], 1)
        self.assertEqual(second["claimed"], 0)
        incidents = self.rows("SELECT * FROM incidents")
        self.assertEqual(len(incidents), 1)
        self.assertEqual(incidents[0]["summary_code"], "vacant_activity_shadowed")
        self.assertEqual(
            len(self.rows("SELECT * FROM notification_outbox WHERE status='shadowed'")),
            1,
        )

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

    def test_generic_ring_motion_is_stored_but_not_actionable(self) -> None:
        self.enqueue("ring", "entry.motion_detected")
        self.ingest()

        result = self.run_correlator()

        self.assertEqual(result["acknowledged"], 1)
        self.assertEqual(self.rows("SELECT * FROM incidents"), [])
        self.assertEqual(self.rows("SELECT * FROM notification_outbox"), [])

    def test_nest_person_joins_ring_site_activity_incident(self) -> None:
        self.enqueue("ring", "entry.person_detected", sequence="ring")
        self.enqueue("nest", "camera.person_detected", sequence="nest")
        self.ingest()

        result = self.run_correlator()

        self.assertEqual(result["acknowledged"], 2)
        self.assertEqual(result["shadow_decisions"], 1)
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

        self.assertEqual(result["acknowledged"], 2)
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

    def test_unresolved_access_expires_and_degrades_health(self) -> None:
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
        status = self.rows("SELECT * FROM runtime_status")[0]
        self.assertEqual(status["health"], "degraded")
        self.assertEqual(status["last_error_code"], "access_expired_unresolved")

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
