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
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock


REPO_ROOT = Path(__file__).resolve().parents[2]
LISTENER_PATH = REPO_ROOT / "openclaw/skills/dog-walk/service-runtime.py"


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
        self.signals: list[str] = []
        self.module._ring_automation_signal_sender = self.signals.append

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
        self.assertEqual(self.signals, ["cabin"])
        self.assertEqual(self.publisher.quarantined, 0)

        combined_logs = "\n".join(self.logs)
        self.assertIn("site=cabin entity=front_door", combined_logs)
        self.assertNotIn("Provider Cabin Door Name", combined_logs)
        self.assertNotIn("697442349", combined_logs)
        self.assertNotIn("123457", combined_logs)

    def test_driveway_binding_publishes_without_becoming_a_departure_signal(
        self,
    ) -> None:
        driveway_ids = [
            provider_id
            for provider_id, binding in self.module.RING_EVENT_DEVICES.items()
            if binding == {"site": "cabin", "entity_alias": "driveway"}
        ]
        self.assertEqual(len(driveway_ids), 1)
        driveway_id = driveway_ids[0]

        with mock.patch.object(self.module.time, "time", return_value=1_788_000_001.0):
            self.process(
                event_id=123458,
                device="Sliding Door",
                doorbot_id=driveway_id,
            )

        self.assertEqual(len(self.publisher.payloads), 1)
        payload = self.publisher.payloads[0]
        self.assertEqual(payload["site"], "cabin")
        self.assertEqual(payload["entity_kind"], "doorbell")
        self.assertEqual(payload["entity_alias"], "driveway")
        self.assertEqual(
            payload["source_event_id"],
            f"{driveway_id}:123458",
        )
        self.assertNotIn(driveway_id, self.module.DOORBELL_LOCATIONS)
        self.assertEqual(self.signals, [])
        self.assertEqual(self.publisher.quarantined, 0)

        combined_logs = "\n".join(self.logs)
        self.assertIn("site=cabin entity=driveway", combined_logs)
        self.assertNotIn("Sliding Door", combined_logs)
        self.assertNotIn(str(driveway_id), combined_logs)
        self.assertNotIn("123458", combined_logs)

    def test_video_inventory_reconciliation_covers_driveway_and_unmapped_cameras(
        self,
    ) -> None:
        driveway_id = next(
            provider_id
            for provider_id, binding in self.module.RING_EVENT_DEVICES.items()
            if binding == {"site": "cabin", "entity_alias": "driveway"}
        )
        mapped = types.SimpleNamespace(id=driveway_id)

        mapped_inventory = types.SimpleNamespace(video_devices=(mapped,))
        self.assertFalse(
            self.module._ring_inventory_has_unbound_devices(mapped_inventory)
        )

        unmapped_id = max(self.module.RING_EVENT_DEVICES) + 1
        unmapped = types.SimpleNamespace(id=unmapped_id)
        broad_inventory = types.SimpleNamespace(
            # A doorbell-only check would miss the unmapped video camera.
            doorbots=(mapped,),
            authorized_doorbots=(),
            stickup_cams=(unmapped,),
            video_devices=(mapped, unmapped),
        )
        self.assertTrue(
            self.module._ring_inventory_has_unbound_devices(broad_inventory)
        )

    def test_video_inventory_reconciliation_has_a_fail_closed_sdk_fallback(
        self,
    ) -> None:
        front_door_id = next(iter(self.module.DOORBELL_LOCATIONS))
        legacy_inventory = types.SimpleNamespace(
            doorbots=(types.SimpleNamespace(id=front_door_id),),
            authorized_doorbots=(),
            stickup_cams=(),
        )
        self.assertFalse(
            self.module._ring_inventory_has_unbound_devices(legacy_inventory)
        )
        self.assertTrue(
            self.module._ring_inventory_has_unbound_devices(
                types.SimpleNamespace(
                    doorbots=(),
                    authorized_doorbots=(),
                    stickup_cams=(),
                )
            )
        )

    def test_dedupe_precedes_bus_tee_and_automation_signal(self) -> None:
        self.process()
        self.process()

        self.assertEqual(len(self.publisher.payloads), 1)
        self.assertEqual(
            self.publisher.payloads[0]["event_type"], "entry.person_detected"
        )
        self.assertEqual(self.signals, ["crosstown"])

    def test_delayed_ring_events_are_bounded_backfill_and_never_drive_legacy(self) -> None:
        with mock.patch.object(self.module.time, "time", return_value=1_788_001_000.0):
            self.process(occurred_at_epoch=1_788_000_880.0)

        self.assertEqual(len(self.publisher.payloads), 1)
        payload = self.publisher.payloads[0]
        self.assertEqual(payload["time_precision"], "backfill")
        self.assertTrue(payload["attributes"]["backfill"])
        self.assertEqual(self.signals, [])

        self.module._recent_events.clear()
        self.publisher.payloads.clear()
        with mock.patch.object(self.module.time, "time", return_value=1_788_002_000.0):
            self.process(event_id=123457, occurred_at_epoch=1_788_000_000.0)

        self.assertEqual(self.publisher.payloads, [])
        self.assertEqual(self.signals, [])
        self.assertIn("reason=stale_backfill", "\n".join(self.logs))

    def test_unknown_device_is_quarantined_without_crossing_automation_boundary(self) -> None:
        self.module._return_monitor_active = True
        self.process(doorbot_id=999999999)

        self.assertEqual(self.publisher.payloads, [])
        self.assertEqual(self.publisher.quarantined, 1)
        self.assertEqual(self.signals, [])
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
        self.assertEqual(self.signals, ["crosstown"])

    def test_validated_automation_signal_updates_only_dog_walk_state(self) -> None:
        observed_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        payload = json.dumps(
            {
                "schema_version": 1,
                "signal": "person_motion",
                "site": "crosstown",
                "observed_at": observed_at,
            }
        ).encode()

        site = self.module._decode_ring_automation_signal(payload)
        self.module._apply_ring_automation_signal(site)
        self.assertIn("crosstown", self.module._ring_departure_motion)

        self.module._return_monitor_active = True
        self.module._apply_ring_automation_signal(site)
        self.assertTrue(self.module._ring_motion_during_walk)

    def test_automation_signal_contract_rejects_stale_or_expanded_payloads(self) -> None:
        stale = {
            "schema_version": 1,
            "signal": "person_motion",
            "site": "crosstown",
            "observed_at": "2026-08-05T12:00:00Z",
        }
        now = datetime(2026, 8, 5, 12, 2, tzinfo=timezone.utc)
        with self.assertRaisesRegex(ValueError, "stale_signal"):
            self.module._decode_ring_automation_signal(
                json.dumps(stale).encode(), now=now
            )
        stale["provider_id"] = 123456
        with self.assertRaisesRegex(ValueError, "invalid_signal_contract"):
            self.module._decode_ring_automation_signal(
                json.dumps(stale).encode(), now=now
            )

    def test_protected_local_socket_delivers_ring_signal_to_dog_walk_process(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "dog-walk"
            root.mkdir(mode=0o700)
            endpoint = root / "ring-automation.sock"

            async def scenario() -> None:
                task = asyncio.create_task(self.module._ring_automation_signal_loop())
                try:
                    deadline = time.monotonic() + 1
                    while not endpoint.exists() and time.monotonic() < deadline:
                        await asyncio.sleep(0.01)
                    self.assertTrue(endpoint.exists())
                    self.assertEqual(endpoint.stat().st_mode & 0o777, 0o600)
                    self.assertTrue(
                        self.module._send_ring_automation_signal("crosstown")
                    )
                    deadline = time.monotonic() + 1
                    while (
                        "crosstown" not in self.module._ring_departure_motion
                        and time.monotonic() < deadline
                    ):
                        await asyncio.sleep(0.01)
                    self.assertIn("crosstown", self.module._ring_departure_motion)
                finally:
                    task.cancel()
                    await asyncio.gather(task, return_exceptions=True)

            with mock.patch.object(
                self.module, "RING_AUTOMATION_SOCKET", endpoint
            ):
                asyncio.run(scenario())
            self.assertFalse(endpoint.exists())

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
        self.assertIn("Ring doorbell message sent via supervised channel", combined_logs)
        self.assertNotIn("Provider Front Door Name", combined_logs)
        self.assertNotIn("684794187", combined_logs)
        self.assertNotIn("123456", combined_logs)

    def test_imessage_delivery_uses_one_supervised_channel_request(self) -> None:
        receipt = {
            "action": "send",
            "channel": "imessage",
            "dryRun": False,
            "handledBy": "core",
            "messageId": "test-message-guid",
            "payload": {
                "channel": "imessage",
                "to": "chat_id:171",
                "via": "direct",
                "mediaUrl": None,
                "deliveryStatus": "sent",
                "result": {"messageId": "test-message-guid"},
            },
        }
        completed = subprocess.CompletedProcess(
            [self.module.OPENCLAW_CLI, "message", "send"],
            0,
            stdout=json.dumps(receipt) + "\n",
            stderr="",
        )

        with (
            mock.patch.object(
                self.module,
                "DYLAN_IMESSAGE_TARGET",
                "chat_id:171",
            ),
            mock.patch.object(
                self.module.subprocess,
                "run",
                return_value=completed,
            ) as run,
            mock.patch.dict(
                self.module.os.environ,
                {
                    "HOME": str(Path.home()),
                    "OPENCLAW_GATEWAY_TOKEN": "test-gateway-token",
                },
                clear=True,
            ),
        ):
            self.assertTrue(
                self.module.send_imessage(
                    "\U0001f514 Provider Front Door Name: Doorbell rang!"
                )
            )

        run.assert_called_once()
        command = run.call_args.args[0]
        options = run.call_args.kwargs
        self.assertEqual(
            command,
            [
                self.module.OPENCLAW_CLI,
                "message",
                "send",
                "--channel",
                "imessage",
                "--target",
                "chat_id:171",
                "--message",
                "\U0001f514 Provider Front Door Name: Doorbell rang!",
                "--json",
            ],
        )
        self.assertEqual(options["timeout"], self.module.IMESSAGE_SEND_TIMEOUT_SECONDS)
        self.assertTrue(options["capture_output"])
        self.assertTrue(options["text"])
        self.assertEqual(
            options["env"],
            {
                "HOME": str(Path.home()),
                "PATH": f"{self.module.OPENCLAW_NODE_BIN}:/opt/homebrew/bin:/usr/bin:/bin",
                "LANG": "en_US.UTF-8",
                "OPENCLAW_GATEWAY_TOKEN": "test-gateway-token",
            },
        )

    def test_imessage_channel_timeout_is_not_retried_or_logged_with_payload(self) -> None:
        message = "\U0001f514 Private Provider Name: Doorbell rang!"
        with (
            mock.patch.object(
                self.module,
                "DYLAN_IMESSAGE_TARGET",
                "chat_id:171",
            ),
            mock.patch.object(
                self.module.subprocess,
                "run",
                side_effect=subprocess.TimeoutExpired(
                    [self.module.OPENCLAW_CLI, "message", "send"],
                    self.module.IMESSAGE_SEND_TIMEOUT_SECONDS,
                ),
            ) as run,
            mock.patch.dict(
                self.module.os.environ,
                {
                    "HOME": str(Path.home()),
                    "OPENCLAW_GATEWAY_TOKEN": "test-gateway-token",
                },
                clear=True,
            ),
        ):
            self.assertFalse(self.module.send_imessage(message))

        run.assert_called_once()
        combined_logs = "\n".join(self.logs)
        self.assertIn("channel request timed out", combined_logs)
        self.assertNotIn(message, combined_logs)
        self.assertNotIn("171", combined_logs)

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

    def test_recent_provider_history_recovers_as_inert_backfill_once(self) -> None:
        now = 1_788_000_000.0

        class FakeDoorbell:
            id = 684794187
            name = "Provider Front Door Name"

            async def async_history(self, *, limit: int):
                self.limit = limit
                return [
                    {
                        "id": "123999",
                        "kind": "motion",
                        "created_at": datetime.fromtimestamp(
                            now - 120, tz=timezone.utc
                        ),
                        "cv_properties": {"person_detected": True},
                    }
                ]

        doorbell = FakeDoorbell()
        with mock.patch.object(self.module.time, "time", return_value=now):
            first = asyncio.run(self.module._reconcile_ring_history([doorbell]))
            second = asyncio.run(self.module._reconcile_ring_history([doorbell]))

        self.assertEqual(first, (1, 0))
        self.assertEqual(second, (0, 0))
        self.assertEqual(doorbell.limit, self.module._RING_RECONCILE_HISTORY_LIMIT)
        self.assertEqual(len(self.publisher.payloads), 1)
        self.assertEqual(
            self.publisher.payloads[0]["attributes"],
            {"classification": "person", "backfill": True},
        )
        self.assertEqual(self.publisher.payloads[0]["time_precision"], "backfill")
        self.assertEqual(self.signals, [])

    def test_provider_history_is_bounded_and_skips_stale_or_unbound_events(self) -> None:
        now = 1_788_000_000.0

        class FakeDoorbell:
            def __init__(self, provider_id: int, created_at: datetime):
                self.id = provider_id
                self.name = "Private provider name"
                self.created_at = created_at

            async def async_history(self, *, limit: int):
                return [
                    {
                        "id": 123998,
                        "kind": "ding",
                        "created_at": self.created_at,
                    }
                ]

        stale = FakeDoorbell(
            684794187,
            datetime.fromtimestamp(
                now - self.module._RING_BACKFILL_MAX_SECONDS - 1,
                tz=timezone.utc,
            ),
        )
        unbound = FakeDoorbell(
            999999999,
            datetime.fromtimestamp(now - 30, tz=timezone.utc),
        )
        with mock.patch.object(self.module.time, "time", return_value=now):
            result = asyncio.run(
                self.module._reconcile_ring_history([stale, unbound])
            )

        self.assertEqual(result, (0, 0))
        self.assertEqual(self.publisher.payloads, [])
        self.assertEqual(self.signals, [])

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

    def test_status_persist_failures_back_off_without_clearing_dirty_state(
        self,
    ) -> None:
        publisher = self.module._RingHomeEventPublisher(
            "/fake/home-eventctl",
            status_path=Path(self.class_tempdir.name) / "unused-status.json",
        )
        publisher._increment("accepted")
        log_result = mock.Mock()

        with (
            mock.patch.object(
                publisher,
                "_persist_status",
                side_effect=ValueError("unsafe status path"),
            ) as persist_status,
            mock.patch.object(publisher, "_log_result", log_result),
        ):
            self.assertFalse(publisher._persist_status_if_due(now=0.0))
            self.assertEqual(persist_status.call_count, 1)
            self.assertFalse(publisher._persist_status_if_due(now=4.99))
            self.assertEqual(persist_status.call_count, 1)

            self.assertFalse(publisher._persist_status_if_due(now=5.0))
            self.assertEqual(persist_status.call_count, 2)
            self.assertFalse(publisher._persist_status_if_due(now=14.99))
            self.assertEqual(persist_status.call_count, 2)

            self.assertFalse(publisher._persist_status_if_due(now=15.0))
            self.assertEqual(persist_status.call_count, 3)
            while (
                publisher._status_retry_delay
                < self.module._RING_STATUS_RETRY_MAX_SECONDS
            ):
                self.assertFalse(
                    publisher._persist_status_if_due(
                        now=publisher._status_retry_not_before
                    )
                )

            capped_retry_at = publisher._status_retry_not_before
            self.assertFalse(
                publisher._persist_status_if_due(now=capped_retry_at)
            )
            self.assertEqual(
                publisher._status_retry_not_before - capped_retry_at,
                self.module._RING_STATUS_RETRY_MAX_SECONDS,
            )

        log_result.assert_called_once_with("status_persist_failure")
        self.assertTrue(publisher._dirty.is_set())

        def recover_status() -> None:
            publisher._dirty.clear()

        with mock.patch.object(
            publisher,
            "_persist_status",
            side_effect=recover_status,
        ):
            self.assertTrue(
                publisher._persist_status_if_due(
                    now=publisher._status_retry_not_before
                )
            )

        self.assertFalse(publisher._dirty.is_set())
        self.assertEqual(
            publisher._status_retry_delay,
            self.module._RING_STATUS_RETRY_INITIAL_SECONDS,
        )
        self.assertEqual(publisher._status_retry_not_before, 0.0)
        self.assertFalse(publisher._status_persist_failure_logged)

        publisher._increment("accepted")
        with (
            mock.patch.object(
                publisher,
                "_persist_status",
                side_effect=ValueError("unsafe status path"),
            ),
            mock.patch.object(publisher, "_log_result", log_result),
        ):
            self.assertFalse(publisher._persist_status_if_due(now=5000.0))
        self.assertEqual(
            log_result.call_args_list,
            [
                mock.call("status_persist_failure"),
                mock.call("status_persist_failure"),
            ],
        )

    def test_v1_quarantine_history_recovers_after_clean_inventory_reconciliation(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            status_path = Path(directory) / "state" / "ring-producer.json"
            status_path.parent.mkdir(mode=0o700)
            status_path.write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "updated_at": "2026-07-24T20:00:00Z",
                        "health": "degraded",
                        "counters": {
                            "accepted": 33,
                            "published": 33,
                            "failed": 0,
                            "dropped": 0,
                            "quarantined": 8,
                        },
                    }
                ),
                encoding="utf-8",
            )
            status_path.chmod(0o600)
            publisher = self.module._RingHomeEventPublisher(
                "/fake/home-eventctl",
                status_path=status_path,
            )

            publisher._load_status()
            self.assertEqual(publisher._health_locked(), "degraded")
            publisher.reconcile_device_bindings(has_unbound_devices=False)
            publisher._persist_status()

            status = json.loads(status_path.read_text(encoding="utf-8"))
            self.assertEqual(status["schema_version"], 1)
            self.assertEqual(
                set(status),
                {"schema_version", "updated_at", "health", "counters"},
            )
            self.assertEqual(status["health"], "ok")
            self.assertEqual(status["counters"]["quarantined"], 8)
            self.assertEqual(status["counters"]["published"], 33)

    def test_active_unbound_device_stays_degraded_until_reconciled(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            status_path = Path(directory) / "state" / "ring-producer.json"
            status_path.parent.mkdir(mode=0o700)
            publisher = self.module._RingHomeEventPublisher(
                "/fake/home-eventctl",
                status_path=status_path,
            )

            publisher.reconcile_device_bindings(has_unbound_devices=True)
            publisher.quarantine_unknown_device()
            publisher._increment("published")
            publisher._persist_status()

            degraded = json.loads(status_path.read_text(encoding="utf-8"))
            self.assertEqual(degraded["health"], "degraded")
            self.assertEqual(degraded["schema_version"], 1)
            self.assertEqual(
                set(degraded),
                {"schema_version", "updated_at", "health", "counters"},
            )
            self.assertEqual(degraded["counters"]["quarantined"], 1)

            publisher.reconcile_device_bindings(has_unbound_devices=False)
            publisher._persist_status()

            recovered = json.loads(status_path.read_text(encoding="utf-8"))
            self.assertEqual(recovered["health"], "ok")
            self.assertEqual(recovered["counters"]["quarantined"], 1)

    def test_delivery_failure_and_drop_recover_only_after_publish(self) -> None:
        for outcome in ("failed", "dropped"):
            with self.subTest(outcome=outcome), tempfile.TemporaryDirectory() as directory:
                status_path = Path(directory) / "state" / "ring-producer.json"
                status_path.parent.mkdir(mode=0o700)
                publisher = self.module._RingHomeEventPublisher(
                    "/fake/home-eventctl",
                    status_path=status_path,
                )
                publisher.reconcile_device_bindings(has_unbound_devices=False)

                publisher._increment(outcome)
                publisher._persist_status()
                degraded = json.loads(status_path.read_text(encoding="utf-8"))
                self.assertEqual(degraded["health"], "degraded")
                self.assertEqual(degraded["schema_version"], 1)
                self.assertEqual(degraded["counters"][outcome], 1)

                publisher._increment("published")
                publisher._persist_status()
                recovered = json.loads(status_path.read_text(encoding="utf-8"))
                self.assertEqual(recovered["health"], "ok")
                self.assertEqual(
                    set(recovered),
                    {"schema_version", "updated_at", "health", "counters"},
                )
                self.assertEqual(recovered["counters"][outcome], 1)


if __name__ == "__main__":
    unittest.main()
