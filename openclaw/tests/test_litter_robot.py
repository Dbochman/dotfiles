#!/usr/bin/env python3
"""Safety and multi-device contract tests for the Litter-Robot skill."""

from __future__ import annotations

import asyncio
from contextlib import redirect_stdout
from datetime import datetime, timezone
from enum import Enum
import importlib.util
from io import StringIO
import json
import os
from pathlib import Path
import stat
import tempfile
import unittest
from unittest.mock import AsyncMock, patch


REPO_ROOT = Path(__file__).resolve().parents[2]
SKILL_DIR = REPO_ROOT / "openclaw" / "skills" / "litter-robot"
API_PATH = SKILL_DIR / "litter-robot-api.py"
CLI_PATH = SKILL_DIR / "litter-robot"
SKILL_PATH = SKILL_DIR / "SKILL.md"
HOME_DASHBOARD_PATH = REPO_ROOT / "openclaw" / "bin" / "home-dashboard.py"


def load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Could not load {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


litter_robot_api = load_module("litter_robot_api_test", API_PATH)


class FakeStatus(Enum):
    READY = "READY"
    CLEAN_CYCLE = "CLEAN_CYCLE"

    @property
    def text(self) -> str:
        return self.name.replace("_", " ").title()


class FakeRobot:
    def __init__(self, serial: str, *, name: str, online: bool = True) -> None:
        self.serial = serial
        self.name = name
        self.model = "Litter-Robot 4"
        self.is_online = online
        self.status = FakeStatus.READY
        self.waste_drawer_level = 24
        self.night_light_mode_enabled = True
        self.panel_lock_enabled = False
        self.clean_cycle_wait_time_minutes = 7
        self.cycle_count = 42
        self.cycle_capacity = 0
        self.is_waste_drawer_full = False
        self.litter_level = 85
        self.actions: list[tuple[str, object | None]] = []
        self.action_error: Exception | None = None
        self.history: list[object] = []

    async def start_cleaning(self) -> bool:
        self.actions.append(("clean", None))
        if self.action_error:
            raise self.action_error
        return True

    async def reset(self) -> bool:
        self.actions.append(("reset", None))
        if self.action_error:
            raise self.action_error
        return True

    async def set_night_light(self, enabled: bool) -> bool:
        self.actions.append(("nightlight", enabled))
        if self.action_error:
            raise self.action_error
        return True

    async def get_activity_history(self, *, limit: int):
        return self.history[:limit]


class FakeActivity:
    def __init__(self, timestamp: datetime, action: str) -> None:
        self.timestamp = timestamp
        self.action = type("FakeAction", (), {"text": action})()


class FakePet:
    name = "Test Cat"
    pet_type = None
    weight = 10.5
    gender = None

    async def fetch_weight_history(self, *, limit: int):
        return []


class FakeAccount:
    def __init__(self, robots: list[FakeRobot]) -> None:
        self.robots = robots
        self.pets = [FakePet()]
        self.disconnect = AsyncMock()


class LitterRobotTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tempdir.cleanup)
        self.config_dir = Path(self.tempdir.name) / ".config" / "litter-robot"
        self.config_dir.mkdir(parents=True, mode=0o700)
        self.config_file = self.config_dir / "config.yaml"
        self.token_file = self.config_dir / "token-cache.json"
        self.bindings_file = self.config_dir / "bindings.json"
        self.config_file.write_text(
            "email: test@example.invalid\npassword: fake-password\n", encoding="utf-8"
        )
        self.config_file.chmod(0o600)
        self.bindings_file.write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "robots": [
                        {
                            "alias": "crosstown-litter-robot",
                            "site": "crosstown",
                            "serial": "CROSS-SERIAL",
                        },
                        {
                            "alias": "cabin-litter-robot",
                            "site": "cabin",
                            "serial": "CABIN-SERIAL",
                        },
                    ],
                }
            ),
            encoding="utf-8",
        )
        self.bindings_file.chmod(0o600)
        patches = (
            patch.object(litter_robot_api, "CONFIG_DIR", self.config_dir),
            patch.object(litter_robot_api, "CONFIG_FILE", self.config_file),
            patch.object(litter_robot_api, "TOKEN_FILE", self.token_file),
            patch.object(litter_robot_api, "BINDINGS_FILE", self.bindings_file),
        )
        for active_patch in patches:
            active_patch.start()
            self.addCleanup(active_patch.stop)

        self.crosstown = FakeRobot("CROSS-SERIAL", name="Default")
        self.cabin = FakeRobot("CABIN-SERIAL", name="Cabin")
        self.account = FakeAccount([self.cabin, self.crosstown])
        connect_patch = patch.object(
            litter_robot_api,
            "connect_account",
            AsyncMock(return_value=self.account),
        )
        connect_patch.start()
        self.addCleanup(connect_patch.stop)

    def test_config_and_bindings_require_owner_only_regular_files(self) -> None:
        self.config_file.chmod(0o644)
        with self.assertRaises(litter_robot_api.LitterRobotError) as insecure:
            litter_robot_api.load_config()
        self.assertEqual(insecure.exception.code, "config_unsafe")

        self.config_file.chmod(0o600)
        target = self.config_dir / "bindings-target.json"
        target.write_bytes(self.bindings_file.read_bytes())
        target.chmod(0o600)
        self.bindings_file.unlink()
        self.bindings_file.symlink_to(target)
        with self.assertRaises(litter_robot_api.LitterRobotError) as symlinked:
            litter_robot_api.load_bindings()
        self.assertEqual(symlinked.exception.code, "bindings_unsafe")

    def test_status_maps_by_protected_serial_and_never_emits_it(self) -> None:
        payload = asyncio.run(litter_robot_api.command_status())

        self.assertTrue(payload["ok"])
        self.assertEqual(
            [item["alias"] for item in payload["robots"]],
            ["crosstown-litter-robot", "cabin-litter-robot"],
        )
        self.assertNotIn("CROSS-SERIAL", json.dumps(payload))
        self.assertNotIn("CABIN-SERIAL", json.dumps(payload))
        self.assertEqual(payload["robots"][0]["waste_level_pct"], 24)

    def test_overview_combines_profiles_robot_state_and_recent_activity(self) -> None:
        payload = asyncio.run(litter_robot_api.command_overview(14))

        self.assertTrue(payload["ok"])
        self.assertEqual([robot["site"] for robot in payload["robots"]], ["crosstown", "cabin"])
        self.assertTrue(all(robot["recent_activity"] == [] for robot in payload["robots"]))
        self.assertEqual(payload["pets"][0]["name"], "Test Cat")
        self.assertIn("recent_weights", payload["pets"][0])
        self.assertNotIn("CROSS-SERIAL", json.dumps(payload))

    def test_observe_is_exact_two_site_and_privacy_bounded(self) -> None:
        self.crosstown.history = [
            FakeActivity(
                datetime(2026, 8, 26, 21, 8, 24, tzinfo=timezone.utc),
                "Cat Detected",
            ),
            FakeActivity(
                datetime(2026, 8, 26, 21, 19, 0, tzinfo=timezone.utc),
                "Clean Cycle Complete",
            ),
        ]
        self.cabin.history = [
            FakeActivity(
                datetime(2026, 8, 26, 9, 15, 39, tzinfo=timezone.utc),
                " cat sensor interrupted ",
            )
        ]

        payload = asyncio.run(litter_robot_api.command_observe(100))

        self.assertTrue(payload["ok"])
        self.assertEqual(
            [item["selector"] for item in payload["robots"]],
            ["crosstown-litter-robot", "cabin-litter-robot"],
        )
        self.assertEqual(
            payload["robots"][0]["activities"],
            [
                {
                    "occurredAt": "2026-08-26T21:08:24Z",
                    "classification": "cat_detected",
                }
            ],
        )
        self.assertEqual(
            payload["robots"][1]["activities"][0]["classification"],
            "cat_sensor_interrupted",
        )
        serialized = json.dumps(payload)
        for forbidden in ("CROSS-SERIAL", "CABIN-SERIAL", "Test Cat", "10.5"):
            self.assertNotIn(forbidden, serialized)

    def test_observe_requires_both_exact_bindings(self) -> None:
        bindings = json.loads(self.bindings_file.read_text(encoding="utf-8"))
        bindings["robots"] = bindings["robots"][:1]
        self.bindings_file.write_text(json.dumps(bindings), encoding="utf-8")
        self.bindings_file.chmod(0o600)

        with self.assertRaises(litter_robot_api.LitterRobotError) as missing:
            asyncio.run(litter_robot_api.command_observe())

        self.assertEqual(missing.exception.code, "observe_bindings_incomplete")

    def test_mutations_require_exact_alias_and_do_not_choose_first_robot(self) -> None:
        payload = asyncio.run(
            litter_robot_api.command_mutation("clean", "crosstown-litter-robot")
        )

        self.assertTrue(payload["ok"])
        self.assertEqual(self.crosstown.actions, [("clean", None)])
        self.assertEqual(self.cabin.actions, [])
        with self.assertRaises(litter_robot_api.LitterRobotError) as fuzzy:
            asyncio.run(litter_robot_api.command_mutation("clean", "crosstown"))
        self.assertEqual(fuzzy.exception.code, "invalid_robot_selector")

    def test_remote_reset_uses_lr4_reset_not_nonexistent_waste_reset(self) -> None:
        payload = asyncio.run(
            litter_robot_api.command_mutation("reset", "cabin-litter-robot")
        )

        self.assertEqual(payload["action"], "robot_reset")
        self.assertEqual(self.cabin.actions, [("reset", None)])

    def test_uncertain_physical_action_is_non_retryable(self) -> None:
        self.cabin.action_error = TimeoutError("private transport detail")
        with self.assertRaises(litter_robot_api.LitterRobotError) as uncertain:
            asyncio.run(
                litter_robot_api.command_mutation("clean", "cabin-litter-robot")
            )

        self.assertEqual(uncertain.exception.code, "action_outcome_unknown")
        self.assertTrue(uncertain.exception.non_retryable)
        self.assertTrue(uncertain.exception.action_may_have_occurred)
        self.assertNotIn("private transport detail", uncertain.exception.message)

    def test_enrollment_is_gated_exact_and_writes_protected_bindings(self) -> None:
        self.bindings_file.unlink()
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaises(litter_robot_api.LitterRobotError) as gated:
                asyncio.run(
                    litter_robot_api.command_enroll(
                        "cabin", name_contains="cabin", remaining=False
                    )
                )
        self.assertEqual(gated.exception.code, "enrollment_not_allowed")

        with patch.dict(os.environ, {"LITTER_ROBOT_ALLOW_ENROLL": "1"}):
            cabin = asyncio.run(
                litter_robot_api.command_enroll(
                    "cabin", name_contains="cabin", remaining=False
                )
            )
            crosstown = asyncio.run(
                litter_robot_api.command_enroll(
                    "crosstown", name_contains=None, remaining=True
                )
            )

        self.assertEqual(cabin["enrolled"]["alias"], "cabin-litter-robot")
        self.assertEqual(crosstown["enrolled"]["alias"], "crosstown-litter-robot")
        self.assertEqual(stat.S_IMODE(self.bindings_file.stat().st_mode), 0o600)
        self.assertEqual(len(litter_robot_api.load_bindings()), 2)

    def test_main_returns_one_structured_json_line(self) -> None:
        output = StringIO()
        with redirect_stdout(output):
            code = litter_robot_api.main(["clean", "not-an-alias"])
        self.assertEqual(code, 1)
        lines = output.getvalue().splitlines()
        self.assertEqual(len(lines), 1)
        self.assertEqual(json.loads(lines[0])["error"], "invalid_robot_selector")

    def test_skill_and_cli_do_not_embed_protected_identifiers(self) -> None:
        skill_text = SKILL_PATH.read_text(encoding="utf-8")
        cli_text = CLI_PATH.read_text(encoding="utf-8")
        self.assertNotIn("Serial", skill_text)
        self.assertNotRegex(skill_text, r"LR4[A-Z0-9]{6,}")
        self.assertIn("~/.openclaw/venvs/litter-robot", skill_text)
        self.assertNotIn(".openclaw/litter-robot/venv", cli_text)

    def test_home_dashboard_uses_exact_aliases_and_structured_status(self) -> None:
        dashboard = load_module("litter_robot_dashboard_test", HOME_DASHBOARD_PATH)
        builder = dashboard.COMMANDS["litter_robot"]["clean"]
        self.assertEqual(
            builder({"robot": "cabin-litter-robot"}),
            ["litter-robot", "clean", "cabin-litter-robot"],
        )
        with self.assertRaises(dashboard.CommandValidationError):
            builder({"robot": "cabin"})
        with patch.object(
            dashboard,
            "_run_cli",
            return_value={"ok": True, "robots": []},
        ) as run_cli:
            dashboard.collect_litter_robot()
        run_cli.assert_called_once_with(
            ["litter-robot", "--json", "status"], parse_json=True
        )


if __name__ == "__main__":
    unittest.main()
