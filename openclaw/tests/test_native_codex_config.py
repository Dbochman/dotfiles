"""Protect the Astra native-runtime route from authored transport overrides."""

import json
import unittest
from pathlib import Path


class NativeCodexConfigTests(unittest.TestCase):
    def setUp(self):
        self.config = json.loads((Path(__file__).parents[1] / "openclaw.json").read_text())

    def test_astra_retains_explicit_native_runtime(self):
        defaults = self.config["agents"]["defaults"]
        self.assertEqual(defaults["models"]["openai/gpt-6-astra"]["agentRuntime"]["id"], "codex")
        model = next(model for model in self.config["models"]["providers"]["openai"]["models"]
                     if model["id"] == "gpt-6-astra")
        self.assertEqual(model["agentRuntime"]["id"], "codex")

    def test_astra_has_no_authored_transport_compatibility_override(self):
        model = next(model for model in self.config["models"]["providers"]["openai"]["models"]
                     if model["id"] == "gpt-6-astra")
        self.assertEqual(model["compat"], {"supportsReasoningEffort": True})
        self.assertFalse(model.get("headers"))
        self.assertFalse(model.get("params"))


if __name__ == "__main__":
    unittest.main()
