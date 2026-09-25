#!/usr/bin/env python3
"""Tests for Julia's bounded read-only inbox review."""

from __future__ import annotations

import importlib.util
import json
import sys
import unittest
from unittest import mock
from datetime import datetime
from pathlib import Path


SCRIPT = Path(__file__).parents[1] / "bin" / "julia-inbox-review.py"
SPEC = importlib.util.spec_from_file_location("julia_inbox_review", SCRIPT)
assert SPEC and SPEC.loader
review = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = review
SPEC.loader.exec_module(review)


class InboxReviewTests(unittest.TestCase):
    def setUp(self):
        account = review.briefing.ACCOUNT
        review.briefing.ACCOUNT = "julia@example.invalid"
        self.addCleanup(setattr, review.briefing, "ACCOUNT", account)
        self.now = datetime(2026, 9, 25, 8, 0, tzinfo=review.briefing.TIME_ZONE)
        self.calls = []

    def message(self, message_id, labels, offset=0):
        return {
            "id": message_id,
            "internalDate": str(int(self.now.timestamp() * 1000) - offset),
            "labelIds": labels,
            "snippet": "Synthetic context",
            "payload": {"headers": [
                {"name": "From", "value": "Synthetic sender"},
                {"name": "Subject", "value": "Synthetic subject"},
            ], "body": {"data": "RAW_BODY_MUST_NOT_APPEAR"}},
        }

    def runner(self, args, env, timeout):
        self.calls.append(args)
        self.assertEqual(env["GOOGLE_WORKSPACE_CLI_ACCOUNT"], "julia@example.invalid")
        self.assertNotIn("--account", args)
        self.assertEqual(args[1:3], ["gmail", "users"])
        resource, method = args[3:5]
        self.assertIn((resource, method), {
            ("labels", "list"), ("labels", "get"),
            ("messages", "list"), ("threads", "get"),
        })
        params = json.loads(args[args.index("--params") + 1])
        self.assertEqual(params["userId"], "me")
        if (resource, method) == ("labels", "list"):
            result = {"labels": [{"id": "action-label", "name": "OpenClaw/Action"}]}
        elif (resource, method) == ("labels", "get"):
            result = {"messagesTotal": 3, "messagesUnread": 0}
        elif resource == "messages":
            is_action = params["q"] == review.ACTION_QUERY
            prefix = "action" if is_action else "backlog"
            result = {"messages": [{"id": prefix, "threadId": prefix + "-thread"}]}
        else:
            self.assertEqual(params["format"], "metadata")
            is_action = params["id"].startswith("action")
            prefix = "action" if is_action else "backlog"
            result = {"messages": [
                self.message(prefix, ["INBOX", "action-label"] if is_action else ["INBOX", "STARRED"], 10 * 86400000),
                self.message(prefix + "-reply", ["SENT"], 86400000),
                self.message(prefix + "-draft", ["DRAFT"]),
            ]}
        return review.briefing.CommandResult(0, json.dumps(result), "")

    def test_preview_includes_read_actions_without_any_mutation(self):
        result = review.collect_review(now=self.now, runner=self.runner, clock=lambda: 0)
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["mode"], "preview")
        self.assertNotIn("is:unread", review.ACTION_QUERY)
        self.assertEqual(result["metrics"], {"inboxTotal": 3, "inboxUnread": 0})
        self.assertEqual(result["scope"], "actions")
        self.assertNotIn("backlog", result)
        queries = [json.loads(args[-1])["q"] for args in self.calls if args[3:5] == ["messages", "list"]]
        self.assertEqual(queries, [review.ACTION_QUERY])
        action = result["queue"]["candidates"][0]
        self.assertTrue(action["latestNonDraftSentByJulia"])
        self.assertEqual(action["draftCount"], 1)
        self.assertEqual(action["ageDays"], 10)
        self.assertTrue(action["protected"])
        self.assertNotIn("RAW_BODY_MUST_NOT_APPEAR", json.dumps(result))

    def test_backlog_requires_explicit_scope_and_remains_read_only(self):
        result = review.collect_review(scope="backlog", now=self.now, runner=self.runner, clock=lambda: 0)
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["scope"], "backlog")
        queries = [json.loads(args[-1])["q"] for args in self.calls if args[3:5] == ["messages", "list"]]
        self.assertEqual(queries, [review.BACKLOG_QUERY])
        self.assertTrue(result["queue"]["candidates"][0]["protected"])
        self.assertIn("is:read", queries[0])
        for label in review.PRIMARY_LABELS:
            self.assertIn(f'-label:"OpenClaw/{label}"', queries[0])

    def test_cli_defaults_to_actions_and_accepts_explicit_backlog(self):
        for arguments, scope in (([], "actions"), (["--scope", "backlog"], "backlog")):
            with self.subTest(scope=scope), mock.patch.object(review, "collect_review", return_value={"status": "ok"}) as collect:
                with mock.patch("builtins.print"):
                    self.assertEqual(review.main(arguments), 0)
                collect.assert_called_once_with(scope=scope)

    def test_candidate_limit_does_not_claim_full_content_review(self):
        def runner(args, env, timeout):
            params = json.loads(args[-1])
            if args[3:5] == ["messages", "list"]:
                entries = [{"id": "backlog", "threadId": f"backlog-{index}"} for index in range(30)]
                entries = [{**item, "id": f"message-{index}"} for index, item in enumerate(entries)]
                return review.briefing.CommandResult(0, json.dumps({"messages": entries}), "")
            if args[3:5] == ["threads", "get"]:
                index = params["id"].rsplit("-", 1)[-1]
                return review.briefing.CommandResult(0, json.dumps({"messages": [self.message(f"message-{index}", [])]}), "")
            return self.runner(args, env, timeout)

        result = review.collect_review(now=self.now, runner=runner, clock=lambda: 0)
        self.assertEqual(len(result["queue"]["candidates"]), 20)
        self.assertTrue(result["queue"]["hasMoreCandidates"])
        self.assertTrue(result["queue"]["complete"])
        self.assertEqual(result["queue"]["count"], 30)

    def test_multiple_messages_in_one_thread_are_reviewed_once(self):
        def runner(args, env, timeout):
            if args[3:5] == ["messages", "list"]:
                return review.briefing.CommandResult(0, json.dumps({"messages": [
                    {"id": "backlog", "threadId": "backlog-thread"},
                    {"id": "backlog-reply", "threadId": "backlog-thread"},
                ]}), "")
            return self.runner(args, env, timeout)

        result = review.collect_review(now=self.now, runner=runner, clock=lambda: 0)
        self.assertEqual(result["queue"]["count"], 2)
        self.assertEqual(len(result["queue"]["candidates"]), 1)
        self.assertFalse(result["queue"]["hasMoreCandidates"])

    def test_expired_deadline_does_not_launch_a_command(self):
        times = iter((0, 151))
        result = review.collect_review(
            now=self.now, runner=lambda *_: self.fail("Unexpected launch"),
            clock=lambda: next(times),
        )
        self.assertEqual(result["status"], "partial")
        self.assertEqual(result["errors"], ["timeout"])

    def test_scan_limit_keeps_inventory_incomplete(self):
        pages = []

        def runner(args, env, timeout):
            if args[3:5] == ["messages", "list"]:
                pages.append(args)
                return review.briefing.CommandResult(0, json.dumps({
                    "messages": [], "nextPageToken": str(len(pages)),
                }), "")
            return self.runner(args, env, timeout)

        result = review.collect_review(now=self.now, runner=runner, clock=lambda: 0)
        self.assertEqual(len(pages), 10)
        self.assertEqual(result["status"], "partial")
        self.assertEqual(result["errors"], ["inventory_truncated"])
        self.assertIsNone(result["queue"]["count"])
        self.assertFalse(result["queue"]["complete"])
        self.assertTrue(result["queue"]["hasMoreCandidates"])

    def test_token_cycle_is_partial_not_an_exact_count(self):
        def runner(args, env, timeout):
            if args[3:5] == ["messages", "list"]:
                return review.briefing.CommandResult(0, json.dumps({"messages": [], "nextPageToken": "same"}), "")
            return self.runner(args, env, timeout)

        result = review.collect_review(now=self.now, runner=runner, clock=lambda: 0)
        self.assertEqual(result["status"], "partial")
        self.assertIsNone(result["queue"]["count"])
        self.assertFalse(result["queue"]["complete"])
        self.assertEqual(result["errors"], ["invalid_pagination"])

    def test_auth_failure_stops_without_retry_or_private_error_output(self):
        calls = []

        def runner(*args):
            calls.append(args)
            return review.briefing.CommandResult(1, "", "No credentials provided PRIVATE_SECRET")

        result = review.collect_review(now=self.now, runner=runner, clock=lambda: 0)
        self.assertEqual(len(calls), 1)
        self.assertEqual(result["errors"], ["auth_error"])
        self.assertNotIn("PRIVATE_SECRET", json.dumps(result))

    def test_missing_account_never_launches_gws(self):
        review.briefing.ACCOUNT = ""
        result = review.collect_review(runner=lambda *_: self.fail("Unexpected GWS launch"))
        self.assertEqual(result["status"], "unavailable")

    def test_read_state_does_not_change_activity_key(self):
        entry = {"id": "message", "threadId": "thread"}
        message = self.message("message", ["INBOX", "UNREAD"])
        first = review.summarize_thread(entry, [message], {}, self.now)
        message["labelIds"] = ["INBOX"]
        second = review.summarize_thread(entry, [message], {}, self.now)
        self.assertEqual(first["activityKey"], second["activityKey"])
        message["id"] = "new-message"
        changed = review.summarize_thread({**entry, "id": "new-message"}, [message], {}, self.now)
        self.assertNotEqual(first["activityKey"], changed["activityKey"])


if __name__ == "__main__":
    unittest.main()
