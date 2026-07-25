#!/usr/bin/env python3
"""Fake-only tests for the read-only August home-event adapter."""

from __future__ import annotations

import importlib.util
import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
ADAPTER = REPO_ROOT / "openclaw" / "bin" / "august-event-adapter.py"
BUS_PATH = REPO_ROOT / "openclaw" / "bin" / "home_event_bus.py"

ADAPTER_SPEC = importlib.util.spec_from_file_location(
    "august_test_event_adapter", ADAPTER
)
assert ADAPTER_SPEC and ADAPTER_SPEC.loader
ADAPTER_MODULE = importlib.util.module_from_spec(ADAPTER_SPEC)
sys.modules[ADAPTER_SPEC.name] = ADAPTER_MODULE
ADAPTER_SPEC.loader.exec_module(ADAPTER_MODULE)

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
import json, os, sys, time
with open(os.environ['AUGUST_CALLS_LOG'], 'a', encoding='utf-8') as handle:
    handle.write(' '.join(sys.argv[1:]) + '\\n')
if os.environ.get('FAKE_AUGUST_STDERR'):
    sys.stderr.write(os.environ['FAKE_AUGUST_STDERR'])
if os.environ.get('FAKE_AUGUST_SLEEP'):
    time.sleep(float(os.environ['FAKE_AUGUST_SLEEP']))
if os.environ.get('FAKE_AUGUST_FAIL') == '1':
    sys.stdout.write(os.environ.get('FAKE_AUGUST_FAILURE_OUTPUT', ''))
    raise SystemExit(int(os.environ.get('FAKE_AUGUST_EXIT', '7')))
if os.environ.get('FAKE_AUGUST_NO_STDOUT') != '1':
    sys.stdout.write(os.environ.get(
        'FAKE_AUGUST_RAW_OUTPUT',
        os.environ['FAKE_AUGUST_OBSERVATION'] + '\\n',
    ))
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

    def reset_runtime(self) -> None:
        shutil.rmtree(
            self.home / ".openclaw" / "home-events",
            ignore_errors=True,
        )
        self.events_log.unlink(missing_ok=True)
        self.calls_log.unlink(missing_ok=True)

    @staticmethod
    def failure_envelope(code: str, message: str = "Observation unavailable") -> str:
        return json.dumps(
            {"success": False, "error_code": code, "message": message},
            separators=(",", ":"),
            sort_keys=True,
        )

    def establish_failure(
        self, **overrides: str
    ) -> subprocess.CompletedProcess[str]:
        baseline = self.run_adapter()
        self.assertEqual(baseline.returncode, 0, baseline.stderr)
        result = baseline
        for _ in range(3):
            self.make_due()
            result = self.run_adapter(**overrides)
            self.assertNotEqual(result.returncode, 0)
        return result

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
        self.assertEqual(state["last_error_code"], "observe_remote_failed")
        self.assertEqual(
            unavailable["attributes"]["reason_code"], "observe_remote_failed"
        )

    def test_allowlisted_wrapper_stages_reach_status_and_unavailable_event(self) -> None:
        secret_canary = "PRIVATE_AUGUST_STREAM_CANARY_6c3ca2"
        for stage in sorted(ADAPTER_MODULE.OBSERVE_STAGE_CODES):
            with self.subTest(stage=stage):
                self.reset_runtime()
                result = self.establish_failure(
                    FAKE_AUGUST_FAIL="1",
                    FAKE_AUGUST_FAILURE_OUTPUT=self.failure_envelope(stage),
                    FAKE_AUGUST_STDERR=secret_canary,
                )

                self.assertEqual(
                    json.loads(result.stdout)["error_code"],
                    stage,
                )
                state = json.loads(self.state_path.read_text(encoding="utf-8"))
                self.assertEqual(state["last_error_code"], stage)
                unavailable = self.published()[0]["payload"]
                self.assertEqual(
                    unavailable["attributes"]["reason_code"],
                    stage,
                )
                BUS.normalize_input(
                    "august",
                    unavailable,
                    b"a" * 32,
                    clock=lambda: unavailable["observed_at"],
                )
                self.assertNotIn(
                    secret_canary,
                    result.stdout + result.stderr + json.dumps(unavailable),
                )

    def test_untrusted_wrapper_stage_and_message_are_not_propagated(self) -> None:
        private_code = "private_provider_account_disabled"
        secret_canary = "PRIVATE_AUGUST_FAILURE_MESSAGE_942ad7"
        result = self.establish_failure(
            FAKE_AUGUST_FAIL="1",
            FAKE_AUGUST_FAILURE_OUTPUT=self.failure_envelope(
                private_code, secret_canary
            ),
            FAKE_AUGUST_STDERR=secret_canary,
        )

        self.assertEqual(
            json.loads(result.stdout)["error_code"],
            "observe_remote_failed",
        )
        state = json.loads(self.state_path.read_text(encoding="utf-8"))
        self.assertEqual(state["last_error_code"], "observe_remote_failed")
        unavailable = self.published()[0]["payload"]
        self.assertEqual(
            unavailable["attributes"]["reason_code"],
            "observe_remote_failed",
        )
        combined = result.stdout + result.stderr + json.dumps(state) + json.dumps(
            unavailable
        )
        self.assertNotIn(private_code, combined)
        self.assertNotIn(secret_canary, combined)

    def test_success_output_failures_are_distinct_and_stream_safe(self) -> None:
        private_canary = "PRIVATE_AUGUST_OUTPUT_CANARY_0e765f"
        cases = (
            (
                "missing",
                {"FAKE_AUGUST_NO_STDOUT": "1"},
                "observe_output_missing",
            ),
            (
                "oversize",
                {"FAKE_AUGUST_RAW_OUTPUT": private_canary + ("x" * 4096)},
                "observe_output_oversize",
            ),
            (
                "malformed",
                {"FAKE_AUGUST_RAW_OUTPUT": private_canary},
                "observe_output_malformed",
            ),
            (
                "contract_invalid",
                {
                    "FAKE_AUGUST_RAW_OUTPUT": json.dumps(
                        {
                            "ok": True,
                            "alias": "private_lock_alias",
                            "observed_at": "2026-01-01T12:05:00Z",
                            "lock_state": "locked",
                            "door_state": "closed",
                            "private": private_canary,
                        }
                    )
                },
                "observe_output_contract_invalid",
            ),
        )
        for label, overrides, expected in cases:
            with self.subTest(case=label):
                self.reset_runtime()
                baseline = self.run_adapter()
                self.assertEqual(baseline.returncode, 0, baseline.stderr)
                self.make_due()

                result = self.run_adapter(
                    FAKE_AUGUST_STDERR=private_canary,
                    **overrides,
                )

                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(json.loads(result.stdout)["error_code"], expected)
                state = json.loads(self.state_path.read_text(encoding="utf-8"))
                self.assertEqual(state["last_error_code"], expected)
                self.assertEqual(self.published(), [])
                self.assertNotIn(
                    private_canary,
                    result.stdout + result.stderr + json.dumps(state),
                )

    def test_legacy_state_codes_migrate_but_arbitrary_codes_fail_closed(self) -> None:
        for legacy, expected in ADAPTER_MODULE.LEGACY_OBSERVE_STAGE_CODES.items():
            with self.subTest(legacy=legacy):
                state = ADAPTER_MODULE.initial_state()
                state["last_error_code"] = legacy
                self.assertEqual(
                    ADAPTER_MODULE.validate_state(state)["last_error_code"],
                    expected,
                )

        state = ADAPTER_MODULE.initial_state()
        state["last_error_code"] = "provider_private_failure"
        with self.assertRaises(ADAPTER_MODULE.AdapterError) as raised:
            ADAPTER_MODULE.validate_state(state)
        self.assertEqual(raised.exception.code, "invalid_state_file")

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
