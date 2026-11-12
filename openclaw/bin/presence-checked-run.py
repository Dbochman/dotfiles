#!/usr/bin/env python3
"""Classify startup failures without changing the approved presence scanner."""

import argparse
import json
import os
import stat
import subprocess
import sys
from pathlib import Path


def node_command(environment):
    # Match the approved scanner, including Bash command -v finding a disabled
    # executable earlier in PATH. Do not silently select a different runtime.
    for name in ("/opt/homebrew/opt/node@22/bin/node", "/opt/homebrew/bin/node"):
        if os.access(name, os.X_OK):
            return name
    result = subprocess.run(
        ["/bin/bash", "-c", "command -v node"], env=environment,
        capture_output=True, text=True, timeout=5,
    )
    return result.stdout.strip() if result.returncode == 0 else None


def startup_reason(environment):
    node = node_command(environment)
    if not node or not Path(node).is_file():
        return "node_missing"
    if not os.access(node, os.X_OK):
        return "node_not_executable"
    try:
        result = subprocess.run(
            [node, "-e", "process.exit(0)"], env=environment,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=5,
        )
    except (OSError, subprocess.TimeoutExpired):
        return "node_start_failed"
    if result.returncode:
        return "node_start_failed"
    return None


def config_reason(environment):
    path = Path(environment.get(
        "PRESENCE_DEVICE_CONFIG", str(Path.home() / ".openclaw/presence-devices.json")
    ))
    try:
        parent, info = path.parent.lstat(), path.lstat()
        if (not stat.S_ISDIR(parent.st_mode) or parent.st_uid != os.getuid()
                or parent.st_mode & 0o077 or not stat.S_ISREG(info.st_mode)
                or info.st_uid != os.getuid() or info.st_nlink != 1
                or stat.S_IMODE(info.st_mode) != 0o600):
            return "config_permissions"
        if not os.access(path, os.R_OK):
            return "config_unreadable"
    except FileNotFoundError:
        return "config_missing"
    except OSError:
        return "config_unreadable"
    return None


def diagnostic(reason, mode, site):
    print(json.dumps({"job": "presence", "mode": mode, "site": site,
                      "reason": reason}), file=sys.stderr, flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scanner", type=Path, default=Path.home()
                        / ".openclaw/workspace/scripts/presence-detect.sh")
    parser.add_argument("mode", choices=("validate-config", "observe", "cabin"))
    parser.add_argument("site", nargs="?", choices=("cabin", "crosstown"))
    args = parser.parse_args()
    if (args.mode == "cabin" and args.site is not None
            or args.mode != "cabin" and args.site is None):
        parser.error("use cabin, or validate-config/observe <site>")
    site = args.site or "cabin"
    environment = dict(os.environ)
    if not args.scanner.is_file():
        diagnostic("scanner_missing", args.mode, site)
        return 78
    try:
        reason = startup_reason(environment)
    except (OSError, subprocess.TimeoutExpired):
        reason = "node_start_failed"
    reason = reason or config_reason(environment)
    if reason:
        diagnostic(reason, args.mode, site)
        return 78
    command = ["/bin/bash", str(args.scanner), args.mode]
    if args.site:
        command.append(args.site)
    try:
        # Stdout retains the approved scanner's existing contract. Raw stderr
        # is suppressed; diagnostics below contain only fixed categories.
        result = subprocess.run(command, env=environment, stderr=subprocess.DEVNULL)
    except OSError:
        diagnostic("scanner_start_failed", args.mode, site)
        return 70
    if result.returncode:
        if args.mode == "validate-config":
            reason = "config_invalid"
        else:
            # Validate only after failure; never run a second network scan.
            check = subprocess.run(
                ["/bin/bash", str(args.scanner), "validate-config", site],
                env=environment, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
            reason = "config_invalid" if check.returncode else "scanner_failed"
        diagnostic(reason, args.mode, site)
        return result.returncode if result.returncode > 0 else 128 - result.returncode
    return 0


if __name__ == "__main__":
    sys.exit(main())
