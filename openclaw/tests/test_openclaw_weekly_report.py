#!/usr/bin/env python3
"""Weekly-report coverage for protected OpenTable refresh status."""

from __future__ import annotations

import importlib.util
import json
import os
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock


REPO_ROOT = Path(__file__).resolve().parents[2]
REPORT_PATH = REPO_ROOT / "openclaw" / "bin" / "openclaw-weekly-report.py"
SPEC = importlib.util.spec_from_file_location("openclaw_weekly_report", REPORT_PATH)
REPORT = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(REPORT)


class OpenTableWeeklyReportTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tempdir.cleanup)
        self.home = Path(self.tempdir.name)
        self.now = datetime(2026, 8, 19, 8, 31, tzinfo=timezone.utc)
        self.log_path = self.home / ".openclaw" / "logs" / "opentable-refresh.log"
        self.log_path.parent.mkdir(parents=True)
        self.log_path.write_text("refresh failed\n", encoding="utf-8")
        timestamp = self.now.timestamp()
        self.log_path.touch()
        os.utime(self.log_path, (timestamp, timestamp))

    def write_status(self, **overrides) -> None:
        status_path = (
            self.home / ".openclaw" / "run" / "opentable-refresh-status.json"
        )
        status_path.parent.mkdir(parents=True)
        payload = {
            "schema_version": 1,
            "attempt_kind": "scheduled",
            "attempt_number": 2,
            "outcome": "failed",
            "stage": "email_verification_email",
            "reason_code": "verification_email_unavailable",
            "started_at": "2026-08-19T08:30:00Z",
            "completed_at": "2026-08-19T08:31:00Z",
            "last_success_at": "2026-08-12T08:01:00Z",
        }
        payload.update(overrides)
        status_path.write_text(json.dumps(payload), encoding="utf-8")
        status_path.chmod(0o600)

    def test_failed_retry_reports_bounded_attempt_and_exact_stage(self) -> None:
        self.write_status()
        with (
            mock.patch.object(REPORT, "HOME", self.home),
            mock.patch.object(
                REPORT,
                "launchd_status",
                return_value={"loaded": True, "pid": None, "exit_code": 1},
            ),
        ):
            attention = REPORT.opentable_attention(self.now)

        self.assertEqual(
            attention,
            "OpenTable token refresh failed Wed 08/19 after scheduled attempt 2/2 "
            "at email_verification_email (verification_email_unavailable); "
            "it needs separate auth repair",
        )

    def test_successful_launchagent_suppresses_stale_failure_status(self) -> None:
        self.write_status()
        with (
            mock.patch.object(REPORT, "HOME", self.home),
            mock.patch.object(
                REPORT,
                "launchd_status",
                return_value={"loaded": True, "pid": None, "exit_code": 0},
            ),
        ):
            self.assertIsNone(REPORT.opentable_attention(self.now))

    def test_insecure_status_file_falls_back_without_reading_details(self) -> None:
        self.write_status()
        status_path = (
            self.home / ".openclaw" / "run" / "opentable-refresh-status.json"
        )
        status_path.chmod(0o644)
        with (
            mock.patch.object(REPORT, "HOME", self.home),
            mock.patch.object(
                REPORT,
                "launchd_status",
                return_value={"loaded": True, "pid": None, "exit_code": 1},
            ),
        ):
            attention = REPORT.opentable_attention(self.now)

        self.assertEqual(
            attention,
            "OpenTable token refresh failed Wed 08/19; it needs separate auth repair",
        )


if __name__ == "__main__":
    unittest.main()
