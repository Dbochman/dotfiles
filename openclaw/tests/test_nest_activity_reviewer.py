#!/usr/bin/env python3
"""Focused safety and behavior tests for Cabin activity commentary."""

from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import sqlite3
import sys
import tempfile
import time
import unittest
from unittest import mock


MODULE_PATH = Path(__file__).resolve().parents[1] / "bin" / "nest-activity-reviewer.py"
SPEC = importlib.util.spec_from_file_location("nest_activity_reviewer", MODULE_PATH)
assert SPEC and SPEC.loader
review = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = review
SPEC.loader.exec_module(review)


class FakeClock:
    def __init__(self, value: float):
        self.value = value

    def __call__(self) -> float:
        return self.value


class FakeCommands:
    def __init__(self, clock: FakeClock):
        self.clock = clock
        self.capture_calls: list[Path] = []
        self.analysis_calls: list[Path] = []
        self.messages: list[str] = []
        self.decision = review.AnalysisDecision(
            False, "unknown", "routine", "high", ""
        )
        self.capture_error: str | None = None
        self.leave_capture_temp = False
        self.analysis_error: str | None = None
        self.analysis_delay = 0.0
        self.analysis_hook = None
        self.send_error: str | None = None
        self.crash_during_send = False
        self.presence_observation_calls = 0
        self.presence_observation_result = True
        self.presence_observation_error: str | None = None

    def observe_presence(self) -> bool:
        self.presence_observation_calls += 1
        if self.presence_observation_error:
            raise review.ReviewerError(self.presence_observation_error)
        return self.presence_observation_result

    def capture(self, image_path: Path) -> None:
        self.capture_calls.append(image_path)
        if self.capture_error:
            raise review.ReviewerError(self.capture_error)
        image_path.write_bytes(b"\xff\xd8\xffprivate-frame\xff\xd9")
        image_path.chmod(0o600)
        os.utime(image_path, (self.clock(), self.clock()))
        if self.leave_capture_temp:
            temporary = image_path.parent / f".{image_path.name}.fixture.tmp"
            temporary.write_bytes(b"private-partial-frame")
            temporary.chmod(0o600)

    def analyze(self, image_path: Path) -> "review.AnalysisDecision":
        self.analysis_calls.append(image_path)
        if self.analysis_error:
            raise review.ReviewerError(self.analysis_error)
        self.clock.value += self.analysis_delay
        if self.analysis_hook is not None:
            self.analysis_hook()
        return self.decision

    def send(self, message: str) -> None:
        self.messages.append(message)
        if self.crash_during_send:
            raise KeyboardInterrupt
        if self.send_error:
            raise review.ReviewerError(self.send_error)


class ActivityReviewerTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.home = Path(self.temporary.name) / "home"
        self.state_dir = self.home / ".openclaw" / "nest-events" / "state"
        self.state_dir.mkdir(parents=True, mode=0o700)
        self.state_dir.chmod(0o700)
        self.db_path = self.state_dir / "events.sqlite3"
        with sqlite3.connect(self.db_path) as connection:
            connection.execute(
                """
                CREATE TABLE outbox (
                    id INTEGER PRIMARY KEY,
                    event_record_id INTEGER NOT NULL UNIQUE,
                    alias TEXT NOT NULL,
                    site TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    event_at TEXT NOT NULL,
                    capture_strategy TEXT NOT NULL,
                    status TEXT NOT NULL,
                    created_at TEXT NOT NULL
                )
                """
            )
        self.db_path.chmod(0o600)
        self.clock = FakeClock(time.time())
        self.presence_dir = self.home / ".openclaw" / "presence"
        self.presence_dir.mkdir(mode=0o755)
        self.presence_state = self.presence_dir / "state.json"
        self.cabin_scan = self.presence_dir / "cabin-scan.json"
        self.crosstown_scan = self.presence_dir / "crosstown-scan.json"
        self.write_presence()
        self.environ = {
            "HOME": str(self.home),
            "NEST_ACTIVITY_MODE": "cabin-commentary",
            "NEST_EVENT_STATE_DIR": str(self.state_dir),
            "OPENCLAW_DYLAN_IMESSAGE_TARGET": "chat_id:7",
        }
        self.settings = review.load_settings(self.environ)
        self.commands = FakeCommands(self.clock)

    def write_presence(
        self,
        *,
        occupancy: str = "confirmed_vacant",
        state_age: float = 30,
        scan_age: float = 30,
        cabin_present: bool = False,
        crosstown_fresh: bool = True,
        people_consistent: bool = True,
        changed_age: float = 3600,
    ) -> None:
        state_timestamp = review._timestamp(self.clock() - state_age)
        changed_at = review._timestamp(self.clock() - changed_age)
        people = {
            person: {
                "cabin": False if people_consistent else True,
                "crosstown": True if people_consistent else False,
                "location": "crosstown" if people_consistent else "cabin",
            }
            for person in ("Dylan", "Julia")
        }
        state = {
            "timestamp": state_timestamp,
            "people": people,
            "cabin": {
                "occupancy": occupancy,
                "stateChangedAt": changed_at,
                "scanAge": "0min",
                "fresh": True,
            },
            "crosstown": {
                "occupancy": "occupied",
                "stateChangedAt": changed_at,
                "scanAge": "0min",
                "fresh": crosstown_fresh,
            },
            "transitions": [],
        }
        scan_timestamp = review._timestamp(self.clock() - scan_age)
        cabin_scan = {
            "location": "cabin",
            "timestamp": scan_timestamp,
            "presence": {
                person: {"present": cabin_present}
                for person in ("Dylan", "Julia")
            },
        }
        crosstown_scan = {
            "location": "crosstown",
            "timestamp": scan_timestamp,
            "presence": {
                person: {"present": True}
                for person in ("Dylan", "Julia")
            },
        }
        for path, payload, mode in (
            (self.presence_state, state, 0o644),
            (self.cabin_scan, cabin_scan, 0o644),
            (self.crosstown_scan, crosstown_scan, 0o600),
        ):
            path.write_text(json.dumps(payload), encoding="utf-8")
            path.chmod(mode)

    def reviewer(self, commands: FakeCommands | None = None) -> "review.ActivityReviewer":
        return review.ActivityReviewer(
            self.settings,
            commands=self.commands if commands is None else commands,
            clock=self.clock,
        )

    def add_event(
        self,
        *,
        alias: str = "Kitchen",
        site: str = "Cabin",
        event_type: str = "motion",
        capture_strategy: str = "live",
        status: str = "shadowed",
        age: float = 10,
    ) -> int:
        created = review._timestamp(self.clock() - age)
        with sqlite3.connect(self.db_path) as connection:
            row_id = connection.execute("SELECT COALESCE(MAX(id), 0) + 1 FROM outbox").fetchone()[0]
            connection.execute(
                """
                INSERT INTO outbox(
                    id, event_record_id, alias, site, event_type, event_at,
                    capture_strategy, status, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    row_id,
                    row_id,
                    alias,
                    site,
                    event_type,
                    created,
                    capture_strategy,
                    status,
                    created,
                ),
            )
        return row_id


class ConfigurationAndInitializationTests(ActivityReviewerTestCase):
    def test_only_explicit_chat_id_and_scoped_paths_are_accepted(self) -> None:
        self.assertEqual(self.settings.chat_id, "7")
        for invalid in ("", "7", "chat_guid:private", "chat_id:0", "chat_id:7x"):
            with self.subTest(invalid=invalid):
                environment = dict(self.environ)
                environment["OPENCLAW_DYLAN_IMESSAGE_TARGET"] = invalid
                with self.assertRaisesRegex(review.ReviewerError, "chat_target_invalid"):
                    review.load_settings(environment)

        outside = dict(self.environ)
        outside["NEST_ACTIVITY_STATE_FILE"] = str(self.home / "outside.json")
        with self.assertRaisesRegex(review.ReviewerError, "path_scope_invalid"):
            review.load_settings(outside)

    def test_initialization_baselines_existing_history(self) -> None:
        historical = self.add_event(alias="Living Room Wired", site="Crosstown")
        reviewer = self.reviewer()
        state = reviewer.initialize()
        self.assertEqual(state["lastSeenOutboxId"], historical)
        self.assertEqual(reviewer.run_once(), "idle")
        self.assertEqual(self.commands.capture_calls, [])
        self.assertEqual(self.settings.state_path.stat().st_mode & 0o777, 0o600)
        self.assertEqual(self.settings.image_dir.stat().st_mode & 0o777, 0o700)

    def test_insecure_or_symlink_state_fails_closed(self) -> None:
        reviewer = self.reviewer()
        reviewer.initialize()
        self.settings.state_path.chmod(0o644)
        with self.assertRaisesRegex(review.ReviewerError, "state_permissions_invalid"):
            reviewer.state()

        self.settings.state_path.unlink()
        self.settings.state_path.symlink_to(self.db_path)
        with self.assertRaisesRegex(review.ReviewerError, "state_permissions_invalid"):
            reviewer.initialize()


class PolicyAndReviewTests(ActivityReviewerTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.worker = self.reviewer()
        self.worker.initialize()

    def test_crosstown_rows_never_capture_analyze_or_send(self) -> None:
        first = self.add_event(alias="Living Room", site="Crosstown")
        second = self.add_event(alias="Living Room Wired", site="Crosstown")
        self.assertEqual(self.worker.run_once(), "ignored")
        state = self.worker.state()
        self.assertEqual(state["lastSeenOutboxId"], second)
        self.assertGreater(second, first)
        self.assertEqual(self.commands.capture_calls, [])
        self.assertEqual(self.commands.analysis_calls, [])
        self.assertEqual(self.commands.messages, [])

    def test_occupied_presence_is_shadow_with_zero_visual_work(self) -> None:
        self.write_presence(occupancy="occupied", people_consistent=False)
        self.add_event(event_type="person")
        self.assertEqual(self.worker.run_once(), "presence_shadow")
        self.assertEqual(self.commands.presence_observation_calls, 0)
        self.assertEqual(self.commands.capture_calls, [])
        self.assertEqual(self.commands.analysis_calls, [])
        self.assertEqual(self.commands.messages, [])
        state = self.worker.state()
        self.assertEqual(state["lastPresenceMode"], "shadow_occupied")
        self.assertEqual(state["counters"]["presenceSuppressed"], 1)

    def test_live_presence_veto_closes_cached_arrival_window(self) -> None:
        self.commands.presence_observation_result = False
        self.add_event(event_type="person")
        self.assertEqual(self.worker.run_once(), "presence_shadow")
        self.assertEqual(self.commands.presence_observation_calls, 1)
        self.assertEqual(self.commands.capture_calls, [])
        self.assertEqual(self.commands.messages, [])
        self.assertEqual(
            self.worker.state()["lastPresenceMode"], "shadow_live_veto"
        )

    def test_presence_observation_failure_is_shadow(self) -> None:
        self.commands.presence_observation_error = "presence_observation_failed"
        self.add_event()
        self.assertEqual(self.worker.run_once(), "presence_shadow")
        self.assertEqual(self.commands.capture_calls, [])
        self.assertEqual(self.commands.messages, [])

    def test_occupancy_change_during_analysis_suppresses_delivery(self) -> None:
        self.commands.decision = review.AnalysisDecision(
            True, "person", "routine", "high", "A person is beside the kitchen table."
        )
        self.commands.analysis_hook = lambda: self.write_presence(
            occupancy="occupied", people_consistent=False
        )
        self.add_event(event_type="person")
        self.assertEqual(self.worker.run_once(), "presence_shadow")
        self.assertEqual(len(self.commands.capture_calls), 1)
        self.assertEqual(len(self.commands.analysis_calls), 1)
        self.assertEqual(self.commands.messages, [])
        self.assertIsNone(self.worker.state()["lastMessageAttemptAt"])

    def test_cursor_recovers_if_listener_pruning_resets_sqlite_row_ids(self) -> None:
        first = self.add_event(alias="Living Room", site="Crosstown")
        self.assertEqual(self.worker.run_once(), "ignored")
        self.assertEqual(self.worker.state()["lastSeenOutboxId"], first)
        with sqlite3.connect(self.db_path) as connection:
            connection.execute("DELETE FROM outbox")
        self.assertEqual(self.worker.run_once(), "idle")
        self.assertEqual(self.worker.state()["lastSeenOutboxId"], 0)

        self.add_event(event_type="person")
        self.assertEqual(self.worker.run_once(), "silent")
        self.assertEqual(len(self.commands.capture_calls), 1)

    def test_kitchen_policy_drift_fails_without_side_effects(self) -> None:
        self.add_event(status="sent")
        with self.assertRaisesRegex(review.ReviewerError, "kitchen_policy_invalid"):
            self.worker.run_once()
        self.assertEqual(self.commands.capture_calls, [])

    def test_fresh_event_settles_then_empty_frame_is_silent_and_deleted(self) -> None:
        self.add_event(age=1)
        self.assertEqual(self.worker.run_once(), "waiting")
        self.assertEqual(self.commands.capture_calls, [])
        self.clock.value += 8
        self.assertEqual(self.worker.run_once(), "silent")
        self.assertEqual(len(self.commands.capture_calls), 1)
        self.assertEqual(len(self.commands.analysis_calls), 1)
        self.assertEqual(self.commands.messages, [])
        self.assertEqual(list(self.settings.image_dir.iterdir()), [])
        self.assertEqual(self.worker.state()["lastDecision"], "silent")

    def test_helper_temporary_frame_is_removed_in_same_cycle(self) -> None:
        self.commands.leave_capture_temp = True
        self.add_event()
        self.assertEqual(self.worker.run_once(), "silent")
        self.assertEqual(list(self.settings.image_dir.iterdir()), [])

    def test_image_cleanup_failure_stops_instead_of_accumulating(self) -> None:
        self.add_event()
        with mock.patch.object(Path, "unlink", side_effect=OSError("private path")):
            with self.assertRaisesRegex(review.ReviewerError, "image_cleanup_failed"):
                self.worker.run_once()
        self.assertNotEqual(list(self.settings.image_dir.iterdir()), [])

        # A subsequent locked startup can remove the crash orphan; the image
        # is never treated as a reason to continue creating more files.
        restarted = self.reviewer(FakeCommands(self.clock))
        self.assertEqual(list(self.settings.image_dir.iterdir()), [])

    def test_meaningful_frame_sends_image_grounded_text_only(self) -> None:
        summary = "A person in a blue jacket is standing beside the kitchen table."
        self.commands.decision = review.AnalysisDecision(
            True, "person", "routine", "high", summary
        )
        self.add_event(event_type="person")
        self.assertEqual(self.worker.run_once(), "sent")
        self.assertEqual(self.commands.messages, [f"Cabin kitchen: {summary}"])
        state = self.worker.state()
        self.assertEqual(state["lastDecision"], "sent")
        self.assertEqual(state["counters"]["messageAttempts"], 1)
        self.assertEqual(state["counters"]["messagesSent"], 1)
        persisted = self.settings.state_path.read_text(encoding="utf-8")
        self.assertNotIn(summary, persisted)
        self.assertNotIn("chat_id", persisted)
        self.assertEqual(list(self.settings.image_dir.iterdir()), [])

    def test_hard_hour_cap_survives_restart_and_skips_model_work(self) -> None:
        self.commands.decision = review.AnalysisDecision(
            True, "person", "routine", "high", "A person is walking through the kitchen."
        )
        self.add_event(event_type="person")
        self.assertEqual(self.worker.run_once(), "sent")
        self.assertEqual(len(self.commands.capture_calls), 1)

        self.clock.value += review.MESSAGE_INTERVAL_SECONDS - 1
        self.write_presence()
        self.add_event(event_type="motion")
        restarted_commands = FakeCommands(self.clock)
        restarted_commands.decision = self.commands.decision
        restarted = self.reviewer(restarted_commands)
        self.assertEqual(restarted.run_once(), "rate_limited")
        self.assertEqual(restarted_commands.capture_calls, [])
        self.assertEqual(restarted_commands.analysis_calls, [])
        self.assertEqual(restarted_commands.messages, [])

        self.clock.value += 1.001
        self.write_presence()
        self.add_event(event_type="person")
        self.assertEqual(restarted.run_once(), "sent")
        self.assertEqual(len(restarted_commands.messages), 1)

    def test_hour_starts_at_send_reservation_after_slow_analysis(self) -> None:
        self.commands.decision = review.AnalysisDecision(
            True, "person", "routine", "high", "A person is beside the kitchen table."
        )
        self.commands.analysis_delay = 125.25
        initial = self.clock()
        self.add_event(event_type="person")
        self.assertEqual(self.worker.run_once(), "sent")
        reserved = review._parse_timestamp(
            self.worker.state()["lastMessageAttemptAt"]
        )
        self.assertAlmostEqual(reserved, initial + 125.25, places=5)

        self.clock.value = reserved + review.MESSAGE_INTERVAL_SECONDS - 0.25
        self.write_presence()
        self.add_event(event_type="person")
        restarted_commands = FakeCommands(self.clock)
        restarted_commands.decision = self.commands.decision
        restarted = self.reviewer(restarted_commands)
        self.assertEqual(restarted.run_once(), "rate_limited")
        self.assertEqual(restarted_commands.capture_calls, [])

    def test_send_failure_burns_slot_and_prevents_retry(self) -> None:
        self.commands.decision = review.AnalysisDecision(
            True, "animal", "notable", "high", "A dog is standing near the kitchen doorway."
        )
        self.commands.send_error = "message_command_failed"
        self.add_event(event_type="person")
        self.assertEqual(self.worker.run_once(), "send_failed")
        self.assertIsNotNone(self.worker.state()["lastMessageAttemptAt"])

        self.clock.value += 30
        self.add_event(event_type="motion")
        restarted = self.reviewer(FakeCommands(self.clock))
        self.assertEqual(restarted.run_once(), "rate_limited")
        self.assertEqual(restarted.commands.capture_calls, [])

    def test_crash_after_reservation_becomes_unknown_and_cannot_duplicate(self) -> None:
        self.commands.decision = review.AnalysisDecision(
            True, "person", "routine", "high", "A person is seated at the kitchen table."
        )
        self.commands.crash_during_send = True
        self.add_event(event_type="person")
        with self.assertRaises(KeyboardInterrupt):
            self.worker.run_once()
        reserved = self.worker.state()
        self.assertEqual(reserved["lastDecision"], "send_reserved")

        self.clock.value += 5
        self.add_event(event_type="motion")
        restarted_commands = FakeCommands(self.clock)
        restarted = self.reviewer(restarted_commands)
        self.assertEqual(restarted.initialize()["lastDecision"], "delivery_unknown")
        self.assertEqual(restarted.run_once(), "rate_limited")
        self.assertEqual(restarted_commands.messages, [])

    def test_failures_and_stale_events_are_silent_and_clean(self) -> None:
        self.commands.capture_error = "capture_command_failed"
        self.add_event()
        self.assertEqual(self.worker.run_once(), "capture_failed")
        self.assertEqual(self.commands.messages, [])
        self.assertEqual(list(self.settings.image_dir.iterdir()), [])

        self.clock.value += review.REVIEW_COOLDOWN_SECONDS + 0.001
        self.commands.capture_error = None
        self.commands.analysis_error = "analysis_command_failed"
        self.add_event()
        self.assertEqual(self.worker.run_once(), "analysis_failed")
        self.assertEqual(self.commands.messages, [])
        self.assertEqual(list(self.settings.image_dir.iterdir()), [])

        self.clock.value += review.REVIEW_COOLDOWN_SECONDS + 0.001
        self.commands.analysis_error = None
        self.add_event(age=review.TRIGGER_MAX_AGE_SECONDS + 1)
        self.assertEqual(self.worker.run_once(), "expired")
        self.assertEqual(self.commands.messages, [])

    def test_review_cooldown_suppresses_repeated_motion_without_capture(self) -> None:
        self.add_event()
        self.assertEqual(self.worker.run_once(), "silent")
        self.clock.value += 60
        self.add_event()
        captures = len(self.commands.capture_calls)
        self.assertEqual(self.worker.run_once(), "review_limited")
        self.assertEqual(len(self.commands.capture_calls), captures)


class AnalysisContractTests(ActivityReviewerTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.image = self.state_dir / "model-input.jpg"
        self.image.write_bytes(b"\xff\xd8\xffx\xff\xd9")
        self.image.chmod(0o600)

    def envelope(self, inner: object, **updates: object) -> bytes:
        outer = {
            "ok": True,
            "capability": "image.describe",
            "transport": "local",
            "provider": "codex",
            "model": "gpt-5.6-sol",
            "outputs": [
                {
                    "path": str(self.image),
                    "text": json.dumps(inner, separators=(",", ":")),
                    "provider": "codex",
                    "model": "gpt-5.6-sol",
                    "kind": "image.description",
                }
            ],
        }
        outer.update(updates)
        return json.dumps(outer).encode("utf-8")

    def test_strict_valid_report_and_silent_decisions(self) -> None:
        report = {
            "should_notify": True,
            "category": "person",
            "urgency": "routine",
            "confidence": "high",
            "summary": "A person is standing beside the kitchen counter.",
        }
        decision = review.parse_analysis(self.envelope(report), self.image)
        self.assertTrue(decision.should_notify)

        silent = {
            "should_notify": False,
            "category": "unknown",
            "urgency": "routine",
            "confidence": "high",
            "summary": "",
        }
        self.assertFalse(review.parse_analysis(self.envelope(silent), self.image).should_notify)

    def test_low_confidence_and_unknown_reports_fail_silent(self) -> None:
        for category, confidence in (("person", "low"), ("unknown", "high")):
            inner = {
                "should_notify": True,
                "category": category,
                "urgency": "routine",
                "confidence": confidence,
                "summary": "A vague shape is near the kitchen table.",
            }
            with self.subTest(category=category, confidence=confidence):
                decision = review.parse_analysis(self.envelope(inner), self.image)
                self.assertFalse(decision.should_notify)
                self.assertEqual(decision.summary, "")

    def test_malformed_or_alert_style_output_is_rejected(self) -> None:
        valid = {
            "should_notify": True,
            "category": "person",
            "urgency": "routine",
            "confidence": "high",
            "summary": "Motion detected by the camera.",
        }
        cases = [
            b"not-json",
            self.envelope({**valid, "extra": True}),
            self.envelope(valid),
            self.envelope({**valid, "summary": "A person is here.\nSecond line."}),
            self.envelope({**valid, "summary": "See https://example.com"}),
            self.envelope({**valid, "should_notify": 1}),
        ]
        for index, payload in enumerate(cases):
            with self.subTest(index=index):
                with self.assertRaises(review.ReviewerError):
                    review.parse_analysis(payload, self.image)

    def test_wrong_model_transport_or_path_is_rejected(self) -> None:
        inner = {
            "should_notify": False,
            "category": "unknown",
            "urgency": "routine",
            "confidence": "high",
            "summary": "",
        }
        other = self.state_dir / "other.jpg"
        other.write_bytes(self.image.read_bytes())
        other.chmod(0o600)
        payloads = [
            self.envelope(inner, transport="gateway"),
            self.envelope(inner, model="other"),
        ]
        path_payload = json.loads(self.envelope(inner))
        path_payload["outputs"][0]["path"] = str(other)
        payloads.append(json.dumps(path_payload).encode("utf-8"))
        for payload in payloads:
            with self.assertRaisesRegex(review.ReviewerError, "analysis_envelope_invalid"):
                review.parse_analysis(payload, self.image)


class PresenceContractTests(ActivityReviewerTestCase):
    def decision(self, *, event_age: float = 10) -> "review.PresenceDecision":
        gate = review.PresenceGate(self.settings)
        return gate.evaluate(
            now=self.clock(),
            event_at=review._timestamp(self.clock() - event_age),
        )

    def test_only_fresh_consistent_confirmed_vacancy_is_active(self) -> None:
        self.assertEqual(self.decision().mode, "active_vacant")

        for occupancy in ("occupied", "possibly_vacant", "unknown"):
            with self.subTest(occupancy=occupancy):
                self.write_presence(
                    occupancy=occupancy,
                    people_consistent=occupancy != "occupied",
                )
                self.assertFalse(self.decision().active)

    def test_stale_future_inconsistent_or_direct_positive_is_shadow(self) -> None:
        cases = (
            {"state_age": review.PRESENCE_MAX_AGE_SECONDS + 1},
            {"state_age": -(review.PRESENCE_FUTURE_SKEW_SECONDS + 1)},
            {"scan_age": review.PRESENCE_MAX_AGE_SECONDS + 1},
            {"crosstown_fresh": False},
            {"people_consistent": False},
            {"cabin_present": True},
        )
        for values in cases:
            with self.subTest(values=values):
                self.write_presence(**values)
                self.assertFalse(self.decision().active)

    def test_event_before_vacancy_transition_is_shadow(self) -> None:
        self.write_presence(changed_age=5)
        self.assertEqual(
            self.decision(event_age=10).mode,
            "shadow_unconfirmed",
        )

    def test_missing_malformed_or_writable_presence_is_untrusted_shadow(self) -> None:
        self.presence_state.unlink()
        self.assertEqual(self.decision().mode, "shadow_untrusted")

        self.presence_state.write_text("not-json", encoding="utf-8")
        self.presence_state.chmod(0o644)
        self.assertEqual(self.decision().mode, "shadow_untrusted")

        self.write_presence()
        self.presence_state.chmod(0o666)
        self.assertEqual(self.decision().mode, "shadow_untrusted")

    def test_strict_live_observation_parser(self) -> None:
        payload = {
            "location": "cabin",
            "timestamp": review._timestamp(self.clock()),
            "presence": {
                "Dylan": {"present": False, "private": "discarded"},
                "Julia": {"present": False},
            },
        }
        self.assertTrue(
            review.parse_presence_observation(
                json.dumps(payload).encode("utf-8"), self.clock()
            )
        )
        payload["presence"]["Dylan"]["present"] = True
        self.assertFalse(
            review.parse_presence_observation(
                json.dumps(payload).encode("utf-8"), self.clock()
            )
        )
        payload["location"] = "crosstown"
        with self.assertRaisesRegex(
            review.ReviewerError, "presence_observation_invalid"
        ):
            review.parse_presence_observation(
                json.dumps(payload).encode("utf-8"), self.clock()
            )


class ProcessCommandSafetyTests(ActivityReviewerTestCase):
    def test_subprocess_output_and_time_are_bounded(self) -> None:
        environment = {"PATH": "/usr/bin:/bin", "HOME": str(self.home)}
        output = review.ProcessCommands._run(
            ["/usr/bin/python3", "-c", "print('ok')"],
            environment=environment,
            timeout=2,
            failure_code="fixture_failed",
        )
        self.assertEqual(output, b"ok\n")

        with self.assertRaisesRegex(review.ReviewerError, "fixture_failed"):
            review.ProcessCommands._run(
                [
                    "/usr/bin/python3",
                    "-c",
                    f"import os; os.write(1, b'x' * {review.MAX_COMMAND_OUTPUT_BYTES + 1})",
                ],
                environment=environment,
                timeout=2,
                failure_code="fixture_failed",
            )

        started = time.monotonic()
        with self.assertRaisesRegex(review.ReviewerError, "fixture_failed"):
            review.ProcessCommands._run(
                ["/usr/bin/python3", "-c", "import time; time.sleep(10)"],
                environment=environment,
                timeout=0.1,
                failure_code="fixture_failed",
            )
        self.assertLess(time.monotonic() - started, 2)


if __name__ == "__main__":
    unittest.main()
