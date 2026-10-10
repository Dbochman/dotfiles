import json
import subprocess
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class MemoryBootstrapContractTests(unittest.TestCase):
    def test_memory_uses_current_config_surface_and_exact_environment(self):
        config = json.loads((ROOT / 'openclaw.json').read_text())
        self.assertNotIn('memorySearch', config['agents']['defaults'])
        self.assertEqual(config['memory']['search']['remote']['apiKey'], {
            'source': 'exec', 'provider': 'openai_memory_profile', 'id': 'value',
        })
        provider = config['secrets']['providers']['openai_memory_profile']
        self.assertEqual(provider['passEnv'], ['OPENAI_API_KEY'])
        self.assertTrue(provider['command'].endswith('/openai-memory-key'))
        self.assertFalse(provider['jsonOnly'])

    def test_resolver_uses_only_supplied_key_and_does_not_read_auth_store(self):
        path = ROOT / 'bin/openai-memory-key'
        source = path.read_text()
        for forbidden in ['sqlite', 'auth_profile', 'source ', '.secrets-cache', 'op read']:
            self.assertNotIn(forbidden, source)
        result = subprocess.run(
            ['/bin/zsh', str(path)], env={'OPENAI_API_KEY': 'synthetic-test-only'},
            capture_output=True, text=True, timeout=5,
        )
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout, 'synthetic-test-only\n')
        self.assertEqual(result.stderr, '')

    def test_missing_key_fails_closed_without_empty_success(self):
        for environment in [{}, {'OPENAI_API_KEY': ''}]:
            result = subprocess.run(
                ['/bin/zsh', str(ROOT / 'bin/openai-memory-key')], env=environment,
                capture_output=True, text=True, timeout=5,
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual(result.stdout, '')
            self.assertIn('unavailable', result.stderr)

    def test_bootstrap_stays_small_even_after_legacy_tools_migration(self):
        agents = (ROOT / 'workspace/AGENTS.md').read_text()
        tools = (ROOT / 'workspace/TOOLS.md').read_text()
        self.assertLess(len(agents) + len(tools) + 1000, 12000)
        self.assertIn('OPERATIONS.md', agents)
        self.assertIn('OPERATIONS.md', tools)
        self.assertIn('human-confirmation', tools)
        for marker in ['Reachy continuity boundary', 'Don\'t exfiltrate',
                       'ONLY load in main session', 'restaurant-booking-scopes.json']:
            self.assertIn(marker, agents)

    def test_operations_reference_and_deployment_remain_available(self):
        operations = (ROOT / 'workspace/OPERATIONS.md').read_text()
        for section in ['## Browser Work', '## Restaurant Reservations',
                        '## Eight Sleep Pod', '## Native iMessage Recovery',
                        '## RTX 5090 Desktop Compute', '## Presence Detection']:
            self.assertIn(section, operations)
        deploy = (ROOT / 'bin/dotfiles-pull.command').read_text()
        self.assertIn('for f in OPERATIONS.md TOOLS.md HEARTBEAT.md;', deploy)


if __name__ == '__main__':
    unittest.main()
