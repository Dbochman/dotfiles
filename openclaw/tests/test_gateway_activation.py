import hashlib
import importlib.util
import json
import os
from pathlib import Path
import plistlib
import stat
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock


SCRIPT = Path(__file__).resolve().parents[1] / "bin/gateway_activation.py"
SPEC = importlib.util.spec_from_file_location("gateway_activation", SCRIPT)
activation = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = activation
SPEC.loader.exec_module(activation)
NODE_VERSION = "24.21.0"
PACKAGE_VERSION = "2026.8.35"
STAMP = 1700000000


class FakeProbe(activation.SystemProbe):
    def __init__(self, paths, arguments):
        super().__init__(paths)
        self.arguments = arguments
        self.pid = 1234
        self.version = NODE_VERSION
        self.started = STAMP + 10
        self.mapping_valid = True
        self.listener_valid = True
        self.health = {"ok": True}
        self.payload = {"skills": [
            {"name": name, "eligible": True, "modelVisible": True}
            for name in activation.REQUIRED_SKILLS
        ]}
        self.rpc_error = False
        self.on_rpc = lambda: None
        self.calls = []
        self.retries = 0

    def wait_for_retry(self):
        self.retries += 1
        with mock.patch.object(activation.time, "sleep"):
            super().wait_for_retry()

    def run(self, command, input_text=None, limit=5):
        self.calls.append(command)
        binary = Path(command[0]).name
        if command[1:] == ["--version"]:
            return "v" + self.version + "\n"
        if binary == "launchctl":
            return f"pid = {self.pid}\narguments = {{\n" + "\n".join(self.arguments) + "\n}\n"
        if binary == "ps":
            return time.strftime("%a %b %d %H:%M:%S %Y", time.localtime(self.started))
        if binary == "lsof" and "-d" in command:
            node = self.paths.node(self.version).resolve()
            name = str(node) if self.mapping_valid else "/wrong/node"
            return f"p{self.pid}\nftxt\ni{node.stat().st_ino}\nn{name}\n"
        if binary == "lsof":
            owner = self.pid if self.listener_valid else 9876
            return f"p{owner}\nf12\nn127.0.0.1:18789\n"
        if binary == "curl":
            return json.dumps(self.health)
        if binary == "bash" and "read_active_skills" in command[2]:
            if self.rpc_error:
                raise activation.VerificationError("probe_failed")
            self.on_rpc()
            return json.dumps(self.payload)
        if binary == "bash" and "missing_skills_from_status" in command[2]:
            return super().run(command, input_text, limit)
        raise AssertionError(f"unexpected probe: {command}")


class GatewayActivationTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.home = Path(self.temporary.name).resolve()
        self.paths = activation.Paths(self.home, self.home / "brew")
        self.arguments = ["/bin/sh", str(self.paths.environment_wrapper),
                          str(self.paths.environment_file), str(self.paths.app),
                          "gateway", "--port", "18789"]
        files = {
            self.paths.wrapper: "reviewed wrapper\n",
            self.paths.node(NODE_VERSION): "fake node\n",
            self.paths.entry: "fake entry\n",
            self.paths.package: json.dumps({"version": PACKAGE_VERSION}),
            self.paths.environment_wrapper: "generated wrapper\n",
            self.paths.environment_file: "generated env\n",
        }
        for path, contents in files.items():
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(contents)
            path.chmod(0o700)
            os.utime(path, (STAMP, STAMP))
        self.paths.plist.parent.mkdir(parents=True)
        self.paths.plist.write_bytes(plistlib.dumps({"ProgramArguments": self.arguments}))
        os.utime(self.paths.plist, (STAMP, STAMP))
        applications = self.home / "Applications"
        applications.mkdir()
        (applications / "OpenClawGateway.app").symlink_to(self.paths.wrapper.parents[2])
        self.paths.receipt.parent.mkdir(parents=True)
        self.paths.receipt.write_text("previous\n")
        self.paths.receipt.chmod(0o600)
        cache = self.home / ".openclaw/.secrets-cache"
        cache.write_text('OPENCLAW_GATEWAY_TOKEN="synthetic-secret"\n')
        cache.chmod(0o600)
        self.digest = hashlib.sha256(self.paths.wrapper.read_bytes()).hexdigest()
        self.node_digest = hashlib.sha256(self.paths.node(NODE_VERSION).read_bytes()).hexdigest()
        self.probe = FakeProbe(self.paths, self.arguments.copy())

    def verify(self, record=True, previous_pid=99, digest=None):
        return activation.verify(self.paths, self.probe, digest or self.digest,
                                 NODE_VERSION, PACKAGE_VERSION, previous_pid, record, self.node_digest)

    def assert_blocked(self):
        with self.assertRaises((activation.VerificationError, OSError, ValueError)):
            self.verify()
        self.assertEqual(self.paths.receipt.read_text(), "previous\n")
        self.assertEqual(list(self.paths.receipt.parent.glob(".gateway-wrapper.sha256.*")), [])

    def test_runtime_paths_separate_official_node24_and_homebrew_rollback(self):
        self.assertEqual(
            self.paths.node("24.21.0"),
            self.home / ".local/share/openclaw/runtimes/node-v24.21.0-darwin-arm64/bin/node",
        )
        self.assertEqual(self.paths.node("22.23.1"), self.home / "brew/opt/node@22/bin/node")

    def test_read_only_default_preserves_receipt_and_creates_no_lock(self):
        before = self.paths.receipt.stat()
        result = self.verify(record=False, previous_pid=None)
        self.assertFalse(result["receiptRecorded"])
        after = self.paths.receipt.stat()
        for attribute in ("st_dev", "st_ino", "st_mode", "st_nlink", "st_uid", "st_gid",
                          "st_size", "st_mtime_ns", "st_ctime_ns"):
            self.assertEqual(getattr(after, attribute), getattr(before, attribute), attribute)
        self.assertEqual(self.paths.receipt.read_text(), "previous\n")
        self.assertFalse((self.paths.receipt.parent / ".gateway-activation.lock").exists())

    def test_missing_runtime_hash_refuses_all_probes(self):
        with self.assertRaisesRegex(activation.VerificationError, "runtime_hash_required"):
            activation.verify(self.paths, self.probe, self.digest, NODE_VERSION, PACKAGE_VERSION)
        self.assertEqual(self.probe.calls, [])
        self.assertEqual(self.paths.receipt.read_text(), "previous\n")

    def test_same_version_replaced_runtime_is_rejected_before_execution(self):
        self.paths.node(NODE_VERSION).write_text("different node with same version\n")
        with self.assertRaisesRegex(activation.VerificationError, "runtime_hash_mismatch"):
            self.verify()
        self.assertEqual(self.probe.calls, [])
        self.assertEqual(self.paths.receipt.read_text(), "previous\n")

    def test_runtime_replacement_during_rpc_preserves_receipt(self):
        self.probe.on_rpc = lambda: self.paths.node(NODE_VERSION).write_text("replaced during RPC\n")
        with self.assertRaisesRegex(activation.VerificationError, "runtime_hash_mismatch"):
            self.verify()
        self.assertEqual(self.paths.receipt.read_text(), "previous\n")

    def test_verified_record_is_atomic_and_private(self):
        result = self.verify()
        self.assertTrue(result["receiptRecorded"])
        self.assertEqual(result["pid"], 1234)
        self.assertEqual(self.paths.receipt.read_text(), self.digest + "\n")
        self.assertEqual(stat.S_IMODE(self.paths.receipt.stat().st_mode), 0o600)
        self.assertEqual(list(self.paths.receipt.parent.glob(".gateway-wrapper.sha256.*")), [])

    def test_missing_previous_pid_blocks_record(self):
        with self.assertRaisesRegex(activation.VerificationError, "previous_pid_required"):
            self.verify(previous_pid=None)
        self.assertEqual(self.paths.receipt.read_text(), "previous\n")

    def test_same_previous_pid_blocks_record(self):
        with self.assertRaisesRegex(activation.VerificationError, "not_replaced"):
            self.verify(previous_pid=self.probe.pid)

    def test_wrong_hash_blocks_record(self):
        with self.assertRaisesRegex(activation.VerificationError, "hash_mismatch"):
            self.verify(digest="0" * 64)
        self.assertEqual(self.paths.receipt.read_text(), "previous\n")

    def test_runtime_version_and_package_mismatch(self):
        self.probe.version = "22.23.1"
        self.assert_blocked()
        self.probe.version = NODE_VERSION
        self.paths.package.write_text('{"version":"wrong"}')
        self.assert_blocked()

    def test_node22_rollback_uses_same_verification_contract(self):
        version = "22.23.1"
        node = self.paths.node(version)
        node.parent.mkdir(parents=True)
        node.write_text("rollback node\n")
        node.chmod(0o700)
        os.utime(node, (STAMP, STAMP))
        self.probe.version = version
        result = activation.verify(self.paths, self.probe, self.digest,
                                   version, PACKAGE_VERSION, previous_pid=99, record=True,
                                   node_hash=hashlib.sha256(node.read_bytes()).hexdigest())
        self.assertTrue(result["receiptRecorded"])
        self.assertEqual(result["nodeVersion"], version)

    def test_malformed_package_and_plist_are_rejected(self):
        self.paths.package.write_text("[]")
        self.assert_blocked()
        self.paths.package.write_text(json.dumps({"version": PACKAGE_VERSION}))
        self.paths.plist.write_bytes(plistlib.dumps([]))
        self.assert_blocked()

    def test_missing_runtime_and_unsafe_cache_are_rejected(self):
        node = self.paths.node(NODE_VERSION)
        node.chmod(0o600)
        self.assert_blocked()
        node.chmod(0o700)
        (self.home / ".openclaw/.secrets-cache").chmod(0o644)
        self.assert_blocked()

    def test_process_must_postdate_artifacts(self):
        self.probe.started = STAMP
        self.assert_blocked()

    def test_loaded_job_must_match_generated_contract(self):
        self.probe.arguments = ["/wrong/wrapper"]
        self.assert_blocked()

    def test_generated_contract_cannot_bypass_app(self):
        self.paths.plist.write_bytes(plistlib.dumps({"ProgramArguments": ["/wrong/app"]}))
        self.assert_blocked()

    def test_executable_mapping_and_listener_owner_must_match(self):
        self.probe.mapping_valid = False
        self.assert_blocked()
        self.probe.mapping_valid = True
        self.probe.listener_valid = False
        self.assert_blocked()

    def test_nested_ok_does_not_pass_health(self):
        self.probe.health = {"ok": False, "nested": {"ok": True}}
        self.assert_blocked()

    def test_rpc_failure_and_invalid_or_ineligible_skills_preserve_receipt(self):
        self.probe.rpc_error = True
        self.assert_blocked()
        self.probe.rpc_error = False
        for payload in ({}, {"skills": []}, {"skills": [
                {"name": name, "eligible": True, "modelVisible": False}
                for name in activation.REQUIRED_SKILLS]}):
            with self.subTest(payload=payload):
                self.probe.payload = payload
                self.assert_blocked()

    def test_process_change_during_rpc_preserves_receipt(self):
        self.probe.on_rpc = lambda: setattr(self.probe, "pid", 4321)
        self.assert_blocked()

    def test_transient_rpc_failure_and_catalog_warmup_are_retried(self):
        ready = self.probe.payload
        attempts = []

        def warmup():
            attempts.append(True)
            if len(attempts) == 1:
                raise activation.VerificationError("probe_failed")
            self.probe.payload = {"skills": []} if len(attempts) == 2 else ready

        self.probe.on_rpc = warmup
        self.assertTrue(self.verify()["receiptRecorded"])
        self.assertEqual(len(attempts), 3)
        self.assertEqual(self.probe.retries, 2)

    def test_permanent_rpc_failure_stops_after_ten_attempts(self):
        self.probe.rpc_error = True
        self.assert_blocked()
        calls = [command for command in self.probe.calls if "read_active_skills" in " ".join(command)]
        self.assertEqual(len(calls), 10)
        self.assertEqual(self.probe.retries, 9)

    def test_retry_cannot_exceed_verification_deadline(self):
        self.probe.rpc_error = True
        self.probe.deadline = time.monotonic() - 1
        with self.assertRaisesRegex(activation.VerificationError, "verification_deadline"):
            self.verify()
        self.assertEqual(self.paths.receipt.read_text(), "previous\n")

    def test_wrapper_change_during_rpc_preserves_receipt(self):
        self.probe.on_rpc = lambda: self.paths.wrapper.write_text("changed")
        self.assert_blocked()

    def test_external_receipt_change_is_not_overwritten(self):
        self.probe.on_rpc = lambda: self.paths.receipt.write_text("other writer\n")
        with self.assertRaisesRegex(activation.VerificationError, "evidence_changed"):
            self.verify()
        self.assertEqual(self.paths.receipt.read_text(), "other writer\n")

    def test_busy_lock_blocks_another_writer(self):
        with activation.receipt_lock(self.paths.receipt):
            with self.assertRaisesRegex(activation.VerificationError, "busy"):
                self.verify()
        self.assertEqual(self.paths.receipt.read_text(), "previous\n")

    def test_symlink_receipt_and_writable_directory_are_rejected(self):
        self.paths.receipt.unlink()
        target = self.home / "target"
        target.write_text("untouched")
        self.paths.receipt.symlink_to(target)
        with self.assertRaises(activation.VerificationError):
            self.verify()
        self.assertEqual(target.read_text(), "untouched")
        self.paths.receipt.unlink()
        self.paths.receipt.parent.chmod(0o777)
        with self.assertRaisesRegex(activation.VerificationError, "unsafe_receipt_directory"):
            self.verify()

    def test_atomic_replace_failure_preserves_previous_receipt(self):
        with mock.patch.object(activation.os, "replace", side_effect=OSError("synthetic")):
            self.assert_blocked()

    def test_change_at_final_publication_check_preserves_receipt(self):
        snapshot = self.probe.snapshot
        calls = []

        def changed_snapshot(*arguments):
            calls.append(True)
            if len(calls) == 3:
                self.probe.pid = 4321
            return snapshot(*arguments)

        with mock.patch.object(self.probe, "snapshot", side_effect=changed_snapshot):
            self.assert_blocked()

    def test_bounded_probe_timeout_and_clean_environment(self):
        probe = activation.SystemProbe(self.paths)
        output = probe.run([sys.executable, "-c",
                            "import os; print(','.join(sorted(os.environ)))"])
        self.assertNotIn("TOKEN", output)
        self.assertNotIn("SECRET", output)
        started = time.monotonic()
        with self.assertRaisesRegex(activation.VerificationError, "probe_timeout"):
            probe.run(["/bin/sleep", "10"], limit=0.05)
        self.assertLess(time.monotonic() - started, 3)
        probe.deadline = time.monotonic() - 1
        with self.assertRaisesRegex(activation.VerificationError, "verification_deadline"):
            probe.run(["/usr/bin/true"])

    def test_cli_rejects_health_override_without_probing(self):
        result = subprocess.run(
            [sys.executable, str(SCRIPT), "--expected-wrapper-sha256", self.digest,
             "--expected-node-version", NODE_VERSION, "--expected-package-version",
             PACKAGE_VERSION, "--expected-node-sha256", self.node_digest, "--health-passed"],
            env={"HOME": str(self.home)}, capture_output=True, text=True, timeout=5,
        )
        self.assertEqual(result.returncode, 2)
        self.assertEqual(self.paths.receipt.read_text(), "previous\n")


if __name__ == "__main__":
    unittest.main()
