#!/usr/bin/env python3
"""Fake-only tests for attended secret refresh and cache-only Nest access."""

from __future__ import annotations

import errno
import json
import os
import pty
import re
import select
import shlex
import stat
import subprocess
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock


REPO_ROOT = Path(__file__).resolve().parents[2]
REFRESH = REPO_ROOT / "openclaw" / "bin" / "openclaw-refresh-secrets"
NEST = REPO_ROOT / "openclaw" / "bin" / "nest"

CACHE_ONLY_KEYS = [
    "DYLAN_EMAIL",
    "JULIA_EMAIL",
    "OPENCLAW_EMAIL",
    "OPENTABLE_EMAIL",
    "HOUSEHOLD_CHAT_ID",
    "JULIA_CHAT_ID",
    "DYLAN_CHAT_ID",
    "ECHONEST_EMAIL",
    "STARMARKET_GMAIL",
    "STARMARKET_USER_HASH",
    "STARMARKET_DEVICE_TOKEN",
    "EIGHTSLEEP_CLIENT_ID",
    "EIGHTSLEEP_CLIENT_SECRET",
    "EIGHTSLEEP_DYLAN_USER_ID",
    "EIGHTSLEEP_JULIA_USER_ID",
    "EIGHTSLEEP_CROSSTOWN_DEVICE_ID",
    "EIGHTSLEEP_CABIN_DEVICE_ID",
    "PETLIBRO_APPSN",
    "CIELO_API_KEY",
    "TRYFI_EMAIL",
    "TRYFI_PASSWORD",
    "CROSSTOWN_LAT",
    "CROSSTOWN_LON",
    "CABIN_LAT",
    "CABIN_LON",
]
VAULT_KEYS = [
    "OPENAI_API_KEY",
    "ELEVENLABS_API_KEY",
    "OPENCLAW_GATEWAY_TOKEN",
    "CIELO_USERNAME",
    "CIELO_PASSWORD",
    "STARMARKET_USERNAME",
    "STARMARKET_PASSWORD",
    "PLAID_CLIENT_ID",
    "PLAID_SECRET_PRODUCTION",
    "PLAID_SECRET_SANDBOX",
    "NEST_CLIENT_ID",
    "NEST_CLIENT_SECRET",
    "NEST_REFRESH_TOKEN",
    "NEST_PROJECT_ID",
    "OLA_API_KEY",
    "OLA_HOOK_TOKEN",
    "OLA_WEBHOOK_SECRET",
]
FINANCE_VAULT_KEYS = [
    "FINANCE_EVERSOURCE_USERNAME",
    "FINANCE_EVERSOURCE_PASSWORD",
    "FINANCE_NATIONAL_GRID_USERNAME",
    "FINANCE_NATIONAL_GRID_PASSWORD",
    "FINANCE_BWSC_USERNAME",
    "FINANCE_BWSC_PASSWORD",
    "FINANCE_PENNYMAC_USERNAME",
    "FINANCE_PENNYMAC_PASSWORD",
    "FINANCE_BOA_USERNAME",
    "FINANCE_BOA_PASSWORD",
]
NEST_EVENTS_VAULT_KEYS = ["NEST_EVENTS_SERVICE_ACCOUNT_JSON"]
NEST_RUNTIME_CREDENTIAL_KEYS = [
    "NEST_CLIENT_ID",
    "NEST_CLIENT_SECRET",
    "NEST_REFRESH_TOKEN",
    "NEST_PROJECT_ID",
]


class SecretCacheTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tempdir.cleanup)
        self.root = Path(self.tempdir.name)
        self.home = self.root / "home"
        self.fake_bin = self.root / "bin"
        self.cache = self.home / ".openclaw" / ".secrets-cache"
        self.finance_cache = (
            self.home
            / ".openclaw"
            / "financial-dashboard"
            / "scraper-credentials.json"
        )
        self.nest_events_service_account = (
            self.home
            / ".openclaw"
            / "nest-events"
            / "credentials"
            / "subscriber-service-account.json"
        )
        self.seed = self.home / ".openclaw" / ".secrets-refresh.env"
        self.op_log = self.root / "op.log"
        self.http_log = self.root / "http.log"
        self.nest_cache = self.root / "nest-cache"
        self.home.mkdir()
        self.fake_bin.mkdir()
        self._write_executable(
            self.fake_bin / "op",
            r'''#!/usr/bin/env bash
set -euo pipefail
[[ "${1:-}" == "read" && $# -eq 2 ]] || exit 91
printf 'read:%s\n' "$2" >> "$FAKE_OP_LOG"
key="${2#ref://}"
[[ "$key" != "${FAKE_OP_FAIL_KEY:-}" ]] || exit 92
if [[ "$key" == "NEST_EVENTS_SERVICE_ACCOUNT_JSON" ]]; then
  [[ -n "${FAKE_NEST_EVENTS_SERVICE_ACCOUNT_JSON:-}" ]] || exit 93
  printf '%s' "$FAKE_NEST_EVENTS_SERVICE_ACCOUNT_JSON"
  exit 0
fi
if [[ "$key" == NEST_* ]]; then
  printf 'fake-vault-%s' "${key//_/-}"
  exit 0
fi
printf 'fake-vault-%s with spaces and symbols-$-#-!' "$key"
''',
        )
        self._write_executable(
            self.fake_bin / "curl",
            r'''#!/usr/bin/env bash
set -euo pipefail
# The hardened Nest client streams OAuth form data and SDM curl configuration
# through stdin. Consume those pipes so the producer observes a real reader
# instead of failing under pipefail with a synthetic BrokenPipe.
stdin_payload=""
case " $* " in
  *" --data-binary @- "*) cat >/dev/null ;;
  *" --config - "*) stdin_payload=$(cat) ;;
esac
case "$* $stdin_payload" in
  *oauth2/v4/token*)
    printf 'oauth\n' >> "$FAKE_HTTP_LOG"
    printf '%s\n' '{"access_token":"fake-access-token"}'
    ;;
  *smartdevicemanagement.googleapis.com*)
    printf 'sdm\n' >> "$FAKE_HTTP_LOG"
    printf '%s\n' '{"devices":[]}'
    ;;
  *)
    exit 93
    ;;
esac
''',
        )

    @staticmethod
    def _write_executable(path: Path, content: str) -> None:
        path.write_text(content, encoding="utf-8")
        path.chmod(path.stat().st_mode | stat.S_IXUSR)

    @staticmethod
    def _write_assignments(path: Path, values: dict[str, str], mode: int = 0o600) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = "".join(f"{key}={shlex.quote(value)}\n" for key, value in values.items())
        path.write_text(payload, encoding="utf-8")
        path.chmod(mode)

    def seed_values(self) -> dict[str, str]:
        values = {key: f"fake-cache-{key.lower()}" for key in CACHE_ONLY_KEYS}
        values["TRYFI_PASSWORD"] = "fake cache value with spaces '$HOME # !"
        values.update(
            {
                f"OP_REF_{key}": f"ref://{key}"
                for key in VAULT_KEYS + FINANCE_VAULT_KEYS
            }
        )
        return values

    @staticmethod
    def nest_events_service_account_payload() -> dict[str, str]:
        project_id = "fake-nest-events"
        return {
            "type": "service_account",
            "project_id": project_id,
            "private_key_id": "fake-private-key-id",
            "private_key": (
                "-----BEGIN PRIVATE KEY-----\n"
                "fake-test-private-key\n"
                "-----END PRIVATE KEY-----\n"
            ),
            "client_email": (
                f"openclaw-nest-events@{project_id}.iam.gserviceaccount.com"
            ),
            "client_id": "123456789012345678901",
            "token_uri": "https://oauth2.googleapis.com/token",
        }

    def seed_values_with_nest_events(self) -> dict[str, str]:
        values = self.seed_values()
        values["OP_REF_NEST_EVENTS_SERVICE_ACCOUNT_JSON"] = (
            "ref://NEST_EVENTS_SERVICE_ACCOUNT_JSON"
        )
        return values

    def environment(self, **overrides: str) -> dict[str, str]:
        env = os.environ.copy()
        # The gateway process normally exports live Nest credentials. Tests
        # must exercise only their fake cache or explicit fake OP_REF values,
        # never inherit those real runtime credentials from the parent.
        for key in NEST_RUNTIME_CREDENTIAL_KEYS:
            env.pop(key, None)
            env.pop(f"OP_REF_{key}", None)
        env.update(
            {
                "HOME": str(self.home),
                "PATH": f"{self.fake_bin}:{env['PATH']}",
                "OPENCLAW_SECRETS_CACHE": str(self.cache),
                "OPENCLAW_FINANCE_SECRETS_CACHE": str(self.finance_cache),
                "OPENCLAW_SECRETS_SEED": str(self.seed),
                "NEST_CACHE_DIR": str(self.nest_cache),
                "FAKE_OP_LOG": str(self.op_log),
                "FAKE_HTTP_LOG": str(self.http_log),
            }
        )
        env.update(overrides)
        return env

    def test_environment_scrubs_inherited_nest_credentials(self) -> None:
        inherited = {
            **{key: f"live-{key.lower()}" for key in NEST_RUNTIME_CREDENTIAL_KEYS},
            **{
                f"OP_REF_{key}": f"op://live/{key}"
                for key in NEST_RUNTIME_CREDENTIAL_KEYS
            },
        }

        with mock.patch.dict(os.environ, inherited, clear=False):
            isolated = self.environment()
            explicit = self.environment(
                NEST_CLIENT_ID="fake-explicit-client",
                OP_REF_NEST_CLIENT_ID="ref://NEST_CLIENT_ID",
            )

        for key in inherited:
            self.assertNotIn(key, isolated)
        self.assertEqual(explicit["NEST_CLIENT_ID"], "fake-explicit-client")
        self.assertEqual(
            explicit["OP_REF_NEST_CLIENT_ID"], "ref://NEST_CLIENT_ID"
        )

    def run_refresh(self, *args: str, **env: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["bash", str(REFRESH), *args],
            check=False,
            capture_output=True,
            text=True,
            env=self.environment(**env),
        )

    def run_refresh_in_pty(self, *args: str, **env: str) -> tuple[int, str]:
        master, slave = pty.openpty()
        process = subprocess.Popen(
            ["bash", str(REFRESH), *args],
            stdin=slave,
            stdout=slave,
            stderr=slave,
            env=self.environment(**env),
            close_fds=True,
        )
        os.close(slave)
        chunks: list[bytes] = []
        deadline = time.monotonic() + 15
        sent = False
        try:
            while time.monotonic() < deadline:
                ready, _, _ = select.select([master], [], [], 0.1)
                if ready:
                    try:
                        chunk = os.read(master, 65536)
                    except OSError as exc:
                        if exc.errno == errno.EIO:
                            break
                        raise
                    if not chunk:
                        break
                    chunks.append(chunk)
                    if not sent and b"Type REFRESH" in b"".join(chunks):
                        os.write(master, b"REFRESH\n")
                        sent = True
                if process.poll() is not None and not ready:
                    break
            try:
                returncode = process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
                self.fail("Refresh PTY test timed out")
            return returncode, b"".join(chunks).decode(errors="replace")
        finally:
            os.close(master)

    def run_nest(self, *args: str, **env: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["bash", str(NEST), *args],
            check=False,
            capture_output=True,
            text=True,
            stdin=subprocess.DEVNULL,
            env=self.environment(**env),
        )

    def run_nest_in_pty(self, *args: str, **env: str) -> tuple[int, str]:
        master, slave = pty.openpty()
        process = subprocess.Popen(
            ["bash", str(NEST), *args],
            stdin=slave,
            stdout=slave,
            stderr=slave,
            env=self.environment(**env),
            close_fds=True,
        )
        os.close(slave)
        chunks: list[bytes] = []
        deadline = time.monotonic() + 10
        try:
            while time.monotonic() < deadline:
                ready, _, _ = select.select([master], [], [], 0.1)
                if ready:
                    try:
                        chunk = os.read(master, 65536)
                        if not chunk:
                            break
                        chunks.append(chunk)
                    except OSError as error:
                        if error.errno != errno.EIO:
                            raise
                        break
                if process.poll() is not None and not ready:
                    break
            try:
                returncode = process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
                self.fail("Nest PTY test timed out")
            return returncode, b"".join(chunks).decode(errors="replace")
        finally:
            os.close(master)

    def test_refresh_is_explicit_atomic_quoted_and_exact_field_only(self) -> None:
        self._write_assignments(self.seed, self.seed_values())

        returncode, refresh_output = self.run_refresh_in_pty("--interactive")

        self.assertEqual(returncode, 0, refresh_output)
        self.assertEqual(stat.S_IMODE(self.cache.stat().st_mode), 0o600)
        text = self.cache.read_text(encoding="utf-8")
        self.assertNotIn("OP_REF_", text)
        for key in ("OLA_API_KEY", "OLA_HOOK_TOKEN", "OLA_WEBHOOK_SECRET"):
            self.assertIn(f"{key}=", text)
        self.assertEqual(list(self.cache.parent.glob(".secrets-cache.*")), [])
        expected_calls = [
            f"read:ref://{key}"
            for key in VAULT_KEYS + FINANCE_VAULT_KEYS
        ]
        self.assertEqual(self.op_log.read_text().splitlines(), expected_calls)
        self.assertNotIn("fake-vault-", refresh_output)
        self.assertNotIn("ref://", refresh_output)
        self.assertEqual(stat.S_IMODE(self.finance_cache.stat().st_mode), 0o600)
        self.assertFalse(self.nest_events_service_account.exists())
        finance_payload = json.loads(self.finance_cache.read_text(encoding="utf-8"))
        self.assertEqual(
            set(finance_payload),
            {"eversource", "national_grid", "bwsc", "pennymac", "boa"},
        )
        for key in FINANCE_VAULT_KEYS:
            self.assertNotIn(key, text)

        expected = self.seed_values()["TRYFI_PASSWORD"]
        check = subprocess.run(
            [
                "bash",
                "-c",
                'source "$1"; [[ "$TRYFI_PASSWORD" == "$2" ]]',
                "_",
                str(self.cache),
                expected,
            ],
            check=False,
        )
        self.assertEqual(check.returncode, 0)

    def test_nest_event_credential_is_optional_isolated_and_atomic(self) -> None:
        self._write_assignments(self.seed, self.seed_values_with_nest_events())
        credential = self.nest_events_service_account_payload()

        returncode, output = self.run_refresh_in_pty(
            "--interactive",
            FAKE_NEST_EVENTS_SERVICE_ACCOUNT_JSON=json.dumps(credential),
        )

        self.assertEqual(returncode, 0, output)
        credential_stat = self.nest_events_service_account.lstat()
        self.assertTrue(stat.S_ISREG(credential_stat.st_mode))
        self.assertEqual(credential_stat.st_uid, os.getuid())
        self.assertEqual(stat.S_IMODE(credential_stat.st_mode), 0o600)
        self.assertEqual(
            stat.S_IMODE(self.nest_events_service_account.parent.stat().st_mode),
            0o700,
        )
        self.assertEqual(
            stat.S_IMODE(
                self.nest_events_service_account.parent.parent.stat().st_mode
            ),
            0o700,
        )
        self.assertEqual(
            json.loads(self.nest_events_service_account.read_text(encoding="utf-8")),
            credential,
        )
        self.assertEqual(
            list(
                self.nest_events_service_account.parent.glob(
                    ".subscriber-service-account.json.*"
                )
            ),
            [],
        )

        cache_text = self.cache.read_text(encoding="utf-8")
        self.assertNotIn("NEST_EVENTS_SERVICE_ACCOUNT_JSON", cache_text)
        self.assertNotIn("fake-test-private-key", cache_text)
        self.assertNotIn(credential["client_email"], cache_text)
        self.assertNotIn("fake-test-private-key", output)
        self.assertNotIn(credential["client_email"], output)
        self.assertNotIn("ref://", output)
        self.assertEqual(
            self.op_log.read_text(encoding="utf-8").splitlines(),
            [
                *(f"read:ref://{key}" for key in VAULT_KEYS),
                *(f"read:ref://{key}" for key in FINANCE_VAULT_KEYS),
                "read:ref://NEST_EVENTS_SERVICE_ACCOUNT_JSON",
            ],
        )

    def test_invalid_nest_event_credential_preserves_last_good_file(self) -> None:
        self._write_assignments(self.seed, self.seed_values_with_nest_events())
        self.nest_events_service_account.parent.mkdir(parents=True)
        self.nest_events_service_account.parent.parent.chmod(0o700)
        self.nest_events_service_account.parent.chmod(0o700)
        original = b'{"last_good":true}\n'
        self.nest_events_service_account.write_bytes(original)
        self.nest_events_service_account.chmod(0o600)
        invalid = self.nest_events_service_account_payload()
        invalid["client_email"] = "unexpected-account@example.invalid"

        returncode, output = self.run_refresh_in_pty(
            "--interactive",
            FAKE_NEST_EVENTS_SERVICE_ACCOUNT_JSON=json.dumps(invalid),
        )

        self.assertNotEqual(returncode, 0, output)
        self.assertEqual(self.nest_events_service_account.read_bytes(), original)
        self.assertFalse(self.cache.exists())
        self.assertFalse(self.finance_cache.exists())
        self.assertEqual(
            list(
                self.nest_events_service_account.parent.glob(
                    ".subscriber-service-account.json.*"
                )
            ),
            [],
        )
        self.assertNotIn("unexpected-account", output)
        self.assertNotIn("fake-test-private-key", output)
        self.assertNotIn("ref://", output)

    def test_nest_event_field_read_failure_preserves_last_good_file(self) -> None:
        self._write_assignments(self.seed, self.seed_values_with_nest_events())
        self.nest_events_service_account.parent.mkdir(parents=True)
        self.nest_events_service_account.parent.parent.chmod(0o700)
        self.nest_events_service_account.parent.chmod(0o700)
        original = b'{"last_good":"preserve exactly"}\n'
        self.nest_events_service_account.write_bytes(original)
        self.nest_events_service_account.chmod(0o600)

        returncode, output = self.run_refresh_in_pty(
            "--interactive",
            FAKE_OP_FAIL_KEY="NEST_EVENTS_SERVICE_ACCOUNT_JSON",
        )

        self.assertNotEqual(returncode, 0, output)
        self.assertEqual(self.nest_events_service_account.read_bytes(), original)
        self.assertFalse(self.cache.exists())
        self.assertFalse(self.finance_cache.exists())
        self.assertNotIn("ref://", output)
        self.assertNotIn("fake-vault-", output)

    def test_nest_event_credential_refuses_symlink_destination(self) -> None:
        self._write_assignments(self.seed, self.seed_values_with_nest_events())
        self.nest_events_service_account.parent.mkdir(parents=True)
        self.nest_events_service_account.parent.parent.chmod(0o700)
        self.nest_events_service_account.parent.chmod(0o700)
        symlink_target = self.root / "last-good-service-account.json"
        original = b'{"last_good":"symlink target"}\n'
        symlink_target.write_bytes(original)
        symlink_target.chmod(0o600)
        self.nest_events_service_account.symlink_to(symlink_target)

        returncode, output = self.run_refresh_in_pty(
            "--interactive",
            FAKE_NEST_EVENTS_SERVICE_ACCOUNT_JSON=json.dumps(
                self.nest_events_service_account_payload()
            ),
        )

        self.assertNotEqual(returncode, 0, output)
        self.assertTrue(self.nest_events_service_account.is_symlink())
        self.assertEqual(symlink_target.read_bytes(), original)
        self.assertFalse(self.cache.exists())
        self.assertFalse(self.finance_cache.exists())
        self.assertNotIn("fake-test-private-key", output)
        self.assertNotIn("ref://", output)

    def test_nest_event_materialization_requires_attended_terminal(self) -> None:
        self._write_assignments(self.seed, self.seed_values_with_nest_events())

        result = self.run_refresh(
            "--interactive",
            FAKE_NEST_EVENTS_SERVICE_ACCOUNT_JSON=json.dumps(
                self.nest_events_service_account_payload()
            ),
        )

        self.assertEqual(result.returncode, 2)
        self.assertIn("interactive terminal", result.stderr)
        self.assertFalse(self.op_log.exists())
        self.assertFalse(self.nest_events_service_account.exists())
        self.assertNotIn("ref://", result.stdout + result.stderr)

    def test_refresh_failure_preserves_last_good_cache_byte_for_byte(self) -> None:
        self._write_assignments(self.seed, self.seed_values())
        original = b"LAST_GOOD='preserve me exactly'\n"
        original_finance = b'{"last_good": true}\n'
        self.cache.parent.mkdir(parents=True, exist_ok=True)
        self.cache.write_bytes(original)
        self.cache.chmod(0o600)
        self.finance_cache.parent.mkdir(parents=True, exist_ok=True)
        self.finance_cache.write_bytes(original_finance)
        self.finance_cache.chmod(0o600)

        returncode, refresh_output = self.run_refresh_in_pty(
            "--interactive",
            FAKE_OP_FAIL_KEY="NEST_PROJECT_ID",
        )

        self.assertNotEqual(returncode, 0)
        self.assertEqual(self.cache.read_bytes(), original)
        self.assertEqual(self.finance_cache.read_bytes(), original_finance)
        self.assertEqual(list(self.cache.parent.glob(".secrets-cache.*")), [])
        self.assertNotIn("fake-vault-", refresh_output)

    def test_ola_hook_token_refresh_failure_preserves_last_good_cache(self) -> None:
        self._write_assignments(self.seed, self.seed_values())
        original = b"LAST_GOOD='keep the prior gateway environment'\n"
        self.cache.parent.mkdir(parents=True, exist_ok=True)
        self.cache.write_bytes(original)
        self.cache.chmod(0o600)

        returncode, refresh_output = self.run_refresh_in_pty(
            "--interactive",
            FAKE_OP_FAIL_KEY="OLA_HOOK_TOKEN",
        )

        self.assertNotEqual(returncode, 0)
        self.assertEqual(self.cache.read_bytes(), original)
        self.assertEqual(list(self.cache.parent.glob(".secrets-cache.*")), [])
        self.assertNotIn("fake-vault-", refresh_output)

    def test_ola_webhook_secret_refresh_failure_preserves_last_good_cache(self) -> None:
        self._write_assignments(self.seed, self.seed_values())
        original = b"LAST_GOOD='keep the prior gateway environment'\n"
        self.cache.parent.mkdir(parents=True, exist_ok=True)
        self.cache.write_bytes(original)
        self.cache.chmod(0o600)

        returncode, refresh_output = self.run_refresh_in_pty(
            "--interactive",
            FAKE_OP_FAIL_KEY="OLA_WEBHOOK_SECRET",
        )

        self.assertNotEqual(returncode, 0)
        self.assertEqual(self.cache.read_bytes(), original)
        self.assertEqual(list(self.cache.parent.glob(".secrets-cache.*")), [])
        self.assertNotIn("fake-vault-", refresh_output)

    def test_general_refresh_does_not_require_or_export_finance_references(self) -> None:
        values = self.seed_values()
        for key in FINANCE_VAULT_KEYS:
            values.pop(f"OP_REF_{key}")
        self._write_assignments(self.seed, values)

        returncode, output = self.run_refresh_in_pty("--interactive")

        self.assertEqual(returncode, 0, output)
        self.assertFalse(self.finance_cache.exists())
        cache_text = self.cache.read_text(encoding="utf-8")
        for key in FINANCE_VAULT_KEYS:
            self.assertNotIn(key, cache_text)
        self.assertEqual(
            self.op_log.read_text().splitlines(),
            [f"read:ref://{key}" for key in VAULT_KEYS],
        )

    def test_partial_finance_references_preserve_both_caches(self) -> None:
        values = self.seed_values()
        values.pop("OP_REF_FINANCE_BOA_PASSWORD")
        self._write_assignments(self.seed, values)
        original = b"LAST_GOOD='general'\n"
        original_finance = b'{"last_good": true}\n'
        self.cache.parent.mkdir(parents=True, exist_ok=True)
        self.cache.write_bytes(original)
        self.cache.chmod(0o600)
        self.finance_cache.parent.mkdir(parents=True, exist_ok=True)
        self.finance_cache.write_bytes(original_finance)
        self.finance_cache.chmod(0o600)

        returncode, output = self.run_refresh_in_pty("--interactive")

        self.assertNotEqual(returncode, 0, output)
        self.assertEqual(self.cache.read_bytes(), original)
        self.assertEqual(self.finance_cache.read_bytes(), original_finance)

    def test_refresh_requires_confirmation_and_secure_seed_without_calling_op(self) -> None:
        self._write_assignments(self.seed, self.seed_values(), mode=0o644)
        unconfirmed = self.run_refresh()
        self.assertEqual(unconfirmed.returncode, 2)
        self.assertFalse(self.op_log.exists())

        non_tty = self.run_refresh("--interactive")
        self.assertEqual(non_tty.returncode, 2)
        self.assertIn("interactive terminal", non_tty.stderr)

        insecure_returncode, _ = self.run_refresh_in_pty("--interactive")
        self.assertNotEqual(insecure_returncode, 0)
        self.assertFalse(self.op_log.exists())
        self.assertFalse(self.cache.exists())

    def test_nest_uses_protected_cache_and_never_calls_op(self) -> None:
        self._write_assignments(
            self.cache,
            {
                "NEST_CLIENT_ID": "fake-client",
                "NEST_CLIENT_SECRET": "fake-secret",
                "NEST_REFRESH_TOKEN": "fake-refresh",
                "NEST_PROJECT_ID": "fake-project",
            },
        )

        result = self.run_nest("raw")

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(self.op_log.exists())
        self.assertEqual(self.http_log.read_text().splitlines(), ["oauth", "sdm"])
        access_cache = self.nest_cache / "access_token"
        self.assertEqual(stat.S_IMODE(access_cache.stat().st_mode), 0o600)

    def test_nest_missing_or_insecure_cache_fails_closed_without_op(self) -> None:
        missing = self.run_nest("raw")
        self.assertNotEqual(missing.returncode, 0)
        self.assertFalse(self.op_log.exists())

        self._write_assignments(self.cache, {"NEST_CLIENT_ID": "fake"}, mode=0o644)
        insecure = self.run_nest("raw")
        self.assertNotEqual(insecure.returncode, 0)
        self.assertFalse(self.op_log.exists())

    def test_nest_op_fallback_requires_real_interactive_terminal(self) -> None:
        references = {f"OP_REF_{key}": f"ref://{key}" for key in (
            "NEST_CLIENT_ID",
            "NEST_CLIENT_SECRET",
            "NEST_REFRESH_TOKEN",
            "NEST_PROJECT_ID",
        )}

        returncode, output = self.run_nest_in_pty("raw", **references)

        self.assertEqual(returncode, 0, output)
        self.assertEqual(
            self.op_log.read_text().splitlines(),
            [
                "read:ref://NEST_CLIENT_ID",
                "read:ref://NEST_CLIENT_SECRET",
                "read:ref://NEST_REFRESH_TOKEN",
                "read:ref://NEST_PROJECT_ID",
            ],
        )
        self.assertNotIn("fake-vault-", output)

    def test_tracked_refresh_has_no_literal_secret_assignments(self) -> None:
        source = REFRESH.read_text(encoding="utf-8")
        self.assertNotIn("OP_SERVICE_ACCOUNT_TOKEN=", source)
        self.assertNotIn("op item get", source)
        for key in (
            CACHE_ONLY_KEYS
            + VAULT_KEYS
            + FINANCE_VAULT_KEYS
            + NEST_EVENTS_VAULT_KEYS
        ):
            literal_assignment = re.compile(
                rf"^\s*(?:export\s+)?{re.escape(key)}\s*=\s*(?![\"']?\$)",
                re.MULTILINE,
            )
            self.assertIsNone(literal_assignment.search(source), key)
            self.assertNotRegex(source, rf"echo\s+[\"']{re.escape(key)}=")

    def test_identity_placeholders_are_domain_specific(self) -> None:
        cases = {
            "openclaw/skills/august-lock/SKILL.md": ("augustId", "${TRYFI_EMAIL}"),
            "openclaw/workspace/MEMORY.md": ("protected MBP config", "${TRYFI_EMAIL}"),
            "openclaw/skills/echonest/SKILL.md": ("${ECHONEST_EMAIL}", "${TRYFI_EMAIL}"),
            "openclaw/plans/archive/bluebubbles-implementation-current-state.md": (
                "${BLUEBUBBLES_CHAT_GUID}",
                "${TRYFI_EMAIL}",
            ),
            "openclaw/plans/archive/openclaw-2026-6-native-imessage-migration.md": (
                "${IMESSAGE_CHAT_IDENTIFIER}",
                "${TRYFI_EMAIL}",
            ),
        }
        for relative, (required, forbidden) in cases.items():
            with self.subTest(path=relative):
                content = (REPO_ROOT / relative).read_text(encoding="utf-8")
                self.assertIn(required, content)
                self.assertNotIn(forbidden, content)

        for relative in (
            "openclaw/skills/gws-calendar/SKILL.md",
            "openclaw/skills/gws-drive/SKILL.md",
            "openclaw/skills/gws-gmail/SKILL.md",
        ):
            with self.subTest(path=relative):
                content = (REPO_ROOT / relative).read_text(encoding="utf-8")
                self.assertIn("${DYLAN_EMAIL}", content)
                self.assertIn("${JULIA_EMAIL}", content)
                self.assertNotIn("${TRYFI_EMAIL}", content)
                spam_rows = [
                    line
                    for line in content.splitlines()
                    if line.startswith("|") and "spam" in line.casefold()
                ]
                self.assertEqual(len(spam_rows), 1)
                self.assertIn("${STARMARKET_GMAIL}", spam_rows[0])
                self.assertNotIn("${DYLAN_EMAIL}", spam_rows[0])

        briefing = (REPO_ROOT / "openclaw/bin/dylan-morning-briefing-data.py").read_text()
        self.assertIn('os.environ.get("DYLAN_EMAIL", "")', briefing)
        self.assertNotIn("TRYFI_EMAIL", briefing)
        self.assertNotIn("GWS_DYLAN_ACCOUNT", briefing)

        julia_briefing = (
            REPO_ROOT / "openclaw/bin/julia-morning-briefing-data.py"
        ).read_text()
        self.assertIn('os.environ.get("JULIA_EMAIL", "")', julia_briefing)
        self.assertNotIn("TRYFI_EMAIL", julia_briefing)
        self.assertNotIn("STARMARKET_GMAIL", julia_briefing)

        opentable_refresh = (
            REPO_ROOT / "openclaw/bin/opentable-refresh-token.sh"
        ).read_text(encoding="utf-8")
        self.assertIn('OT_EMAIL="$OPENTABLE_EMAIL"', opentable_refresh)
        self.assertIn('GWS_ACCOUNT="$OPENTABLE_EMAIL"', opentable_refresh)
        self.assertNotIn('GWS_ACCOUNT="$OPENCLAW_EMAIL"', opentable_refresh)

        tools = (REPO_ROOT / "openclaw/workspace/TOOLS.md").read_text(
            encoding="utf-8"
        )
        spam_rows = [
            line
            for line in tools.splitlines()
            if line.startswith("|") and "spam" in line.casefold()
        ]
        self.assertEqual(len(spam_rows), 1)
        self.assertIn("${STARMARKET_GMAIL}", spam_rows[0])
        self.assertNotIn("${DYLAN_EMAIL}", spam_rows[0])

        for relative in (
            "openclaw/workspace/MEMORY.md",
            "openclaw/workspace/SOUL.md",
            "openclaw/workspace/TOOLS.md",
        ):
            with self.subTest(path=relative):
                content = (REPO_ROOT / relative).read_text(encoding="utf-8")
                self.assertNotRegex(content, r"chat_id:\d{2,}")

    def test_active_automation_uses_structural_private_placeholders(self) -> None:
        email = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
        mac = re.compile(r"(?:[0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}")
        failures: list[str] = []
        roots = (
            REPO_ROOT / "openclaw" / "skills",
            REPO_ROOT / "openclaw" / "workspace" / "scripts",
            REPO_ROOT / "openclaw" / "bin",
        )
        for root in roots:
            for path in root.rglob("*"):
                if not path.is_file() or "__pycache__" in path.parts:
                    continue
                try:
                    content = path.read_text(encoding="utf-8")
                except (OSError, UnicodeDecodeError):
                    continue
                private_emails = [
                    value for value in email.findall(content)
                    if not value.lower().endswith("@example.com")
                    and not value.lower().endswith("@example.invalid")
                ]
                if private_emails or mac.search(content):
                    failures.append(str(path.relative_to(REPO_ROOT)))
        self.assertFalse(
            sorted(set(failures)),
            "active automation contains a literal private email or MAC",
        )


if __name__ == "__main__":
    unittest.main()
