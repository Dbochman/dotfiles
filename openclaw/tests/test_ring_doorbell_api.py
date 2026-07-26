#!/usr/bin/env python3
"""Fake-only tests for the legacy Ring status wrapper."""

from __future__ import annotations

import asyncio
import contextlib
import importlib.util
import io
import json
import sys
import types
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
API_PATH = REPO_ROOT / "openclaw" / "skills" / "ring-doorbell" / "ring-api.py"


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


if __name__ == "__main__":
    unittest.main()
