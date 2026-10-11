import hashlib
import json
from pathlib import Path
import plistlib
import re
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
WRAPPER = ROOT / "OpenClawGateway.app/Contents/MacOS/OpenClawGateway"
PULL = ROOT / "bin/dotfiles-pull.command"
LIBRARY = ROOT / "lib/deployment.sh"
RECOVERY = ROOT / "launchagents/ai.openclaw.gateway.plist"
NODE_DIRECTORY = "/Users/dbochman/.local/share/openclaw/runtimes/node-v24.21.0-darwin-arm64/bin"
NODE_SHA256 = "e4b5a3af0e05c75de2eae013904145f40fe7fc2a6e6f17510128bf45cca4e79b"
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
        source = source.replace(NODE_DIRECTORY, str(self.node_directory))
        source = source.replace("/Users/dbochman", str(self.home))
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
        for source in (WRAPPER, PULL):
            self.assertIn(f"readonly GATEWAY_NODE_SHA256={NODE_SHA256}", source.read_text())
        self.assertIn(
            f"exec {NODE_DIRECTORY}/node {ENTRY} gateway --port 18789",
            WRAPPER.read_text(),
        )
        self.assertIn(
            "/opt/homebrew/opt/node@22/bin", (ROOT / "bin/oauth-refresh.sh").read_text()
        )

    def test_missing_node24_stops_wrapper_before_cache_and_logs(self):
        wrapper = self.wrapper_fixture()
        log = self.home / ".openclaw/logs/gateway.log"
        log.parent.mkdir()
        contents = b"unchanged log\n" * 500000
        log.write_bytes(contents)
        result = self.run_shell('/bin/bash "$1"', wrapper)
        self.assertEqual(result.returncode, 78, result.stderr)
        self.assertIn("Node24", result.stderr)
        self.assertFalse((self.home / "cache-read").exists())
        self.assertEqual(log.read_bytes(), contents)
        self.assertFalse(log.with_suffix(".log.tmp").exists())

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
        wrapper.write_text(wrapper.read_text().replace(NODE_SHA256, hashlib.sha256(node.read_bytes()).hexdigest()))
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

    def test_missing_node24_logs_and_stops_before_deployment(self):
        source = PULL.read_text()
        guard = source[:source.index('REPO="$HOME/dotfiles"')]
        self.assertIn(NODE_DIRECTORY, guard)
        guard = guard.replace(NODE_DIRECTORY, str(self.node_directory))
        result = self.run_shell(guard + '\nprintf unsafe-continuation\n')
        self.assertEqual(result.returncode, 78, result.stderr)
        self.assertNotIn("unsafe-continuation", result.stdout)
        self.assertFalse(self.receipt.exists())
        self.assertIn("Required Node24 runtime", (self.home / ".openclaw/logs/dotfiles-pull.log").read_text())

    def test_unapproved_executable_stops_wrapper_and_deployer_before_use(self):
        wrapper = self.wrapper_fixture()
        node = self.node_directory / "node"
        node.write_text('#!/bin/bash\nprintf executed > "$HOME/unapproved-executed"\n')
        node.chmod(0o700)
        result = self.run_shell('/bin/bash "$1"', wrapper)
        self.assertEqual(result.returncode, 78)
        self.assertFalse((self.home / "cache-read").exists())
        guard = PULL.read_text().split('REPO="$HOME/dotfiles"', 1)[0]
        guard = guard.replace(NODE_DIRECTORY, str(self.node_directory))
        result = self.run_shell(guard + '\nprintf unsafe-continuation\n')
        self.assertEqual(result.returncode, 78)
        self.assertNotIn("unsafe-continuation", result.stdout)
        self.assertFalse((self.home / "unapproved-executed").exists())

    def test_deployer_skill_rpc_uses_absolute_node24(self):
        node = self.node_directory / "node"
        node.write_text(
            '#!/bin/bash\n'
            '[ "$OPENCLAW_GATEWAY_URL" = "ws://127.0.0.1:18789" ] || exit 41\n'
            '[ "$OPENCLAW_GATEWAY_TOKEN" = "synthetic-secret" ] || exit 42\n'
            '[ -z "${OTHER_CACHED_SECRET:-}" ] || exit 43\n'
            'printf "%s\\n" "$@"\n'
        )
        node.chmod(0o700)
        cache = self.home / "synthetic-cache"
        cache.write_text('OPENCLAW_GATEWAY_TOKEN="synthetic-secret"\nOTHER_CACHED_SECRET="not-exported"\n')
        cache.chmod(0o600)
        result = self.run_shell(
            'source "$1"\nopenclaw_gateway_read_active_skills "$2" "$3" "$4"',
            LIBRARY, cache, node, self.home / "index.js",
            environment={**self.environment, "OPENCLAW_GATEWAY_URL": "ws://wrong.invalid"},
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

    def run_verifier_dispatch(self, *, status=0, health=True, changed=True):
        source = PULL.read_text()
        block = source[
            source.index("GATEWAY_ACTIVATION_ARGS=("):
            source.index("# Finish deployment and self-update")
        ]
        helper = self.home / "repo/openclaw/bin/gateway_activation.py"
        helper.parent.mkdir(parents=True, exist_ok=True)
        helper.write_text(
            "import json, pathlib, sys\n"
            + "pathlib.Path.home().joinpath('invoked.json').write_text(json.dumps(sys.argv[1:]))\n"
            + f"sys.exit({status})\n"
        )
        script = '\n'.join((
            'REPO="$HOME/repo"',
            'GATEWAY_WRAPPER_HASH="' + "a" * 64 + '"',
            f'GATEWAY_NODE_SHA256="{NODE_SHA256}"',
            'GATEWAY_PID_BEFORE=123',
            'LOG="$HOME/test.log"',
            f'GATEWAY_READY={int(health)}',
            f'GATEWAY_RESTART_REQUIRED={int(changed)}',
            'DEPLOYMENT_SMOKE_FAILED=0',
            block,
            'printf "%s" "$DEPLOYMENT_SMOKE_FAILED"',
        ))
        return self.run_shell(script)

    def test_changed_wrapper_delegates_recording_to_verifier(self):
        result = self.run_verifier_dispatch()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "0")
        arguments = json.loads((self.home / "invoked.json").read_text())
        self.assertIn("--record", arguments)
        self.assertEqual(arguments[-2:], ["--previous-pid", "123"])
        self.assertIn("24.21.0", arguments)
        self.assertEqual(arguments[arguments.index("--expected-node-sha256") + 1],
                         NODE_SHA256)
        self.assertIn("2026.8.35", arguments)

    def test_unhealthy_gateway_never_invokes_verifier(self):
        result = self.run_verifier_dispatch(health=False)
        self.assertEqual(result.stdout, "1")
        self.assertFalse((self.home / "invoked.json").exists())

    def test_verifier_failure_blocks_deployment_acceptance(self):
        result = self.run_verifier_dispatch(status=1)
        self.assertEqual(result.stdout, "1")
        self.assertFalse(self.receipt.exists())

    def test_unchanged_wrapper_still_verifies_without_recording(self):
        result = self.run_verifier_dispatch(changed=False)
        self.assertEqual(result.stdout, "0")
        arguments = json.loads((self.home / "invoked.json").read_text())
        self.assertNotIn("--record", arguments)


if __name__ == "__main__":
    unittest.main()
