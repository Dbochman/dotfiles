import fcntl
import json
import os
from pathlib import Path
import subprocess
import tempfile
import time
import unittest


SCRIPT = Path(__file__).resolve().parents[1] / "bin" / "oauth-refresh.sh"
FAKE_CLAUDE = '''#!/usr/bin/python3
import json, os, pathlib, sys, time
directory = pathlib.Path(os.environ["CLAUDE_CONFIG_DIR"])
assert str(directory).endswith("/.openclaw/claude-automation")
assert "/usr/bin" not in os.environ["PATH"].split(":")
assert "/opt/homebrew/opt/node@22/bin" in os.environ["PATH"]
assert not os.environ.get("ANTHROPIC_API_KEY")
assert not os.environ.get("CLAUDE_CODE_OAUTH_TOKEN")
mode = os.environ.get("FAKE_MODE", "success")
(directory / "invoked").write_text("yes")
print("private-token-must-not-escape", flush=True)
if mode == "failed": sys.exit(1)
if mode == "unchanged": sys.exit(0)
if "--sso" in sys.argv:
    assert not os.environ.get("CLAUDE_CODE_OAUTH_REFRESH_TOKEN")
else:
    assert os.environ["CLAUDE_CODE_OAUTH_REFRESH_TOKEN"] == "old-refresh"
data = {"claudeAiOauth": {
    "accessToken": "new-access", "refreshToken": "new-refresh",
    "expiresAt": (time.time() + 3600) * 1000,
    "subscriptionType": "enterprise", "scopes": ["user:inference"]}}
if mode == "expired": data["claudeAiOauth"]["expiresAt"] = 1
if mode == "wrong-account": data["claudeAiOauth"]["subscriptionType"] = "pro"
if mode == "nan": data["claudeAiOauth"]["expiresAt"] = float("nan")
path = directory / ".credentials.json"
path.write_text(json.dumps(data));path.chmod(0o600)
'''


class OAuthRefreshTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.home = Path(self.temporary.name)
        self.root = self.home / ".openclaw"
        self.root.mkdir(mode=0o700)
        self.directory = self.root / "claude-automation"
        self.directory.mkdir(mode=0o700)
        self.cache = self.root / ".anthropic-oauth-cache"
        self.cache.write_text("previous-good-cache")
        self.cache.chmod(0o600)
        self.personal = self.home / ".claude" / ".credentials.json"
        self.personal.parent.mkdir()
        self.personal.write_text("personal-login-untouched")
        self.fake = self.home / "fake-claude"
        self.fake.write_text(FAKE_CLAUDE)
        self.fake.chmod(0o700)

    def seed(self, fresh=False):
        path = self.directory / ".credentials.json"
        path.write_text(json.dumps({"claudeAiOauth": {
            "accessToken": "old-access", "refreshToken": "old-refresh",
            "expiresAt": (time.time() + 3600) * 1000 if fresh else 1,
            "subscriptionType": "enterprise", "scopes": ["user:inference"],
        }}))
        path.chmod(0o600)
        return path

    def run_script(self, mode="success", login=False):
        environment = os.environ.copy()
        environment.update(HOME=str(self.home), OPENCLAW_CLAUDE_BIN=str(self.fake),
                           FAKE_MODE=mode, ANTHROPIC_API_KEY="ignored-secret",
                           CLAUDE_CODE_OAUTH_TOKEN="ignored-secret",
                           CLAUDE_CODE_OAUTH_REFRESH_TOKEN="ignored-secret")
        result = subprocess.run(["/bin/bash", str(SCRIPT)] + (["--login"] if login else []),
                                env=environment, capture_output=True, text=True, timeout=15)
        self.assertNotIn("private-token", result.stdout + result.stderr)
        self.assertEqual(self.personal.read_text(), "personal-login-untouched")
        return result

    def test_refresh_uses_only_isolated_credentials_and_atomically_publishes(self):
        self.seed()
        result = self.run_script()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(self.cache.read_text())["claudeAiOauth"]["refreshToken"], "new-refresh")
        self.assertEqual(self.cache.stat().st_mode & 0o777, 0o600)

    def test_attended_enterprise_login_does_not_reuse_inherited_refresh_token(self):
        result = self.run_script(login=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(os.readlink(self.directory / "browser-bin" / "open"), "/usr/bin/open")

    def test_missing_isolated_credentials_never_fall_back_to_personal_or_cache(self):
        result = self.run_script()
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse((self.directory / "invoked").exists())
        self.assertEqual(self.cache.read_text(), "previous-good-cache")

    def test_failure_invalid_account_expired_and_nonfinite_expiry_preserve_cache(self):
        for mode in ("failed", "wrong-account", "expired", "nan"):
            with self.subTest(mode=mode):
                self.seed()
                self.assertNotEqual(self.run_script(mode).returncode, 0)
                self.assertEqual(self.cache.read_text(), "previous-good-cache")

    def test_success_without_new_credentials_is_rejected(self):
        self.seed(fresh=True)
        self.assertNotEqual(self.run_script("unchanged").returncode, 0)
        self.assertEqual(self.cache.read_text(), "previous-good-cache")

    def test_symlink_credentials_are_rejected_before_login(self):
        (self.directory / ".credentials.json").symlink_to(self.personal)
        self.assertNotEqual(self.run_script(login=True).returncode, 0)
        self.assertFalse((self.directory / "invoked").exists())

    def test_public_credentials_are_rejected_before_refresh(self):
        self.seed().chmod(0o644)
        self.assertNotEqual(self.run_script().returncode, 0)
        self.assertFalse((self.directory / "invoked").exists())

    def test_concurrent_refresh_is_rejected(self):
        self.seed()
        with (self.directory / ".refresh.lock").open("w") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            self.assertNotEqual(self.run_script().returncode, 0)
        self.assertFalse((self.directory / "invoked").exists())

    def test_cache_symlink_is_not_followed(self):
        self.seed()
        self.cache.unlink()
        self.cache.symlink_to(self.personal)
        self.assertNotEqual(self.run_script().returncode, 0)
        self.assertTrue(self.cache.is_symlink())

    def test_nonprivate_automation_directory_is_rejected(self):
        self.directory.chmod(0o755)
        self.assertNotEqual(self.run_script(login=True).returncode, 0)
        self.assertFalse((self.directory / "invoked").exists())


if __name__ == "__main__":
    unittest.main()
