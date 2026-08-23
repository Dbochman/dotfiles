#!/usr/bin/env python3
"""Tests for the observation-only vacancy journal adapter."""

from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest


ADAPTER = Path(__file__).resolve().parents[1] / "bin/vacancy-event-adapter.py"


class VacancyEventAdapterTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.home = Path(temporary.name)
        self.root = self.home / "home-events"
        self.state = self.root / "state"
        self.runs = self.home / "vacancy-actions/journal/runs"
        for directory in (self.root, self.state, self.runs.parent, self.runs):
            directory.mkdir(parents=True, exist_ok=True, mode=0o700)
            directory.chmod(0o700)
        self.capture = self.home / "events.jsonl"
        self.publisher = self.home / "home-eventctl"
        self.publisher.write_text(
            "#!/usr/bin/env python3\n"
            "import json, os, sys\n"
            "event=json.load(sys.stdin)\n"
            "with open(os.environ['FAKE_CAPTURE'], 'a', encoding='utf-8') as stream:\n"
            " stream.write(json.dumps({'args':sys.argv[1:],'event':event}, sort_keys=True)+'\\n')\n",
            encoding="utf-8",
        )
        self.publisher.chmod(0o700)

    @staticmethod
    def private_json(path: Path, value: dict) -> None:
        path.write_text(json.dumps(value, sort_keys=True) + "\n", encoding="utf-8")
        path.chmod(0o600)

    def write_run(self, suffix: str, *, site: str = "crosstown") -> str:
        run_id = "run_" + (suffix * 32)
        value = {
            "schema_version": 1,
            "site": site,
            "cycle_id": "cycle_" + (suffix * 32),
            "run_id": run_id,
            "trigger_state_hash": suffix * 64,
            "triggered_at": "2026-08-22T14:00:00Z",
            "started_at": "2026-08-22T14:00:01Z",
            "completed_at": "2026-08-22T14:00:05Z",
            "state": "complete",
            "marker_committed": True,
            "actions": [
                {
                    "attempt_id": "attempt_" + (suffix * 32),
                    "target": "all_lights",
                    "action": "turn_off",
                    "state": "terminal",
                    "outcome": "skipped",
                    "verification": "policy_decision",
                    "reason_code": "delegated_to_event_bus",
                    "not_before": "2026-08-22T14:00:02Z",
                    "not_after": "2026-08-22T14:00:03Z",
                }
            ],
        }
        self.private_json(self.runs / f"{run_id}.json", value)
        return run_id

    def run_adapter(self, **overrides: str) -> subprocess.CompletedProcess[str]:
        env = {
            **os.environ,
            "HOME": str(self.home),
            "HOME_EVENTS_ROOT": str(self.root),
            "VACANCY_JOURNAL_ROOT": str(self.runs.parent),
            "HOME_EVENTCTL": str(self.publisher),
            "FAKE_CAPTURE": str(self.capture),
            "HOME_EVENTS_VACANCY_CABIN_ENABLED": "0",
            "HOME_EVENTS_VACANCY_CROSSTOWN_ENABLED": "1",
            **overrides,
        }
        return subprocess.run(
            ["python3", str(ADAPTER)],
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )

    def published(self) -> list[dict]:
        if not self.capture.exists():
            return []
        return [json.loads(line) for line in self.capture.read_text().splitlines()]

    def test_disabled_touches_no_runtime_or_journal(self) -> None:
        absent_root = self.home / "absent"
        result = self.run_adapter(
            HOME_EVENTS_ROOT=str(absent_root),
            HOME_EVENTS_VACANCY_CROSSTOWN_ENABLED="0",
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)["mode"], "disabled")
        self.assertFalse(absent_root.exists())

    def test_first_enable_silently_baselines_existing_runs(self) -> None:
        self.write_run("a")
        result = self.run_adapter()
        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["mode"], "baseline")
        self.assertEqual(payload["baselined_runs"], 1)
        self.assertEqual(self.published(), [])

    def test_new_terminal_run_publishes_ordered_safe_events_once(self) -> None:
        self.write_run("a")
        self.assertEqual(self.run_adapter().returncode, 0)
        self.write_run("b")

        first = self.run_adapter()
        second = self.run_adapter()

        self.assertEqual(first.returncode, 0, first.stderr)
        self.assertEqual(second.returncode, 0, second.stderr)
        published = self.published()
        self.assertEqual(len(published), 3)
        self.assertEqual(
            [entry["event"]["event_type"] for entry in published],
            [
                "automation.vacancy_run_started",
                "automation.action_skipped",
                "automation.vacancy_run_completed",
            ],
        )
        for entry in published:
            self.assertEqual(entry["args"], ["enqueue", "--source", "vacancy"])
            self.assertEqual(entry["event"]["site"], "crosstown")
            self.assertNotIn("command", json.dumps(entry["event"]))
        self.assertEqual(
            published[1]["event"]["attributes"]["reason_code"],
            "delegated_to_event_bus",
        )


if __name__ == "__main__":
    unittest.main()
