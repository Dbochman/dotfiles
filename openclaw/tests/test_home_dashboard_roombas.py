#!/usr/bin/env python3
"""Fake-only Cabin Roomba integration tests for the Home Control Plane."""

from __future__ import annotations

from io import BytesIO
import importlib.util
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from urllib.error import URLError


REPO_ROOT = Path(__file__).resolve().parents[2]
DASHBOARD_PATH = REPO_ROOT / "openclaw" / "bin" / "home-dashboard.py"


def load_dashboard(temp_home: Path):
    spec = importlib.util.spec_from_file_location(
        "home_dashboard_roomba_test", DASHBOARD_PATH
    )
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Could not load {DASHBOARD_PATH}")
    module = importlib.util.module_from_spec(spec)
    with patch.dict(os.environ, {"HOME": str(temp_home)}):
        spec.loader.exec_module(module)
    return module


class HomeDashboardRoombaTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tempdir.cleanup)
        self.dashboard = load_dashboard(Path(self.tempdir.name))

    def test_cabin_status_reuses_bounded_local_dashboard_api(self) -> None:
        payload = {
            "location": "cabin",
            "telemetry": "assistant_status",
            "fetchedAt": "2026-09-03T00:23:27Z",
            "integration": {
                "ok": False,
                "label": "Assistant status",
                "error": "assistant_quota_exhausted",
            },
            "robots": {
                "floomba": {
                    "name": "provider-controlled",
                    "phase": "unknown",
                    "status": "provider diagnostic must not escape",
                    "error": "assistant_quota_exhausted",
                },
                "philly": {
                    "name": "provider-controlled",
                    "phase": "stop",
                    "status": "provider diagnostic must not escape",
                    "error": None,
                },
            },
        }
        response = BytesIO(json.dumps(payload).encode())

        with patch.object(
            self.dashboard, "urlopen", return_value=response
        ) as urlopen, patch.object(self.dashboard, "_run_cli") as run_cli:
            result = self.dashboard.collect_roombas_cabin()

        urlopen.assert_called_once_with(
            self.dashboard.ROOMBA_DASHBOARD_CABIN_STATUS_URL,
            timeout=self.dashboard.LOCAL_DASHBOARD_TIMEOUT_SECONDS,
        )
        run_cli.assert_not_called()
        self.assertEqual(
            result["integration"]["error"], "assistant_quota_exhausted"
        )
        self.assertEqual(result["robots"]["floomba"]["name"], "Floomba")
        self.assertEqual(
            result["robots"]["floomba"]["status"], "Status unavailable"
        )
        self.assertEqual(result["robots"]["philly"]["status"], "Stopped")
        self.assertNotIn("provider diagnostic", json.dumps(result))

    def test_local_dashboard_failure_returns_friendly_bounded_state(self) -> None:
        with patch.object(
            self.dashboard,
            "urlopen",
            side_effect=URLError("private provider detail"),
        ):
            result = self.dashboard.collect_roombas_cabin()

        encoded = json.dumps(result)
        self.assertFalse(result["integration"]["ok"])
        self.assertEqual(
            result["integration"]["error"], "assistant_status_unavailable"
        )
        self.assertEqual(set(result["robots"]), {"floomba", "philly"})
        self.assertNotIn("private provider detail", encoded)


if __name__ == "__main__":
    unittest.main()
