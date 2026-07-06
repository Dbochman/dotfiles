#!/usr/bin/env python3
"""Safety-contract tests for the OpenTable upcoming-reservations reader."""

from __future__ import annotations

import json
import hashlib
import os
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / "openclaw" / "bin" / "opentable-reservations"


class OpenTableReservationsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tempdir.cleanup)
        self.root = Path(self.tempdir.name)
        self.helper = self.root / "pinchtab-headless-instance"
        self.log = self.root / "calls.jsonl"
        self.poll_state = self.root / "poll-count"
        self.expected_account = self.root / "opentable_expected_account.sha256"
        self.account_binding = self.root / "opentable_account_binding.json"
        self.browser_token = "browser-token-for-expected-account-12345"
        expected_hash = hashlib.sha256(b"expected@example.invalid").hexdigest()
        self.expected_account.write_text(expected_hash + "\n", encoding="utf-8")
        self.expected_account.chmod(0o600)
        self.account_binding.write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "profile": "opentable",
                    "verified_via": "email_otp",
                    "account_sha256": expected_hash,
                    "token_sha256": hashlib.sha256(
                        self.browser_token.encode("utf-8")
                    ).hexdigest(),
                }
            ),
            encoding="utf-8",
        )
        self.account_binding.chmod(0o600)
        self._write_executable(
            self.helper,
            r'''#!/usr/bin/env python3
import json
import os
import pathlib
import sys

action = sys.argv[1] if len(sys.argv) > 1 else ""
log = pathlib.Path(os.environ["FAKE_LOG"])
with log.open("a", encoding="utf-8") as handle:
    json.dump({"action": action, "scope": sys.argv[2:4]}, handle)
    handle.write("\n")

if action == "acquire":
    print("inst_test\t1")
    raise SystemExit(0)
if action == "open":
    print("tab_test")
    raise SystemExit(0)
if action in {"close", "release"}:
    raise SystemExit(0)
if action != "eval":
    raise SystemExit(2)

mode = os.environ.get("FAKE_MODE", "success")
if mode == "transport-failure":
    print("RAW_SECRET_CANARY")
    print("RAW_ERROR_CANARY", file=sys.stderr)
    raise SystemExit(9)

if mode == "loading-then-success":
    state = pathlib.Path(os.environ["FAKE_POLL_STATE"])
    count = int(state.read_text() if state.exists() else "0")
    state.write_text(str(count + 1))
    if count == 0:
        print(json.dumps({"result": json.dumps({"status": "loading"})}))
        raise SystemExit(0)

def complete_payload(reservations, evidence="dashboard_state", **overrides):
    payload = {
        "status": "ready",
        "complete": True,
        "completeness_evidence": evidence,
        "expected_count": len(reservations),
        "rendered_count": len(reservations),
        "opaque_count": 0,
        "has_pagination": False,
        "browser_identity_hash": os.environ["FAKE_BROWSER_IDENTITY_HASH"],
        "browser_token": os.environ["FAKE_BROWSER_TOKEN"],
        "reservations": reservations,
    }
    payload.update(overrides)
    return payload

payloads = {
    "success": complete_payload(
        [
            {
                "platform": "opentable",
                "reservation_id": "CONFIRM_456",
                "restaurant": "Later Bistro",
                "date": "2099-08-16",
                "time": "19:30",
                "party_size": 4,
                "status": "confirmed",
            },
            {
                "platform": "opentable",
                "reservation_id": "CONFIRM-123",
                "restaurant": "Example Restaurant",
                "date": "2099-08-15",
                "time": "19:00",
                "party_size": 2,
                "status": "pending",
                "location": "Boston, MA",
                "raw_url": "RAW_URL_CANARY",
                "auth": "RAW_TOKEN_CANARY",
            },
        ],
        page="RAW_PAGE_CANARY",
    ),
    "empty": complete_payload([]),
    "authentication": {"status": "authentication_required", "reservations": []},
    "invalid-context": {"status": "invalid_context", "reservations": []},
    "unrecognized": {"status": "unrecognized", "raw": "RAW_BODY_CANARY"},
    "malformed": {"status": "malformed", "candidateCount": 1},
    "bad-reservation": complete_payload(
        [
            {
                "platform": "opentable",
                "reservation_id": "CONFIRM-123",
                "restaurant": "Example Restaurant",
                "date": "not-a-date",
                "time": "19:00",
                "party_size": 2,
                "status": "confirmed",
            }
        ],
    ),
    "conflicting-duplicate": complete_payload(
        [
            {
                "platform": "opentable",
                "reservation_id": "CONFIRM-123",
                "restaurant": "Example Restaurant",
                "date": "2099-08-15",
                "time": "19:00",
                "party_size": 2,
                "status": "confirmed",
            },
            {
                "platform": "opentable",
                "reservation_id": "CONFIRM-123",
                "restaurant": "Different Restaurant",
                "date": "2099-08-15",
                "time": "19:00",
                "party_size": 2,
                "status": "confirmed",
            },
        ],
    ),
    "partial-visible": complete_payload(
        [],
        evidence="dashboard_state",
        expected_count=2,
        rendered_count=1,
    ),
    "pagination": {"status": "incomplete", "reason": "pagination"},
    "opaque-card": {"status": "incomplete", "reason": "opaque_card"},
    "old-ready-without-proof": {
        "status": "ready",
        "browser_identity_hash": os.environ["FAKE_BROWSER_IDENTITY_HASH"],
        "browser_token": os.environ["FAKE_BROWSER_TOKEN"],
        "reservations": [],
    },
    "empty-without-explicit-marker": complete_payload([], evidence="total_count"),
    "identity-unavailable": {"status": "identity_unavailable"},
    "identity-mismatch": complete_payload(
        [],
        browser_token="different-account-token-67890",
    ),
    "identity-hash-mismatch": complete_payload(
        [], browser_identity_hash="0" * 64
    ),
}
payload = payloads["success"] if mode == "loading-then-success" else payloads[mode]
print(json.dumps({"result": json.dumps(payload)}))
''',
        )

    @staticmethod
    def _write_executable(path: Path, contents: str) -> None:
        path.write_text(contents, encoding="utf-8")
        path.chmod(path.stat().st_mode | stat.S_IXUSR)

    def run_reader(self, mode: str = "success", *args: str) -> subprocess.CompletedProcess[str]:
        env = {
            **os.environ,
            "FAKE_LOG": str(self.log),
            "FAKE_MODE": mode,
            "FAKE_POLL_STATE": str(self.poll_state),
            "OPENTABLE_PINCHTAB_INSTANCE_HELPER": str(self.helper),
            "OPENTABLE_ACCOUNT_BINDING_FILE": str(self.account_binding),
            "OPENTABLE_EXPECTED_ACCOUNT_FILE": str(self.expected_account),
            "OPENTABLE_RESERVATIONS_POLL_SECONDS": "0",
            "FAKE_BROWSER_TOKEN": self.browser_token,
            "FAKE_BROWSER_IDENTITY_HASH": self.expected_account.read_text(
                encoding="utf-8"
            ).strip(),
            "PYTHONDONTWRITEBYTECODE": "1",
        }
        return subprocess.run(
            [str(SCRIPT), *args],
            check=False,
            capture_output=True,
            text=True,
            env=env,
        )

    @staticmethod
    def output(result: subprocess.CompletedProcess[str]) -> dict:
        lines = result.stdout.splitlines()
        if len(lines) != 1:
            raise AssertionError(f"expected one JSON line, got {result.stdout!r}")
        return json.loads(lines[0])

    def calls(self) -> list[dict]:
        if not self.log.exists():
            return []
        return [json.loads(line) for line in self.log.read_text().splitlines()]

    def test_success_emits_only_sorted_normalized_facts(self) -> None:
        result = self.run_reader("success", "--json")

        self.assertEqual(result.returncode, 0, result.stderr)
        payload = self.output(result)
        self.assertTrue(payload["success"])
        self.assertEqual(payload["provider"], "opentable")
        self.assertRegex(payload["checked_at"], r"^\d{4}-\d{2}-\d{2}T.*\+00:00$")
        self.assertEqual(
            [item["restaurant"] for item in payload["reservations"]],
            ["Example Restaurant", "Later Bistro"],
        )
        self.assertEqual(payload["reservations"][0]["location"], "Boston, MA")
        self.assertEqual(
            set(payload["reservations"][0]),
            {
                "platform",
                "restaurant",
                "date",
                "time",
                "party_size",
                "status",
                "location",
            },
        )
        serialized = result.stdout + result.stderr
        for canary in (
            "RAW_URL_CANARY",
            "RAW_TOKEN_CANARY",
            "RAW_PAGE_CANARY",
            self.browser_token,
            self.expected_account.read_text(encoding="utf-8").strip(),
            "CONFIRM-123",
            "CONFIRM_456",
        ):
            self.assertNotIn(canary, serialized)

    def test_empty_is_success_only_after_explicit_ready_result(self) -> None:
        result = self.run_reader("empty")

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.output(result)["reservations"], [])

        source = SCRIPT.read_text(encoding="utf-8")
        self.assertIn(
            "completeness_evidence: 'dashboard_state'",
            source,
        )
        self.assertIn("if (hasPagination || hasVirtualizedList || opaqueCount > 0)", source)
        self.assertIn("if (roots.length !== expectedCount)", source)
        self.assertIn("window.__INITIAL_STATE__?.diningDashboard", source)
        self.assertIn("dashboard.upcomingReservations", source)
        self.assertIn("dashboard.upcomingInvites", source)
        self.assertIn("const authSurface = Array.from(document.querySelectorAll", source)
        self.assertIn("if (authSurface ||", source)
        self.assertNotIn(".secrets-cache", source)

    def test_loading_is_polled_without_losing_instance_scope(self) -> None:
        result = self.run_reader("loading-then-success")

        self.assertEqual(result.returncode, 0, result.stderr)
        calls = self.calls()
        eval_calls = [call for call in calls if call["action"] == "eval"]
        self.assertEqual(len(eval_calls), 2)
        self.assertTrue(all(call["scope"] == ["inst_test", "tab_test"] for call in eval_calls))

    def test_all_tab_operations_use_instance_scoped_helper(self) -> None:
        result = self.run_reader("success")

        self.assertEqual(result.returncode, 0, result.stderr)
        calls = self.calls()
        self.assertEqual([call["action"] for call in calls], ["acquire", "open", "eval", "close", "release"])
        self.assertEqual(calls[1]["scope"][0], "inst_test")
        self.assertEqual(calls[2]["scope"], ["inst_test", "tab_test"])
        self.assertEqual(calls[3]["scope"], ["inst_test", "tab_test"])
        source = SCRIPT.read_text(encoding="utf-8")
        self.assertNotIn("pinchtab eval", source)
        self.assertNotIn("pinchtab close", source)

    def test_auth_context_and_shape_failures_never_become_empty_success(self) -> None:
        expected = {
            "authentication": "authentication_required",
            "invalid-context": "invalid_browser_context",
            "unrecognized": "reservations_unrecognized",
            "malformed": "reservations_malformed",
            "bad-reservation": "reservations_malformed",
            "conflicting-duplicate": "reservations_malformed",
            "partial-visible": "reservations_incomplete",
            "pagination": "reservations_incomplete",
            "opaque-card": "reservations_incomplete",
            "old-ready-without-proof": "reservations_incomplete",
            "empty-without-explicit-marker": "reservations_incomplete",
            "identity-unavailable": "account_identity_unverified",
            "identity-mismatch": "account_identity_unverified",
            "identity-hash-mismatch": "account_identity_unverified",
        }
        for mode, error_code in expected.items():
            with self.subTest(mode=mode):
                self.log.unlink(missing_ok=True)
                result = self.run_reader(mode)
                self.assertNotEqual(result.returncode, 0)
                payload = self.output(result)
                self.assertFalse(payload["success"])
                self.assertEqual(payload["error_code"], error_code)
                self.assertNotIn("reservations", payload)
                self.assertEqual(
                    [call["action"] for call in self.calls()][-2:],
                    ["close", "release"],
                )

    def test_transport_failure_drops_raw_browser_output(self) -> None:
        result = self.run_reader("transport-failure")

        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.output(result)["error_code"], "browser_read_failed")
        self.assertNotIn("RAW_SECRET_CANARY", result.stdout + result.stderr)
        self.assertNotIn("RAW_ERROR_CANARY", result.stdout + result.stderr)

    def test_helper_must_be_managed_regular_executable(self) -> None:
        target = self.root / "target-helper"
        target.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        target.chmod(0o755)
        self.helper.unlink()
        self.helper.symlink_to(target)

        result = self.run_reader("success")

        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.output(result)["error_code"], "browser_helper_unavailable")
        self.assertFalse(self.log.exists())

    def test_missing_or_mismatched_protected_binding_fails_before_browser(self) -> None:
        for mutation in ("missing", "account-mismatch", "bad-mode"):
            with self.subTest(mutation=mutation):
                self.log.unlink(missing_ok=True)
                expected_hash = self.expected_account.read_text(encoding="utf-8").strip()
                binding = {
                    "schema_version": 1,
                    "profile": "opentable",
                    "verified_via": "email_otp",
                    "account_sha256": expected_hash,
                    "token_sha256": hashlib.sha256(
                        self.browser_token.encode("utf-8")
                    ).hexdigest(),
                }
                self.account_binding.write_text(json.dumps(binding), encoding="utf-8")
                self.account_binding.chmod(0o600)
                if mutation == "missing":
                    self.account_binding.unlink()
                elif mutation == "account-mismatch":
                    binding["account_sha256"] = "0" * 64
                    self.account_binding.write_text(json.dumps(binding), encoding="utf-8")
                    self.account_binding.chmod(0o600)
                else:
                    self.account_binding.chmod(0o644)

                result = self.run_reader("success")

                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(
                    self.output(result)["error_code"], "account_identity_unverified"
                )
                self.assertFalse(self.log.exists())


if __name__ == "__main__":
    unittest.main()
