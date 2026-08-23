#!/usr/bin/env python3
"""Fake-only tests for daily Crosstown vacancy cleaning."""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path


MODULE_PATH = (
    Path(__file__).resolve().parents[1] / "bin/crosstown-vacant-roomba.py"
)
PLIST_PATH = (
    Path(__file__).resolve().parents[1]
    / "launchagents/ai.openclaw.crosstown-vacant-roomba.plist"
)
SPEC = importlib.util.spec_from_file_location("crosstown_vacant_roomba", MODULE_PATH)
assert SPEC and SPEC.loader
automation_module = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = automation_module
SPEC.loader.exec_module(automation_module)


class FakeCommands:
    def __init__(self) -> None:
        self.calls: list[tuple[str, ...]] = []
        self.recent_cat = False
        self.phases = {"roomba": "charge", "scoomba": "stop"}
        self.status_failure: str | None = None
        self.start_failure: str | None = None
        self.litter_failure = False

    @staticmethod
    def completed(
        command: tuple[str, ...], payload: object, returncode: int = 0
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(
            command,
            returncode,
            json.dumps(payload),
            "",
        )

    def __call__(self, command: tuple[str, ...] | list[str]) -> subprocess.CompletedProcess[str]:
        args = tuple(command)
        self.calls.append(args)
        if args[:3] == ("litter-robot", "--json", "history"):
            if self.litter_failure:
                return self.completed(args, {"ok": False}, returncode=1)
            action = "Cat Detected" if self.recent_cat else "Clean Cycle Complete"
            timestamp = (
                "2026-08-22T09:47:00Z"
                if self.recent_cat
                else "2026-08-21T18:00:00Z"
            )
            return self.completed(
                args,
                {
                    "ok": True,
                    "alias": "crosstown-litter-robot",
                    "site": "crosstown",
                    "history": [{"action": action, "timestamp": timestamp}],
                },
            )
        if args[:2] == ("crosstown-roomba", "state"):
            alias = args[2]
            if self.status_failure == alias:
                return self.completed(args, {"error": "offline"}, returncode=1)
            return self.completed(
                args,
                {
                    "connected": True,
                    "batPct": 100,
                    "bin": {"present": True, "full": False},
                    "cleanMissionStatus": {
                        "phase": self.phases[alias],
                        "error": 0,
                    },
                },
            )
        if args[:2] == ("crosstown-roomba", "start"):
            alias = args[2]
            if self.start_failure == alias:
                return self.completed(args, {"ok": False}, returncode=1)
            return self.completed(
                args,
                {
                    "action": "start",
                    "target": alias,
                    "ok": True,
                    "results": [
                        {
                            "ok": True,
                            "verification": "passed",
                            "phase": "run",
                        }
                    ],
                },
            )
        raise AssertionError(f"unexpected command: {args}")


class CrosstownVacantRoombaTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tempdir.cleanup)
        self.home = Path(self.tempdir.name)
        self.now = datetime(2026, 8, 22, 10, 0, tzinfo=timezone.utc)
        self.commands = FakeCommands()
        self.presence_dir = self.home / ".openclaw/presence"
        self.outbox = self.presence_dir / "home-events-outbox"
        self.outbox.mkdir(parents=True, mode=0o700)
        (self.home / ".openclaw/logs").mkdir(mode=0o700)
        self.write_presence()
        self.automation = automation_module.CrosstownVacantRoomba(
            home=self.home,
            clock=lambda: self.now,
            command_runner=self.commands,
        )

    @staticmethod
    def write_private(path: Path, value: object) -> None:
        path.write_text(json.dumps(value, sort_keys=True) + "\n", encoding="utf-8")
        path.chmod(0o600)

    def write_presence(
        self,
        *,
        occupancy: str = "confirmed_vacant",
        fresh: bool = True,
        evaluated_at: datetime | None = None,
    ) -> None:
        evaluated = evaluated_at or self.now - timedelta(minutes=5)
        timestamp = automation_module.isoformat(evaluated)
        state = {
            "cabin": {"occupancy": "occupied", "fresh": True},
            "crosstown": {
                "occupancy": occupancy,
                "fresh": fresh,
                "scanAge": "0min",
                "stateChangedAt": "2026-08-21T20:00:00Z",
            },
            "people": {},
            "timestamp": timestamp,
            "transitions": [],
        }
        producer = {
            "schema_version": 1,
            "sequence": 10,
            "observation_id": "a" * 64,
            "state_hash": automation_module.state_hash(state),
            "evaluated_at": timestamp,
        }
        self.write_private(self.presence_dir / "state.json", state)
        self.write_private(self.outbox / "producer-state.json", producer)

    def record_path(self) -> Path:
        return (
            self.home
            / ".openclaw/vacant-roomba/crosstown/runs/2026-08-22.json"
        )

    def latest_status(self) -> dict:
        return json.loads(
            (
                self.home
                / ".openclaw/vacant-roomba/crosstown/latest-status.json"
            ).read_text(encoding="utf-8")
        )

    def test_occupied_house_is_noop_and_does_not_consume_the_day(self) -> None:
        self.write_presence(occupancy="occupied")

        code, result = self.automation.run("scheduled")

        self.assertEqual(code, 0)
        self.assertEqual(result["outcome"], "not_vacant")
        self.assertEqual(self.commands.calls, [])
        self.assertFalse(self.record_path().exists())
        self.assertEqual(self.latest_status()["outcome"], "not_vacant")

    def test_recent_cat_detection_suppresses_all_roomba_calls(self) -> None:
        self.commands.recent_cat = True

        code, result = self.automation.run("scheduled")

        self.assertEqual(code, 0)
        self.assertEqual(result["outcome"], "recent_cat_activity")
        self.assertEqual(len(self.commands.calls), 1)
        record = json.loads(self.record_path().read_text())
        self.assertEqual(record["outcome"], "recent_cat_activity")
        self.assertTrue(record["checks"]["recent_cat_activity"])
        self.assertEqual(self.latest_status()["outcome"], "recent_cat_activity")

    def test_dashboard_snooze_suppresses_external_reads_and_commands(self) -> None:
        snooze_file = self.home / ".openclaw/dog-walk/snooze.json"
        snooze_file.parent.mkdir(parents=True, mode=0o700)
        snooze_file.write_text(
            '{"cabin":null,"crosstown":"2026-08-22T12:00:00Z"}\n',
            encoding="utf-8",
        )
        snooze_file.chmod(0o644)

        code, result = self.automation.run("scheduled")

        self.assertEqual(code, 0)
        self.assertEqual(result["outcome"], "snoozed")
        self.assertEqual(self.commands.calls, [])

    def test_idle_robots_start_once_and_are_verified_by_the_guarded_cli(self) -> None:
        code, result = self.automation.run("scheduled")

        self.assertEqual(code, 0)
        self.assertEqual(result["outcome"], "started")
        self.assertEqual(result["started_robots"], ["roomba", "scoomba"])
        self.assertIn(("crosstown-roomba", "start", "roomba"), self.commands.calls)
        self.assertIn(("crosstown-roomba", "start", "scoomba"), self.commands.calls)
        first_call_count = len(self.commands.calls)

        code, result = self.automation.run("vacancy_transition")

        self.assertEqual(code, 0)
        self.assertEqual(result["outcome"], "already_handled")
        self.assertEqual(len(self.commands.calls), first_call_count)
        self.assertEqual(self.latest_status()["outcome"], "already_handled")
        self.assertEqual(self.latest_status()["decision_outcome"], "started")

    def test_already_cleaning_robot_is_not_restarted(self) -> None:
        self.commands.phases["roomba"] = "run"

        code, result = self.automation.run("vacancy_transition")

        self.assertEqual(code, 0)
        self.assertEqual(result["outcome"], "started")
        self.assertEqual(result["started_robots"], ["scoomba"])
        self.assertNotIn(("crosstown-roomba", "start", "roomba"), self.commands.calls)

    def test_stale_presence_fails_closed_before_writing_a_daily_record(self) -> None:
        self.write_presence(evaluated_at=self.now - timedelta(minutes=31))

        code, result = self.automation.run("scheduled")

        self.assertEqual(code, 1)
        self.assertEqual(result["reason"], "presence_state_stale")
        self.assertEqual(self.commands.calls, [])
        self.assertFalse(self.record_path().exists())

    def test_uncertain_robot_status_consumes_day_without_starting_either_robot(self) -> None:
        self.commands.phases["scoomba"] = "hmUsrDock"

        code, result = self.automation.run("scheduled")

        self.assertEqual(code, 0)
        self.assertEqual(result["outcome"], "robot_not_ready")
        self.assertFalse(any(call[1:2] == ("start",) for call in self.commands.calls))

    def test_history_failure_is_fail_closed_and_not_retried_that_day(self) -> None:
        self.commands.litter_failure = True

        code, result = self.automation.run("scheduled")

        self.assertEqual(code, 1)
        self.assertEqual(result["reason"], "litter_history_unavailable")
        self.assertEqual(self.latest_status()["outcome"], "failed")
        first_call_count = len(self.commands.calls)
        code, result = self.automation.run("scheduled")
        self.assertEqual(code, 0)
        self.assertEqual(result["outcome"], "already_handled")
        self.assertEqual(len(self.commands.calls), first_call_count)

    def test_launchagent_runs_at_six_without_run_at_load(self) -> None:
        text = PLIST_PATH.read_text(encoding="utf-8")
        self.assertIn("<integer>6</integer>", text)
        self.assertIn("<integer>0</integer>", text)
        self.assertNotIn("<key>RunAtLoad</key>", text)
        self.assertIn("--source", text)
        self.assertIn("scheduled", text)


if __name__ == "__main__":
    unittest.main()
