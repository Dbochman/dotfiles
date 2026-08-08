#!/usr/bin/env python3
"""Crash safety and privacy tests for bounded event-bus camera evidence."""

from __future__ import annotations

import importlib.util
from contextlib import closing
import json
import os
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest
from unittest import mock


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
camera = load_module("home_event_camera", BIN_DIR / "home-event-camera.py")


class FakeCommands:
    def __init__(
        self,
        decisions: list[camera.VisionDecision] | None = None,
        failures: set[tuple[str, str, str]] | None = None,
    ):
        self.decisions = list(decisions or [])
        self.failures = set(failures or set())
        self.captures: list[tuple[str, str, str]] = []

    def capture(
        self,
        provider: str,
        site: str,
        alias: str,
        path: Path,
    ) -> None:
        self.captures.append((provider, site, alias))
        if (provider, site, alias) in self.failures:
            raise camera.CameraError(provider + "_capture_command_failed")
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            os.write(descriptor, b"\xff\xd8\xfffixture\xff\xd9")
        finally:
            os.close(descriptor)

    def analyze(self, _path: Path) -> camera.VisionDecision:
        return self.decisions.pop(0)


class HomeEventCameraTests(unittest.TestCase):
    NOW = "2026-07-12T15:00:00Z"

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name) / "home-events"
        self.clock = lambda: self.NOW
        self.store = bus.initialize_runtime(self.root, clock=self.clock)
        self.paths = bus.RuntimePaths(self.root)
        policy = {
            "schema_version": 3,
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
            "camera_enabled": True,
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
        bus.install_delivery_policy(
            self.paths, json.dumps(policy).encode("utf-8")
        )
        self.store.set_runtime_mode("limited_delivery")

    def insert_evaluation(
        self,
        *,
        first: str = "pending",
        second: str = "pending",
    ) -> int:
        with closing(self.store.connect()) as connection:
            row_id = connection.execute(
                """
                INSERT INTO camera_evaluations(
                    evaluation_uid, site, camera_alias, state, trigger_at,
                    due_30_at, due_60_at, snapshot_30_result,
                    snapshot_60_result, created_at, updated_at
                ) VALUES (?, 'cabin', 'Kitchen', 'pending', ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    "cam_" + ("a" * 32),
                    "2026-07-12T14:59:00Z",
                    "2026-07-12T14:59:30Z",
                    "2026-07-12T15:00:00Z",
                    first,
                    second,
                    self.NOW,
                    self.NOW,
                ),
            ).lastrowid
            connection.commit()
        return int(row_id)

    def row(self, row_id: int) -> sqlite3.Row:
        with closing(self.store.connect(read_only=True)) as connection:
            return connection.execute(
                "SELECT * FROM camera_evaluations WHERE id = ?", (row_id,)
            ).fetchone()

    def attach_ring_event(self, evaluation_id: int, alias: str) -> None:
        payload = {
            "schema_version": 1,
            "source_event_id": "private-device:private-event-" + alias,
            "event_type": "entry.person_detected",
            "site": "cabin",
            "entity_kind": "doorbell",
            "entity_alias": alias,
            "occurred_at": self.NOW,
            "observed_at": self.NOW,
            "time_precision": "source",
            "attributes": {"classification": "person"},
        }
        bus.enqueue_event(
            self.root,
            "ring",
            json.dumps(payload).encode("utf-8"),
            clock=self.clock,
        )
        bus.ingest_once(self.root, clock=self.clock)
        with closing(self.store.connect()) as connection:
            event_id = connection.execute(
                "SELECT id FROM events ORDER BY id DESC LIMIT 1"
            ).fetchone()["id"]
            connection.execute(
                """
                INSERT INTO camera_evaluation_events(
                    evaluation_id, event_id, relation, created_at
                ) VALUES (?, ?, 'trigger', ?)
                """,
                (evaluation_id, event_id, self.NOW),
            )
            connection.commit()

    def test_two_slots_reduce_to_structured_result_and_delete_images(self) -> None:
        row_id = self.insert_evaluation()
        commands = FakeCommands(
            [
                camera.VisionDecision(False, "high"),
                camera.VisionDecision(False, "high"),
                camera.VisionDecision(True, "medium"),
                camera.VisionDecision(False, "high"),
            ]
        )
        worker = camera.CameraWorker(
            self.root, clock=self.clock, commands=commands
        )

        first = worker.run_once()
        second = worker.run_once()

        self.assertEqual(first["outcome"], "clear")
        self.assertEqual(second["outcome"], "person_visible")
        row = self.row(row_id)
        self.assertEqual(row["state"], "complete")
        self.assertEqual(row["result"], "person_visible")
        self.assertEqual(
            commands.captures,
            [
                ("ring", "cabin", "front_door"),
                ("nest", "cabin", "Kitchen"),
                ("ring", "cabin", "front_door"),
                ("nest", "cabin", "Kitchen"),
            ],
        )
        self.assertEqual(list(self.paths.camera_images.iterdir()), [])
        self.assertEqual(self.store.status_snapshot()["camera"]["health"], "ok")

    def test_ring_trigger_selects_its_exact_alias_plus_nest(self) -> None:
        row_id = self.insert_evaluation()
        self.attach_ring_event(row_id, "driveway")
        commands = FakeCommands(
            [
                camera.VisionDecision(False, "high"),
                camera.VisionDecision(False, "high"),
            ]
        )

        result = camera.CameraWorker(
            self.root, clock=self.clock, commands=commands
        ).run_once()

        self.assertEqual(result["outcome"], "clear")
        self.assertEqual(
            commands.captures,
            [
                ("ring", "cabin", "driveway"),
                ("nest", "cabin", "Kitchen"),
            ],
        )

    def test_ring_failure_keeps_nest_evidence_but_marks_slot_uncertain(self) -> None:
        row_id = self.insert_evaluation()
        commands = FakeCommands(
            [camera.VisionDecision(False, "high")],
            failures={("ring", "cabin", "front_door")},
        )

        result = camera.CameraWorker(
            self.root, clock=self.clock, commands=commands
        ).run_once()

        self.assertEqual(result["outcome"], "uncertain")
        row = self.row(row_id)
        self.assertEqual(row["snapshot_30_result"], "uncertain")
        self.assertEqual(row["error_code"], "camera_target_partial")
        self.assertEqual(self.store.status_snapshot()["camera"]["health"], "degraded")
        self.assertEqual(list(self.paths.camera_images.iterdir()), [])

    def test_real_commands_dispatch_ring_only_through_safe_binding(self) -> None:
        commands = camera.CameraCommands()
        path = Path("/private/tmp/event-camera-test.jpg")
        with mock.patch.object(commands, "_run") as run:
            commands.capture("ring", "cabin", "driveway", path)

        run.assert_called_once_with(
            [
                camera.RING_BIN,
                "_snapshot-bound",
                "cabin",
                "driveway",
                str(path),
            ],
            timeout=25,
            code="ring_capture_command_failed",
        )

    def test_uncertain_prior_capture_is_failed_without_retry(self) -> None:
        row_id = self.insert_evaluation(first="capturing", second="failed")
        commands = FakeCommands()
        result = camera.CameraWorker(
            self.root, clock=self.clock, commands=commands
        ).run_once()

        self.assertEqual(result["outcome"], "idle")
        row = self.row(row_id)
        self.assertEqual(row["state"], "failed")
        self.assertEqual(row["result"], "unavailable")
        self.assertEqual(row["error_code"], "prior_capture_uncertain")
        self.assertEqual(commands.captures, [])

    def test_shadow_mode_never_calls_camera(self) -> None:
        self.insert_evaluation()
        self.store.set_runtime_mode("shadow")
        commands = FakeCommands()
        result = camera.CameraWorker(
            self.root, clock=self.clock, commands=commands
        ).run_once()

        self.assertEqual(result["outcome"], "idle")
        self.assertEqual(commands.captures, [])
        self.assertEqual(self.store.status_snapshot()["camera"]["health"], "disabled")

    def test_invalid_active_policy_degrades_camera_health(self) -> None:
        self.paths.delivery_policy.write_text("{}\n", encoding="utf-8")
        self.paths.delivery_policy.chmod(0o600)
        with self.assertRaisesRegex(camera.CameraError, "camera_policy_unavailable"):
            camera.CameraWorker(
                self.root, clock=self.clock, commands=FakeCommands()
            ).run_once()
        status = self.store.status_snapshot()["camera"]
        self.assertEqual(status["health"], "degraded")
        self.assertEqual(status["last_error_code"], "camera_policy_unavailable")


if __name__ == "__main__":
    unittest.main()
