#!/usr/bin/env python3
"""Prevent the retired Ola integration from returning through normal deployment."""
import json
from pathlib import Path
import unittest

OPENCLAW_DIR = Path(__file__).resolve().parents[1]

class OlaRetirementTests(unittest.TestCase):
    def test_no_messaging_or_hook_entry_point(self):
        config = json.loads((OPENCLAW_DIR / "openclaw.json").read_text())
        self.assertNotIn("ola", config.get("mcp", {}).get("servers", {}))
        self.assertEqual(config["hooks"], {"enabled": False})
        self.assertEqual(config["gateway"]["bind"], "loopback")
        self.assertEqual(config["gateway"]["tailscale"]["mode"], "serve")
        self.assertNotIn("OLA_", json.dumps(config))
        heartbeat = (OPENCLAW_DIR / "workspace/HEARTBEAT.md").read_text()
        self.assertNotIn("get_inbox", heartbeat)
        self.assertNotIn("Ola messaging", heartbeat)
        self.assertIn("Health ownership:", heartbeat)

    def test_retired_bridge_cannot_be_redeployed(self):
        for relative in (
            "bin/ola-webhook-bridge.py",
            "bin/ola-webhook-bridge-wrapper.sh",
            "launchagents/ai.openclaw.ola-webhook-bridge.plist",
        ):
            self.assertFalse((OPENCLAW_DIR / relative).exists())
        pull = (OPENCLAW_DIR / "bin/dotfiles-pull.command").read_text()
        self.assertNotIn("OLA_BRIDGE_", pull)
        self.assertNotIn("ola-webhook-bridge", pull)

if __name__ == "__main__":
    unittest.main()
