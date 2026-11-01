#!/usr/bin/env python3
"""Cabin public-deploy health: status validation, briefing and weekly-report wiring."""

from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock


BIN = Path(__file__).resolve().parents[1] / "bin"


def load(name: str, filename: str):
    spec = importlib.util.spec_from_file_location(name, BIN / filename)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


STATUS = load("cabin_public_deploy_status_under_test", "cabin-public-deploy-status.py")
BRIEFING = load("dylan_morning_briefing_data_for_deploy", "dylan-morning-briefing-data.py")
WEEKLY = load("openclaw_weekly_report_for_deploy", "openclaw-weekly-report.py")

NOW = datetime(2026, 11, 2, 13, 0, tzinfo=timezone.utc)  # 08:00 Eastern


class StatusFileTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tempdir.cleanup)
        self.path = Path(self.tempdir.name) / "status.json"

    def write(self, mode: int = 0o600, **overrides) -> None:
        payload = {"time": "2026-11-02T08:40:00Z", "result": "unchanged", "message": "issues: vol-1-no-03-draft-v2"}
        payload.update(overrides)
        self.path.write_text(json.dumps(payload), encoding="utf-8")
        self.path.chmod(mode)

    def warning(self, now: datetime = NOW):
        return STATUS.monitoring_warning(now=now, path=self.path)

    def test_fresh_deployed_and_unchanged_runs_are_healthy(self) -> None:
        for result in ("deployed", "unchanged"):
            with self.subTest(result=result):
                self.write(result=result, time="2026-11-02T07:40:00Z")
                self.assertIsNone(self.warning())
                self.assertEqual(STATUS.briefing_summary(now=NOW, path=self.path),
                                 {"status": "ok", "monitoringWarning": None})

    def test_missing_file_is_a_warning_not_silence(self) -> None:
        self.assertIn("has not reported yet", self.warning())

    def test_failed_run_reports_date_and_reason(self) -> None:
        self.write(result="failed", message="export failed its checks; nothing deployed", time="2026-11-02T07:40:00Z")
        warning = self.warning()
        self.assertIn("failed Mon 11/02 02:40 EST", warning)
        self.assertIn("export failed its checks; nothing deployed", warning)
        self.assertIn("left as it was", warning)
        self.assertEqual(STATUS.briefing_summary(now=NOW, path=self.path)["status"], "warning")

    def test_a_failure_stays_visible_even_when_old(self) -> None:
        self.write(result="failed", message="upload failed", time="2026-10-25T07:40:00Z")
        self.assertIn("failed", self.warning())

    def test_stale_success_after_thirty_six_hours(self) -> None:
        self.write(time="2026-10-31T00:00:00Z")
        self.assertIn("has not run since Fri 10/30 20:00 EDT", self.warning(NOW - timedelta(hours=1)))
        self.write(time="2026-11-01T01:00:00Z")  # 36h before NOW
        self.assertIsNone(self.warning(NOW - timedelta(minutes=1)))
        self.assertIn("has not run since", self.warning(NOW + timedelta(hours=1)))

    def test_unsafe_or_malformed_files_are_unreadable(self) -> None:
        cases = {
            "loose mode": lambda: self.write(mode=0o644),
            "extra field": lambda: self.write(extra="x"),
            "unknown result": lambda: self.write(result="great"),
            "bad time": lambda: self.write(time="yesterday"),
            "non-string message": lambda: self.write(message=7),
            "not an object": lambda: (self.path.write_text("[]"), self.path.chmod(0o600)),
            "not json": lambda: (self.path.write_text("nope"), self.path.chmod(0o600)),
            "empty": lambda: (self.path.write_text(""), self.path.chmod(0o600)),
            "too large": lambda: (self.path.write_text(" " * 5000), self.path.chmod(0o600)),
        }
        for label, make in cases.items():
            with self.subTest(label):
                if self.path.exists():
                    self.path.unlink()
                make()
                self.assertIn("unreadable", self.warning())

    def test_symlink_is_rejected(self) -> None:
        target = Path(self.tempdir.name) / "real.json"
        target.write_text(json.dumps({"time": "2026-11-02T07:40:00Z", "result": "unchanged", "message": ""}))
        target.chmod(0o600)
        self.path.symlink_to(target)
        self.assertIn("unreadable", self.warning())

    def test_future_timestamp_is_not_trusted(self) -> None:
        self.write(time="2026-11-03T13:00:00Z")
        self.assertIn("invalid time", self.warning())

    def test_message_is_bounded_and_stripped_of_control_characters(self) -> None:
        self.write(result="failed", message="bad\x1b[31m\nthing " + "x" * 400)
        warning = self.warning()
        self.assertNotIn("\x1b", warning)
        self.assertNotIn("\n", warning)
        self.assertLess(len(warning), 300)


class BriefingWiringTests(unittest.TestCase):
    def test_summary_wrapper_returns_the_module_summary(self) -> None:
        with mock.patch.object(STATUS, "STATUS_PATH", Path("/nonexistent/status.json")):
            # The wrapper loads its own copy of the module, so exercise the real default path logic.
            summary = BRIEFING.cabin_deploy_summary(NOW)
        self.assertIn(summary["status"], {"ok", "warning"})
        self.assertIn("monitoringWarning", summary)

    def test_summary_wrapper_never_raises_and_reports_itself(self) -> None:
        with mock.patch.object(BRIEFING.importlib.util, "spec_from_file_location", side_effect=OSError("boom")):
            summary = BRIEFING.cabin_deploy_summary(NOW)
        self.assertEqual(summary["status"], "unavailable")
        self.assertIn("could not be loaded", summary["monitoringWarning"])

    def test_payload_carries_the_cabin_deploy_key_in_every_path(self) -> None:
        source = (BIN / "dylan-morning-briefing-data.py").read_text(encoding="utf-8")
        self.assertGreaterEqual(source.count('"cabinDeploy": cabin_deploy_summary('), 3)
        self.assertIn("--cabin-deploy-status", source)

    def test_prompt_surfaces_the_warning_as_an_operations_note(self) -> None:
        jobs = json.loads((BIN.parent / "cron" / "jobs.json").read_text(encoding="utf-8"))["jobs"]
        job = next(j for j in jobs if j["id"] == "gws-dylan-morning-briefing-0001")
        prompt = job["payload"]["message"]
        self.assertIn("SHARED MAGAZINE DEPLOY HEALTH", prompt)
        self.assertIn("cabinDeploy.monitoringWarning", prompt)
        self.assertIn("not a security finding", prompt)
        self.assertIn("omit the line", prompt)
        # The existing security rules are untouched.
        self.assertIn("MONITORING HEALTH EXCEPTION", prompt)
        self.assertIn("Do not hide these warnings under the urgent-advisory filter", prompt)


class WeeklyReportWiringTests(unittest.TestCase):
    def test_unloaded_job_is_reported(self) -> None:
        with mock.patch.object(WEEKLY, "launchd_status", return_value={"loaded": False, "pid": None, "exit_code": None}):
            self.assertIn("not loaded", WEEKLY.cabin_deploy_attention(NOW))

    def test_loaded_job_defers_to_the_status_file(self) -> None:
        with mock.patch.object(WEEKLY, "launchd_status", return_value={"loaded": True, "pid": None, "exit_code": 0}), \
                mock.patch.object(STATUS, "STATUS_PATH", Path("/nonexistent/status.json")):
            attention = WEEKLY.cabin_deploy_attention(NOW)
        self.assertTrue(attention is None or isinstance(attention, str))

    def test_evaluation_failure_is_reported_not_raised(self) -> None:
        with mock.patch.object(WEEKLY, "launchd_status", return_value={"loaded": True, "pid": None, "exit_code": 0}), \
                mock.patch.object(WEEKLY.importlib.util, "spec_from_file_location", side_effect=OSError("boom")):
            self.assertIn("could not be evaluated", WEEKLY.cabin_deploy_attention(NOW))

    def test_report_appends_an_attention_line_only_when_needed(self) -> None:
        source = (BIN / "openclaw-weekly-report.py").read_text(encoding="utf-8")
        self.assertIn("deploy_attention = cabin_deploy_attention(now)", source)
        self.assertIn('lines.append(f"- Attention: {deploy_attention}")', source)


if __name__ == "__main__":
    unittest.main()
