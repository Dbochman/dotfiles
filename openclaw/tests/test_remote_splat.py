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

    def test_host_alias_is_loopback_only_and_key_pinned(self) -> None:
        text = (REPO_ROOT / "ssh_config").read_text(encoding="utf-8")
        block = text.split("Host desktop-compute", 1)[1].split("\nHost ", 1)[0]
        self.assertIn("HostName 127.0.0.1", block)
        self.assertIn("HostKeyAlias desktop-compute-wsl", block)
        self.assertIn("Port 22022", block)
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
            with mock.patch.object(self.helper, "remote_command") as remote:
                with mock.patch.object(self.helper, "run_rsync") as rsync:
                    code, stdout, stderr = self.run_main(
                        ["stage", "--job", "dry-run", "--source", directory, "--dry-run"]
                    )
        self.assertEqual(code, 0)
        self.assertEqual(stderr, "")
        payload = json.loads(stdout)
        self.assertTrue(payload["dryRun"])
        remote.assert_not_called()
        rsync.assert_not_called()

    def test_stage_rejects_a_symlinked_source(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            real = Path(directory) / "real"
            real.write_text("data", encoding="utf-8")
            linked = Path(directory) / "linked"
            linked.symlink_to(real)
            code, _stdout, stderr = self.run_main(
                ["stage", "--job", "linked", "--source", str(linked), "--dry-run"]
            )
        self.assertEqual(code, 2)
        self.assertIn("regular file or directory", stderr)

    def test_plan_and_run_are_bound_to_exact_hash(self) -> None:
        digest = "a" * 64
        with mock.patch.object(
            self.helper,
            "remote_command",
            return_value=json.dumps({"exists": True, "sha256": digest, "sizeBytes": 42}),
        ) as remote:
            code, stdout, _stderr = self.run_main(
                ["plan-run", "--job", "cabin", "--script", "retrain.ps1"]
            )
        self.assertEqual(code, 0)
        self.assertTrue(json.loads(stdout)["requiresApproval"])
        self.assertEqual(remote.call_args.args[1][-1], "retrain.ps1")

        with mock.patch.object(
            self.helper,
            "remote_command",
            return_value=json.dumps({"started": True, "session": "splat-cabin", "sha256": digest}),
        ) as remote:
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
        self.assertEqual(remote.call_args.args[1][-1], digest)

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

    def test_fetch_verifies_before_atomic_install(self) -> None:
        body = b"validated-sog-fixture"
        digest = hashlib.sha256(body).hexdigest()
        metadata = json.dumps({"exists": True, "sha256": digest, "sizeBytes": len(body)})

        def fake_rsync(arguments, **_kwargs):
            Path(arguments[-1]).write_bytes(body)

        with tempfile.TemporaryDirectory() as directory:
            with mock.patch.object(self.helper, "remote_command", return_value=metadata):
                with mock.patch.object(self.helper, "run_rsync", side_effect=fake_rsync):
                    code, stdout, stderr = self.run_main(
                        [
                            "fetch",
                            "--job",
                            "cabin",
                            "--artifact",
                            "nested/cabin.sog",
                            "--destination",
                            directory,
                        ]
                    )
            installed = Path(directory) / "cabin.sog"
            self.assertEqual(installed.read_bytes(), body)
        self.assertEqual(code, 0)
        self.assertEqual(stderr, "")
        self.assertEqual(json.loads(stdout)["sha256"], digest)

    def test_fetch_refuses_to_overwrite_a_different_file(self) -> None:
        body = b"expected"
        digest = hashlib.sha256(body).hexdigest()
        metadata = json.dumps({"exists": True, "sha256": digest, "sizeBytes": len(body)})
        with tempfile.TemporaryDirectory() as directory:
            (Path(directory) / "cabin.sog").write_bytes(b"different")
            with mock.patch.object(self.helper, "remote_command", return_value=metadata):
                with mock.patch.object(self.helper, "run_rsync") as rsync:
                    code, _stdout, stderr = self.run_main(
                        [
                            "fetch",
                            "--job",
                            "cabin",
                            "--artifact",
                            "cabin.sog",
                            "--destination",
                            directory,
                        ]
                    )
        self.assertEqual(code, 2)
        self.assertIn("different artifact", stderr)
        rsync.assert_not_called()

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

    def test_rsync_uses_drvfs_compatible_non_destructive_flags(self) -> None:
        completed = mock.Mock(returncode=0, stderr=b"")
        with mock.patch.object(self.helper.subprocess, "run", return_value=completed) as run:
            self.helper.run_rsync(("source/", "desktop-compute:/managed/"))
        command = run.call_args.args[0]
        self.assertIn("--inplace", command)
        self.assertIn("-r", command)
        self.assertNotIn("-a", command)
        self.assertNotIn("--delete", command)


if __name__ == "__main__":
    unittest.main()
