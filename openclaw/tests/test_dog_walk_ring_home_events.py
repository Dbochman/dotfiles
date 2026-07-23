#!/usr/bin/env python3
"""Focused tests for the nonblocking Ring home-event publisher tee."""

from __future__ import annotations

import asyncio
import importlib.util
import json
import os
import queue
import subprocess
import sys
import tempfile
import threading
import time
import types
import unittest
from pathlib import Path
from unittest import mock


REPO_ROOT = Path(__file__).resolve().parents[2]
LISTENER_PATH = REPO_ROOT / "openclaw/skills/dog-walk/dog-walk-listener.py"


def load_listener(fake_home: Path):
    fake_aiohttp = types.ModuleType("aiohttp")
    fake_ring = types.ModuleType("ring_doorbell")
    fake_ring_listen = types.ModuleType("ring_doorbell.listen")
    fake_listener_config = types.ModuleType("ring_doorbell.listen.listenerconfig")

    class Placeholder:
        pass

    fake_ring.Auth = Placeholder
    fake_ring.Ring = Placeholder
    fake_ring.RingEvent = Placeholder
    fake_ring.RingEventListener = Placeholder
    fake_listener_config.RingEventListenerConfig = Placeholder

    replacements = {
        "aiohttp": fake_aiohttp,
        "ring_doorbell": fake_ring,
        "ring_doorbell.listen": fake_ring_listen,
        "ring_doorbell.listen.listenerconfig": fake_listener_config,
    }
    previous_modules = {name: sys.modules.get(name) for name in replacements}
    previous_home = os.environ.get("HOME")
    try:
        sys.modules.update(replacements)
        os.environ["HOME"] = str(fake_home)
        spec = importlib.util.spec_from_file_location(
            "dog_walk_ring_home_events_test", LISTENER_PATH
        )
        assert spec and spec.loader
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    finally:
        if previous_home is None:
            os.environ.pop("HOME", None)
        else:
            os.environ["HOME"] = previous_home
        for name, previous in previous_modules.items():
            if previous is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = previous


class CapturingPublisher:
    def __init__(self) -> None:
        self.payloads: list[dict] = []
        self.quarantined = 0

    def submit(self, payload: dict) -> bool:
        self.payloads.append(payload)
        return True

    def quarantine_unknown_device(self) -> None:
        self.quarantined += 1


class RingHomeEventTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.class_tempdir = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.class_tempdir.cleanup)
        cls.module = load_listener(Path(cls.class_tempdir.name))

    def setUp(self) -> None:
        self.logs: list[str] = []
        self.publisher = CapturingPublisher()
        self.module.log = self.logs.append
        self.module._ring_home_event_publisher = self.publisher
        self.module._recent_events.clear()
        self.module._ring_departure_motion.clear()
        self.module._return_monitor_active = False
        self.module._ring_motion_during_walk = False
        self.module.HOME_EVENTS_RING_ENABLED = True

    def process(self, **overrides) -> None:
        values = {
            "event_id": 123456,
            "kind": "motion",
            "device": "Provider Front Door Name",
            "doorbot_id": 684794187,
            "state": "human",
            "occurred_at_epoch": 1_788_000_000.0,
        }
        values.update(overrides)
        self.module._process_ring_event_on_loop(**values)

    def test_normalizes_all_supported_ring_event_types(self) -> None:
        cases = [
            ("ding", "ringing", "entry.doorbell_rang", {}),
            (
                "motion",
                "human",
                "entry.person_detected",
                {"classification": "person"},
            ),
            (
                "motion",
                "other",
                "entry.motion_detected",
                {"classification": "motion"},
            ),
        ]
        for kind, state, expected_type, expected_attributes in cases:
            with self.subTest(kind=kind, state=state):
                with mock.patch.object(
                    self.module.time, "time", return_value=1_788_000_001.0
                ):
                    payload = self.module._normalize_ring_home_event(
                        event_id=123456,
                        kind=kind,
                        doorbot_id=684794187,
                        state=state,
                        occurred_at_epoch=1_788_000_000.0,
                    )

                assert payload is not None
                self.assertEqual(payload["schema_version"], 1)
                self.assertEqual(payload["event_type"], expected_type)
                self.assertEqual(payload["site"], "crosstown")
                self.assertEqual(payload["entity_kind"], "doorbell")
                self.assertEqual(payload["entity_alias"], "front_door")
                self.assertEqual(payload["time_precision"], "source")
                self.assertEqual(payload["attributes"], expected_attributes)
                self.assertEqual(payload["source_event_id"], "684794187:123456")
                sanitized = dict(payload)
                sanitized.pop("source_event_id")
                self.assertNotIn("684794187", json.dumps(sanitized))
                self.assertNotIn("123456", json.dumps(sanitized))

    def test_second_device_binding_normalizes_to_cabin(self) -> None:
        with mock.patch.object(self.module.time, "time", return_value=1_788_000_001.0):
            self.process(
                event_id=123457,
                device="Provider Cabin Door Name",
                doorbot_id=697442349,
            )

        self.assertEqual(len(self.publisher.payloads), 1)
        payload = self.publisher.payloads[0]
        self.assertEqual(payload["event_type"], "entry.person_detected")
        self.assertEqual(payload["site"], "cabin")
        self.assertEqual(payload["entity_kind"], "doorbell")
        self.assertEqual(payload["entity_alias"], "front_door")
        self.assertEqual(payload["source_event_id"], "697442349:123457")
        self.assertIn("cabin", self.module._ring_departure_motion)
        self.assertNotIn("crosstown", self.module._ring_departure_motion)
        self.assertEqual(self.publisher.quarantined, 0)

        combined_logs = "\n".join(self.logs)
        self.assertIn("site=cabin entity=front_door", combined_logs)
        self.assertNotIn("Provider Cabin Door Name", combined_logs)
        self.assertNotIn("697442349", combined_logs)
        self.assertNotIn("123457", combined_logs)

    def test_dedupe_precedes_bus_tee_and_legacy_person_motion_is_preserved(self) -> None:
        self.process()
        first_departure_timestamp = self.module._ring_departure_motion["crosstown"]
        self.process()

        self.assertEqual(len(self.publisher.payloads), 1)
        self.assertEqual(
            self.publisher.payloads[0]["event_type"], "entry.person_detected"
        )
        self.assertEqual(
            self.module._ring_departure_motion["crosstown"],
            first_departure_timestamp,
        )

    def test_delayed_ring_events_are_bounded_backfill_and_never_drive_legacy(self) -> None:
        with mock.patch.object(self.module.time, "time", return_value=1_788_001_000.0):
            self.process(occurred_at_epoch=1_788_000_880.0)

        self.assertEqual(len(self.publisher.payloads), 1)
        payload = self.publisher.payloads[0]
        self.assertEqual(payload["time_precision"], "backfill")
        self.assertTrue(payload["attributes"]["backfill"])
        self.assertNotIn("crosstown", self.module._ring_departure_motion)

        self.module._recent_events.clear()
        self.publisher.payloads.clear()
        with mock.patch.object(self.module.time, "time", return_value=1_788_002_000.0):
            self.process(event_id=123457, occurred_at_epoch=1_788_000_000.0)

        self.assertEqual(self.publisher.payloads, [])
        self.assertNotIn("crosstown", self.module._ring_departure_motion)
        self.assertIn("reason=stale_backfill", "\n".join(self.logs))

    def test_unknown_device_is_quarantined_but_legacy_motion_continues(self) -> None:
        self.module._return_monitor_active = True
        self.process(doorbot_id=999999999)

        self.assertEqual(self.publisher.payloads, [])
        self.assertEqual(self.publisher.quarantined, 1)
        self.assertTrue(self.module._ring_motion_during_walk)
        self.assertEqual(self.module._ring_departure_motion, {})
        combined_logs = "\n".join(self.logs)
        self.assertIn("reason=unknown_device", combined_logs)
        self.assertNotIn("site=cabin", combined_logs)
        self.assertNotIn("site=crosstown", combined_logs)
        self.assertNotIn("999999999", combined_logs)
        self.assertNotIn("123456", combined_logs)

    def test_disabled_producer_preserves_legacy_without_queueing(self) -> None:
        self.module.HOME_EVENTS_RING_ENABLED = False

        self.process()

        self.assertEqual(self.publisher.payloads, [])
        self.assertIn("reason=producer_disabled", "\n".join(self.logs))
        self.assertIn("crosstown", self.module._ring_departure_motion)

    def test_ding_message_is_unchanged_while_logs_use_safe_aliases(self) -> None:
        sent: list[str] = []

        async def fake_send(message: str) -> bool:
            sent.append(message)
            return True

        async def scenario() -> None:
            with mock.patch.object(
                self.module, "_send_imessage_async", side_effect=fake_send
            ):
                self.process(kind="ding", state="ringing")
                await asyncio.sleep(0)

        asyncio.run(scenario())

        self.assertEqual(sent, ["\U0001f514 Provider Front Door Name: Doorbell rang!"])
        self.assertEqual(
            self.publisher.payloads[0]["event_type"], "entry.doorbell_rang"
        )
        combined_logs = "\n".join(self.logs)
        self.assertIn("site=crosstown entity=front_door", combined_logs)
        self.assertNotIn("Provider Front Door Name", combined_logs)
        self.assertNotIn("684794187", combined_logs)
        self.assertNotIn("123456", combined_logs)

    def test_fcm_callback_only_bridges_plain_fields_to_the_event_loop(self) -> None:
        calls: list[tuple] = []

        class FakeLoop:
            def call_soon_threadsafe(self, *args) -> None:
                calls.append(args)

        event = types.SimpleNamespace(
            is_update=False,
            id=123456,
            kind="motion",
            device_name="Provider Front Door Name",
            doorbot_id=684794187,
            state="human",
            now=1_788_000_000.0,
        )
        self.module._main_loop = FakeLoop()
        with mock.patch.object(
            self.module.subprocess,
            "run",
            side_effect=AssertionError("callback attempted subprocess I/O"),
        ):
            self.module.on_event(event)

        self.assertEqual(len(calls), 1)
        self.assertIs(calls[0][0], self.module._process_ring_event_on_loop)
        self.assertEqual(
            calls[0][1:],
            (
                123456,
                "motion",
                "Provider Front Door Name",
                684794187,
                "human",
                1_788_000_000.0,
            ),
        )

    def test_publisher_queue_is_bounded_and_submit_never_runs_subprocess(self) -> None:
        runner = mock.Mock(
            side_effect=AssertionError("submit attempted subprocess I/O")
        )
        status_path = (
            Path(self.class_tempdir.name)
            / "home-events"
            / "state"
            / "ring-producer.json"
        )
        status_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        status_path.parent.chmod(0o700)
        publisher = self.module._RingHomeEventPublisher(
            "/fake/home-eventctl", runner=runner, status_path=status_path
        )
        self.assertEqual(
            publisher._queue.maxsize, self.module._RING_HOME_EVENT_QUEUE_MAX
        )
        publisher._queue = queue.Queue(maxsize=1)

        self.assertTrue(publisher.submit({"one": 1}))
        self.assertFalse(publisher.submit({"two": 2}))
        self.assertEqual(
            publisher.counters(),
            {
                "accepted": 1,
                "published": 0,
                "failed": 0,
                "dropped": 1,
                "quarantined": 0,
            },
        )
        runner.assert_not_called()

    def test_daemon_worker_invokes_home_eventctl_and_logs_only_safe_counters(self) -> None:
        calls: list[tuple] = []
        invoked = threading.Event()
        raw_child_output = "provider ids 684794187 and 123456"

        def runner(command, **kwargs):
            calls.append((command, kwargs))
            invoked.set()
            return subprocess.CompletedProcess(
                command,
                0 if len(calls) == 1 else 7,
                stdout=raw_child_output,
                stderr=raw_child_output,
            )

        status_path = (
            Path(self.class_tempdir.name)
            / "home-events-worker"
            / "state"
            / "ring-producer.json"
        )
        status_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        status_path.parent.chmod(0o700)
        publisher = self.module._RingHomeEventPublisher(
            "/fake/home-eventctl", runner=runner, status_path=status_path
        )
        payload = {
            "schema_version": 1,
            "source_event_id": "684794187:123456",
            "event_type": "entry.person_detected",
            "site": "crosstown",
            "entity_kind": "doorbell",
            "entity_alias": "front_door",
            "occurred_at": "2026-08-29T21:20:00.000000Z",
            "observed_at": "2026-08-29T21:20:01.000000Z",
            "time_precision": "source",
            "attributes": {"classification": "person"},
        }

        publisher.start()
        assert publisher._thread is not None
        self.assertTrue(publisher._thread.daemon)
        self.assertEqual(publisher._thread.name, "ring-home-event-publisher")
        self.assertTrue(publisher.submit(payload))
        self.assertTrue(publisher.submit(payload))
        self.assertTrue(invoked.wait(1), "publisher worker never invoked command")
        deadline = time.monotonic() + 1
        while (
            publisher.counters()["published"] != 1
            or publisher.counters()["failed"] != 1
        ) and time.monotonic() < deadline:
            time.sleep(0.01)

        self.assertEqual(len(calls), 2)
        self.assertEqual(
            calls[0][0],
            ["/fake/home-eventctl", "enqueue", "--source", "ring"],
        )
        self.assertEqual(json.loads(calls[0][1]["input"]), payload)
        self.assertTrue(calls[0][1]["text"])
        self.assertTrue(calls[0][1]["capture_output"])
        self.assertEqual(
            calls[0][1]["timeout"],
            self.module._RING_HOME_EVENT_PUBLISH_TIMEOUT,
        )
        self.assertEqual(publisher.counters()["published"], 1)
        self.assertEqual(publisher.counters()["failed"], 1)
        combined_logs = "\n".join(self.logs)
        self.assertIn("result=success", combined_logs)
        self.assertIn("result=failure", combined_logs)
        self.assertNotIn(raw_child_output, combined_logs)
        self.assertNotIn("684794187", combined_logs)
        self.assertNotIn("123456", combined_logs)
        deadline = time.monotonic() + 1
        status = None
        while time.monotonic() < deadline:
            if status_path.exists():
                candidate = json.loads(status_path.read_text(encoding="utf-8"))
                if candidate["counters"]["failed"] == 1:
                    status = candidate
                    break
            time.sleep(0.01)
        self.assertIsNotNone(status)
        assert status is not None
        self.assertEqual(status["health"], "degraded")
        self.assertEqual(status["counters"]["published"], 1)
        self.assertEqual(status["counters"]["failed"], 1)
        self.assertEqual(status_path.stat().st_mode & 0o777, 0o600)


if __name__ == "__main__":
    unittest.main()
