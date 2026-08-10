#!/usr/bin/env python3

from __future__ import annotations

import importlib.util
import json
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock


REPO_ROOT = Path(__file__).resolve().parents[2]
MODULE_PATH = (
    REPO_ROOT
    / "openclaw"
    / "skills"
    / "airthings-monitor"
    / "scripts"
    / "airthings.py"
)
SPEC = importlib.util.spec_from_file_location("airthings_monitor", MODULE_PATH)
assert SPEC and SPEC.loader
airthings = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(airthings)


class AirthingsNormalizationTests(unittest.TestCase):
    def test_wave_enhance_reading_is_normalized_and_classified(self) -> None:
        binding = {
            "alias": "cabin-living-room-airthings",
            "site": "cabin",
            "room": "Living Room",
        }
        device = SimpleNamespace(
            sensors={
                "temperature": 21.27,
                "humidity": 33.75,
                "co2": 732,
                "voc": 277,
                "pressure": 974.97,
                "noise": 39,
                "lux": 1,
                "battery": 81,
            }
        )

        result = airthings.normalize_reading(binding, device)

        self.assertEqual(result["temperature_f"], 70.3)
        self.assertEqual(result["co2_ppm"], 732)
        self.assertEqual(result["voc_ppb"], 277)
        self.assertEqual(result["battery_percent"], 81)
        self.assertEqual(result["air_quality"]["co2"], "good")
        self.assertEqual(result["air_quality"]["voc"], "fair")
        self.assertEqual(result["air_quality"]["overall"], "fair")
        self.assertNotIn("address", result)
        self.assertNotIn("identifier", result)

    def test_unknown_values_remain_unknown_instead_of_zero(self) -> None:
        binding = {"alias": "sensor", "site": "cabin", "room": "Living Room"}

        result = airthings.normalize_reading(binding, SimpleNamespace(sensors={}))

        self.assertIsNone(result["temperature_f"])
        self.assertIsNone(result["co2_ppm"])
        self.assertEqual(result["air_quality"]["overall"], "unknown")


class AirthingsConfigTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tempdir.cleanup)
        self.root = Path(self.tempdir.name)
        self.config = self.root / "config.json"
        self.state = self.root / "state"
        self.monitor = airthings.AirthingsMonitor(
            config_file=self.config,
            state_dir=self.state,
            cache_ttl_seconds=300,
        )

    def write_config(self, mode: int = 0o600) -> None:
        self.config.write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "devices": [
                        {
                            "alias": "cabin-living-room-airthings",
                            "site": "cabin",
                            "room": "Living Room",
                            "model": "Wave Enhance",
                            "address": "private-corebluetooth-identifier",
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )
        self.config.chmod(mode)

    def test_config_requires_owner_only_permissions(self) -> None:
        self.write_config(0o644)

        with self.assertRaises(airthings.AirthingsError) as caught:
            self.monitor.load_config()

        self.assertEqual(caught.exception.category, "unsafe_config")

    def test_status_uses_fresh_cache_without_touching_ble(self) -> None:
        self.write_config()
        binding = self.monitor.load_config()[0]
        reading = airthings.normalize_reading(
            binding,
            SimpleNamespace(
                sensors={"temperature": 20, "humidity": 40, "co2": 600, "voc": 100}
            ),
        )
        self.monitor._save_cache([reading])

        with mock.patch.object(
            self.monitor,
            "_read_address",
            side_effect=AssertionError("BLE should not be called"),
        ):
            result = __import__("asyncio").run(self.monitor.status(None, False))

        self.assertTrue(result["devices"][0]["cached"])
        self.assertGreaterEqual(result["devices"][0]["cache_age_seconds"], 0)

    def test_refresh_returns_safe_offline_category(self) -> None:
        self.write_config()

        async def unavailable(binding):
            return airthings.offline_reading(binding, "bluetooth_unauthorized")

        with mock.patch.object(self.monitor, "_read_address", side_effect=unavailable):
            result = __import__("asyncio").run(self.monitor.status(None, True))

        device = result["devices"][0]
        self.assertFalse(device["online"])
        self.assertEqual(device["error"], "bluetooth_unauthorized")
        self.assertNotIn("address", device)

    def test_cache_only_never_touches_ble_when_cache_is_missing(self) -> None:
        self.write_config()

        with mock.patch.object(
            self.monitor,
            "_read_address",
            side_effect=AssertionError("BLE should not be called"),
        ):
            result = __import__("asyncio").run(
                self.monitor.status(None, False, cache_only=True)
            )

        self.assertFalse(result["devices"][0]["online"])
        self.assertEqual(result["devices"][0]["error"], "cache_unavailable")

    def test_enrollment_requires_explicit_operator_gate(self) -> None:
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("AIRTHINGS_ALLOW_ENROLL", None)
            with self.assertRaises(airthings.AirthingsError) as caught:
                __import__("asyncio").run(
                    self.monitor.enroll(
                        "cabin-living-room-airthings", "cabin", "Living Room", False
                    )
                )

        self.assertEqual(caught.exception.category, "enrollment_not_authorized")


if __name__ == "__main__":
    unittest.main()
