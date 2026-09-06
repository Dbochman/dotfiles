#!/usr/bin/env python3
"""Offline contracts for the remote-splat skill and helper."""

from __future__ import annotations

from contextlib import redirect_stderr, redirect_stdout
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock


REPO_ROOT = Path(__file__).resolve().parents[2]
SKILL_DIR = REPO_ROOT / "openclaw" / "skills" / "remote-splat"
HELPER = SKILL_DIR / "scripts" / "remote_splat.py"
SKILL = SKILL_DIR / "SKILL.md"
WRAPPER = REPO_ROOT / "openclaw" / "bin" / "remote-splat"


def load_helper():
    spec = importlib.util.spec_from_file_location("remote_splat_for_test", HELPER)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class RemoteSplatTests(unittest.TestCase):
    def setUp(self) -> None:
        self.helper = load_helper()

    def run_main(self, argv: list[str]) -> tuple[int, str, str]:
        stdout = io.StringIO()
        stderr = io.StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            result = self.helper.main(argv)
        return result, stdout.getvalue(), stderr.getvalue()

    def test_skill_and_wrapper_contracts(self) -> None:
        text = SKILL.read_text(encoding="utf-8")
        self.assertIn("name: remote-splat", text)
        self.assertIn("allowed-tools: Bash(remote-splat:*)", text)
        self.assertIn('"bins":["remote-splat"]', text)
        self.assertNotIn("TODO", text)
        self.assertIn("$HOME/.openclaw/skills/remote-splat", WRAPPER.read_text(encoding="utf-8"))
        self.assertTrue((SKILL_DIR / "scripts/compute_canary.sh").is_file())

        installer = (REPO_ROOT / "install.sh").read_text(encoding="utf-8")
        deployer = (REPO_ROOT / "openclaw/bin/dotfiles-pull.command").read_text(encoding="utf-8")
        self.assertGreaterEqual(installer.count("remote-splat"), 2)
        self.assertIn("remote-splat", deployer)

    def test_restricted_host_alias_is_loopback_only_and_key_pinned(self) -> None:
        text = (REPO_ROOT / "ssh_config").read_text(encoding="utf-8")
        block = text.split("Host desktop-jobs", 1)[1].split("\nHost ", 1)[0]
        self.assertIn("HostName 127.0.0.1", block)
        self.assertIn("HostKeyAlias desktop-compute-wsl", block)
        self.assertIn("Port 22022", block)
        self.assertIn("id_desktop_compute_jobs", block)
        self.assertIn("IdentityAgent none", block)
        self.assertIn("StrictHostKeyChecking yes", block)

    def test_job_and_relative_paths_are_closed(self) -> None:
        self.assertEqual(self.helper.validate_job("cabin-refresh"), "cabin-refresh")
        for value in ("Cabin", "-cabin", "cabin_2", "../cabin", "a" * 49):
            with self.subTest(value=value), self.assertRaises(self.helper.PublicError):
                self.helper.validate_job(value)

        self.assertEqual(
            self.helper.validate_relative(
                "scripts/retrain.ps1",
                suffixes=self.helper.SCRIPT_SUFFIXES,
                label="script",
            ),
            "scripts/retrain.ps1",
        )
        for value in ("../retrain.ps1", "/tmp/retrain.ps1", "retrain.py", "a b.ps1"):
            with self.subTest(value=value), self.assertRaises(self.helper.PublicError):
                self.helper.validate_relative(
                    value,
                    suffixes=self.helper.SCRIPT_SUFFIXES,
                    label="script",
                )

    def test_stage_dry_run_is_non_mutating(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with mock.patch.object(
                self.helper,
                "run_desktop",
                return_value={"dryRun": True, "job": "dry-run"},
            ) as desktop:
                code, stdout, stderr = self.run_main(
                    ["stage", "--job", "dry-run", "--source", directory, "--dry-run"]
                )
        self.assertEqual(code, 0)
        self.assertEqual(stderr, "")
        payload = json.loads(stdout)
        self.assertTrue(payload["dryRun"])
        self.assertEqual(desktop.call_args.args[0], "stage")
        self.assertIn("--dry-run", desktop.call_args.args[1])

    def test_plan_and_run_are_bound_to_exact_hash(self) -> None:
        digest = "a" * 64
        with mock.patch.object(
            self.helper,
            "run_desktop",
            return_value={"ok": True, "requiresApproval": True, "sha256": digest, "sizeBytes": 42},
        ) as desktop:
            code, stdout, _stderr = self.run_main(
                ["plan-run", "--job", "cabin", "--script", "retrain.ps1"]
            )
        self.assertEqual(code, 0)
        self.assertTrue(json.loads(stdout)["requiresApproval"])
        self.assertEqual(desktop.call_args.args[0], "plan-run")
        self.assertIn("retrain.ps1", desktop.call_args.args[1])

        with mock.patch.object(
            self.helper,
            "run_desktop",
            return_value={"started": True, "session": "splat-cabin", "sha256": digest},
        ) as desktop:
            code, stdout, _stderr = self.run_main(
                [
                    "run",
                    "--job",
                    "cabin",
                    "--script",
                    "retrain.ps1",
                    "--approved-sha256",
                    digest,
                ]
            )
        self.assertEqual(code, 0)
        self.assertTrue(json.loads(stdout)["started"])
        self.assertIn(digest, desktop.call_args.args[1])

        code, _stdout, stderr = self.run_main(
            [
                "run",
                "--job",
                "cabin",
                "--script",
                "retrain.ps1",
                "--approved-sha256",
                "not-a-hash",
            ]
        )
        self.assertEqual(code, 2)
        self.assertIn("64 hexadecimal", stderr)

    def test_fetch_keeps_splat_suffix_guard_and_delegates(self) -> None:
        digest = "b" * 64
        with tempfile.TemporaryDirectory() as directory:
            with mock.patch.object(
                self.helper,
                "run_desktop",
                return_value={"ok": True, "artifact": "cabin.sog", "sha256": digest},
            ) as desktop:
                code, stdout, stderr = self.run_main(
                    ["fetch", "--job", "cabin", "--artifact", "nested/cabin.sog", "--destination", directory]
                )
        self.assertEqual((code, stderr), (0, ""))
        self.assertEqual(json.loads(stdout)["sha256"], digest)
        self.assertEqual(desktop.call_args.args[0], "fetch")

        code, _stdout, stderr = self.run_main(
            ["fetch", "--job", "cabin", "--artifact", "notes.txt", "--destination", "/tmp"]
        )
        self.assertEqual(code, 2)
        self.assertIn(".ply", stderr)

    def test_publish_plan_never_uploads(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            artifact = Path(directory) / "scene.sog"
            artifact.write_bytes(b"scene")
            with mock.patch.object(self.helper, "subprocess") as subprocess_module:
                code, stdout, stderr = self.run_main(
                    ["publish-plan", "--file", str(artifact)]
                )
        self.assertEqual(code, 0)
        self.assertEqual(stderr, "")
        payload = json.loads(stdout)
        self.assertFalse(payload["uploadPerformed"])
        self.assertTrue(payload["requiresConfirmation"])
        subprocess_module.run.assert_not_called()

    def test_desktop_compute_subprocess_uses_internal_splat_scope(self) -> None:
        command = self.helper.desktop_arguments("status", ())
        self.assertEqual(command[0], self.helper.DESKTOP_COMPUTE)
        self.assertEqual(command[1:4], ["status", "--scope", "splat"])


if __name__ == "__main__":
    unittest.main()
