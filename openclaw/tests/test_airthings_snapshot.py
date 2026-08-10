#!/usr/bin/env python3
"""Tests for the dedicated five-minute Airthings history sampler."""

from __future__ import annotations

import json
import os
import stat
import subprocess
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
SNAPSHOT = (
    REPO_ROOT
    / "openclaw"
    / "skills"
    / "airthings-monitor"
    / "scripts"
    / "airthings_snapshot.py"
)
APPENDER = REPO_ROOT / "openclaw" / "bin" / "nest-history-append"


class AirthingsSnapshotTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tempdir.cleanup)
        self.root = Path(self.tempdir.name)
        self.home = self.root / "home"
        self.home.mkdir()
        self.history = self.home / ".openclaw" / "nest-history"
        self.status_file = self.home / ".openclaw" / "airthings" / "snapshot-status.json"
        self.airthings = self.root / "airthings"

    def payload(self, *, online: bool = True) -> dict:
        return {
            "ok": True,
            "devices": [
                {
                    "alias": "cabin-living-room-airthings",
                    "site": "cabin",
                    "room": "Living Room",
                    "model": "Wave Enhance",
                    "online": online,
                    "cached": True,
                    "cache_age_seconds": 3,
                    "read_at": datetime.now(timezone.utc)
                    .isoformat(timespec="seconds")
                    .replace("+00:00", "Z"),
                    "temperature_c": 21.27,
                    "temperature_f": 70.3,
                    "humidity_percent": 33.8,
                    "co2_ppm": 732,
                    "voc_ppb": 277,
                    "pressure_hpa": 975.0,
                    "noise_dba": 39,
                    "light_lux": 1,
                    "battery_percent": 81,
                    "air_quality": {
                        "overall": "fair",
                        "co2": "good",
                        "voc": "fair",
                        "humidity": "good",
                        "private_detail": "must-not-escape",
                    },
                    "serial": "must-not-escape",
                    "error": None if online else "device_not_found",
                }
            ],
        }

    def write_airthings(self, payload: dict) -> None:
        self.airthings.write_text(
            "#!/bin/sh\nprintf '%s\\n' "
            + repr(json.dumps(payload, separators=(",", ":")))
            + "\n",
            encoding="utf-8",
        )
        self.airthings.chmod(self.airthings.stat().st_mode | stat.S_IXUSR)

    def run_snapshot(self) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["/usr/bin/python3", str(SNAPSHOT)],
            capture_output=True,
            text=True,
            check=False,
            env={
                **os.environ,
                "HOME": str(self.home),
                "AIRTHINGS_BIN": str(self.airthings),
                "NEST_HISTORY_APPEND_BIN": str(APPENDER),
                "NEST_HISTORY_DIR": str(self.history),
                "AIRTHINGS_SNAPSHOT_STATUS_FILE": str(self.status_file),
            },
        )

    def test_fresh_exact_read_is_appended_and_health_is_private(self) -> None:
        self.write_airthings(self.payload())

        result = self.run_snapshot()

        self.assertEqual(result.returncode, 0, result.stderr)
        health = json.loads(result.stdout)
        self.assertEqual(health["outcome"], "appended")
        self.assertNotIn("co2", result.stdout)
        self.assertNotIn("must-not-escape", result.stdout)
        self.assertEqual(self.status_file.stat().st_mode & 0o777, 0o600)
        daily = next(self.history.glob("*.jsonl"))
        record = json.loads(daily.read_text())
        self.assertEqual(record["history_origin"], "airthings_ble_sampler_v1")
        self.assertEqual(len(record["rooms"]), 1)
        room = record["rooms"][0]
        self.assertEqual(room["source"], "airthings")
        self.assertEqual(room["structure"], "Philly")
        self.assertEqual(room["co2_ppm"], 732)
        self.assertEqual(room["voc_ppb"], 277)
        self.assertNotIn("serial", room)
        self.assertNotIn("private_detail", room["air_quality"])

    def test_same_sensor_timestamp_is_not_duplicated(self) -> None:
        self.write_airthings(self.payload())

        first = self.run_snapshot()
        second = self.run_snapshot()

        self.assertEqual(first.returncode, 0, first.stderr)
        self.assertEqual(json.loads(second.stdout)["outcome"], "duplicate")
        daily = next(self.history.glob("*.jsonl"))
        self.assertEqual(len(daily.read_text().splitlines()), 1)

    def test_unavailable_device_writes_safe_health_but_no_measurement(self) -> None:
        self.write_airthings(self.payload(online=False))

        result = self.run_snapshot()

        self.assertNotEqual(result.returncode, 0)
        health = json.loads(self.status_file.read_text())
        self.assertFalse(health["ok"])
        self.assertEqual(health["error"], "device_unavailable")
        self.assertNotIn("device_not_found", self.status_file.read_text())
        self.assertEqual(list(self.history.glob("*.jsonl")), [])


if __name__ == "__main__":
    unittest.main()
