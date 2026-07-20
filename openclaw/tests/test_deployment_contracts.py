#!/usr/bin/env python3

from __future__ import annotations

import json
import os
import plistlib
import re
import stat
import subprocess
import tempfile
import time
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
DEPLOYMENT_LIB = REPO_ROOT / "openclaw" / "lib" / "deployment.sh"
INSTALLER = REPO_ROOT / "install.sh"
DOTFILES_PULL = REPO_ROOT / "openclaw" / "bin" / "dotfiles-pull.command"
GATEWAY_APP_WRAPPER = (
    REPO_ROOT
    / "openclaw"
    / "OpenClawGateway.app"
    / "Contents"
    / "MacOS"
    / "OpenClawGateway"
)
GATEWAY_RECOVERY_PLIST = (
    REPO_ROOT / "openclaw" / "launchagents" / "ai.openclaw.gateway.plist"
)
TOP_LEVEL_NEST = REPO_ROOT / "bin" / "nest"
OPENCLAW_NEST = REPO_ROOT / "openclaw" / "bin" / "nest"
NEST_SNAPSHOT_PLIST = (
    REPO_ROOT / "openclaw" / "launchagents" / "ai.openclaw.nest-snapshot.plist"
)
NEST_EVENT_LISTENER_WRAPPER = (
    REPO_ROOT / "openclaw" / "bin" / "nest-event-listener-wrapper.sh"
)
NEST_EVENT_LISTENER_PLIST = (
    REPO_ROOT
    / "openclaw"
    / "launchagents"
    / "ai.openclaw.nest-event-listener.plist"
)
NEST_ACTIVITY_REVIEWER_WRAPPER = (
    REPO_ROOT / "openclaw" / "bin" / "nest-activity-reviewer-wrapper.sh"
)
NEST_ACTIVITY_REVIEWER_PLIST = (
    REPO_ROOT
    / "openclaw"
    / "launchagents"
    / "ai.openclaw.nest-activity-reviewer.plist"
)

REQUIRED_HELPERS = {
    "bin/august": "august wrapper\n",
    "bin/pinchtab-headless-instance": "pinchtab helper\n",
    "bin/opentable-book": "opentable wrapper\n",
    "bin/opentable-reservations": "opentable reservations wrapper\n",
    "bin/restaurant-book": "restaurant coordinator wrapper\n",
    "bin/restaurant-book.py": "restaurant coordinator\n",
    "bin/restaurant-snipe": "snipe wrapper\n",
    "bin/resy-read": "resy wrapper\n",
    "workspace/scripts/opentable-book.sh": "opentable helper\n",
    "workspace/scripts/opentable-book-state.py": "state helper\n",
}
REQUIRED_PROTECTED_FILES = {
    "cron/restaurant-booking-scopes.json": (
        "restaurant-bookings/scopes.json",
        '{"schema_version":1,"jobs":{}}\n',
    ),
}
GATEWAY_RESTAURANT_COMMANDS = (
    "opentable-book",
    "opentable-reservations",
    "restaurant-book",
    "restaurant-snipe",
    "resy-read",
)


class DeploymentContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tempdir.cleanup)
        self.root = Path(self.tempdir.name)

    def run_bash(self, script: str, *args: Path) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["/bin/bash", "-c", script, "deployment-test", *(str(arg) for arg in args)],
            cwd=REPO_ROOT,
            env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
            capture_output=True,
            text=True,
            check=False,
        )

    def make_skill_fixture(self, root: Path, *, include_symlink: bool = False) -> None:
        (root / "nested" / "__pycache__").mkdir(parents=True)
        (root / ".pytest_cache").mkdir()
        (root / ".mypy_cache").mkdir()
        (root / ".ruff_cache").mkdir()
        (root / "SKILL.md").write_text("# Fixture\n", encoding="utf-8")
        (root / "nested" / "keep.py").write_text("print('keep')\n", encoding="utf-8")
        (root / "nested" / "__pycache__" / "keep.cpython-314.pyc").write_bytes(b"pyc")
        (root / "orphan.pyc").write_bytes(b"pyc")
        (root / "orphan.pyo").write_bytes(b"pyo")
        (root / ".DS_Store").write_bytes(b"finder")
        (root / "address.local.md").write_text("private override\n", encoding="utf-8")
        (root / ".pytest_cache" / "state").write_text("generated\n", encoding="utf-8")
        (root / ".mypy_cache" / "state").write_text("generated\n", encoding="utf-8")
        (root / ".ruff_cache" / "state").write_text("generated\n", encoding="utf-8")
        if include_symlink:
            (root / "nested-link").symlink_to(root / "SKILL.md")

    def assert_skill_copy_is_sanitized(self, destination: Path) -> None:
        self.assertEqual((destination / "SKILL.md").read_text(encoding="utf-8"), "# Fixture\n")
        self.assertTrue((destination / "nested" / "keep.py").is_file())
        self.assertFalse((destination / "nested" / "__pycache__").exists())
        self.assertFalse((destination / ".pytest_cache").exists())
        self.assertFalse((destination / ".mypy_cache").exists())
        self.assertFalse((destination / ".ruff_cache").exists())
        self.assertFalse((destination / "orphan.pyc").exists())
        self.assertFalse((destination / "orphan.pyo").exists())
        self.assertFalse((destination / ".DS_Store").exists())
        self.assertFalse((destination / "address.local.md").exists())
        self.assertFalse((destination / "nested-link").exists())
        self.assertFalse((destination / "nested-link").is_symlink())

    def test_shared_skill_copy_prunes_generated_private_and_symlink_artifacts(self) -> None:
        source = self.root / "skill-source"
        destination = self.root / "skill-destination"
        self.make_skill_fixture(source, include_symlink=True)

        completed = self.run_bash(
            'source "$1"\ncopy_openclaw_skill_tree "$2" "$3"',
            DEPLOYMENT_LIB,
            source,
            destination,
        )

        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assert_skill_copy_is_sanitized(destination)

    def test_installer_skill_copy_stays_idempotent_with_ignored_source_artifacts(self) -> None:
        source = self.root / "installer-source"
        destination = self.root / "installed" / "fixture"
        self.make_skill_fixture(source)

        completed = self.run_bash(
            '\n'.join(
                (
                    'source "$1"',
                    "DRY_RUN=false",
                    "FORCE=true",
                    "QUIET=true",
                    "VERBOSE=false",
                    "ITEMS_LINKED=1",
                    'deploy_openclaw_skill_copy "$2" "$3"',
                    'deploy_openclaw_skill_copy "$2" "$3"',
                    'test -f "$3/SKILL.md"',
                )
            ),
            INSTALLER,
            source,
            destination,
        )

        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assert_skill_copy_is_sanitized(destination)

    def make_guarded_helper_fixture(self, openclaw_source: Path) -> None:
        for relative, contents in REQUIRED_HELPERS.items():
            path = openclaw_source / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(contents, encoding="utf-8")
        for relative, (_, contents) in REQUIRED_PROTECTED_FILES.items():
            path = openclaw_source / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(contents, encoding="utf-8")

    def test_fresh_install_copies_required_guarded_helpers(self) -> None:
        source = self.root / "openclaw-source"
        destination = self.root / "openclaw-home"
        self.make_guarded_helper_fixture(source)

        completed = self.run_bash(
            '\n'.join(
                (
                    'source "$1"',
                    "DRY_RUN=false",
                    "QUIET=true",
                    "ITEMS_LINKED=1",
                    'install_openclaw_guarded_helpers "$2" "$3"',
                    "true",
                )
            ),
            INSTALLER,
            source,
            destination,
        )

        self.assertEqual(completed.returncode, 0, completed.stderr)
        for relative, contents in REQUIRED_HELPERS.items():
            deployed = destination / relative
            self.assertEqual(deployed.read_text(encoding="utf-8"), contents)
            self.assertEqual(stat.S_IMODE(deployed.stat().st_mode), 0o755)
        for _, (destination_relative, contents) in REQUIRED_PROTECTED_FILES.items():
            deployed = destination / destination_relative
            self.assertEqual(deployed.read_text(encoding="utf-8"), contents)
            self.assertEqual(stat.S_IMODE(deployed.stat().st_mode), 0o600)

    def test_guarded_helper_dry_run_reports_every_copy_without_writing(self) -> None:
        source = self.root / "openclaw-source"
        destination = self.root / "dry-run-home"
        self.make_guarded_helper_fixture(source)

        completed = self.run_bash(
            '\n'.join(
                (
                    'source "$1"',
                    "DRY_RUN=true",
                    "QUIET=false",
                    "ITEMS_LINKED=1",
                    'install_openclaw_guarded_helpers "$2" "$3"',
                    "true",
                )
            ),
            INSTALLER,
            source,
            destination,
        )

        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertFalse(destination.exists())
        self.assertEqual(
            completed.stdout.count("[dry-run] Would install"),
            len(REQUIRED_HELPERS) + len(REQUIRED_PROTECTED_FILES),
        )
        for relative in REQUIRED_HELPERS:
            self.assertIn(str(destination / relative), completed.stdout)
        for _, (destination_relative, _) in REQUIRED_PROTECTED_FILES.items():
            self.assertIn(str(destination / destination_relative), completed.stdout)

    def test_real_gateway_path_exposes_guarded_restaurant_wrappers(self) -> None:
        source = self.root / "openclaw-source"
        home = self.root / "home"
        destination = home / ".openclaw"
        self.make_guarded_helper_fixture(source)

        completed = self.run_bash(
            "\n".join(
                (
                    'source "$1"',
                    "DRY_RUN=false",
                    "QUIET=true",
                    "ITEMS_LINKED=1",
                    'install_openclaw_guarded_helpers "$2" "$3"',
                    "true",
                )
            ),
            INSTALLER,
            source,
            destination,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)

        app_text = GATEWAY_APP_WRAPPER.read_text(encoding="utf-8")
        pull_text = DOTFILES_PULL.read_text(encoding="utf-8")
        app_match = re.search(r'^export PATH="([^"]+)"$', app_text, re.MULTILINE)
        pull_match = re.search(
            r'^GATEWAY_RUNTIME_PATH="([^"]+)"$', pull_text, re.MULTILINE
        )
        self.assertIsNotNone(app_match)
        self.assertIsNotNone(pull_match)
        path_expression = app_match.group(1)
        self.assertEqual(path_expression, pull_match.group(1))
        self.assertTrue(path_expression.startswith("$HOME/.openclaw/bin:"))
        self.assertIn("/opt/homebrew/opt/node@22/bin", path_expression)
        self.assertIn('export PATH="$GATEWAY_RUNTIME_PATH"', pull_text)
        self.assertNotIn('export PATH="$BIN_DST:', pull_text)

        with GATEWAY_RECOVERY_PLIST.open("rb") as plist_file:
            recovery_plist = plistlib.load(plist_file)
        recovery_path = recovery_plist["EnvironmentVariables"]["PATH"]
        self.assertEqual(
            path_expression.replace("$HOME", "/Users/dbochman"), recovery_path
        )

        gateway_path = path_expression.replace("$HOME", str(home))
        command_check = subprocess.run(
            [
                "/bin/sh",
                "-c",
                "set -e\n"
                + "\n".join(
                    f'command -v "{name}"' for name in GATEWAY_RESTAURANT_COMMANDS
                ),
            ],
            env={**os.environ, "HOME": str(home), "PATH": gateway_path},
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(command_check.returncode, 0, command_check.stderr)
        self.assertEqual(
            command_check.stdout.splitlines(),
            [str(destination / "bin" / name) for name in GATEWAY_RESTAURANT_COMMANDS],
        )
        self.assertIn(
            "GATEWAY_REQUIRED_SKILLS=(opentable restaurant-book restaurant-snipe resy)",
            pull_text,
        )
        self.assertIn("gateway call skills.status --json", pull_text)
        self.assertIn("openclaw_gateway_missing_skills_from_status", pull_text)
        self.assertIn("active restaurant skill catalog passed", pull_text)
        self.assertNotIn("openclaw skills check --json", pull_text)
        self.assertIn('if [ "$DEPLOYMENT_SMOKE_FAILED" -ne 0 ]', pull_text)
        self.assertIn("GATEWAY_WRAPPER_HASH_STATE", pull_text)
        self.assertIn("GATEWAY_ACTIVATED_HASH", pull_text)
        self.assertIn("GATEWAY_RESTART_REQUIRED=1", pull_text)
        self.assertIn("GATEWAY_PID_AFTER", pull_text)
        self.assertIn(
            'mv -f "$GATEWAY_HASH_STATE_TMP" "$GATEWAY_WRAPPER_HASH_STATE"',
            pull_text,
        )
        self.assertIn(
            '/bin/launchctl kickstart -k "$GATEWAY_DOMAIN/$GATEWAY_LABEL"', pull_text
        )
        self.assertIn(
            'atomic_install_executable "$wrapper" "$BIN_DST/$fname"', pull_text
        )
        self.assertIn(
            'atomic_install_managed_file "$RESTAURANT_SCOPES_SRC" "$RESTAURANT_SCOPES_DST" 600',
            pull_text,
        )
        self.assertNotIn('cp "$wrapper" "$BIN_DST/$fname"', pull_text)

        live_check_index = pull_text.index("# Query the active Gateway explicitly")
        activated_state_index = pull_text.index(
            'mv -f "$GATEWAY_HASH_STATE_TMP" "$GATEWAY_WRAPPER_HASH_STATE"'
        )
        self.assertGreater(activated_state_index, live_check_index)

    def test_gateway_skill_status_parser_is_authoritative_and_fails_closed(self) -> None:
        required = ("opentable", "restaurant-book", "restaurant-snipe", "resy")

        def parse(payload: object) -> subprocess.CompletedProcess[str]:
            return subprocess.run(
                [
                    "/bin/bash",
                    "-c",
                    'source "$1"\n'
                    'openclaw_gateway_missing_skills_from_status "${@:2}"',
                    "deployment-test",
                    str(DEPLOYMENT_LIB),
                    *required,
                ],
                cwd=REPO_ROOT,
                env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
                input=json.dumps(payload),
                capture_output=True,
                text=True,
                check=False,
            )

        ready = parse(
            {
                "agentId": "main",
                "skills": [
                    {"name": name, "eligible": True, "modelVisible": True}
                    for name in required
                ],
            }
        )
        self.assertEqual(ready.returncode, 0, ready.stderr)
        self.assertEqual(ready.stdout, "\n")

        missing = parse(
            {
                "agentId": "main",
                "skills": [
                    {"name": "opentable", "eligible": True, "modelVisible": True},
                    {
                        "name": "restaurant-book",
                        "eligible": False,
                        "modelVisible": False,
                    },
                    {
                        "name": "restaurant-snipe",
                        "eligible": True,
                        "modelVisible": False,
                    },
                ],
            }
        )
        self.assertEqual(missing.returncode, 0, missing.stderr)
        self.assertEqual(
            missing.stdout.strip(), "restaurant-book,restaurant-snipe,resy"
        )

        local_fallback_shape = parse(
            {
                "eligible": list(required),
                "modelVisible": list(required),
            }
        )
        self.assertEqual(local_fallback_shape.returncode, 2)
        self.assertEqual(local_fallback_shape.stdout, "")

    def test_both_deployers_use_the_shared_filter_and_fresh_install_hook(self) -> None:
        install_text = INSTALLER.read_text(encoding="utf-8")
        pull_text = DOTFILES_PULL.read_text(encoding="utf-8")

        for relative in REQUIRED_HELPERS:
            self.assertTrue((REPO_ROOT / "openclaw" / relative).is_file(), relative)
        self.assertIn('source "$DOTFILES_DIR/openclaw/lib/deployment.sh"', install_text)
        self.assertIn('copy_openclaw_skill_tree "$src" "$staged"', install_text)
        self.assertIn("install_openclaw_guarded_helpers \\", install_text)
        self.assertIn('source "$REPO/openclaw/lib/deployment.sh"', pull_text)
        self.assertIn(
            'copy_openclaw_skill_tree "$skill_dir" "$SKILLS_DST/$skill_name"',
            pull_text,
        )
        self.assertLess(
            pull_text.index("# Sync files the Crosstown MBP runs"),
            pull_text.index("# Deploy skills as real copies"),
        )
        self.assertIn("MBP_PROTOCOL_SYNC_FAILED", pull_text)
        self.assertIn("presence-devices.json", pull_text)
        self.assertIn("validate-config crosstown", pull_text)
        self.assertIn("validate-config cabin", pull_text)
        self.assertIn("preserved prior scanner", pull_text)
        self.assertIn("HOME_EVENT_SCHEMA_DEPLOY_READY", pull_text)
        self.assertIn("NEST_EVENT_SCHEMA_DEPLOY_READY", pull_text)
        self.assertIn(
            "nest-activity-reviewer.py|nest-activity-reviewer-wrapper.sh",
            pull_text,
        )
        self.assertIn("home-event migration required; preserving prior runtime", pull_text)
        self.assertIn("Nest listener migration required; preserving prior runtime", pull_text)
        self.assertIn(".openclaw/august/config.json", pull_text)
        self.assertIn(".openclaw/rest980/env-10max", pull_text)
        self.assertIn(".openclaw/rest980/env-j5", pull_text)

    def test_scheduled_nest_path_uses_the_hardened_cli(self) -> None:
        plist_text = NEST_SNAPSHOT_PLIST.read_text(encoding="utf-8")
        nest_text = TOP_LEVEL_NEST.read_text(encoding="utf-8")

        self.assertIn("/opt/homebrew/bin/nest snapshot", plist_text)
        self.assertEqual(TOP_LEVEL_NEST.read_bytes(), OPENCLAW_NEST.read_bytes())
        self.assertIn("NEST_CLIENT_ID", nest_text)
        self.assertIn("_get_cielo_status", nest_text)
        self.assertIn("_get_mysa_status", nest_text)
        self.assertIn("'source': 'cielo'", nest_text)
        self.assertIn("'source': 'mysa'", nest_text)

    def test_nest_event_listener_deployment_is_private_and_explicit(self) -> None:
        wrapper_text = NEST_EVENT_LISTENER_WRAPPER.read_text(encoding="utf-8")
        pull_text = DOTFILES_PULL.read_text(encoding="utf-8")
        with NEST_EVENT_LISTENER_PLIST.open("rb") as plist_file:
            listener_plist = plistlib.load(plist_file)

        self.assertEqual(
            listener_plist["Label"], "ai.openclaw.nest-event-listener"
        )
        self.assertTrue(listener_plist["RunAtLoad"])
        self.assertTrue(listener_plist["KeepAlive"])
        self.assertEqual(listener_plist["ThrottleInterval"], 60)
        self.assertEqual(listener_plist["Umask"], 0o77)
        self.assertEqual(listener_plist["StandardOutPath"], "/dev/null")
        self.assertEqual(listener_plist["StandardErrorPath"], "/dev/null")
        environment = listener_plist["EnvironmentVariables"]
        self.assertEqual(environment["NEST_EVENT_MODE"], "shadow")
        self.assertEqual(
            environment["NEST_EVENT_SUBSCRIPTION"], "openclaw-nest-events"
        )
        self.assertNotIn("GOOGLE_APPLICATION_CREDENTIALS", environment)

        self.assertIn("umask 077", wrapper_text)
        self.assertIn(".openclaw/venvs/nest-events/bin/python", wrapper_text)
        self.assertIn(
            'CREDENTIAL_FILE="$CREDENTIAL_DIR/subscriber-service-account.json"',
            wrapper_text,
        )
        self.assertIn('CONFIG_FILE="$CONFIG_DIR/cameras.json"', wrapper_text)
        self.assertIn('require_private_dir "$STATE_DIR"', wrapper_text)
        self.assertIn('require_private_file "$CREDENTIAL_FILE"', wrapper_text)
        self.assertIn('require_private_file "$CONFIG_FILE"', wrapper_text)
        self.assertIn(
            'GOOGLE_APPLICATION_CREDENTIALS="$CREDENTIAL_FILE"', wrapper_text
        )
        self.assertIn("exec /usr/bin/env -i", wrapper_text)
        self.assertIn("LOG_LIMIT_BYTES=262144", wrapper_text)
        self.assertIn("bounded_log_stream", wrapper_text)
        self.assertIn("/usr/bin/mkfifo -m 600", wrapper_text)
        self.assertNotIn("/opt/homebrew/bin/uv", wrapper_text)
        self.assertNotIn("gcloud", wrapper_text)
        self.assertNotRegex(wrapper_text, r"(?:^|[ /])op(?:[ \"']|$)")

        self.assertIn(
            'NEST_EVENT_AGENT_LABEL="ai.openclaw.nest-event-listener"', pull_text
        )
        self.assertIn(
            'if [ -e "$NEST_EVENT_AGENT_DST" ] || [ -L "$NEST_EVENT_AGENT_DST" ]',
            pull_text,
        )
        self.assertIn(
            "LaunchAgent remains unloaded pending explicit bootstrap", pull_text
        )
        self.assertIn("NEST_EVENT_RUNTIME_CHANGED=0", pull_text)
        self.assertIn(
            "nest-event-listener.py|nest-event-listener-wrapper.sh", pull_text
        )
        self.assertIn(
            '[ "$NEST_EVENT_AGENT_CHANGED" -eq 1 ] || [ "$NEST_EVENT_RUNTIME_CHANGED" -eq 1 ]',
            pull_text,
        )
        self.assertIn("NEST_EVENT_RELOAD_OK=0", pull_text)
        self.assertIn("NEST_EVENT_RELOAD_ATTEMPT in 1 2 3 4 5", pull_text)
        self.assertIn("FATAL listener reload failed", pull_text)
        self.assertNotIn('mkdir -p "$HOME/.openclaw/nest-events', pull_text)

    def test_nest_event_wrapper_uses_only_protected_runtime_inputs(self) -> None:
        home = self.root / "home"
        base = home / ".openclaw" / "nest-events"
        credential_dir = base / "credentials"
        config_dir = base / "config"
        state_dir = base / "state"
        log_dir = home / ".openclaw" / "logs"
        bin_dir = home / ".openclaw" / "bin"
        venv_bin = home / ".openclaw" / "venvs" / "nest-events" / "bin"
        for directory in (base, credential_dir, config_dir, state_dir):
            directory.mkdir(parents=True, exist_ok=True)
            directory.chmod(0o700)
        for directory in (log_dir, bin_dir, venv_bin):
            directory.mkdir(parents=True, exist_ok=True)

        credential = credential_dir / "subscriber-service-account.json"
        config = config_dir / "cameras.json"
        credential.write_text("{}\n", encoding="utf-8")
        config.write_text("{}\n", encoding="utf-8")
        credential.chmod(0o600)
        config.chmod(0o600)
        listener = bin_dir / "nest-event-listener.py"
        listener.write_text("# fixture\n", encoding="utf-8")
        listener.chmod(0o755)
        interpreter = venv_bin / "python"
        interpreter.write_text(
            "#!/bin/bash\n"
            "printf 'args=%s\\n' \"$*\"\n"
            "printf 'credential=%s\\n' \"$GOOGLE_APPLICATION_CREDENTIALS\"\n"
            "printf 'leaked=%s\\n' \"${UNRELATED_SECRET-unset}\"\n",
            encoding="utf-8",
        )
        interpreter.chmod(0o755)

        environment = {
            **os.environ,
            "HOME": str(home),
            "UNRELATED_SECRET": "must-not-reach-child",
        }
        for key in (
            "NEST_EVENT_MODE",
            "NEST_EVENT_SUBSCRIPTION",
            "NEST_EVENT_CONFIG",
            "NEST_EVENT_STATE_DIR",
        ):
            environment.pop(key, None)
        credential.chmod(0o644)
        config.chmod(0o644)
        completed = subprocess.run(
            [str(NEST_EVENT_LISTENER_WRAPPER), "status"],
            env=environment,
            capture_output=True,
            text=True,
            check=False,
        )

        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertIn("args=" + str(listener) + " status", completed.stdout)
        self.assertIn("credential=" + str(credential), completed.stdout)
        self.assertIn("leaked=unset", completed.stdout)
        self.assertFalse((log_dir / "nest-event-listener.log").exists())
        self.assertFalse((log_dir / "nest-event-listener.err.log").exists())

        config.chmod(0o600)
        state_dir.rmdir()
        checked = subprocess.run(
            [str(NEST_EVENT_LISTENER_WRAPPER), "check-config"],
            env=environment,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(checked.returncode, 0, checked.stderr)
        self.assertIn("args=" + str(listener) + " check-config", checked.stdout)
        self.assertIn("leaked=unset", checked.stdout)

        state_dir.mkdir(mode=0o700)
        migrated = subprocess.run(
            [str(NEST_EVENT_LISTENER_WRAPPER), "migrate"],
            env=environment,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(migrated.returncode, 0, migrated.stderr)
        self.assertIn("args=" + str(listener) + " migrate", migrated.stdout)
        self.assertIn("leaked=unset", migrated.stdout)
        self.assertFalse((log_dir / "nest-event-listener.log").exists())

        unbounded_migrate = subprocess.run(
            [str(NEST_EVENT_LISTENER_WRAPPER), "migrate", "unexpected"],
            env=environment,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertNotEqual(unbounded_migrate.returncode, 0)
        self.assertIn("command is invalid", unbounded_migrate.stderr)

        failed = subprocess.run(
            [str(NEST_EVENT_LISTENER_WRAPPER), "run", "--once"],
            env=environment,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertNotEqual(failed.returncode, 0)
        log_path = log_dir / "nest-event-listener.log"
        self.assertEqual(stat.S_IMODE(log_path.stat().st_mode), 0o600)
        error_text = (log_dir / "nest-event-listener.err.log").read_text(
            encoding="utf-8"
        )
        self.assertIn("private file ownership or mode is invalid", error_text)
        self.assertNotIn(str(credential), error_text)

        credential.chmod(0o600)
        mismatched = subprocess.run(
            [str(NEST_EVENT_LISTENER_WRAPPER), "check-config"],
            env={**environment, "NEST_EVENT_MODE": "active"},
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertNotEqual(mismatched.returncode, 0)
        self.assertIn("event mode is invalid", mismatched.stderr)

    def test_nest_activity_reviewer_deployment_is_private_and_attended(self) -> None:
        wrapper_text = NEST_ACTIVITY_REVIEWER_WRAPPER.read_text(encoding="utf-8")
        pull_text = DOTFILES_PULL.read_text(encoding="utf-8")
        with NEST_ACTIVITY_REVIEWER_PLIST.open("rb") as plist_file:
            reviewer_plist = plistlib.load(plist_file)

        self.assertEqual(
            reviewer_plist["Label"], "ai.openclaw.nest-activity-reviewer"
        )
        self.assertTrue(reviewer_plist["RunAtLoad"])
        self.assertTrue(reviewer_plist["KeepAlive"])
        self.assertEqual(reviewer_plist["ThrottleInterval"], 60)
        self.assertEqual(reviewer_plist["Umask"], 0o77)
        self.assertEqual(reviewer_plist["StandardOutPath"], "/dev/null")
        self.assertEqual(reviewer_plist["StandardErrorPath"], "/dev/null")
        environment = reviewer_plist["EnvironmentVariables"]
        self.assertEqual(environment["NEST_ACTIVITY_MODE"], "cabin-commentary")
        self.assertNotIn("OPENCLAW_DYLAN_IMESSAGE_TARGET", environment)
        self.assertNotIn("DYLAN_CHAT_ID", environment)
        self.assertNotIn("GOOGLE_APPLICATION_CREDENTIALS", environment)

        self.assertIn("umask 077", wrapper_text)
        self.assertIn('SECRETS_CACHE="$HOME/.openclaw/.secrets-cache"', wrapper_text)
        self.assertIn('require_private_file "$SECRETS_CACHE"', wrapper_text)
        self.assertIn("resolve_chat_target", wrapper_text)
        self.assertIn("exec /usr/bin/env -i", wrapper_text)
        self.assertIn("LOG_LIMIT_BYTES=262144", wrapper_text)
        self.assertIn("bounded_log_stream", wrapper_text)
        self.assertIn("/usr/bin/mkfifo -m 600", wrapper_text)
        self.assertNotRegex(wrapper_text, r"(?:^|[ /])op(?:[ \"']|$)")
        self.assertNotIn("OPENCLAW_GATEWAY_TOKEN", wrapper_text)

        self.assertIn(
            'NEST_ACTIVITY_AGENT_LABEL="ai.openclaw.nest-activity-reviewer"',
            pull_text,
        )
        self.assertIn("NEST_ACTIVITY_RUNTIME_CHANGED=0", pull_text)
        self.assertIn(
            "nest-activity-reviewer.py|nest-activity-reviewer-wrapper.sh",
            pull_text,
        )
        self.assertIn("NEST_ACTIVITY_RELOAD_ATTEMPT in 1 2 3 4 5", pull_text)
        self.assertIn("FATAL reviewer reload failed", pull_text)
        self.assertIn(
            "LaunchAgent remains unloaded pending explicit bootstrap", pull_text
        )

    def test_nest_activity_wrapper_exports_only_validated_chat_target(self) -> None:
        home = self.root / "reviewer-home"
        base = home / ".openclaw" / "nest-events"
        state_dir = base / "state"
        log_dir = home / ".openclaw" / "logs"
        bin_dir = home / ".openclaw" / "bin"
        workspace_scripts = home / ".openclaw" / "workspace" / "scripts"
        for directory in (base, state_dir):
            directory.mkdir(parents=True, exist_ok=True)
            directory.chmod(0o700)
        for directory in (log_dir, bin_dir, workspace_scripts):
            directory.mkdir(parents=True, exist_ok=True)

        database = state_dir / "events.sqlite3"
        database.write_bytes(b"fixture")
        database.chmod(0o600)
        cache = home / ".openclaw" / ".secrets-cache"
        cache.write_text(
            "DYLAN_CHAT_ID=7\nUNRELATED_CACHE_SECRET=cache-secret\n",
            encoding="utf-8",
        )
        cache.chmod(0o600)
        reviewer = bin_dir / "nest-activity-reviewer.py"
        reviewer.write_text(
            "#!/usr/bin/python3\n"
            "import os, sys\n"
            "print('args=' + ' '.join(sys.argv[1:]))\n"
            "print('target=' + os.environ.get('OPENCLAW_DYLAN_IMESSAGE_TARGET', 'unset'))\n"
            "print('outer=' + os.environ.get('UNRELATED_OUTER_SECRET', 'unset'))\n"
            "print('cache=' + os.environ.get('UNRELATED_CACHE_SECRET', 'unset'))\n",
            encoding="utf-8",
        )
        reviewer.chmod(0o755)
        presence_observer = workspace_scripts / "presence-detect.sh"
        presence_observer.write_text("#!/bin/bash\nexit 0\n", encoding="utf-8")
        presence_observer.chmod(0o755)

        environment = {
            **os.environ,
            "HOME": str(home),
            "UNRELATED_OUTER_SECRET": "outer-secret",
        }
        for key in (
            "NEST_ACTIVITY_MODE",
            "NEST_EVENT_STATE_DIR",
            "NEST_EVENT_DATABASE",
            "NEST_ACTIVITY_STATE_FILE",
            "NEST_ACTIVITY_IMAGE_DIR",
            "NEST_ACTIVITY_LOCK_FILE",
            "OPENCLAW_PRESENCE_STATE",
            "OPENCLAW_PRESENCE_CABIN_SCAN",
            "OPENCLAW_PRESENCE_CROSSTOWN_SCAN",
            "OPENCLAW_PRESENCE_OBSERVER",
            "OPENCLAW_DYLAN_IMESSAGE_TARGET",
            "DYLAN_CHAT_ID",
        ):
            environment.pop(key, None)

        completed = subprocess.run(
            [str(NEST_ACTIVITY_REVIEWER_WRAPPER), "status"],
            env={
                **environment,
                "OPENCLAW_DYLAN_IMESSAGE_TARGET": "chat_id:8",
                "DYLAN_CHAT_ID": "9",
            },
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertIn("args=status", completed.stdout)
        self.assertIn("target=chat_id:7", completed.stdout)
        self.assertIn("outer=unset", completed.stdout)
        self.assertIn("cache=unset", completed.stdout)
        self.assertNotIn("cache-secret", completed.stdout + completed.stderr)
        self.assertNotIn("outer-secret", completed.stdout + completed.stderr)
        self.assertFalse((log_dir / "nest-activity-reviewer.log").exists())

        cache.chmod(0o644)
        failed = subprocess.run(
            [str(NEST_ACTIVITY_REVIEWER_WRAPPER), "status"],
            env=environment,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertNotEqual(failed.returncode, 0)
        self.assertIn("private file ownership or mode is invalid", failed.stderr)
        self.assertNotIn("chat_id:7", failed.stdout + failed.stderr)

    def test_nest_event_run_logs_stay_bounded_while_listener_is_healthy(self) -> None:
        home = self.root / "bounded-home"
        base = home / ".openclaw" / "nest-events"
        credential_dir = base / "credentials"
        config_dir = base / "config"
        state_dir = base / "state"
        log_dir = home / ".openclaw" / "logs"
        bin_dir = home / ".openclaw" / "bin"
        venv_bin = home / ".openclaw" / "venvs" / "nest-events" / "bin"
        for directory in (base, credential_dir, config_dir, state_dir):
            directory.mkdir(parents=True, exist_ok=True)
            directory.chmod(0o700)
        for directory in (log_dir, bin_dir, venv_bin):
            directory.mkdir(parents=True, exist_ok=True)

        credential = credential_dir / "subscriber-service-account.json"
        config = config_dir / "cameras.json"
        credential.write_text("{}\n", encoding="utf-8")
        config.write_text("{}\n", encoding="utf-8")
        credential.chmod(0o600)
        config.chmod(0o600)
        listener = bin_dir / "nest-event-listener.py"
        listener.write_text("# fixture\n", encoding="utf-8")
        listener.chmod(0o755)
        interpreter = venv_bin / "python"
        interpreter.write_text(
            "#!/bin/bash\n"
            "i=0\n"
            "while [ \"$i\" -lt 600 ]; do\n"
            "  printf '%09000d\\n' \"$i\"\n"
            "  i=$((i + 1))\n"
            "done\n"
            "printf 'final-marker\\n'\n"
            ': > "$HOME/.openclaw/nest-events/state/producer-ready"\n'
            "while :; do /bin/sleep 1; done\n",
            encoding="utf-8",
        )
        interpreter.chmod(0o755)

        environment = {**os.environ, "HOME": str(home)}
        for key in (
            "NEST_EVENT_MODE",
            "NEST_EVENT_SUBSCRIPTION",
            "NEST_EVENT_CONFIG",
            "NEST_EVENT_STATE_DIR",
        ):
            environment.pop(key, None)
        process = subprocess.Popen(
            [str(NEST_EVENT_LISTENER_WRAPPER), "run"],
            env=environment,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            text=True,
        )
        self.addCleanup(lambda: process.poll() is None and process.kill())

        log_path = log_dir / "nest-event-listener.log"
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            if log_path.exists() and "final-marker" in log_path.read_text(
                encoding="utf-8"
            ):
                break
            if process.poll() is not None:
                self.fail(f"listener wrapper exited early with {process.returncode}")
            time.sleep(0.05)
        else:
            self.fail("listener did not finish its bounded-log fixture output")

        self.assertIsNone(process.poll())
        self.assertLessEqual(log_path.stat().st_size, 262144)
        self.assertEqual(stat.S_IMODE(log_path.stat().st_mode), 0o600)
        self.assertIn("[line truncated]", log_path.read_text(encoding="utf-8"))

        process.terminate()
        process.wait(timeout=5)
        self.assertEqual(list(state_dir.glob(".listener-run.*")), [])


if __name__ == "__main__":
    unittest.main()
