import contextlib
from datetime import datetime, timedelta
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest import mock

from openclaw.tests.test_julia_morning_briefing_data import briefing

SPEC = importlib.util.spec_from_file_location("sleep_snapshot", Path(__file__).resolve().parents[1] / "bin/8sleep-snapshot.py")
snapshot = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(snapshot)


class SleepSnapshotTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.home = Path(temporary.name)
        self.root = self.home / ".openclaw/8sleep-snapshots"
        self.root.parent.mkdir(mode=0o700)
        snapshot.prepare_root(self.root)
        self.cache = self.root.parent / ".secrets-cache"
        self.cache.write_text("\n".join(f"{key}='fake-{index}'" for index, key in enumerate(snapshot.KEYS)) + "\nUNRELATED_SECRET=never_in_child\n")
        self.cache.chmod(0o600)
        self.now = datetime(2026, 10, 12, 6, 50, tzinfo=snapshot.TIME_ZONE)
        self.payload = {"side": "julia", "days": [{"day": "2026-10-12", "sleepDuration": 25200, "score": 85}]}

    def test_cache_is_narrow_and_never_evaluates_shell(self):
        environment = snapshot.load_environment(self.cache)
        self.assertEqual(set(environment), set(snapshot.KEYS) | {"HOME", "PATH"})
        self.assertNotIn("UNRELATED_SECRET", environment)
        self.cache.write_text(self.cache.read_text().replace("'fake-0'", "'$(touch /not-executed)'"))
        self.assertEqual(snapshot.load_environment(self.cache)[snapshot.KEYS[0]], "$(touch /not-executed)")

    def test_cache_rejects_unsafe_permissions_symlinks_missing_and_duplicate_owners(self):
        self.cache.chmod(0o644)
        with self.assertRaises(ValueError):snapshot.load_environment(self.cache)
        self.cache.chmod(0o600)
        link = self.root / "cache-link"
        link.symlink_to(self.cache)
        with self.assertRaises(OSError):snapshot.load_environment(link)
        self.cache.write_text(self.cache.read_text().replace("fake-3", "fake-2"))
        with self.assertRaises(ValueError):snapshot.load_environment(self.cache)
        self.cache.write_text("")
        with self.assertRaises(ValueError):snapshot.load_environment(self.cache)

    def test_success_requires_owner_wake_date_and_real_duration(self):
        self.assertEqual(snapshot.summarize(self.payload, "julia", "2026-10-12")[0], "ok")
        self.assertEqual(snapshot.summarize(self.payload, "dylan", "2026-10-12")[0], "unavailable")
        self.assertEqual(snapshot.summarize(self.payload, "julia", "2026-10-13")[1], "stale_response")
        for duration in (None, 0, True, -1, float("nan"), 90000):
            self.payload["days"][0]["sleepDuration"] = duration
            self.assertEqual(snapshot.summarize(self.payload, "julia", "2026-10-12")[0], "unavailable")

    def test_no_data_is_not_an_auth_or_success_result(self):
        self.assertEqual(snapshot.summarize({"side": "julia", "days": []}, "julia", "2026-10-12"), ("no_data", "no_sleep_data", None))
        for payload in ({"side": "julia", "error": "private", "days": []}, [], {}, {"side": "julia", "days": None}):
            self.assertEqual(snapshot.summarize(payload, "julia", "2026-10-12")[0], "unavailable")

    def test_bounded_read_only_command_and_response(self):
        def runner(command, **kwargs):
            self.assertEqual(command, ["/usr/bin/python3", "/fake/api.py", "sleep", "julia", "2026-10-12"])
            self.assertEqual(kwargs["timeout"], 60)
            self.assertEqual(kwargs["stderr"], subprocess.DEVNULL)
            kwargs["stdout"].write(json.dumps(self.payload).encode())
            return subprocess.CompletedProcess(command, 0)
        self.assertEqual(snapshot.collect("julia", "2026-10-12", {}, "/fake/api.py", runner)[0], "ok")

    def test_api_failure_timeout_and_malformed_output(self):
        for raw, code, expected in ((b"", 1, "api_failed"), (b"not json", 0, "invalid_response"), (b"x" * (snapshot.LIMIT + 1), 0, "oversized_response")):
            def runner(command, **kwargs):
                kwargs["stdout"].write(raw)
                return subprocess.CompletedProcess(command, code)
            self.assertEqual(snapshot.collect("julia", "2026-10-12", {}, "/fake", runner)[1], expected)
        runner = mock.Mock(side_effect=subprocess.TimeoutExpired("fake", 60))
        self.assertEqual(snapshot.collect("julia", "2026-10-12", {}, "/fake", runner)[1], "timeout")
        self.assertEqual(runner.call_count, 1)

    def test_private_atomic_snapshot_and_failed_replacement(self):
        snapshot.write_snapshot(self.root, "julia", snapshot.summarize(self.payload, "julia", "2026-10-12"), self.now)
        path = self.root / "julia.json"
        self.assertEqual(path.stat().st_mode & 0o777, 0o600)
        self.assertEqual(self.root.stat().st_mode & 0o777, 0o700)
        self.assertEqual(briefing.collect_sleep(self.now, snapshot_path=path)["status"], "ok")
        snapshot.write_snapshot(self.root, "julia", ("unavailable", "api_failed", None), self.now)
        self.assertNotIn("text", json.loads(path.read_text()))
        self.assertEqual(briefing.collect_sleep(self.now, snapshot_path=path), {"status": "unavailable", "reason": "api_failed"})
        self.assertEqual(list(self.root.glob(".snapshot-*")), [])

    def test_reader_rejects_other_owner_stale_future_and_public_snapshot(self):
        path = self.root / "julia.json"
        snapshot.write_snapshot(self.root, "julia", ("ok", "verified", "private-summary"), self.now)
        original = json.loads(path.read_text())
        for patch in ({"owner": "dylan"}, {"date": "2026-10-11"}, {"capturedAt": (self.now + timedelta(hours=1)).isoformat()}, {"capturedAt": "2026-10-12T06:50:00"}, {"schemaVersion": 99}):
            path.write_text(json.dumps({**original, **patch}))
            self.assertEqual(briefing.collect_sleep(self.now, snapshot_path=path)["status"], "unavailable")
        path.write_text(json.dumps(original)); path.chmod(0o644)
        self.assertEqual(briefing.collect_sleep(self.now, snapshot_path=path)["status"], "unavailable")

    def test_no_data_reader_and_unknown_reason_are_truthful(self):
        path = self.root / "julia.json"
        snapshot.write_snapshot(self.root, "julia", ("no_data", "no_sleep_data", None), self.now)
        self.assertEqual(briefing.collect_sleep(self.now, snapshot_path=path)["reason"], "no_sleep_data")
        snapshot.write_snapshot(self.root, "julia", ("unavailable", "private-error", None), self.now)
        self.assertNotIn("private", json.dumps(briefing.collect_sleep(self.now, snapshot_path=path)))

    def test_one_owner_failure_does_not_hide_other_owner_and_exit_is_nonzero(self):
        with mock.patch.object(snapshot.Path, "home", return_value=self.home), mock.patch.object(snapshot, "collect", side_effect=[("unavailable", "api_failed", None), ("no_data", "no_sleep_data", None)]) as collect, contextlib.redirect_stdout(io.StringIO()) as output:
            self.assertEqual(snapshot.main(), 1)
        self.assertEqual(collect.call_count, 2)
        self.assertIn("julia no_data", output.getvalue())
        self.assertEqual(json.loads((self.root / "julia.json").read_text())["status"], "no_data")

    def test_missing_credentials_make_no_api_calls(self):
        self.cache.unlink()
        with mock.patch.object(snapshot.Path, "home", return_value=self.home), mock.patch.object(snapshot, "collect") as collect, contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(snapshot.main(), 1)
        collect.assert_not_called()
        for owner in snapshot.OWNERS:
            self.assertEqual(json.loads((self.root / f"{owner}.json").read_text())["reason"], "credentials_unavailable")
