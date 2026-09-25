#!/usr/bin/env python3
"""Tests for the deterministic Dylan morning briefing data collector."""

from __future__ import annotations

import importlib.util
import json
import os
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from datetime import datetime, timezone
from pathlib import Path


SCRIPT = Path(__file__).parents[1] / "bin" / "dylan-morning-briefing-data.py"
SPEC = importlib.util.spec_from_file_location("dylan_morning_briefing_data", SCRIPT)
assert SPEC and SPEC.loader
briefing = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = briefing
SPEC.loader.exec_module(briefing)


class DylanMorningBriefingDataTests(unittest.TestCase):
    def setUp(self) -> None:
        original_account = briefing.ACCOUNT
        briefing.ACCOUNT = "dylan@example.invalid"
        self.addCleanup(setattr, briefing, "ACCOUNT", original_account)

    def test_missing_account_fails_unavailable_before_spawning_gws(self) -> None:
        briefing.ACCOUNT = ""

        def forbidden_runner(*_args, **_kwargs):
            self.fail("GWS must not be spawned without an explicit account")

        result = briefing.collect_data(runner=forbidden_runner)

        self.assertEqual(result["calendar"]["reason"], "missing_account")
        self.assertEqual(result["inbox"]["reason"], "missing_account")

    def test_success_filters_gmail_output_and_uses_raw_api_account_env(self) -> None:
        calls: list[tuple[list[str], dict[str, str]]] = []

        def runner(args: list[str], env: dict[str, str], timeout: float):
            self.assertEqual(timeout, briefing.COMMAND_TIMEOUT_SECONDS)
            calls.append((args, env))
            if "+agenda" in args:
                return briefing.CommandResult(
                    0,
                    json.dumps(
                        {
                            "count": 1,
                            "timeMin": "private-bound",
                            "timeMax": "private-bound",
                            "events": [
                                {
                                    "calendar": "private-calendar",
                                    "id": "private-event-id",
                                    "start": "Synthetic start",
                                    "end": "Synthetic end",
                                    "summary": "Synthetic\nevent",
                                    "location": "Synthetic location",
                                }
                            ],
                        }
                    ),
                    "",
                )
            params = json.loads(args[args.index("--params") + 1])
            if "list" in args:
                self.assertEqual(params["q"], "in:inbox newer_than:1d")
                self.assertEqual(params["maxResults"], 100)
                return briefing.CommandResult(
                    0,
                    json.dumps(
                        {
                            "messages": [{"id": "private-id-1"}],
                            "resultSizeEstimate": 1,
                        }
                    ),
                    "",
                )
            self.assertNotIn("metadataHeaders", params)
            self.assertEqual(params["format"], "metadata")
            return briefing.CommandResult(
                0,
                json.dumps(
                    {
                        "id": params["id"],
                        "snippet": "must not escape",
                        "payload": {
                            "headers": [
                                {"name": "From", "value": "Person <person@example.com>"},
                                {"name": "Subject", "value": "Synthetic\nsubject"},
                                {"name": "Date", "value": "Synthetic date"},
                                {"name": "Message-ID", "value": "private-message-id"},
                            ]
                        },
                    }
                ),
                "",
            )

        result = briefing.collect_data(runner=runner, sleeper=lambda _: None)

        self.assertEqual(result["calendar"]["status"], "ok")
        self.assertEqual(
            set(result["calendar"]["events"][0]),
            {"start", "end", "summary", "location"},
        )
        self.assertEqual(result["calendar"]["events"][0]["summary"], "Synthetic event")
        self.assertEqual(result["inbox"]["status"], "ok")
        self.assertEqual(result["inbox"]["count"], 1)
        self.assertEqual(
            set(result["inbox"]["messages"][0]), {"from", "subject", "date"}
        )
        self.assertEqual(
            result["inbox"]["messages"][0]["subject"], "Synthetic subject"
        )
        serialized = json.dumps(result)
        self.assertNotIn("private-id-1", serialized)
        self.assertNotIn("private-message-id", serialized)
        self.assertNotIn("private-event-id", serialized)
        self.assertNotIn("private-calendar", serialized)
        self.assertNotIn("private-bound", serialized)
        self.assertNotIn("must not escape", serialized)
        for args, env in calls:
            if "gmail" in args:
                self.assertEqual(
                    env["GOOGLE_WORKSPACE_CLI_ACCOUNT"], briefing.ACCOUNT
                )
                self.assertNotIn("--account", args)

    def test_token_races_retry_once(self) -> None:
        attempts = {"calendar": 0, "list": 0}
        sleeps: list[float] = []

        def runner(args: list[str], env: dict[str, str], timeout: float):
            if "+agenda" in args:
                attempts["calendar"] += 1
                if attempts["calendar"] == 1:
                    return briefing.CommandResult(1, "", "Failed to get token")
                return briefing.CommandResult(
                    0, json.dumps({"count": 0, "events": []}), ""
                )
            attempts["list"] += 1
            if attempts["list"] == 1:
                return briefing.CommandResult(1, "Failed to get token", "")
            return briefing.CommandResult(0, json.dumps({"messages": []}), "")

        result = briefing.collect_data(runner=runner, sleeper=sleeps.append)

        self.assertEqual(result["calendar"]["status"], "ok")
        self.assertEqual(result["inbox"]["status"], "ok")
        self.assertEqual(attempts, {"calendar": 2, "list": 2})
        self.assertEqual(sleeps, [5.0, 5.0])

    def test_inbox_paginates_exact_counts_and_bounds_header_sampling(self) -> None:
        calls = []

        def runner(args, env, timeout):
            calls.append(args)
            params = json.loads(args[-1])
            if "list" in args:
                if "pageToken" not in params:
                    payload = {
                        "messages": [{"id": f"private-{index}"} for index in range(20)],
                        "nextPageToken": "private-next", "resultSizeEstimate": 1,
                    }
                else:
                    self.assertEqual(params["pageToken"], "private-next")
                    payload = {"messages": [{"id": f"private-{index}"} for index in range(19, 31)]}
            else:
                payload = {"payload": {"headers": [{"name": "Subject", "value": "Synthetic"}]}}
            return briefing.CommandResult(0, json.dumps(payload), "")

        result = briefing.collect_inbox(deadline=150, clock=lambda: 0, runner=runner, sleeper=lambda _: None)
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["count"], 31)
        self.assertTrue(result["inventoryComplete"])
        self.assertTrue(result["truncated"])
        self.assertEqual(result["sampledCount"], 25)
        self.assertEqual(len(result["messages"]), 25)
        self.assertEqual(len(calls), 27)
        self.assertNotIn("private-", json.dumps(result))

    def test_failed_or_cyclic_inventory_is_not_an_exact_count(self) -> None:
        for failure in ("auth", "cycle", "malformed"):
            with self.subTest(failure=failure):
                calls = []

                def runner(args, env, timeout):
                    calls.append(args)
                    self.assertIn("list", args)
                    if len(calls) > 1 and failure == "auth":
                        return briefing.CommandResult(1, "", "No credentials provided PRIVATE_DETAIL")
                    payload = {"messages": [{"id": "private-id"}], "nextPageToken": "same"}
                    if len(calls) > 1 and failure == "malformed":
                        payload = {"messages": [{"id": None}]}
                    return briefing.CommandResult(0, json.dumps(payload), "")

                result = briefing.collect_inbox(deadline=150, clock=lambda: 0, runner=runner, sleeper=lambda _: self.fail("No retry"))
                self.assertEqual(result["status"], "partial")
                self.assertIsNone(result["count"])
                self.assertEqual(result["observedCount"], 1)
                self.assertFalse(result["inventoryComplete"])
                self.assertEqual(len(calls), 2)
                self.assertNotIn("PRIVATE_DETAIL", json.dumps(result))

    def test_inventory_scan_limit_is_explicit(self) -> None:
        calls = []

        def runner(args, env, timeout):
            calls.append(args)
            return briefing.CommandResult(0, json.dumps({
                "messages": [{"id": f"private-{len(calls)}"}],
                "nextPageToken": str(len(calls)),
            }), "")

        result = briefing.collect_inbox(deadline=150, clock=lambda: 0, runner=runner, sleeper=lambda _: None)
        self.assertEqual(len(calls), 10)
        self.assertEqual(result["reason"], "inventory_truncated")
        self.assertIsNone(result["count"])
        self.assertEqual(result["observedCount"], 10)

    def test_metadata_auth_failure_stops_and_preserves_existing_summaries(self) -> None:
        calls = []

        def runner(args, env, timeout):
            calls.append(args)
            if "list" in args:
                return briefing.CommandResult(0, json.dumps({
                    "messages": [{"id": str(index)} for index in range(3)],
                }), "")
            if len(calls) == 2:
                return briefing.CommandResult(0, json.dumps({
                    "payload": {"headers": [{"name": "Subject", "value": "Known subject"}]},
                }), "")
            return briefing.CommandResult(1, "", "No credentials provided")

        result = briefing.collect_inbox(deadline=150, clock=lambda: 0, runner=runner, sleeper=lambda _: self.fail("No retry"))
        self.assertEqual(len(calls), 3)
        self.assertEqual(result["status"], "partial")
        self.assertEqual(result["count"], 3)
        self.assertEqual(result["sampledCount"], 2)
        self.assertEqual(result["messages"][0]["subject"], "Known subject")
        self.assertEqual(result["failedCount"], 1)

    def test_current_eastern_date_and_independent_source_failures(self) -> None:
        def runner(args, env, timeout):
            if "+agenda" in args:
                return briefing.CommandResult(1, "", "No credentials provided")
            return briefing.CommandResult(0, json.dumps({"messages": []}), "")

        result = briefing.collect_data(
            now=datetime(2026, 9, 26, 1, tzinfo=timezone.utc),
            runner=runner, sleeper=lambda _: self.fail("No retry"),
        )
        self.assertEqual(result["date"], "2026-09-25")
        self.assertEqual(result["calendar"]["status"], "unavailable")
        self.assertEqual(result["inbox"]["status"], "ok")

    def test_empty_inbox_is_success(self) -> None:
        def runner(args: list[str], env: dict[str, str], timeout: float):
            if "+agenda" in args:
                return briefing.CommandResult(
                    0, json.dumps({"count": 0, "events": []}), ""
                )
            return briefing.CommandResult(0, json.dumps({"resultSizeEstimate": 0}), "")

        result = briefing.collect_data(runner=runner, sleeper=lambda _: None)

        self.assertEqual(result["inbox"]["status"], "ok")
        self.assertEqual(result["inbox"]["count"], 0)
        self.assertEqual(result["inbox"]["messages"], [])

    def test_successful_user_content_does_not_trigger_token_retry(self) -> None:
        calls = 0

        def runner(args: list[str], env: dict[str, str], timeout: float):
            nonlocal calls
            calls += 1
            return briefing.CommandResult(
                0,
                json.dumps(
                    {
                        "count": 1,
                        "events": [
                            {
                                "start": "Synthetic start",
                                "end": "Synthetic end",
                                "summary": "Failed to get token workshop",
                                "location": "Synthetic location",
                            }
                        ],
                    }
                ),
                "",
            )

        result = briefing.collect_calendar(
            deadline=150.0,
            clock=lambda: 0.0,
            runner=runner,
            sleeper=lambda _: self.fail("successful content must not be retried"),
        )

        self.assertEqual(result["status"], "ok")
        self.assertEqual(calls, 1)

    def test_missing_headers_is_partial_without_raw_payload(self) -> None:
        def runner(args: list[str], env: dict[str, str], timeout: float):
            if "+agenda" in args:
                return briefing.CommandResult(
                    0, json.dumps({"count": 0, "events": []}), ""
                )
            if "list" in args:
                return briefing.CommandResult(
                    0, json.dumps({"messages": [{"id": "private-id"}]}), ""
                )
            return briefing.CommandResult(
                0,
                json.dumps(
                    {"id": "private-id", "payload": {"mimeType": "text/plain"}}
                ),
                "",
            )

        result = briefing.collect_data(runner=runner, sleeper=lambda _: None)

        self.assertEqual(result["inbox"]["status"], "partial")
        self.assertEqual(result["inbox"]["failedCount"], 1)
        self.assertEqual(result["inbox"]["errorCounts"], {"missing_headers": 1})
        self.assertNotIn("private-id", json.dumps(result))

    def test_overall_deadline_prevents_additional_commands(self) -> None:
        times = iter([0.0, 151.0, 151.0])

        def runner(args: list[str], env: dict[str, str], timeout: float):
            self.fail("runner must not be called after the overall deadline")

        result = briefing.collect_data(
            runner=runner, sleeper=lambda _: None, clock=lambda: next(times)
        )

        self.assertEqual(result["calendar"]["status"], "unavailable")
        self.assertEqual(result["calendar"]["reason"], "timeout")
        self.assertEqual(result["inbox"]["status"], "unavailable")
        self.assertEqual(result["inbox"]["reason"], "timeout")

    def test_timeout_terminates_the_complete_child_process_group(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            marker = Path(temp_dir) / "child.pid"
            parent_code = """
import pathlib
import subprocess
import sys

child = subprocess.Popen(['/bin/sleep', '30'])
pathlib.Path(sys.argv[1]).write_text(str(child.pid))
child.wait()
"""
            result = briefing.run_command(
                [sys.executable, "-c", parent_code, str(marker)],
                os.environ.copy(),
                1.0,
            )

            self.assertEqual(result.returncode, 124)
            self.assertTrue(marker.is_file())
            child_pid = int(marker.read_text())
            process_gone = False
            deadline = time.monotonic() + 2.0
            while time.monotonic() < deadline:
                try:
                    os.kill(child_pid, 0)
                except ProcessLookupError:
                    process_gone = True
                    break
                time.sleep(0.05)
            self.assertTrue(process_gone, "timed-out grandchild survived cleanup")

    def test_external_termination_cleans_up_active_gws_process_group(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            marker = temp_path / "child.pid"
            fake_gws = temp_path / "fake-gws"
            fake_gws.write_text(
                """#!/usr/bin/env python3
import os
import pathlib
import subprocess

child = subprocess.Popen(['/bin/sleep', '30'])
pathlib.Path(os.environ['CHILD_MARKER']).write_text(str(child.pid))
child.wait()
"""
            )
            fake_gws.chmod(0o700)
            env = os.environ.copy()
            env["GWS_BIN"] = str(fake_gws)
            env["CHILD_MARKER"] = str(marker)
            env["DYLAN_EMAIL"] = "dylan@example.invalid"
            process = subprocess.Popen(
                [sys.executable, str(SCRIPT)],
                env=env,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            try:
                marker_deadline = time.monotonic() + 3.0
                while not marker.is_file() and time.monotonic() < marker_deadline:
                    time.sleep(0.05)
                self.assertTrue(marker.is_file(), "fake gws child did not start")
                child_pid = int(marker.read_text())
                process.send_signal(signal.SIGTERM)
                process.communicate(timeout=5.0)
                self.assertEqual(process.returncode, 128 + signal.SIGTERM)

                process_gone = False
                cleanup_deadline = time.monotonic() + 2.0
                while time.monotonic() < cleanup_deadline:
                    try:
                        os.kill(child_pid, 0)
                    except ProcessLookupError:
                        process_gone = True
                        break
                    time.sleep(0.05)
                self.assertTrue(
                    process_gone, "externally interrupted grandchild survived cleanup"
                )
            finally:
                if process.poll() is None:
                    process.kill()
                    process.communicate()


if __name__ == "__main__":
    unittest.main()
