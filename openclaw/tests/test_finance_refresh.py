"""Regression tests for the unified Plaid and crypto refresh wrapper."""

import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch


BIN_DIR = Path(__file__).resolve().parents[1] / "bin"


def load_script(name, filename):
    spec = importlib.util.spec_from_file_location(name, BIN_DIR / filename)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


finance_refresh = load_script("finance_refresh", "finance-refresh.py")


class FinanceRefreshTests(unittest.TestCase):
    COMPONENTS = (
        {"name": "plaid"},
        {"name": "crypto"},
    )

    def run_refresh(self, results):
        temporary_directory = tempfile.TemporaryDirectory()
        state_dir = Path(temporary_directory.name)
        status_path = state_dir / "status.json"
        lock_path = state_dir / ".lock"
        patches = (
            patch.object(finance_refresh, "STATE_DIR", state_dir),
            patch.object(finance_refresh, "STATUS_PATH", status_path),
            patch.object(finance_refresh, "LOCK_PATH", lock_path),
            patch.object(finance_refresh, "COMPONENTS", self.COMPONENTS),
            patch.object(finance_refresh, "run_component", side_effect=results),
        )
        for active_patch in patches:
            active_patch.start()
            self.addCleanup(active_patch.stop)
        self.addCleanup(temporary_directory.cleanup)
        return status_path

    def test_both_sources_succeed(self):
        status_path = self.run_refresh(
            [
                {"status": "ok", "exit_code": 0, "attempts": 1},
                {"status": "ok", "exit_code": 0, "attempts": 1},
            ]
        )

        self.assertEqual(finance_refresh.main(), 0)
        payload = json.loads(status_path.read_text(encoding="utf-8"))
        self.assertEqual(payload["status"], "ok")
        self.assertEqual(set(payload["components"]), {"plaid", "crypto"})

    def test_one_source_failure_makes_combined_refresh_partial(self):
        status_path = self.run_refresh(
            [
                {"status": "ok", "exit_code": 0, "attempts": 1},
                {"status": "error", "exit_code": 1, "attempts": 2},
            ]
        )

        self.assertEqual(finance_refresh.main(), 1)
        payload = json.loads(status_path.read_text(encoding="utf-8"))
        self.assertEqual(payload["status"], "partial")
        self.assertEqual(payload["components"]["plaid"]["status"], "ok")
        self.assertEqual(payload["components"]["crypto"]["status"], "error")


if __name__ == "__main__":
    unittest.main()
