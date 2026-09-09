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
import subprocess
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
                [
                    "run",
                    "--job",
                    "video",
                    "--script",
                    "process.sh",
                    "--approved-sha256",
                    digest,
                    "--owner",
                    "sol",
                    "--depends-on",
                    "prepare",
                    "--reserve",
                    "desktop-heavy",
                ]
            )
        self.assertEqual((code, stderr), (0, ""))
        self.assertTrue(json.loads(stdout)["started"])
        self.assertEqual(
            remote.call_args.args,
            (
                "run",
                "linux",
                "video",
                "process.sh",
                digest,
                "sol",
                '["prepare"]',
                '["desktop-heavy"]',
            ),
        )

    def test_cancel_is_owner_bound(self) -> None:
        with mock.patch.object(
            self.client,
            "call_json",
            return_value={"ok": True, "cancelRequested": True},
        ) as remote:
            code, stdout, stderr = self.run_client(
                ["cancel", "--job", "video", "--owner", "sol"]
            )
        self.assertEqual((code, stderr), (0, ""))
        self.assertTrue(json.loads(stdout)["cancelRequested"])
        self.assertEqual(remote.call_args.args, ("cancel", "linux", "video", "sol"))

    def test_only_bounded_blocking_errors_cross_the_ssh_boundary(self) -> None:
        blocked = subprocess.CompletedProcess(
            args=[],
            returncode=64,
            stdout=b"",
            stderr=b"requested resources are held by unfinished jobs: old-run\n",
        )
        with mock.patch.object(self.client.subprocess, "run", return_value=blocked):
            with self.assertRaisesRegex(self.client.PublicError, "old-run"):
                self.client.run_ssh("run", ())

        unsafe = subprocess.CompletedProcess(
            args=[],
            returncode=64,
            stdout=b"",
            stderr=b"unexpected failure at /private/secret\n",
        )
        with mock.patch.object(self.client.subprocess, "run", return_value=unsafe):
            with self.assertRaisesRegex(self.client.PublicError, "request failed") as error:
                self.client.run_ssh("run", ())
        self.assertNotIn("secret", str(error.exception))

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

    def test_unfinished_run_holds_its_resource_without_a_tmux_session(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            job = root / "orphaned"
            (job / "state").mkdir(parents=True)
            (job / "state/run.json").write_text(
                json.dumps({"resources": ["desktop-heavy"]}), encoding="utf-8"
            )
            with mock.patch.dict(self.dispatcher.ROOTS, {"splat": root}, clear=False):
                self.assertEqual(
                    self.dispatcher.active_resource_holders(
                        "splat", ["desktop-heavy"], "new-run"
                    ),
                    ["orphaned"],
                )
                (job / "state/exit-code").write_text("130\n", encoding="ascii")
                self.assertEqual(
                    self.dispatcher.active_resource_holders(
                        "splat", ["desktop-heavy"], "new-run"
                    ),
                    [],
                )

    def test_staging_cannot_mutate_inputs_after_run_provenance_exists(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            job = root / "running"
            for child in ("input", "outputs", "logs", "state"):
                (job / child).mkdir(parents=True, exist_ok=True)
            target = job / "input/convert.sh"
            target.write_bytes(b"printf approved\n")
            (job / "state/run.json").write_text(
                json.dumps(
                    {
                        "script": "run-workflow.sh",
                        "owner": "sol",
                        "resources": ["desktop-heavy"],
                    }
                ),
                encoding="utf-8",
            )
            replacement = b"printf replaced\n"
            fake_stdin = mock.Mock(buffer=io.BytesIO(replacement))
            with mock.patch.dict(self.dispatcher.ROOTS, {"splat": root}, clear=True):
                with mock.patch.object(self.dispatcher.sys, "stdin", fake_stdin):
                    with self.assertRaisesRegex(self.dispatcher.RequestError, "frozen"):
                        self.dispatcher.command_put(
                            [
                                "splat",
                                "running",
                                "convert.sh",
                                str(len(replacement)),
                                hashlib.sha256(replacement).hexdigest(),
                            ]
                        )
            self.assertEqual(target.read_bytes(), b"printf approved\n")

    def test_rejected_repeat_run_preserves_completion_receipts(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            job = root / "completed"
            for child in ("input", "outputs", "logs", "state"):
                (job / child).mkdir(parents=True, exist_ok=True)
            script = job / "input/run-workflow.sh"
            script.write_text("exit 0\n", encoding="utf-8")
            (job / "state/run.json").write_text(
                json.dumps({"resources": ["desktop-heavy"]}), encoding="utf-8"
            )
            (job / "state/exit-code").write_text("0\n", encoding="ascii")
            (job / "state/runner-pid").write_text("12345\n", encoding="ascii")
            no_session = subprocess.CompletedProcess([], 1, "", "")
            with mock.patch.dict(self.dispatcher.ROOTS, {"splat": root}, clear=True):
                with mock.patch.object(Path, "home", return_value=root):
                    with mock.patch.object(
                        self.dispatcher.subprocess, "run", return_value=no_session
                    ):
                        with self.assertRaisesRegex(
                            self.dispatcher.RequestError, "immutable"
                        ):
                            self.dispatcher.command_run(
                                [
                                    "splat",
                                    "completed",
                                    script.name,
                                    self.dispatcher.hash_file(script),
                                    "sol",
                                    "[]",
                                    '["desktop-heavy"]',
                                ]
                            )
                self.assertEqual(self.dispatcher.exit_code_for("splat", "completed"), 0)
            self.assertEqual(
                (job / "state/runner-pid").read_text(encoding="ascii"), "12345\n"
            )

    def test_host_wide_resource_blocks_across_windows_and_splat_scopes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            windows_root = root / "windows"
            splat_root = root / "splat"
            old_job = windows_root / "existing-heavy"
            new_job = splat_root / "new-heavy"
            for job in (old_job, new_job):
                for child in ("input", "outputs", "logs", "state"):
                    (job / child).mkdir(parents=True, exist_ok=True)
            (old_job / "state/run.json").write_text(
                json.dumps({"owner": "other", "resources": ["desktop-heavy"]}),
                encoding="utf-8",
            )
            script = new_job / "input/run-workflow.sh"
            script.write_text("exit 0\n", encoding="utf-8")
            no_session = subprocess.CompletedProcess([], 1, "", "")
            with mock.patch.dict(
                self.dispatcher.ROOTS,
                {"windows": windows_root, "splat": splat_root},
                clear=True,
            ):
                with mock.patch.object(Path, "home", return_value=root):
                    with mock.patch.object(
                        self.dispatcher.subprocess, "run", return_value=no_session
                    ):
                        with self.assertRaisesRegex(
                            self.dispatcher.RequestError, "resources are held"
                        ):
                            self.dispatcher.command_run(
                                [
                                    "splat",
                                    "new-heavy",
                                    script.name,
                                    self.dispatcher.hash_file(script),
                                    "sol",
                                    "[]",
                                    '["desktop-heavy"]',
                                ]
                            )

    def test_workflow_failure_holds_resources_until_termination_is_verified(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            job = root / "failed-workflow"
            (job / "state").mkdir(parents=True)
            (job / "state/run.json").write_text(
                json.dumps(
                    {
                        "script": "run-workflow.sh",
                        "resources": ["desktop-heavy"],
                    }
                ),
                encoding="utf-8",
            )
            (job / "state/exit-code").write_text("130\n", encoding="ascii")
            (job / "state/workflow-state.json").write_text(
                json.dumps({"resourceReleaseVerified": False}), encoding="utf-8"
            )
            with mock.patch.dict(self.dispatcher.ROOTS, {"splat": root}, clear=True):
                self.assertEqual(
                    self.dispatcher.active_resource_holders(
                        "splat", ["desktop-heavy"], "replacement"
                    ),
                    ["failed-workflow"],
                )
                (job / "state/workflow-state.json").write_text(
                    json.dumps({"resourceReleaseVerified": True}), encoding="utf-8"
                )
                self.assertEqual(
                    self.dispatcher.active_resource_holders(
                        "splat", ["desktop-heavy"], "replacement"
                    ),
                    [],
                )

    def test_progress_surfaces_interrupted_run_and_cancel_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            job = root / "interrupted"
            for child in ("input", "outputs", "logs", "state"):
                (job / child).mkdir(parents=True, exist_ok=True)
            (job / "state/run.json").write_text(
                json.dumps(
                    {
                        "script": "run-workflow.sh",
                        "owner": "sol",
                        "resources": ["desktop-heavy"],
                    }
                ),
                encoding="utf-8",
            )
            no_session = mock.Mock(returncode=1, stdout="")
            with mock.patch.dict(self.dispatcher.ROOTS, {"splat": root}, clear=False):
                with mock.patch.object(
                    self.dispatcher.subprocess, "run", return_value=no_session
                ):
                    stdout = io.StringIO()
                    with redirect_stdout(stdout):
                        self.dispatcher.command_progress(["splat", "interrupted", "40"])
                    with self.assertRaisesRegex(
                        self.dispatcher.RequestError, "interrupted"
                    ):
                        self.dispatcher.command_cancel(["splat", "interrupted", "sol"])
        self.assertEqual(json.loads(stdout.getvalue())["state"], "interrupted")


if __name__ == "__main__":
    unittest.main()
