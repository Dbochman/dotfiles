"""August schema compatibility without weakening durable triage validation."""

import contextlib
import json
import os
import sqlite3
import subprocess
import unittest
from pathlib import Path

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
        system = {"id": "managed-heartbeat", "agentId": "main", "declarationKey": "heartbeat:main",
                  "enabled": True, "schedule": {"kind": "every", "everyMs": 43200000},
                  "payload": {"kind": "heartbeat"}}
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
                        "if sys.argv[1]=='doctor': sys.exit(0)\n"
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
        staged = json.loads(live.read_text())["jobs"]
        self.assertIn(system, staged)
        self.assertNotIn("once", {job["id"] for job in staged})
        saved = subprocess.run(["bash", str(script), "save"], env=environment, capture_output=True, text=True)
        self.assertEqual(saved.returncode, 0, saved.stdout + saved.stderr)
        self.assertEqual({job["id"] for job in json.loads(canonical.read_text())["jobs"]}, set(existing.triage.OWNER_JOBS.values()))


if __name__ == "__main__":
    unittest.main()
