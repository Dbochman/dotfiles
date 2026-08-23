#!/usr/bin/env python3
"""Fake-only security contract tests for the home control dashboard."""

from __future__ import annotations

from io import BytesIO
import importlib.util
import json
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch


REPO_ROOT = Path(__file__).resolve().parents[2]
DASHBOARD_PATH = REPO_ROOT / "openclaw" / "bin" / "home-dashboard.py"


def load_dashboard(temp_home: Path):
    spec = importlib.util.spec_from_file_location(
        "home_dashboard_security_test", DASHBOARD_PATH
    )
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
        return {name.casefold() for name, _value in self.response_headers}


class HomeDashboardSecurityTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tempdir.cleanup)
        self.dashboard = load_dashboard(Path(self.tempdir.name))

    def command_request(self, *, token=None, origin="http://dashboard.test:8558"):
        body = json.dumps(
            {
                "device": "petlibro",
                "action": "feed",
                "args": {"portions": 2},
            }
        ).encode()
        headers = {
            "Content-Length": str(len(body)),
            "Content-Type": "application/json",
            "Host": "dashboard.test:8558",
        }
        if origin is not None:
            headers["Origin"] = origin
        if token is not None:
            headers["Authorization"] = f"Bearer {token}"
        return HandlerHarness(
            self.dashboard,
            path="/api/command",
            headers=headers,
            body=body,
        )

    def test_lan_read_binding_is_preserved(self) -> None:
        self.assertEqual(self.dashboard.BIND_HOST, "0.0.0.0")

    def test_html_embeds_ephemeral_token_and_disables_caching(self) -> None:
        harness = HandlerHarness(self.dashboard)

        harness.handler._serve_html()

        text = harness.body.decode()
        self.assertEqual(harness.status, 200)
        self.assertIn(
            f"const MUTATION_TOKEN = {json.dumps(self.dashboard.MUTATION_TOKEN)};",
            text,
        )
        self.assertNotIn(self.dashboard.MUTATION_TOKEN_PLACEHOLDER, text)
        self.assertIn("'Authorization': `Bearer ${MUTATION_TOKEN}`", text)
        self.assertIn(("Cache-Control", "no-store"), harness.response_headers)
        self.assertIn(("X-Frame-Options", "DENY"), harness.response_headers)
        self.assertNotIn("access-control-allow-origin", harness.header_names)

    def test_mutation_requires_bearer_before_command_execution(self) -> None:
        harness = self.command_request()
        with patch.object(self.dashboard, "execute_command") as execute:
            harness.handler.do_POST()

        self.assertEqual(harness.status, 401)
        execute.assert_not_called()
        self.assertIn(("WWW-Authenticate", "Bearer"), harness.response_headers)
        self.assertNotIn("access-control-allow-origin", harness.header_names)

    def test_same_origin_bearer_allows_ui_and_header_only_clients(self) -> None:
        for origin in ("http://dashboard.test:8558", None):
            with self.subTest(origin=origin):
                harness = self.command_request(
                    token=self.dashboard.MUTATION_TOKEN,
                    origin=origin,
                )
                with patch.object(
                    self.dashboard,
                    "execute_command",
                    return_value=(200, {"success": True}),
                ) as execute:
                    harness.handler.do_POST()

                self.assertEqual(harness.status, 200)
                execute.assert_called_once()
                self.assertNotIn("access-control-allow-origin", harness.header_names)

    def test_cross_origin_mutation_is_denied_even_with_token(self) -> None:
        harness = self.command_request(
            token=self.dashboard.MUTATION_TOKEN,
            origin="https://attacker.invalid",
        )
        with patch.object(self.dashboard, "execute_command") as execute:
            harness.handler.do_POST()

        self.assertEqual(harness.status, 403)
        execute.assert_not_called()

    def test_preflight_and_json_responses_do_not_enable_wildcard_cors(self) -> None:
        options = HandlerHarness(self.dashboard)
        options.handler.do_OPTIONS()
        self.assertEqual(options.status, 204)
        self.assertNotIn("access-control-allow-origin", options.header_names)
        self.assertNotIn("access-control-allow-headers", options.header_names)

        response = HandlerHarness(self.dashboard)
        response.handler._respond(200, {"ok": True})
        self.assertNotIn("access-control-allow-origin", response.header_names)

    def test_nest_midea_and_petlibro_builders_are_exact_and_bounded(self) -> None:
        commands = self.dashboard.COMMANDS
        self.assertEqual(
            commands["hue_crosstown"]["automation_disable"](
                {"name": "Potato Nightlight"}
            ),
            [
                "hue",
                "--crosstown",
                "automation",
                "disable",
                "Potato Nightlight",
            ],
        )
        self.assertEqual(
            commands["nest"]["set"]({"room": "Bedroom", "temp": "72"}),
            ["nest", "set", "Bedroom", "72"],
        )
        self.assertEqual(
            commands["nest"]["mode"]({"room": "Living Room", "mode": "OFF"}),
            ["nest", "mode", "Living Room", "OFF"],
        )
        self.assertEqual(
            commands["nest"]["eco"]({"room": "Solarium"}),
            ["nest", "eco", "Solarium", "on"],
        )
        self.assertEqual(
            commands["nest_camera"]["snap"]({"room": "laundry"}),
            [
                "nest",
                "camera",
                "snap",
                "laundry",
                str(Path(self.dashboard.CAMERA_SNAP_DIR) / "laundry.jpg"),
            ],
        )
        self.assertEqual(
            commands["midea"]["on"]({"alias": "cabin-air-conditioner"}),
            ["midea-ac", "on", "cabin-air-conditioner"],
        )
        self.assertEqual(
            commands["midea"]["temperature"](
                {"alias": "cabin-lil-air-conditioner", "temp": "72"}
            ),
            ["midea-ac", "temperature", "cabin-lil-air-conditioner", "72"],
        )
        self.assertEqual(
            commands["midea"]["mode"](
                {"alias": "cabin-air-conditioner", "mode": "cool"}
            ),
            ["midea-ac", "mode", "cabin-air-conditioner", "cool"],
        )
        self.assertEqual(
            commands["midea"]["fan"](
                {"alias": "cabin-air-conditioner", "fan": "silent"}
            ),
            ["midea-ac", "fan", "cabin-air-conditioner", "silent"],
        )
        self.assertEqual(
            commands["midea"]["eco"](
                {"alias": "cabin-air-conditioner", "state": "off"}
            ),
            ["midea-ac", "eco", "cabin-air-conditioner", "off"],
        )
        for portions in (1, "2", 3):
            with self.subTest(portions=portions):
                self.assertEqual(
                    commands["petlibro"]["feed"]({"portions": portions})[-1],
                    str(portions),
                )

    def test_malicious_or_near_match_arguments_never_spawn(self) -> None:
        cases = (
            {"device": "petlibro", "action": "feed", "args": {"portions": 4}},
            {"device": "petlibro", "action": "feed", "args": {"portions": "2; id"}},
            {"device": "petlibro", "action": "feed ", "args": {"portions": 2}},
            {"device": "nest", "action": "set", "args": {"room": "Bed", "temp": 72}},
            {"device": "nest", "action": "set", "args": {"room": "Bedroom", "temp": "nan"}},
            {"device": "nest", "action": "mode", "args": {"room": "Bedroom", "mode": "heat"}},
            {"device": "nest", "action": "eco", "args": {"room": "Bedroom", "mode": "OFF"}},
            {"device": "nest_camera", "action": "snap", "args": {"room": "../../tmp"}},
            {"device": "midea", "action": "on", "args": {"alias": "air-conditioner"}},
            {"device": "midea", "action": "on", "args": {"alias": "cabin-air-conditioner; id"}},
            {"device": "midea", "action": "temperature", "args": {"alias": "cabin-air-conditioner", "temp": "nan"}},
            {"device": "midea", "action": "temperature", "args": {"alias": "cabin-air-conditioner", "temp": 59}},
            {"device": "midea", "action": "mode", "args": {"alias": "cabin-air-conditioner", "mode": "Cool"}},
            {"device": "midea", "action": "fan", "args": {"alias": "cabin-air-conditioner", "fan": "turbo"}},
            {"device": "midea", "action": "eco", "args": {"alias": "cabin-air-conditioner", "state": True}},
            {"device": "hue_crosstown", "action": "automation_disable", "args": {"name": "Potato"}},
            {"device": "hue_cabin", "action": "automation_enable", "args": {"name": "Potato Nightlight"}},
        )
        with patch.object(self.dashboard.subprocess, "run") as run:
            for payload in cases:
                with self.subTest(payload=payload):
                    status, response = self.dashboard.execute_command(payload)
                    self.assertEqual(status, 400)
                    self.assertFalse(response["success"])
        run.assert_not_called()

    def test_midea_collector_uses_safe_json_status_contract(self) -> None:
        expected = {"ok": True, "devices": []}
        with patch.object(
            self.dashboard,
            "_run_cli",
            return_value=expected,
        ) as run:
            result = self.dashboard.collect_midea()

        self.assertEqual(result, expected)
        run.assert_called_once_with(
            ["midea-ac", "status", "--json"],
            parse_json=True,
        )

    def test_hue_collector_combines_rooms_and_safe_automation_inventory(self) -> None:
        with patch.object(
            self.dashboard,
            "_run_cli",
            side_effect=[
                {"raw": "Bedroom  OFF  0%  1 lights  400 mired"},
                {
                    "ok": True,
                    "site": "crosstown",
                    "automations": [
                        {
                            "name": "Potato Nightlight",
                            "enabled": True,
                            "status": "running",
                            "schedule": {"recurrence": "daily", "when": "22:00"},
                        }
                    ],
                },
                {
                    "ok": True,
                    "automation_suspensions": {
                        "active_sites": ["crosstown"],
                        "latest": {"outcome": "suspended"},
                    },
                },
            ],
        ):
            result = self.dashboard.collect_hue_crosstown()

        self.assertEqual(result["automations"][0]["name"], "Potato Nightlight")
        self.assertNotIn("id", result["automations"][0])
        self.assertTrue(result["vacancy_automation"]["active"])

    def test_hue_ui_renders_guarded_automation_controls(self) -> None:
        html = self.dashboard.DASHBOARD_HTML

        self.assertIn("function renderHue(result, device)", html)
        self.assertIn("automation_disable", html)
        self.assertIn("automation_enable", html)
        self.assertIn("Vacancy management is active", html)

    def test_midea_ui_is_source_specific_and_exact_alias_only(self) -> None:
        html = self.dashboard.DASHBOARD_HTML

        self.assertIn('id="mideaContent"', html)
        self.assertIn('data-device="midea"', html)
        self.assertIn('value="cabin-air-conditioner"', html)
        self.assertIn('value="cabin-lil-air-conditioner"', html)
        self.assertIn(
            "filter((room) => !room.source || room.source === 'nest')",
            html,
        )
        self.assertIn("else if (device === 'midea')", html)
        self.assertIn("data.result && data.result.status", html)

    def test_midea_command_reuses_verified_readback_without_second_poll(self) -> None:
        self.dashboard.STATUS_CACHE["midea"] = {
            "data": {
                "ok": True,
                "devices": [
                    {"alias": "cabin-air-conditioner", "online": True, "mode": "cool"},
                    {"alias": "cabin-lil-air-conditioner", "online": True, "mode": "cool"},
                ],
            },
            "timestamp": 1,
        }
        verified = {
            "ok": True,
            "alias": "cabin-air-conditioner",
            "command": "mode",
            "changed": True,
            "verified": True,
            "status": {
                "alias": "cabin-air-conditioner",
                "online": True,
                "mode": "fan",
            },
        }
        completed = SimpleNamespace(
            returncode=0,
            stdout=json.dumps(verified),
            stderr="",
        )

        with patch.object(
            self.dashboard.subprocess,
            "run",
            return_value=completed,
        ) as run:
            status, response = self.dashboard.execute_command(
                {
                    "device": "midea",
                    "action": "mode",
                    "args": {"alias": "cabin-air-conditioner", "mode": "fan"},
                }
            )

        self.assertEqual(status, 200)
        self.assertTrue(response["success"])
        self.assertEqual(response["result"], verified)
        run.assert_called_once_with(
            ["midea-ac", "mode", "cabin-air-conditioner", "fan"],
            capture_output=True,
            timeout=self.dashboard.COMMAND_TIMEOUT_SECONDS,
            text=True,
        )
        cached = self.dashboard.STATUS_CACHE["midea"]["data"]["devices"]
        self.assertEqual(
            {item["alias"]: item["mode"] for item in cached},
            {
                "cabin-air-conditioner": "fan",
                "cabin-lil-air-conditioner": "cool",
            },
        )


if __name__ == "__main__":
    unittest.main()
