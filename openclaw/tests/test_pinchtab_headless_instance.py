#!/usr/bin/env python3
"""Instance-routing tests for the managed headless PinchTab helper."""

from __future__ import annotations

import os
import re
import runpy
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock


REPO_ROOT = Path(__file__).resolve().parents[2]
HELPER = REPO_ROOT / "openclaw" / "bin" / "pinchtab-headless-instance"
OPENTABLE_REFRESH = REPO_ROOT / "openclaw" / "bin" / "opentable-refresh-token.sh"
GROCERY_REORDER = (
    REPO_ROOT / "openclaw" / "workspace" / "scripts" / "grocery-reorder.py"
)


class PinchTabHeadlessInstanceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tempdir.cleanup)
        self.root = Path(self.tempdir.name)
        self.fake_pinchtab = self.root / "pinchtab"
        self.log = self.root / "calls.log"
        self.fake_pinchtab.write_text(
            r'''#!/usr/bin/env bash
set -euo pipefail
printf '%q ' "$@" >> "$FAKE_LOG"
printf '\n' >> "$FAKE_LOG"

if [[ "${1:-}" == "instances" && "${2:-}" == "--json" ]]; then
  printf '%s\n' '[{"id":"inst-test","port":"19868","mode":"headless","status":"running"}]'
  exit 0
fi

[[ "${1:-}" == "--server" && "${2:-}" == "http://127.0.0.1:19868" ]]
operation="${3:-}"
if [[ "${FAKE_ERROR_OPERATION:-}" == "$operation" ]]; then
  printf 'Error 404: tab fake-tab not found\n' >&2
  exit 0
fi

case "$operation" in
  nav)
    if [[ "${5:-}" == "--new-tab" ]]; then
      [[ "${4:-}" == "https://www.opentable.com/s" && "${6:-}" == "--print-tab-id" ]]
      printf 'fake-tab\n'
    else
      [[ "${4:-}" == "https://www.opentable.com/user/dining-dashboard" && "${5:-}" == "--tab" && "${6:-}" == "fake-tab" && "${7:-}" == "--json" ]]
      printf '%s\n' '{"success":true}'
    fi
    ;;
  snap)
    [[ "${4:-}" == "--tab" && "${5:-}" == "fake-tab" && "${6:-}" == "-i" && "${7:-}" == "-c" && "${8:-}" == "--max-tokens" && "${9:-}" == "6000" ]]
    printf '%s\n' 'e42:button "Continue"'
    ;;
  click)
    [[ "${4:-}" == "e42" && "${5:-}" == "--tab" && "${6:-}" == "fake-tab" && "${7:-}" == "--json" ]]
    printf '%s\n' '{"success":true}'
    ;;
  fill)
    [[ "${4:-}" == "e43" && "${5:-}" == "test@example.invalid" && "${6:-}" == "--tab" && "${7:-}" == "fake-tab" && "${8:-}" == "--json" ]]
    printf '%s\n' '{"success":true}'
    ;;
  eval)
    [[ "${4:-}" == "document.location.origin" && "${5:-}" == "--tab" && "${6:-}" == "fake-tab" && "${7:-}" == "--json" ]]
    printf '%s\n' '{"result":"https://www.opentable.com"}'
    ;;
  close)
    [[ "${4:-}" == "fake-tab" && "${5:-}" == "--json" ]]
    printf '%s\n' '{"success":true}'
    ;;
  *)
    exit 2
    ;;
esac
''',
            encoding="utf-8",
        )
        self.fake_pinchtab.chmod(self.fake_pinchtab.stat().st_mode | stat.S_IXUSR)

    def run_helper(
        self, *args: str, error_operation: str = ""
    ) -> subprocess.CompletedProcess[str]:
        env = os.environ.copy()
        env.update(
            {
                "FAKE_LOG": str(self.log),
                "FAKE_ERROR_OPERATION": error_operation,
                "PINCHTAB_BIN": str(self.fake_pinchtab),
                "TMPDIR": str(self.root),
            }
        )
        return subprocess.run(
            ["bash", str(HELPER), *args],
            check=False,
            capture_output=True,
            text=True,
            env=env,
        )

    def test_typed_tab_actions_target_the_acquired_instance_server(self) -> None:
        opened = self.run_helper(
            "open", "inst-test", "https://www.opentable.com/s"
        )
        navigated = self.run_helper(
            "navigate",
            "inst-test",
            "fake-tab",
            "https://www.opentable.com/user/dining-dashboard",
        )
        snapshot = self.run_helper("snap", "inst-test", "fake-tab")
        clicked = self.run_helper("click", "inst-test", "fake-tab", "e42")
        filled = self.run_helper(
            "fill",
            "inst-test",
            "fake-tab",
            "e43",
            "test@example.invalid",
        )
        evaluated = self.run_helper(
            "eval", "inst-test", "fake-tab", "document.location.origin"
        )
        closed = self.run_helper("close", "inst-test", "fake-tab")

        self.assertEqual(opened.returncode, 0, opened.stderr)
        self.assertEqual(opened.stdout.strip(), "fake-tab")
        self.assertEqual(navigated.returncode, 0, navigated.stderr)
        self.assertEqual(snapshot.returncode, 0, snapshot.stderr)
        self.assertIn('e42:button "Continue"', snapshot.stdout)
        self.assertEqual(clicked.returncode, 0, clicked.stderr)
        self.assertEqual(filled.returncode, 0, filled.stderr)
        self.assertEqual(evaluated.returncode, 0, evaluated.stderr)
        self.assertIn("https://www.opentable.com", evaluated.stdout)
        self.assertEqual(closed.returncode, 0, closed.stderr)

        scoped_calls = [
            line
            for line in self.log.read_text(encoding="utf-8").splitlines()
            if line.startswith("--server ")
        ]
        self.assertEqual(len(scoped_calls), 7)
        self.assertTrue(
            all("http://127.0.0.1:19868" in line for line in scoped_calls)
        )
        self.assertTrue(any(" nav " in f" {line} " for line in scoped_calls))
        self.assertTrue(any(" snap " in f" {line} " for line in scoped_calls))
        self.assertTrue(any(" click " in f" {line} " for line in scoped_calls))
        self.assertTrue(any(" fill " in f" {line} " for line in scoped_calls))
        self.assertTrue(any(" eval " in f" {line} " for line in scoped_calls))
        self.assertTrue(any(" close " in f" {line} " for line in scoped_calls))

    def test_zero_exit_cli_errors_fail_closed_for_every_tab_operation(self) -> None:
        cases = (
            (
                "nav",
                "open",
                ("open", "inst-test", "https://www.opentable.com/s"),
            ),
            (
                "nav",
                "navigate",
                (
                    "navigate",
                    "inst-test",
                    "fake-tab",
                    "https://www.opentable.com/user/dining-dashboard",
                ),
            ),
            ("snap", "snap", ("snap", "inst-test", "fake-tab")),
            ("click", "click", ("click", "inst-test", "fake-tab", "e42")),
            (
                "fill",
                "fill",
                (
                    "fill",
                    "inst-test",
                    "fake-tab",
                    "e43",
                    "test@example.invalid",
                ),
            ),
            (
                "eval",
                "eval",
                (
                    "eval",
                    "inst-test",
                    "fake-tab",
                    "document.location.origin",
                ),
            ),
            ("close", "close", ("close", "inst-test", "fake-tab")),
        )
        for cli_operation, helper_operation, args in cases:
            with self.subTest(operation=helper_operation):
                result = self.run_helper(*args, error_operation=cli_operation)
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(result.stdout, "")
                self.assertIn(f"PinchTab {helper_operation} failed", result.stderr)
                self.assertNotIn("fake-tab not found", result.stderr)

    def test_click_and_fill_accept_only_accessibility_refs(self) -> None:
        for action, args in (
            ("click", ("inst-test", "fake-tab", "text:Continue")),
            ("fill", ("inst-test", "fake-tab", "#email", "value")),
        ):
            with self.subTest(action=action):
                result = self.run_helper(action, *args)
                self.assertEqual(result.returncode, 2)
                self.assertIn("invalid accessibility reference", result.stderr)


class InstanceScopedConsumerTests(unittest.TestCase):
    def test_opentable_refresh_routes_every_tab_action_through_helper(self) -> None:
        source = OPENTABLE_REFRESH.read_text(encoding="utf-8")
        direct_action = re.compile(
            r"(?m)(?<![-\w])pinchtab\s+(?:snap|click|fill|eval|nav|close)\b"
        )

        self.assertIsNone(direct_action.search(source))
        for action in ("open", "snap", "click", "fill", "eval", "close"):
            self.assertIn(f'"$PINCHTAB_INSTANCE_HELPER" {action}', source)

    def test_grocery_eval_navigation_and_close_keep_instance_scope(self) -> None:
        scope = runpy.run_path(str(GROCERY_REORDER), run_name="grocery_reorder_test")
        runtime = scope["pt_eval"].__globals__
        runtime["PT_INSTANCE_ID"] = "inst-grocery"
        runtime["PT_INSTANCE_STARTED"] = "1"
        runtime["PT_TAB_ID"] = "tab-grocery"
        helper = scope["PINCHTAB_INSTANCE_HELPER"]
        calls: list[list[str]] = []

        def fake_run(argv: list[str], **_: object) -> subprocess.CompletedProcess[str]:
            calls.append(argv)
            stdout = '{"result":"ok"}' if argv[1] == "eval" else ""
            return subprocess.CompletedProcess(argv, 0, stdout=stdout, stderr="")

        with mock.patch.object(scope["subprocess"], "run", side_effect=fake_run):
            self.assertEqual(scope["pt_eval"]("1 + 1"), "ok")
            scope["pt_nav"]("https://www.starmarket.com/order-account/orders")
            scope["stop_browser"]()

        self.assertEqual(
            calls,
            [
                [helper, "eval", "inst-grocery", "tab-grocery", "1 + 1"],
                [
                    helper,
                    "navigate",
                    "inst-grocery",
                    "tab-grocery",
                    "https://www.starmarket.com/order-account/orders",
                ],
                [helper, "close", "inst-grocery", "tab-grocery"],
                [helper, "release", "inst-grocery", "1"],
            ],
        )
        source = GROCERY_REORDER.read_text(encoding="utf-8")
        self.assertNotRegex(
            source,
            r'\[PINCHTAB,\s*"(?:eval|nav|close)"',
        )


if __name__ == "__main__":
    unittest.main()
