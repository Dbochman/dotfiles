#!/usr/bin/env python3

import importlib.util
import json
import os
import sqlite3
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
MODULE_PATH = REPO_ROOT / "openclaw" / "bin" / "usage-dashboard.py"
SPEC = importlib.util.spec_from_file_location("usage_dashboard", MODULE_PATH)
usage_dashboard = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(usage_dashboard)


class IMessageResponseLatencyTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.tempdir.name, "chat.db")
        self.connection = sqlite3.connect(self.db_path)
        self.connection.executescript(
            """
            CREATE TABLE chat (
                ROWID INTEGER PRIMARY KEY,
                style INTEGER
            );
            CREATE TABLE message (
                ROWID INTEGER PRIMARY KEY AUTOINCREMENT,
                guid TEXT NOT NULL,
                reply_to_guid TEXT,
                date INTEGER NOT NULL,
                is_from_me INTEGER NOT NULL,
                is_sent INTEGER DEFAULT 0,
                error INTEGER DEFAULT 0,
                is_finished INTEGER DEFAULT 1,
                service TEXT DEFAULT 'iMessage',
                item_type INTEGER DEFAULT 0,
                is_empty INTEGER DEFAULT 0,
                is_system_message INTEGER DEFAULT 0,
                associated_message_type INTEGER DEFAULT 0
            );
            CREATE TABLE chat_message_join (
                chat_id INTEGER NOT NULL,
                message_id INTEGER NOT NULL
            );
            """
        )
        self.connection.executemany(
            "INSERT INTO chat (ROWID, style) VALUES (?, ?)",
            [(1, 45), (2, 45), (3, 43), (4, 45)],
        )
        self.connection.commit()
        self.original_db = usage_dashboard.MESSAGES_DB
        self.original_config = usage_dashboard.OPENCLAW_CONFIG
        usage_dashboard.MESSAGES_DB = self.db_path
        self.now = datetime(2026, 6, 27, 20, 0, tzinfo=timezone.utc)

    def tearDown(self):
        usage_dashboard.MESSAGES_DB = self.original_db
        usage_dashboard.OPENCLAW_CONFIG = self.original_config
        self.connection.close()
        self.tempdir.cleanup()

    def raw_time(self, seconds_ago, nanoseconds=True):
        apple_epoch = datetime(2001, 1, 1, tzinfo=timezone.utc)
        value = (self.now - timedelta(seconds=seconds_ago) - apple_epoch).total_seconds()
        return int(value * 1_000_000_000) if nanoseconds else int(value)

    def add_message(
        self, chat_id, guid, seconds_ago, *, from_me=False,
        reply_to=None, nanoseconds=True, **overrides
    ):
        fields = {
            "is_sent": 1 if from_me else 0,
            "error": 0,
            "is_finished": 1,
            "service": "iMessage",
            "item_type": 0,
            "is_empty": 0,
            "is_system_message": 0,
            "associated_message_type": 0,
        }
        fields.update(overrides)
        cursor = self.connection.execute(
            """
            INSERT INTO message (
                guid, reply_to_guid, date, is_from_me, is_sent, error,
                is_finished, service, item_type, is_empty,
                is_system_message, associated_message_type
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                guid, reply_to, self.raw_time(seconds_ago, nanoseconds),
                1 if from_me else 0, fields["is_sent"], fields["error"],
                fields["is_finished"], fields["service"], fields["item_type"],
                fields["is_empty"], fields["is_system_message"],
                fields["associated_message_type"],
            ),
        )
        self.connection.execute(
            "INSERT INTO chat_message_join (chat_id, message_id) VALUES (?, ?)",
            (chat_id, cursor.lastrowid),
        )
        self.connection.commit()

    def test_response_summary_pairs_only_native_direct_replies(self):
        # Consecutive inbound messages are one turn, anchored to the last input.
        self.add_message(1, "inbound-a", 1010)
        self.add_message(1, "inbound-b", 990)
        self.add_message(1, "response-b", 950, from_me=True, reply_to="inbound-a")
        # A multipart continuation replies to the first outbound and is ignored.
        self.add_message(1, "response-b-part-2", 949, from_me=True, reply_to="response-b")

        self.add_message(1, "inbound-slow", 700)
        self.add_message(1, "response-slow", 520, from_me=True, reply_to="inbound-slow")
        # Seconds-scale timestamps are normalized alongside modern nanoseconds.
        self.add_message(2, "inbound-seconds", 400, nanoseconds=False)
        self.add_message(
            2, "response-seconds", 380, from_me=True,
            reply_to="inbound-seconds", nanoseconds=False,
        )
        self.add_message(1, "inbound-latest", 100)
        self.add_message(1, "response-latest", 90, from_me=True, reply_to="inbound-latest")

        # Proactive output and group traffic are not direct-response samples.
        self.add_message(1, "proactive", 800, from_me=True)
        self.add_message(3, "group-inbound", 300)
        self.add_message(3, "group-response", 290, from_me=True, reply_to="group-inbound")

        # One fresh unresolved turn and one stale unresolved turn.
        self.add_message(2, "pending", 300)
        self.add_message(4, "unmatched", 2000)

        summary = usage_dashboard._imessage_response_latency(now=self.now)

        self.assertTrue(summary["available"])
        self.assertEqual(summary["sample_count"], 4)
        self.assertEqual(summary["latest_ms"], 10000.0)
        self.assertEqual(summary["median_ms"], 30000.0)
        self.assertEqual(summary["p95_ms"], 180000.0)
        self.assertEqual(summary["over_120s_count"], 1)
        self.assertEqual(summary["pending_turn_count"], 1)
        self.assertEqual(summary["unmatched_turn_count"], 1)
        self.assertEqual(
            set(summary),
            {
                "available", "window_hours", "sample_count", "latest_ms",
                "median_ms", "p95_ms", "over_120s_count",
                "pending_turn_count", "unmatched_turn_count",
                "latest_received_at", "latest_response_at",
            },
        )

    def test_invalid_and_non_message_rows_are_excluded(self):
        exclusions = [
            {"service": "SMS"},
            {"item_type": 1},
            {"is_empty": 1},
            {"is_system_message": 1},
            {"associated_message_type": 2000},
            {"is_finished": 0},
        ]
        for index, overrides in enumerate(exclusions):
            inbound = f"excluded-inbound-{index}"
            self.add_message(1, inbound, 500 - index * 20, **overrides)
            self.add_message(
                1, f"excluded-response-{index}", 490 - index * 20,
                from_me=True, reply_to=inbound, **overrides
            )

        self.add_message(1, "failed-inbound", 200)
        self.add_message(
            1, "failed-response", 190, from_me=True,
            reply_to="failed-inbound", is_sent=0, error=1,
        )
        self.add_message(2, "stale-inbound", 150)
        self.add_message(2, "unlinked-output", 140, from_me=True)
        self.add_message(
            2, "too-late-linked-output", 130, from_me=True,
            reply_to="stale-inbound",
        )
        summary = usage_dashboard._imessage_response_latency(now=self.now)
        self.assertEqual(summary["sample_count"], 0)
        self.assertEqual(summary["pending_turn_count"], 1)
        self.assertEqual(summary["unmatched_turn_count"], 1)

    def test_p95_uses_nearest_rank_instead_of_max(self):
        values = list(range(1000, 21000, 1000))
        self.assertEqual(usage_dashboard._nearest_rank_percentile(values, 0.95), 19000)

    def test_missing_database_is_structured_and_private(self):
        usage_dashboard.MESSAGES_DB = os.path.join(self.tempdir.name, "missing.db")
        summary = usage_dashboard._imessage_response_latency(now=self.now)
        self.assertFalse(summary["available"])
        self.assertEqual(summary["sample_count"], 0)
        self.assertNotIn("guid", summary)
        self.assertNotIn("chat_id", summary)
        self.assertNotIn("recipient", summary)
        self.assertNotIn("text", summary)

    def test_runtime_behavior_uses_openclaw_configuration(self):
        config_path = os.path.join(self.tempdir.name, "openclaw.json")
        with open(config_path, "w", encoding="utf-8") as config_file:
            json.dump({
                "session": {"typingMode": "instant"},
                "channels": {"imessage": {"sendReadReceipts": False}},
                "private": {"token": "must-not-leak"},
            }, config_file)
        usage_dashboard.OPENCLAW_CONFIG = config_path

        behavior = usage_dashboard._imessage_runtime_behavior()

        self.assertEqual(behavior, {
            "available": True,
            "typing_mode": "instant",
            "send_read_receipts": False,
        })
        self.assertNotIn("private", behavior)
        self.assertNotIn("token", behavior)

    def test_runtime_behavior_honors_fallbacks_and_defaults(self):
        config_path = os.path.join(self.tempdir.name, "openclaw-defaults.json")
        with open(config_path, "w", encoding="utf-8") as config_file:
            json.dump({
                "agents": {"defaults": {"typingMode": "thinking"}},
                "channels": {"imessage": {}},
            }, config_file)
        usage_dashboard.OPENCLAW_CONFIG = config_path

        behavior = usage_dashboard._imessage_runtime_behavior()

        self.assertEqual(behavior, {
            "available": True,
            "typing_mode": "thinking",
            "send_read_receipts": True,
        })

    def test_dashboard_contains_latency_metrics_and_runtime_behavior(self):
        for metric_id in (
            "imessageResponseLatest",
            "imessageResponseWindow",
            "imessageResponseTail",
        ):
            self.assertIn(f'id="{metric_id}"', usage_dashboard.DASHBOARD_HTML)
            self.assertIn(f"'{metric_id}'", usage_dashboard.DASHBOARD_HTML)
        self.assertIn("Message behavior", usage_dashboard.DASHBOARD_HTML)
        self.assertIn("d.behavior || {}", usage_dashboard.DASHBOARD_HTML)
        self.assertNotIn('id="imessageBridge"', usage_dashboard.DASHBOARD_HTML)
        self.assertNotIn("'imessageBridge'", usage_dashboard.DASHBOARD_HTML)
        self.assertNotIn("i.typing_indicators === true", usage_dashboard.DASHBOARD_HTML)


if __name__ == "__main__":
    unittest.main()
