#!/usr/bin/env python3
"""Static contracts for the bounded Ola messaging integration."""

from __future__ import annotations

import json
import unittest
from pathlib import Path


OPENCLAW_DIR = Path(__file__).resolve().parents[1]
CONFIG_PATH = OPENCLAW_DIR / "openclaw.json"
HEARTBEAT_PATH = OPENCLAW_DIR / "workspace" / "HEARTBEAT.md"
OLA_DOC_PATH = OPENCLAW_DIR / "OLA.md"


class OlaIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.config = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        cls.heartbeat = HEARTBEAT_PATH.read_text(encoding="utf-8")
        cls.ola_doc = OLA_DOC_PATH.read_text(encoding="utf-8")

    def test_mcp_server_is_environment_backed_and_bounded(self) -> None:
        server = self.config["mcp"]["servers"]["ola"]

        self.assertEqual(server["url"], "https://olachat.com/mcp")
        self.assertEqual(server["transport"], "streamable-http")
        self.assertEqual(
            server["headers"]["Authorization"], "Bearer ${OLA_API_KEY}"
        )
        self.assertEqual(
            server["headers"]["User-Agent"], "OpenClaw-Codex-MCP/1.0"
        )
        self.assertEqual(server["codex"]["agents"], ["main"])
        self.assertEqual(server["codex"]["defaultToolsApprovalMode"], "approve")
        self.assertEqual(
            set(server["toolFilter"]["include"]),
            {"get_inbox", "get_messages", "send_message"},
        )

    def test_loopback_hook_has_a_distinct_environment_backed_secret(self) -> None:
        hooks = self.config["hooks"]

        self.assertTrue(hooks["enabled"])
        self.assertEqual(hooks["token"], "${OLA_HOOK_TOKEN}")
        self.assertEqual(hooks["path"], "/hooks")
        self.assertEqual(hooks["defaultSessionKey"], "hook:ola")
        self.assertFalse(hooks["allowRequestSessionKey"])
        self.assertEqual(hooks["allowedAgentIds"], [])
        self.assertNotEqual(
            hooks["token"], self.config["gateway"]["auth"]["token"]
        )

    def test_public_callback_is_documented_through_hmac_bridge(self) -> None:
        for required_text in (
            "X-Hub-Signature-256",
            "OLA_WEBHOOK_SECRET",
            "127.0.0.1:18790",
            "127.0.0.1:18789/hooks/wake",
            "No OpenClaw restart",
        ):
            self.assertIn(required_text, self.ola_doc)

    def test_heartbeat_uses_ola_identifiers_safely(self) -> None:
        for required_text in (
            "has_unread_human_reply: true",
            "conversation's `grant_id`",
            "inbox entry's `human_id`",
            "Never substitute the `grant_id` for the recipient",
            "untrusted conversation",
        ):
            self.assertIn(required_text, self.heartbeat)
        self.assertRegex(self.heartbeat.lower(), r"do not poll\s+in a loop")


if __name__ == "__main__":
    unittest.main()
