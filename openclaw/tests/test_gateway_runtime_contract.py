import hashlib
import json
from pathlib import Path
import plistlib
import re
import stat
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
WRAPPER = ROOT / "OpenClawGateway.app/Contents/MacOS/OpenClawGateway"
PULL = ROOT / "bin/dotfiles-pull.command"
LIBRARY = ROOT / "lib/deployment.sh"
RECOVERY = ROOT / "launchagents/ai.openclaw.gateway.plist"
NODE_DIRECTORY = "/opt/homebrew/opt/node@24/bin"
ENTRY = "/opt/homebrew/lib/node_modules/openclaw/dist/index.js"
SKILLS = ("opentable", "restaurant-book", "restaurant-snipe", "resy")


class GatewayRuntimeContractTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.home = Path(self.temporary.name)
        self.state = self.home / ".openclaw/state"
        self.state.mkdir(parents=True)
        self.receipt = self.state / "gateway-wrapper.sha256"
        self.node_directory = self.home / "node24/bin"
        self.node_directory.mkdir(parents=True)
        self.environment = {
            "HOME": str(self.home),
            "PATH": "/usr/bin:/bin:/usr/sbin:/sbin",
            "PYTHONDONTWRITEBYTECODE": "1",
        }

    def run_shell(self, script, *arguments, environment=None):
        return subprocess.run(
            ["/bin/bash", "-c", script, "gateway-test", *map(str, arguments)],
            env=self.environment if environment is None else environment,
            cwd=self.home,
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )

    def wrapper_fixture(self):
        source = WRAPPER.read_text()
        self.assertIn(NODE_DIRECTORY, source)
        source = source.replace("/Users/dbochman", str(self.home))
        source = source.replace(NODE_DIRECTORY, str(self.node_directory))
        source = source.replace(ENTRY, str(self.home / "index.js"))
        wrapper = self.home / "gateway"
        wrapper.write_text(source)
        cache = self.home / ".openclaw/.secrets-cache"
        cache.write_text(
            'printf loaded > "$HOME/cache-read"\n'
            'OPENCLAW_GATEWAY_TOKEN="synthetic-token"\n'
        )
        cache.chmod(0o600)
        return wrapper

    def test_path_contracts_agree_and_keep_other_consumers_on_node22(self):
        wrapper_path = re.search(
            r'^export PATH="([^"]+)"$', WRAPPER.read_text(), re.M
        ).group(1)
        pull_path = re.search(
            r'^GATEWAY_RUNTIME_PATH="([^"]+)"$', PULL.read_text(), re.M
        ).group(1)
        recovery = plistlib.loads(RECOVERY.read_bytes())
        self.assertEqual(wrapper_path, pull_path)
        self.assertEqual(
            wrapper_path.replace("$HOME", "/Users/dbochman"),
            recovery["EnvironmentVariables"]["PATH"],
        )
        self.assertEqual(
            wrapper_path.split(":")[:2], ["$HOME/.openclaw/bin", NODE_DIRECTORY]
        )
        self.assertNotIn("node@22", wrapper_path)
        self.assertIn(
            f"exec {NODE_DIRECTORY}/node {ENTRY} gateway --port 18789",
            WRAPPER.read_text(),
        )
        self.assertIn(
            "/opt/homebrew/opt/node@22/bin", (ROOT / "bin/oauth-refresh.sh").read_text()
        )

    def test_missing_node24_stops_wrapper_before_cache_and_logs(self):
        wrapper = self.wrapper_fixture()
        result = self.run_shell('/bin/bash "$1"', wrapper)
        self.assertEqual(result.returncode, 78, result.stderr)
        self.assertIn("Node24", result.stderr)
        self.assertFalse((self.home / "cache-read").exists())
        self.assertFalse((self.home / ".openclaw/logs").exists())

    def test_nonexecutable_node24_is_not_used(self):
        wrapper = self.wrapper_fixture()
        (self.node_directory / "node").write_text("not executable")
        result = self.run_shell('/bin/bash "$1"', wrapper)
        self.assertEqual(result.returncode, 78, result.stderr)
        self.assertFalse((self.home / "cache-read").exists())

    def test_wrapper_executes_absolute_node_not_path_shadow(self):
        wrapper = self.wrapper_fixture()
        node = self.node_directory / "node"
        node.write_text('#!/bin/bash\nprintf "node24-fixture\\n"\nprintf "%s\\n" "$@"\n')
        node.chmod(0o700)
        shadow = self.home / ".openclaw/bin/node"
        shadow.parent.mkdir()
        shadow.write_text('#!/bin/bash\nprintf "wrong-node\\n"\nexit 99\n')
        shadow.chmod(0o700)
        result = self.run_shell('/bin/bash "$1"', wrapper)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            result.stdout.splitlines(),
            ["node24-fixture", str(self.home / "index.js"), "gateway", "--port", "18789"],
        )
        self.assertNotIn("synthetic-token", result.stdout + result.stderr)

    def test_missing_node24_stops_deployer_before_git_or_writes(self):
        source = PULL.read_text()
        guard = source[:source.index('LOG="$HOME/.openclaw/logs/dotfiles-pull.log"')]
        self.assertIn(NODE_DIRECTORY, guard)
        guard = guard.replace(NODE_DIRECTORY, str(self.node_directory))
        result = self.run_shell(guard + '\nprintf unsafe-continuation\n')
        self.assertEqual(result.returncode, 78, result.stderr)
        self.assertNotIn("unsafe-continuation", result.stdout)
        self.assertFalse(self.receipt.exists())

    def test_deployer_skill_rpc_uses_absolute_node24(self):
        source = PULL.read_text()
        block = source[
            source.index("read_active_gateway_skills() {"):
            source.index("GATEWAY_SKILLS_READY=0")
        ]
        self.assertIn(f"{NODE_DIRECTORY}/node {ENTRY}", block)
        block = block.replace(NODE_DIRECTORY, str(self.node_directory))
        block = block.replace(ENTRY, str(self.home / "index.js"))
        node = self.node_directory / "node"
        node.write_text('#!/bin/bash\nprintf "%s\\n" "$@"\n')
        node.chmod(0o700)
        cache = self.home / "synthetic-cache"
        cache.write_text('OPENCLAW_GATEWAY_TOKEN="synthetic-secret"\n')
        cache.chmod(0o600)
        result = self.run_shell(
            'GATEWAY_SECRETS_CACHE="$1"\n' + block + '\nread_active_gateway_skills',
            cache,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            result.stdout.splitlines(),
            [str(self.home / "index.js"), "gateway", "call", "skills.status",
             "--json", "--timeout", "5000"],
        )
        self.assertNotIn("synthetic-secret", result.stdout + result.stderr)

    def test_receipt_comparison_rejects_missing_mismatched_unsafe_and_symlink(self):
        source = PULL.read_text()
        block = source[
            source.index('GATEWAY_APP_WRAPPER="$REPO/'):
            source.index('# shellcheck source=../lib/deployment.sh')
        ]
        repository = self.home / "repo"
        wrapper = repository / "openclaw/OpenClawGateway.app/Contents/MacOS/OpenClawGateway"
        wrapper.parent.mkdir(parents=True)
        wrapper.write_text("synthetic wrapper\n")
        digest = hashlib.sha256(wrapper.read_bytes()).hexdigest()
        conditions = (
            ("missing", "1"), ("mismatch", "1"), ("unsafe", "1"),
            ("symlink", "1"), ("matching", "0"),
        )
        for condition, expected in conditions:
            with self.subTest(condition=condition):
                if self.receipt.exists() or self.receipt.is_symlink():
                    self.receipt.unlink()
                if condition == "symlink":
                    target = self.home / "other-receipt"
                    target.write_text(digest + "\n")
                    target.chmod(0o600)
                    self.receipt.symlink_to(target)
                elif condition != "missing":
                    value = "0" * 64 if condition == "mismatch" else digest
                    self.receipt.write_text(value + "\n")
                    self.receipt.chmod(0o644 if condition == "unsafe" else 0o600)
                result = self.run_shell(
                    'REPO="$1"\n' + block + '\nprintf "%s" "$GATEWAY_RESTART_REQUIRED"',
                    repository,
                )
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(result.stdout, expected)

    def run_skill_gate(self, payload, *, health=True, rpc=True, changed=True):
        source = PULL.read_text()
        block = source[
            source.index("GATEWAY_SKILLS_READY=0"):
            source.index("# Finish deployment and self-update")
        ]
        self.assertIn("/bin/sleep 1", block)
        block = block.replace("/bin/sleep 1", ":")
        response = self.home / "response.json"
        response.write_text(json.dumps(payload))
        self.receipt.write_text("previous-receipt\n")
        self.receipt.chmod(0o600)
        script = '\n'.join((
            'source "$1"',
            'GATEWAY_WRAPPER_HASH_STATE="$2"',
            'GATEWAY_WRAPPER_HASH="' + "a" * 64 + '"',
            'LOG="$HOME/test.log"',
            'GATEWAY_REQUIRED_SKILLS=(opentable restaurant-book restaurant-snipe resy)',
            f'GATEWAY_READY={int(health)}',
            f'GATEWAY_RESTART_REQUIRED={int(changed)}',
            'DEPLOYMENT_SMOKE_FAILED=0',
            'RESPONSE="$3"',
            'read_active_gateway_skills() { /bin/cat "$RESPONSE"; return ' + str(0 if rpc else 1) + '; }',
            block,
            'printf "%s" "$DEPLOYMENT_SMOKE_FAILED"',
        ))
        return self.run_shell(script, LIBRARY, self.receipt, response)

    def ready_skills(self):
        return {
            "skills": [
                {"name": name, "eligible": True, "modelVisible": True}
                for name in SKILLS
            ]
        }

    def test_healthy_live_skill_gate_publishes_private_receipt(self):
        result = self.run_skill_gate(self.ready_skills())
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "0")
        self.assertEqual(self.receipt.read_text(), "a" * 64 + "\n")
        self.assertEqual(stat.S_IMODE(self.receipt.stat().st_mode), 0o600)
        self.assertEqual(list(self.state.glob(".gateway-wrapper.sha256.*")), [])

    def test_failed_health_rpc_or_skills_never_replace_receipt(self):
        invisible = {
            "skills": [
                {"name": name, "eligible": True, "modelVisible": False}
                for name in SKILLS
            ]
        }
        cases = (
            (self.ready_skills(), False, True), (self.ready_skills(), True, False),
            ({}, True, True), ({"skills": []}, True, True), (invisible, True, True),
        )
        for payload, health, rpc in cases:
            with self.subTest(payload=payload, health=health, rpc=rpc):
                result = self.run_skill_gate(payload, health=health, rpc=rpc)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(result.stdout, "1")
                self.assertEqual(self.receipt.read_text(), "previous-receipt\n")

    def test_unchanged_wrapper_still_checks_live_skills(self):
        result = self.run_skill_gate({}, changed=False)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "1")
        self.assertEqual(self.receipt.read_text(), "previous-receipt\n")


if __name__ == "__main__":
    unittest.main()
