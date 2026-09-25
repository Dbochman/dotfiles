#!/usr/bin/env python3
"""Tests for Julia's deterministic morning briefing collector."""

from __future__ import annotations

import copy
import importlib.util
import json
import sqlite3
import sys
import tempfile
import unittest
from unittest import mock
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

    def test_failed_triage_preserves_sections_without_inventing_new_arrivals(self) -> None:
        for handoff_status in ("auth_error", "partial", "unknown"):
            with self.subTest(handoff_status=handoff_status), tempfile.TemporaryDirectory() as temp_dir:
                temp = Path(temp_dir)
                database = temp / "state.sqlite"
                snapshot = temp / "sleep.txt"
                handoff = self.handoff()
                handoff["status"] = handoff_status
                handoff["unreadAfter"] = []
                handoff["errors"] = ["Synthetic failure"]
                self.make_database(database, handoff)
                snapshot.write_text("Eight Sleep snapshot 2026-07-13\nScore 88")
                calls: list[list[str]] = []

                def runner(args: list[str], env: dict[str, str], timeout: float):
                    calls.append(args)
                    self.assertEqual(args[1:4], ["calendar", "events", "list"])
                    return briefing.CommandResult(0, json.dumps({"items": []}), "")

                def http_getter(url: str, timeout: float):
                    if url == briefing.NET_WORTH_URL:
                        return {
                            "known_value": 100,
                            "complete": True,
                            "as_of": "2026-07-13",
                        }
                    return {"progress_pct": 10, "fire_target": 1000}

                result = briefing.collect_data(
                    now=self.now,
                    runner=runner,
                    sleeper=lambda _: self.fail("No retry should be needed"),
                    clock=lambda: 0.0,
                    http_getter=http_getter,
                    db_path=database,
                    sleep_path=snapshot,
                )

                self.assertEqual(result["triage"]["handoffStatus"], handoff_status)
                self.assertEqual(result["triage"]["processed"], 4)
                self.assertEqual(result["triage"]["errorCount"], 1)
                self.assertEqual(len(result["triage"]["attention"]), 1)
                self.assertEqual(result["calendar"]["status"], "ok")
                self.assertEqual(result["sleep"]["status"], "ok")
                self.assertEqual(result["finances"]["status"], "ok")
                self.assertEqual(result["postTriage"]["status"], "skipped")
                self.assertEqual(len(calls), 1)

    def test_ambiguous_auth_error_is_not_retried(self) -> None:
        calls: list[list[str]] = []

        def runner(args: list[str], env: dict[str, str], timeout: float):
            calls.append(args)
            return briefing.CommandResult(1, "", "No credentials provided")

        result = briefing.collect_calendar(
            self.now,
            deadline=150.0,
            clock=lambda: 0.0,
            runner=runner,
            sleeper=lambda _: self.fail("Ambiguous auth errors must not be retried"),
        )

        self.assertEqual(result, {"status": "unavailable", "reason": "auth_error"})
        self.assertEqual(len(calls), 1)

    def review_item(self, **changes):
        return {
            "messageId": "review-private-message", "threadId": "review-private-thread",
            "activityKey": "a" * 64, "subject": "Synthetic review", "reason": "Needs a decision",
            "nextStep": "Confirm whether to keep open", "state": "needs_you",
            "ageDays": 14, "deadline": "", "urgent": False, "protected": False,
            "draftStatus": "none", **changes,
        }

    def version_two_handoff(self):
        return {
            **self.handoff(), "schemaVersion": 2,
            "cleanupVerified": True, "unreadSnapshotVerified": True,
            "review": {
                "mode": "preview", "status": "ok", "inboxTotal": 110,
                "inboxUnread": 1, "actionMessageCount": 7,
                "inventoryComplete": True, "errors": [],
            },
            "actionReview": [self.review_item()],
        }

    def test_version_two_handoff_validates_without_exposing_review_ids(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            database = Path(temp_dir) / "state.sqlite"
            payload = self.version_two_handoff()
            self.assertEqual(briefing.validate_handoff(payload, self.now), payload)
            self.make_database(database, payload)
            triage, unread = briefing.load_triage_handoff(self.now, db_path=database)
        self.assertEqual(triage["outcomes"]["actionMessageCount"], 7)
        self.assertEqual(triage["actionReview"][0]["group"], "new_or_changed")
        for private in ("review-private-message", "review-private-thread", "a" * 64):
            self.assertNotIn(private, json.dumps(triage))
        self.assertEqual(unread, {"old-private-id"})

    def test_invalid_version_two_outcomes_fail_closed(self):
        mutations = [
            lambda payload: payload.update(processed=True),
            lambda payload: payload.update(leftUnread=99),
            lambda payload: payload.update(markedRead=50),
            lambda payload: payload.update(errors=["Failure"]),
            lambda payload: payload.update(cleanupVerified=False),
            lambda payload: payload.update(cleanupVerified=1),
            lambda payload: payload.update(unreadSnapshotVerified=False),
            lambda payload: payload.update(unreadSnapshotVerified=1),
            lambda payload: payload["review"].update(errors=["Unexpected error"]),
            lambda payload: payload["review"].update(status="partial"),
            lambda payload: payload["review"].update(inventoryComplete=False),
            lambda payload: payload["review"].update(inboxTotal=None),
            lambda payload: payload["review"].update(actionMessageCount=0),
            lambda payload: payload["review"].update(status="partial", errors=["truncated"], inventoryComplete=False),
            lambda payload: payload["review"].update(status="unavailable", errors=["timeout"]),
            lambda payload: payload["actionReview"][0].update(state="closed"),
            lambda payload: payload["actionReview"][0].update(deadline="next week"),
            lambda payload: payload["actionReview"][0].update(activityKey="invented"),
            lambda payload: payload.update(actionReview=[self.review_item()] * 21),
            lambda payload: payload.update(backlogPreview=[self.review_item(state="routine_candidate", protected=True)]),
            lambda payload: payload.update(secret="unexpected field"),
        ]
        for index, mutate in enumerate(mutations):
            with self.subTest(index=index):
                payload = self.version_two_handoff()
                mutate(payload)
                with self.assertRaises(ValueError):
                    briefing.validate_handoff(payload, self.now)

    def test_partial_outcomes_are_visible_not_a_verified_success(self):
        payload = self.version_two_handoff()
        payload.update(status="partial", errors=["Readback unavailable"], cleanupVerified=None, unreadSnapshotVerified=False)
        payload["review"].update(status="partial", errors=["timeout"], inventoryComplete=False, actionMessageCount=None)
        self.assertEqual(briefing.validate_handoff(payload, self.now), payload)

    def test_preflight_auth_failure_cannot_claim_review_or_mutation_evidence(self):
        payload = self.version_two_handoff()
        payload.update(status="auth_error", errors=["auth_error"], attention=[], unreadAfter=[], actionReview=[])
        payload.update(cleanupVerified=None, unreadSnapshotVerified=False)
        payload.update({key: 0 for key in briefing.HANDOFF_COUNTERS})
        payload["review"].update(
            status="unavailable", inboxTotal=None, inboxUnread=None,
            actionMessageCount=None, inventoryComplete=False, errors=["auth_error"],
        )
        self.assertEqual(briefing.validate_handoff(payload, self.now), payload)
        payload["actionReview"] = [self.review_item()]
        with self.assertRaises(ValueError):
            briefing.validate_handoff(payload, self.now)

    def test_reminder_groups_keep_due_and_urgent_items_visible(self):
        previous = [self.review_item()]
        monday = briefing.review_presentation(previous, previous, self.now)
        self.assertEqual(monday[0]["group"], "decision_needed")
        tuesday = self.now.replace(day=14)
        unchanged = briefing.review_presentation(previous, previous, tuesday)
        self.assertEqual(unchanged[0]["group"], "unchanged")
        due = self.review_item(deadline="2026-07-15")
        self.assertEqual(briefing.review_presentation([due], [due], tuesday)[0]["group"], "urgent_or_due")
        urgent = self.review_item(urgent=True)
        self.assertEqual(briefing.review_presentation([urgent], [urgent], tuesday)[0]["group"], "urgent_or_due")
        changed = self.review_item(activityKey="b" * 64)
        self.assertEqual(briefing.review_presentation([changed], previous, tuesday)[0]["group"], "new_or_changed")

    def test_prior_day_history_consolidates_without_duplicate_attention(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            database = Path(temp_dir) / "state.sqlite"
            payload = self.version_two_handoff()
            payload["attention"][0]["threadId"] = "review-private-thread"
            self.make_database(database, payload)
            older = copy.deepcopy(payload)
            older["date"] = "2026-07-12"
            with sqlite3.connect(database) as connection:
                connection.execute("INSERT INTO cron_run_logs VALUES (?, ?, 'ok', ?, ?)", (
                    briefing.CRON_STORE_KEY, briefing.TRIAGE_JOB_ID,
                    int(self.now.replace(day=12).timestamp() * 1000), json.dumps(older),
                ))
            triage, _ = briefing.load_triage_handoff(self.now, db_path=database)
        self.assertEqual(triage["attention"][0]["group"], "decision_needed")
        self.assertEqual(triage["attention"][0]["reason"], payload["attention"][0]["reason"])
        self.assertEqual(triage["actionReview"], [])

    def test_inconclusive_preview_preserves_all_confirmed_attention_details(self):
        payload = self.version_two_handoff()
        payload["attention"][0].update(
            threadId="review-private-thread", reason="Confirmed request due today",
            deadline="2026-07-13", draftStatus="created",
        )
        payload["attention"].append({
            **payload["attention"][0], "messageId": "second-private-message",
            "reason": "Another confirmed request", "deadline": "Tomorrow morning",
            "draftStatus": "existing",
        })
        payload["actionReview"][0].update(
            state="needs_context", reason="Inconclusive metadata", deadline="", draftStatus="none",
        )
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "state.sqlite"
            self.make_database(database, payload)
            triage, _ = briefing.load_triage_handoff(self.now, db_path=database)
        self.assertEqual(len(triage["attention"]), 2)
        for original, shown in zip(payload["attention"], triage["attention"]):
            for key in ("from", "subject", "reason", "deadline", "draftStatus"):
                self.assertEqual(shown[key], original[key])
            self.assertEqual(shown["group"], "urgent_or_due")
        self.assertEqual(triage["actionReview"], [])
        self.assertNotIn("private", json.dumps(triage))

    def test_changed_confirmed_reason_is_not_hidden_in_unchanged_reminders(self):
        item = self.review_item()
        original = {**self.handoff()["attention"][0], "threadId": item["threadId"]}
        changed = {**original, "reason": "A newly confirmed action"}
        result = briefing.review_presentation(
            [item], [item], self.now.replace(day=14),
            attention=[changed], previous_attention=[original],
        )
        self.assertEqual(result[0]["group"], "new_or_changed")

    def test_optional_review_failure_preserves_verified_new_arrival_check(self):
        for status in ("partial", "unavailable"):
            with self.subTest(status=status), tempfile.TemporaryDirectory() as directory:
                payload = self.version_two_handoff()
                payload["review"].update(
                    status=status, errors=["timeout"], inventoryComplete=False,
                    actionMessageCount=None, inboxTotal=None, inboxUnread=None,
                )
                payload["actionReview"] = []
                database = Path(directory) / "state.sqlite"
                self.make_database(database, payload)
                triage, baseline = briefing.load_triage_handoff(self.now, db_path=database)
                self.assertEqual(triage["handoffStatus"], "ok")
                self.assertTrue(triage["cleanupVerified"])
                self.assertEqual(baseline, {"old-private-id"})
                self.assertEqual(triage["outcomes"]["status"], status)
                self.assertEqual(triage["outcomes"]["errorCount"], 1)
                self.assertNotIn("errors", triage["outcomes"])

                def runner(args, env, timeout):
                    if args[1:5] == ["gmail", "users", "messages", "list"]:
                        return briefing.CommandResult(0, json.dumps({
                            "messages": [{"id": "old-private-id"}, {"id": "new-private-id"}],
                        }), "")
                    self.assertEqual(args[1:5], ["gmail", "users", "messages", "get"])
                    self.assertEqual(json.loads(args[-1])["id"], "new-private-id")
                    return briefing.CommandResult(0, json.dumps({
                        "snippet": "New request since verified snapshot",
                        "payload": {"headers": [{"name": "Subject", "value": "New request"}]},
                    }), "")

                arrivals = briefing.collect_post_triage_arrivals(
                    baseline, deadline=150, clock=lambda: 0, runner=runner,
                    sleeper=lambda _: self.fail("Unexpected retry"),
                )
                self.assertEqual(arrivals["status"], "ok")
                self.assertEqual(arrivals["count"], 1)
                self.assertEqual(arrivals["messages"][0]["subject"], "New request")

    def test_partial_cleanup_uses_only_explicitly_verified_unread_snapshot(self):
        for verified in (False, True):
            with self.subTest(verified=verified), tempfile.TemporaryDirectory() as directory:
                payload = self.version_two_handoff()
                payload.update(
                    status="partial", errors=["Cleanup incomplete"],
                    cleanupVerified=None, unreadSnapshotVerified=verified,
                )
                database = Path(directory) / "state.sqlite"
                self.make_database(database, payload)
                _, baseline = briefing.load_triage_handoff(self.now, db_path=database)
            self.assertEqual(baseline, {"old-private-id"} if verified else None)

    def test_newer_invalid_or_failed_run_never_falls_back_to_earlier_success(self):
        for status, summary in (("ok", "Prose before JSON {}"), ("error", None)):
            with self.subTest(status=status), tempfile.TemporaryDirectory() as temp_dir:
                database = Path(temp_dir) / "state.sqlite"
                self.make_database(database, self.handoff())
                with sqlite3.connect(database) as connection:
                    connection.execute("INSERT INTO cron_run_logs VALUES (?, ?, ?, ?, ?)", (
                        briefing.CRON_STORE_KEY, briefing.TRIAGE_JOB_ID, status,
                        int(self.now.timestamp() * 1000), summary,
                    ))
                triage, unread = briefing.load_triage_handoff(self.now, db_path=database)
            self.assertEqual(triage["status"], "unavailable")
            self.assertIsNone(unread)

    def test_handoff_file_requires_private_regular_current_json(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "handoff.json"
            payload = self.version_two_handoff()
            payload["date"] = datetime.now(briefing.TIME_ZONE).date().isoformat()
            path.write_text(json.dumps(payload))
            path.chmod(0o600)
            self.assertEqual(briefing.validate_handoff_file(path), payload)
            link = Path(temp_dir) / "link"
            link.symlink_to(path)
            with self.assertRaises(OSError):
                briefing.validate_handoff_file(link)
            path.chmod(0o644)
            with self.assertRaises(ValueError):
                briefing.validate_handoff_file(path)
            path.chmod(0o600)
            path.write_text("Private prose " + json.dumps(payload))
            with self.assertRaises(ValueError):
                briefing.validate_handoff_file(path)

    def test_validation_cli_never_collects_sources(self):
        with mock.patch.object(briefing, "collect_data", side_effect=AssertionError("Unexpected collection")):
            with mock.patch.object(briefing.sys, "stderr"):
                self.assertEqual(briefing.main(["--validate-handoff", "/nonexistent/handoff.json"]), 2)

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
