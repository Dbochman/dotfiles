#!/usr/bin/env python3
"""Protected account-binding tests for the OpenTable refresh path."""

from __future__ import annotations

import hashlib
import json
import os
import plistlib
import subprocess
import tempfile
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
REFRESH = REPO_ROOT / "openclaw" / "bin" / "opentable-refresh-token.sh"


class OpenTableAccountBindingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tempdir.cleanup)
        self.home = Path(self.tempdir.name) / "home"
        cache = self.home / ".openclaw" / ".secrets-cache"
        cache.parent.mkdir(parents=True)
        cache.write_text(
            "OPENTABLE_EMAIL='expected@example.invalid'\n", encoding="utf-8"
        )
        cache.chmod(0o600)

    def run_shell(self, body: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["/bin/bash", "-c", 'source "$1"\n' + body, "binding-test", str(REFRESH)],
            check=False,
            capture_output=True,
            text=True,
            env={**os.environ, "HOME": str(self.home)},
        )

    def test_email_otp_proof_writes_only_protected_hash_binding(self) -> None:
        token = "expected-account-browser-token-123456"
        result = self.run_shell(
            f'''
mkdir -p "$RUNTIME_DIR" "$(dirname "$BINDING_FILE")"
validate_cached_token() {{ return 0; }}
write_expected_identity
install_and_validate_token "{token}" email_otp
binding_matches "{token}"
printf 'ok\n'
'''
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "ok\n")
        expected_path = self.home / ".cache/openclaw-gateway/opentable_expected_account.sha256"
        binding_path = self.home / ".cache/openclaw-gateway/opentable_account_binding.json"
        email_path = self.home / ".cache/openclaw-gateway/opentable_email"
        self.assertEqual(expected_path.stat().st_mode & 0o777, 0o600)
        self.assertEqual(binding_path.stat().st_mode & 0o777, 0o600)
        self.assertEqual(email_path.stat().st_mode & 0o777, 0o600)
        self.assertEqual(email_path.read_text(), "expected@example.invalid")
        expected_hash = hashlib.sha256(b"expected@example.invalid").hexdigest()
        self.assertEqual(expected_path.read_text().strip(), expected_hash)
        binding = json.loads(binding_path.read_text())
        self.assertEqual(binding["account_sha256"], expected_hash)
        self.assertEqual(
            binding["token_sha256"], hashlib.sha256(token.encode()).hexdigest()
        )
        serialized = expected_path.read_text() + binding_path.read_text()
        self.assertNotIn("expected@example.invalid", serialized)
        self.assertNotIn(token, serialized)

    def test_persisted_refresh_requires_exact_existing_binding(self) -> None:
        token = "expected-account-browser-token-123456"
        other = "different-account-browser-token-654321"
        result = self.run_shell(
            f'''
mkdir -p "$RUNTIME_DIR" "$(dirname "$BINDING_FILE")"
validate_cached_token() {{ return 0; }}
write_expected_identity
install_and_validate_token "{token}" email_otp
install_and_validate_token "{token}" persisted
if install_and_validate_token "{other}" persisted; then exit 41; fi
if install_and_validate_token "{other}" unproved; then exit 42; fi
[[ "$(<"$TOKEN_CACHE")" == "{token}" ]]
printf 'ok\n'
'''
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "ok\n")
        self.assertNotIn(token, result.stderr)
        self.assertNotIn(other, result.stderr)

    def test_failed_rebind_restores_previous_token_and_binding(self) -> None:
        old_token = "expected-account-browser-token-123456"
        new_token = "replacement-browser-token-for-account-999"
        result = self.run_shell(
            f'''
mkdir -p "$RUNTIME_DIR" "$(dirname "$BINDING_FILE")"
validate_cached_token() {{ return 0; }}
write_expected_identity
install_and_validate_token "{old_token}" email_otp
PREVIOUS_TOKEN="{old_token}"
snapshot_previous_binding
old_binding=$(shasum -a 256 "$BINDING_FILE" | awk '{{print $1}}')
validate_cached_token() {{ return 1; }}
if install_and_validate_token "{new_token}" email_otp; then exit 51; fi
[[ "$(<"$TOKEN_CACHE")" == "{old_token}" ]]
new_binding=$(shasum -a 256 "$BINDING_FILE" | awk '{{print $1}}')
[[ "$old_binding" == "$new_binding" ]]
printf 'ok\n'
'''
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "ok\n")
        self.assertNotIn(old_token, result.stderr)
        self.assertNotIn(new_token, result.stderr)

    def test_first_run_matching_browser_identity_bootstraps_without_otp(self) -> None:
        token = "expected-account-browser-token-123456"
        result = self.run_shell(
            f'''
mkdir -p "$RUNTIME_DIR" "$(dirname "$BINDING_FILE")"
write_expected_identity
open_tab() {{ TAB_ID=fake-tab; }}
wait_for_atk() {{ printf '%s\n' "{token}"; }}
wait_for_expected_browser_identity() {{ return 0; }}
validate_cached_token() {{ return 0; }}
close_tab() {{ :; }}
refresh_from_persisted_session
binding_matches "{token}"
python3 -c 'import json,sys; raise SystemExit(0 if json.load(open(sys.argv[1]))["verified_via"] == "browser_email_hash" else 1)' "$BINDING_FILE"
printf 'ok\n'
'''
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "ok\n")
        self.assertNotIn(token, result.stderr)

    def test_first_run_mismatched_browser_identity_cannot_bootstrap(self) -> None:
        token = "different-account-browser-token-654321"
        result = self.run_shell(
            f'''
mkdir -p "$RUNTIME_DIR" "$(dirname "$BINDING_FILE")"
write_expected_identity
open_tab() {{ TAB_ID=fake-tab; }}
wait_for_atk() {{ printf '%s\n' "{token}"; }}
wait_for_expected_browser_identity() {{ return 1; }}
validate_cached_token() {{ return 0; }}
close_tab() {{ :; }}
if refresh_from_persisted_session; then exit 61; fi
[[ ! -e "$BINDING_FILE" ]]
[[ ! -e "$TOKEN_CACHE" ]]
printf 'ok\n'
'''
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "ok\n")
        self.assertNotIn(token, result.stderr)

    def test_email_code_auto_submit_does_not_require_disappeared_button(self) -> None:
        token = "auto-submitted-browser-token-123456"
        result = self.run_shell(
            f'''
click_button() {{ printf 'click=%s\n' "$1" >&2; }}
fill_textbox() {{ :; }}
wait_for_ref() {{ return 0; }}
wait_for_email_code() {{ printf '123456\n'; }}
wait_for_atk() {{ printf '%s\n' "{token}"; }}
actual=$(login_with_email)
[[ "$actual" == "{token}" ]]
printf 'ok\n'
'''
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "ok\n")
        self.assertEqual(
            result.stderr.splitlines(),
            ["click=Use email instead", "click=Continue"],
        )
        self.assertNotIn(token, result.stderr)

    def test_scheduled_failures_get_one_retry_then_exhaust_the_budget(self) -> None:
        result = self.run_shell(
            r'''
prepare_runtime_dir
ATTEMPT_KIND=scheduled
ATTEMPT_NUMBER=1
begin_attempt
write_status failed email_verification_email verification_email_unavailable
[[ "$(scheduled_decision)" == "attempt:2" ]]
ATTEMPT_NUMBER=2
write_status failed email_verification_email verification_email_unavailable
[[ "$(scheduled_decision)" == "exhausted" ]]
STATUS_FINALIZED=1
printf 'ok\n'
'''
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "ok\n")

    def test_recent_success_suppresses_the_second_scheduled_slot(self) -> None:
        result = self.run_shell(
            r'''
prepare_runtime_dir
ATTEMPT_KIND=scheduled
ATTEMPT_NUMBER=1
begin_attempt
write_status success persisted_session completed
[[ "$(scheduled_decision)" == "skip" ]]
STATUS_FINALIZED=1
printf 'ok\n'
'''
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "ok\n")
        status_path = self.home / ".openclaw" / "run" / "opentable-refresh-status.json"
        self.assertEqual(status_path.stat().st_mode & 0o777, 0o600)
        status = json.loads(status_path.read_text())
        self.assertEqual(
            set(status),
            {
                "schema_version",
                "attempt_kind",
                "attempt_number",
                "outcome",
                "stage",
                "reason_code",
                "started_at",
                "completed_at",
                "last_success_at",
            },
        )
        serialized = status_path.read_text()
        self.assertNotIn("expected@example.invalid", serialized)
        self.assertNotIn("token", serialized)

    def test_malformed_prior_status_cannot_block_a_new_status_write(self) -> None:
        status_path = self.home / ".openclaw" / "run" / "opentable-refresh-status.json"
        status_path.parent.mkdir(parents=True)
        status_path.write_text("[]\n", encoding="utf-8")
        status_path.chmod(0o600)
        result = self.run_shell(
            r'''
prepare_runtime_dir
ATTEMPT_KIND=scheduled
ATTEMPT_NUMBER=1
begin_attempt
write_status success persisted_session completed
STATUS_FINALIZED=1
printf 'ok\n'
'''
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "ok\n")
        status = json.loads(status_path.read_text())
        self.assertEqual(status["outcome"], "success")

    def test_email_failure_records_the_exact_stage_without_retrying_login(self) -> None:
        result = self.run_shell(
            r'''
mkdir -p "$RUNTIME_DIR"
calls_file="$RUNTIME_DIR/login-calls"
open_tab() { TAB_ID=fake-tab; }
login_with_email() { printf 'called\n' >> "$calls_file"; return 15; }
if refresh_with_email_login; then exit 71; fi
[[ "$CURRENT_STAGE" == "email_verification_email" ]]
[[ "$FAILURE_CODE" == "verification_email_unavailable" ]]
[[ "$(wc -l < "$calls_file" | tr -d ' ')" == "1" ]]
printf 'ok\n'
'''
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "ok\n")
        self.assertIn("did not produce a working CLI token", result.stderr)

    def test_launchagent_has_bounded_second_schedule_slot(self) -> None:
        plist_path = (
            REPO_ROOT
            / "openclaw"
            / "launchagents"
            / "ai.openclaw.opentable-refresh.plist"
        )
        with plist_path.open("rb") as handle:
            config = plistlib.load(handle)

        self.assertEqual(config["ProgramArguments"][-1], "--scheduled")
        self.assertEqual(
            config["StartCalendarInterval"],
            [
                {"Weekday": 3, "Hour": 4, "Minute": 0},
                {"Weekday": 3, "Hour": 4, "Minute": 30},
            ],
        )


if __name__ == "__main__":
    unittest.main()
