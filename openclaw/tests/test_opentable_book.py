#!/usr/bin/env python3
"""Safety-contract tests for the one-shot OpenTable booking script."""

from __future__ import annotations

import json
import os
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / "openclaw" / "workspace" / "scripts" / "opentable-book.sh"


class OpenTableBookTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tempdir.cleanup)
        self.root = Path(self.tempdir.name)
        self.home = self.root / "home"
        self.fake_bin = self.root / "bin"
        self.log = self.root / "calls.log"
        self.approval_cache = self.root / "approval-cache"
        (self.home / ".openclaw" / "bin").mkdir(parents=True)
        self.fake_bin.mkdir()

        self._write_executable(
            self.home / ".openclaw" / "bin" / "pinchtab-headless-instance",
            r'''#!/usr/bin/env bash
set -euo pipefail
case "${1:-}" in
  acquire)
    printf 'helper:acquire\n' >> "$FAKE_LOG"
    printf 'fake-instance\t1\n'
    ;;
  open)
    [[ "${2:-}" == "fake-instance" ]]
    printf 'helper:open:%s\n' "$2" >> "$FAKE_LOG"
    printf 'fake-tab\n'
    ;;
  eval)
    [[ "${2:-}" == "fake-instance" && "${3:-}" == "fake-tab" && $# -eq 4 ]]
    printf 'helper:eval:%s:%s\n' "$2" "$3" >> "$FAKE_LOG"
    exec pinchtab eval "$4" --tab "$3" --json
    ;;
  close)
    [[ "${2:-}" == "fake-instance" && "${3:-}" == "fake-tab" && $# -eq 3 ]]
    printf 'helper:close:%s:%s\n' "$2" "$3" >> "$FAKE_LOG"
    exec pinchtab close "$3" --json
    ;;
  release)
    printf 'helper:release\n' >> "$FAKE_LOG"
    ;;
  *)
    exit 2
    ;;
esac
''',
        )
        self._write_executable(
            self.fake_bin / "sleep",
            "#!/usr/bin/env bash\nexit 0\n",
        )
        self._write_executable(
            self.fake_bin / "pinchtab",
            r'''#!/usr/bin/env bash
set -euo pipefail

if [[ "${1:-}" == "close" ]]; then
  printf 'pinchtab:close\n' >> "$FAKE_LOG"
  exit 0
fi
if [[ "${1:-}" != "eval" ]]; then
  exit 2
fi

javascript="${2:-}"
case "$javascript" in
  *OT_STEP_SMOKE*)
    printf 'pinchtab:smoke\n' >> "$FAKE_LOG"
    case "${FAKE_MODE:-}" in
      cross-origin-smoke)
        printf '%s\n' '{"result":"{\"origin\":\"https://evil.example\",\"path\":\"/s\",\"query\":\"SENSITIVE-SMOKE\"}"}'
        ;;
      wrong-path-smoke)
        printf '%s\n' '{"result":"{\"origin\":\"https://www.opentable.com\",\"path\":\"/booking/details\",\"query\":\"SENSITIVE-SMOKE\"}"}'
        ;;
      tab-not-found-smoke)
        printf '%s\n' 'Error 404: tab fake-tab not found' >&2
        ;;
      *)
        printf '%s\n' '{"result":"{\"origin\":\"https://www.opentable.com\",\"path\":\"/s\",\"query\":\"SENSITIVE-SMOKE\"}"}'
        ;;
    esac
    ;;
  *OT_STEP_COOKIE*)
    printf 'pinchtab:cookie\n' >> "$FAKE_LOG"
    printf '%s\n' '{"result":null}'
    ;;
  *OT_STEP_SLOT*)
    printf 'pinchtab:slot\n' >> "$FAKE_LOG"
    if [[ -n "${EXPECT_MAX_DELTA:-}" && "$javascript" != *"const maxDiff=${EXPECT_MAX_DELTA};"* ]]; then
      exit 9
    fi
    case "${FAKE_MODE:-}" in
      outside)
        printf '%s\n' '{"result":"{\"origin\":\"https://www.opentable.com\",\"path\":\"/s\",\"status\":\"outside_window\",\"closestTime\":\"8:00 PM\",\"diffMinutes\":60,\"secret\":\"SENSITIVE-SLOT\"}"}'
        ;;
      opaque-renderer)
        [[ "$javascript" == *'[data-test="restaurant-card"]'* ]]
        [[ "$javascript" == *"status:'renderer_unavailable'"* ]]
        printf '%s\n' '{"result":"{\"origin\":\"https://www.opentable.com\",\"path\":\"/s\",\"status\":\"renderer_unavailable\",\"secret\":\"SENSITIVE-OPAQUE-CARD\"}"}'
        ;;
      no-slots)
        printf '%s\n' '{"result":"{\"origin\":\"https://www.opentable.com\",\"path\":\"/s\",\"status\":\"no_slots\"}"}'
        ;;
      cross-origin-slot)
        printf '%s\n' '{"result":"{\"origin\":\"https://evil.example\",\"path\":\"/s\",\"status\":\"selected\",\"selectedTime\":\"7:15 PM\",\"diffMinutes\":15}"}'
        ;;
      *)
        printf '%s\n' '{"result":"{\"origin\":\"https://www.opentable.com\",\"path\":\"/s\",\"status\":\"selected\",\"selectedTime\":\"7:15 PM\",\"diffMinutes\":15,\"secret\":\"SENSITIVE-SLOT\"}"}'
        ;;
    esac
    ;;
  *OT_STEP_DETAILS*)
    printf 'pinchtab:details\n' >> "$FAKE_LOG"
    case "${FAKE_DETAILS_VARIANT:-}" in
      changed-location)
        printf '%s\n' '{"result":"{\"origin\":\"https://www.opentable.com\",\"path\":\"/booking/details\",\"routeValid\":true,\"restaurant\":\"Example Restaurant\",\"location\":\"999 Changed Ave, Boston, MA\",\"seating\":\"Standard\",\"selectedDate\":\"2099-08-15\",\"selectedTime\":\"7:15 PM\",\"partySize\":2,\"paymentRequired\":\"no\",\"paymentTerms\":\"No payment required at booking\",\"cancellationPolicy\":\"Cancel by 7:15 PM on Aug 14\",\"noShowPolicy\":\"No-show fee is $25 per person\",\"venueId\":\"venue-123\",\"formIdentity\":\"booking-form-abc\",\"finalButtonCount\":1,\"completeDisabled\":false}"}'
        ;;
      unknown-terms)
        printf '%s\n' '{"result":"{\"origin\":\"https://www.opentable.com\",\"path\":\"/booking/details\",\"routeValid\":true,\"restaurant\":\"Example Restaurant\",\"location\":\"123 Main St, Boston, MA\",\"seating\":\"Standard\",\"selectedDate\":\"2099-08-15\",\"selectedTime\":\"7:15 PM\",\"partySize\":2,\"paymentRequired\":\"unknown\",\"paymentTerms\":\"\",\"cancellationPolicy\":\"\",\"noShowPolicy\":\"\",\"venueId\":\"venue-123\",\"formIdentity\":\"booking-form-abc\",\"finalButtonCount\":1,\"completeDisabled\":false}"}'
        ;;
      date-mismatch)
        printf '%s\n' '{"result":"{\"origin\":\"https://www.opentable.com\",\"path\":\"/booking/details\",\"routeValid\":true,\"restaurant\":\"Example Restaurant\",\"location\":\"123 Main St, Boston, MA\",\"seating\":\"Standard\",\"selectedDate\":\"2099-08-16\",\"selectedTime\":\"7:15 PM\",\"partySize\":2,\"paymentRequired\":\"no\",\"paymentTerms\":\"No payment required at booking\",\"cancellationPolicy\":\"Cancel by 7:15 PM on Aug 14\",\"noShowPolicy\":\"No-show fee is $25 per person\",\"venueId\":\"venue-123\",\"formIdentity\":\"booking-form-abc\",\"finalButtonCount\":1,\"completeDisabled\":false}"}'
        ;;
      party-mismatch)
        printf '%s\n' '{"result":"{\"origin\":\"https://www.opentable.com\",\"path\":\"/booking/details\",\"routeValid\":true,\"restaurant\":\"Example Restaurant\",\"location\":\"123 Main St, Boston, MA\",\"seating\":\"Standard\",\"selectedDate\":\"2099-08-15\",\"selectedTime\":\"7:15 PM\",\"partySize\":3,\"paymentRequired\":\"no\",\"paymentTerms\":\"No payment required at booking\",\"cancellationPolicy\":\"Cancel by 7:15 PM on Aug 14\",\"noShowPolicy\":\"No-show fee is $25 per person\",\"venueId\":\"venue-123\",\"formIdentity\":\"booking-form-abc\",\"finalButtonCount\":1,\"completeDisabled\":false}"}'
        ;;
      cross-origin)
        printf '%s\n' '{"result":"{\"origin\":\"https://evil.example\",\"path\":\"/booking/details\",\"routeValid\":false,\"restaurant\":\"Example Restaurant\",\"location\":\"123 Main St, Boston, MA\",\"seating\":\"Standard\",\"selectedDate\":\"2099-08-15\",\"selectedTime\":\"7:15 PM\",\"partySize\":2,\"paymentRequired\":\"no\",\"paymentTerms\":\"No payment required at booking\",\"cancellationPolicy\":\"Cancel by 7:15 PM on Aug 14\",\"noShowPolicy\":\"No-show fee is $25 per person\",\"venueId\":\"venue-123\",\"formIdentity\":\"booking-form-abc\",\"finalButtonCount\":1,\"completeDisabled\":false}"}'
        ;;
      duplicate-button)
        printf '%s\n' '{"result":"{\"origin\":\"https://www.opentable.com\",\"path\":\"/booking/details\",\"routeValid\":true,\"restaurant\":\"Example Restaurant\",\"location\":\"123 Main St, Boston, MA\",\"seating\":\"Standard\",\"selectedDate\":\"2099-08-15\",\"selectedTime\":\"7:15 PM\",\"partySize\":2,\"paymentRequired\":\"no\",\"paymentTerms\":\"No payment required at booking\",\"cancellationPolicy\":\"Cancel by 7:15 PM on Aug 14\",\"noShowPolicy\":\"No-show fee is $25 per person\",\"venueId\":\"venue-123\",\"formIdentity\":\"booking-form-abc\",\"finalButtonCount\":2,\"completeDisabled\":false}"}'
        ;;
      *)
        printf '%s\n' '{"result":"{\"origin\":\"https://www.opentable.com\",\"path\":\"/booking/details\",\"routeValid\":true,\"restaurant\":\"Example Restaurant\",\"location\":\"123 Main St, Boston, MA\",\"seating\":\"Standard\",\"selectedDate\":\"2099-08-15\",\"selectedTime\":\"7:15 PM\",\"partySize\":2,\"paymentRequired\":\"no\",\"paymentTerms\":\"No payment required at booking\",\"cancellationPolicy\":\"Cancel by 7:15 PM on Aug 14\",\"noShowPolicy\":\"No-show fee is $25 per person\",\"venueId\":\"venue-123\",\"formIdentity\":\"booking-form-abc\",\"finalButtonCount\":1,\"completeDisabled\":false,\"secret\":\"SENSITIVE-DETAILS\"}"}'
        ;;
    esac
    ;;
  *OT_STEP_COMPLETE*)
    printf 'pinchtab:complete\n' >> "$FAKE_LOG"
    if [[ "${FAKE_MODE:-}" == "complete-transport-fail" ]]; then
      exit 9
    fi
    printf '%s\n' '{"result":"{\"status\":\"clicked\",\"secret\":\"SENSITIVE-COMMIT\"}"}'
    ;;
  *OT_STEP_CONFIRMATION*)
    printf 'pinchtab:confirmation\n' >> "$FAKE_LOG"
    if [[ "${FAKE_MODE:-}" == "confirm-fail" ]]; then
      printf '%s\n' '{"result":"{\"origin\":\"https://www.opentable.com\",\"path\":\"/booking/confirmation\",\"confirmed\":false,\"confirmationId\":\"\",\"restaurant\":\"Example Restaurant\",\"confirmedDate\":\"2099-08-15\",\"confirmedTime\":\"7:15 PM\",\"partySize\":2,\"venueId\":\"venue-123\",\"secret\":\"SENSITIVE-CONFIRMATION\"}"}'
    else
      printf '%s\n' '{"result":"{\"origin\":\"https://www.opentable.com\",\"path\":\"/booking/confirmation\",\"confirmed\":true,\"confirmationId\":\"confirmation-123\",\"restaurant\":\"Example Restaurant\",\"confirmedDate\":\"2099-08-15\",\"confirmedTime\":\"7:15 PM\",\"partySize\":2,\"venueId\":\"venue-123\",\"secret\":\"SENSITIVE-CONFIRMATION\"}"}'
    fi
    ;;
  *)
    exit 3
    ;;
esac
''',
        )

    @staticmethod
    def _write_executable(path: Path, content: str) -> None:
        path.write_text(content, encoding="utf-8")
        path.chmod(path.stat().st_mode | stat.S_IXUSR)

    def run_script(
        self,
        *args: str,
        fake_mode: str = "",
        details_variant: str = "",
        expected_max_delta: str = "",
        browser_smoke: bool = False,
    ) -> subprocess.CompletedProcess[str]:
        env = os.environ.copy()
        env.update(
            {
                "HOME": str(self.home),
                "PATH": f"{self.fake_bin}:{env['PATH']}",
                "FAKE_LOG": str(self.log),
                "FAKE_MODE": fake_mode,
                "FAKE_DETAILS_VARIANT": details_variant,
                "EXPECT_MAX_DELTA": expected_max_delta,
                "OPENTABLE_APPROVAL_CACHE_DIR": str(self.approval_cache),
                "OPENTABLE_APPROVAL_TTL_SECONDS": "300",
                "OPENTABLE_BROWSER_SMOKE_TEST": "1" if browser_smoke else "0",
            }
        )
        return subprocess.run(
            ["bash", str(SCRIPT), *args],
            check=False,
            capture_output=True,
            text=True,
            env=env,
        )

    @staticmethod
    def parse_output(result: subprocess.CompletedProcess[str]) -> dict:
        lines = result.stdout.splitlines()
        if len(lines) != 1:
            raise AssertionError(f"expected one JSON line, got: {result.stdout!r}")
        return json.loads(lines[0])

    def calls(self) -> list[str]:
        if not self.log.exists():
            return []
        return self.log.read_text(encoding="utf-8").splitlines()

    def preview(self, **kwargs: str) -> tuple[dict, subprocess.CompletedProcess[str]]:
        result = self.run_script(
            "Italian Brookline",
            "2099-08-15",
            "19:00",
            "2",
            **kwargs,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        return self.parse_output(result), result

    def state_path(self, approval_id: str) -> Path:
        return self.approval_cache / f"{approval_id}.json"

    def test_preview_creates_protected_exact_one_use_approval(self) -> None:
        output, _ = self.preview()

        self.assertEqual(output["status"], "ready_to_confirm")
        self.assertTrue(output["approvable"])
        self.assertRegex(output["approval_id"], r"^[A-Za-z0-9_-]{24,128}$")
        self.assertEqual(output["selected_date"], "2099-08-15")
        self.assertEqual(output["selected_party_size"], 2)
        self.assertEqual(output["payment_required"], "no")
        self.assertIn("No-show fee", output["no_show_policy"])
        self.assertNotIn("pinchtab:complete", self.calls())

        state_path = self.state_path(output["approval_id"])
        self.assertEqual(stat.S_IMODE(self.approval_cache.stat().st_mode), 0o700)
        self.assertEqual(stat.S_IMODE(state_path.stat().st_mode), 0o600)
        state = json.loads(state_path.read_text(encoding="utf-8"))
        self.assertEqual(state["status"], "pending")
        self.assertEqual(state["facts"]["location"], "123 Main St, Boston, MA")

    def test_all_tab_operations_use_the_managed_instance_scope(self) -> None:
        self.preview()

        calls = self.calls()
        self.assertIn("helper:open:fake-instance", calls)
        self.assertIn("helper:eval:fake-instance:fake-tab", calls)
        self.assertIn("helper:close:fake-instance:fake-tab", calls)

    def test_browser_smoke_validates_only_sanitized_origin_and_path(self) -> None:
        result = self.run_script(
            "Italian Brookline",
            "2099-08-15",
            "19:00",
            "2",
            browser_smoke=True,
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        output = self.parse_output(result)
        self.assertEqual(output["status"], "browser_ready")
        self.assertEqual(output["origin"], "https://www.opentable.com")
        self.assertEqual(output["path"], "/s")
        self.assertNotIn("SENSITIVE-SMOKE", result.stdout)
        self.assertNotIn("term=", result.stdout)
        self.assertIn("helper:eval:fake-instance:fake-tab", self.calls())
        self.assertIn("helper:close:fake-instance:fake-tab", self.calls())

    def test_browser_smoke_rejects_unapproved_contexts(self) -> None:
        for fake_mode in (
            "cross-origin-smoke",
            "wrong-path-smoke",
            "tab-not-found-smoke",
        ):
            with self.subTest(fake_mode=fake_mode):
                result = self.run_script(
                    "Italian Brookline",
                    "2099-08-15",
                    "19:00",
                    "2",
                    fake_mode=fake_mode,
                    browser_smoke=True,
                )
                self.assertEqual(result.returncode, 1)
                self.assertIn(
                    self.parse_output(result)["error_code"],
                    {"invalid_browser_context", "browser_smoke_failed"},
                )
                self.assertNotIn("SENSITIVE-SMOKE", result.stdout)

    def test_direct_confirm_without_approval_is_rejected(self) -> None:
        result = self.run_script("--confirm")

        self.assertEqual(result.returncode, 2)
        self.assertEqual(self.parse_output(result)["error_code"], "missing_approval_id")
        self.assertEqual(self.calls(), [])

    def test_commit_requires_and_consumes_exact_approval(self) -> None:
        preview, _ = self.preview()
        approval_id = preview["approval_id"]

        result = self.run_script("--confirm", approval_id)

        self.assertEqual(result.returncode, 0, result.stderr)
        output = self.parse_output(result)
        self.assertEqual(output["status"], "confirmed")
        self.assertEqual(output["approval_id"], approval_id)
        self.assertEqual(output["confirmation_id"], "confirmation-123")
        self.assertEqual(self.calls().count("pinchtab:complete"), 1)
        state = json.loads(self.state_path(approval_id).read_text(encoding="utf-8"))
        self.assertEqual(state["status"], "consumed")
        self.assertTrue(state["mutation_attempted"])
        self.assertEqual(state["outcome"], "confirmed")

    def test_commit_rejects_scope_override_without_consuming_approval(self) -> None:
        preview, _ = self.preview()

        result = self.run_script(
            "--confirm",
            preview["approval_id"],
            "Different Restaurant",
        )

        self.assertEqual(result.returncode, 2)
        self.assertEqual(self.parse_output(result)["error_code"], "approval_scope_override")
        state = json.loads(
            self.state_path(preview["approval_id"]).read_text(encoding="utf-8")
        )
        self.assertEqual(state["status"], "pending")
        self.assertNotIn("pinchtab:complete", self.calls())

    def test_changed_facts_consume_approval_without_clicking(self) -> None:
        preview, _ = self.preview()

        result = self.run_script(
            "--confirm",
            preview["approval_id"],
            details_variant="changed-location",
        )

        self.assertEqual(result.returncode, 3)
        self.assertEqual(self.parse_output(result)["error_code"], "approval_facts_changed")
        self.assertNotIn("pinchtab:complete", self.calls())
        state = json.loads(
            self.state_path(preview["approval_id"]).read_text(encoding="utf-8")
        )
        self.assertEqual(state["status"], "consumed")
        self.assertFalse(state["mutation_attempted"])

    def test_expired_approval_is_rejected_before_browser(self) -> None:
        preview, _ = self.preview()
        state_path = self.state_path(preview["approval_id"])
        state = json.loads(state_path.read_text(encoding="utf-8"))
        state["expires_at"] = 0
        state_path.write_text(json.dumps(state), encoding="utf-8")
        state_path.chmod(0o600)
        calls_before = list(self.calls())

        result = self.run_script("--confirm", preview["approval_id"])

        self.assertEqual(result.returncode, 3)
        self.assertEqual(self.parse_output(result)["error_code"], "approval_expired")
        self.assertEqual(self.calls(), calls_before)

    def test_approval_replay_is_rejected_before_second_browser_run(self) -> None:
        preview, _ = self.preview()
        first = self.run_script("--confirm", preview["approval_id"])
        self.assertEqual(first.returncode, 0, first.stderr)
        complete_count = self.calls().count("pinchtab:complete")

        replay = self.run_script("--confirm", preview["approval_id"])

        self.assertEqual(replay.returncode, 3)
        self.assertEqual(self.parse_output(replay)["error_code"], "approval_replayed")
        self.assertEqual(self.calls().count("pinchtab:complete"), complete_count)

    def test_unknown_payment_or_policy_terms_never_issue_approval(self) -> None:
        output, _ = self.preview(details_variant="unknown-terms")

        self.assertEqual(output["status"], "terms_unknown")
        self.assertFalse(output["approvable"])
        self.assertEqual(output["approval_id"], "")
        self.assertEqual(output["payment_required"], "unknown")
        self.assertEqual(output["cancellation_policy"], "unknown")
        self.assertNotIn("pinchtab:complete", self.calls())

    def test_date_and_party_mismatches_never_issue_approval(self) -> None:
        for variant in ("date-mismatch", "party-mismatch"):
            with self.subTest(variant=variant):
                output, _ = self.preview(details_variant=variant)
                self.assertEqual(output["status"], "request_mismatch")
                self.assertFalse(output["approvable"])
                self.assertEqual(output["approval_id"], "")

    def test_cross_origin_and_duplicate_button_fail_closed(self) -> None:
        cases = (
            ({"fake_mode": "cross-origin-slot"}, "invalid_browser_context"),
            ({"details_variant": "cross-origin"}, "invalid_browser_context"),
            ({"details_variant": "duplicate-button"}, "booking_not_ready"),
        )
        for options, expected_code in cases:
            with self.subTest(options=options):
                result = self.run_script(
                    "Italian Brookline",
                    "2099-08-15",
                    "19:00",
                    "2",
                    **options,
                )
                self.assertEqual(result.returncode, 1)
                self.assertEqual(self.parse_output(result)["error_code"], expected_code)
                self.assertNotIn("pinchtab:complete", self.calls())

    def test_opaque_renderer_is_distinct_from_true_no_availability(self) -> None:
        cases = (
            ("opaque-renderer", "availability_renderer_unavailable"),
            ("no-slots", "no_available_times"),
        )
        for fake_mode, expected_code in cases:
            with self.subTest(fake_mode=fake_mode):
                calls_before = list(self.calls())
                result = self.run_script(
                    "Italian Brookline",
                    "2099-08-15",
                    "19:00",
                    "2",
                    fake_mode=fake_mode,
                )
                self.assertEqual(result.returncode, 1)
                output = self.parse_output(result)
                self.assertEqual(output["error_code"], expected_code)
                self.assertNotIn("SENSITIVE", result.stdout)
                new_calls = self.calls()[len(calls_before) :]
                self.assertNotIn("pinchtab:details", new_calls)
                self.assertNotIn("pinchtab:complete", new_calls)

    def test_post_click_transport_failure_is_non_retryable_unknown(self) -> None:
        preview, _ = self.preview()

        result = self.run_script(
            "--confirm",
            preview["approval_id"],
            fake_mode="complete-transport-fail",
        )

        self.assertEqual(result.returncode, 4)
        output = self.parse_output(result)
        self.assertEqual(output["status"], "unknown")
        self.assertTrue(output["non_retryable"])
        self.assertTrue(output["reservation_may_exist"])
        self.assertTrue(output["mutation_attempted"])
        state = json.loads(
            self.state_path(preview["approval_id"]).read_text(encoding="utf-8")
        )
        self.assertTrue(state["mutation_attempted"])

    def test_confirmation_failure_is_unknown_and_redacts_browser_data(self) -> None:
        preview, _ = self.preview()

        result = self.run_script(
            "--confirm",
            preview["approval_id"],
            fake_mode="confirm-fail",
        )

        self.assertEqual(result.returncode, 4)
        output = self.parse_output(result)
        self.assertEqual(output["status"], "unknown")
        self.assertTrue(output["non_retryable"])
        self.assertNotIn("SENSITIVE", result.stdout)
        self.assertNotIn("SENSITIVE", result.stderr)
        self.assertNotIn("raw", output)
        self.assertNotIn("url", output)

    def test_time_window_and_invalid_inputs_fail_before_commit(self) -> None:
        outside = self.run_script(
            "Italian Brookline",
            "2099-08-15",
            "19:00",
            "2",
            fake_mode="outside",
        )
        self.assertEqual(outside.returncode, 1)
        self.assertEqual(self.parse_output(outside)["error_code"], "outside_time_window")

        cases = (
            (("Italian", "2099-02-30", "19:00", "2"), "invalid_date"),
            (("Italian", "2099-08-15", "24:00", "2"), "invalid_time"),
            (("Italian", "2099-08-15", "19:00", "21"), "invalid_party_size"),
            (
                ("--max-time-delta", "721", "Italian", "2099-08-15", "19:00", "2"),
                "invalid_max_time_delta",
            ),
        )
        for args, expected_code in cases:
            with self.subTest(args=args):
                calls_before = list(self.calls())
                result = self.run_script(*args)
                self.assertEqual(result.returncode, 2)
                self.assertEqual(self.parse_output(result)["error_code"], expected_code)
                self.assertEqual(self.calls(), calls_before)


if __name__ == "__main__":
    unittest.main()
