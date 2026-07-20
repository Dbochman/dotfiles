#!/usr/bin/env python3
"""Deployment contracts for attended-only home-event services."""

from __future__ import annotations

import json
import os
import plistlib
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
OPENCLAW = REPO_ROOT / "openclaw"
WRAPPER = OPENCLAW / "bin" / "home-event-service-wrapper.sh"
PULL = OPENCLAW / "bin" / "dotfiles-pull.command"
LABELS = (
    "ai.openclaw.home-event-ingest",
    "ai.openclaw.home-event-correlator",
    "ai.openclaw.august-event-adapter",
    "ai.openclaw.nest-home-event-bridge",
)


class HomeEventDeploymentTests(unittest.TestCase):
    def test_plists_are_shadow_safe_and_wrapper_owned(self) -> None:
        for label in LABELS:
            with self.subTest(label=label):
                path = OPENCLAW / "launchagents" / f"{label}.plist"
                with path.open("rb") as handle:
                    payload = plistlib.load(handle)
                self.assertEqual(payload["Label"], label)
                self.assertEqual(
                    payload["ProgramArguments"][:2],
                    [
                        "/bin/bash",
                        "/Users/dbochman/.openclaw/bin/home-event-service-wrapper.sh",
                    ],
                )
                self.assertEqual(payload["StandardOutPath"], "/dev/null")
                self.assertEqual(payload["StandardErrorPath"], "/dev/null")
                self.assertEqual(payload["Umask"], 63)
        august = plistlib.loads(
            (OPENCLAW / "launchagents" / "ai.openclaw.august-event-adapter.plist").read_bytes()
        )
        self.assertEqual(
            august["EnvironmentVariables"]["HOME_EVENTS_AUGUST_ENABLED"], "0"
        )
        nest = plistlib.loads(
            (OPENCLAW / "launchagents" / "ai.openclaw.nest-home-event-bridge.plist").read_bytes()
        )
        self.assertEqual(
            nest["EnvironmentVariables"]["HOME_EVENTS_NEST_ENABLED"], "0"
        )
        producer_flags = (
            (
                "ai.openclaw.dog-walk-listener.plist",
                "HOME_EVENTS_RING_ENABLED",
            ),
            (
                "com.openclaw.presence-cabin.plist",
                "HOME_EVENTS_PRESENCE_ENABLED",
            ),
            (
                "com.openclaw.presence-receive.plist",
                "HOME_EVENTS_PRESENCE_ENABLED",
            ),
        )
        for filename, flag in producer_flags:
            with self.subTest(filename=filename):
                payload = plistlib.loads(
                    (OPENCLAW / "launchagents" / filename).read_bytes()
                )
                self.assertEqual(payload["EnvironmentVariables"][flag], "0")

    def test_daily_pull_refreshes_only_attended_installed_jobs(self) -> None:
        source = PULL.read_text(encoding="utf-8")
        for label in LABELS:
            self.assertIn(label, source)
        self.assertIn(
            'if [ -e "$HOME_EVENT_AGENT_DST" ] || [ -L "$HOME_EVENT_AGENT_DST" ]',
            source,
        )
        self.assertIn("remains unloaded pending explicit bootstrap", source)
        self.assertIn(
            "Print :EnvironmentVariables:HOME_EVENTS_AUGUST_ENABLED", source
        )
        self.assertIn(
            "Set :EnvironmentVariables:HOME_EVENTS_AUGUST_ENABLED", source
        )
        self.assertIn(
            "Print :EnvironmentVariables:HOME_EVENTS_NEST_ENABLED", source
        )
        self.assertIn(
            "Set :EnvironmentVariables:HOME_EVENTS_NEST_ENABLED", source
        )
        home_event_block = source[
            source.index("# Refresh home-event LaunchAgents") :
            source.index("# Symlink top-level bin/ scripts")
        ]
        self.assertNotIn("home-eventctl init", home_event_block)

    def test_wrapper_runs_safe_ingest_and_owns_bounded_log(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            home = Path(temporary) / "home"
            root = home / ".openclaw" / "home-events"
            bin_dir = home / ".openclaw" / "bin"
            for directory in (
                root,
                root / "config",
                root / "spool",
                root / "state",
                bin_dir,
                home / ".openclaw" / "logs",
            ):
                directory.mkdir(parents=True, exist_ok=True, mode=0o700)
                directory.chmod(0o700)
            fake = bin_dir / "home-eventctl"
            fake.write_text(
                "#!/bin/sh\nprintf '%s\\n' '{\"ok\":true,\"accepted\":0}'\n",
                encoding="utf-8",
            )
            fake.chmod(fake.stat().st_mode | stat.S_IXUSR)

            result = subprocess.run(
                ["bash", str(WRAPPER), "ingest"],
                env={**os.environ, "HOME": str(home)},
                capture_output=True,
                text=True,
                check=False,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            log_path = home / ".openclaw" / "logs" / "home-events.log"
            self.assertEqual(stat.S_IMODE(log_path.stat().st_mode), 0o600)
            line = log_path.read_text(encoding="utf-8")
            self.assertIn("component=ingest status=0", line)
            self.assertIn(json.dumps({"ok": True})[:1], line)


if __name__ == "__main__":
    unittest.main()
