#!/usr/bin/env python3
"""Tests for observation-only legacy vacancy-action journaling."""

from __future__ import annotations

import importlib.util
import json
import os
import stat
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path


MODULE_PATH = Path(__file__).resolve().parents[1] / "bin/vacancy-action-journal.py"
OPENCLAW_ROOT = MODULE_PATH.parents[1]
VACANCY_RUNNER = OPENCLAW_ROOT / "workspace/scripts/vacancy-actions.sh"
DOTFILES_PULL = OPENCLAW_ROOT / "bin/dotfiles-pull.command"
SPEC = importlib.util.spec_from_file_location("vacancy_action_journal", MODULE_PATH)
assert SPEC and SPEC.loader
journal_module = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = journal_module
SPEC.loader.exec_module(journal_module)


class Clock:
    def __init__(self, value: str) -> None:
        self.value = datetime.fromisoformat(value.replace("Z", "+00:00"))

    def __call__(self) -> str:
        return self.value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")

    def advance(self, **kwargs: int) -> None:
        self.value += timedelta(**kwargs)


class VacancyActionJournalTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tempdir.cleanup)
        self.home = Path(self.tempdir.name)
        self.presence = self.home / ".openclaw/presence"
        self.outbox = self.presence / "home-events-outbox"
        self.markers = self.presence / "vacancy-dispatched"
        for directory in (self.presence, self.outbox, self.markers):
            directory.mkdir(parents=True, mode=0o700)
            directory.chmod(0o700)
        self.state_file = self.presence / "state.json"
        self.producer_file = self.outbox / "producer-state.json"
        self.clock = Clock("2026-08-15T18:00:00Z")
        self.write_presence()
        journal_parent = self.home / ".openclaw/vacancy-actions"
        journal_parent.mkdir(mode=0o700)
        journal_parent.chmod(0o700)
        self.journal = journal_module.VacancyActionJournal(
            state_file=self.state_file,
            producer_state_file=self.producer_file,
            marker_dir=self.markers,
            root=self.home / ".openclaw/vacancy-actions/journal",
            clock=self.clock,
        )

    @staticmethod
    def write_private(path: Path, value: object) -> None:
        path.write_text(json.dumps(value, sort_keys=True) + "\n", encoding="utf-8")
        path.chmod(0o600)

    def write_presence(
        self,
        *,
        state_changed_at: str = "2026-08-15T17:55:00Z",
        occupancy: str = "confirmed_vacant",
        fresh: bool = True,
    ) -> None:
        state = {
            "cabin": {
                "fresh": fresh,
                "occupancy": occupancy,
                "scanAge": "0min",
                "stateChangedAt": state_changed_at,
            },
            "crosstown": {
                "fresh": True,
                "occupancy": "occupied",
                "scanAge": "0min",
                "stateChangedAt": "2026-08-10T00:00:00Z",
            },
            "people": {},
            "timestamp": "2026-08-15T17:59:59Z",
            "transitions": [],
        }
        producer = {
            "schema_version": 1,
            "sequence": 10,
            "observation_id": "a" * 64,
            "state_hash": journal_module.state_hash(state),
            "evaluated_at": state["timestamp"],
        }
        self.write_private(self.state_file, state)
        self.write_private(self.producer_file, producer)

    def read_run(self, run_id: str) -> dict:
        return json.loads(
            (self.journal.runs_dir / f"{run_id}.json").read_text(encoding="utf-8")
        )

    def test_complete_run_records_only_safe_terminal_outcomes(self) -> None:
        started = self.journal.begin_run("cabin")
        action = self.journal.begin_action(started["run_id"], "all_lights", "turn_off")
        self.journal.finish_action(
            started["run_id"],
            action["attempt_id"],
            "command_accepted",
            "command_exit",
            "completed",
        )
        (self.markers / "cabin").write_text("committed\n", encoding="utf-8")
        result = self.journal.complete_run(started["run_id"])

        self.assertEqual(result, {"ok": True, "state": "complete"})
        run = self.read_run(started["run_id"])
        self.assertEqual(run["state"], "complete")
        self.assertTrue(run["marker_committed"])
        self.assertEqual(run["actions"][0]["outcome"], "command_accepted")
        self.assertEqual(
            stat.S_IMODE(
                (self.journal.runs_dir / f"{started['run_id']}.json").stat().st_mode
            ),
            0o600,
        )
        self.assertEqual(stat.S_IMODE(self.journal.root.stat().st_mode), 0o700)

    def test_presence_hash_or_freshness_mismatch_disables_journal(self) -> None:
        state = json.loads(self.state_file.read_text(encoding="utf-8"))
        state["cabin"]["scanAge"] = "changed-after-producer"
        self.write_private(self.state_file, state)
        with self.assertRaisesRegex(journal_module.JournalError, "presence_state_mismatch"):
            self.journal.begin_run("cabin")

        self.write_presence(fresh=False)
        with self.assertRaisesRegex(journal_module.JournalError, "site_not_confirmed_vacant"):
            self.journal.begin_run("cabin")

    def test_cycle_is_stable_until_a_new_vacancy_episode(self) -> None:
        first = self.journal.begin_run("cabin")
        second = self.journal.begin_run("cabin")
        self.assertEqual(first["cycle_id"], second["cycle_id"])

        self.write_presence(state_changed_at="2026-08-16T12:00:00Z")
        third = self.journal.begin_run("cabin")
        self.assertNotEqual(first["cycle_id"], third["cycle_id"])

    def test_stale_unfinished_action_becomes_unknown_without_retry(self) -> None:
        started = self.journal.begin_run("cabin")
        self.journal.begin_action(started["run_id"], "floomba", "start_cleaning")
        self.clock.advance(minutes=16)

        result = self.journal.recover_stale()

        self.assertEqual(result, {"ok": True, "recovered": 1})
        run = self.read_run(started["run_id"])
        self.assertEqual(run["state"], "interrupted")
        self.assertFalse(run["marker_committed"])
        self.assertEqual(run["actions"][0]["outcome"], "outcome_unknown")
        self.assertEqual(run["actions"][0]["reason_code"], "interrupted")

    def test_stale_run_with_committed_marker_completes_honestly(self) -> None:
        started = self.journal.begin_run("cabin")
        action = self.journal.begin_action(started["run_id"], "philly", "start_cleaning")
        self.journal.finish_action(
            started["run_id"],
            action["attempt_id"],
            "failed",
            "command_exit",
            "command_failed",
        )
        (self.markers / "cabin").write_text("committed\n", encoding="utf-8")
        self.clock.advance(minutes=16)

        self.journal.recover_stale()

        run = self.read_run(started["run_id"])
        self.assertEqual(run["state"], "complete")
        self.assertTrue(run["marker_committed"])
        self.assertEqual(run["actions"][0]["outcome"], "failed")

    def test_target_action_pairs_and_duplicates_are_strict(self) -> None:
        started = self.journal.begin_run("cabin")
        with self.assertRaisesRegex(journal_module.JournalError, "action_target_invalid"):
            self.journal.begin_action(started["run_id"], "floomba", "turn_off")
        self.journal.begin_action(started["run_id"], "floomba", "start_cleaning")
        with self.assertRaisesRegex(journal_module.JournalError, "action_target_duplicate"):
            self.journal.begin_action(started["run_id"], "floomba", "start_cleaning")

    def test_symlink_marker_cannot_complete_run(self) -> None:
        started = self.journal.begin_run("cabin")
        external_marker = self.home / "external-marker"
        external_marker.write_text("unsafe\n", encoding="utf-8")
        (self.markers / "cabin").symlink_to(external_marker)

        with self.assertRaisesRegex(journal_module.JournalError, "vacancy_marker_invalid"):
            self.journal.complete_run(started["run_id"])

    def test_tracked_files_follow_atomic_routine_deployment(self) -> None:
        pull = DOTFILES_PULL.read_text(encoding="utf-8")
        runner = VACANCY_RUNNER.read_text(encoding="utf-8")

        self.assertTrue(os.access(MODULE_PATH, os.X_OK))
        self.assertIn('for script in "$BIN_SRC"/*.py "$BIN_SRC"/*.sh; do', pull)
        self.assertIn(
            'atomic_install_executable "$script" "$BIN_DST/$fname"', pull
        )
        self.assertIn('for script in "$SCRIPTS_SRC"/*; do', pull)
        self.assertIn(
            'atomic_install_executable "$script" "$SCRIPTS_DST/$fname"', pull
        )
        self.assertIn(
            "$HOME/.openclaw/bin/vacancy-action-journal.py", runner
        )
        self.assertIn("legacy actions continue", runner)


if __name__ == "__main__":
    unittest.main()
