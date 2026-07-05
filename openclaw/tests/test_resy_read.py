#!/usr/bin/env python3
"""Fake-only tests for the Resy skill's read and authorization boundaries."""

from __future__ import annotations

import os
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
WRAPPER = REPO_ROOT / "openclaw" / "skills" / "resy" / "resy-read"
SKILL = REPO_ROOT / "openclaw" / "skills" / "resy" / "SKILL.md"


class ResyReadTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tempdir.cleanup)
        root = Path(self.tempdir.name)
        self.log = root / "calls.log"
        self.fake = root / "resy"
        self.fake.write_text(
            """#!/usr/bin/env bash
set -euo pipefail
printf '%s\\n' "$*" >> "$FAKE_RESY_LOG"
case "$1" in
  availability)
    printf '%s\\n' 'Example — 2099-08-15 — party of 2' '  19:00-21:00  Dining Room  token: SECRET-CONFIG (cancel fee: $25)'
    ;;
  reservations)
    printf '%s\\n' 'Upcoming Reservations (1):' '    Date: 2099-08-15 at 19:00' '    Token: SECRET-CANCEL'
    ;;
  search)
    printf '%s\\n' '[123] Example Restaurant'
    ;;
esac
""",
            encoding="utf-8",
        )
        self.fake.chmod(self.fake.stat().st_mode | stat.S_IXUSR)

    def run_wrapper(self, *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [str(WRAPPER), *args],
            text=True,
            capture_output=True,
            env={
                **os.environ,
                "RESY_READ_BIN": str(self.fake),
                "FAKE_RESY_LOG": str(self.log),
            },
            check=False,
        )

    def test_only_read_only_commands_are_allowed(self) -> None:
        for command in ("book", "cancel", "snipe", "auth", "status"):
            with self.subTest(command=command):
                result = self.run_wrapper(command, "anything")
                self.assertNotEqual(result.returncode, 0)
        self.assertFalse(self.log.exists())

    def test_arbitrary_flags_and_malformed_arguments_are_rejected(self) -> None:
        cases = (
            ("search", "--json"),
            ("availability", "venue", "2099-08-15", "2"),
            ("availability", "123", "not-a-date", "2"),
            ("availability", "123", "2099-08-15", "21"),
            ("reservations", "--json"),
        )
        for args in cases:
            with self.subTest(args=args):
                self.assertNotEqual(self.run_wrapper(*args).returncode, 0)
        self.assertFalse(self.log.exists())

    def test_availability_and_reservations_redact_mutation_tokens(self) -> None:
        availability = self.run_wrapper("availability", "123", "2099-08-15", "2")
        self.assertEqual(availability.returncode, 0, availability.stderr)
        self.assertNotIn("SECRET-CONFIG", availability.stdout)
        self.assertIn("cancel fee", availability.stdout)

        reservations = self.run_wrapper("reservations")
        self.assertEqual(reservations.returncode, 0, reservations.stderr)
        self.assertNotIn("SECRET-CANCEL", reservations.stdout)
        self.assertIn("2099-08-15", reservations.stdout)

    def test_skill_allows_standing_authorized_raw_booking(self) -> None:
        text = SKILL.read_text(encoding="utf-8")
        self.assertIn("allowed-tools: Bash(resy-read:*)", text)
        self.assertIn("Bash(resy:*)", text)
        self.assertIn("standing user authorization", text)
        self.assertIn("Do not pause for exact-venue confirmation", text)
        self.assertIn("RESY_CACHE_ONLY=1", text)
        self.assertIn("never retry it", text)
        self.assertIn("Cancellation always requires a fresh, explicit user request", text)


if __name__ == "__main__":
    unittest.main()
