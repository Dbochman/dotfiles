#!/usr/bin/env python3
"""Deployment contracts for attended-only home-event services."""

from __future__ import annotations

import json
import os
import plistlib
import shutil
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
    "ai.openclaw.presence-local-event-adapter",
    "ai.openclaw.vacancy-event-adapter",
)
DELIVERY_LABEL = "ai.openclaw.home-event-delivery"
CAMERA_LABEL = "ai.openclaw.home-event-camera"
ACTION_LABEL = "ai.openclaw.home-event-action"


class HomeEventDeploymentTests(unittest.TestCase):
    def test_ring_ingress_and_dog_walk_policy_have_distinct_service_contracts(self) -> None:
        ring_path = OPENCLAW / "launchagents" / "ai.openclaw.ring-event-listener.plist"
        dog_path = OPENCLAW / "launchagents" / "ai.openclaw.dog-walk-automation.plist"
        ring = plistlib.loads(ring_path.read_bytes())
        dog = plistlib.loads(dog_path.read_bytes())

        self.assertEqual(ring["Label"], "ai.openclaw.ring-event-listener")
        self.assertEqual(dog["Label"], "ai.openclaw.dog-walk-automation")
        self.assertIn("ring-event-listener-wrapper.sh", ring["ProgramArguments"][1])
        self.assertIn("dog-walk-automation-wrapper.sh", dog["ProgramArguments"][1])
        self.assertNotEqual(ring["StandardOutPath"], dog["StandardOutPath"])
        self.assertIn("HOME_EVENTS_RING_ENABLED", ring["EnvironmentVariables"])
        self.assertNotIn("HOME_EVENTS_RING_ENABLED", dog["EnvironmentVariables"])
        self.assertFalse(
            (OPENCLAW / "launchagents" / "ai.openclaw.dog-walk-listener.plist").exists()
        )

        skill = OPENCLAW / "skills" / "dog-walk"
        ring_entry = (skill / "ring-event-listener.py").read_text(encoding="utf-8")
        dog_entry = (skill / "dog-walk-automation.py").read_text(encoding="utf-8")
        self.assertIn("ring_event_listener_main", ring_entry)
        self.assertNotIn("dog_walk_automation_main", ring_entry)
        self.assertIn("dog_walk_automation_main", dog_entry)
        self.assertNotIn("ring_event_listener_main", dog_entry)

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
        delivery_plist = plistlib.loads(
            (
                OPENCLAW
                / "launchagents"
                / f"{DELIVERY_LABEL}.plist"
            ).read_bytes()
        )
        self.assertEqual(delivery_plist["Label"], DELIVERY_LABEL)
        self.assertEqual(
            delivery_plist["ProgramArguments"][:2],
            [
                "/bin/bash",
                "/Users/dbochman/.openclaw/bin/home-event-delivery-wrapper.sh",
            ],
        )
        self.assertEqual(delivery_plist["StandardOutPath"], "/dev/null")
        self.assertEqual(delivery_plist["StandardErrorPath"], "/dev/null")
        self.assertEqual(delivery_plist["Umask"], 63)
        for filename in ("home-event-delivery.py", "home-event-delivery-wrapper.sh"):
            path = OPENCLAW / "bin" / filename
            self.assertTrue(path.stat().st_mode & stat.S_IXUSR)
        camera_plist = plistlib.loads(
            (
                OPENCLAW / "launchagents" / f"{CAMERA_LABEL}.plist"
            ).read_bytes()
        )
        self.assertEqual(camera_plist["Label"], CAMERA_LABEL)
        self.assertEqual(
            camera_plist["ProgramArguments"][:2],
            [
                "/bin/bash",
                "/Users/dbochman/.openclaw/bin/home-event-camera-wrapper.sh",
            ],
        )
        self.assertEqual(camera_plist["StandardOutPath"], "/dev/null")
        self.assertEqual(camera_plist["StandardErrorPath"], "/dev/null")
        self.assertEqual(camera_plist["Umask"], 63)
        for filename in ("home-event-camera.py", "home-event-camera-wrapper.sh"):
            path = OPENCLAW / "bin" / filename
            self.assertTrue(path.stat().st_mode & stat.S_IXUSR)
        camera_wrapper = (
            OPENCLAW / "bin" / "home-event-camera-wrapper.sh"
        ).read_text(encoding="utf-8")
        self.assertIn(
            '"HOME_EVENTS_PRESENCE_STATE=$HOME/.openclaw/presence/state.json"',
            camera_wrapper,
        )
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
        local_presence = plistlib.loads(
            (
                OPENCLAW
                / "launchagents"
                / "ai.openclaw.presence-local-event-adapter.plist"
            ).read_bytes()
        )
        self.assertEqual(
            local_presence["EnvironmentVariables"][
                "HOME_EVENTS_LOCAL_PRESENCE_CABIN_ENABLED"
            ],
            "0",
        )
        self.assertEqual(
            local_presence["EnvironmentVariables"][
                "HOME_EVENTS_LOCAL_PRESENCE_CROSSTOWN_ENABLED"
            ],
            "0",
        )
        self.assertEqual(
            local_presence["ProgramArguments"][2],
            "presence-local",
        )
        local_presence_adapter = (
            OPENCLAW / "bin" / "presence-local-event-adapter.py"
        )
        self.assertTrue(local_presence_adapter.is_file())
        self.assertTrue(
            local_presence_adapter.stat().st_mode & stat.S_IXUSR,
            "local-presence adapter must be executable for the service wrapper",
        )
        vacancy = plistlib.loads(
            (
                OPENCLAW
                / "launchagents"
                / "ai.openclaw.vacancy-event-adapter.plist"
            ).read_bytes()
        )
        self.assertEqual(
            vacancy["EnvironmentVariables"]["HOME_EVENTS_VACANCY_CABIN_ENABLED"],
            "0",
        )
        self.assertEqual(
            vacancy["EnvironmentVariables"][
                "HOME_EVENTS_VACANCY_CROSSTOWN_ENABLED"
            ],
            "0",
        )
        self.assertEqual(vacancy["ProgramArguments"][2], "vacancy")
        action = plistlib.loads(
            (OPENCLAW / "launchagents" / f"{ACTION_LABEL}.plist").read_bytes()
        )
        self.assertEqual(action["Label"], ACTION_LABEL)
        self.assertEqual(
            action["ProgramArguments"][:2],
            [
                "/bin/bash",
                "/Users/dbochman/.openclaw/bin/home-event-action-wrapper.sh",
            ],
        )
        for filename in (
            "vacancy-event-adapter.py",
            "home_event_action.py",
            "home-event-action",
            "home-event-action-wrapper.sh",
        ):
            self.assertTrue((OPENCLAW / "bin" / filename).stat().st_mode & stat.S_IXUSR)
        producer_flags = (
            (
                "ai.openclaw.ring-event-listener.plist",
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
        for label in (*LABELS, DELIVERY_LABEL, CAMERA_LABEL, ACTION_LABEL):
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
        self.assertIn(
            "Print :EnvironmentVariables:HOME_EVENTS_LOCAL_PRESENCE_CABIN_ENABLED",
            source,
        )
        self.assertIn(
            "Set :EnvironmentVariables:HOME_EVENTS_LOCAL_PRESENCE_CABIN_ENABLED",
            source,
        )
        self.assertIn(
            "Print :EnvironmentVariables:HOME_EVENTS_LOCAL_PRESENCE_CROSSTOWN_ENABLED",
            source,
        )
        self.assertIn(
            "Set :EnvironmentVariables:HOME_EVENTS_LOCAL_PRESENCE_CROSSTOWN_ENABLED",
            source,
        )
        self.assertIn(
            "Print :EnvironmentVariables:HOME_EVENTS_VACANCY_CABIN_ENABLED",
            source,
        )
        self.assertIn(
            "Set :EnvironmentVariables:HOME_EVENTS_VACANCY_CROSSTOWN_ENABLED",
            source,
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

    def test_wrapper_runs_local_presence_disabled_without_creating_state(self) -> None:
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
            installed_adapter = bin_dir / "presence-local-event-adapter.py"
            shutil.copy2(
                OPENCLAW / "bin" / "presence-local-event-adapter.py",
                installed_adapter,
            )
            installed_adapter.chmod(0o700)

            result = subprocess.run(
                ["bash", str(WRAPPER), "presence-local"],
                env={
                    **os.environ,
                    "HOME": str(home),
                    "HOME_EVENTS_LOCAL_PRESENCE_CABIN_ENABLED": "0",
                    "HOME_EVENTS_LOCAL_PRESENCE_CROSSTOWN_ENABLED": "0",
                },
                capture_output=True,
                text=True,
                check=False,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertFalse(
                (root / "state" / "presence-local-adapter.json").exists()
            )
            log = (home / ".openclaw" / "logs" / "home-events.log").read_text(
                encoding="utf-8"
            )
            self.assertIn("component=presence-local status=0", log)
            self.assertIn('"mode":"disabled"', log)


if __name__ == "__main__":
    unittest.main()
