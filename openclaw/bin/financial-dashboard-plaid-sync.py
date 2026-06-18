#!/usr/bin/env python3
"""Run the production Plaid sync from protected local caches only."""

from __future__ import annotations

from datetime import datetime, timezone
import fcntl
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile


HOME = Path("/Users/dbochman")
REPO_DIR = HOME / "repos" / "financial-dashboard"
PYTHON = REPO_DIR / "venv" / "bin" / "python3"
STATE_DIR = HOME / ".openclaw" / "financial-dashboard"
LOCK_PATH = STATE_DIR / ".plaid-sync.lock"
STATUS_PATH = STATE_DIR / "plaid-sync-status.json"


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def write_status(status: str, started_at: str, exit_code: int | None, reason: str | None = None) -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(STATE_DIR, 0o700)
    payload = {
        "version": 1,
        "status": status,
        "started_at": started_at,
        "finished_at": now_iso(),
        "exit_code": exit_code,
    }
    if reason:
        payload["reason"] = reason

    descriptor, temporary_path = tempfile.mkstemp(
        dir=STATE_DIR,
        prefix=".plaid-sync-status.",
        suffix=".tmp",
    )
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as status_file:
            json.dump(payload, status_file, sort_keys=True)
            status_file.write("\n")
            status_file.flush()
            os.fsync(status_file.fileno())
        os.replace(temporary_path, STATUS_PATH)
        os.chmod(STATUS_PATH, 0o600)
    except Exception:
        try:
            os.unlink(temporary_path)
        except FileNotFoundError:
            pass
        raise


def main() -> int:
    started_at = now_iso()
    STATE_DIR.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(STATE_DIR, 0o700)

    with LOCK_PATH.open("a+", encoding="utf-8") as lock_file:
        os.fchmod(lock_file.fileno(), 0o600)
        try:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            write_status("skipped", started_at, 0, "another_sync_is_running")
            print("Plaid sync skipped: another run is already active.", flush=True)
            return 0

        if not PYTHON.is_file():
            write_status("error", started_at, 1, "financial_dashboard_venv_missing")
            print("Plaid sync failed: financial-dashboard Python environment is missing.", flush=True)
            return 1

        environment = os.environ.copy()
        environment["HOME"] = str(HOME)
        environment["PLAID_ENV"] = "production"
        # update_data.py reads only the required values from the private cache; no op process runs here.
        environment["OPENCLAW_SECRETS_CACHE"] = str(HOME / ".openclaw" / ".secrets-cache")
        try:
            result = subprocess.run(
                [str(PYTHON), str(REPO_DIR / "update_data.py"), "sync"],
                cwd=REPO_DIR,
                env=environment,
                check=False,
            )
        except OSError:
            write_status("error", started_at, 1, "sync_command_unavailable")
            print("Plaid sync failed: the financial-dashboard command is unavailable.", flush=True)
            return 1
        if result.returncode == 0:
            write_status("ok", started_at, 0)
            print("Plaid sync completed.", flush=True)
            return 0

        write_status("error", started_at, result.returncode, "sync_command_failed")
        print("Plaid sync failed; inspect the protected LaunchAgent logs and dashboard source status.", flush=True)
        return result.returncode or 1


if __name__ == "__main__":
    sys.exit(main())
