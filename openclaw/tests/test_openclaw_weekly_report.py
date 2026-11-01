#!/usr/bin/env python3
"""Weekly-report storage, coverage, local-time and recovery regressions."""

from __future__ import annotations

import importlib.util
import json
import os
import sqlite3
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock


REPO_ROOT = Path(__file__).resolve().parents[2]
REPORT_PATH = REPO_ROOT / "openclaw" / "bin" / "openclaw-weekly-report.py"
SPEC = importlib.util.spec_from_file_location("openclaw_weekly_report", REPORT_PATH)
REPORT = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(REPORT)


class CrisisModeWeeklyReportTests(unittest.TestCase):
    def scan(self):
        return {
            "type": "scan", "score": 100,
            "findings": [{"id": f"CHECK-{i}", "service": service, "status": "healthy",
                          "summary": "Healthy", "signals": []}
                         for i, service in enumerate(sorted(REPORT.CRISISMODE_SERVICES))],
            "triage": {"verdict": "healthy", "layers": [{"layer": "dns", "status": "pass"}]},
            "visibility": {"watching": [], "blocked": [], "invisible": []},
        }

    def test_old_excluded_ids_and_all_nonhealthy_statuses_remain_visible(self):
        for finding_id in ("PG-001", "DNS-002", "DNS-001"):
            for status in ("unhealthy", "critical", "warning", "unknown", "recovering", "degraded", None):
                with self.subTest(finding_id=finding_id, status=status):
                    scan = self.scan()
                    scan["findings"][0].update(id=finding_id, status=status, summary="Real failure")
                    score, attention, notes = REPORT.summarize_crisismode_scan(scan)
                    self.assertEqual(score, "100")
                    self.assertTrue(any("Real failure" in item for item in attention))

    def test_dns_split_is_explained_but_not_suppressed(self):
        scan = self.scan()
        dns = next(f for f in scan["findings"] if f["service"] == "dns (local-dns)")
        dns.update(status="recovering", summary="DNS degraded", signals=[
            {"source": "resolver_reachability", "status": "healthy"},
            {"source": "resolution_correctness", "status": "healthy"},
            {"source": "zone_consistency", "status": "critical", "detail":
             "Split-brain detected: resolvers returning different answers for the same hostname."},
        ])
        attention = REPORT.summarize_crisismode_scan(scan)[1]
        self.assertIn("cause unverified", attention[0])
        dns["signals"][0]["status"] = "warning"
        attention = REPORT.summarize_crisismode_scan(scan)[1]
        self.assertIn("resolver_reachability", attention[0])
        self.assertNotIn("checks passed", attention[0])
        dns["status"] = "healthy"
        self.assertTrue(REPORT.summarize_crisismode_scan(scan)[1])

    def test_partial_coverage_and_network_failure_are_not_clean(self):
        scan = self.scan()
        removed = scan["findings"].pop()
        scan["findings"][0]["bestEffort"] = True
        scan["triage"] = {"verdict": "network", "layers": [{"status": "fail"}]}
        scan["visibility"]["blocked"] = [{"label": "service"}]
        score, attention, notes = REPORT.summarize_crisismode_scan(scan)
        self.assertIn("coverage missing: " + removed["service"], attention)
        self.assertTrue(any("network triage: network" in a for a in attention))
        self.assertTrue(any("coverage blocked" in a for a in attention))
        self.assertIn("1 checks labeled best-effort by upstream", notes)
        del scan["triage"]
        del scan["visibility"]
        attention = REPORT.summarize_crisismode_scan(scan)[1]
        self.assertIn("network triage unavailable", attention)
        self.assertIn("scan coverage unavailable", attention)

    def test_empty_or_malformed_scans_are_unavailable(self):
        for changes in ({"score": True}, {"score": float("nan")}, {"score": 101},
                        {"findings": []}, {"findings": [None]}, {"findings": None}):
            with self.subTest(changes=changes):
                self.assertEqual(REPORT.summarize_crisismode_scan(self.scan() | changes),
                                 ("unavailable", [], []))

    def test_scan_uses_explicit_config_and_rejects_failed_process(self):
        result = mock.Mock(returncode=0, stdout=json.dumps(self.scan()))
        with mock.patch.object(REPORT.subprocess, "run", return_value=result) as run:
            self.assertEqual(REPORT.crisismode_summary(), ("100", [], []))
            self.assertEqual(run.call_args.args[0],
                             ["crisismode", "scan", "--config", str(REPORT.CRISISMODE_CONFIG), "--json"])
            self.assertEqual(run.call_args.kwargs["cwd"], REPORT.CRISISMODE_CONFIG.parent)
            self.assertEqual(run.call_args.kwargs["env"]["ANTHROPIC_API_KEY"], "")
            result.returncode = 1
            self.assertEqual(REPORT.crisismode_summary(), ("unavailable", [], []))


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
            "recovery not yet verified",
        )

    def test_launchagent_exit_zero_does_not_erase_protected_failure(self) -> None:
        self.write_status()
        with (
            mock.patch.object(REPORT, "HOME", self.home),
            mock.patch.object(
                REPORT,
                "launchd_status",
                return_value={"loaded": True, "pid": None, "exit_code": 0},
            ),
        ):
            self.assertIn("recovery not yet verified", REPORT.opentable_attention(self.now))

    def test_manual_success_supersedes_old_launchd_failure(self):
        self.write_status(outcome="success", attempt_kind="manual", stage="email_verification",
                          reason_code="completed", last_success_at="2026-08-19T08:31:00Z")
        timestamp = (self.now - timedelta(days=4)).timestamp()
        os.utime(self.log_path, (timestamp, timestamp))
        with mock.patch.object(REPORT, "HOME", self.home), mock.patch.object(
            REPORT, "launchd_status", return_value={"loaded": True, "pid": None, "exit_code": 1}
        ):
            self.assertIsNone(REPORT.opentable_attention(self.now))
            self.assertIn("stale", REPORT.opentable_attention(self.now + timedelta(days=9)))
            self.assertIn("invalid status time", REPORT.opentable_attention(self.now - timedelta(minutes=1)))

    def test_newer_launch_failure_is_not_hidden_by_success_receipt(self):
        self.write_status(outcome="success", last_success_at="2026-08-19T08:31:00Z")
        timestamp = (self.now + timedelta(hours=1)).timestamp()
        os.utime(self.log_path, (timestamp, timestamp))
        with mock.patch.object(REPORT, "HOME", self.home), mock.patch.object(
            REPORT, "launchd_status", return_value={"loaded": True, "pid": None, "exit_code": 1}
        ):
            self.assertIn("current authentication not verified", REPORT.opentable_attention(self.now + timedelta(hours=2)))

    def test_unloaded_job_and_missing_receipt_are_not_healthy(self):
        with mock.patch.object(REPORT, "HOME", self.home), mock.patch.object(
            REPORT, "launchd_status", return_value={"loaded": False, "pid": None, "exit_code": None}
        ):
            self.assertIn("not loaded", REPORT.opentable_attention(self.now))
        with mock.patch.object(REPORT, "HOME", self.home), mock.patch.object(
            REPORT, "launchd_status", return_value={"loaded": True, "pid": None, "exit_code": 0}
        ):
            self.assertIn("verification unavailable", REPORT.opentable_attention(self.now))

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
            "OpenTable refresh job last failed Wed 08/19; current authentication not verified",
        )


class ActivityReportTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.db = self.root / "agent.sqlite"
        self.legacy = self.root / "sessions"
        self.legacy.mkdir()
        self.start = datetime(2026, 10, 26, 4, tzinfo=timezone.utc)
        self.end = datetime(2026, 11, 1, 20, tzinfo=timezone.utc)
        for name, value in [("SESSION_DB", self.db), ("SESSIONS_DIR", self.legacy)]:
            patch = mock.patch.object(REPORT, name, value)
            patch.start()
            self.addCleanup(patch.stop)

    def event(self, event_id="one", **message):
        return {"type": "message", "id": event_id, "timestamp": "2026-10-27T12:00:00Z",
                "message": {"role": "assistant", "model": "model-a",
                            "content": [{"type": "toolCall"}, {"type": "text", "text": "private"}], **message}}

    def database(self, rows=(), archives=()):
        with sqlite3.connect(self.db) as conn:
            conn.executescript("""
                CREATE TABLE transcript_events (session_id TEXT, event_json TEXT, event_zstd BLOB);
                CREATE TABLE session_transcript_archives (session_id TEXT, encoding TEXT, archive_blob BLOB);
                CREATE TABLE session_transcript_cold_archives (session_id TEXT);
            """)
            conn.executemany("INSERT INTO transcript_events VALUES (?,?,?)", rows)
            conn.executemany("INSERT INTO session_transcript_archives VALUES (?,?,?)", archives)

    def collect(self):
        return REPORT.collect_session_activity(self.start, self.end)

    @unittest.skipIf(REPORT.zstd is None, "Python 3.14 zstd unavailable")
    def test_compressed_canonical_history_archives_and_mirrors(self):
        first = self.event()
        second = self.event("two", model="model-b", errorMessage="HTTP 429 rate limit",
                            content=[{"type": "tool_use"}])
        outside = {**self.event("outside"), "timestamp": self.end.isoformat()}
        self.database([
            ("session-a", json.dumps(first), None),
            ("session-a", "", REPORT.zstd.compress(json.dumps(second).encode())),
            ("session-a", json.dumps(self.event("mirror", model="delivery-mirror")), None),
            ("session-a", json.dumps(outside), None),
        ], [("session-a", "zstd", REPORT.zstd.compress((json.dumps(first) + "\n").encode()))])
        # An import copy must not be combined with the authoritative DB.
        (self.legacy / "copy.jsonl").write_text(json.dumps(first))
        before = self.db.read_bytes()
        result = self.collect()
        self.assertTrue(result["available"], result["error"])
        self.assertEqual((result["model_turns"], result["sessions"], result["tool_calls"], result["rate_limits"]), (2, 1, 2, 1))
        self.assertEqual(dict(result["models"]), {"model-a": 1, "model-b": 1})
        self.assertEqual(before, self.db.read_bytes())

    def test_missing_and_broken_sources_are_unavailable_not_zero(self):
        self.assertFalse(self.collect()["available"])
        self.db.write_text("not sqlite")
        (self.legacy / "stale.jsonl").write_text(json.dumps(self.event()))
        self.assertFalse(self.collect()["available"])

    def test_readable_empty_source_is_real_zero(self):
        self.database()
        result = self.collect()
        self.assertTrue(result["available"])
        self.assertEqual(result["model_turns"], 0)

    def test_legacy_overlap_deduplicated_and_migration_copies_ignored(self):
        for name in ["session.jsonl", "session.jsonl.reset.date"]:
            (self.legacy / name).write_text(json.dumps(self.event()))
        for name in ["copy.jsonl.migrated", "copy.jsonl.bak", "copy.trajectory.jsonl", "copy.jsonl.zst"]:
            (self.legacy / name).write_text("invalid")
        result = self.collect()
        self.assertTrue(result["available"])
        self.assertEqual(result["model_turns"], 1)

    def test_corrupt_record_after_valid_record_withholds_partial_count(self):
        self.database([("s", json.dumps(self.event()), None), ("s", "bad json", None)])
        result = self.collect()
        self.assertFalse(result["available"])
        self.assertEqual(REPORT.activity_metric(result, "model_turns"), "unavailable")

    def test_stale_legacy_files_do_not_become_current_zero_activity(self):
        event = self.event()
        event["timestamp"] = "2026-09-01T12:00:00Z"
        (self.legacy / "old.jsonl").write_text(json.dumps(event))
        self.assertFalse(self.collect()["available"])

    def test_cold_archives_require_coverage_not_silent_omission(self):
        self.database()
        with sqlite3.connect(self.db) as conn:
            conn.execute("INSERT INTO session_transcript_cold_archives VALUES ('archived')")
        self.assertFalse(self.collect()["available"])

    def test_invalid_assistant_timestamp_is_not_silently_zero(self):
        event = self.event()
        event["timestamp"] = "unknown"
        self.database([("s", json.dumps(event), None)])
        self.assertFalse(self.collect()["available"])

    @unittest.skipIf(REPORT.zstd is None, "Python 3.14 zstd unavailable")
    def test_compressed_size_limit_and_missing_codec_fail_closed(self):
        data = REPORT.zstd.compress(b"a" * 100)
        with mock.patch.object(REPORT, "MAX_TRANSCRIPT_BYTES", 50):
            with self.assertRaises(REPORT.ActivityUnavailable):
                REPORT.decode_transcript(data, True)
        with mock.patch.object(REPORT, "zstd", None):
            with self.assertRaises(REPORT.ActivityUnavailable):
                REPORT.decode_transcript(data, True)

    def test_dst_week_comparison_uses_same_local_weekday_and_time(self):
        for now in [datetime(2026, 11, 1, 20, tzinfo=timezone.utc),
                    datetime(2026, 3, 8, 19, tzinfo=timezone.utc)]:
            with self.subTest(now=now):
                (start, end), (previous_start, previous_end) = REPORT.week_windows(now)
                self.assertEqual(start.astimezone(REPORT.LOCAL_TZ).hour, 0)
                self.assertEqual(previous_start.astimezone(REPORT.LOCAL_TZ).hour, 0)
                self.assertEqual(previous_end.astimezone(REPORT.LOCAL_TZ).hour, 15)
                self.assertNotEqual((end - previous_end).total_seconds(), 7 * 86400)

    def test_cron_skipped_is_not_failure_and_missing_is_unknown(self):
        with mock.patch.object(REPORT, "CRON_DB", self.root / "missing.sqlite"):
            self.assertFalse(REPORT.collect_cron_runs(self.start, self.end)["available"])
        with sqlite3.connect(self.db) as conn:
            conn.execute("CREATE TABLE cron_run_logs (store_key TEXT, ts INTEGER, status TEXT, delivered INTEGER)")
            conn.executemany("INSERT INTO cron_run_logs VALUES (?,?,?,?)",
                             [(REPORT.CRON_STORE_KEY, int(self.start.timestamp()*1000), status, status == "ok")
                              for status in ["ok", "error", "skipped"]])
        with mock.patch.object(REPORT, "CRON_DB", self.db):
            result = REPORT.collect_cron_runs(self.start, self.end)
        self.assertEqual(result, {"completed": 1, "failed": 1, "skipped": 1, "delivered": 1, "available": True})

    def test_report_does_not_invent_activity_or_healthy_scan(self):
        with (mock.patch.object(REPORT, "collect_cron_runs", return_value={"available": False}),
              mock.patch.object(REPORT, "imessage_messages", return_value=None),
              mock.patch.object(REPORT, "crisismode_summary", return_value=("unavailable", [], [])),
              mock.patch.object(REPORT, "gateway_health", return_value="unreachable"),
              mock.patch.object(REPORT, "imessage_health", return_value="unavailable"),
              mock.patch.object(REPORT, "apfs_disk_usage", return_value="unavailable"),
              mock.patch.object(REPORT, "opentable_attention", return_value="verification unavailable")):
            text = REPORT.report(self.end)
        self.assertIn("Model turns: unavailable", text)
        self.assertIn("CrisisMode: unavailable (scan not verified)", text)
        self.assertNotIn("no actionable findings", text)
        self.assertNotIn("critical OpenClaw services healthy", text)
        self.assertIn("same local weekday/time", text)


if __name__ == "__main__":
    unittest.main()
