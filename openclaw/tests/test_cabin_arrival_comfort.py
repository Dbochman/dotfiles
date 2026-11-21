#!/usr/bin/env python3
"""No-network tests: fake Nest calls, private temporary presence/state."""

import importlib.util
import json
from pathlib import Path
import subprocess
import tempfile
import threading
import unittest
from datetime import datetime, timedelta, timezone

SPEC = importlib.util.spec_from_file_location(
    "comfort", Path(__file__).parents[1] / "bin/cabin-arrival-comfort.py")
comfort = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(comfort)


class ArrivalTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name)
        self.now = datetime(2026, 11, 21, 22, 0, tzinfo=timezone.utc)
        self.calls = []
        self.fail = None
        self.delays = []
        self.controller = comfort.Controller(self.home, self.run_command, lambda: self.now,
                                             self.delays.append)
        self.controller.presence.mkdir(parents=True)
        (self.controller.presence / "home-events-outbox").mkdir()
        self.write_presence("occupied")

    def run_command(self, argv, **kwargs):
        args = argv[1:]
        self.assertEqual(argv[0], str(self.home / ".openclaw/bin/nest-agent"))
        self.calls.append(args)
        if args == self.fail:
            return subprocess.CompletedProcess(argv, 1)
        return subprocess.CompletedProcess(argv, 0)

    def write_presence(self, occupancy, ambiguous=False, stale=False, sticky_only=False):
        home = occupancy == "occupied"
        state = {
            "timestamp": (self.now - timedelta(hours=1) if stale else self.now).isoformat(),
            "cabin": {"occupancy": occupancy, "fresh": True,
                      "stateChangedAt": (self.now - timedelta(hours=2)).isoformat()},
            "crosstown": {"fresh": True},
            "people": {name: {"location": "cabin" if home else "crosstown",
                              "cabin": home and not sticky_only,
                              "crosstown": ambiguous or not home}
                       for name in ("Dylan", "Julia")}}
        comfort.save(self.controller.presence / "state.json", state)
        digest = comfort.hashlib.sha256(json.dumps(
            state, ensure_ascii=False, separators=(",", ":"), sort_keys=True,
            allow_nan=False).encode()).hexdigest()
        comfort.save(self.controller.presence / "home-events-outbox/producer-state.json",
                     {"schema_version": 1, "state_hash": digest, "evaluated_at": state["timestamp"]})

    def arm(self):
        self.write_presence("confirmed_vacant")
        self.assertEqual(self.controller.evaluate()["status"], "armed")
        self.write_presence("occupied")

    def test_arrival_restores_only_two_rooms_and_deduplicates(self):
        self.arm()
        self.assertTrue(self.controller.evaluate()["ok"])
        self.assertEqual(self.calls, [
            ["eco-site", "cabin", "off", "--dry-run"],
            ["eco", "Philly Living Room", "off"],
            ["mode", "Philly Living Room", "HEAT"],
            ["set", "Philly Living Room", "70"],
            ["eco", "Philly Bedroom", "off"],
            ["mode", "Philly Bedroom", "HEAT"],
            ["set", "Philly Bedroom", "68"]])
        self.calls.clear()
        self.controller.evaluate()
        self.assertEqual(self.calls, [])
        record = comfort.read_private(self.controller.state)
        self.assertEqual(record["confirmed_steps"], 6)
        self.assertEqual(self.delays, [20] * 6)

    def test_next_vacancy_rearms(self):
        self.arm()
        self.controller.evaluate()
        self.calls.clear()
        self.arm()
        self.controller.evaluate()
        self.assertEqual(len(self.calls), 7)

    def test_initial_occupied_is_baselined(self):
        self.assertEqual(self.controller.evaluate()["status"], "baselined")
        self.assertEqual(self.calls, [])

    def test_explicit_current_stay_restore(self):
        self.assertEqual(self.controller.evaluate(True)["status"], "complete")
        self.calls.clear()
        self.controller.evaluate()
        self.assertEqual(self.calls, [])

    def test_ambiguous_and_sticky_only_arrivals_wait(self):
        self.arm()
        for option in ("ambiguous", "sticky_only"):
            self.write_presence("occupied", **{option: True})
            self.assertEqual(self.controller.evaluate()["status"], "waiting_for_unambiguous_presence")
            self.assertTrue(comfort.read_private(self.controller.state)["armed"])
        self.assertEqual(self.calls, [])

    def test_uncertain_does_not_rearm_completed_stay(self):
        self.controller.evaluate(True)
        self.calls.clear()
        self.write_presence("possibly_vacant")
        self.controller.evaluate()
        self.write_presence("occupied")
        self.controller.evaluate()
        self.assertEqual(self.calls, [])

    def test_stale_presence_blocks(self):
        self.arm()
        self.write_presence("occupied", stale=True)
        with self.assertRaises(comfort.SafetyError):
            self.controller.evaluate()
        self.assertEqual(self.calls, [])

    def test_hash_mismatch_blocks(self):
        self.arm()
        path = self.controller.presence / "state.json"
        data = comfort.read_private(path)
        data["cabin"]["extra"] = True
        comfort.save(path, data)
        with self.assertRaises(comfort.SafetyError):
            self.controller.evaluate()
        self.assertEqual(self.calls, [])

    def test_partial_failure_is_terminal_until_next_stay(self):
        self.arm()
        self.fail = ["set", "Philly Living Room", "70"]
        self.assertEqual(self.controller.evaluate()["status"], "failed")
        self.assertEqual(len(self.calls), 4)
        self.calls.clear()
        self.fail = None
        self.assertFalse(self.controller.evaluate()["ok"])
        self.assertEqual(self.calls, [])

    def test_preflight_failure_allows_read_only_retry(self):
        self.arm()
        self.fail = ["eco-site", "cabin", "off", "--dry-run"]
        self.assertEqual(self.controller.evaluate()["status"], "preflight_failed")
        self.assertTrue(comfort.read_private(self.controller.state)["armed"])
        self.assertEqual(len(self.calls), 1)

    def test_interrupted_attempt_is_not_replayed(self):
        self.controller.evaluate()
        comfort.save(self.controller.state, {"schema_version": 1, "armed": False, "status": "applying"})
        self.assertEqual(self.controller.evaluate()["status"], "outcome_unknown")
        self.assertEqual(self.calls, [])

    def test_presence_change_during_commands_stops(self):
        self.arm()
        original = self.controller.runner
        def move_after_eco(argv, **kwargs):
            result = original(argv, **kwargs)
            if argv[1] == "eco":
                self.write_presence("confirmed_vacant")
            return result
        self.controller.runner = move_after_eco
        self.assertEqual(self.controller.evaluate()["reason"], "presence_changed")
        self.assertEqual(len(self.calls), 2)

    def test_presence_is_checked_after_rate_limit_delay(self):
        self.arm()
        self.controller.sleep = lambda seconds: self.write_presence("confirmed_vacant")
        self.assertEqual(self.controller.evaluate()["reason"], "presence_changed")
        self.assertEqual(self.calls, [["eco-site", "cabin", "off", "--dry-run"]])

    def test_unsafe_input_permissions_fail_closed(self):
        (self.controller.presence / "state.json").chmod(0o644)
        with self.assertRaises(comfort.SafetyError):
            self.controller.evaluate(True)
        self.assertEqual(self.calls, [])

    def test_timeout_does_not_replay(self):
        self.arm()
        original = self.controller.runner
        def timeout(argv, **kwargs):
            if argv[1] == "eco":
                raise subprocess.TimeoutExpired(argv, 90)
            return original(argv, **kwargs)
        self.controller.runner = timeout
        self.assertEqual(self.controller.evaluate()["status"], "failed")
        self.calls.clear()
        self.controller.runner = original
        self.controller.evaluate()
        self.assertEqual(self.calls, [])

    def test_concurrent_evaluations_restore_once(self):
        self.arm()
        barrier = threading.Barrier(2)
        results = []
        def evaluate():
            controller = comfort.Controller(self.home, self.run_command, lambda: self.now,
                                             self.delays.append)
            barrier.wait(timeout=5)
            results.append(controller.evaluate())
        threads = [threading.Thread(target=evaluate) for _ in range(2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=5)
            self.assertFalse(thread.is_alive())
        self.assertEqual(len(results), 2)
        self.assertTrue(all(result["ok"] for result in results))
        self.assertEqual(len(self.calls), 7)


if __name__ == "__main__":
    unittest.main()
