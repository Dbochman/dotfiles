#!/usr/bin/env python3
"""Static deployment contracts for the managed Cielo refresh path."""

from __future__ import annotations

import plistlib
import re
import subprocess
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
REFRESH_SCRIPT = (
    REPO_ROOT / "openclaw" / "workspace" / "scripts" / "cielo-refresh.sh"
)
GRAB_SCRIPT = (
    REPO_ROOT
    / "openclaw"
    / "workspace"
    / "scripts"
    / "grab-cielo-tokens.py"
)
LAUNCH_AGENT = (
    REPO_ROOT
    / "openclaw"
    / "launchagents"
    / "com.openclaw.cielo-refresh.plist"
)


class CieloRefreshContractTests(unittest.TestCase):
    def test_refresh_script_is_valid_bash(self) -> None:
        result = subprocess.run(
            ["/bin/bash", "-n", str(REFRESH_SCRIPT)],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_cielo_uses_a_dedicated_pinchtab_profile(self) -> None:
        script = REFRESH_SCRIPT.read_text(encoding="utf-8")
        with LAUNCH_AGENT.open("rb") as handle:
            launch_agent = plistlib.load(handle)

        self.assertIn(
            'PINCHTAB_PROFILE="${CIELO_PINCHTAB_PROFILE:-cielo}"',
            script,
        )
        self.assertEqual(
            launch_agent["EnvironmentVariables"]["CIELO_PINCHTAB_PROFILE"],
            "cielo",
        )

    def test_tab_operations_are_scoped_to_the_acquired_instance(self) -> None:
        script = REFRESH_SCRIPT.read_text(encoding="utf-8")

        self.assertIn('PINCHTAB_INSTANCE_URL=$("$PINCHTAB" instances', script)
        self.assertIn(
            'nav "https://home.cielowigle.com/" --new-tab --print-tab-id',
            script,
        )
        self.assertNotIn('"$PINCHTAB" instance navigate', script)
        self.assertIn(
            '"$PINCHTAB" --server "$PINCHTAB_INSTANCE_URL" \\\n'
            '      close "$CIELO_TAB_ID"',
            script,
        )
        self.assertIsNone(
            re.search(r'(?m)^\s*"\$PINCHTAB" eval\b', script),
            "Cielo refresh contains an unscoped PinchTab eval",
        )

    def test_active_capture_reuses_existing_session_metadata(self) -> None:
        script = GRAB_SCRIPT.read_text(encoding="utf-8")

        self.assertIn("if not passive and os.path.exists(CONFIG_FILE):", script)
        self.assertIn(
            'session_id = existing_config.get("sessionId") or None',
            script,
        )
        self.assertIn(
            'user_id = existing_config.get("userId") or None',
            script,
        )


if __name__ == "__main__":
    unittest.main()
