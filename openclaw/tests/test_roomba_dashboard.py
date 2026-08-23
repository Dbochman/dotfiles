#!/usr/bin/env python3
"""Fake-only behavior, provenance, and privacy tests for Roomba dashboard."""

from __future__ import annotations

from io import BytesIO
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from datetime import datetime, timezone
from unittest.mock import patch


REPO_ROOT = Path(__file__).resolve().parents[2]
DASHBOARD_PATH = REPO_ROOT / "openclaw" / "bin" / "roomba-dashboard.py"


def load_dashboard(temp_home: Path):
    spec = importlib.util.spec_from_file_location(
        "roomba_dashboard_test", DASHBOARD_PATH
    )
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Could not load {DASHBOARD_PATH}")
    module = importlib.util.module_from_spec(spec)
    with patch.dict(os.environ, {"HOME": str(temp_home)}):
        spec.loader.exec_module(module)
    return module


class HandlerHarness:
    def __init__(self, module) -> None:
        self.status = None
        self.response_headers: list[tuple[str, str]] = []
        self.handler = object.__new__(module.DashboardHandler)
        self.handler.path = "/"
        self.handler.headers = {}
        self.handler.rfile = BytesIO()
        self.handler.wfile = BytesIO()
        self.handler.send_response = self._send_response
        self.handler.send_header = self._send_header
        self.handler.end_headers = lambda: None

    def _send_response(self, status, *_args, **_kwargs) -> None:
        self.status = status

    def _send_header(self, name, value) -> None:
        self.response_headers.append((name, value))

    @property
    def body(self) -> bytes:
        return self.handler.wfile.getvalue()

    @property
    def header_names(self) -> set[str]:
        return {name.casefold() for name, _ in self.response_headers}


class RoombaDashboardTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tempdir.cleanup)
        self.home = Path(self.tempdir.name)
        self.dashboard = load_dashboard(self.home)

    @staticmethod
    def write_private(path: Path, value: object) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value, sort_keys=True) + "\n", encoding="utf-8")
        path.chmod(0o600)

    @staticmethod
    def roomba_state(*, phase="charge", battery=88, bin_full=False) -> dict:
        return {
            "connected": True,
            "batPct": battery,
            "bin": {"present": True, "full": bin_full},
            "cleanMissionStatus": {"phase": phase, "error": 0, "nMssn": 9},
        }

    def test_crosstown_collector_uses_guarded_read_only_cli(self) -> None:
        responses = [
            subprocess.CompletedProcess([], 0, json.dumps(self.roomba_state()), ""),
            subprocess.CompletedProcess(
                [], 0, json.dumps(self.roomba_state(phase="run", battery=65)), ""
            ),
        ]
        with patch.object(self.dashboard.subprocess, "run", side_effect=responses) as run:
            result = self.dashboard.fetch_roomba_status()

        self.assertEqual(result["telemetry"], "live_local")
        self.assertTrue(result["integration"]["ok"])
        self.assertEqual(result["robots"]["10max"]["battery"], 88)
        self.assertEqual(
            [call.args[0] for call in run.call_args_list],
            [
                [self.dashboard.CROSSTOWN_ROOMBA_CLI, "state", "roomba"],
                [self.dashboard.CROSSTOWN_ROOMBA_CLI, "state", "scoomba"],
            ],
        )
        for call in run.call_args_list:
            self.assertNotIn("start", call.args[0])

    def test_cabin_collector_uses_read_only_assistant_status_queries(self) -> None:
        responses = [
            subprocess.CompletedProcess(
                [], 0, 'Sending: "is Floomba running"\nResponse: Floomba isn\'t running.\n', ""
            ),
            subprocess.CompletedProcess(
                [], 0, 'Sending: "is Philly running"\nResponse: Philly is running.\n', ""
            ),
        ]
        with patch.object(self.dashboard.subprocess, "run", side_effect=responses) as run:
            result = self.dashboard.fetch_cabin_roomba_status()

        self.assertEqual(result["telemetry"], "assistant_status")
        self.assertTrue(result["integration"]["ok"])
        self.assertEqual(result["robots"]["floomba"]["phase"], "stop")
        self.assertEqual(result["robots"]["philly"]["phase"], "run")
        self.assertEqual(
            [call.args[0] for call in run.call_args_list],
            [
                [self.dashboard.CABIN_ROOMBA_CLI, "status", "floomba"],
                [self.dashboard.CABIN_ROOMBA_CLI, "status", "philly"],
            ],
        )
        for call in run.call_args_list:
            self.assertNotIn("start", call.args[0])
            self.assertNotIn("stop", call.args[0])
            self.assertNotIn("dock", call.args[0])

    def test_cabin_failure_is_integration_error_not_robot_offline(self) -> None:
        responses = [
            subprocess.CompletedProcess([], 1, "", "secret details must not escape"),
            subprocess.CompletedProcess([], 0, "Response: OK (command sent)\n", ""),
        ]
        with patch.object(self.dashboard.subprocess, "run", side_effect=responses):
            result = self.dashboard.fetch_cabin_roomba_status()

        encoded = json.dumps(result)
        self.assertEqual(result["telemetry"], "assistant_status")
        self.assertFalse(result["integration"]["ok"])
        self.assertEqual(
            result["integration"]["error"], "assistant_status_degraded"
        )
        self.assertEqual(
            result["robots"]["floomba"]["error"], "assistant_status_unavailable"
        )
        self.assertEqual(
            result["robots"]["philly"]["error"], "assistant_status_unverified"
        )
        self.assertNotIn("secret details", encoded)
        self.assertNotIn("stderr", encoded)
        self.assertNotIn("offline", encoded.casefold())

    def test_automation_state_requires_verified_presence_and_is_redacted(self) -> None:
        evaluated_at = "2026-08-22T13:55:00Z"
        state = {
            "timestamp": evaluated_at,
            "crosstown": {
                "occupancy": "confirmed_vacant",
                "fresh": True,
                "stateChangedAt": "2026-08-21T20:00:00Z",
            },
            "cabin": {
                "occupancy": "occupied",
                "fresh": True,
                "stateChangedAt": "2026-08-22T11:00:00Z",
            },
            "people": {"Dylan": {"private_wifi": "secret"}},
        }
        producer = {
            "evaluated_at": evaluated_at,
            "state_hash": self.dashboard._state_hash(state),
        }
        decision = {
            "schema_version": 1,
            "site": "crosstown",
            "local_date": "2026-08-22",
            "source": "scheduled",
            "evaluated_at": "2026-08-22T10:00:00Z",
            "outcome": "recent_cat_activity",
            "reason": "recent_cat_activity",
            "started_robots": [],
            "checks": {"presence": "confirmed_vacant", "recent_cat_activity": True},
            "private_note": "never publish this",
        }
        self.write_private(self.dashboard.PRESENCE_STATE_FILE, state)
        self.write_private(self.dashboard.PRESENCE_PRODUCER_FILE, producer)
        self.write_private(
            self.dashboard.VACANT_ROOMBA_RUNS_DIR / "2026-08-22.json", decision
        )
        self.write_private(self.dashboard.VACANT_ROOMBA_LATEST_FILE, decision)

        result = self.dashboard.collect_automation_state(
            datetime(2026, 8, 22, 14, 0, tzinfo=timezone.utc)
        )

        crosstown = result["homes"]["crosstown"]
        self.assertTrue(crosstown["presenceVerified"])
        self.assertEqual(crosstown["presence"]["occupancy"], "confirmed_vacant")
        self.assertEqual(crosstown["todayDecision"]["outcome"], "recent_cat_activity")
        encoded = json.dumps(result)
        self.assertNotIn("Dylan", encoded)
        self.assertNotIn("private_wifi", encoded)
        self.assertNotIn("never publish this", encoded)

    def test_decision_projection_rejects_unbounded_private_fields(self) -> None:
        value = {
            "local_date": "2026-08-22",
            "source": "scheduled",
            "evaluated_at": "2026-08-22T10:00:00Z",
            "outcome": "failed",
            "reason": "private operator note",
            "decision_outcome": "private outcome",
            "started_robots": [],
            "checks": {"presence": "private evidence", "snooze": "clear"},
        }

        projected = self.dashboard.normalize_decision(value)

        self.assertIsNotNone(projected)
        self.assertIsNone(projected["reason"])
        self.assertIsNone(projected["decision_outcome"])
        self.assertIsNone(projected["checks"]["presence"])

    def test_calendar_merges_daily_vacancy_decision(self) -> None:
        decision = {
            "schema_version": 1,
            "site": "crosstown",
            "local_date": "2026-08-22",
            "source": "scheduled",
            "completed_at": "2026-08-22T10:00:00Z",
            "outcome": "started",
            "reason": None,
            "started_robots": ["roomba", "scoomba"],
            "checks": {},
        }
        self.write_private(
            self.dashboard.VACANT_ROOMBA_RUNS_DIR / "2026-08-22.json", decision
        )

        result = self.dashboard.load_calendar_data(2026, 8)

        self.assertEqual(len(result["crosstown"]["22"]), 1)
        entry = result["crosstown"]["22"][0]
        self.assertEqual(entry["trigger"], "daily_vacancy")
        self.assertEqual(entry["outcome"], "started")
        self.assertEqual(entry["roombas"], ["roomba", "scoomba"])

    def test_snooze_requests_are_exact_and_persist_owner_only(self) -> None:
        invalid = HandlerHarness(self.dashboard)
        invalid.handler._set_snooze("garage", 60)
        self.assertEqual(invalid.status, 400)
        self.assertFalse(Path(self.dashboard.SNOOZE_FILE).exists())

        valid = HandlerHarness(self.dashboard)
        valid.handler._set_snooze("cabin", 60)
        self.assertEqual(valid.status, 200)
        self.assertEqual(Path(self.dashboard.SNOOZE_FILE).stat().st_mode & 0o777, 0o600)

    def test_html_explains_two_home_telemetry_and_automation(self) -> None:
        harness = HandlerHarness(self.dashboard)
        harness.handler._serve_html()
        text = harness.body.decode()

        self.assertEqual(harness.status, 200)
        self.assertIn('data-view="both"', text)
        self.assertIn('data-view="crosstown"', text)
        self.assertIn('data-view="cabin"', text)
        self.assertIn("Live local", text)
        self.assertIn("Assistant status", text)
        self.assertIn("Automation Pause", text)
        self.assertIn("Cleaning &amp; Decision History", text)
        self.assertIn("/api/automation", text)
        self.assertIn(("Cache-Control", "no-store"), harness.response_headers)
        self.assertIn(("X-Frame-Options", "DENY"), harness.response_headers)
        self.assertNotIn("access-control-allow-origin", harness.header_names)


if __name__ == "__main__":
    unittest.main()
