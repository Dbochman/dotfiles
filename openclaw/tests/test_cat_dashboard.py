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
        invalid = (
            {"device": "whisker", "action": "clean", "selector": "cabin"},
            {"device": "whisker", "action": "reset", "selector": "cabin-litter-robot"},
            {"device": "petlibro", "action": "feed", "selector": "cabin-feeder", "portions": 4},
            {"device": "petlibro", "action": "feed", "selector": "cabin-feeder", "portions": True},
            {"device": "petlibro", "action": "feed", "selector": "cabin-feeder", "portions": 1, "extra": "x"},
        )
        for payload in invalid:
            with self.subTest(payload=payload), self.assertRaises(ValueError):
                self.dashboard.build_command(payload)

    def test_collectors_request_structured_vendor_views(self) -> None:
        with patch.object(
            self.dashboard,
            "_run_json",
            side_effect=[{"ok": True, "robots": [], "pets": []}, []],
        ) as run:
            whisker = self.dashboard.collect_whisker()
            petlibro = self.dashboard.collect_petlibro()
        self.assertTrue(whisker["ok"])
        self.assertEqual(petlibro, {"ok": True, "devices": []})
        self.assertEqual(
            run.call_args_list[0].args[0],
            [self.dashboard.LITTER_ROBOT_CLI, "--json", "overview", "14"],
        )
        self.assertEqual(
            run.call_args_list[1].args[0],
            [self.dashboard.PETLIBRO_CLI, "--json", "status"],
        )

    def test_html_is_cat_first_and_embeds_ephemeral_token(self) -> None:
        harness = HandlerHarness(self.dashboard)
        harness.handler._serve_html()
        text = harness.body.decode()
        self.assertEqual(harness.status, 200)
        self.assertIn("<title>Cat Care</title>", text)
        self.assertIn("The cats", text)
        self.assertIn("Recent litter-box activity", text)
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
