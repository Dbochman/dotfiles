#!/usr/bin/env python3
"""Tests for Julia's deterministic morning briefing collector."""

from __future__ import annotations

import importlib.util
import json
import sqlite3
import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path


SCRIPT = Path(__file__).parents[1] / "bin" / "julia-morning-briefing-data.py"
SPEC = importlib.util.spec_from_file_location("julia_morning_briefing_data", SCRIPT)
assert SPEC and SPEC.loader
briefing = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = briefing
SPEC.loader.exec_module(briefing)


class JuliaMorningBriefingDataTests(unittest.TestCase):
    def setUp(self) -> None:
        original_account = briefing.ACCOUNT
        briefing.ACCOUNT = "julia@example.invalid"
        self.addCleanup(setattr, briefing, "ACCOUNT", original_account)
        self.now = datetime(2026, 7, 13, 7, 0, tzinfo=briefing.TIME_ZONE)

    def make_database(self, path: Path, summary: dict[str, object], *, day: int = 13) -> None:
        connection = sqlite3.connect(path)
        connection.execute(
            """CREATE TABLE cron_run_logs (
                store_key TEXT NOT NULL,
                job_id TEXT NOT NULL,
                status TEXT,
                run_at_ms INTEGER,
                summary TEXT
            )"""
        )
        run_at = datetime(2026, 7, day, 6, 45, tzinfo=briefing.TIME_ZONE)
        connection.execute(
            "INSERT INTO cron_run_logs VALUES (?, ?, 'ok', ?, ?)",
            (
                briefing.CRON_STORE_KEY,
                briefing.TRIAGE_JOB_ID,
                int(run_at.timestamp() * 1000),
                json.dumps(summary),
            ),
        )
        connection.commit()
        connection.close()

    @staticmethod
    def handoff() -> dict[str, object]:
        return {
            "schemaVersion": 1,
            "status": "ok",
            "date": "2026-07-13",
            "processed": 4,
            "markedRead": 3,
            "leftUnread": 1,
            "draftsCreated": 0,
            "draftsExisting": 0,
            "archived": 3,
            "trashed": 0,
            "unreadAfter": ["old-private-id"],
            "attention": [
                {
                    "messageId": "attention-private-id",
                    "threadId": "thread-private-id",
                    "from": "Synthetic Person",
                    "subject": "Synthetic request",
                    "reason": "Please review",
                    "deadline": "",
                    "draftStatus": "none",
                }
            ],
            "errors": [],
        }

    def test_collects_all_sources_without_emitting_private_ids(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp = Path(temp_dir)
            database = temp / "state.sqlite"
            snapshot = temp / "sleep.txt"
            self.make_database(database, self.handoff())
            snapshot.write_text(
                "Eight Sleep snapshot 2026-07-13\nScore 88; duration 7h 40m; REM 2h; deep 1h",
                encoding="utf-8",
            )
            calls: list[tuple[list[str], dict[str, str]]] = []

            def runner(args: list[str], env: dict[str, str], timeout: float):
                self.assertEqual(timeout, briefing.COMMAND_TIMEOUT_SECONDS)
                calls.append((args, env))
                params = json.loads(args[args.index("--params") + 1])
                if args[1:4] == ["calendar", "events", "list"]:
                    if "pageToken" not in params:
                        return briefing.CommandResult(
                            0,
                            json.dumps(
                                {
                                    "items": [
                                        {
                                            "id": "private-event-id",
                                            "summary": "Synthetic event",
                                            "start": {"dateTime": "2026-07-13T09:00:00-04:00"},
                                            "end": {"dateTime": "2026-07-13T10:00:00-04:00"},
                                            "location": "Synthetic place",
                                            "description": "Synthetic details",
                                        }
                                    ],
                                    "nextPageToken": "calendar-private-token",
                                }
                            ),
                            "",
                        )
                    self.assertEqual(params["pageToken"], "calendar-private-token")
                    return briefing.CommandResult(0, json.dumps({"items": []}), "")
                if args[-3] == "list":
                    if "pageToken" not in params:
                        return briefing.CommandResult(
                            0,
                            json.dumps(
                                {
                                    "messages": [{"id": "old-private-id"}],
                                    "nextPageToken": "gmail-private-token",
                                }
                            ),
                            "",
                        )
                    self.assertEqual(params["pageToken"], "gmail-private-token")
                    return briefing.CommandResult(
                        0, json.dumps({"messages": [{"id": "new-private-id"}]}), ""
                    )
                self.assertEqual(params["id"], "new-private-id")
                self.assertEqual(params["format"], "full")
                return briefing.CommandResult(
                    0,
                    json.dumps(
                        {
                            "id": "new-private-id",
                            "snippet": "A bounded synthetic request",
                            "payload": {
                                "headers": [
                                    {"name": "From", "value": "New Person"},
                                    {"name": "Subject", "value": "New request"},
                                    {"name": "Date", "value": "Synthetic date"},
                                    {"name": "Message-ID", "value": "private-message-id"},
                                ]
                            },
                        }
                    ),
                    "",
                )

            def http_getter(url: str, timeout: float) -> dict[str, object]:
                self.assertEqual(timeout, briefing.HTTP_TIMEOUT_SECONDS)
                if url == briefing.NET_WORTH_URL:
                    return {"known_value": 2_100_000, "complete": True, "as_of": "2026-07-13"}
                self.assertEqual(url, briefing.FIRE_URL)
                return {"progress_pct": 15.2, "fire_target": 6_300_000}

            result = briefing.collect_data(
                now=self.now,
                runner=runner,
                sleeper=lambda _: None,
                clock=lambda: 0.0,
                http_getter=http_getter,
                db_path=database,
                sleep_path=snapshot,
            )

        self.assertEqual(result["triage"]["status"], "ok")
        self.assertEqual(result["calendar"]["count"], 1)
        self.assertEqual(result["sleep"]["status"], "ok")
        self.assertEqual(result["finances"]["status"], "ok")
        self.assertEqual(result["postTriage"]["count"], 1)
        self.assertEqual(result["postTriage"]["messages"][0]["subject"], "New request")
        serialized = json.dumps(result)
        for private_value in (
            "old-private-id",
            "new-private-id",
            "attention-private-id",
            "thread-private-id",
            "private-event-id",
            "calendar-private-token",
            "gmail-private-token",
            "private-message-id",
        ):
            self.assertNotIn(private_value, serialized)
        for args, env in calls:
            self.assertEqual(env["GOOGLE_WORKSPACE_CLI_ACCOUNT"], briefing.ACCOUNT)
            self.assertNotIn("--account", args)

    def test_token_race_retries_once(self) -> None:
        attempts = 0
        sleeps: list[float] = []

        def runner(args: list[str], env: dict[str, str], timeout: float):
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                return briefing.CommandResult(1, "", "Failed to get token")
            return briefing.CommandResult(0, json.dumps({"items": []}), "")

        result = briefing.collect_calendar(
            self.now,
            deadline=150.0,
            clock=lambda: 0.0,
            runner=runner,
            sleeper=sleeps.append,
        )

        self.assertEqual(result["status"], "ok")
        self.assertEqual(attempts, 2)
        self.assertEqual(sleeps, [5.0])

    def test_stale_handoff_and_sleep_are_unavailable(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp = Path(temp_dir)
            database = temp / "state.sqlite"
            snapshot = temp / "sleep.txt"
            stale = self.handoff()
            stale["date"] = "2026-07-12"
            self.make_database(database, stale, day=12)
            snapshot.write_text("Eight Sleep snapshot 2026-07-12\nScore 90")

            triage, unread_ids = briefing.load_triage_handoff(
                self.now, db_path=database
            )
            sleep = briefing.collect_sleep(self.now, snapshot_path=snapshot)

        self.assertEqual(triage["status"], "unavailable")
        self.assertIsNone(unread_ids)
        self.assertEqual(sleep, {"status": "unavailable", "reason": "stale"})

    def test_missing_account_fails_closed_without_spawning_gws(self) -> None:
        briefing.ACCOUNT = ""

        def forbidden_runner(*_args, **_kwargs):
            self.fail("GWS must not run without Julia's explicit account")

        calendar = briefing.collect_calendar(
            self.now,
            deadline=150.0,
            clock=lambda: 0.0,
            runner=forbidden_runner,
            sleeper=lambda _: None,
        )
        arrivals = briefing.collect_post_triage_arrivals(
            set(),
            deadline=150.0,
            clock=lambda: 0.0,
            runner=forbidden_runner,
            sleeper=lambda _: None,
        )

        self.assertEqual(calendar["reason"], "missing_account")
        self.assertEqual(arrivals["reason"], "missing_account")


if __name__ == "__main__":
    unittest.main()
