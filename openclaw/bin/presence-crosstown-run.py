#!/usr/bin/env python3
"""Bound the existing approved Crosstown scanner and all of its child processes.

This launcher never interprets or rewrites presence. A failed/timed-out scan
leaves the scanner's existing atomic-write and sticky-occupancy rules intact.
"""

import json
import os
import signal
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path


TIMEOUT_SECONDS = 60
TERM_GRACE_SECONDS = 3


class Interrupted(Exception):
    def __init__(self, signum):
        self.signum = signum


def signal_group(process, signum):
    try:
        os.killpg(process.pid, signum)
    except ProcessLookupError:
        pass


def stop_group(process, grace_seconds):
    signal_group(process, signal.SIGTERM)
    try:
        process.wait(timeout=grace_seconds)
    except subprocess.TimeoutExpired:
        pass
    finally:
        # The shell may exit on TERM while a ping/transfer ignores it.
        signal_group(process, signal.SIGKILL)
    try:
        process.wait(timeout=1)
    except subprocess.TimeoutExpired:
        pass


def run_bounded(command, timeout_seconds=TIMEOUT_SECONDS,
                grace_seconds=TERM_GRACE_SECONDS):
    process = subprocess.Popen(
        command, start_new_session=True, stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    try:
        code = process.wait(timeout=timeout_seconds)
    except subprocess.TimeoutExpired:
        stop_group(process, grace_seconds)
        return 124, "deadline_exceeded"
    except BaseException:
        stop_group(process, grace_seconds)
        raise
    else:
        # Do not leave an orphan from an early shell exit in this owned group.
        signal_group(process, signal.SIGKILL)
        return (code, "completed" if code == 0 else "scanner_failed")


def main():
    if len(sys.argv) != 1:
        return 64
    os.umask(0o077)
    scanner = Path.home() / ".openclaw/workspace/scripts/presence-detect.sh"

    def interrupted(signum, _frame):
        # Cleanup gets its bounded TERM grace even if another signal arrives.
        signal.signal(signal.SIGTERM, signal.SIG_IGN)
        signal.signal(signal.SIGINT, signal.SIG_IGN)
        raise Interrupted(signum)

    signal.signal(signal.SIGTERM, interrupted)
    signal.signal(signal.SIGINT, interrupted)
    started = time.monotonic()
    try:
        code, outcome = run_bounded(["/bin/bash", str(scanner), "crosstown"])
    except Interrupted as exc:
        code, outcome = 128 + exc.signum, "interrupted"
    except OSError:
        code, outcome = 1, "launch_failed"
    # Child output can contain household data; log only fixed operational facts.
    print(json.dumps({
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "job": "presence-crosstown", "outcome": outcome,
        "exit_code": code, "elapsed_seconds": round(time.monotonic() - started, 3),
        "deadline_seconds": TIMEOUT_SECONDS,
    }), file=sys.stderr, flush=True)
    return code if code >= 0 else 128 - code


if __name__ == "__main__":
    sys.exit(main())
