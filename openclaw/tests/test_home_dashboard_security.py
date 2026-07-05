#!/usr/bin/env python3
"""Fake-only security contract tests for the home control dashboard."""

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

    def test_nest_and_petlibro_builders_are_exact_and_bounded(self) -> None:
        commands = self.dashboard.COMMANDS
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
        )
        with patch.object(self.dashboard.subprocess, "run") as run:
            for payload in cases:
                with self.subTest(payload=payload):
                    status, response = self.dashboard.execute_command(payload)
                    self.assertEqual(status, 400)
                    self.assertFalse(response["success"])
        run.assert_not_called()


if __name__ == "__main__":
    unittest.main()
