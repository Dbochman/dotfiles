#!/usr/bin/env python3
"""Offline contracts for the protected Plant Tracker skill."""

from __future__ import annotations

from contextlib import redirect_stderr, redirect_stdout
import importlib.machinery
import importlib.util
import io
import json
import os
from pathlib import Path
import stat
import tempfile
import unittest


REPO_ROOT = Path(__file__).resolve().parents[2]
SKILL_DIR = REPO_ROOT / "openclaw" / "skills" / "plant-tracker"
HELPER = SKILL_DIR / "scripts" / "plant_tracker.py"
SKILL = SKILL_DIR / "SKILL.md"
INSTALLER = REPO_ROOT / "install.sh"
WRAPPER = REPO_ROOT / "openclaw" / "bin" / "plant-tracker"


def load_helper():
    loader = importlib.machinery.SourceFileLoader(
        "plant_tracker_helper",
        str(HELPER),
    )
    spec = importlib.util.spec_from_loader(loader.name, loader)
    if spec is None:
        raise RuntimeError("could not load Plant Tracker helper")
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


class PlantTrackerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.openclaw = self.root / ".openclaw"
        self.openclaw.mkdir(mode=0o700)
        self.workspace = self.openclaw / "workspace"
        self.workspace.mkdir(mode=0o700)
        self.helper = load_helper()
        self.helper.RUNTIME_DIRECTORY = self.openclaw / "plant-tracker"
        self.helper.DATABASE_PATH = (
            self.helper.RUNTIME_DIRECTORY / "plants.json"
        )
        self.helper.LOCK_PATH = self.helper.RUNTIME_DIRECTORY / ".lock"
        self.helper.EXPORT_DIRECTORY = (
            self.workspace / "exports" / "plant-tracker"
        )

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_private_atomic_inventory_and_care(self) -> None:
        self.assertEqual(
            self.helper.initialize(),
            {"initialized": True, "count": 0},
        )
        self.assertEqual(
            stat.S_IMODE(self.helper.RUNTIME_DIRECTORY.stat().st_mode),
            0o700,
        )
        self.assertEqual(
            stat.S_IMODE(self.helper.DATABASE_PATH.stat().st_mode),
            0o600,
        )

        added = self.helper.add_plant(
            "Cabin Lavender",
            species="Lavandula",
            location="Cabin front bed",
            planted=None,
            notes="Julia will confirm the planting date.",
        )
        self.assertIs(added["added"], True)
        with self.assertRaisesRegex(
            self.helper.PublicError,
            "^Plant already exists$",
        ):
            self.helper.add_plant(
                "cabin lavender",
                species=None,
                location=None,
                planted=None,
                notes=None,
            )

        recorded = self.helper.record_care(
            "CABIN LAVENDER",
            action="inspect",
            notes="No visible issues reported.",
        )
        self.assertIs(recorded["recorded"], True)
        listing = self.helper.list_plants()
        self.assertEqual(listing["count"], 1)
        self.assertEqual(listing["plants"][0]["lastCare"], "inspect")
        shown = self.helper.show_plant("Cabin Lavender")["plant"]
        self.assertEqual(len(shown["careHistory"]), 1)

    def test_invalid_database_fails_closed_without_overwrite(self) -> None:
        self.helper.initialize()
        corrupt = b'{"version":1,"plants":"not-a-list"}\n'
        self.helper.DATABASE_PATH.write_bytes(corrupt)
        self.helper.DATABASE_PATH.chmod(0o600)

        with self.assertRaises(self.helper.PublicError):
            self.helper.add_plant(
                "Do Not Add",
                species=None,
                location=None,
                planted=None,
                notes=None,
            )
        self.assertEqual(self.helper.DATABASE_PATH.read_bytes(), corrupt)

    def test_database_symlinks_fail_closed(self) -> None:
        self.helper.RUNTIME_DIRECTORY.mkdir(mode=0o700)
        outside = self.root / "outside.json"
        outside.write_text("do not replace", encoding="utf-8")
        self.helper.DATABASE_PATH.symlink_to(outside)

        with self.assertRaisesRegex(
            self.helper.PublicError,
            "^Plant database is unavailable$",
        ):
            self.helper.initialize()
        self.assertEqual(
            outside.read_text(encoding="utf-8"),
            "do not replace",
        )
        self.assertTrue(self.helper.DATABASE_PATH.is_symlink())

    def test_exports_are_private_bounded_and_no_clobber_by_default(self) -> None:
        self.helper.add_plant(
            "Flower Bed Roses",
            species="Rosa",
            location="Cabin driveway bed",
            planted="2026-05-01",
            notes=None,
        )
        result = self.helper.export_plants(
            "cabin-plants.md",
            overwrite=False,
        )
        output = Path(result["mediaPath"])
        self.assertEqual(output.parent, self.helper.EXPORT_DIRECTORY)
        self.assertEqual(stat.S_IMODE(output.stat().st_mode), 0o600)
        self.assertIn("Flower Bed Roses", output.read_text(encoding="utf-8"))

        with self.assertRaisesRegex(
            self.helper.PublicError,
            "^Plant export already exists$",
        ):
            self.helper.export_plants(
                "cabin-plants.md",
                overwrite=False,
            )
        for unsafe in ("../outside.md", ".hidden.md", "notes.txt"):
            with self.subTest(filename=unsafe):
                with self.assertRaisesRegex(
                    self.helper.PublicError,
                    "^Plant export filename is invalid$",
                ):
                    self.helper.export_plants(unsafe, overwrite=False)

    def test_cli_returns_one_json_object_or_one_safe_error(self) -> None:
        stdout = io.StringIO()
        stderr = io.StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            status = self.helper.main(["init"])
        self.assertEqual(status, 0)
        self.assertEqual(
            json.loads(stdout.getvalue()),
            {"initialized": True, "count": 0},
        )
        self.assertEqual(len(stdout.getvalue().splitlines()), 1)
        self.assertEqual(stderr.getvalue(), "")

        stdout = io.StringIO()
        stderr = io.StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            status = self.helper.main(
                ["care", "missing", "--action", "water"]
            )
        self.assertEqual(status, 1)
        self.assertEqual(stdout.getvalue(), "")
        self.assertEqual(stderr.getvalue(), "Plant was not found\n")

    def test_skill_and_installer_expose_guarded_workflow(self) -> None:
        skill = SKILL.read_text(encoding="utf-8")
        for required in (
            "currently verified Dylan or Julia",
            "Do not create or change a record from model inference",
            "reolink-camera",
            "no records will be created until the details are confirmed",
            "plant-tracker add",
            "plant-tracker care",
            "existing export",
        ):
            with self.subTest(required=required):
                self.assertIn(required, skill)

        installer = INSTALLER.read_text(encoding="utf-8")
        self.assertIn(
            '"bin/plant-tracker|bin/plant-tracker|755|'
            'private plant tracker wrapper"',
            installer,
        )
        self.assertTrue(WRAPPER.read_text(encoding="utf-8").startswith("#!"))


if __name__ == "__main__":
    unittest.main()
