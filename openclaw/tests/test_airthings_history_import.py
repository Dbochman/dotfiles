#!/usr/bin/env python3

from __future__ import annotations

import importlib.util
import json
import os
import stat
import tempfile
import unittest
from pathlib import Path
from unittest import mock


REPO_ROOT = Path(__file__).resolve().parents[2]
MODULE_PATH = (
    REPO_ROOT
    / "openclaw"
    / "skills"
    / "airthings-monitor"
    / "scripts"
    / "airthings_history_import.py"
)
SPEC = importlib.util.spec_from_file_location("airthings_history_import", MODULE_PATH)
assert SPEC and SPEC.loader
history_import = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(history_import)


CSV_HEADER = (
    "recorded;CO2 ppm;HUMIDITY %;TEMP °F;VOC ppb;PRESSURE inHg;"
    "SOUND_LEVEL_A dBSPL;LUX lux\n"
)


class AirthingsHistoryImportTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tempdir.cleanup)
        self.root = Path(self.tempdir.name)
        self.csv_file = self.root / "airthings.csv"
        self.csv_file.write_text(
            CSV_HEADER
            + "2026-08-01T00:02:10;750;42.5;71.6;275;29.92;38.2;12\n"
            + "2026-08-01T00:07:10;1050;65;72.5;125;29.91;39;8\n",
            encoding="utf-8",
        )
        self.history_dir = self.root / "history"
        self.backup_root = self.root / "backups"

    def test_export_schema_is_normalized_to_climate_rows(self) -> None:
        samples = history_import.load_export(self.csv_file)

        self.assertEqual(len(samples), 2)
        self.assertEqual(samples[0]["timestamp"], "2026-08-01T00:02:10Z")
        room = samples[0]["rooms"][0]
        self.assertEqual(room["structure"], "Philly")
        self.assertEqual(room["source"], "airthings")
        self.assertEqual(room["co2_ppm"], 750)
        self.assertEqual(room["voc_ppb"], 275)
        self.assertEqual(room["temp_c"], 22.0)
        self.assertEqual(room["pressure_hpa"], 1013.2)
        self.assertEqual(room["air_quality"]["overall"], "fair")
        self.assertEqual(room["history_origin"], "airthings_csv_v1")

    def test_prepare_import_is_dry_run_and_preserves_existing_records(self) -> None:
        self.history_dir.mkdir()
        existing = {
            "timestamp": "2026-08-01T00:00:00Z",
            "rooms": [{"structure": "Philly", "room": "Bedroom", "source": "nest"}],
        }
        history_path = self.history_dir / "2026-08-01.jsonl"
        original = json.dumps(existing) + "\n"
        history_path.write_text(original, encoding="utf-8")

        merged, counts = history_import.prepare_import(
            history_import.load_export(self.csv_file), self.history_dir
        )

        self.assertEqual(counts["added"], 2)
        self.assertEqual(counts["duplicates"], 0)
        self.assertEqual(len(merged["2026-08-01"]), 3)
        self.assertEqual(history_path.read_text(encoding="utf-8"), original)
        self.assertFalse(self.backup_root.exists())

    def test_apply_requires_explicit_environment_gate(self) -> None:
        samples = history_import.load_export(self.csv_file)
        merged, counts = history_import.prepare_import(samples, self.history_dir)
        summary = {
            **counts,
            "first_timestamp": samples[0]["timestamp"],
            "last_timestamp": samples[-1]["timestamp"],
        }

        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("AIRTHINGS_ALLOW_HISTORY_IMPORT", None)
            with self.assertRaises(history_import.ImportErrorSafe) as caught:
                history_import.apply_import(
                    merged,
                    self.history_dir,
                    self.backup_root,
                    self.csv_file,
                    summary,
                )

        self.assertEqual(caught.exception.category, "import_not_authorized")

    def test_apply_is_backed_up_private_and_idempotent(self) -> None:
        self.history_dir.mkdir()
        existing = {
            "timestamp": "2026-08-01T00:00:00Z",
            "rooms": [{"structure": "Philly", "room": "Bedroom", "source": "nest"}],
        }
        history_path = self.history_dir / "2026-08-01.jsonl"
        original = json.dumps(existing) + "\n"
        history_path.write_text(original, encoding="utf-8")
        samples = history_import.load_export(self.csv_file)
        merged, counts = history_import.prepare_import(samples, self.history_dir)
        summary = {
            **counts,
            "first_timestamp": samples[0]["timestamp"],
            "last_timestamp": samples[-1]["timestamp"],
        }

        with mock.patch.dict(
            os.environ, {"AIRTHINGS_ALLOW_HISTORY_IMPORT": "1"}, clear=False
        ):
            backup = history_import.apply_import(
                merged,
                self.history_dir,
                self.backup_root,
                self.csv_file,
                summary,
            )

        self.assertIsNotNone(backup)
        assert backup is not None
        self.assertEqual((backup / history_path.name).read_text(encoding="utf-8"), original)
        self.assertTrue((backup / "manifest.json").is_file())
        self.assertEqual(stat.S_IMODE(history_path.stat().st_mode), 0o600)
        records = [
            json.loads(line)
            for line in history_path.read_text(encoding="utf-8").splitlines()
        ]
        self.assertEqual(len(records), 3)
        self.assertEqual(records, sorted(records, key=lambda item: item["timestamp"]))

        second_merged, second_counts = history_import.prepare_import(
            samples, self.history_dir
        )
        self.assertEqual(second_merged, {})
        self.assertEqual(second_counts["added"], 0)
        self.assertEqual(second_counts["duplicates"], 2)


if __name__ == "__main__":
    unittest.main()
