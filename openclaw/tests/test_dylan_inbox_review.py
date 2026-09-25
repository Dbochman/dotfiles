#!/usr/bin/env python3
"""Verify Dylan's review wrapper never falls back to Julia's account."""

from __future__ import annotations

import importlib.util
import os
import unittest
from pathlib import Path
from unittest import mock


SCRIPT = Path(__file__).parents[1] / "bin" / "dylan-inbox-review.py"
SPEC = importlib.util.spec_from_file_location("dylan_inbox_review", SCRIPT)
assert SPEC and SPEC.loader
wrapper = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(wrapper)


class DylanInboxReviewTests(unittest.TestCase):
    def test_explicit_dylan_identity_and_scope_are_forwarded(self):
        with mock.patch.dict(os.environ, {
            "DYLAN_EMAIL": "dylan@example.invalid", "JULIA_EMAIL": "julia@example.invalid",
            "GOOGLE_WORKSPACE_CLI_ACCOUNT": "wrong@example.invalid",
        }):
            with mock.patch.object(wrapper.review, "main", return_value=0) as review:
                self.assertEqual(wrapper.main(["--scope", "actions"]), 0)
                review.assert_called_once_with(["--scope", "actions"], account="dylan@example.invalid")

    def test_missing_dylan_account_cannot_fall_back_to_julia(self):
        with mock.patch.dict(os.environ, {"DYLAN_EMAIL": "", "JULIA_EMAIL": "julia@example.invalid"}):
            with mock.patch.object(wrapper.review.briefing, "run_command", side_effect=AssertionError("Unexpected Gmail call")):
                with mock.patch("builtins.print") as output:
                    self.assertEqual(wrapper.main([]), 1)
                self.assertIn("missing_account", output.call_args.args[0])


if __name__ == "__main__":
    unittest.main()
