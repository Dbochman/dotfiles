#!/usr/bin/env python3
"""Fake-only behavior and security tests for the Cat Care dashboard."""

from __future__ import annotations

from io import BytesIO
import importlib.util
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch


REPO_ROOT = Path(__file__).resolve().parents[2]
DASHBOARD_PATH = REPO_ROOT / "openclaw" / "bin" / "cat-dashboard.py"


def load_dashboard(temp_home: Path):
    spec = importlib.util.spec_from_file_location("cat_dashboard_test", DASHBOARD_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Could not load {DASHBOARD_PATH}")
    module = importlib.util.module_from_spec(spec)
    with patch.dict(os.environ, {"HOME": str(temp_home)}):
        spec.loader.exec_module(module)
    return module


class HandlerHarness:
    def __init__(self, module, *, path="/", headers=None, body=b"") -> None:
        self.status = None
        self.response_headers: list[tuple[str, str]] = []
        self.handler = object.__new__(module.DashboardHandler)
        self.handler.path = path
        self.handler.headers = headers or {}
        self.handler.rfile = BytesIO(body)
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


class CatDashboardTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tempdir.cleanup)
        self.dashboard = load_dashboard(Path(self.tempdir.name))

    def command_request(self, *, token=None, origin="http://dashboard.test:8554"):
        body = json.dumps(
            {"device": "whisker", "action": "clean", "selector": "cabin-litter-robot"}
        ).encode()
        headers = {
            "Content-Length": str(len(body)),
            "Content-Type": "application/json",
            "Host": "dashboard.test:8554",
        }
        if origin is not None:
            headers["Origin"] = origin
        if token is not None:
            headers["Authorization"] = f"Bearer {token}"
        return HandlerHarness(self.dashboard, path="/api/command", headers=headers, body=body)

    def test_commands_are_exact_and_bounded(self) -> None:
        self.assertEqual(
            self.dashboard.build_command(
                {"device": "whisker", "action": "clean", "selector": "cabin-litter-robot"}
            ),
            [self.dashboard.LITTER_ROBOT_CLI, "--json", "clean", "cabin-litter-robot"],
        )
        self.assertEqual(
            self.dashboard.build_command(
                {"device": "petlibro", "action": "feed", "selector": "crosstown-feeder", "portions": 2}
            ),
            [self.dashboard.PETLIBRO_CLI, "--json", "feed", "crosstown-feeder", "2"],
        )
        self.assertEqual(
            self.dashboard.build_command(
                {
                    "device": "petlibro",
                    "action": "schedule",
                    "selector": "cabin-feeder",
                    "state": "off",
                }
            ),
            [
                self.dashboard.PETLIBRO_CLI,
                "--json",
                "schedule-set",
                "cabin-feeder",
                "off",
            ],
        )
        invalid = (
            {"device": "whisker", "action": "clean", "selector": "cabin"},
            {"device": "whisker", "action": "reset", "selector": "cabin-litter-robot"},
            {"device": "petlibro", "action": "feed", "selector": "cabin-feeder", "portions": 4},
            {"device": "petlibro", "action": "feed", "selector": "cabin-feeder", "portions": True},
            {"device": "petlibro", "action": "feed", "selector": "cabin-feeder", "portions": 1, "extra": "x"},
            {"device": "petlibro", "action": "schedule", "selector": "cabin", "state": "off"},
            {"device": "petlibro", "action": "schedule", "selector": "cabin-feeder", "state": "pause"},
            {"device": "petlibro", "action": "schedule", "selector": "cabin-feeder", "state": "off", "extra": "x"},
        )
        for payload in invalid:
            with self.subTest(payload=payload), self.assertRaises(ValueError):
                self.dashboard.build_command(payload)

    def test_collectors_request_structured_vendor_views(self) -> None:
        with patch.object(
            self.dashboard,
            "_run_json",
            side_effect=[
                {"ok": True, "robots": [], "pets": []},
                [],
                {"ok": True, "feeder_suspensions": {"sites": {}}},
                {"ok": True, "owner": "bus"},
                {"ok": True, "owner": "bus"},
                {
                    "health": "ok",
                    "database_bytes": 12345,
                    "sources": {
                        "whisker": {
                            "accepted": 2,
                            "observer": {
                                "health": "ok",
                                "sites": {
                                    "cabin": {
                                        "enabled": True,
                                        "baselined": True,
                                        "health": "ok",
                                        "poll_age_seconds": 12,
                                    },
                                    "crosstown": {
                                        "enabled": True,
                                        "baselined": True,
                                        "health": "ok",
                                        "poll_age_seconds": 13,
                                    },
                                },
                            },
                        }
                    },
                    "actions": {"counts": {"pending": 0, "outcome_unknown": 0}},
                },
            ],
        ) as run:
            whisker = self.dashboard.collect_whisker()
            petlibro = self.dashboard.collect_petlibro()
            automation = self.dashboard.collect_feeder_automation()
            transfer = self.dashboard.collect_transfer_coverage()
        self.assertTrue(whisker["ok"])
        self.assertEqual(petlibro, {"ok": True, "devices": []})
        self.assertTrue(automation["ok"])
        self.assertEqual(
            automation["feeding_schedule_owners"],
            {"crosstown": "bus", "cabin": "bus"},
        )
        self.assertEqual(
            transfer,
            {
                "ok": True,
                "bus_health": "ok",
                "observer_health": "ok",
                "coverage_ready": True,
                "sites": {
                    "cabin": {
                        "enabled": True,
                        "baselined": True,
                        "health": "ok",
                        "poll_age_seconds": 12,
                    },
                    "crosstown": {
                        "enabled": True,
                        "baselined": True,
                        "health": "ok",
                        "poll_age_seconds": 13,
                    },
                },
                "accepted_events": 2,
                "pending_actions": 0,
                "unknown_actions": 0,
            },
        )
        self.assertNotIn("database_bytes", transfer)
        self.assertEqual(
            run.call_args_list[0].args[0],
            [self.dashboard.LITTER_ROBOT_CLI, "--json", "overview", "14"],
        )
        self.assertEqual(
            run.call_args_list[1].args[0],
            [self.dashboard.PETLIBRO_CLI, "--json", "status"],
        )
        self.assertEqual(
            run.call_args_list[2].args[0],
            [self.dashboard.HOME_EVENT_ACTION_CLI, "status"],
        )
        self.assertEqual(
            run.call_args_list[3].args[0],
            [
                self.dashboard.HOME_EVENT_ACTION_CLI,
                "ownership",
                "--site",
                "crosstown",
                "--target",
                "feeding_schedule",
            ],
        )
        self.assertEqual(
            run.call_args_list[4].args[0],
            [
                self.dashboard.HOME_EVENT_ACTION_CLI,
                "ownership",
                "--site",
                "cabin",
                "--target",
                "feeding_schedule",
            ],
        )
        self.assertEqual(
            run.call_args_list[5].args[0],
            [self.dashboard.HOME_EVENTCTL_CLI, "status"],
        )

    def test_transfer_coverage_stays_ready_when_unrelated_bus_health_is_degraded(self) -> None:
        with patch.object(
            self.dashboard,
            "_run_json",
            return_value={
                "health": "degraded",
                "sources": {
                    "whisker": {
                        "accepted": 2,
                        "observer": {
                            "health": "ok",
                            "sites": {
                                "cabin": {
                                    "enabled": True,
                                    "baselined": True,
                                    "health": "ok",
                                    "poll_age_seconds": 12,
                                },
                                "crosstown": {
                                    "enabled": True,
                                    "baselined": True,
                                    "health": "ok",
                                    "poll_age_seconds": 13,
                                },
                            },
                        },
                    }
                },
                "actions": {"counts": {"pending": 0, "outcome_unknown": 0}},
            },
        ):
            transfer = self.dashboard.collect_transfer_coverage()

        self.assertEqual(transfer["bus_health"], "degraded")
        self.assertTrue(transfer["coverage_ready"])

    def test_transfer_summary_describes_confirmed_cabin_state_in_plain_english(
        self,
    ) -> None:
        summary = self.dashboard.summarize_transfer_state(
            {
                "ok": True,
                "feeding_schedule_owners": {"cabin": "bus", "crosstown": "bus"},
                "feeder_suspensions": {
                    "sites": {
                        "crosstown": {
                            "selector": "crosstown-feeder",
                            "phase": "suspended",
                            "attention": True,
                            "last_error": "feeder_readback_unavailable",
                        }
                    }
                },
            },
            {
                "ok": True,
                "coverage_ready": True,
                "pending_actions": 0,
                "unknown_actions": 0,
            },
            {
                "ok": True,
                "devices": [
                    {
                        "selector": "cabin-feeder",
                        "scheduleReadback": "verified",
                        "scheduleEnabled": True,
                    },
                    {
                        "selector": "crosstown-feeder",
                        "scheduleReadback": "verified",
                        "scheduleEnabled": False,
                    },
                ],
            },
        )

        self.assertEqual(summary["label"], "Working")
        self.assertEqual(summary["title"], "Cats are at Cabin")
        self.assertEqual(summary["sites"]["cabin"]["label"], "Meals on")
        self.assertEqual(summary["sites"]["crosstown"]["label"], "Meals paused")
        self.assertFalse(summary["attention"])
        self.assertIn("turn back on automatically", summary["description"])
        self.assertNotIn("feeder_readback_unavailable", json.dumps(summary))

    def test_transfer_summary_keeps_an_unconfirmed_change_as_attention(self) -> None:
        summary = self.dashboard.summarize_transfer_state(
            {
                "ok": True,
                "feeding_schedule_owners": {"cabin": "bus", "crosstown": "bus"},
                "feeder_suspensions": {"sites": {}},
            },
            {
                "ok": True,
                "coverage_ready": True,
                "pending_actions": 0,
                "unknown_actions": 1,
            },
            {"ok": True, "devices": []},
        )

        self.assertTrue(summary["attention"])
        self.assertEqual(summary["label"], "Needs review")
        self.assertEqual(summary["title"], "A feeder change could not be confirmed")
        self.assertNotIn("outcome_unknown", json.dumps(summary))

    def test_petlibro_collector_uses_exact_schedule_readback(self) -> None:
        with patch.object(
            self.dashboard,
            "_run_json",
            side_effect=[
                [
                    {
                        "selector": "cabin-feeder",
                        "type": "feeder",
                        "scheduleEnabled": True,
                    },
                    {"selector": "cabin-fountain", "type": "fountain"},
                ],
                {
                    "success": True,
                    "selector": "cabin-feeder",
                    "scheduleEnabled": False,
                    "enabledMealCount": 2,
                    "observedAt": "2026-08-27T12:00:00Z",
                },
                {
                    "success": True,
                    "selector": "cabin-feeder",
                    "site": "cabin",
                    "feedings": [
                        {"occurredAt": "2026-08-27T09:00:00Z", "portions": 4}
                    ],
                    "observedAt": "2026-08-27T12:00:00Z",
                },
            ],
        ) as run:
            payload = self.dashboard.collect_petlibro()

        feeder = payload["devices"][0]
        self.assertFalse(feeder["scheduleEnabled"])
        self.assertEqual(feeder["enabledMealCount"], 2)
        self.assertEqual(feeder["scheduleReadback"], "verified")
        self.assertEqual(feeder["scheduleObservedAt"], "2026-08-27T12:00:00Z")
        self.assertEqual(feeder["feedingHistoryReadback"], "verified")
        self.assertEqual(
            feeder["recentScheduledFeedings"],
            [{"occurredAt": "2026-08-27T09:00:00Z", "portions": 4}],
        )
        self.assertEqual(
            run.call_args_list[1].args[0],
            [
                self.dashboard.PETLIBRO_CLI,
                "--json",
                "schedule-state",
                "cabin-feeder",
            ],
        )
        self.assertEqual(
            run.call_args_list[2].args[0],
            [
                self.dashboard.PETLIBRO_CLI,
                "--json",
                "feeding-history",
                "cabin-feeder",
                "14",
            ],
        )

    def test_petlibro_collector_fails_closed_on_invalid_schedule_readback(self) -> None:
        with patch.object(
            self.dashboard,
            "_run_json",
            side_effect=[
                [{"selector": "cabin-feeder", "type": "feeder", "scheduleEnabled": True}],
                {"ok": False, "error": "provider unavailable"},
                {"ok": False, "error": "provider unavailable"},
            ],
        ):
            payload = self.dashboard.collect_petlibro()

        feeder = payload["devices"][0]
        self.assertIsNone(feeder["scheduleEnabled"])
        self.assertIsNone(feeder["enabledMealCount"])
        self.assertEqual(feeder["scheduleReadback"], "unavailable")
        self.assertEqual(feeder["recentScheduledFeedings"], [])
        self.assertEqual(feeder["feedingHistoryReadback"], "unavailable")

    def test_petlibro_collector_preserves_verified_master_state(self) -> None:
        with patch.object(
            self.dashboard,
            "_run_json",
            side_effect=[
                [
                    {
                        "selector": "cabin-feeder",
                        "type": "feeder",
                        "scheduleEnabled": False,
                        "scheduleState": "disabled",
                    }
                ],
                {"ok": False, "error": "meal list unavailable"},
                {"ok": False, "error": "history unavailable"},
            ],
        ):
            payload = self.dashboard.collect_petlibro()

        feeder = payload["devices"][0]
        self.assertFalse(feeder["scheduleEnabled"])
        self.assertIsNone(feeder["enabledMealCount"])
        self.assertEqual(feeder["scheduleReadback"], "master_verified")
        self.assertIsInstance(feeder["scheduleObservedAt"], str)

    def test_cat_activity_collapses_visits_and_combines_feedings_and_moves(self) -> None:
        activity = self.dashboard.build_cat_activity(
            {
                "pets": [
                    {
                        "name": "Burrito",
                        "recent_weights": [
                            {
                                "timestamp": "2026-08-30T13:23:28Z",
                                "weight_lbs": 10.49,
                            }
                        ],
                    }
                ],
                "robots": [
                    {
                        "site": "crosstown",
                        "recent_activity": [
                            {
                                "timestamp": "2026-08-30T13:23:28Z",
                                "action": "Weight recorded",
                                "weight_lbs": 10.49,
                            },
                            {
                                "timestamp": "2026-08-30T13:22:06Z",
                                "action": "Cat Detected",
                            },
                            {
                                "timestamp": "2026-08-30T13:32:31Z",
                                "action": "DFILevelPercent",
                            },
                            {
                                "timestamp": "2026-08-30T13:33:36Z",
                                "action": "Clean Cycle Complete",
                            },
                        ],
                    }
                ],
            },
            {
                "devices": [
                    {
                        "selector": "cabin-feeder",
                        "recentScheduledFeedings": [
                            {"occurredAt": "2026-08-30T09:01:16Z", "portions": 4}
                        ],
                    }
                ]
            },
            {
                "cat_transfers": {
                    "recent": [
                        {
                            "origin_site": "crosstown",
                            "destination_site": "cabin",
                            "occurred_at": "2026-08-29T23:23:23Z",
                            "schedule_changed": True,
                        }
                    ]
                }
            },
        )

        self.assertEqual(
            [event["kind"] for event in activity],
            ["litter_visit", "scheduled_feeding", "cat_move"],
        )
        self.assertEqual(activity[0]["title"], "Burrito used the Litter-Robot")
        self.assertEqual(activity[0]["detail"], "10.49 lb")
        self.assertEqual(activity[1]["title"], "Scheduled feeding")
        self.assertEqual(activity[1]["detail"], "4 portions dispensed")
        self.assertEqual(activity[2]["title"], "Cats moved to Cabin")
        self.assertEqual(activity[2]["sites"], ["crosstown", "cabin"])
        serialized = json.dumps(activity)
        self.assertNotIn("DFILevelPercent", serialized)
        self.assertNotIn("Clean Cycle Complete", serialized)
        self.assertNotIn("Cat Detected", serialized)

    def test_html_is_cat_first_and_embeds_ephemeral_token(self) -> None:
        harness = HandlerHarness(self.dashboard)
        harness.handler._serve_html()
        text = harness.body.decode()
        self.assertEqual(harness.status, 200)
        self.assertIn("<title>Cat Care</title>", text)
        self.assertIn('rel="icon"', text)
        self.assertIn("%F0%9F%90%B1", text)
        self.assertIn("The cats", text)
        self.assertIn("Feeding between homes", text)
        self.assertIn("Automatic feeder switching", text)
        self.assertIn("Will pause automatically when the cats move homes", text)
        self.assertIn("Schedule checked", text)
        self.assertIn("On/off switch checked", text)
        self.assertIn("Cabin meals", text)
        self.assertIn("Crosstown meals", text)
        self.assertIn("Cat activity", text)
        self.assertIn("Litter visits · scheduled feedings · home moves", text)
        self.assertIn("weightSparkline", text)
        self.assertIn("weight-chart", text)
        self.assertIn("readings ·", text)
        self.assertIn("role=\"img\"", text)
        self.assertIn("event.title", text)
        self.assertNotIn("Recent litter-box activity", text)
        self.assertNotIn("Weight recorded ·", text)
        self.assertIn("Scheduled meals", text)
        self.assertIn("Pause schedule", text)
        self.assertIn("Resume schedule", text)
        self.assertIn("Turns back on automatically", text)
        self.assertIn("Managed automatically", text)
        self.assertIn("Manual feeding remains available", text)
        self.assertIn("data-site=\"crosstown\"", text)
        self.assertIn(f"const MUTATION_TOKEN = {json.dumps(self.dashboard.MUTATION_TOKEN)};", text)
        self.assertNotIn(self.dashboard.MUTATION_TOKEN_PLACEHOLDER, text)
        self.assertIn(("Cache-Control", "no-store"), harness.response_headers)
        self.assertIn(("X-Frame-Options", "DENY"), harness.response_headers)
        self.assertNotIn("access-control-allow-origin", harness.header_names)

    def test_mutation_requires_same_origin_bearer(self) -> None:
        missing = self.command_request()
        with patch.object(self.dashboard, "execute_command") as execute:
            missing.handler.do_POST()
        self.assertEqual(missing.status, 401)
        execute.assert_not_called()

        hostile = self.command_request(
            token=self.dashboard.MUTATION_TOKEN, origin="https://attacker.invalid"
        )
        with patch.object(self.dashboard, "execute_command") as execute:
            hostile.handler.do_POST()
        self.assertEqual(hostile.status, 403)
        execute.assert_not_called()

        allowed = self.command_request(token=self.dashboard.MUTATION_TOKEN)
        with patch.object(
            self.dashboard, "execute_command", return_value=(200, {"ok": True})
        ) as execute:
            allowed.handler.do_POST()
        self.assertEqual(allowed.status, 200)
        execute.assert_called_once()


if __name__ == "__main__":
    unittest.main()
