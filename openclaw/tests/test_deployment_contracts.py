#!/usr/bin/env python3

from __future__ import annotations

import os
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
DEPLOYMENT_LIB = REPO_ROOT / "openclaw" / "lib" / "deployment.sh"
INSTALLER = REPO_ROOT / "install.sh"
DOTFILES_PULL = REPO_ROOT / "openclaw" / "bin" / "dotfiles-pull.command"
TOP_LEVEL_NEST = REPO_ROOT / "bin" / "nest"
OPENCLAW_NEST = REPO_ROOT / "openclaw" / "bin" / "nest"
NEST_SNAPSHOT_PLIST = (
    REPO_ROOT / "openclaw" / "launchagents" / "ai.openclaw.nest-snapshot.plist"
)

REQUIRED_HELPERS = {
    "bin/august": "august wrapper\n",
    "bin/opentable-book": "opentable wrapper\n",
    "bin/restaurant-snipe": "snipe wrapper\n",
    "bin/resy-read": "resy wrapper\n",
    "workspace/scripts/opentable-book.sh": "opentable helper\n",
    "workspace/scripts/opentable-book-state.py": "state helper\n",
}


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
        self.assertEqual(completed.stdout.count("[dry-run] Would install"), len(REQUIRED_HELPERS))
        for relative in REQUIRED_HELPERS:
            self.assertIn(str(destination / relative), completed.stdout)

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
        self.assertIn("presence-devices.env", pull_text)
        self.assertIn(".openclaw/august/config.json", pull_text)
        self.assertIn(".openclaw/rest980/env-10max", pull_text)
        self.assertIn(".openclaw/rest980/env-j5", pull_text)

    def test_scheduled_nest_path_uses_the_hardened_cli(self) -> None:
        plist_text = NEST_SNAPSHOT_PLIST.read_text(encoding="utf-8")

        self.assertIn("/opt/homebrew/bin/nest snapshot", plist_text)
        self.assertEqual(TOP_LEVEL_NEST.read_bytes(), OPENCLAW_NEST.read_bytes())
        self.assertIn("NEST_CLIENT_ID", TOP_LEVEL_NEST.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
