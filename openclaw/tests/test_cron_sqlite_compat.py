"""August schema compatibility without weakening durable triage validation."""

import contextlib
import json
import os
import sqlite3
import subprocess
import unittest
from pathlib import Path
from unittest import mock

from openclaw.tests import test_morning_triage_handoff as existing
from openclaw_cron_sqlite import connect, is_system_job


class CompactCronStoreTests(unittest.TestCase):
    def setUp(self):
        self.fixture = existing.DurableTriageHandoffTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        with sqlite3.connect(self.fixture.database) as database:
            database.execute("DROP TABLE cron_jobs")
            database.execute("CREATE TABLE cron_jobs (store_key TEXT, job_id TEXT, enabled INTEGER, job_json TEXT, state_json TEXT)")
            database.execute("DROP TABLE cron_run_logs")
            database.execute("CREATE TABLE cron_run_receipts (receipt_id TEXT, store_key TEXT, job_id TEXT, request_run_id TEXT, status TEXT, started_at_ms INTEGER, finished_at_ms INTEGER)")
            for column in ("detail_json TEXT", "last_event_at INTEGER", "created_at INTEGER", "terminal_summary TEXT", "child_session_key TEXT"):
                database.execute("ALTER TABLE task_runs ADD COLUMN " + column)
            for owner, job_id in existing.triage.OWNER_JOBS.items():
                database.execute("INSERT INTO cron_run_receipts VALUES (?, ?, ?, NULL, 'running', ?, NULL)",
                                 (f"receipt-{owner}", self.fixture.store, job_id, self.fixture.started))
                database.execute("UPDATE task_runs SET run_id=?, detail_json=? WHERE source_id=?",
                                 (f"cron:{job_id}:{self.fixture.started}:receipt-{owner}",
                                  json.dumps({"storeKey": self.fixture.store}), job_id))
                database.execute("INSERT INTO cron_jobs VALUES (?, ?, 1, ?, ?)", (
                    self.fixture.store, job_id,
                    json.dumps({"id": job_id, "schedule": {"kind": "cron", "expr": "0 7 * * *"}}),
                    json.dumps({"runningAtMs": self.fixture.started, "nextRunAtMs": self.fixture.started + 86400000}),
                ))

    def finish(self, receipt, *, owner="julia", store=None, status="ok", kind="cron-run", terminal=True):
        detail = {
            "kind": kind, "storeKey": store or self.fixture.store, "status": status,
            "runAtMs": self.fixture.started, "summary": json.dumps(receipt),
            "delivered": True, "durationMs": 1000, "usage": {"total_tokens": 10},
        }
        with sqlite3.connect(self.fixture.database) as database:
            database.execute(
                "INSERT INTO task_runs (runtime, source_id, status, started_at, run_id, ended_at, detail_json, created_at, last_event_at) "
                "VALUES ('cron', ?, 'succeeded', ?, 'completed', ?, ?, ?, ?)",
                (existing.triage.OWNER_JOBS[owner], self.fixture.started,
                 self.fixture.started + 1000 if terminal else None, json.dumps(detail),
                 self.fixture.started, self.fixture.started + 1000),
            )

    def test_both_owners_keep_verified_durable_handoffs(self):
        for owner in existing.triage.OWNER_JOBS:
            with self.subTest(owner=owner):
                receipt = self.fixture.publish(owner)
                self.finish(receipt, owner=owner)
                result, unread = self.fixture.read(owner)
                self.assertEqual(result["status"], "ok")
                self.assertEqual(len(unread), 200)

    def test_finished_run_blocks_new_publication(self):
        self.finish({})
        with self.assertRaisesRegex(ValueError, "active_triage_run_missing"):
            self.fixture.publish()

    def activate_receipt_backed_runs(self):
        with sqlite3.connect(self.fixture.database) as database:
            database.execute("DELETE FROM task_runs WHERE status='running'")
            for owner, job_id in existing.triage.OWNER_JOBS.items():
                database.execute(
                    "UPDATE cron_jobs SET state_json=json_set(state_json, '$.runningReceiptId', ?) WHERE job_id=?",
                    (f"receipt-{owner}", job_id),
                )

    def test_receipt_backed_runs_publish_before_task_history_exists(self):
        self.activate_receipt_backed_runs()
        for owner in existing.triage.OWNER_JOBS:
            with self.subTest(owner=owner):
                receipt = self.fixture.publish(owner)
                self.assertEqual(receipt["runAtMs"], self.fixture.started)
                self.assertEqual(self.fixture.publish(owner), receipt)
                self.finish(receipt, owner=owner)
                result, unread = self.fixture.read(owner)
                self.assertEqual(result["status"], "ok")
                self.assertEqual(len(unread), 200)

    def test_receipt_backed_runs_fail_closed_on_invalid_authority(self):
        self.activate_receipt_backed_runs()
        mutations = (
            "DELETE FROM cron_jobs",
            "UPDATE cron_jobs SET enabled=0",
            "UPDATE cron_jobs SET state_json='{}'",
            "UPDATE cron_jobs SET state_json=json_remove(state_json, '$.runningAtMs')",
            "UPDATE cron_jobs SET state_json=json_remove(state_json, '$.runningReceiptId')",
            "UPDATE cron_jobs SET state_json=json_set(state_json, '$.runningReceiptId', NULL)",
            "UPDATE cron_jobs SET state_json=json_set(state_json, '$.runningReceiptId', '')",
            "UPDATE cron_jobs SET state_json=json_set(state_json, '$.runningReceiptId', 1)",
            "UPDATE cron_jobs SET state_json=json_set(state_json, '$.runningReceiptId', 'wrong')",
            "UPDATE cron_jobs SET state_json=json_set(state_json, '$.queuedAtMs', 1)",
            "DELETE FROM cron_run_receipts",
            "INSERT INTO cron_run_receipts SELECT * FROM cron_run_receipts",
            "UPDATE cron_run_receipts SET store_key='other'",
            "UPDATE cron_run_receipts SET job_id='other'",
            "UPDATE cron_run_receipts SET receipt_id='other'",
            "UPDATE cron_run_receipts SET status='ok'",
            "UPDATE cron_run_receipts SET finished_at_ms=1",
            "UPDATE cron_run_receipts SET started_at_ms=started_at_ms-1",
        )
        original = self.fixture.database.read_bytes()
        for mutation in mutations:
            with self.subTest(mutation=mutation):
                self.fixture.database.write_bytes(original)
                with sqlite3.connect(self.fixture.database) as database:
                    database.execute(mutation)
                with self.assertRaisesRegex(ValueError, "active_triage_run_missing"):
                    self.fixture.publish()
                self.assertFalse(self.fixture.target().exists())

    def test_receipt_backed_runs_reject_stale_or_future_start(self):
        self.activate_receipt_backed_runs()
        original = self.fixture.database.read_bytes()
        for offset in (-86400000, -1200001, 3600000):
            with self.subTest(offset=offset):
                self.fixture.database.write_bytes(original)
                started = self.fixture.started + offset
                with sqlite3.connect(self.fixture.database) as database:
                    database.execute("UPDATE cron_jobs SET state_json=json_set(state_json, '$.runningAtMs', ?)", (started,))
                    database.execute("UPDATE cron_run_receipts SET started_at_ms=?", (started,))
                with self.assertRaisesRegex(ValueError, "active_triage_run_invalid"):
                    self.fixture.publish()

    def test_receipt_backed_run_cannot_publish_after_completion(self):
        self.activate_receipt_backed_runs()
        self.finish({})
        with self.assertRaisesRegex(ValueError, "active_triage_run_missing"):
            self.fixture.publish()

    def test_receipt_backed_authority_is_rechecked_before_write(self):
        self.activate_receipt_backed_runs()
        check_active = existing.triage._active_run
        calls = []

        def finish_before_recheck(*args):
            calls.append(True)
            if len(calls) == 2:
                with sqlite3.connect(self.fixture.database) as database:
                    database.execute("UPDATE cron_run_receipts SET status='ok', finished_at_ms=started_at_ms+1")
            return check_active(*args)

        with mock.patch.object(existing.triage, "_active_run", side_effect=finish_before_recheck):
            with self.assertRaisesRegex(ValueError, "active_triage_run_missing"):
                self.fixture.publish()
        self.assertFalse(self.fixture.target().exists())

    def test_receipt_backed_running_job_blocks_older_success(self):
        self.activate_receipt_backed_runs()
        receipt = self.fixture.publish()
        self.finish(receipt)
        with sqlite3.connect(self.fixture.database) as database:
            database.execute("UPDATE cron_jobs SET state_json=json_set(state_json, '$.runningAtMs', ?)", (self.fixture.started + 1,))
        result, baseline = self.fixture.read()
        self.assertEqual(result["reason"], "triage_run_in_progress")
        self.assertIsNone(baseline)

    def test_receipt_backed_run_still_checks_existing_task_identity(self):
        with sqlite3.connect(self.fixture.database) as database:
            for owner, job_id in existing.triage.OWNER_JOBS.items():
                database.execute(
                    "UPDATE cron_jobs SET state_json=json_set(state_json, '$.runningReceiptId', ?) WHERE job_id=?",
                    (f"receipt-{owner}", job_id),
                )
            database.execute("UPDATE task_runs SET run_id=run_id || ':wrong'")
        with self.assertRaisesRegex(ValueError, "active_triage_run_missing"):
            self.fixture.publish()

    def test_public_run_id_must_match_receipt(self):
        with sqlite3.connect(self.fixture.database) as database:
            database.execute("UPDATE cron_run_receipts SET request_run_id='manual-run'")
            database.execute("UPDATE task_runs SET run_id=run_id || ':manual-run'")
        self.fixture.publish()

    def test_compact_publication_requires_exact_live_receipt(self):
        mutations = (
            "DELETE FROM cron_run_receipts",
            "UPDATE cron_run_receipts SET store_key='other'",
            "UPDATE cron_run_receipts SET job_id='other'",
            "UPDATE cron_run_receipts SET status='finished'",
            "UPDATE cron_run_receipts SET finished_at_ms=1",
            "UPDATE cron_run_receipts SET started_at_ms=started_at_ms-1",
            "UPDATE cron_run_receipts SET request_run_id='wrong'",
            "INSERT INTO cron_run_receipts SELECT * FROM cron_run_receipts",
            "UPDATE task_runs SET detail_json='{}'",
            "UPDATE task_runs SET detail_json='[]'",
            "UPDATE task_runs SET detail_json='{\"storeKey\":\"other\"}'",
            "UPDATE task_runs SET run_id=run_id || ':unverified'",
            "UPDATE task_runs SET run_id=substr(run_id, 1, instr(run_id, ':receipt-')-1)",
        )
        original = self.fixture.database.read_bytes()
        for mutation in mutations:
            with self.subTest(mutation=mutation):
                self.fixture.database.write_bytes(original)
                with sqlite3.connect(self.fixture.database) as database:
                    database.execute(mutation)
                with self.assertRaisesRegex(ValueError, "active_triage_run_missing"):
                    self.fixture.publish()

    def test_wrong_store_cannot_verify_a_receipt(self):
        receipt = self.fixture.publish()
        self.finish(receipt, store="/other/cron/jobs.json")
        result, unread = self.fixture.read()
        self.assertEqual(result["status"], "unavailable")
        self.assertIsNone(unread)

    def test_failed_run_remains_failed(self):
        receipt = self.fixture.publish()
        self.finish(receipt, status="error")
        result, unread = self.fixture.read()
        self.assertEqual(result["reason"], "triage_run_failed")
        self.assertIsNone(unread)

    def test_nonterminal_or_nonhistory_task_is_not_a_receipt(self):
        self.finish({}, terminal=False)
        self.finish({}, kind="quiet-trigger")
        with contextlib.closing(connect(self.fixture.database)) as database:
            self.assertEqual(database.execute("SELECT COUNT(*) FROM cron_run_logs").fetchone()[0], 0)

    def test_views_project_usage_and_schedule_without_durable_writes(self):
        self.finish({})
        before = self.fixture.database.read_bytes()
        with contextlib.closing(connect(self.fixture.database)) as database:
            self.assertEqual(database.execute("SELECT total_tokens, delivered, status FROM cron_run_logs").fetchone(), (10, 1, "ok"))
            self.assertEqual(database.execute("SELECT next_run_at_ms FROM cron_jobs LIMIT 1").fetchone()[0], self.fixture.started + 86400000)
            with self.assertRaises(sqlite3.OperationalError):
                database.execute("UPDATE main.cron_jobs SET enabled=0")
        self.assertEqual(before, self.fixture.database.read_bytes())
        with sqlite3.connect(self.fixture.database) as database:
            self.assertIsNone(database.execute("SELECT 1 FROM sqlite_master WHERE name='cron_run_logs'").fetchone())

    def test_system_jobs_require_matching_declarations(self):
        job = {"agentId": "main", "declarationKey": "heartbeat:main", "payload": {"kind": "heartbeat"}}
        self.assertTrue(is_system_job(job))
        self.assertFalse(is_system_job({**job, "agentId": "other"}))
        self.assertFalse(is_system_job({**job, "payload": {"kind": "agentTurn"}}))
        self.assertFalse(is_system_job({"name": "heartbeat-main"}))

    def test_migrated_skill_review_requires_full_system_contract(self):
        job = {"agentId": "main", "declarationKey": "skill-collection-review:main",
               "name": "skill-collection-review-main", "sessionTarget": "isolated",
               "payload": {"kind": "agentTurn"}, "delivery": {"mode": "none"}}
        self.assertTrue(is_system_job(job))
        for change in ({"agentId": "other"}, {"declarationKey": None},
                       {"name": "ordinary-review"}, {"sessionTarget": "main"},
                       {"delivery": {"mode": "announce"}}):
            with self.subTest(change=change):
                self.assertFalse(is_system_job({**job, **change}))

    def test_sync_preserves_system_jobs_and_completed_tombstones(self):
        home = self.fixture.home
        live = Path(self.fixture.store)
        live.parent.mkdir(parents=True)
        config = home / ".openclaw"
        config.mkdir()
        cache = config / ".secrets-cache"
        cache.write_text("DYLAN_EMAIL=dylan@example.invalid\nJULIA_EMAIL=julia@example.invalid\nHOUSEHOLD_CHAT_ID=3\nJULIA_CHAT_ID=1\nDYLAN_CHAT_ID=2\n")
        cache.chmod(0o600)
        self.finish({})
        system = {"id": "managed-review", "agentId": "main", "declarationKey": "skill-collection-review:main",
                  "name": "skill-collection-review-main", "sessionTarget": "isolated",
                  "enabled": True, "schedule": {"kind": "every", "everyMs": 604800000},
                  "payload": {"kind": "agentTurn"}, "delivery": {"mode": "none"}}
        with sqlite3.connect(self.fixture.database) as database:
            database.execute("ALTER TABLE cron_jobs ADD COLUMN sort_order INTEGER DEFAULT 0")
            database.execute("ALTER TABLE cron_jobs ADD COLUMN updated_at INTEGER DEFAULT 0")
            definitions = [json.loads(row[0]) for row in database.execute("SELECT job_json FROM cron_jobs")]
            database.execute("UPDATE task_runs SET source_id='once' WHERE run_id='completed'")
            database.execute("INSERT INTO cron_jobs (store_key, job_id, enabled, job_json, state_json) VALUES (?, ?, 1, ?, '{}')",
                             (self.fixture.store, system["id"], json.dumps(system)))
        definitions.append({"id": "once", "deleteAfterRun": True,
                            "schedule": {"kind": "at", "at": "2020-01-01T00:00:00Z"}})
        canonical = home / "jobs.json"
        canonical.write_text(json.dumps({"version": 1, "jobs": definitions}))
        binaries = home / "fake-bin"
        binaries.mkdir()
        fake = binaries / "openclaw"
        fake.write_text("#!/usr/bin/python3\nimport json,os,sqlite3,sys\n"
                        "if sys.argv[1]=='doctor': sys.exit(78)\n"
                        "if sys.argv[1:3]!=['cron','list']: sys.exit(99)\n"
                        "with sqlite3.connect(os.environ['SQLITE_DB']) as db:\n"
                        "    jobs=[json.loads(row[0]) for row in db.execute('SELECT job_json FROM cron_jobs')]\n"
                        "print(json.dumps({'jobs':jobs}))\n")
        fake.chmod(0o700)
        environment = {**os.environ, "HOME": str(home), "DOTFILES_JOBS": str(canonical),
                       "LIVE_JOBS": str(live), "SQLITE_DB": str(self.fixture.database),
                       "OPENCLAW_SECRETS_CACHE": str(cache), "PATH": str(binaries) + ":" + os.environ["PATH"]}
        script = Path(__file__).resolve().parents[1] / "sync-cron-jobs.sh"
        deployed = subprocess.run(["bash", str(script), "deploy"], env=environment, capture_output=True, text=True)
        self.assertEqual(deployed.returncode, 0, deployed.stdout + deployed.stderr)
        self.assertFalse(live.exists(), "SQLite deployment must not recreate the legacy bridge")
        with sqlite3.connect(self.fixture.database) as database:
            deployed_jobs = [json.loads(row[0]) for row in database.execute("SELECT job_json FROM cron_jobs")]
        self.assertIn(system, deployed_jobs)
        self.assertNotIn("once", {job["id"] for job in deployed_jobs})
        self.assertIn("completed one-shots skipped: 1", deployed.stdout)
        saved = subprocess.run(["bash", str(script), "save"], env=environment, capture_output=True, text=True)
        self.assertEqual(saved.returncode, 0, saved.stdout + saved.stderr)
        self.assertEqual({job["id"] for job in json.loads(canonical.read_text())["jobs"]}, set(existing.triage.OWNER_JOBS.values()))


if __name__ == "__main__":
    unittest.main()
