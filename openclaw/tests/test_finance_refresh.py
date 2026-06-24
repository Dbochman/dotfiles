"""Regression tests for the unified finance refresh wrappers."""

import importlib.util
import json
import os
from pathlib import Path
import stat
import tempfile
import unittest
from unittest.mock import patch


BIN_DIR = Path(__file__).resolve().parents[1] / "bin"


def load_script(name, filename):
    spec = importlib.util.spec_from_file_location(name, BIN_DIR / filename)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


property_sync = load_script(
    "financial_dashboard_property_value_sync",
    "financial-dashboard-property-value-sync.py",
)
finance_refresh = load_script("finance_refresh", "finance-refresh.py")


class PropertyValueWrapperTests(unittest.TestCase):
    def test_secret_cache_parser_supports_shell_escaped_values(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            cache = Path(temporary_directory) / ".secrets-cache"
            cache.write_text("OTHER=value\nRENTCAST_API_KEY=abc\\ def\n", encoding="utf-8")
            cache.chmod(0o600)
            with patch.object(property_sync, "SECRETS_CACHE", cache):
                self.assertEqual(property_sync.read_cached_secret("RENTCAST_API_KEY"), "abc def")

    def test_secret_cache_rejects_group_readable_file(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            cache = Path(temporary_directory) / ".secrets-cache"
            cache.write_text("RENTCAST_API_KEY=secret\n", encoding="utf-8")
            cache.chmod(0o640)
            with patch.object(property_sync, "SECRETS_CACHE", cache):
                with self.assertRaises(PermissionError):
                    property_sync.read_cached_secret("RENTCAST_API_KEY")

    def test_status_file_is_private_and_contains_only_safe_metrics(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            state_dir = Path(temporary_directory)
            status_path = state_dir / "status.json"
            with patch.object(property_sync, "STATE_DIR", state_dir), patch.object(
                property_sync, "STATUS_PATH", status_path
            ):
                property_sync.write_status(
                    "ok",
                    "2026-06-23T10:00:00Z",
                    0,
                    metrics={
                        "configured": 2,
                        "missing_address": 0,
                        "due": 2,
                        "current": 0,
                        "refreshed": 2,
                        "failed": 0,
                        "address": "must not be stored",
                    },
                )
            payload = json.loads(status_path.read_text(encoding="utf-8"))
            self.assertEqual(payload["metrics"]["refreshed"], 2)
            self.assertNotIn("address", payload["metrics"])
            self.assertEqual(stat.S_IMODE(status_path.stat().st_mode), 0o600)


class FinanceRefreshTests(unittest.TestCase):
    COMPONENTS = (
        {"name": "plaid"},
        {"name": "crypto"},
        {"name": "home_equity"},
    )

    def test_home_equity_failure_makes_combined_refresh_partial(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            state_dir = Path(temporary_directory)
            status_path = state_dir / "status.json"
            lock_path = state_dir / ".lock"
            results = [
                {"status": "ok", "exit_code": 0, "attempts": 1},
                {"status": "ok", "exit_code": 0, "attempts": 1},
                {"status": "error", "exit_code": 1, "attempts": 2},
            ]
            with patch.object(finance_refresh, "STATE_DIR", state_dir), patch.object(
                finance_refresh, "STATUS_PATH", status_path
            ), patch.object(finance_refresh, "LOCK_PATH", lock_path), patch.object(
                finance_refresh, "COMPONENTS", self.COMPONENTS
            ), patch.object(finance_refresh, "run_component", side_effect=results):
                self.assertEqual(finance_refresh.main(), 1)

            payload = json.loads(status_path.read_text(encoding="utf-8"))
            self.assertEqual(payload["status"], "partial")
            self.assertEqual(payload["components"]["home_equity"]["status"], "error")
            self.assertEqual(payload["components"]["plaid"]["status"], "ok")
            self.assertEqual(payload["components"]["crypto"]["status"], "ok")


if __name__ == "__main__":
    unittest.main()
