#!/usr/bin/env python3
"""Offline contracts for the remote-splat skill and helper."""

from __future__ import annotations

from contextlib import redirect_stderr, redirect_stdout
import errno
import hashlib
import importlib.util
from importlib.machinery import SourceFileLoader
import io
import json
import os
import shlex
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock


REPO_ROOT = Path(__file__).resolve().parents[2]
SKILL_DIR = REPO_ROOT / "openclaw" / "skills" / "remote-splat"
HELPER = SKILL_DIR / "scripts" / "remote_splat.py"
WORKFLOW_RUNNER = SKILL_DIR / "scripts" / "workflow_runner.py"
DESKTOP_DISPATCHER = (
    REPO_ROOT / "openclaw" / "desktop-compute" / "openclaw-desktop-dispatch"
)
SKILL = SKILL_DIR / "SKILL.md"
WRAPPER = REPO_ROOT / "openclaw" / "bin" / "remote-splat"
REAL_SLEEP = time.sleep


def load_module(path: Path, name: str):
    if path.suffix:
        spec = importlib.util.spec_from_file_location(name, path)
    else:
        loader = SourceFileLoader(name, str(path))
        spec = importlib.util.spec_from_loader(name, loader)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def load_helper():
    return load_module(HELPER, "remote_splat_for_test")


class RemoteSplatTests(unittest.TestCase):
    def setUp(self) -> None:
        self.helper = load_helper()

    def run_main(self, argv: list[str]) -> tuple[int, str, str]:
        stdout = io.StringIO()
        stderr = io.StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            result = self.helper.main(argv)
        return result, stdout.getvalue(), stderr.getvalue()

    def workflow_args(self, prefix: str, profile: str) -> list[str]:
        return [
            "workflow-plan",
            "--job-prefix",
            prefix,
            "--profile",
            profile,
            "--target-registered-views",
            "300",
            "--plausible-registered-views",
            "248",
            "--minimum-points",
            "25000",
            "--maximum-reprojection-error-px",
            "1.25",
            "--segment-floor",
            "interior-a=15",
            "--segment-floor",
            "interior-b=30",
        ]

    def runner_fixture(
        self, root: Path, script_text: str
    ) -> tuple[Path, dict[str, str]]:
        job = root / "review"
        for child in ("input", "outputs", "logs", "state"):
            (job / child).mkdir(parents=True, exist_ok=True)
        manifest = {
            "schemaVersion": 1,
            "job": "review",
            "owner": "sol",
            "settings": {"iterations": 5000},
            "dependencies": {"fixture": "local-only"},
            "qualityTiers": {
                "target": {"minimumRegisteredViews": 300},
                "plausibleCandidate": {"minimumRegisteredViews": 248},
                "experimentalOnly": {"promotionEligible": False},
            },
            "phases": [{"name": "finish", "script": "finish.sh"}],
        }
        execution = {
            "job": "review",
            "owner": "sol",
            "externalDependencies": [],
            "resources": ["desktop-heavy"],
        }
        (job / "input/workflow.json").write_text(
            json.dumps(manifest), encoding="utf-8"
        )
        (job / "input/compute_canary.sh").write_text(
            "exit 0\n", encoding="utf-8"
        )
        (job / "input/finish.sh").write_text(script_text, encoding="utf-8")
        (job / "input/approval-inventory.json").write_text(
            json.dumps(
                {
                    "approvalSha256": "a" * 64,
                    "execution": execution,
                    "files": [],
                }
            ),
            encoding="utf-8",
        )
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
        environment = {
            "OPENCLAW_WORKFLOW_APPROVAL_SHA256": "a" * 64,
            "OPENCLAW_JOB_NAME": "review",
            "OPENCLAW_JOB_OWNER": "sol",
            "OPENCLAW_JOB_DEPENDENCIES_JSON": "[]",
            "OPENCLAW_JOB_RESOURCES_JSON": '["desktop-heavy"]',
            "OPENCLAW_RUNNER_SHA256": "b" * 64,
        }
        return job, environment

    def test_skill_and_wrapper_contracts(self) -> None:
        text = SKILL.read_text(encoding="utf-8")
        self.assertIn("name: remote-splat", text)
        self.assertIn("allowed-tools: Bash(remote-splat:*)", text)
        self.assertIn('"bins":["remote-splat"]', text)
        self.assertNotIn("TODO", text)
        self.assertIn("$HOME/.openclaw/skills/remote-splat", WRAPPER.read_text(encoding="utf-8"))
        self.assertTrue((SKILL_DIR / "scripts/compute_canary.sh").is_file())
        self.assertTrue((SKILL_DIR / "scripts/windows_process_runner.ps1").is_file())

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

    def test_workflow_plan_separates_private_delivery_from_promotion(self) -> None:
        with mock.patch.object(self.helper, "run_desktop") as desktop:
            code, stdout, stderr = self.run_main(
                self.workflow_args("cabin-next", "best-current")
            )
        self.assertEqual((code, stderr), (0, ""))
        desktop.assert_not_called()
        payload = json.loads(stdout)
        self.assertEqual(payload["jobs"]["connect"], "cabin-next-connect")
        self.assertEqual(payload["registeredViewSelection"], "all-registered-within-declared-bound")
        self.assertFalse(payload["training"]["defaultsApplied"])
        self.assertEqual(
            payload["scheduling"]["phaseDependencies"]["train"],
            ["preflight", "connect"],
        )
        self.assertEqual(payload["jobs"]["preview"], "cabin-next-preview")
        self.assertTrue(payload["qualityTiers"]["declaredBeforePreparation"])
        self.assertEqual(
            payload["qualityTiers"]["plausibleCandidate"]["minimumRegisteredViews"],
            248,
        )
        self.assertTrue(payload["runOwnership"]["ownerRequired"])
        self.assertTrue(payload["monitoring"]["checkpointStepsNormalizeZeroPadding"])
        self.assertEqual(payload["fallback"]["criticalUntil"], "trainable-primary-reconstruction")
        incremental = payload["incrementalExtension"]
        self.assertEqual(incremental["basePolicy"], "immutable-versioned-feature-compatible")
        self.assertIn("feature-schema", incremental["preflightRequires"])
        self.assertIn("targeted-new-to-known-anchors", incremental["matching"])
        self.assertEqual(incremental["registration"][-1], "repeat-until-count-stable")
        self.assertIn("without-stopping-final", incremental["preview"])
        self.assertEqual(payload["privateDelivery"]["when"], "immediately-after-basic-validation")
        self.assertFalse(payload["promotion"]["eligible"])
        self.assertTrue(payload["promotion"]["requiresExplicitConfirmation"])

    def test_promotion_profile_keeps_full_qa_out_of_private_delivery_gate(self) -> None:
        code, stdout, stderr = self.run_main(
            self.workflow_args("cabin-release", "promotion-candidate")
        )
        self.assertEqual((code, stderr), (0, ""))
        payload = json.loads(stdout)
        self.assertTrue(payload["promotion"]["eligible"])
        self.assertEqual(
            payload["fallback"]["criticalUntil"],
            "promotion-gates-pass-or-candidate-is-rejected",
        )
        self.assertEqual(payload["privateDelivery"]["when"], "immediately-after-basic-validation")
        self.assertEqual(payload["privateDelivery"]["comparativeQa"], "full-before-promotion")
        self.assertNotIn("comparative-validation", payload["privateDelivery"]["requires"])

    def test_workflow_plan_rejects_prefix_that_cannot_fit_phase_names(self) -> None:
        code, _stdout, stderr = self.run_main(
            self.workflow_args("a" * 48, "preview")
        )
        self.assertEqual(code, 2)
        self.assertIn("too long", stderr)

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

    def test_workstation_inbox_stage_hash_binds_direct_taildrop(self) -> None:
        digest = "c" * 64
        source = "/Users/dylanbochman/Downloads/IMG_4113.MOV"
        with mock.patch.object(
            self.helper,
            "workstation_metadata",
            side_effect=[(611075882, digest), (611075882, digest)],
        ) as metadata:
            with mock.patch.object(
                self.helper,
                "workstation_tailscale_path",
                return_value="/usr/local/bin/tailscale",
            ):
                with mock.patch.object(self.helper, "run_workstation", return_value="") as remote:
                    code, stdout, stderr = self.run_main(
                        ["workstation-inbox-stage", "--job", "cabin-structure", "--source", source]
                    )
        self.assertEqual((code, stderr), (0, ""))
        payload = json.loads(stdout)
        self.assertEqual(payload["sha256"], digest)
        self.assertEqual(payload["transport"], "workstation-taildrop")
        self.assertTrue(payload["requiresHashVerifiedIngest"])
        self.assertEqual(metadata.call_count, 2)
        command = remote.call_args.args[0]
        self.assertEqual(command[0:3], ["/usr/local/bin/tailscale", "file", "cp"])
        self.assertEqual(command[-2:], [source, self.helper.TAILDROP_TARGET])

    def test_workstation_tailscale_path_uses_known_executable(self) -> None:
        with mock.patch.object(
            self.helper,
            "WORKSTATION_TAILSCALE_CANDIDATES",
            ("/missing/tailscale", "/Applications/Tailscale.app/Contents/MacOS/Tailscale"),
        ):
            with mock.patch.object(
                self.helper,
                "run_workstation",
                side_effect=[self.helper.PublicError("missing"), ""],
            ) as remote:
                path = self.helper.workstation_tailscale_path()
        self.assertEqual(path, "/Applications/Tailscale.app/Contents/MacOS/Tailscale")
        self.assertEqual(remote.call_count, 2)

    def test_workstation_inbox_stage_falls_back_to_guarded_relay(self) -> None:
        digest = "e" * 64
        source = "/Users/dylanbochman/Downloads/IMG_4117.MOV"
        with mock.patch.object(
            self.helper,
            "workstation_metadata",
            side_effect=[(759447867, digest), (759447867, digest)],
        ):
            with mock.patch.object(
                self.helper,
                "workstation_tailscale_path",
                side_effect=self.helper.PublicError("unavailable"),
            ):
                with mock.patch.object(self.helper, "relay_workstation_taildrop") as relay:
                    code, stdout, stderr = self.run_main(
                        ["workstation-inbox-stage", "--job", "cabin", "--source", source]
                    )
        self.assertEqual((code, stderr), (0, ""))
        payload = json.loads(stdout)
        self.assertEqual(payload["transport"], "workstation-relay-taildrop")
        relay.assert_called_once_with(
            self.helper.PurePosixPath(source),
            inbox_name="openclaw-splat-cabin-IMG_4117.MOV",
        )

    def test_workstation_inbox_stage_dry_run_is_read_only(self) -> None:
        digest = "d" * 64
        with mock.patch.object(
            self.helper, "workstation_metadata", return_value=(345170785, digest)
        ):
            with mock.patch.object(self.helper, "run_workstation") as remote:
                code, stdout, stderr = self.run_main(
                    [
                        "workstation-inbox-stage",
                        "--job",
                        "cabin-structure",
                        "--source",
                        "/Users/dylanbochman/Downloads/IMG_4114.MOV",
                        "--dry-run",
                    ]
                )
        self.assertEqual((code, stderr), (0, ""))
        self.assertTrue(json.loads(stdout)["dryRun"])
        remote.assert_not_called()

    def test_workstation_inbox_stage_rejects_other_paths(self) -> None:
        for source in (
            "/Users/dylanbochman/Desktop/IMG_4113.MOV",
            "/Users/dylanbochman/Downloads/../private.MOV",
            "/Users/dylanbochman/Downloads/notes.txt",
        ):
            with self.subTest(source=source):
                code, _stdout, stderr = self.run_main(
                    ["workstation-inbox-stage", "--job", "cabin", "--source", source]
                )
                self.assertEqual(code, 2)
                self.assertIn("directly inside Downloads", stderr)

    def workstation_fetch_args(self, destination: Path, data: bytes) -> list[str]:
        return [
            "workstation-fetch",
            "--source",
            "/Users/dylanbochman/Downloads/IMG_4120.MOV",
            "--destination",
            str(destination),
            "--expected-sha256",
            hashlib.sha256(data).hexdigest(),
            "--expected-size-bytes",
            str(len(data)),
        ]

    def mocked_workstation_cat(self, data: bytes, *, delay: float = 0) -> subprocess.Popen[bytes]:
        script = "import sys,time; time.sleep(float(sys.argv[1])); sys.stdout.buffer.write(sys.argv[2].encode())"
        return subprocess.Popen(
            [sys.executable, "-c", script, str(delay), data.decode()],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
        )

    def test_workstation_fetch_streams_and_emits_json(self) -> None:
        data = b"manifest-bound-video"
        with tempfile.TemporaryDirectory() as directory:
            destination = Path(directory) / "prepared.mov"
            with mock.patch.object(
                self.helper, "workstation_source_size", return_value=len(data)
            ) as metadata:
                with mock.patch.object(
                    self.helper,
                    "start_workstation_cat",
                    side_effect=lambda _source: self.mocked_workstation_cat(data),
                ):
                    code, stdout, stderr = self.run_main(
                        self.workstation_fetch_args(destination, data)
                    )
            self.assertEqual(destination.read_bytes(), data)
            self.assertEqual(list(destination.parent.glob("*.partial")), [])
        self.assertEqual((code, stderr), (0, ""))
        payload = json.loads(stdout)
        self.assertFalse(payload["reused"])
        self.assertEqual(payload["sha256"], hashlib.sha256(data).hexdigest())
        self.assertEqual(payload["sizeBytes"], len(data))
        metadata.assert_called_once()

    def test_workstation_fetch_rejects_corrupt_truncated_and_oversized_streams(self) -> None:
        expected = b"expected-video"
        cases = {
            "corrupt": b"unexpected-vid",
            "truncated": expected[:-1],
            "oversized": expected + b"x",
        }
        for label, received in cases.items():
            with self.subTest(label=label), tempfile.TemporaryDirectory() as directory:
                destination = Path(directory) / "prepared.mov"
                with mock.patch.object(
                    self.helper, "workstation_source_size", return_value=len(expected)
                ):
                    with mock.patch.object(
                        self.helper,
                        "start_workstation_cat",
                        side_effect=lambda _source, data=received: self.mocked_workstation_cat(data),
                    ):
                        code, _stdout, _stderr = self.run_main(
                            self.workstation_fetch_args(destination, expected)
                        )
                self.assertEqual(code, 2)
                self.assertFalse(destination.exists())
                self.assertEqual(list(destination.parent.glob("*.partial")), [])

    def test_workstation_fetch_rejects_timeout_and_missing_source(self) -> None:
        data = b"video"
        with tempfile.TemporaryDirectory() as directory:
            destination = Path(directory) / "prepared.mov"
            with mock.patch.object(
                self.helper, "workstation_source_size", return_value=len(data)
            ):
                with mock.patch.object(
                    self.helper,
                    "start_workstation_cat",
                    side_effect=lambda _source: self.mocked_workstation_cat(data, delay=1),
                ):
                    with mock.patch.object(self.helper, "WORKSTATION_FETCH_TIMEOUT", 0.01):
                        code, _stdout, stderr = self.run_main(
                            self.workstation_fetch_args(destination, data)
                        )
            self.assertEqual(code, 2)
            self.assertIn("timed out", stderr)
            self.assertFalse(destination.exists())

            with mock.patch.object(
                self.helper,
                "workstation_source_size",
                side_effect=self.helper.PublicError("workstation splat operation failed"),
            ):
                code, _stdout, stderr = self.run_main(
                    self.workstation_fetch_args(destination, data)
                )
            self.assertEqual(code, 2)
            self.assertIn("operation failed", stderr)

    def test_workstation_fetch_reuses_match_and_rejects_collisions_and_symlinks(self) -> None:
        data = b"existing-video"
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            destination = root / "prepared.mov"
            destination.write_bytes(data)
            with mock.patch.object(self.helper, "workstation_source_size") as metadata:
                code, stdout, stderr = self.run_main(
                    self.workstation_fetch_args(destination, data)
                )
            self.assertEqual((code, stderr), (0, ""))
            self.assertTrue(json.loads(stdout)["reused"])
            metadata.assert_not_called()

            destination.write_bytes(b"different")
            code, _stdout, stderr = self.run_main(
                self.workstation_fetch_args(destination, data)
            )
            self.assertEqual(code, 2)
            self.assertIn("different content", stderr)

            destination.unlink()
            destination.symlink_to(root / "missing.mov")
            code, _stdout, stderr = self.run_main(
                self.workstation_fetch_args(destination, data)
            )
            self.assertEqual(code, 2)
            self.assertIn("symlink", stderr)

            destination.unlink()
            symlink_parent = root / "linked"
            symlink_parent.symlink_to(root)
            code, _stdout, stderr = self.run_main(
                self.workstation_fetch_args(symlink_parent / "prepared.mov", data)
            )
            self.assertEqual(code, 2)
            self.assertIn("symlink", stderr)

    def test_workstation_fetch_cli_requires_manifest_arguments(self) -> None:
        arguments = [
            "workstation-fetch",
            "--source",
            "/Users/dylanbochman/Downloads/IMG_4120.MOV",
            "--destination",
            "/tmp/prepared.mov",
            "--expected-sha256",
            "a" * 64,
            "--expected-size-bytes",
            "5",
        ]
        for option in (
            "--source",
            "--destination",
            "--expected-sha256",
            "--expected-size-bytes",
        ):
            with self.subTest(option=option):
                omitted = list(arguments)
                index = omitted.index(option)
                del omitted[index : index + 2]
                with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                    self.helper.parser().parse_args(omitted)

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
                    "--owner",
                    "sol",
                    "--depends-on",
                    "cabin-prepare",
                    "--reserve",
                    "desktop-heavy",
                ]
            )
        self.assertEqual(code, 0)
        self.assertTrue(json.loads(stdout)["started"])
        self.assertIn(digest, desktop.call_args.args[1])
        self.assertIn("sol", desktop.call_args.args[1])
        self.assertIn("desktop-heavy", desktop.call_args.args[1])

        code, _stdout, stderr = self.run_main(
            [
                "run",
                "--job",
                "cabin",
                "--script",
                "retrain.ps1",
                "--approved-sha256",
                "not-a-hash",
                "--owner",
                "sol",
                "--reserve",
                "desktop-heavy",
            ]
        )
        self.assertEqual(code, 2)
        self.assertIn("64 hexadecimal", stderr)

    def test_resource_blocker_is_visible_without_forwarding_arbitrary_stderr(self) -> None:
        blocked = mock.Mock(
            returncode=2,
            stdout="",
            stderr="requested resources are held by unfinished jobs: old-run\n",
        )
        with mock.patch.object(self.helper.subprocess, "run", return_value=blocked):
            with self.assertRaisesRegex(self.helper.PublicError, "old-run"):
                self.helper.run_desktop("status")

        unsafe = mock.Mock(
            returncode=2,
            stdout="",
            stderr="unexpected failure at /private/secret\n",
        )
        with mock.patch.object(self.helper.subprocess, "run", return_value=unsafe):
            with self.assertRaisesRegex(self.helper.PublicError, "operation failed") as error:
                self.helper.run_desktop("status")
        self.assertNotIn("secret", str(error.exception))

    def test_progress_emits_structured_receipt_from_latest_valid_marker(self) -> None:
        with mock.patch.object(
            self.helper,
            "run_desktop",
            return_value={
                "ok": True,
                "state": "running",
                "logTail": [
                    'OPENCLAW_PROGRESS {"phase":"matching","completed":50,"total":100}',
                    "ordinary tool output",
                    'OPENCLAW_PROCESS {"kind":"brush","pid":32780}',
                    'OPENCLAW_PROGRESS {"phase":"mapping","completed":8,"total":10,'
                    '"unit":"attempts","etaSeconds":420,"message":"global BA",'
                    '"checkpoint":"cabin_interior_05000.ply"}',
                ],
            },
        ):
            code, stdout, stderr = self.run_main(["progress", "--job", "cabin-connect"])
        self.assertEqual((code, stderr), (0, ""))
        receipt = json.loads(stdout)["progressReceipt"]
        self.assertTrue(receipt["reported"])
        self.assertEqual(receipt["phase"], "mapping")
        self.assertEqual(receipt["percent"], 80.0)
        self.assertEqual(receipt["etaSeconds"], 420)
        self.assertEqual(receipt["checkpoint"]["step"], 5000)
        process = json.loads(stdout)["processReceipt"]
        self.assertEqual(process, {"reported": True, "kind": "brush", "pid": 32780})

    def test_start_plan_dry_run_seals_one_manifest_bundle_locally(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            bundle = Path(directory)
            (bundle / "train.sh").write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")
            manifest = {
                "schemaVersion": 1,
                "job": "cabin-one-run",
                "owner": "sol",
                "settings": {"iterations": 30000, "seed": 42},
                "dependencies": {"colmap": "4.2.0", "brush": "0.3.0"},
                "qualityTiers": {
                    "target": {"minimumRegisteredViews": 300},
                    "plausibleCandidate": {"minimumRegisteredViews": 248},
                    "experimentalOnly": {"promotionEligible": False},
                },
                "phases": [{"name": "train", "script": "train.sh"}],
                "artifacts": [
                    {"name": "final-sog", "path": "scene.sog", "required": True}
                ],
            }
            (bundle / "workflow.json").write_text(json.dumps(manifest), encoding="utf-8")
            with mock.patch.object(self.helper, "run_desktop") as desktop:
                code, stdout, stderr = self.run_main(
                    [
                        "start-plan",
                        "--job",
                        "cabin-one-run",
                        "--bundle",
                        str(bundle),
                        "--reserve",
                        "desktop-heavy",
                        "--dry-run",
                    ]
                )
        self.assertEqual((code, stderr), (0, ""))
        payload = json.loads(stdout)
        self.assertTrue(payload["dryRun"])
        self.assertEqual(payload["owner"], "sol")
        self.assertRegex(payload["approvalSha256"], r"\A[0-9a-f]{64}\Z")
        self.assertRegex(payload["sealedRunnerSha256"], r"\A[0-9a-f]{64}\Z")
        desktop.assert_not_called()

    def test_standalone_preflight_uses_sealed_workflow_lifecycle(self) -> None:
        with mock.patch.object(self.helper, "run_desktop") as desktop:
            code, stdout, stderr = self.run_main(
                [
                    "preflight-plan",
                    "--job",
                    "standalone-preflight",
                    "--dry-run",
                ]
            )
        self.assertEqual((code, stderr), (0, ""))
        payload = json.loads(stdout)
        self.assertTrue(payload["preflightOnly"])
        self.assertTrue(payload["dryRun"])
        self.assertEqual(payload["resources"], ["desktop-heavy"])
        self.assertRegex(payload["approvalSha256"], r"\A[0-9a-f]{64}\Z")
        self.assertRegex(payload["sealedRunnerSha256"], r"\A[0-9a-f]{64}\Z")
        desktop.assert_not_called()

    def test_shared_support_scripts_encode_preflight_copy_and_checkpoint_contracts(self) -> None:
        scripts = self.helper.SCRIPT_DIR
        canary = (scripts / "compute_canary.sh").read_text(encoding="utf-8")
        windows_io = (scripts / "windows_io.ps1").read_text(encoding="utf-8")
        windows_runner = (scripts / "windows_phase_runner.ps1").read_text(
            encoding="utf-8"
        )
        windows_process_runner = (
            scripts / "windows_process_runner.ps1"
        ).read_text(encoding="utf-8")
        runner = load_module(WORKFLOW_RUNNER, "workflow_runner_for_checkpoint_test")
        self.assertIn("SIFT_BRUTEFORCE", canary)
        self.assertIn("--FeatureExtraction.type SIFT", canary)
        self.assertIn("--FeatureMatching.type SIFT_BRUTEFORCE", canary)
        self.assertNotIn("feature_type", canary)
        self.assertIn("PRAGMA table_info", canary)
        self.assertIn("Windows Node runtime", canary)
        self.assertIn("[System.IO.File]::Copy", windows_io)
        self.assertIn("Get-FileHash", windows_io)
        self.assertIn("windows_process_runner.ps1", windows_runner)
        self.assertIn("-ReceiptFile $PidFile", windows_runner)
        self.assertIn("OPENCLAW_NATIVE_PID_FILE", canary)
        self.assertGreaterEqual(canary.count("run_windows"), 6)
        self.assertIn("[System.IO.File]::WriteAllText", windows_process_runner)
        self.assertIn("[System.Diagnostics.ProcessStartInfo]", windows_process_runner)
        self.assertIn("Get-CimInstance -ClassName Win32_Process", windows_process_runner)
        self.assertIn("treeCleanupVerified = $TreeCleanupVerified", windows_process_runner)
        self.assertNotIn("Delete($receiptPath)", windows_process_runner)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            checkpoints = root / "checkpoints"
            checkpoints.mkdir()
            checkpoint = checkpoints / "scene_05000.ply"
            checkpoint.write_bytes(
                b"ply\nformat binary_little_endian 1.0\n"
                b"element vertex 1\nproperty float x\nend_header\n"
                b"\x00\x00\x00\x00"
            )
            (checkpoints / "scene_05000.ply.complete.json").write_text(
                json.dumps(
                    {
                        "sizeBytes": checkpoint.stat().st_size,
                        "sha256": hashlib.sha256(checkpoint.read_bytes()).hexdigest(),
                    }
                ),
                encoding="utf-8",
            )
            ready, name, step = runner.checkpoint_ready(
                root,
                {
                    "directory": "checkpoints",
                    "prefix": "scene_",
                    "suffix": ".ply",
                    "minimumStep": 5000,
                },
            )
        self.assertTrue(ready)
        self.assertEqual(name, "scene_05000.ply")
        self.assertEqual(step, 5000)

    def test_incomplete_checkpoint_without_readable_receipt_is_not_ready(self) -> None:
        runner = load_module(WORKFLOW_RUNNER, "workflow_runner_for_partial_checkpoint_test")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "checkpoints").mkdir()
            (root / "checkpoints/scene_05000.ply").write_bytes(b"p")
            ready, name, step = runner.checkpoint_ready(
                root,
                {
                    "directory": "checkpoints",
                    "prefix": "scene_",
                    "suffix": ".ply",
                    "minimumStep": 5000,
                },
            )
        self.assertFalse(ready)
        self.assertIsNone(name)
        self.assertIsNone(step)

    def test_windows_native_wrapper_preserves_powershell_51_argument_array(self) -> None:
        source = (self.helper.SCRIPT_DIR / "windows_process_runner.ps1").read_text(
            encoding="utf-8"
        )
        self.assertIn(
            "[string[]]$Arguments = ConvertFrom-Json -InputObject $argumentsJson", source
        )
        self.assertNotIn("@(ConvertFrom-Json -InputObject $argumentsJson)", source)

    def test_windows_native_receipt_replacement_uses_true_null_string(self) -> None:
        source = (self.helper.SCRIPT_DIR / "windows_process_runner.ps1").read_text(
            encoding="utf-8"
        )
        self.assertIn(
            "[System.IO.File]::Replace($temporary, $receiptPath, [NullString]::Value)",
            source,
        )

    def test_brush_preflight_preserves_runtime_failure_status(self) -> None:
        source = (self.helper.SCRIPT_DIR / "compute_canary.sh").read_text(encoding="utf-8")
        gate = source[source.index("if ! brush_help=") : source.index("node_version=")]
        shell = (
            "set -euo pipefail\n"
            'run_windows_timeout() { printf "simulated runtime error\\n" >&2; '
            "return 53; }\n"
            "brush=/unused\n"
            + gate
        )
        completed = subprocess.run(
            ["/bin/bash", "-c", shell],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            check=False,
        )
        self.assertNotEqual(completed.returncode, 0)

    def test_process_group_permission_error_blocks_cleanup_verification(self) -> None:
        runner = load_module(WORKFLOW_RUNNER, "workflow_runner_for_permission_test")
        process = mock.Mock(pid=12345)
        with mock.patch.object(runner.os, "killpg", side_effect=PermissionError) as killpg:
            self.assertTrue(runner.process_group_exists(process.pid))
            self.assertFalse(runner.process_tree_absent(process, None))
        self.assertEqual(killpg.call_args_list, [mock.call(process.pid, 0)] * 2)

    def test_process_group_wait_retries_transient_permission_error(self) -> None:
        runner = load_module(WORKFLOW_RUNNER, "workflow_runner_for_transient_permission_test")
        with mock.patch.object(
            runner.os, "killpg", side_effect=[PermissionError, ProcessLookupError]
        ) as killpg:
            with mock.patch.object(runner.time, "monotonic", return_value=0):
                with mock.patch.object(runner.time, "sleep") as sleep:
                    self.assertTrue(runner.wait_for_process_group_exit(12345, 1))
        self.assertEqual(killpg.call_args_list, [mock.call(12345, 0)] * 2)
        sleep.assert_called_once_with(0.05)

    def test_process_group_wait_times_out_on_persistent_permission_error(self) -> None:
        runner = load_module(WORKFLOW_RUNNER, "workflow_runner_for_persistent_permission_test")
        with mock.patch.object(runner.os, "killpg", side_effect=PermissionError) as killpg:
            with mock.patch.object(runner.time, "monotonic", side_effect=[0, 0, 1]):
                with mock.patch.object(runner.time, "sleep") as sleep:
                    self.assertFalse(runner.wait_for_process_group_exit(12345, 1))
        self.assertEqual(killpg.call_args_list, [mock.call(12345, 0)] * 2)
        sleep.assert_called_once_with(0.05)

    def test_process_group_probe_propagates_unexpected_errors(self) -> None:
        runner = load_module(WORKFLOW_RUNNER, "workflow_runner_for_probe_error_test")
        error = OSError(errno.EIO, "process probe failed")
        with mock.patch.object(runner.os, "killpg", side_effect=error):
            with self.assertRaises(OSError) as raised:
                runner.process_group_exists(12345)
        self.assertIs(raised.exception, error)

    def test_terminate_process_removes_complete_process_group(self) -> None:
        runner = load_module(WORKFLOW_RUNNER, "workflow_runner_for_termination_test")
        with tempfile.TemporaryDirectory() as directory:
            pid_path = Path(directory) / "child.pid"
            child_source = (
                "import os,signal,sys,time; from pathlib import Path; "
                "signal.signal(signal.SIGTERM,signal.SIG_IGN); "
                "Path(sys.argv[1]).write_text(str(os.getpid())); time.sleep(60)"
            )
            parent_source = (
                "import subprocess,sys,time; "
                "subprocess.Popen([sys.executable,'-c',sys.argv[1],sys.argv[2]]); "
                "time.sleep(60)"
            )
            process = subprocess.Popen(
                [sys.executable, "-c", parent_source, child_source, str(pid_path)],
                start_new_session=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            child_pid = None
            try:
                deadline = time.monotonic() + 5
                while not pid_path.exists() and time.monotonic() < deadline:
                    time.sleep(0.01)
                self.assertTrue(pid_path.exists())
                child_pid = int(pid_path.read_text(encoding="ascii"))
                runner.terminate_process(process)
                with self.assertRaises(ProcessLookupError):
                    os.kill(child_pid, 0)
            finally:
                if child_pid is not None:
                    try:
                        os.kill(child_pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                process.wait(timeout=5)

    def test_successful_phase_cannot_release_with_live_descendants(
        self,
    ) -> None:
        runner = load_module(WORKFLOW_RUNNER, "workflow_runner_for_lingering_child_test")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            pid_path = root / "child.pid"
            parent_source = (
                "import subprocess,sys; from pathlib import Path; "
                "child=subprocess.Popen([sys.executable,'-c','import time; time.sleep(60)']); "
                "Path(sys.argv[1]).write_text(str(child.pid))"
            )
            script = (
                "exec "
                + shlex.join([sys.executable, "-c", parent_source, str(pid_path)])
                + "\n"
            )
            job, environment = self.runner_fixture(root, script)
            child_pid = None
            try:
                with mock.patch.dict(runner.os.environ, environment, clear=False):
                    with mock.patch.object(
                        runner.time, "sleep", side_effect=lambda _: REAL_SLEEP(0.01)
                    ):
                        with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                            self.assertNotEqual(
                                runner.main([str(job / "input/workflow.json")]), 0
                            )
                child_pid = int(pid_path.read_text(encoding="ascii"))
                with self.assertRaises(ProcessLookupError):
                    os.kill(child_pid, 0)
                state = json.loads(
                    (job / "state/workflow-state.json").read_text(encoding="utf-8")
                )
                self.assertEqual(state["state"], "failed")
                self.assertFalse(
                    state["resourceReleaseVerified"] and runner.process_group_exists(
                        state["phases"]["finish"]["pid"]
                    )
                )
            finally:
                if child_pid is not None:
                    try:
                        os.kill(child_pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass

    def test_deleted_native_receipt_cannot_prove_detached_child_cleanup(self) -> None:
        runner = load_module(WORKFLOW_RUNNER, "workflow_runner_for_native_evidence_test")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            child_pid_path = root / "native-child.pid"
            wrapper_source = (
                "import os,subprocess,sys,time; from pathlib import Path; "
                "child=subprocess.Popen([sys.executable,'-c','import time; time.sleep(60)'],"
                "start_new_session=True); "
                "Path(sys.argv[1]).write_text(str(child.pid)); "
                "receipt=Path(os.environ['OPENCLAW_NATIVE_PID_FILE']); "
                "receipt.write_text(str(os.getpid())); time.sleep(0.15); receipt.unlink()"
            )
            script = (
                "exec "
                + shlex.join(
                    [sys.executable, "-c", wrapper_source, str(child_pid_path)]
                )
                + "\n"
            )
            job, environment = self.runner_fixture(root, script)
            child_pid = None
            try:
                with mock.patch.dict(runner.os.environ, environment, clear=False):
                    with mock.patch.object(
                        runner.time, "sleep", side_effect=lambda _: REAL_SLEEP(0.01)
                    ):
                        with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                            self.assertNotEqual(
                                runner.main([str(job / "input/workflow.json")]), 0
                            )
                state = json.loads(
                    (job / "state/workflow-state.json").read_text(encoding="utf-8")
                )
                self.assertIn("nativePid", state["phases"]["finish"])
                child_pid = int(child_pid_path.read_text(encoding="ascii"))
                os.kill(child_pid, 0)
                self.assertFalse(state["resourceReleaseVerified"])
            finally:
                if child_pid is not None:
                    try:
                        os.kill(child_pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass

    def test_clean_phase_failure_still_awaits_manual_windows_verification(self) -> None:
        runner = load_module(WORKFLOW_RUNNER, "workflow_runner_for_clean_failure_test")
        dispatcher = load_module(
            DESKTOP_DISPATCHER, "desktop_compute_for_clean_failure_test"
        )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            job, environment = self.runner_fixture(root, "exit 7\n")
            with mock.patch.dict(runner.os.environ, environment, clear=False):
                with mock.patch.object(
                    runner.time, "sleep", side_effect=lambda _: REAL_SLEEP(0.01)
                ):
                    with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                        return_code = runner.main(
                            [str(job / "input/workflow.json")]
                        )
            self.assertNotEqual(return_code, 0)
            (job / "state/exit-code").write_text(
                f"{return_code}\n", encoding="ascii"
            )
            state = json.loads(
                (job / "state/workflow-state.json").read_text(encoding="utf-8")
            )
            self.assertFalse(state["resourceReleaseVerified"])
            self.assertEqual(
                state["reservationState"],
                "awaiting_manual_windows_verification",
            )
            self.assertIsInstance(
                state["reservationVerificationRequiredUnixSeconds"], int
            )
            for details in state["phases"].values():
                if "pid" in details:
                    self.assertFalse(runner.process_group_exists(details["pid"]))
            with mock.patch.dict(dispatcher.ROOTS, {"splat": root}, clear=True):
                self.assertEqual(
                    dispatcher.active_resource_holders(
                        "splat", ["desktop-heavy"], "replacement"
                    ),
                    ["review"],
                )

    def test_success_keeps_computation_and_reservation_states_separate(self) -> None:
        runner = load_module(WORKFLOW_RUNNER, "workflow_runner_for_supervised_success_test")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            job, environment = self.runner_fixture(root, "exit 0\n")
            with mock.patch.dict(runner.os.environ, environment, clear=False):
                with mock.patch.object(
                    runner.time, "sleep", side_effect=lambda _: REAL_SLEEP(0.01)
                ):
                    with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                        return_code = runner.main(
                            [str(job / "input/workflow.json")]
                        )
            state = json.loads(
                (job / "state/workflow-state.json").read_text(encoding="utf-8")
            )
        self.assertEqual(return_code, 0)
        self.assertEqual(state["state"], "succeeded")
        self.assertEqual(
            state["reservationState"], "awaiting_manual_windows_verification"
        )
        self.assertFalse(state["resourceReleaseVerified"])

    def test_native_windows_termination_requires_absence_verification(self) -> None:
        runner = load_module(WORKFLOW_RUNNER, "workflow_runner_for_native_termination_test")
        taskkill_result = subprocess.CompletedProcess([], 0)
        still_running = subprocess.CompletedProcess([], 3)
        with mock.patch.object(
            runner.subprocess,
            "run",
            side_effect=[taskkill_result, still_running],
        ):
            with mock.patch.object(runner.time, "monotonic", side_effect=[0, 16]):
                with self.assertRaisesRegex(runner.WorkflowError, "still running"):
                    runner.terminate_windows_process_tree(1234)

    def test_failed_taskkill_cannot_be_verified_by_parent_absence(self) -> None:
        runner = load_module(WORKFLOW_RUNNER, "workflow_runner_for_taskkill_failure_test")
        taskkill_failed = subprocess.CompletedProcess([], 128)
        parent_absent = subprocess.CompletedProcess([], 0)
        with mock.patch.object(
            runner.subprocess,
            "run",
            side_effect=[taskkill_failed, parent_absent],
        ):
            with self.assertRaisesRegex(runner.WorkflowError, "termination failed"):
                runner.terminate_windows_process_tree(1234)

    def test_durable_native_completion_receipt_is_positive_cleanup_evidence(
        self,
    ) -> None:
        runner = load_module(WORKFLOW_RUNNER, "workflow_runner_for_native_receipt_test")
        with tempfile.TemporaryDirectory() as directory:
            receipt = Path(directory) / "native-process.json"
            receipt.write_text(
                json.dumps(
                    {
                        "schemaVersion": 1,
                        "state": "complete",
                        "rootPid": 1234,
                        "targetPid": 5678,
                        "treeCleanupVerified": True,
                        "exitCode": 0,
                    }
                ),
                encoding="utf-8",
            )
            process = mock.Mock(pid=4321)
            with mock.patch.object(runner, "process_group_exists", return_value=False):
                with mock.patch.object(runner, "windows_process_exists", return_value=False):
                    self.assertTrue(
                        runner.process_tree_absent(
                            process,
                            receipt,
                            native_evidence_required=True,
                        )
                    )

    def test_workflow_approval_binds_dependencies_and_resources(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            bundle = Path(directory)
            (bundle / "finish.sh").write_text("exit 0\n", encoding="utf-8")
            (bundle / "workflow.json").write_text(
                json.dumps(
                    {
                        "schemaVersion": 1,
                        "job": "approval-review",
                        "owner": "sol",
                        "settings": {"iterations": 5000},
                        "dependencies": {"colmap": "4.2.0"},
                        "qualityTiers": {
                            "target": {"minimumRegisteredViews": 300},
                            "plausibleCandidate": {"minimumRegisteredViews": 248},
                            "experimentalOnly": {"promotionEligible": False},
                        },
                        "phases": [{"name": "finish", "script": "finish.sh"}],
                    }
                ),
                encoding="utf-8",
            )
            approvals = []
            for reservation, dependency in (
                ("desktop-heavy", "base-model"),
                ("anything-else", "different-base"),
            ):
                code, stdout, stderr = self.run_main(
                    [
                        "start",
                        "--job",
                        "approval-review",
                        "--bundle",
                        str(bundle),
                        "--reserve",
                        reservation,
                        "--depends-on",
                        dependency,
                        "--dry-run",
                    ]
                )
                self.assertEqual((code, stderr), (0, ""))
                approvals.append(json.loads(stdout)["approvalSha256"])
        self.assertNotEqual(*approvals)

    def test_runner_owns_phases_and_writes_one_complete_run_manifest(self) -> None:
        runner = load_module(WORKFLOW_RUNNER, "workflow_runner_for_execution_test")
        approval = "f" * 64
        with tempfile.TemporaryDirectory() as directory:
            job_root = Path(directory) / "one-run"
            input_root = job_root / "input"
            (job_root / "state").mkdir(parents=True)
            (job_root / "outputs").mkdir()
            input_root.mkdir()
            (input_root / "compute_canary.sh").write_text(
                "#!/usr/bin/env bash\nexit 0\n", encoding="utf-8"
            )
            (input_root / "finish.sh").write_text(
                "#!/usr/bin/env bash\nprintf 'sog' > outputs/scene.sog\n",
                encoding="utf-8",
            )
            manifest = {
                "schemaVersion": 1,
                "job": "one-run",
                "owner": "sol",
                "settings": {"iterations": 5000, "seed": 42},
                "dependencies": {"colmap": "4.2.0", "brush": "0.3.0"},
                "qualityTiers": {
                    "target": {"minimumRegisteredViews": 300},
                    "plausibleCandidate": {"minimumRegisteredViews": 248},
                    "experimentalOnly": {"promotionEligible": False},
                },
                "phases": [{"name": "finish", "script": "finish.sh"}],
                "artifacts": [{"name": "scene", "path": "scene.sog"}],
            }
            manifest_path = input_root / "workflow.json"
            manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
            (input_root / "approval-inventory.json").write_text(
                json.dumps(
                    {
                        "approvalSha256": approval,
                        "execution": {
                            "job": "one-run",
                            "owner": "sol",
                            "externalDependencies": ["base-model"],
                            "resources": ["desktop-heavy"],
                        },
                        "files": [],
                    }
                ),
                encoding="utf-8",
            )
            environment = {
                "OPENCLAW_WORKFLOW_APPROVAL_SHA256": approval,
                "OPENCLAW_JOB_NAME": "one-run",
                "OPENCLAW_JOB_OWNER": "sol",
                "OPENCLAW_JOB_DEPENDENCIES_JSON": '["base-model"]',
                "OPENCLAW_JOB_RESOURCES_JSON": '["desktop-heavy"]',
                "OPENCLAW_RUNNER_SHA256": "e" * 64,
            }
            with mock.patch.dict(runner.os.environ, environment, clear=False):
                with mock.patch.object(runner.time, "sleep", return_value=None):
                    self.assertEqual(runner.run_workflow(manifest_path), 0)
            result = json.loads(
                (job_root / "outputs/run-manifest.json").read_text(encoding="utf-8")
            )
        self.assertEqual(result["approvalScope"]["sha256"], approval)
        self.assertEqual(result["execution"]["owner"], "sol")
        self.assertEqual(result["execution"]["externalDependencies"], ["base-model"])
        self.assertEqual(result["execution"]["resources"], ["desktop-heavy"])
        self.assertEqual(result["execution"]["sealedRunnerSha256"], "e" * 64)
        self.assertEqual(result["manifest"]["settings"]["iterations"], 5000)
        self.assertEqual(result["artifacts"][0]["sha256"], hashlib.sha256(b"sog").hexdigest())

    def test_primary_operator_commands_delegate_without_new_diagnostic_jobs(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            bundle = Path(directory)
            (bundle / "finish.sh").write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")
            (bundle / "workflow.json").write_text(
                json.dumps(
                    {
                        "schemaVersion": 1,
                        "job": "one-run",
                        "owner": "sol",
                        "settings": {"iterations": 5000},
                        "dependencies": {"colmap": "4.2.0"},
                        "qualityTiers": {
                            "target": {"minimumRegisteredViews": 300},
                            "plausibleCandidate": {"minimumRegisteredViews": 248},
                            "experimentalOnly": {"promotionEligible": False},
                        },
                        "phases": [{"name": "finish", "script": "finish.sh"}],
                        "artifacts": [],
                    }
                ),
                encoding="utf-8",
            )
            code, stdout, stderr = self.run_main(
                [
                    "start",
                    "--job",
                    "one-run",
                    "--bundle",
                    str(bundle),
                    "--reserve",
                    "desktop-heavy",
                    "--dry-run",
                ]
            )
            self.assertEqual((code, stderr), (0, ""))
            approval = json.loads(stdout)["approvalSha256"]
            sealed_runner_sha = ""

            def remote(command, arguments=(), **_kwargs):
                nonlocal sealed_runner_sha
                if command == "stage":
                    source = Path(arguments[arguments.index("--source") + 1])
                    seal = source / "run-workflow.sh" if source.is_dir() else source
                    if seal.name == "run-workflow.sh" and seal.is_file():
                        sealed_runner_sha = self.helper.hash_file(seal)
                    return {"ok": True}
                if command == "plan-run":
                    return {"ok": True, "sha256": sealed_runner_sha}
                if command == "run":
                    return {"ok": True, "started": True}
                if command == "progress":
                    return {"ok": True, "state": "running", "logTail": []}
                if command == "cancel":
                    return {"ok": True, "cancelRequested": True}
                self.fail(f"unexpected remote command: {command}")

            with mock.patch.object(self.helper, "run_desktop", side_effect=remote) as desktop:
                code, stdout, stderr = self.run_main(
                    [
                        "start",
                        "--job",
                        "one-run",
                        "--bundle",
                        str(bundle),
                        "--approved-workflow-sha256",
                        approval,
                        "--reserve",
                        "desktop-heavy",
                    ]
                )
                self.assertEqual((code, stderr), (0, ""))
                self.assertTrue(json.loads(stdout)["runPerformed"])
                self.assertEqual(self.run_main(["inspect", "--job", "one-run"])[0], 0)
                self.assertEqual(
                    self.run_main(["cancel", "--job", "one-run", "--owner", "sol"])[0],
                    0,
                )
        commands = [call.args[0] for call in desktop.call_args_list]
        self.assertEqual(commands.count("run"), 1)
        self.assertEqual(commands[-2:], ["progress", "cancel"])

    def test_progress_ignores_malformed_or_unbounded_markers(self) -> None:
        payload = {
            "state": "running",
            "logTail": [
                "OPENCLAW_PROGRESS not-json",
                'OPENCLAW_PROGRESS {"phase":"Bad Phase","etaSeconds":999999999}',
            ],
        }
        self.assertEqual(
            self.helper.progress_receipt(payload),
            {"state": "running", "reported": False},
        )

    def test_manual_release_is_human_confirmed_and_hash_bound(self) -> None:
        with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            self.helper.parser().parse_args(
                [
                    "verify-release",
                    "--job",
                    "one-run",
                    "--owner",
                    "sol",
                    "--approved-workflow-sha256",
                    "a" * 64,
                ]
            )
        with mock.patch.object(
            self.helper,
            "run_desktop",
            return_value={"ok": True, "released": True},
        ) as desktop:
            code, stdout, stderr = self.run_main(
                [
                    "verify-release",
                    "--job",
                    "one-run",
                    "--owner",
                    "sol",
                    "--approved-workflow-sha256",
                    "a" * 64,
                    "--confirm-windows-processes-absent",
                ]
            )
        self.assertEqual((code, stderr), (0, ""))
        self.assertTrue(json.loads(stdout)["released"])
        self.assertEqual(desktop.call_args.args[0], "verify-release")
        self.assertIn("--confirm-windows-processes-absent", desktop.call_args.args[1])

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
