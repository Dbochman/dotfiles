#!/usr/bin/env python3

from __future__ import annotations

import importlib.machinery
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


HELPER = Path(__file__).resolve().parents[1] / "bin" / "presence-crosstown-canary"
LOADER = importlib.machinery.SourceFileLoader(
    "presence_crosstown_canary", str(HELPER)
)
SPEC = importlib.util.spec_from_loader(LOADER.name, LOADER)
assert SPEC is not None
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
LOADER.exec_module(MODULE)


RAW_STDOUT = "RAW_PRIVATE_STDOUT_CANARY_54A7E2"
RAW_STDERR = "RAW_PRIVATE_STDERR_CANARY_09B4C1"
PRIVATE_MAC_1 = "02:00:00:00:00:11"
PRIVATE_MAC_2 = "02:00:00:00:00:22"
PRIVATE_IP = "192.168.165.77"


FAKE_SSH = r'''#!/usr/bin/env python3
import datetime as dt
import json
import os
import sys
import time
from pathlib import Path

state = Path(os.environ["PRESENCE_CROSSTOWN_CANARY_FAKE_STATE"])
mode = os.environ.get("PRESENCE_CROSSTOWN_CANARY_FAKE_MODE", "success")
remote = sys.argv[-1]
stdin = sys.stdin.buffer.read()
now = dt.datetime.now(dt.timezone.utc)

if "presence-canary-projection-v1" in remote:
    counter = state / "projection-count"
    count = int(counter.read_text() or "0") if counter.exists() else 0
    counter.write_text(str(count + 1))
    if mode == "projection_fail":
        print("RAW_PRIVATE_STDOUT_CANARY_54A7E2")
        print("RAW_PRIVATE_STDERR_CANARY_09B4C1", file=sys.stderr)
        raise SystemExit(9)
    timestamp = now
    if mode == "stale_legacy":
        timestamp -= dt.timedelta(hours=1)
    if mode == "future_legacy":
        timestamp += dt.timedelta(minutes=10)
    if mode == "timestamp_skew":
        timestamp -= dt.timedelta(seconds=150)
    digest = "a" * 64
    inode = 200
    if mode == "canonical_changed" and count > 0:
        digest = "b" * 64
        inode = 201
    payload = {
        "binding_parity": mode != "binding_mismatch",
        "decision": [True, True],
        "fingerprint": {
            "device": 1,
            "inode": inode,
            "mtime_ns": 123456789,
            "sha256": digest,
            "size": 800,
        },
        "ok": True,
        "schema_version": 1,
        "timestamp": timestamp.isoformat().replace("+00:00", "Z"),
    }
    print(json.dumps(payload, separators=(",", ":")))
    raise SystemExit(0)

if "validate-config crosstown" in remote:
    (state / "validate.stdin").write_bytes(stdin)
    if mode == "timeout":
        time.sleep(5)
    if mode == "oversized":
        print("X" * 4096 + "RAW_PRIVATE_STDOUT_CANARY_54A7E2")
        raise SystemExit(0)
    if mode == "validate_fail":
        print("RAW_PRIVATE_STDOUT_CANARY_54A7E2")
        print("RAW_PRIVATE_STDERR_CANARY_09B4C1", file=sys.stderr)
        raise SystemExit(7)
    print('{"ok":true,"site":"crosstown"}')
    raise SystemExit(0)

if "observe crosstown" in remote:
    (state / "observe.stdin").write_bytes(stdin)
    if mode == "observe_fail":
        print("RAW_PRIVATE_STDOUT_CANARY_54A7E2")
        print("RAW_PRIVATE_STDERR_CANARY_09B4C1", file=sys.stderr)
        raise SystemExit(8)
    second = mode != "decision_mismatch"
    details = {"present": True}
    if mode == "unsanitized_observe":
        details["mac"] = "02:00:00:00:00:11"
        details["ip"] = "192.168.165.77"
    payload = {
        "location": "crosstown",
        "timestamp": now.isoformat().replace("+00:00", "Z"),
        "totalDevices": 3,
        "presence": {
            "Dylan": details,
            "Julia": {"present": second},
        },
    }
    print(json.dumps(payload, separators=(",", ":")))
    raise SystemExit(0)

print("RAW_PRIVATE_STDERR_CANARY_09B4C1", file=sys.stderr)
raise SystemExit(64)
'''


class CrosstownCanaryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.home = self.root / "home"
        self.home.mkdir(mode=0o700)
        self.state = self.root / "fake-state"
        self.state.mkdir(mode=0o700)
        self.scanner = self.root / "presence-detect.sh"
        self.scanner_bytes = (
            b"#!/bin/bash\n# exact candidate scanner bytes\n"
            b'PRESENCE_SCANNER_DEPLOYMENT_CONTRACT="strict-site-bindings-v1"\n'
            b"printf 'SCANNER_BYTE_SENTINEL_6019' >/dev/null\n"
        )
        self.scanner.write_bytes(self.scanner_bytes)
        self.scanner.chmod(0o755)
        self.key = self.root / "id_mini_to_mbp"
        self.key.write_text("synthetic test key\n", encoding="utf-8")
        self.key.chmod(0o600)
        self.fake_ssh = self.root / "fake-ssh"
        self.fake_ssh.write_text(FAKE_SSH, encoding="utf-8")
        self.fake_ssh.chmod(0o755)

    def environment(self, mode: str = "success") -> dict[str, str]:
        return {
            **os.environ,
            "HOME": str(self.home),
            "PRESENCE_CROSSTOWN_CANARY_SCANNER": str(self.scanner),
            "PRESENCE_CROSSTOWN_CANARY_SSH_BIN": str(self.fake_ssh),
            "PRESENCE_CROSSTOWN_CANARY_SSH_KEY": str(self.key),
            "PRESENCE_CROSSTOWN_CANARY_SSH_HOST": "fake-crosstown",
            "PRESENCE_CROSSTOWN_CANARY_FAKE_STATE": str(self.state),
            "PRESENCE_CROSSTOWN_CANARY_FAKE_MODE": mode,
        }

    def run_helper(
        self, mode: str = "success", *, extra: dict[str, str] | None = None
    ) -> subprocess.CompletedProcess[str]:
        environment = self.environment(mode)
        if extra:
            environment.update(extra)
        return subprocess.run(
            [sys.executable, str(HELPER)],
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=10,
            env=environment,
            check=False,
        )

    def assert_no_private_output(self, result: subprocess.CompletedProcess[str]) -> None:
        combined = result.stdout + result.stderr
        for sentinel in (
            RAW_STDOUT,
            RAW_STDERR,
            PRIVATE_MAC_1,
            PRIVATE_MAC_2,
            PRIVATE_IP,
            "SCANNER_BYTE_SENTINEL_6019",
        ):
            self.assertNotIn(sentinel, combined)

    def error(self, result: subprocess.CompletedProcess[str]) -> dict[str, object]:
        self.assertFalse(result.stdout)
        return json.loads(result.stderr)

    def test_success_streams_exact_scanner_and_emits_only_safe_summary(self) -> None:
        before_home = sorted(path.relative_to(self.home) for path in self.home.rglob("*"))
        result = self.run_helper()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(result.stderr)
        summary = json.loads(result.stdout)
        self.assertEqual(
            set(summary),
            {
                "binding_parity",
                "canonical_unchanged",
                "config_valid",
                "decision_parity",
                "legacy_snapshot_fresh",
                "mismatch_count",
                "ok",
                "scanner_sha256",
                "site",
                "strict_output_sanitized",
                "timestamp_skew_bucket",
            },
        )
        self.assertTrue(summary["ok"])
        self.assertTrue(summary["binding_parity"])
        self.assertTrue(summary["canonical_unchanged"])
        self.assertTrue(summary["decision_parity"])
        self.assertEqual(summary["mismatch_count"], 0)
        self.assertRegex(summary["scanner_sha256"], r"^[0-9a-f]{64}$")
        self.assertEqual(
            (self.state / "validate.stdin").read_bytes(), self.scanner_bytes
        )
        self.assertEqual(
            (self.state / "observe.stdin").read_bytes(), self.scanner_bytes
        )
        after_home = sorted(path.relative_to(self.home) for path in self.home.rglob("*"))
        self.assertEqual(after_home, before_home)
        self.assert_no_private_output(result)

    def test_missing_strict_contract_fails_before_ssh(self) -> None:
        self.scanner.write_bytes(b"#!/bin/bash\nexit 0\n")

        completed = self.run_helper()

        self.assertNotEqual(completed.returncode, 0)
        self.assertEqual(completed.stdout, "")
        error = json.loads(completed.stderr)
        self.assertEqual(error["error_code"], "scanner_contract_invalid")
        self.assertFalse((self.state / "validate.stdin").exists())
        self.assert_no_private_output(completed)

    def test_remote_commands_use_sanitized_environments(self) -> None:
        for command in (
            MODULE.REMOTE_VALIDATE_COMMAND,
            MODULE.REMOTE_OBSERVE_COMMAND,
            MODULE.REMOTE_PROJECTION_COMMAND,
        ):
            self.assertIn("/usr/bin/env -i", command)
        self.assertIn(
            "HOME_EVENTS_PRESENCE_ENABLED=0", MODULE.REMOTE_VALIDATE_COMMAND
        )
        self.assertIn(
            "HOME_EVENTS_PRESENCE_ENABLED=0", MODULE.REMOTE_OBSERVE_COMMAND
        )
        self.assertNotIn(
            "HOME_EVENTS_PRESENCE_ENABLED=0", MODULE.REMOTE_PROJECTION_COMMAND
        )

    def test_option_like_ssh_host_is_rejected_before_ssh(self) -> None:
        completed = self.run_helper(
            extra={"PRESENCE_CROSSTOWN_CANARY_SSH_HOST": "-proxy-command"}
        )

        self.assertNotEqual(completed.returncode, 0)
        self.assertEqual(self.error(completed)["error_code"], "configuration_invalid")
        self.assertFalse((self.state / "validate.stdin").exists())

    def test_group_writable_scanner_is_rejected_before_ssh(self) -> None:
        self.scanner.chmod(0o775)

        completed = self.run_helper()

        self.assertNotEqual(completed.returncode, 0)
        self.assertEqual(self.error(completed)["error_code"], "scanner_unsafe")
        self.assertFalse((self.state / "validate.stdin").exists())

    def test_decision_mismatch_is_a_safe_summary_failure(self) -> None:
        result = self.run_helper("decision_mismatch")
        self.assertEqual(result.returncode, 1)
        self.assertFalse(result.stderr)
        summary = json.loads(result.stdout)
        self.assertFalse(summary["ok"])
        self.assertFalse(summary["decision_parity"])
        self.assertEqual(summary["mismatch_count"], 1)
        self.assertTrue(summary["canonical_unchanged"])
        self.assert_no_private_output(result)

    def test_binding_mismatch_stops_before_observation(self) -> None:
        result = self.run_helper("binding_mismatch")
        self.assertEqual(result.returncode, 1)
        self.assertEqual(self.error(result)["error_code"], "binding_parity_failed")
        self.assertFalse((self.state / "observe.stdin").exists())
        self.assert_no_private_output(result)

    def test_changed_canonical_snapshot_is_rejected(self) -> None:
        result = self.run_helper("canonical_changed")
        self.assertEqual(result.returncode, 1)
        self.assertEqual(self.error(result)["error_code"], "canonical_changed")
        self.assert_no_private_output(result)

    def test_unsanitized_strict_observation_never_reaches_output(self) -> None:
        result = self.run_helper("unsanitized_observe")
        self.assertEqual(result.returncode, 1)
        self.assertEqual(
            self.error(result)["error_code"], "strict_observation_invalid"
        )
        self.assert_no_private_output(result)

    def test_raw_subprocess_failures_are_replaced_with_fixed_codes(self) -> None:
        cases = {
            "validate_fail": "strict_validate_failed",
            "projection_fail": "legacy_projection_failed",
            "observe_fail": "strict_observe_failed",
        }
        for mode, expected in cases.items():
            with self.subTest(mode=mode):
                result = self.run_helper(mode)
                self.assertEqual(result.returncode, 1)
                self.assertEqual(self.error(result)["error_code"], expected)
                self.assert_no_private_output(result)
                for child in self.state.iterdir():
                    child.unlink()

    def test_subprocess_timeout_and_output_cap_are_safe(self) -> None:
        timeout = self.run_helper(
            "timeout",
            extra={"PRESENCE_CROSSTOWN_CANARY_TIMEOUT_SECONDS": "1"},
        )
        self.assertEqual(timeout.returncode, 1)
        self.assertEqual(self.error(timeout)["error_code"], "strict_validate_failed")
        self.assert_no_private_output(timeout)

        for child in self.state.iterdir():
            child.unlink()
        oversized = self.run_helper(
            "oversized",
            extra={"PRESENCE_CROSSTOWN_CANARY_MAX_OUTPUT_BYTES": "1024"},
        )
        self.assertEqual(oversized.returncode, 1)
        self.assertEqual(
            self.error(oversized)["error_code"], "strict_validate_failed"
        )
        self.assert_no_private_output(oversized)

    def test_stale_legacy_snapshot_stops_before_observation(self) -> None:
        result = self.run_helper("stale_legacy")
        self.assertEqual(result.returncode, 1)
        self.assertEqual(self.error(result)["error_code"], "legacy_snapshot_stale")
        self.assertFalse((self.state / "observe.stdin").exists())
        self.assert_no_private_output(result)

    def test_future_legacy_snapshot_stops_before_observation(self) -> None:
        result = self.run_helper("future_legacy")
        self.assertEqual(result.returncode, 1)
        self.assertEqual(self.error(result)["error_code"], "legacy_snapshot_stale")
        self.assertFalse((self.state / "observe.stdin").exists())
        self.assert_no_private_output(result)

    def test_timestamp_skew_is_rejected_after_observation(self) -> None:
        result = self.run_helper("timestamp_skew")
        self.assertEqual(result.returncode, 1)
        self.assertEqual(self.error(result)["error_code"], "timestamp_skew_exceeded")
        self.assert_no_private_output(result)


class RemoteProjectionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.home = self.root / "home"
        self.openclaw = self.home / ".openclaw"
        self.presence = self.openclaw / "presence"
        self.presence.mkdir(parents=True, mode=0o700)
        self.openclaw.chmod(0o700)
        self.presence.chmod(0o700)
        self.legacy_env = self.openclaw / "presence-devices.env"
        self.strict_json = self.openclaw / "presence-devices.json"
        self.canonical = self.presence / "crosstown-scan.json"
        self.legacy_env.write_text(
            f"CROSSTOWN_DYLAN_MAC={PRIVATE_MAC_1}\n"
            f"export CROSSTOWN_JULIA_MAC='{PRIVATE_MAC_2}'\n",
            encoding="utf-8",
        )
        self.strict_json.write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "site": "crosstown",
                    "people": {
                        "Dylan": {"kind": "mac", "value": PRIVATE_MAC_1},
                        "Julia": {"kind": "mac", "value": PRIVATE_MAC_2},
                    },
                }
            ),
            encoding="utf-8",
        )
        self.canonical.write_text(
            json.dumps(
                {
                    "location": "crosstown",
                    "timestamp": "2026-07-20T12:00:00Z",
                    "presence": {
                        "Dylan": {
                            "present": True,
                            "mac": PRIVATE_MAC_1,
                            "ip": PRIVATE_IP,
                        },
                        "Julia": {"present": False},
                    },
                }
            ),
            encoding="utf-8",
        )
        for path in (self.legacy_env, self.strict_json, self.canonical):
            path.chmod(0o600)

    def run_projection(self) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, "-c", MODULE.REMOTE_PROJECTION_SOURCE],
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=5,
            env={"HOME": str(self.home), "PATH": os.environ.get("PATH", "")},
            check=False,
        )

    def test_projection_parses_env_as_data_and_sanitizes_legacy_snapshot(self) -> None:
        result = self.run_projection()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(result.stderr)
        projection = json.loads(result.stdout)
        self.assertTrue(projection["binding_parity"])
        self.assertEqual(projection["decision"], [True, False])
        combined = result.stdout + result.stderr
        self.assertNotIn(PRIVATE_MAC_1, combined)
        self.assertNotIn(PRIVATE_MAC_2, combined)
        self.assertNotIn(PRIVATE_IP, combined)
        self.assertNotIn("Dylan", combined)
        self.assertNotIn("Julia", combined)

    def test_env_file_is_never_executed_and_invalid_syntax_is_silent(self) -> None:
        marker = self.root / "must-not-exist"
        self.legacy_env.write_text(
            f"CROSSTOWN_DYLAN_MAC={PRIVATE_MAC_1}\n"
            f"touch {marker}\n"
            f"CROSSTOWN_JULIA_MAC={PRIVATE_MAC_2}\n",
            encoding="utf-8",
        )
        self.legacy_env.chmod(0o600)
        result = self.run_projection()
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(result.stdout)
        self.assertFalse(result.stderr)
        self.assertFalse(marker.exists())


if __name__ == "__main__":
    unittest.main()
