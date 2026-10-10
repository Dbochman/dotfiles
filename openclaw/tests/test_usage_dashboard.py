#!/usr/bin/env python3

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
MODULE_PATH = REPO_ROOT / "openclaw" / "bin" / "usage-dashboard.py"
SPEC = importlib.util.spec_from_file_location("usage_dashboard", MODULE_PATH)
usage_dashboard = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(usage_dashboard)


class CcusageIngestionTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.original_history_dir = usage_dashboard.HISTORY_DIR
        usage_dashboard.HISTORY_DIR = self.tempdir.name

    def tearDown(self):
        usage_dashboard.HISTORY_DIR = self.original_history_dir
        self.tempdir.cleanup()

    def write_usage(self, machine, daily):
        path = Path(self.tempdir.name) / f"ccusage-codex-{machine}.json"
        path.write_text(json.dumps({"daily": daily}), encoding="utf-8")

    def write_legacy_usage(self, machine, daily):
        path = Path(self.tempdir.name) / f"ccusage-{machine}.json"
        path.write_text(json.dumps({"daily": daily}), encoding="utf-8")

    def test_current_period_schema_merges_across_machines(self):
        self.write_usage("mini", [{
            "date": "2026-08-01",
            "totalTokens": 100,
            "inputTokens": 40,
            "outputTokens": 60,
            "reasoningOutputTokens": 5,
            "costUSD": 1.25,
        }])
        self.write_usage("macbook", [{
            "period": "2026-08-01",
            "totalTokens": 200,
            "inputTokens": 75,
            "outputTokens": 125,
            "reasoningOutputTokens": 7,
            "totalCost": 2.5,
        }])

        rows = usage_dashboard.load_ccusage()

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["date"], "2026-08-01")
        self.assertEqual(rows[0]["totalTokens"], 300)
        self.assertEqual(rows[0]["inputTokens"], 115)
        self.assertEqual(rows[0]["outputTokens"], 185)
        self.assertEqual(rows[0]["reasoningOutputTokens"], 12)
        self.assertEqual(rows[0]["totalCost"], 3.75)
        self.assertEqual(rows[0]["source"], "codex")
        self.assertEqual(set(rows[0]["machines"]), {"mini", "macbook"})

    def test_legacy_date_is_supported_and_invalid_rows_are_ignored(self):
        self.write_usage("legacy", [
            {"date": "2026-07-31", "totalTokens": 50},
            {"period": "2026-02-30", "totalTokens": 999},
            {"period": "not-a-date", "totalTokens": 999},
            "not-an-object",
        ])

        self.assertEqual(usage_dashboard.load_ccusage(), [{
            "date": "2026-07-31",
            "source": "codex",
            "totalTokens": 50,
            "inputTokens": 0,
            "outputTokens": 0,
            "cacheCreationTokens": 0,
            "cacheReadTokens": 0,
            "reasoningOutputTokens": 0,
            "totalCost": 0,
            "machines": ["legacy"],
        }])

    def test_retained_claude_history_is_not_mixed_into_codex_totals(self):
        self.write_legacy_usage("macbook", [
            {"date": "2026-07-31", "totalTokens": 999},
        ])

        self.assertEqual(usage_dashboard.load_ccusage(), [])


class GatewayUsageSchemaTests(unittest.TestCase):
    def test_current_daily_schema_gets_stable_token_and_cost_aliases(self):
        data = {
            "aggregates": {
                "daily": [{
                    "date": "2026-08-03",
                    "tokens": 109134,
                    "cost": 1.25,
                    "messages": 25,
                }],
            },
        }

        normalized = usage_dashboard._normalize_gateway_usage(data)
        row = normalized["aggregates"]["daily"][0]

        self.assertEqual(row["totalTokens"], 109134)
        self.assertEqual(row["totalCost"], 1.25)
        self.assertEqual(row["tokens"], 109134)
        self.assertEqual(row["messages"], 25)

    def test_legacy_daily_schema_remains_supported(self):
        data = {
            "aggregates": {
                "daily": [{
                    "date": "2026-08-03",
                    "totalTokens": 42,
                    "totalCost": 0.5,
                    "input": 20,
                    "output": 22,
                }],
            },
        }

        normalized = usage_dashboard._normalize_gateway_usage(data)
        row = normalized["aggregates"]["daily"][0]

        self.assertEqual(row["totalTokens"], 42)
        self.assertEqual(row["totalCost"], 0.5)
        self.assertEqual(row["input"], 20)
        self.assertEqual(row["output"], 22)

    def test_invalid_daily_numbers_fail_closed_to_zero(self):
        data = {
            "aggregates": {
                "daily": [{"date": "2026-08-03", "tokens": "many", "cost": float("inf")}],
            },
        }

        row = usage_dashboard._normalize_gateway_usage(data)["aggregates"]["daily"][0]

        self.assertEqual(row["totalTokens"], 0)
        self.assertEqual(row["totalCost"], 0)

    def test_dashboard_uses_all_session_and_date_scoped_aggregates(self):
        html = usage_dashboard.DASHBOARD_HTML

        self.assertIn("renderGauges(agg.utilization, agg, ccusage, gwData)", html)
        self.assertIn("buildCharts(snaps, agg, ccusage, gwData)", html)
        self.assertIn("const gatewayDaily = gwData", html)
        self.assertIn("const modelDaily = (agg.modelDaily || []).filter", html)
        self.assertIn("modelTotals[key] += m.tokens || 0", html)


class LaunchAgentStatusTests(unittest.TestCase):
    def test_running_service_marks_prior_exit_as_not_current(self):
        output = (
            "123\t143\tai.openclaw.running-service\n"
            "-\t1\tai.openclaw.idle-service\n"
        )
        completed = usage_dashboard.subprocess.CompletedProcess(
            ["launchctl", "list"], 0, stdout=output, stderr=""
        )
        with mock.patch.object(
            usage_dashboard.subprocess, "run", return_value=completed
        ), mock.patch.object(usage_dashboard, "_plist_info", return_value={}):
            services = {
                service["label"]: service
                for service in usage_dashboard.get_launchagent_status()
            }

        self.assertEqual(services["ai.openclaw.running-service"]["last_exit"], 143)
        self.assertFalse(
            services["ai.openclaw.running-service"]["exit_relevant"]
        )
        self.assertTrue(services["ai.openclaw.idle-service"]["exit_relevant"])

    def test_dashboard_renders_prior_running_exit_as_neutral_history(self):
        self.assertIn("const exitRelevant = s.exit_relevant !== false", usage_dashboard.DASHBOARD_HTML)
        self.assertIn("prior exit (", usage_dashboard.DASHBOARD_HTML)
        self.assertIn("badge-prior", usage_dashboard.DASHBOARD_HTML)


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
        self.original_ingress_db = usage_dashboard.INGRESS_DB
        usage_dashboard.INGRESS_DB = os.path.join(self.tempdir.name, "missing-ingress.db")
        self.original_config = usage_dashboard.OPENCLAW_CONFIG
        usage_dashboard.MESSAGES_DB = self.db_path
        self.now = datetime(2026, 6, 27, 20, 0, tzinfo=timezone.utc)

    def tearDown(self):
        usage_dashboard.MESSAGES_DB = self.original_db
        usage_dashboard.INGRESS_DB = self.original_ingress_db
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
                "latest_received_at", "latest_response_at", "ingress",
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

    def test_native_linked_type_100_reply_is_counted_but_reactions_are_not(self):
        self.add_message(1, "native-inbound", 100)
        self.add_message(1, "reaction", 99, from_me=True,
                         reply_to="native-inbound", associated_message_type=2000)
        self.add_message(1, "native-reply", 90, from_me=True,
                         reply_to="native-inbound", associated_message_type=100)
        self.add_message(2, "other-inbound", 80)
        self.add_message(2, "unlinked", 70, from_me=True, associated_message_type=100)
        result = usage_dashboard._imessage_response_latency(now=self.now)
        self.assertEqual(result["sample_count"], 1)
        self.assertEqual(result["latest_ms"], 10000)
        usage_dashboard.INGRESS_DB = os.path.join(self.tempdir.name, "ingress.db")
        with sqlite3.connect(usage_dashboard.INGRESS_DB) as ingress:
            ingress.execute("CREATE TABLE channel_ingress_events "
                            "(event_id TEXT, received_at INTEGER, channel_id TEXT)")
            ingress.execute("INSERT INTO channel_ingress_events VALUES (?, ?, 'imessage')",
                            ("native-inbound", int((self.now.timestamp() - 99) * 1000)))
        result = usage_dashboard._imessage_response_latency(now=self.now)
        self.assertEqual(result["ingress"]["latest_sent_to_ingress_ms"], 1000)
        self.assertEqual(result["ingress"]["latest_ingress_to_reply_ms"], 9000)

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


class IMessageIngressTimingTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.path = Path(self.tempdir.name) / "ingress.db"
        self.connection = sqlite3.connect(self.path)
        self.connection.execute(
            "CREATE TABLE channel_ingress_events (event_id TEXT, received_at INTEGER, "
            "channel_id TEXT, payload_json TEXT)"
        )
        self.patch = mock.patch.object(usage_dashboard, "INGRESS_DB", str(self.path))
        self.patch.start()
        self.now = datetime(2026, 10, 10, 14, tzinfo=timezone.utc)
        self.now_ms = self.now.timestamp() * 1000

    def tearDown(self):
        self.patch.stop()
        self.connection.close()
        self.tempdir.cleanup()

    def add(self, guid, age=60, delay=1000, channel="imessage", from_me=False):
        received = self.now_ms - age * 1000
        self.connection.execute(
            "INSERT INTO channel_ingress_events VALUES (?, ?, ?, ?)",
            (guid, received, channel, "PRIVATE MESSAGE MUST NOT LEAK"),
        )
        self.connection.commit()
        return {"guid": guid, "seconds": (received - delay) / 1000 - 978307200,
                "is_from_me": from_me}

    def summarize(self, rows, samples=None):
        return usage_dashboard._imessage_ingress_timing(rows, samples or [], self.now)

    def test_exact_match_separates_ingress_and_reply_without_private_data(self):
        row = self.add("private-guid", delay=245000)
        samples = [{"inbound_guid": "private-guid", "response_seconds":
                    self.now.timestamp() - 50 - 978307200}]
        result = self.summarize([row], samples)
        self.assertEqual(result["state"], "delayed")
        self.assertEqual(result["latest_sent_to_ingress_ms"], 245000)
        self.assertEqual(result["latest_ingress_to_reply_ms"], 10000)
        self.assertEqual(result["reply_sample_count"], 1)
        serialized = json.dumps(result)
        for private in ["private-guid", "PRIVATE MESSAGE", "payload", "chat_id"]:
            self.assertNotIn(private, serialized)

    def test_recent_stale_and_empty_are_not_equivalent_to_outage(self):
        recent = self.add("recent")
        stale = self.add("stale", age=901)
        self.assertEqual(self.summarize([recent])["state"], "recent")
        self.assertEqual(self.summarize([stale])["state"], "stale")
        unmatched = dict(recent, guid="unmatched")
        self.assertEqual(self.summarize([unmatched])["state"], "unverified")

    def test_wrong_channel_outbound_and_future_timestamps_are_excluded(self):
        rows = [self.add("wrong", channel="other"), self.add("outbound", from_me=True),
                self.add("future", age=-10), self.add("negative-delay", delay=-1000),
                self.add("expired", age=14401)]
        result = self.summarize(rows)
        self.assertEqual(result["sample_count"], 0)
        self.assertEqual(result["state"], "unverified")

    def test_ambiguous_duplicate_event_is_not_timing_evidence(self):
        row = self.add("duplicate")
        self.add("duplicate", age=50)
        self.assertEqual(self.summarize([row])["sample_count"], 0)

    def test_reply_before_ingress_is_excluded(self):
        row = self.add("reply")
        sample = {"inbound_guid": "reply", "response_seconds":
                  self.now.timestamp() - 100 - 978307200}
        self.assertEqual(self.summarize([row], [sample])["reply_sample_count"], 0)

    def test_capped_history_is_disclosed(self):
        row = self.add("oldest", age=90)
        self.connection.executemany(
            "INSERT INTO channel_ingress_events VALUES (?, ?, 'imessage', '')",
            [(str(index), self.now_ms - index) for index in range(1001)],
        )
        self.connection.commit()
        result = self.summarize([row])
        self.assertTrue(result["truncated"])
        self.assertEqual(result["state"], "unverified")

    def test_missing_or_incompatible_database_fails_closed_without_creation(self):
        row = self.add("row")
        absent = str(self.path.parent / "absent.db")
        with mock.patch.object(usage_dashboard, "INGRESS_DB", absent):
            self.assertEqual(self.summarize([row])["state"], "unavailable")
        self.assertFalse(Path(absent).exists())
        self.connection.execute("DROP TABLE channel_ingress_events")
        self.connection.commit()
        self.assertEqual(self.summarize([row])["state"], "unavailable")

    def test_healthy_components_do_not_claim_unverified_inbound_health(self):
        bridge = {"basic_features": True, "advanced_features": True, "v2_ready": True}
        with mock.patch.object(usage_dashboard, "_run_json_probe", return_value=bridge), \
                mock.patch.object(usage_dashboard, "_imessage_runtime_behavior", return_value={}), \
                mock.patch.object(usage_dashboard, "_latest_imessage_delivery", return_value={}), \
                mock.patch.object(usage_dashboard, "_gateway_is_live", return_value=True), \
                mock.patch.object(usage_dashboard, "_gateway_launchd_pid", return_value=123), \
                mock.patch.object(usage_dashboard, "_gateway_imsg_worker_count", return_value=1), \
                mock.patch.object(usage_dashboard, "_imessage_health_cache", {"data": None, "ts": 0}):
            for state, expected in [("recent", "healthy"), ("delayed", "degraded"),
                                    ("stale", "unknown"), ("unavailable", "unknown"),
                                    ("unverified", "unknown")]:
                usage_dashboard._imessage_health_cache["data"] = None
                with mock.patch.object(usage_dashboard, "_imessage_response_latency",
                                       return_value={"ingress": {"state": state}}):
                    result = usage_dashboard.fetch_imessage_health()
                self.assertEqual(result["component_status"], "healthy")
                self.assertEqual(result["status"], expected)
            usage_dashboard._imessage_health_cache["data"] = None
            with mock.patch.object(usage_dashboard, "_gateway_is_live", return_value=False), \
                    mock.patch.object(usage_dashboard, "_imessage_response_latency",
                                      return_value={"ingress": {"state": "recent"}}):
                self.assertEqual(usage_dashboard.fetch_imessage_health()["status"], "down")

    def test_ui_distinguishes_measurement_from_guarantees(self):
        html = usage_dashboard.DASHBOARD_HTML
        self.assertIn("Quiet chat is not an outage", html)
        self.assertIn("not Apple arrival time or model-only duration", html)
        for field in ["imessageInbound", "imessageIngressTiming", "imessageReplyTiming"]:
            self.assertIn('id="' + field + '"', html)

if __name__ == "__main__":
    unittest.main()
