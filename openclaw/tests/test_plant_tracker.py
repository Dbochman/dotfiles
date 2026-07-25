#!/usr/bin/env python3
"""Offline contracts for the protected Plant Tracker skill."""

from __future__ import annotations

from contextlib import redirect_stderr, redirect_stdout
import importlib.machinery
import importlib.util
import io
import json
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

    def add(
        self,
        name: str,
        *,
        species: str | None = None,
        location: str | None = "Cabin",
        bed: str | None = None,
        cameras: list[str] | None = None,
        planted: str | None = None,
        notes: str | None = None,
    ):
        return self.helper.add_plant(
            name,
            species=species,
            location=location,
            bed=bed,
            camera_views=cameras,
            planted=planted,
            notes=notes,
        )

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

        added = self.add(
            "Cabin Lavender",
            species="Lavandula",
            location="Cabin front yard",
            bed="East flower bed",
            cameras=["Flower Cam #1"],
            notes="Julia will confirm the planting date.",
        )
        self.assertIs(added["added"], True)
        self.assertEqual(
            added["plant"]["cameraViews"],
            ["Flower Cam #1"],
        )
        with self.assertRaisesRegex(
            self.helper.PublicError,
            "^Plant already exists$",
        ):
            self.add("cabin lavender")

        recorded = self.helper.record_care(
            "CABIN LAVENDER",
            action="inspect",
            notes="No visible issues reported.",
        )
        self.assertIs(recorded["recorded"], True)
        listing = self.helper.list_plants()
        self.assertEqual(listing["count"], 1)
        self.assertEqual(
            listing["plants"][0]["bed"],
            "East flower bed",
        )
        self.assertEqual(listing["plants"][0]["lastCare"], "inspect")
        shown = self.helper.show_plant("Cabin Lavender")["plant"]
        self.assertEqual(len(shown["careHistory"]), 1)

    def test_explicit_v1_migration_preserves_records(self) -> None:
        self.helper.RUNTIME_DIRECTORY.mkdir(mode=0o700)
        legacy = {
            "version": 1,
            "plants": [
                {
                    "name": "Legacy Rose",
                    "species": "Rosa",
                    "location": "Cabin",
                    "planted": "2026-05-01",
                    "notes": None,
                    "createdAt": "2026-07-25T12:00:00Z",
                    "careHistory": [
                        {
                            "action": "water",
                            "recordedAt": "2026-07-25T13:00:00Z",
                            "notes": "Confirmed.",
                        }
                    ],
                }
            ],
        }
        self.helper.DATABASE_PATH.write_text(
            json.dumps(legacy) + "\n",
            encoding="utf-8",
        )
        self.helper.DATABASE_PATH.chmod(0o600)

        with self.assertRaisesRegex(
            self.helper.PublicError,
            "^Plant database is invalid$",
        ):
            self.helper.list_plants()
        result = self.helper.migrate_database()
        self.assertEqual(
            result,
            {
                "migrated": True,
                "fromVersion": 1,
                "toVersion": 2,
                "count": 1,
            },
        )
        plant = self.helper.show_plant("Legacy Rose")["plant"]
        self.assertIsNone(plant["bed"])
        self.assertEqual(plant["cameraViews"], [])
        self.assertEqual(len(plant["careHistory"]), 1)
        self.assertEqual(
            self.helper.migrate_database()["migrated"],
            False,
        )

    def test_invalid_database_fails_closed_without_overwrite(self) -> None:
        self.helper.initialize()
        corrupt = b'{"version":2,"plants":"not-a-list"}\n'
        self.helper.DATABASE_PATH.write_bytes(corrupt)
        self.helper.DATABASE_PATH.chmod(0o600)

        with self.assertRaises(self.helper.PublicError):
            self.add("Do Not Add")
        with self.assertRaises(self.helper.PublicError):
            self.helper.migrate_database()
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

    def test_camera_and_bed_filters_support_overlapping_views(self) -> None:
        self.add(
            "Cam One Rose",
            bed="Rose bed",
            cameras=["Flower Cam #1"],
        )
        self.add(
            "Cam Two Sage",
            bed="Herb bed",
            cameras=["Flower Cam #2"],
        )
        self.add(
            "Shared Hydrangea",
            bed="Rose bed",
            cameras=["Flower Cam #2", "Flower Cam #1"],
        )
        self.add("Porch Fern", bed="Porch", cameras=[])

        cam_one = self.helper.list_plants(camera="Flower Cam #1")
        self.assertEqual(cam_one["count"], 2)
        self.assertEqual(
            [plant["name"] for plant in cam_one["plants"]],
            ["Cam One Rose", "Shared Hydrangea"],
        )
        cam_two_herbs = self.helper.list_plants(
            camera="Flower Cam #2",
            bed="herb BED",
        )
        self.assertEqual(cam_two_herbs["count"], 1)
        searched = self.helper.search_plants(
            "hydrangea",
            camera="Flower Cam #1",
        )
        self.assertEqual(searched["count"], 1)
        with self.assertRaisesRegex(
            self.helper.PublicError,
            "^Plant camera view is invalid$",
        ):
            self.helper.list_plants(camera="Flower Cam 1")

    def test_update_can_move_and_reassociate_a_plant(self) -> None:
        self.add("Movable Pot", bed="Porch", cameras=[])
        updated = self.helper.update_plant(
            "Movable Pot",
            new_name="Garden Pot",
            bed="Herb bed",
            camera_views=["Flower Cam #2"],
            location="Cabin front yard",
        )
        self.assertIs(updated["updated"], True)
        self.assertEqual(
            updated["changedFields"],
            ["name", "location", "bed", "cameraViews"],
        )
        shown = self.helper.show_plant("Garden Pot")["plant"]
        self.assertEqual(shown["cameraViews"], ["Flower Cam #2"])
        cleared = self.helper.update_plant(
            "Garden Pot",
            bed=None,
            camera_views=[],
        )
        self.assertIsNone(cleared["plant"]["bed"])
        self.assertEqual(cleared["plant"]["cameraViews"], [])
        with self.assertRaisesRegex(
            self.helper.PublicError,
            "^Plant update has no changes$",
        ):
            self.helper.update_plant("Garden Pot")

    def test_batch_care_requires_exact_count_and_is_atomic(self) -> None:
        self.add(
            "Rose One",
            bed="Rose bed",
            cameras=["Flower Cam #1"],
        )
        self.add(
            "Rose Two",
            bed="Rose bed",
            cameras=["Flower Cam #1"],
        )
        self.add(
            "Herb One",
            bed="Herb bed",
            cameras=["Flower Cam #2"],
        )
        before = self.helper.DATABASE_PATH.read_bytes()
        with self.assertRaisesRegex(
            self.helper.PublicError,
            "^Plant batch confirmation did not match$",
        ):
            self.helper.record_care_set(
                camera="Flower Cam #1",
                bed="Rose bed",
                action="water",
                notes="Julia confirmed the whole bed was watered.",
                confirm_count=3,
            )
        self.assertEqual(self.helper.DATABASE_PATH.read_bytes(), before)

        result = self.helper.record_care_set(
            camera="Flower Cam #1",
            bed="Rose bed",
            action="water",
            notes="Julia confirmed the whole bed was watered.",
            confirm_count=2,
        )
        self.assertEqual(result["count"], 2)
        self.assertEqual(result["plants"], ["Rose One", "Rose Two"])
        self.assertEqual(
            len(self.helper.show_plant("Rose One")["plant"]["careHistory"]),
            1,
        )
        self.assertEqual(
            len(self.helper.show_plant("Herb One")["plant"]["careHistory"]),
            0,
        )

    def test_exports_are_grouped_filterable_private_and_no_clobber(self) -> None:
        self.add(
            "Flower Bed Roses",
            species="Rosa",
            bed="Rose bed",
            cameras=["Flower Cam #1"],
            planted="2026-05-01",
        )
        self.add(
            "Driveway Sage",
            bed="Herb bed",
            cameras=["Flower Cam #2"],
        )
        self.add(
            "Shared Hydrangea",
            bed="Rose bed",
            cameras=["Flower Cam #1", "Flower Cam #2"],
        )
        self.add("Porch Fern", bed="Porch", cameras=[])

        result = self.helper.export_plants(
            "cabin-plants.md",
            overwrite=False,
        )
        output = Path(result["mediaPath"])
        markdown = output.read_text(encoding="utf-8")
        self.assertEqual(result["count"], 4)
        self.assertEqual(output.parent, self.helper.EXPORT_DIRECTORY)
        self.assertEqual(stat.S_IMODE(output.stat().st_mode), 0o600)
        self.assertIn("## Flower Cam \\#1", markdown)
        self.assertIn("## Flower Cam \\#2", markdown)
        self.assertIn("## No camera view", markdown)
        self.assertEqual(markdown.count("Shared Hydrangea"), 2)

        filtered = self.helper.export_plants(
            "cam-one.md",
            overwrite=False,
            camera="Flower Cam #1",
        )
        filtered_text = Path(filtered["mediaPath"]).read_text(encoding="utf-8")
        self.assertEqual(filtered["count"], 2)
        self.assertIn("Flower Bed Roses", filtered_text)
        self.assertNotIn("Driveway Sage", filtered_text)

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
                [
                    "add",
                    "CLI Rose",
                    "--bed",
                    "Rose bed",
                    "--camera",
                    "Flower Cam #1",
                ]
            )
        self.assertEqual(status, 0)
        self.assertEqual(
            json.loads(stdout.getvalue())["plant"]["cameraViews"],
            ["Flower Cam #1"],
        )

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
            "`cameraViews`",
            "plant-tracker update",
            "plant-tracker care-set",
            "`--confirm-count`",
            "Existing files",
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
