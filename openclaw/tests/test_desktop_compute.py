#!/usr/bin/env python3
"""Offline contracts for the restricted desktop-compute interface."""

from __future__ import annotations

from contextlib import redirect_stderr, redirect_stdout
import hashlib
import importlib.util
from importlib.machinery import SourceFileLoader
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock


REPO_ROOT = Path(__file__).resolve().parents[2]
SKILL_DIR = REPO_ROOT / "openclaw" / "skills" / "desktop-compute"
CLIENT = SKILL_DIR / "scripts" / "desktop_compute.py"
DISPATCHER = REPO_ROOT / "openclaw" / "desktop-compute" / "openclaw-desktop-dispatch"
SKILL = SKILL_DIR / "SKILL.md"
WRAPPER = REPO_ROOT / "openclaw" / "bin" / "desktop-compute"


def load_module(path: Path, name: str):
    loader = SourceFileLoader(name, str(path))
    spec = importlib.util.spec_from_loader(name, loader)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class DesktopComputeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.client = load_module(CLIENT, "desktop_compute_client_for_test")
        self.dispatcher = load_module(DISPATCHER, "desktop_compute_dispatcher_for_test")

    def run_client(self, argv: list[str]) -> tuple[int, str, str]:
        stdout = io.StringIO()
        stderr = io.StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            result = self.client.main(argv)
        return result, stdout.getvalue(), stderr.getvalue()

    def test_skill_wrapper_and_deployment_contracts(self) -> None:
        text = SKILL.read_text(encoding="utf-8")
        self.assertIn("name: desktop-compute", text)
        self.assertIn("allowed-tools: Bash(desktop-compute:*)", text)
        self.assertIn('"bins":["desktop-compute"]', text)
        self.assertNotIn("TODO", text)
        self.assertIn("$HOME/.openclaw/skills/desktop-compute", WRAPPER.read_text(encoding="utf-8"))

        installer = (REPO_ROOT / "install.sh").read_text(encoding="utf-8")
        deployer = (REPO_ROOT / "openclaw/bin/dotfiles-pull.command").read_text(encoding="utf-8")
        self.assertGreaterEqual(installer.count("desktop-compute"), 2)
        self.assertIn("desktop-compute", deployer)

    def test_restricted_alias_uses_a_separate_pinned_key(self) -> None:
        text = (REPO_ROOT / "ssh_config").read_text(encoding="utf-8")
        block = text.split("Host desktop-jobs", 1)[1].split("\nHost ", 1)[0]
        self.assertIn("HostName 127.0.0.1", block)
        self.assertIn("HostKeyAlias desktop-compute-wsl", block)
        self.assertIn("IdentityFile ~/.ssh/id_desktop_compute_jobs", block)
        self.assertIn("IdentityAgent none", block)
        self.assertIn("StrictHostKeyChecking yes", block)
        self.assertNotIn("id_openclaw_desktop", block)

    def test_boot_task_uses_startup_s4u_and_retries(self) -> None:
        script = (REPO_ROOT / "openclaw/desktop-compute/install-prelogin-bridge.ps1").read_text(
            encoding="utf-8"
        )
        self.assertIn("New-ScheduledTaskTrigger -AtStartup", script)
        self.assertIn("-LogonType S4U", script)
        self.assertIn("-RunLevel Limited", script)
        self.assertIn("-RestartCount 12", script)
        self.assertIn("openclaw-wsl-reverse-ssh", script)
        self.assertNotIn("Remove-Item", script)

    def test_paths_and_hashes_fail_closed(self) -> None:
        self.assertEqual(self.client.validate_job("video-index"), "video-index")
        for value in ("Video", "../video", "video_job", "a" * 49):
            with self.subTest(value=value), self.assertRaises(self.client.PublicError):
                self.client.validate_job(value)
        self.assertEqual(self.client.validate_relative("nested/file.txt", label="file"), "nested/file.txt")
        for value in ("../file.txt", "/tmp/file.txt", "a b.txt"):
            with self.subTest(value=value), self.assertRaises(self.client.PublicError):
                self.client.validate_relative(value, label="file")
        with self.assertRaises(self.client.PublicError):
            self.client.validate_script("job.ps1", "linux")
        self.assertEqual(self.client.validate_script("job.ps1", "windows"), "job.ps1")

    def test_stage_dry_run_is_local_and_non_mutating(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "input"
            source.mkdir()
            (source / "one.txt").write_text("one", encoding="utf-8")
            with mock.patch.object(self.client, "call_json") as remote:
                code, stdout, stderr = self.run_client(
                    ["stage", "--job", "dry-run", "--source", str(source), "--dry-run"]
                )
        self.assertEqual(code, 0)
        self.assertEqual(stderr, "")
        payload = json.loads(stdout)
        self.assertTrue(payload["dryRun"])
        self.assertEqual(payload["fileCount"], 1)
        remote.assert_not_called()

    def test_stage_rejects_symlinks(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "input"
            source.mkdir()
            real = source / "real.txt"
            real.write_text("private", encoding="utf-8")
            (source / "link.txt").symlink_to(real)
            code, _stdout, stderr = self.run_client(
                ["stage", "--job", "linked", "--source", str(source), "--dry-run"]
            )
        self.assertEqual(code, 2)
        self.assertIn("regular files", stderr)

    def test_plan_and_run_bind_the_exact_hash(self) -> None:
        digest = "a" * 64
        with mock.patch.object(
            self.client,
            "call_json",
            return_value={"ok": True, "sha256": digest, "sizeBytes": 42},
        ) as remote:
            code, stdout, stderr = self.run_client(
                ["plan-run", "--job", "video", "--script", "process.sh"]
            )
        self.assertEqual((code, stderr), (0, ""))
        self.assertTrue(json.loads(stdout)["requiresApproval"])
        self.assertEqual(remote.call_args.args, ("stat-input", "linux", "video", "process.sh"))

        with mock.patch.object(
            self.client,
            "call_json",
            return_value={"ok": True, "started": True, "sha256": digest},
        ) as remote:
            code, stdout, stderr = self.run_client(
                ["run", "--job", "video", "--script", "process.sh", "--approved-sha256", digest]
            )
        self.assertEqual((code, stderr), (0, ""))
        self.assertTrue(json.loads(stdout)["started"])
        self.assertEqual(remote.call_args.args[-1], digest)

    def test_fetch_verifies_stream_before_atomic_install(self) -> None:
        body = b"verified desktop artifact"
        digest = hashlib.sha256(body).hexdigest()

        def fake_call(operation, *_arguments, **_kwargs):
            self.assertEqual(operation, "stat-output")
            return {"ok": True, "sizeBytes": len(body), "sha256": digest}

        def fake_ssh(operation, _arguments, **kwargs):
            self.assertEqual(operation, "get-output")
            kwargs["stdout"].write(body)
            return mock.Mock(returncode=0, stderr=b"")

        with tempfile.TemporaryDirectory() as directory:
            with mock.patch.object(self.client, "call_json", side_effect=fake_call):
                with mock.patch.object(self.client, "run_ssh", side_effect=fake_ssh):
                    code, stdout, stderr = self.run_client(
                        [
                            "fetch",
                            "--job",
                            "video",
                            "--artifact",
                            "result.bin",
                            "--destination",
                            directory,
                        ]
                    )
            installed = Path(directory) / "result.bin"
            self.assertEqual(installed.read_bytes(), body)
        self.assertEqual((code, stderr), (0, ""))
        self.assertEqual(json.loads(stdout)["sha256"], digest)

    def test_dispatcher_requires_protocol_and_managed_paths(self) -> None:
        with mock.patch.dict(os.environ, {"SSH_ORIGINAL_COMMAND": "uname -a"}, clear=False):
            stderr = io.StringIO()
            with redirect_stderr(stderr), self.assertRaises(SystemExit) as exit_context:
                self.dispatcher.main()
        self.assertEqual(exit_context.exception.code, 64)
        self.assertIn("outside the desktop-compute protocol", stderr.getvalue())

        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            with self.assertRaises(self.dispatcher.RequestError):
                self.dispatcher.managed_path(base, "../secret")

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "root"
            outside = Path(directory) / "outside"
            root.mkdir()
            outside.mkdir()
            (root / "linked").symlink_to(outside, target_is_directory=True)
            with mock.patch.dict(self.dispatcher.ROOTS, {"linux": root}, clear=False):
                with self.assertRaises(self.dispatcher.RequestError):
                    self.dispatcher.job_dir("linux", "linked")


if __name__ == "__main__":
    unittest.main()
