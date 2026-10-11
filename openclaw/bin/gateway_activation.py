#!/usr/bin/python3

import argparse
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
import fcntl
import hashlib
import json
import os
from pathlib import Path
import plistlib
import re
import signal
import stat
import subprocess
import sys
import tempfile
import time


LIBRARY = Path(__file__).resolve().parents[1] / "lib/deployment.sh"
REQUIRED_SKILLS = ("opentable", "restaurant-book", "restaurant-snipe", "resy")


class VerificationError(Exception):
    pass


@dataclass(frozen=True)
class Paths:
    home: Path
    prefix: Path = Path("/opt/homebrew")

    @property
    def wrapper(self):
        return self.home / "dotfiles/openclaw/OpenClawGateway.app/Contents/MacOS/OpenClawGateway"

    @property
    def app(self):
        return self.home / "Applications/OpenClawGateway.app/Contents/MacOS/OpenClawGateway"

    @property
    def plist(self):
        return self.home / "Library/LaunchAgents/ai.openclaw.gateway.plist"

    @property
    def environment_wrapper(self):
        return self.home / ".openclaw/service-env/ai.openclaw.gateway-env-wrapper.sh"

    @property
    def environment_file(self):
        return self.home / ".openclaw/service-env/ai.openclaw.gateway.env"

    @property
    def entry(self):
        return self.prefix / "lib/node_modules/openclaw/dist/index.js"

    @property
    def package(self):
        return self.entry.parent.parent / "package.json"

    @property
    def receipt(self):
        return self.home / ".openclaw/state/gateway-wrapper.sha256"

    def node(self, version):
        return self.prefix / f"opt/node@{version.split('.')[0]}/bin/node"


def protected_file(path, mode=None):
    metadata = path.lstat()
    if (not stat.S_ISREG(metadata.st_mode) or metadata.st_uid != os.getuid()
            or metadata.st_nlink != 1 or metadata.st_mode & 0o022
            or (mode is not None and stat.S_IMODE(metadata.st_mode) != mode)):
        raise VerificationError("unsafe_file_metadata")
    return metadata


def fingerprint(path):
    metadata = protected_file(path)
    return (metadata.st_dev, metadata.st_ino, metadata.st_mtime_ns,
            metadata.st_size, hashlib.sha256(path.read_bytes()).hexdigest())


def receipt_snapshot(path):
    if path.exists() or path.is_symlink():
        if protected_file(path, 0o600).st_size > 65:
            raise VerificationError("invalid_receipt_size")
        return fingerprint(path)
    return None


class SystemProbe:
    def __init__(self, paths):
        self.paths = paths
        self.deadline = time.monotonic() + 45

    def run(self, command, input_text=None, limit=5):
        remaining = min(limit, self.deadline - time.monotonic())
        if remaining <= 0:
            raise VerificationError("verification_deadline")
        environment = {"HOME": str(self.paths.home), "LC_ALL": "C",
                       "PATH": "/usr/bin:/bin:/usr/sbin:/sbin"}
        with subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                              stderr=subprocess.DEVNULL, text=True, env=environment,
                              start_new_session=True) as process:
            try:
                output, _ = process.communicate(input_text, timeout=remaining)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)
                process.communicate()
                raise VerificationError("probe_timeout") from None
            if process.returncode or len(output) > 4 * 1024 * 1024:
                raise VerificationError("probe_failed")
            return output

    def snapshot(self, expected_hash, node_version, package_version, previous_pid):
        node = self.paths.node(node_version).resolve(strict=True)
        if not os.access(node, os.X_OK):
            raise VerificationError("runtime_not_executable")
        if self.run([str(node), "--version"]).strip() != "v" + node_version:
            raise VerificationError("runtime_version_mismatch")
        wrapper = self.paths.wrapper
        if self.paths.app.resolve(strict=True) != wrapper.resolve(strict=True):
            raise VerificationError("app_target_mismatch")
        files = (wrapper, node, self.paths.entry, self.paths.package, self.paths.plist,
                 self.paths.environment_wrapper, self.paths.environment_file)
        identities = tuple(fingerprint(path) for path in files)
        if identities[0][-1] != expected_hash:
            raise VerificationError("wrapper_hash_mismatch")
        package = json.loads(self.paths.package.read_text())
        if not isinstance(package, dict) or package.get("version") != package_version:
            raise VerificationError("package_version_mismatch")
        plist = plistlib.loads(self.paths.plist.read_bytes())
        expected_arguments = ["/bin/sh", str(self.paths.environment_wrapper),
                              str(self.paths.environment_file), str(self.paths.app),
                              "gateway", "--port", "18789"]
        if not isinstance(plist, dict) or plist.get("ProgramArguments") != expected_arguments:
            raise VerificationError("launch_contract_mismatch")
        launch = self.run(["/bin/launchctl", "print", f"gui/{os.getuid()}/ai.openclaw.gateway"])
        matches = re.findall(r"^\s*pid = ([1-9][0-9]*)\s*$", launch, re.M)
        if len(matches) != 1:
            raise VerificationError("gateway_pid_unavailable")
        pid = int(matches[0])
        if pid == previous_pid:
            raise VerificationError("gateway_not_replaced")
        loaded_arguments = re.search(r"^\s*arguments = \{\n(.*?)^\s*\}", launch, re.M | re.S)
        if not loaded_arguments or [line.strip() for line in loaded_arguments[1].splitlines()
                                    if line.strip()] != expected_arguments:
            raise VerificationError("loaded_launch_contract_mismatch")
        started = self.run(["/bin/ps", "-p", str(pid), "-o", "lstart="]).strip()
        started_at = datetime.strptime(started, "%a %b %d %H:%M:%S %Y").timestamp()
        if started_at <= max(identity[2] / 1_000_000_000 for identity in identities):
            raise VerificationError("process_predates_artifacts")
        mapping = self.run(["/usr/sbin/lsof", "-nP", "-a", "-p", str(pid), "-d", "txt", "-Ffin"])
        names = [line[1:] for line in mapping.splitlines() if line.startswith("n")]
        inodes = [line[1:] for line in mapping.splitlines() if line.startswith("i")]
        if not names or not inodes or names[0] != str(node) or inodes[0] != str(node.stat().st_ino):
            raise VerificationError("executable_mapping_mismatch")
        listeners = self.run(["/usr/sbin/lsof", "-nP", "-iTCP:18789", "-sTCP:LISTEN", "-Fpn"])
        owners = {line[1:] for line in listeners.splitlines() if line.startswith("p")}
        endpoints = {line[1:] for line in listeners.splitlines() if line.startswith("n")}
        if owners != {str(pid)} or not endpoints or not endpoints <= {"127.0.0.1:18789", "[::1]:18789"}:
            raise VerificationError("listener_owner_mismatch")
        return {"pid": pid, "started": started, "files": identities}

    def check_health_and_skills(self, node_version):
        health = json.loads(self.run(["/usr/bin/curl", "--noproxy", "*", "-fsS",
                                      "--max-time", "2", "http://127.0.0.1:18789/health"]))
        if not isinstance(health, dict) or health.get("ok") is not True:
            raise VerificationError("gateway_unhealthy")
        cache = self.paths.home / ".openclaw/.secrets-cache"
        protected_file(cache, 0o600)
        for attempt in range(10):
            try:
                payload = self.run([
                    "/bin/bash", "-c", 'source "$1"; openclaw_gateway_read_active_skills "$2" "$3" "$4"',
                    "gateway-probe", str(LIBRARY), str(cache), str(self.paths.node(node_version)),
                    str(self.paths.entry),
                ], limit=10)
                missing = self.run([
                    "/bin/bash", "-c", 'source "$1"; openclaw_gateway_missing_skills_from_status "${@:2}"',
                    "gateway-probe", str(LIBRARY), *REQUIRED_SKILLS,
                ], input_text=payload)
                if not missing.strip():
                    return
                failure = VerificationError("required_skills_unavailable")
            except VerificationError as error:
                if str(error) not in {"probe_failed", "probe_timeout"}:
                    raise
                failure = error
            if attempt == 9:
                raise failure
            self.wait_for_retry()

    def wait_for_retry(self):
        if self.deadline - time.monotonic() <= 1:
            raise VerificationError("verification_deadline")
        time.sleep(1)


@contextmanager
def receipt_lock(receipt):
    directory = receipt.parent
    for parent in (directory, directory.parent):
        metadata = parent.lstat()
        if (not stat.S_ISDIR(metadata.st_mode) or metadata.st_uid != os.getuid()
                or metadata.st_mode & 0o022):
            raise VerificationError("unsafe_receipt_directory")
    descriptor = os.open(str(directory / ".gateway-activation.lock"),
                         os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    with os.fdopen(descriptor, "w") as lock:
        protected_file(directory / ".gateway-activation.lock", 0o600)
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise VerificationError("activation_verification_busy") from None
        yield


def verify(paths, probe, expected_hash, node_version, package_version, previous_pid=None, record=False):
    def check_and_record():
        old_receipt = receipt_snapshot(paths.receipt)
        before = probe.snapshot(expected_hash, node_version, package_version, previous_pid)
        probe.check_health_and_skills(node_version)
        after = probe.snapshot(expected_hash, node_version, package_version, previous_pid)
        if before != after or receipt_snapshot(paths.receipt) != old_receipt:
            raise VerificationError("activation_evidence_changed")
        if record:
            descriptor, temporary = tempfile.mkstemp(prefix=".gateway-wrapper.sha256.", dir=paths.receipt.parent)
            try:
                with os.fdopen(descriptor, "w") as output:
                    output.write(expected_hash + "\n")
                    output.flush()
                    os.fsync(output.fileno())
                if probe.snapshot(expected_hash, node_version, package_version, previous_pid) != after:
                    raise VerificationError("activation_evidence_changed")
                if receipt_snapshot(paths.receipt) != old_receipt:
                    raise VerificationError("activation_evidence_changed")
                os.replace(temporary, paths.receipt)
            finally:
                if os.path.exists(temporary):
                    os.unlink(temporary)
        return {"ok": True, "pid": after["pid"], "nodeVersion": node_version,
                "packageVersion": package_version, "wrapperSha256": expected_hash,
                "receiptRecorded": record}

    if record:
        if previous_pid is None:
            raise VerificationError("previous_pid_required_for_record")
        with receipt_lock(paths.receipt):
            return check_and_record()
    return check_and_record()


def main():
    parser = argparse.ArgumentParser(description="Verify an already-started gateway; never start/restart/deploy it.")
    parser.add_argument("--expected-wrapper-sha256", required=True)
    parser.add_argument("--expected-node-version", required=True)
    parser.add_argument("--expected-package-version", required=True)
    parser.add_argument("--previous-pid", type=int)
    parser.add_argument("--record", action="store_true", help="Publish the receipt only after verification; requires an approved window.")
    arguments = parser.parse_args()
    if (not re.fullmatch(r"[0-9a-f]{64}", arguments.expected_wrapper_sha256)
            or not re.fullmatch(r"(?:22|24)\.\d+\.\d+", arguments.expected_node_version)
            or not re.fullmatch(r"\d{4}\.\d+\.\d+(?:-[\w.]+)?", arguments.expected_package_version)
            or (arguments.previous_pid is not None and arguments.previous_pid < 0)):
        parser.error("invalid pinned version, hash or previous PID")
    paths = Paths(Path.home())
    try:
        result = verify(paths, SystemProbe(paths), arguments.expected_wrapper_sha256,
                        arguments.expected_node_version, arguments.expected_package_version,
                        arguments.previous_pid, arguments.record)
    except (VerificationError, OSError, ValueError, TypeError) as error:
        code = str(error) if isinstance(error, VerificationError) else "verification_unavailable"
        print(json.dumps({"ok": False, "error": code}), file=sys.stderr)
        return 1
    print(json.dumps(result))
    return 0


if __name__ == "__main__":
    sys.exit(main())
