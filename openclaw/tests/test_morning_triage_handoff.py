#!/usr/bin/env python3
"""Durable owner- and run-bound triage handoff regressions."""

from __future__ import annotations

import contextlib
import hashlib
import io
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest import mock
from datetime import datetime, timedelta

from openclaw.tests import test_julia_morning_briefing_data as julia_tests
from openclaw.tests import test_dylan_morning_briefing_data as dylan_tests


triage = julia_tests.briefing.triage


class DurableTriageHandoffTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.home = Path(temporary.name)
        self.home.chmod(0o700)
        self.root = self.home / "handoffs"
        self.database = self.home / "state.sqlite"
        self.store = str(self.home / "cron/jobs.json")
        self.now = datetime(2026, 7, 13, 7, 0, tzinfo=triage.TIME_ZONE)
        self.started = int((self.now - timedelta(minutes=10)).timestamp() * 1000)
        self.source = self.home / "input.json"
        self.payload = julia_tests.JuliaMorningBriefingDataTests().version_two_handoff()
        self.payload["unreadAfter"] = [f"private-message-{index:04d}" for index in range(200)]
        self.payload["leftUnread"] = 200
        self.write_source()
        with contextlib.closing(sqlite3.connect(self.database)) as connection:
            connection.execute("CREATE TABLE cron_jobs (store_key TEXT, job_id TEXT, enabled INTEGER, running_at_ms INTEGER)")
            connection.execute("CREATE TABLE cron_run_logs (store_key TEXT, job_id TEXT, status TEXT, run_at_ms INTEGER, summary TEXT)")
            connection.execute("CREATE TABLE task_runs (runtime TEXT, source_id TEXT, status TEXT, started_at INTEGER, run_id TEXT, ended_at INTEGER)")
            for job_id in triage.OWNER_JOBS.values():
                connection.execute("INSERT INTO cron_jobs VALUES (?, ?, 1, ?)", (self.store, job_id, self.started))
                connection.execute("INSERT INTO task_runs VALUES ('cron', ?, 'running', ?, ?, NULL)",
                    (job_id, self.started, f"cron:{job_id}:{self.started}"))
            connection.commit()

    def write_source(self):
        self.source.write_text(json.dumps(self.payload))
        self.source.chmod(0o600)

    def publish(self, owner="julia"):
        return triage.publish_handoff_file(
            self.source, owner=owner, db_path=self.database, store_key=self.store,
            root=self.root, now=self.now,
        )

    def record(self, receipt, *, owner="julia", status="ok", started=None):
        with contextlib.closing(sqlite3.connect(self.database)) as connection:
            connection.execute("INSERT INTO cron_run_logs VALUES (?, ?, ?, ?, ?)", (
                self.store, triage.OWNER_JOBS[owner], status,
                self.started if started is None else started,
                json.dumps(receipt) if not isinstance(receipt, str) else receipt,
            ))
            connection.commit()

    def read(self, owner="julia"):
        return triage.load_triage_handoff(
            self.now, db_path=self.database, job_id=triage.OWNER_JOBS[owner],
            store_key=self.store, handoff_root=self.root,
        )

    def target(self, owner="julia"):
        return self.root / owner / f"{self.started}.json"

    def test_large_handoff_survives_cron_summary_limit_for_both_owners(self):
        self.assertGreater(self.source.stat().st_size, 2000)
        for owner in triage.OWNER_JOBS:
            with self.subTest(owner=owner):
                receipt = self.publish(owner)
                text = json.dumps(receipt, separators=(",", ":"))
                self.assertLess(len(text), 1000)
                self.assertNotIn("private-message", text)
                self.record(text[:2000], owner=owner)
                result, baseline = self.read(owner)
                self.assertEqual(result["status"], "ok")
                self.assertEqual(len(baseline), 200)
                self.assertNotIn("private-message", json.dumps(result))
                self.assertEqual(self.target(owner).stat().st_mode & 0o777, 0o600)
                self.assertEqual((self.root / owner).stat().st_mode & 0o777, 0o700)
                self.assertEqual(self.root.stat().st_mode & 0o777, 0o700)

    def test_publication_is_idempotent_but_never_overwrites_a_run(self):
        receipt = self.publish()
        original = self.target().read_bytes()
        self.assertEqual(self.publish(), receipt)
        self.payload["attention"][0]["reason"] = "Changed after publication"
        self.write_source()
        with self.assertRaisesRegex(ValueError, "handoff_already_published"):
            self.publish()
        self.assertEqual(self.target().read_bytes(), original)

    def test_publication_requires_current_active_exact_job_and_store(self):
        for running, enabled in ((None, 1), (self.started, 0), (self.started - 86400000, 1), (self.started + 3600000, 1)):
            with self.subTest(running=running, enabled=enabled):
                with contextlib.closing(sqlite3.connect(self.database)) as connection:
                    connection.execute("UPDATE cron_jobs SET running_at_ms=?, enabled=? WHERE job_id=?", (running, enabled, triage.OWNER_JOBS["julia"]))
                    connection.commit()
                with self.assertRaises(ValueError):
                    self.publish()
        self.assertFalse(self.root.exists())

    def test_active_run_rechecked_before_publication(self):
        with mock.patch.object(triage, "_active_run", side_effect=[self.started, self.started + 1]):
            with self.assertRaisesRegex(ValueError, "active_triage_run_changed"):
                self.publish()
        self.assertFalse(self.target().exists())

    def test_actual_start_not_scheduler_reservation_binds_receipt(self):
        with contextlib.closing(sqlite3.connect(self.database)) as connection:
            connection.execute("UPDATE cron_jobs SET running_at_ms=?", (self.started - 21,))
            connection.commit()
        receipt = self.publish()
        self.assertEqual(receipt["runAtMs"], self.started)
        self.record(receipt)
        self.assertEqual(self.read()[0]["status"], "ok")

    def test_missing_ambiguous_terminal_or_misbound_task_fails_closed(self):
        job_id = triage.OWNER_JOBS["julia"]
        active = ("cron", job_id, "running", self.started, f"cron:{job_id}:{self.started}", None)
        variants = [[], [active, active]]
        for index, value in ((0, "other"), (1, "other"), (2, "succeeded"),
                             (3, self.started - 1), (4, "wrong-run"), (5, self.started + 1)):
            changed = list(active)
            changed[index] = value
            variants.append([changed])
        for rows in variants:
            with self.subTest(rows=rows):
                with contextlib.closing(sqlite3.connect(self.database)) as connection:
                    connection.execute("DELETE FROM task_runs")
                    connection.executemany("INSERT INTO task_runs VALUES (?, ?, ?, ?, ?, ?)", rows)
                    connection.commit()
                with self.assertRaisesRegex(ValueError, "active_triage_run_missing"):
                    self.publish()
                self.assertFalse(self.root.exists())

    def test_completed_run_cannot_publish_using_stale_running_marker(self):
        self.record("HANDOFF_PUBLICATION_FAILED")
        with self.assertRaisesRegex(ValueError, "active_triage_run_missing"):
            self.publish()
        self.assertFalse(self.root.exists())

    def test_newer_active_run_prevents_reusing_earlier_same_day_success(self):
        self.record(self.publish())
        with contextlib.closing(sqlite3.connect(self.database)) as connection:
            connection.execute("UPDATE cron_jobs SET running_at_ms=?", (self.started + 1,))
            connection.commit()
        result, baseline = self.read()
        self.assertEqual(result["reason"], "triage_run_in_progress")
        self.assertIsNone(baseline)

    def test_missing_corrupt_or_altered_file_rejects_receipt(self):
        receipt = self.publish()
        self.record(receipt)
        original = self.target().read_bytes()
        for content in (b"{}", original + b" ", None):
            with self.subTest(content_present=content is not None):
                if content is None:
                    self.target().unlink()
                else:
                    self.target().write_bytes(content)
                result, baseline = self.read()
                self.assertEqual(result["reason"], "invalid_handoff")
                self.assertIsNone(baseline)

    def test_receipt_binds_owner_job_store_date_run_and_digest(self):
        receipt = self.publish()
        for field, value in (
            ("owner", "dylan"), ("jobId", triage.OWNER_JOBS["dylan"]),
            ("storeKey", "/other/jobs.json"), ("date", "2026-07-12"),
            ("runAtMs", self.started + 1), ("sha256", "f" * 64),
            ("handoffReceiptVersion", True),
        ):
            with self.subTest(field=field), self.assertRaises(ValueError):
                triage._run_payload(json.dumps({**receipt, field: value}), self.now,
                    run_at_ms=self.started, job_id=triage.OWNER_JOBS["julia"],
                    store_key=self.store, root=self.root)

    def test_newest_failed_missing_or_invalid_run_never_uses_older_success(self):
        receipt = self.publish()
        self.record(receipt)
        for offset, status, summary in ((1, "error", receipt), (2, "ok", "{}"), (3, "ok", receipt)):
            self.record(summary, status=status, started=self.started + offset)
            result, baseline = self.read()
            self.assertEqual(result["status"], "unavailable")
            self.assertIsNone(baseline)

    def test_foreign_owner_file_never_substitutes(self):
        receipt = self.publish("julia")
        self.record(receipt, owner="dylan")
        result, baseline = self.read("dylan")
        self.assertEqual(result["status"], "unavailable")
        self.assertIsNone(baseline)

    def test_unsafe_file_modes_symlinks_hardlinks_and_directories_fail(self):
        receipt = self.publish()
        self.record(receipt)
        self.target().chmod(0o644)
        self.assertEqual(self.read()[0]["status"], "unavailable")
        self.target().chmod(0o600)
        linked = self.home / "linked"
        os.link(self.target(), linked)
        self.assertEqual(self.read()[0]["status"], "unavailable")
        linked.unlink()
        original = self.target().read_bytes()
        self.target().unlink()
        self.target().symlink_to(self.source)
        self.assertEqual(self.read()[0]["status"], "unavailable")
        self.target().unlink()
        self.target().write_bytes(original)
        self.target().chmod(0o600)
        (self.root / "julia").chmod(0o755)
        self.assertEqual(self.read()[0]["status"], "unavailable")

    def test_publisher_rejects_symlink_root_and_unprotected_input(self):
        self.source.chmod(0o644)
        with self.assertRaises(ValueError):
            self.publish()
        self.source.chmod(0o600)
        alternate = self.home / "alternate"
        alternate.mkdir(mode=0o700)
        self.root.symlink_to(alternate, target_is_directory=True)
        with self.assertRaises(ValueError):
            self.publish()
        self.assertEqual(list(alternate.iterdir()), [])

    def test_schema_validation_still_applies_even_with_matching_digest(self):
        receipt = self.publish()
        envelope = json.loads(self.target().read_bytes())
        envelope["payload"]["leftUnread"] = 999
        content = json.dumps(envelope).encode()
        self.target().write_bytes(content)
        receipt["sha256"] = hashlib.sha256(content).hexdigest()
        self.record(receipt)
        self.assertEqual(self.read()[0]["reason"], "invalid_handoff")

    def test_previous_day_file_supplies_reminders_without_cross_owner_fallback(self):
        current = self.publish()
        old_now = self.now
        old_start = self.started
        self.now -= timedelta(days=1)
        self.started -= 86400000
        self.payload["date"] = self.now.date().isoformat()
        self.write_source()
        with contextlib.closing(sqlite3.connect(self.database)) as connection:
            connection.execute("UPDATE cron_jobs SET running_at_ms=?", (self.started,))
            for job_id in triage.OWNER_JOBS.values():
                connection.execute("UPDATE task_runs SET started_at=?, run_id=? WHERE source_id=?",
                    (self.started, f"cron:{job_id}:{self.started}", job_id))
            connection.commit()
        previous = self.publish()
        self.record(previous)
        self.now, self.started = old_now, old_start
        self.record(current)
        result, _ = self.read()
        self.assertEqual(result["actionReview"][0]["group"], "decision_needed")

    def test_partial_primary_and_optional_review_semantics_are_preserved(self):
        self.payload["review"].update(status="unavailable", inboxTotal=None,
            inboxUnread=None, actionMessageCount=None, inventoryComplete=False, errors=["timeout"])
        self.payload["actionReview"] = []
        self.payload.update(status="partial", cleanupVerified=False, errors=["cleanup_readback_failed"])
        self.write_source()
        self.record(self.publish())
        result, baseline = self.read()
        self.assertEqual(result["handoffStatus"], "partial")
        self.assertEqual(len(baseline), 200)
        self.assertEqual(result["outcomes"]["status"], "unavailable")

    def test_publication_failure_never_emits_success_receipt(self):
        with mock.patch.object(triage.os, "link", side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                self.publish()
        self.assertFalse(self.target().exists())
        self.assertEqual(list((self.root / "julia").iterdir()), [])

    def test_both_cli_modes_never_collect_mail_or_other_sources(self):
        for module in (julia_tests.briefing, dylan_tests.briefing):
            with self.subTest(module=module.__name__), mock.patch.object(module, "collect_data") as collect:
                with mock.patch.object(module.triage, "publish_handoff_file", return_value={"receipt": "safe"}) as publish:
                    with contextlib.redirect_stdout(io.StringIO()) as output:
                        self.assertEqual(module.main(["--publish-handoff", str(self.source)]), 0)
                    self.assertEqual(json.loads(output.getvalue()), {"receipt": "safe"})
                    self.assertEqual(publish.call_args.kwargs["owner"], "julia" if module is julia_tests.briefing else "dylan")
                with mock.patch.object(module.triage, "load_triage_handoff", return_value=({"status": "unavailable", "reason": "invalid_handoff"}, None)):
                    with contextlib.redirect_stdout(io.StringIO()) as output:
                        self.assertEqual(module.main(["--triage-status"]), 1)
                    self.assertNotIn("private-message", output.getvalue())
                collect.assert_not_called()


if __name__ == "__main__":
    unittest.main()
