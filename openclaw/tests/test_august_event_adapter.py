#!/usr/bin/env python3
"""Fake-only tests for the read-only August home-event adapter."""

from __future__ import annotations

import importlib.util
import json
import os
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
ADAPTER = REPO_ROOT / "openclaw" / "bin" / "august-event-adapter.py"
BUS_PATH = REPO_ROOT / "openclaw" / "bin" / "home_event_bus.py"

BUS_SPEC = importlib.util.spec_from_file_location("august_test_home_event_bus", BUS_PATH)
assert BUS_SPEC and BUS_SPEC.loader
BUS = importlib.util.module_from_spec(BUS_SPEC)
sys.modules[BUS_SPEC.name] = BUS
BUS_SPEC.loader.exec_module(BUS)


class AugustEventAdapterTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tempdir.cleanup)
        self.root = Path(self.tempdir.name)
        self.home = self.root / "home"
        self.bin = self.root / "bin"
        self.events_log = self.root / "events.jsonl"
        self.calls_log = self.root / "august-calls.log"
        self.home.mkdir()
        self.bin.mkdir()
        self.august = self.bin / "august"
        self.home_eventctl = self.bin / "home-eventctl"
        self._write_executable(
            self.august,
            """#!/usr/bin/env python3
import json, os, sys
with open(os.environ['AUGUST_CALLS_LOG'], 'a', encoding='utf-8') as handle:
    handle.write(' '.join(sys.argv[1:]) + '\\n')
if os.environ.get('FAKE_AUGUST_FAIL') == '1':
    raise SystemExit(7)
print(os.environ['FAKE_AUGUST_OBSERVATION'])
""",
        )
        self._write_executable(
            self.home_eventctl,
            """#!/usr/bin/env python3
import json, os, sys
payload = json.load(sys.stdin)
with open(os.environ['EVENTS_LOG'], 'a', encoding='utf-8') as handle:
    handle.write(json.dumps({'args': sys.argv[1:], 'payload': payload}) + '\\n')
raise SystemExit(0 if os.environ.get('FAKE_PUBLISH_FAIL') != '1' else 8)
""",
        )

    @staticmethod
    def _write_executable(path: Path, content: str) -> None:
        path.write_text(content, encoding="utf-8")
        path.chmod(path.stat().st_mode | stat.S_IXUSR)

    def environment(self, observation: dict | None = None, **overrides: str) -> dict[str, str]:
        env = os.environ.copy()
        env.update(
            {
                "HOME": str(self.home),
                "HOME_EVENTS_AUGUST_ENABLED": "1",
                "HOME_EVENTS_ROOT": str(self.home / ".openclaw" / "home-events"),
                "AUGUST_BIN": str(self.august),
                "HOME_EVENTCTL": str(self.home_eventctl),
                "AUGUST_CALLS_LOG": str(self.calls_log),
                "EVENTS_LOG": str(self.events_log),
                "FAKE_AUGUST_OBSERVATION": json.dumps(
                    observation
                    or {
                        "ok": True,
                        "alias": "front_door",
                        "observed_at": "2026-01-01T12:00:00Z",
                        "lock_state": "locked",
                        "door_state": "closed",
                        "battery_percent": 30,
                    }
                ),
            }
        )
        env.update(overrides)
        return env

    def run_adapter(
        self, observation: dict | None = None, **overrides: str
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["python3", str(ADAPTER)],
            capture_output=True,
            text=True,
            check=False,
            env=self.environment(observation, **overrides),
        )

    @property
    def state_path(self) -> Path:
        return self.home / ".openclaw" / "home-events" / "state" / "august-adapter.json"

    def make_due(self) -> None:
        state = json.loads(self.state_path.read_text(encoding="utf-8"))
        state["next_poll_at"] = "2000-01-01T00:00:00Z"
        self.state_path.write_text(json.dumps(state), encoding="utf-8")
        self.state_path.chmod(0o600)

    def published(self) -> list[dict]:
        if not self.events_log.exists():
            return []
        return [json.loads(line) for line in self.events_log.read_text().splitlines()]

    def test_disabled_mode_does_not_observe_or_create_state(self) -> None:
        result = self.run_adapter(HOME_EVENTS_AUGUST_ENABLED="0")

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)["mode"], "disabled")
        self.assertFalse(self.calls_log.exists())
        self.assertFalse(self.state_path.exists())

    def test_baseline_is_silent_then_transitions_publish_once(self) -> None:
        baseline = self.run_adapter()
        self.assertEqual(baseline.returncode, 0, baseline.stderr)
        self.assertEqual(json.loads(baseline.stdout)["mode"], "baseline")
        self.assertEqual(self.published(), [])
        self.make_due()

        changed = {
            "ok": True,
            "alias": "front_door",
            "observed_at": "2026-01-01T12:05:00Z",
            "lock_state": "unlocked",
            "door_state": "open",
            "battery_percent": 19,
        }
        result = self.run_adapter(changed)

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)["event_count"], 3)
        published = self.published()
        self.assertEqual(
            {entry["payload"]["event_type"] for entry in published},
            {"lock.unlocked", "door.opened", "device.battery_low"},
        )
        for entry in published:
            self.assertEqual(entry["args"], ["enqueue", "--source", "august"])
            self.assertEqual(entry["payload"]["site"], "crosstown")
            self.assertNotIn("lockID", json.dumps(entry))
            BUS.normalize_input(
                "august",
                entry["payload"],
                b"a" * 32,
                clock=lambda: "2026-01-01T12:10:00Z",
            )
        self.assertEqual(self.calls_log.read_text().splitlines(), ["observe", "observe"])

    def test_unknown_state_edges_checkpoint_silently(self) -> None:
        baseline = self.run_adapter()
        self.assertEqual(baseline.returncode, 0, baseline.stderr)
        self.make_due()

        ambiguous_door = {
            "ok": True,
            "alias": "front_door",
            "observed_at": "2026-01-01T12:05:00Z",
            "lock_state": "locked",
            "door_state": "unknown",
            "battery_percent": 30,
        }
        unknown_result = self.run_adapter(ambiguous_door)
        self.assertEqual(unknown_result.returncode, 0, unknown_result.stderr)
        self.assertEqual(json.loads(unknown_result.stdout)["event_count"], 0)
        self.assertEqual(self.published(), [])
        checkpoint = json.loads(self.state_path.read_text(encoding="utf-8"))
        self.assertEqual(checkpoint["observation"]["door_state"], "unknown")
        self.make_due()

        known_again = {
            **ambiguous_door,
            "observed_at": "2026-01-01T12:10:00Z",
            "lock_state": "unlocked",
            "door_state": "open",
        }
        known_result = self.run_adapter(known_again)
        self.assertEqual(known_result.returncode, 0, known_result.stderr)
        self.assertEqual(json.loads(known_result.stdout)["event_count"], 1)
        self.assertEqual(
            [entry["payload"]["event_type"] for entry in self.published()],
            ["lock.unlocked"],
        )
        self.make_due()

        known_transition = {
            **known_again,
            "observed_at": "2026-01-01T12:15:00Z",
            "door_state": "closed",
        }
        transition_result = self.run_adapter(known_transition)
        self.assertEqual(transition_result.returncode, 0, transition_result.stderr)
        self.assertEqual(json.loads(transition_result.stdout)["event_count"], 1)
        self.assertEqual(
            [entry["payload"]["event_type"] for entry in self.published()],
            ["lock.unlocked", "door.closed"],
        )

    def test_publish_failure_leaves_pending_for_idempotent_retry(self) -> None:
        self.assertEqual(self.run_adapter().returncode, 0)
        self.make_due()
        changed = {
            "ok": True,
            "alias": "front_door",
            "observed_at": "2026-01-01T12:05:00Z",
            "lock_state": "unlocked",
            "door_state": "closed",
            "battery_percent": 30,
        }

        failed = self.run_adapter(changed, FAKE_PUBLISH_FAIL="1")

        self.assertNotEqual(failed.returncode, 0)
        pending = self.state_path.with_name("august-adapter.pending.json")
        self.assertTrue(pending.exists())
        retried = self.run_adapter(changed)
        self.assertEqual(retried.returncode, 0, retried.stderr)
        self.assertFalse(pending.exists())
        self.assertEqual(
            [entry["payload"]["event_type"] for entry in self.published()],
            ["lock.unlocked", "lock.unlocked"],
        )

    def test_three_failures_emit_one_unavailable_transition(self) -> None:
        self.assertEqual(self.run_adapter().returncode, 0)
        for _ in range(3):
            self.make_due()
            failed = self.run_adapter(FAKE_AUGUST_FAIL="1")
            self.assertNotEqual(failed.returncode, 0)

        self.assertEqual(
            [entry["payload"]["event_type"] for entry in self.published()],
            ["source.unavailable"],
        )
        unavailable = self.published()[0]["payload"]
        BUS.normalize_input(
            "august",
            unavailable,
            b"a" * 32,
            clock=lambda: unavailable["observed_at"],
        )
        state = json.loads(self.state_path.read_text(encoding="utf-8"))
        self.assertTrue(state["offline_emitted"])
        self.assertEqual(state["consecutive_failures"], 3)

    def test_adapter_source_has_no_mutation_command(self) -> None:
        source = ADAPTER.read_text(encoding="utf-8")
        self.assertNotIn('[august_bin, "lock"', source)
        self.assertNotIn('[august_bin, "unlock"', source)
        self.assertIn('[august_bin, "observe"', source)

    def test_lock_file_symlink_fails_closed_without_touching_target(self) -> None:
        self.assertEqual(self.run_adapter().returncode, 0)
        lock_path = self.state_path.with_name("august-adapter.lock")
        lock_path.unlink()
        canary = self.root / "lock-canary"
        canary.write_text("unchanged\n", encoding="utf-8")
        lock_path.symlink_to(canary)

        result = self.run_adapter()

        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(json.loads(result.stdout)["error_code"], "unsafe_lock_file")
        self.assertEqual(canary.read_text(encoding="utf-8"), "unchanged\n")


if __name__ == "__main__":
    unittest.main()
