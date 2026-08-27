#!/usr/bin/env python3
"""Fake-only tests for the legacy Ring status wrapper."""

from __future__ import annotations

import ast
import asyncio
import contextlib
import importlib.util
import io
import json
import stat
import sys
import tempfile
import time
import types
import unittest
from unittest import mock
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
API_PATH = REPO_ROOT / "openclaw" / "skills" / "ring-doorbell" / "ring-api.py"
RUNTIME_PATH = (
    REPO_ROOT / "openclaw" / "skills" / "dog-walk" / "service-runtime.py"
)


def load_api():
    fake_ring = types.ModuleType("ring_doorbell")

    class Placeholder:
        pass

    fake_ring.Auth = Placeholder
    fake_ring.Ring = Placeholder
    fake_ring.Requires2FAError = Placeholder
    fake_ring.AuthenticationError = Placeholder
    previous = sys.modules.get("ring_doorbell")
    try:
        sys.modules["ring_doorbell"] = fake_ring
        spec = importlib.util.spec_from_file_location(
            "ring_doorbell_api_test", API_PATH
        )
        assert spec and spec.loader
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    finally:
        if previous is None:
            sys.modules.pop("ring_doorbell", None)
        else:
            sys.modules["ring_doorbell"] = previous


class FakeDevices:
    def __init__(self, doorbell) -> None:
        self.doorbots = [doorbell]
        self.authorized_doorbots = []
        self.video_devices = [doorbell]


class FakeRing:
    def __init__(self, doorbell) -> None:
        self._devices = FakeDevices(doorbell)

    def devices(self):
        return self._devices


class BaseDoorbell:
    name = "Front Door"
    model = "Fake Doorbell"
    id = 123
    family = "doorbots"
    firmware = "1.2.3"
    address = "Example"
    timezone = "UTC"
    battery_life = 95
    wifi_name = "Private Network"
    wifi_signal_strength = -50
    wifi_signal_category = "good"

    async def async_history(self, *, limit):
        self.history_limit = limit
        return []


class MissingChimeDoorbell(BaseDoorbell):
    @property
    def existing_doorbell_type(self):
        raise KeyError("subscribed")


class MechanicalChimeDoorbell(BaseDoorbell):
    existing_doorbell_type = "mechanical"


class SnapshotDoorbell(BaseDoorbell):
    async def async_take_snapshot(self, *, max_age, max_wait):
        self.snapshot_request = (max_age, max_wait)
        return b"\xff\xd8\xfffixture\xff\xd9"


class FakeSnapshotResponse:
    content = b"\xff\xd8\xfffallback\xff\xd9"


class FakeSnapshotTransport:
    async def async_query(self, url, **kwargs):
        self.request = (url, kwargs)
        return FakeSnapshotResponse()


class LegacySnapshotDoorbell(BaseDoorbell):
    def __init__(self):
        self._ring = FakeSnapshotTransport()


class RingDoorbellApiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.module = load_api()

    def status(self, doorbell) -> dict:
        async def fake_get_ring():
            return FakeRing(doorbell)

        self.module.get_ring = fake_get_ring
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            asyncio.run(self.module.cmd_status())
        return json.loads(output.getvalue())

    def test_status_omits_missing_optional_chime_property(self) -> None:
        doorbell = MissingChimeDoorbell()

        payload = self.status(doorbell)

        self.assertEqual(len(payload["doorbells"]), 1)
        self.assertNotIn("chimeType", payload["doorbells"][0])
        self.assertEqual(doorbell.history_limit, 1)

    def test_status_includes_available_chime_property(self) -> None:
        payload = self.status(MechanicalChimeDoorbell())

        self.assertEqual(payload["doorbells"][0]["chimeType"], "mechanical")

    def test_camera_bindings_match_the_event_listener(self) -> None:
        tree = ast.parse(RUNTIME_PATH.read_text(encoding="utf-8"))
        event_bindings = None
        for node in tree.body:
            if (
                isinstance(node, ast.Assign)
                and len(node.targets) == 1
                and isinstance(node.targets[0], ast.Name)
                and node.targets[0].id == "RING_EVENT_DEVICES"
            ):
                event_bindings = ast.literal_eval(node.value)
                break
        self.assertIsNotNone(event_bindings)
        expected = {
            (binding["site"], binding["entity_alias"]): provider_id
            for provider_id, binding in event_bindings.items()
        }
        self.assertEqual(self.module.RING_CAMERA_BINDINGS, expected)

    def test_bound_snapshot_uses_safe_alias_and_private_file(self) -> None:
        doorbell = SnapshotDoorbell()

        async def fake_get_ring():
            return FakeRing(doorbell)

        self.module.get_ring = fake_get_ring
        previous = self.module.RING_CAMERA_BINDINGS
        self.module.RING_CAMERA_BINDINGS = {("cabin", "driveway"): doorbell.id}
        self.addCleanup(
            setattr, self.module, "RING_CAMERA_BINDINGS", previous
        )
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "ring.jpg"
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                asyncio.run(
                    self.module.cmd_snapshot_bound(
                        "cabin", "driveway", str(path)
                    )
                )
            payload = json.loads(output.getvalue())

            self.assertEqual(
                payload,
                {
                    "ok": True,
                    "site": "cabin",
                    "alias": "driveway",
                    "size": len(b"\xff\xd8\xfffixture\xff\xd9"),
                },
            )
            self.assertEqual(doorbell.snapshot_request, (5, 15))
            self.assertEqual(path.read_bytes(), b"\xff\xd8\xfffixture\xff\xd9")
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
            self.assertNotIn(str(doorbell.id), output.getvalue())
            self.assertNotIn(doorbell.name, output.getvalue())

    def test_fresh_snapshot_falls_back_to_reliable_ring_endpoint(self) -> None:
        doorbell = LegacySnapshotDoorbell()

        with mock.patch.object(time, "time", return_value=1_000):
            data = asyncio.run(self.module.take_fresh_snapshot(doorbell))

        self.assertEqual(data, FakeSnapshotResponse.content)
        url, kwargs = doorbell._ring.request
        self.assertEqual(url, "/snapshots/next/123")
        self.assertEqual(
            kwargs,
            {
                "extra_params": {
                    "after-ms": 995_000,
                    "max-wait-ms": 15_000,
                    "extras": "force",
                },
                "base_uri": "https://app-snaps.ring.com",
                "timeout": 16,
            },
        )

    def test_bound_snapshot_rejects_unknown_safe_alias_before_auth(self) -> None:
        async def unexpected_get_ring():
            self.fail("unknown bindings must fail before Ring authentication")

        self.module.get_ring = unexpected_get_ring
        output = io.StringIO()
        with contextlib.redirect_stdout(output), self.assertRaises(SystemExit):
            asyncio.run(
                self.module.cmd_snapshot_bound(
                    "crosstown", "driveway", "/tmp/never-written.jpg"
                )
            )
        self.assertEqual(
            json.loads(output.getvalue()), {"error": "camera_binding_invalid"}
        )


if __name__ == "__main__":
    unittest.main()
