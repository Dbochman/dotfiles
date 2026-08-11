#!/usr/bin/env python3
"""Crash-boundary and privacy tests for limited home-event delivery."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import sqlite3
import subprocess
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
delivery = load_module(
    "home_event_delivery", BIN_DIR / "home-event-delivery.py"
)


class HomeEventDeliveryTests(unittest.TestCase):
    NOW = "2026-07-12T15:00:00Z"
    TARGET = "chat_id:171"

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name) / "home-events"
        self.presence = Path(self.temporary.name) / "presence.json"
        self.clock = lambda: self.NOW
        self.store = bus.initialize_runtime(self.root, clock=self.clock)
        self.paths = bus.RuntimePaths(self.root)
        self.write_presence("confirmed_vacant", "confirmed_vacant")
        environment = mock.patch.dict(
            delivery.os.environ,
            {"OPENCLAW_GATEWAY_TOKEN": "test-gateway-token"},
            clear=False,
        )
        environment.start()
        self.addCleanup(environment.stop)

    def write_presence(self, cabin: str, crosstown: str) -> None:
        self.presence.write_text(
            json.dumps(
                {
                    "timestamp": self.NOW,
                    "cabin": {"occupancy": cabin, "fresh": True},
                    "crosstown": {"occupancy": crosstown, "fresh": True},
                }
            ),
            encoding="utf-8",
        )
        self.presence.chmod(0o600)

    def policy(self, *, camera: bool = False) -> dict:
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
        return policy

    def activate(self, *, camera: bool = False) -> None:
        bus.install_delivery_policy(
            self.paths, json.dumps(self.policy(camera=camera)).encode("utf-8")
        )
        self.store.set_runtime_mode("limited_delivery")

    def reserve(
        self,
        template: str = "person_activity",
        *,
        camera_result: str | None = None,
    ) -> None:
        with self.store.connect() as connection:
            incident = connection.execute(
                """
                INSERT INTO incidents(
                    incident_uid, site, state, category, summary_code,
                    opened_at, updated_at
                ) VALUES (?, 'cabin', 'open', 'activity', 'reserved', ?, ?)
                """,
                ("inc_" + ("a" * 32), self.NOW, self.NOW),
            ).lastrowid
            camera_id = None
            if camera_result is not None:
                camera_id = connection.execute(
                    """
                    INSERT INTO camera_evaluations(
                        evaluation_uid, site, camera_alias, state, trigger_at,
                        due_30_at, due_60_at, snapshot_30_result,
                        snapshot_60_result, result, created_at, updated_at,
                        completed_at
                    ) VALUES (?, 'cabin', 'Kitchen', 'complete', ?, ?, ?,
                              'clear', 'clear', ?, ?, ?, ?)
                    """,
                    (
                        "cam_" + ("c" * 32),
                        self.NOW,
                        self.NOW,
                        self.NOW,
                        camera_result,
                        self.NOW,
                        self.NOW,
                        self.NOW,
                    ),
                ).lastrowid
            connection.execute(
                """
                INSERT INTO notification_outbox(
                    incident_id, site, status, reservation_token,
                    reserved_until, recipient_route, template_code,
                    attempt_count, camera_evaluation_id, camera_result,
                    created_at, updated_at
                ) VALUES (?, 'cabin', 'reserved', ?, ?, 'dylan', ?, 0, ?, ?, ?, ?)
                """,
                (
                    incident,
                    "res_" + ("b" * 32),
                    "2026-07-12T15:05:00Z",
                    template,
                    camera_id,
                    camera_result,
                    self.NOW,
                    self.NOW,
                ),
            )
            connection.commit()

    def worker(self, target: str | None = None):
        return delivery.DeliveryWorker(
            self.root,
            self.presence,
            self.TARGET if target is None else target,
            clock=self.clock,
        )

    def row(self) -> sqlite3.Row:
        with self.store.connect(read_only=True) as connection:
            return connection.execute("SELECT * FROM notification_outbox").fetchone()

    @staticmethod
    def receipt(*, via: str = "direct") -> subprocess.CompletedProcess[str]:
        channel_payload = {
            "channel": "imessage",
            "to": "chat_id:171",
            "via": via,
            "result": {"messageId": "message-guid"},
        }
        if via == "direct":
            channel_payload.update(
                {
                    "mediaUrl": None,
                    "deliveryStatus": "sent",
                    "payloadOutcomes": [
                        {"index": 0, "status": "sent", "messageId": "message-guid"}
                    ],
                }
            )
        payload = {
            "action": "send",
            "channel": "imessage",
            "dryRun": False,
            "handledBy": "core",
            "messageId": "message-guid",
            "payload": channel_payload,
        }
        return subprocess.CompletedProcess(
            [delivery.OPENCLAW_BIN, "message", "send"],
            0,
            stdout=json.dumps(payload) + "\n",
            stderr="",
        )

    def test_receipt_accepts_legacy_gateway_and_current_native_direct_shapes(
        self,
    ) -> None:
        delivery.validate_receipt(self.receipt(via="gateway").stdout, self.TARGET)
        delivery.validate_receipt(self.receipt(via="direct").stdout, self.TARGET)

    def test_native_direct_receipt_requires_confirmed_delivery(self) -> None:
        payload = json.loads(self.receipt(via="direct").stdout)
        payload["payload"]["deliveryStatus"] = "failed"
        with self.assertRaisesRegex(delivery.DeliveryError, "message_receipt_invalid"):
            delivery.validate_receipt(json.dumps(payload), self.TARGET)

    def test_receipt_rejects_unknown_message_identity(self) -> None:
        payload = json.loads(self.receipt(via="direct").stdout)
        payload["messageId"] = "unknown"
        payload["payload"]["result"]["messageId"] = "unknown"
        with self.assertRaisesRegex(delivery.DeliveryError, "message_receipt_invalid"):
            delivery.validate_receipt(json.dumps(payload), self.TARGET)

    def test_shadow_worker_is_inert_without_target(self) -> None:
        with mock.patch.object(delivery.subprocess, "run") as run:
            result = self.worker(target="").run_once()
        self.assertEqual(result, {"ok": True, "outcome": "idle"})
        run.assert_not_called()
        self.assertEqual(self.store.status_snapshot()["delivery"]["health"], "disabled")

    def test_fresh_vacancy_sends_one_fixed_template_and_records_receipt(self) -> None:
        self.activate()
        self.reserve()
        with mock.patch.object(
            delivery.subprocess, "run", return_value=self.receipt()
        ) as run:
            result = self.worker().run_once()

        self.assertEqual(result["outcome"], "sent")
        row = self.row()
        self.assertEqual(row["status"], "sent")
        self.assertEqual(row["attempt_count"], 1)
        self.assertEqual(row["sent_at"], self.NOW)
        command = run.call_args.args[0]
        self.assertEqual(command[command.index("--target") + 1], self.TARGET)
        message = command[command.index("--message") + 1]
        self.assertEqual(message, delivery.TEMPLATES["person_activity"].format(site="Cabin"))
        self.assertNotIn("inc_", message)
        self.assertNotIn("res_", message)

    def test_structured_camera_result_adds_only_fixed_context_clause(self) -> None:
        self.activate(camera=True)
        self.reserve(camera_result="no_person_visible")
        with mock.patch.object(
            delivery.subprocess, "run", return_value=self.receipt()
        ) as run:
            result = self.worker().run_once()

        self.assertEqual(result["outcome"], "sent")
        command = run.call_args.args[0]
        message = command[command.index("--message") + 1]
        self.assertTrue(
            message.endswith(delivery.CAMERA_CLAUSES["no_person_visible"])
        )
        self.assertNotIn("cam_", message)

    def test_presence_change_after_reservation_burns_without_send(self) -> None:
        self.activate()
        self.reserve()
        self.write_presence("occupied", "confirmed_vacant")
        with mock.patch.object(delivery.subprocess, "run") as run:
            result = self.worker().run_once()

        self.assertEqual(result["outcome"], "burned")
        self.assertEqual(self.row()["error_code"], "presence_not_vacant")
        run.assert_not_called()

    def test_timeout_is_unknown_and_never_retried(self) -> None:
        self.activate()
        self.reserve()
        with mock.patch.object(
            delivery.subprocess,
            "run",
            side_effect=subprocess.TimeoutExpired([delivery.OPENCLAW_BIN], 20),
        ) as run:
            first = self.worker().run_once()
            second = self.worker().run_once()

        self.assertEqual(first["outcome"], "unknown")
        self.assertEqual(second["outcome"], "idle")
        self.assertEqual(run.call_count, 1)
        self.assertEqual(self.row()["status"], "unknown")
        before = self.store.status_snapshot()
        self.assertEqual(before["delivery"]["health"], "degraded")
        self.assertEqual(
            before["delivery"]["attention"],
            {
                "required": True,
                "unknown_unreviewed": 1,
                "latest_at": self.NOW,
                "last_reviewed_at": None,
                "last_review_outcome": None,
            },
        )
        self.assertTrue(before["attention"]["required"])

        reviewed = self.store.review_delivery_attention("not_received")
        after = self.store.status_snapshot()

        self.assertEqual(
            reviewed,
            {"reviewed": 1, "pending": 0, "outcome": "not_received"},
        )
        row = self.row()
        self.assertEqual(row["status"], "unknown")
        self.assertEqual(row["reviewed_at"], self.NOW)
        self.assertEqual(row["review_outcome"], "not_received")
        self.assertEqual(after["delivery"]["health"], "ok")
        self.assertFalse(after["delivery"]["attention"]["required"])
        self.assertEqual(
            after["delivery"]["attention"]["last_review_outcome"],
            "not_received",
        )


if __name__ == "__main__":
    unittest.main()
