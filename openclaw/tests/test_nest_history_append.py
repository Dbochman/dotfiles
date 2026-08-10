#!/usr/bin/env python3
"""Tests for the shared, locked climate-history appender."""

from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
APPENDER = REPO_ROOT / "openclaw" / "bin" / "nest-history-append"


class NestHistoryAppendTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tempdir.cleanup)
        self.history = Path(self.tempdir.name) / "history"

    @staticmethod
    def record(timestamp: str, source: str = "airthings") -> dict:
        return {
            "timestamp": timestamp,
            "rooms": [
                {
                    "structure": "Philly",
                    "room": "Living Room",
                    "source": source,
                }
            ],
        }

    def run_append(self, record: dict, *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [str(APPENDER), "--history-dir", str(self.history), *args],
            input=json.dumps(record),
            capture_output=True,
            text=True,
            check=False,
        )

    def test_append_creates_private_utc_daily_history_and_lock(self) -> None:
        result = self.run_append(self.record("2026-08-10T23:59:00-04:00"))

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)["outcome"], "appended")
        daily = self.history / "2026-08-11.jsonl"
        self.assertEqual(json.loads(daily.read_text()), self.record("2026-08-10T23:59:00-04:00"))
        self.assertEqual(daily.stat().st_mode & 0o777, 0o600)
        self.assertEqual((self.history / ".history.lock").stat().st_mode & 0o777, 0o600)

    def test_exact_source_room_timestamp_is_idempotent(self) -> None:
        record = self.record("2026-08-10T14:00:00Z")
        args = (
            "--dedupe-source",
            "airthings",
            "--dedupe-structure",
            "Philly",
            "--dedupe-room",
            "Living Room",
        )

        first = self.run_append(record, *args)
        second = self.run_append(record, *args)

        self.assertEqual(first.returncode, 0, first.stderr)
        self.assertEqual(json.loads(second.stdout)["outcome"], "duplicate")
        daily = self.history / "2026-08-10.jsonl"
        self.assertEqual(len(daily.read_text().splitlines()), 1)

    def test_concurrent_writers_preserve_every_complete_record(self) -> None:
        records = [
            self.record(f"2026-08-10T14:{minute:02d}:00Z", source="nest")
            for minute in range(16)
        ]
        with ThreadPoolExecutor(max_workers=16) as pool:
            results = list(pool.map(self.run_append, records))

        self.assertTrue(all(result.returncode == 0 for result in results), results)
        lines = (self.history / "2026-08-10.jsonl").read_text().splitlines()
        self.assertEqual(len(lines), 16)
        self.assertTrue(all(isinstance(json.loads(line), dict) for line in lines))

    def test_invalid_record_fails_without_creating_a_daily_file(self) -> None:
        result = self.run_append({"timestamp": "not-a-time", "rooms": []})

        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(json.loads(result.stderr)["error"], "invalid_record_shape")
        self.assertEqual(list(self.history.glob("*.jsonl")), [])


if __name__ == "__main__":
    unittest.main()
