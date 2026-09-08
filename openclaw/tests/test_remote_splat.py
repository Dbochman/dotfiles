#!/usr/bin/env python3
"""Offline contracts for the remote-splat skill and helper."""

from __future__ import annotations

from contextlib import redirect_stderr, redirect_stdout
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import shutil
import subprocess
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

    def test_inbox_stage_is_hash_bound_and_targets_only_the_desktop(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "IMG_4112.MOV"
            source.write_bytes(b"video-data")
            with mock.patch.object(self.helper.subprocess, "run") as run:
                run.return_value.returncode = 0
                code, stdout, stderr = self.run_main(
                    ["inbox-stage", "--job", "cabin-trails-v1", "--source", str(source)]
                )
        self.assertEqual((code, stderr), (0, ""))
        payload = json.loads(stdout)
        self.assertEqual(payload["sha256"], hashlib.sha256(b"video-data").hexdigest())
        self.assertTrue(payload["requiresHashVerifiedIngest"])
        command = run.call_args.args[0]
        self.assertEqual(command[0:3], [self.helper.TAILSCALE, "file", "cp"])
        self.assertEqual(command[-2], "-")
        self.assertEqual(command[-1], "desktop-r9js0ok:")
        self.assertIn("openclaw-splat-cabin-trails-v1-IMG_4112.MOV", command)

    def test_inbox_stage_dry_run_does_not_transfer(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "capture.mov"
            source.write_bytes(b"video")
            with mock.patch.object(self.helper.subprocess, "run") as run:
                code, stdout, stderr = self.run_main(
                    [
                        "inbox-stage",
                        "--job",
                        "cabin",
                        "--source",
                        str(source),
                        "--dry-run",
                    ]
                )
        self.assertEqual((code, stderr), (0, ""))
        self.assertTrue(json.loads(stdout)["dryRun"])
        run.assert_not_called()

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

        with tempfile.TemporaryDirectory() as directory:
            with mock.patch.object(
                self.helper,
                "run_desktop",
                return_value={"ok": True, "artifact": "orbit-01.webp", "sha256": digest},
            ) as desktop:
                code, stdout, stderr = self.run_main(
                    [
                        "fetch",
                        "--job",
                        "cabin",
                        "--artifact",
                        "validation/orbit-01.webp",
                        "--destination",
                        directory,
                    ]
                )
        self.assertEqual((code, stderr), (0, ""))
        self.assertEqual(json.loads(stdout)["artifact"], "orbit-01.webp")
        self.assertEqual(desktop.call_args.args[0], "fetch")

    def test_publish_plan_does_not_accept_visual_qa_outputs(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            review = Path(directory) / "orbit.webp"
            review.write_bytes(b"review")
            code, _stdout, stderr = self.run_main(["publish-plan", "--file", str(review)])
        self.assertEqual(code, 2)
        self.assertIn(".sog", stderr)

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

    @unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "FFmpeg is required")
    def test_video_review_and_extract_end_to_end(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "walk.mp4"
            created = subprocess.run(
                [
                    shutil.which("ffmpeg"),
                    "-hide_banner",
                    "-loglevel",
                    "error",
                    "-f",
                    "lavfi",
                    "-i",
                    "testsrc2=size=640x360:rate=30",
                    "-t",
                    "6",
                    "-c:v",
                    "libx264",
                    "-pix_fmt",
                    "yuv420p",
                    str(source),
                ],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                check=False,
            )
            self.assertEqual(created.returncode, 0, created.stderr.decode(errors="replace"))
            original_hash = self.helper.hash_file(source)

            code, stdout, stderr = self.run_main(["video-probe", "--source", str(source)])
            self.assertEqual((code, stderr), (0, ""))
            self.assertEqual(json.loads(stdout)["width"], 640)

            review = root / "review"
            review_args = [
                "video-review",
                "--source",
                str(source),
                "--output",
                str(review),
                "--interval-seconds",
                "5",
                "--proxy",
            ]
            scene_tool = Path.home() / ".local/bin/scenedetect"
            if scene_tool.is_file():
                review_args.append("--scene-suggestions")
            code, stdout, stderr = self.run_main(review_args)
            self.assertEqual((code, stderr), (0, ""))
            review_payload = json.loads(stdout)
            self.assertGreaterEqual(review_payload["thumbnailCount"], 1)
            self.assertEqual(review_payload["sceneSuggestionsCreated"], scene_tool.is_file())
            self.assertTrue((review / "index.html").is_file())
            self.assertTrue((review / "video-01/review-proxy.mp4").is_file())

            manifest = json.loads((review / "segments.template.json").read_text(encoding="utf-8"))
            manifest["videos"][0]["segments"] = [
                {"name": "yard-anchor", "start": "00:00:01.000", "end": "00:00:04.000", "fps": 2.0}
            ]
            manifest_path = root / "segments.json"
            manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
            extraction = root / "extraction"

            code, stdout, stderr = self.run_main(
                ["video-extract", "--manifest", str(manifest_path), "--output", str(extraction), "--dry-run"]
            )
            self.assertEqual((code, stderr), (0, ""))
            self.assertEqual(json.loads(stdout)["estimatedFrames"], 6)
            self.assertFalse(extraction.exists())

            code, stdout, stderr = self.run_main(
                ["video-extract", "--manifest", str(manifest_path), "--output", str(extraction)]
            )
            self.assertEqual((code, stderr), (0, ""))
            payload = json.loads(stdout)
            self.assertEqual(payload["segments"][0]["frameCount"], 6)
            self.assertTrue((extraction / "clips/yard-anchor.mp4").is_file())
            self.assertEqual(len(list((extraction / "frames/yard-anchor").glob("*.jpg"))), 6)
            self.assertEqual(self.helper.hash_file(source), original_hash)


if __name__ == "__main__":
    unittest.main()
