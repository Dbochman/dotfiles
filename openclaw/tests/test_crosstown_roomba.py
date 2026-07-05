#!/usr/bin/env python3
"""Fake-only reliability tests for the Crosstown Roomba wrappers."""

from __future__ import annotations

import hashlib
import json
import os
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
SKILL_DIR = REPO_ROOT / "openclaw" / "skills" / "crosstown-roomba"
WRAPPER = SKILL_DIR / "crosstown-roomba"
NODE_CLI = SKILL_DIR / "roomba-cmd.js"
DEPLOYED_NODE_SOURCE = REPO_ROOT / "openclaw" / "rest980" / "roomba-cmd.js"


class CrosstownRoombaTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tempdir.cleanup)
        self.root = Path(self.tempdir.name)
        self.home = self.root / "home"
        self.fake_bin = self.root / "bin"
        self.ssh_log = self.root / "ssh-log.jsonl"
        self.node_log = self.root / "node-log.jsonl"
        self.env_file = self.root / "robot.env"
        self.home.mkdir()
        self.fake_bin.mkdir()

        self.fake_ssh = self.fake_bin / "ssh"
        self._write_executable(
            self.fake_ssh,
            r'''#!/usr/bin/env python3
import json
import os
import sys
from pathlib import Path

log_path = Path(os.environ["FAKE_ROOMBA_SSH_LOG"])
remote = sys.argv[-1]
action = remote.rsplit(" ", 1)[-1]
robot = "10max" if "env-10max" in remote else "j5"
entries = []
if log_path.exists():
    entries = [json.loads(line) for line in log_path.read_text(encoding="utf-8").splitlines()]
prior_statuses = sum(
    entry["robot"] == robot and entry["action"] == "status"
    for entry in entries
)
with log_path.open("a", encoding="utf-8") as handle:
    handle.write(json.dumps({"robot": robot, "action": action, "argv": sys.argv[1:]}) + "\n")

scenario = os.environ.get("FAKE_ROOMBA_SCENARIO", "success_all_start")

if scenario == "transport":
    print("fake transport failure", file=sys.stderr)
    raise SystemExit(255)

if scenario == "invalid_json":
    print("this is not json")
    raise SystemExit(0)

if scenario == "action_error_zero_exit" and action == "start":
    print(json.dumps({"error": "rejected", "message": "fake rejection"}))
    raise SystemExit(0)

if scenario == "partial_start_failure" and robot == "j5" and action == "start":
    print(json.dumps({"error": "rejected", "message": "fake rejection"}), file=sys.stderr)
    raise SystemExit(1)

if scenario == "status_partial_failure" and robot == "j5" and action == "status":
    print(json.dumps({"error": "offline", "message": "fake offline"}), file=sys.stderr)
    raise SystemExit(1)

if scenario == "dock_stop_failure":
    if action == "status":
        print(json.dumps({"cleanMissionStatus": {"phase": "run"}, "batPct": 80}))
        raise SystemExit(0)
    if action == "stop":
        print(json.dumps({"error": "stop_rejected", "message": "fake stop failure"}), file=sys.stderr)
        raise SystemExit(1)

if scenario == "dock_success" and action == "status":
    phases = ["run", "stop", "hmUsrDock"]
    phase = phases[min(prior_statuses, len(phases) - 1)]
    print(json.dumps({"cleanMissionStatus": {"phase": phase}, "batPct": 80}))
    raise SystemExit(0)

if scenario == "dock_already_charging" and action == "status":
    print(json.dumps({"cleanMissionStatus": {"phase": "charge"}, "batPct": 100}))
    raise SystemExit(0)

if scenario == "verification_timeout" and action == "status":
    print(json.dumps({"cleanMissionStatus": {"phase": "new"}, "batPct": 80}))
    raise SystemExit(0)

if action == "status":
    print(json.dumps({
        "cleanMissionStatus": {"phase": "run", "nMssn": 2, "error": 0},
        "batPct": 80,
        "bin": {"present": True, "full": False},
    }))
else:
    print(json.dumps({"ok": True, "accepted": action}))
''',
        )

        module_dir = (
            self.home
            / ".openclaw"
            / "rest980"
            / "node_modules"
            / "dorita980"
        )
        module_dir.mkdir(parents=True)
        (module_dir / "index.js").write_text(
            r'''const fs = require("fs");
const { EventEmitter } = require("events");

function record(event) {
  fs.appendFileSync(process.env.FAKE_ROOMBA_NODE_LOG, JSON.stringify({ event }) + "\n");
}

class Local extends EventEmitter {
  constructor() {
    super();
    record("constructor");
    setImmediate(() => this.emit("connect"));
  }

  action(name) {
    record(name);
    const mode = process.env.FAKE_DORITA_MODE || "success";
    if (mode === "throw") throw new Error("fake action exception");
    if (mode === "reject") return Promise.reject(new Error("fake action rejection"));
    if (mode === "error_result") {
      return Promise.resolve({ error: "rejected", message: "fake action failure" });
    }
    if (mode === "ok_false") {
      return Promise.resolve({ ok: false, message: "fake action failure" });
    }
    if (mode === "undefined") return Promise.resolve(undefined);
    return Promise.resolve({ ok: true, accepted: name });
  }

  start() { return this.action("start"); }
  stop() { return this.action("stop"); }
  pause() { return this.action("pause"); }
  resume() { return this.action("resume"); }
  dock() { return this.action("dock"); }
  find() { return this.action("find"); }
  getMission() {
    record("getMission");
    return Promise.resolve({ cleanMissionStatus: { phase: "run" }, batPct: 80 });
  }
  getRobotState() {
    record("getRobotState");
    return Promise.resolve({ netinfo: {}, signal: {}, wlcfg: {} });
  }
  end() { record("end"); }
}

module.exports = { Local };
''',
            encoding="utf-8",
        )
        self.write_env(
            "BLID=fake-blid\n"
            "PASSWORD=fake-password\n"
            "ROBOT_IP=127.0.0.1\n"
        )

    @staticmethod
    def _write_executable(path: Path, content: str) -> None:
        path.write_text(content, encoding="utf-8")
        path.chmod(path.stat().st_mode | stat.S_IXUSR)

    def write_env(self, content: str) -> None:
        self.env_file.write_text(content, encoding="utf-8")
        self.env_file.chmod(0o600)

    def environment(self, **overrides: str) -> dict[str, str]:
        env = os.environ.copy()
        env.update(
            {
                "HOME": str(self.home),
                "FAKE_ROOMBA_SSH_LOG": str(self.ssh_log),
                "FAKE_ROOMBA_NODE_LOG": str(self.node_log),
                "CROSSTOWN_ROOMBA_SSH_BIN": str(self.fake_ssh),
                "CROSSTOWN_ROOMBA_VERIFY_ATTEMPTS": "2",
                "CROSSTOWN_ROOMBA_VERIFY_INTERVAL": "0",
                "CROSSTOWN_ROOMBA_DOCK_STOP_DELAY": "0",
            }
        )
        env.update(overrides)
        return env

    def run_wrapper(
        self, *args: str, scenario: str = "success_all_start", **env_overrides: str
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["bash", str(WRAPPER), *args],
            check=False,
            capture_output=True,
            text=True,
            env=self.environment(FAKE_ROOMBA_SCENARIO=scenario, **env_overrides),
        )

    def run_node(
        self, *args: str, mode: str = "success"
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["node", str(NODE_CLI), *args],
            check=False,
            capture_output=True,
            text=True,
            env=self.environment(FAKE_DORITA_MODE=mode),
        )

    def ssh_calls(self) -> list[dict]:
        if not self.ssh_log.exists():
            return []
        return [json.loads(line) for line in self.ssh_log.read_text().splitlines()]

    def node_calls(self) -> list[dict]:
        if not self.node_log.exists():
            return []
        return [json.loads(line) for line in self.node_log.read_text().splitlines()]

    @staticmethod
    def action_summary(result: subprocess.CompletedProcess[str]) -> dict:
        return json.loads(result.stdout)

    @staticmethod
    def error_payload(result: subprocess.CompletedProcess[str]) -> dict:
        return json.loads(result.stderr.splitlines()[-1])

    def test_tracked_node_sources_remain_identical(self) -> None:
        self.assertEqual(NODE_CLI.read_bytes(), DEPLOYED_NODE_SOURCE.read_bytes())
        self.assertEqual(
            hashlib.sha256(NODE_CLI.read_bytes()).hexdigest(),
            hashlib.sha256(DEPLOYED_NODE_SOURCE.read_bytes()).hexdigest(),
        )

    def test_node_rejects_unknown_command_and_invalid_inputs_before_connect(self) -> None:
        invalid_env = self.root / "invalid.env"
        invalid_env.write_text(
            "BLID=fake\nPASSWORD=fake\nROBOT_IP=not-an-ip\n",
            encoding="utf-8",
        )
        invalid_env.chmod(0o600)
        missing_value_env = self.root / "missing-value.env"
        missing_value_env.write_text(
            "BLID=fake\nROBOT_IP=127.0.0.1\n",
            encoding="utf-8",
        )
        missing_value_env.chmod(0o600)
        cases = [
            ((), "usage"),
            ((str(self.env_file), "unknown"), "unknown_command"),
            ((str(self.env_file), "start", "extra"), "usage"),
            ((str(self.root / "missing.env"), "start"), "env_file_unreadable"),
            ((str(missing_value_env), "start"), "env_missing_value"),
            ((str(invalid_env), "start"), "env_invalid_ip"),
        ]
        for args, error_code in cases:
            with self.subTest(args=args):
                self.node_log.unlink(missing_ok=True)
                result = self.run_node(*args)
                self.assertEqual(result.returncode, 2, result.stderr)
                self.assertEqual(self.error_payload(result)["error"], error_code)
                self.assertEqual(self.node_calls(), [])

    def test_node_rejects_insecure_or_symlinked_env_before_connect(self) -> None:
        self.env_file.chmod(0o644)
        insecure = self.run_node(str(self.env_file), "start")
        self.assertEqual(insecure.returncode, 2, insecure.stderr)
        self.assertEqual(self.error_payload(insecure)["error"], "env_file_unsafe")
        self.assertEqual(self.node_calls(), [])

        target = self.root / "robot-target.env"
        target.write_text(
            "BLID=fake\nPASSWORD=fake\nROBOT_IP=127.0.0.1\n",
            encoding="utf-8",
        )
        target.chmod(0o600)
        self.env_file.unlink()
        self.env_file.symlink_to(target)
        symlinked = self.run_node(str(self.env_file), "start")
        self.assertEqual(symlinked.returncode, 2, symlinked.stderr)
        self.assertEqual(self.error_payload(symlinked)["error"], "env_file_unsafe")
        self.assertEqual(self.node_calls(), [])

    def test_node_action_exceptions_rejections_and_error_results_are_nonzero(self) -> None:
        expected_codes = {
            "throw": "command_failed",
            "reject": "command_failed",
            "error_result": "rejected",
            "ok_false": "action_failed",
        }
        for mode, expected_code in expected_codes.items():
            with self.subTest(mode=mode):
                self.node_log.unlink(missing_ok=True)
                result = self.run_node(str(self.env_file), "start", mode=mode)
                self.assertEqual(result.returncode, 1, result.stderr)
                self.assertEqual(self.error_payload(result)["error"], expected_code)
                events = [call["event"] for call in self.node_calls()]
                self.assertIn("constructor", events)
                self.assertIn("end", events)

    def test_node_normalizes_undefined_success_to_json(self) -> None:
        result = self.run_node(str(self.env_file), "start", mode="undefined")

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout), {"ok": True, "command": "start"})

    def test_wrapper_rejects_fuzzy_or_extra_targets_without_ssh(self) -> None:
        for args in [("start", "room"), ("start", "roomba", "extra")]:
            with self.subTest(args=args):
                self.ssh_log.unlink(missing_ok=True)
                result = self.run_wrapper(*args)
                self.assertEqual(result.returncode, 2)
                self.assertEqual(self.ssh_calls(), [])

    def test_all_actions_continue_after_failure_and_return_structured_nonzero(self) -> None:
        result = self.run_wrapper("start", "all", scenario="partial_start_failure")

        self.assertEqual(result.returncode, 1, result.stderr)
        summary = self.action_summary(result)
        self.assertFalse(summary["ok"])
        self.assertEqual([item["robot"] for item in summary["results"]], ["10max", "j5"])
        self.assertTrue(summary["results"][0]["ok"])
        self.assertFalse(summary["results"][1]["ok"])
        self.assertEqual(summary["results"][1]["error"]["code"], "action_error")
        calls = self.ssh_calls()
        self.assertIn({"robot": "10max", "action": "start"}, [
            {"robot": call["robot"], "action": call["action"]} for call in calls
        ])
        self.assertIn({"robot": "j5", "action": "start"}, [
            {"robot": call["robot"], "action": call["action"]} for call in calls
        ])

    def test_transport_invalid_json_and_action_json_failures_propagate(self) -> None:
        expected_codes = {
            "transport": "transport_error",
            "invalid_json": "invalid_json",
            "action_error_zero_exit": "action_error",
        }
        for scenario, expected_code in expected_codes.items():
            with self.subTest(scenario=scenario):
                self.ssh_log.unlink(missing_ok=True)
                result = self.run_wrapper("start", "roomba", scenario=scenario)
                self.assertEqual(result.returncode, 1, result.stderr)
                item = self.action_summary(result)["results"][0]
                self.assertFalse(item["ok"])
                self.assertEqual(item["error"]["code"], expected_code)

    def test_dock_does_not_suppress_failed_stop(self) -> None:
        result = self.run_wrapper("dock", "roomba", scenario="dock_stop_failure")

        self.assertEqual(result.returncode, 1, result.stderr)
        item = self.action_summary(result)["results"][0]
        self.assertEqual(item["error"]["code"], "pre_dock_stop_failed")
        self.assertNotIn("dock", [call["action"] for call in self.ssh_calls()])

    def test_dock_verifies_stop_and_return_phase(self) -> None:
        result = self.run_wrapper("dock", "roomba", scenario="dock_success")

        self.assertEqual(result.returncode, 0, result.stderr)
        item = self.action_summary(result)["results"][0]
        self.assertTrue(item["ok"])
        self.assertEqual(item["verification"], "passed")
        self.assertEqual(item["phase"], "hmUsrDock")
        self.assertEqual(
            [call["action"] for call in self.ssh_calls()],
            ["status", "stop", "status", "dock", "status"],
        )

    def test_dock_skips_commands_only_when_charging_is_verified(self) -> None:
        result = self.run_wrapper(
            "dock", "roomba", scenario="dock_already_charging"
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        item = self.action_summary(result)["results"][0]
        self.assertTrue(item["skipped"])
        self.assertEqual(item["phase"], "charge")
        self.assertEqual([call["action"] for call in self.ssh_calls()], ["status"])

    def test_action_fails_when_bounded_verification_never_matches(self) -> None:
        result = self.run_wrapper(
            "start", "roomba", scenario="verification_timeout"
        )

        self.assertEqual(result.returncode, 1, result.stderr)
        item = self.action_summary(result)["results"][0]
        self.assertEqual(item["error"]["code"], "verification_failed")
        self.assertEqual(item["phase"], "new")
        self.assertEqual(
            [call["action"] for call in self.ssh_calls()],
            ["start", "status", "status"],
        )

    def test_status_all_checks_both_and_returns_nonzero_on_partial_failure(self) -> None:
        result = self.run_wrapper("status", "all", scenario="status_partial_failure")

        self.assertEqual(result.returncode, 1)
        self.assertIn("Roomba (10 Max)", result.stdout)
        self.assertIn("scoomba (J5): Error", result.stderr)
        self.assertEqual(
            [(call["robot"], call["action"]) for call in self.ssh_calls()],
            [("10max", "status"), ("j5", "status")],
        )


if __name__ == "__main__":
    unittest.main()
