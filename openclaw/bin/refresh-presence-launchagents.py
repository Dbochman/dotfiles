#!/usr/bin/env python3
"""Refresh installed presence jobs when their loaded configuration is stale."""

import argparse
import fcntl
import hashlib
import json
import os
import plistlib
import re
import subprocess
import sys
import tempfile
from pathlib import Path


LABELS = ("com.openclaw.presence-cabin", "com.openclaw.presence-receive",
          "com.openclaw.vacancy-actions")


def atomic_write(path, data, mode):
    descriptor, temporary = tempfile.mkstemp(prefix="." + path.name, dir=path.parent)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(data)
            os.fchmod(stream.fileno(), mode)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def launchctl(*arguments):
    return subprocess.run(["/bin/launchctl", *arguments], capture_output=True,
                          text=True, timeout=30)


def loaded_environment(output):
    match = re.search(r"^\tenvironment = \{\n(.*?)^\t\}", output, re.M | re.S)
    if not match:
        return {}
    return dict(line.strip().split(" => ", 1) for line in match[1].splitlines()
                if " => " in line)


def refresh(label, sources, installed, state, runner=launchctl, dry_run=False):
    destination = installed / (label + ".plist")
    if not destination.exists() and not destination.is_symlink():
        return "not_installed"
    if destination.is_symlink():
        raise ValueError("installed_plist_symlink")
    candidate = (sources / (label + ".plist")).read_bytes()
    definition = plistlib.loads(candidate)
    if definition.get("Label") != label:
        raise ValueError("label_mismatch")
    digest = hashlib.sha256(plistlib.dumps(definition, sort_keys=True)).hexdigest()
    receipt = state / (label + ".sha256")
    previous = destination.read_bytes()
    target = f"gui/{os.getuid()}/{label}"
    status = runner("print", target)
    if status.returncode:
        if status.returncode != 113:
            raise RuntimeError("launchctl_inspection_failed")
        if previous != candidate and not dry_run:
            atomic_write(destination, candidate, 0o644)
        # Keep intentionally stopped jobs stopped, and invalidate any old receipt.
        if not dry_run:
            receipt.unlink(missing_ok=True)
        return "preserved_unloaded"
    applied = receipt.read_text().strip() if receipt.is_file() else None
    environment = loaded_environment(status.stdout)
    drift = any(environment.get(key) != value for key, value
                in definition.get("EnvironmentVariables", {}).items())
    if previous == candidate and applied == digest and not drift:
        return "unchanged"
    # Never terminate an in-progress scan, receive, or device action. A pending
    # refresh has no receipt and is retried on the next deployment.
    if not re.search(r"^\tstate = not running$", status.stdout, re.M):
        return "deferred_active"
    if dry_run:
        return "would_refresh"
    if runner("bootout", target).returncode:
        raise RuntimeError("bootout_failed")
    try:
        atomic_write(destination, candidate, 0o644)
        if runner("bootstrap", f"gui/{os.getuid()}", str(destination)).returncode:
            raise RuntimeError("bootstrap_failed")
        verified = runner("print", target)
        actual = loaded_environment(verified.stdout)
        if verified.returncode or any(actual.get(k) != v for k, v
                                       in definition.get("EnvironmentVariables", {}).items()):
            raise RuntimeError("loaded_environment_mismatch")
    except Exception:
        # A failed refresh must not leave the service stopped or mark it applied.
        runner("bootout", target)
        atomic_write(destination, previous, 0o644)
        recovered = runner("bootstrap", f"gui/{os.getuid()}", str(destination))
        receipt.unlink(missing_ok=True)
        if recovered.returncode:
            raise RuntimeError("rollback_bootstrap_failed") from None
        raise
    atomic_write(receipt, (digest + "\n").encode(), 0o600)
    return "refreshed"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-dir", type=Path, default=Path.home()
                        / "dotfiles/openclaw/launchagents")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("labels", nargs="*")
    args = parser.parse_args()
    if any(label not in LABELS for label in args.labels):
        parser.error("only Cabin presence, presence-receive and vacancy-actions are supported")
    os.umask(0o077)
    state = Path.home() / ".openclaw/state/presence-launchagents"
    state.mkdir(parents=True, exist_ok=True, mode=0o700)
    failed = False
    with (state / ".lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            print(json.dumps({"job": "presence-launchagents", "outcome": "busy"}))
            return 75
        for label in args.labels or LABELS:
            try:
                outcome = refresh(label, args.source_dir,
                                  Path.home() / "Library/LaunchAgents", state,
                                  dry_run=args.dry_run)
            except (OSError, ValueError, RuntimeError, subprocess.TimeoutExpired) as exc:
                # No raw launchctl output or plist/environment contents in logs.
                outcome = str(exc) if isinstance(exc, RuntimeError) else "refresh_failed"
                failed = True
            print(json.dumps({"job": label, "outcome": outcome}), flush=True)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
