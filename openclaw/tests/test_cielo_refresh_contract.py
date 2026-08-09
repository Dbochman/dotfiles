#!/usr/bin/env python3
"""Static deployment contracts for the managed Cielo refresh path."""

from __future__ import annotations

import plistlib
import re
import subprocess
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
AUTH_HELPER = REPO_ROOT / "openclaw" / "bin" / "cielo-auth.py"
REAUTH_HELPER = REPO_ROOT / "openclaw" / "bin" / "cielo-reauth"
CONTROL_WRAPPER = REPO_ROOT / "openclaw" / "bin" / "cielo"
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

    def test_launch_agent_enables_bounded_headless_login(self) -> None:
        script = REFRESH_SCRIPT.read_text(encoding="utf-8")
        with LAUNCH_AGENT.open("rb") as handle:
            launch_agent = plistlib.load(handle)

        self.assertEqual(
            launch_agent["EnvironmentVariables"][
                "CIELO_ALLOW_HEADLESS_LOGIN"
            ],
            "true",
        )
        self.assertIn(
            'if [[ "${CIELO_ALLOW_HEADLESS_LOGIN:-false}" != "true" ]]',
            script,
        )
        self.assertIn(
            'if [[ -z "${CIELO_USERNAME:-}" ]] || '
            '[[ -z "${CIELO_PASSWORD:-}" ]]',
            script,
        )
        self.assertIn(
            '"Cielo headless login did not complete; attended recovery is required."',
            script,
        )
        self.assertIn("headless_login_backoff_status", script)
        self.assertNotIn("Login blocked by reCAPTCHA", script)
        self.assertLess(
            script.index(
                'CIELO_TAB_ID="$CIELO_TAB_ID" python3 '
                '"$GRAB_SCRIPT" "$CDP_PORT" --passive'
            ),
            script.index('LOGIN_RESULT=$('),
            "passive token capture must start before login submission",
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

    def test_current_refresh_contract_is_canonical(self) -> None:
        auth = AUTH_HELPER.read_text(encoding="utf-8")
        refresh = REFRESH_SCRIPT.read_text(encoding="utf-8")

        self.assertIn('REFRESH_PATH = "/web/token/refresh/1"', auth)
        self.assertIn('"locale": "en"', auth)
        self.assertNotIn('"/web/token/refresh"', auth)
        self.assertIn('AUTH_HELPER="$HOME/.openclaw/bin/cielo-auth.py"', refresh)
        self.assertNotIn("curl -s -X POST", refresh)

    def test_refresh_and_capture_share_atomic_auth_state(self) -> None:
        auth = AUTH_HELPER.read_text(encoding="utf-8")
        grab = GRAB_SCRIPT.read_text(encoding="utf-8")
        wrapper = CONTROL_WRAPPER.read_text(encoding="utf-8")

        self.assertIn("fcntl.flock", auth)
        self.assertIn("os.replace(temporary_path, path)", auth)
        self.assertIn("with auth_lock():", grab)
        self.assertIn('config["refreshTokenCapturedAt"]', grab)
        self.assertIn('AUTH_HELPER="$HOME/.openclaw/bin/cielo-auth.py"', wrapper)
        self.assertIn('refresh --quiet', wrapper)

    def test_attended_recovery_is_capture_first_and_bounded(self) -> None:
        helper = REAUTH_HELPER.read_text(encoding="utf-8")

        self.assertIn('LOGIN_URL = "https://home.cielowigle.com/auth/login"', helper)
        self.assertLess(
            helper.index('"--passive"'),
            helper.index('"awaiting_attended_login"'),
        )
        self.assertIn('refresh_token_captured', helper)
        self.assertIn('[str(AUTH_HELPER), "refresh", "--force"]', helper)
        self.assertIn('if args.command in ("start", "abort") and not args.attended', helper)


if __name__ == "__main__":
    unittest.main()
