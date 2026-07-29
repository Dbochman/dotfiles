#!/usr/bin/env python3
"""Regression checks for the owner-only iMessage approval posture."""

import json
import unittest
from pathlib import Path


CONFIG_PATH = Path(__file__).resolve().parents[1] / "openclaw.json"
OWNER_SESSION = "agent:main:imessage:direct:dylanbochman@gmail.com"


class TrustedIMessageApprovalTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.config = json.loads(CONFIG_PATH.read_text())

    def test_trusted_runtime_uses_explicit_no_prompt_exec_mode(self) -> None:
        self.assertEqual(self.config["tools"]["exec"]["mode"], "full")

    def test_remaining_approvals_route_only_to_owner_imessage_session(self) -> None:
        for approval_kind in ("exec", "plugin"):
            policy = self.config["approvals"][approval_kind]
            self.assertTrue(policy["enabled"])
            self.assertEqual(policy["mode"], "session")
            self.assertEqual(policy["agentFilter"], ["main"])
            self.assertEqual(policy["sessionFilter"], [OWNER_SESSION])

    def test_open_dms_include_verified_owner_handles(self) -> None:
        self.assertEqual(
            self.config["channels"]["imessage"]["dmPolicy"],
            "open",
        )
        self.assertEqual(
            self.config["channels"]["imessage"]["allowFrom"],
            ["dylanbochman@gmail.com", "+15084234853", "*"],
        )


if __name__ == "__main__":
    unittest.main()
