"""Shell runtime routing tests against this rollout's installed runtime."""
from pathlib import Path
import subprocess
import unittest

ROOT = Path(__file__).parents[2]


class NodeRuntimeShellTests(unittest.TestCase):
    def test_selector_syntax(self):
        subprocess.run(["/bin/zsh", "-n", str(ROOT / "node-runtime.zsh")], check=True)

    def test_selector_is_idempotent(self):
        script = 'source "$1"; first="$PATH"; source "$1"; [[ "$first" == "$PATH" ]]'
        subprocess.run(["/bin/zsh", "-f", "-c", script, "test", str(ROOT / "node-runtime.zsh")], check=True)

    def test_interactive_config_uses_shared_selector(self):
        text = (ROOT / "zshrc").read_text()
        self.assertIn('source "$_node_runtime_selector"', text)
        self.assertIn('${${(%):-%N}:A:h}/node-runtime.zsh', text)
        self.assertNotIn('export PATH="/opt/homebrew/opt/node@22/bin:$PATH"', text)


if __name__ == "__main__":
    unittest.main()
