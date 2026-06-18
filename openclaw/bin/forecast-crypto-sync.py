#!/usr/bin/env python3
"""Refresh the forecast dashboard crypto cache from protected local credentials."""

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
REPO_DIR = HOME / "repos" / "Financial Advisor"
STATE_DIR = HOME / ".openclaw" / "forecast-dashboard"
SECRETS_DIR = STATE_DIR / "crypto-secrets"
VENV_DIR = STATE_DIR / "crypto-sync-venv"
PYTHON = VENV_DIR / "bin" / "python"
SYNC_SCRIPT = REPO_DIR / "dashboard" / "sync_crypto_holdings.py"
COINBASE_KEY = SECRETS_DIR / "coinbase-cdp-api-key.json"
ETHERSCAN_ENV = SECRETS_DIR / "etherscan.env"
MANUAL_VALUES = STATE_DIR / "crypto-manual-values.json"
LOCK_PATH = STATE_DIR / ".crypto-sync.lock"
STATUS_PATH = STATE_DIR / "crypto-sync-status.json"


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def write_status(status: str, started_at: str, exit_code: int, reason: str | None = None) -> None:
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
        prefix=".crypto-sync-status.",
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
            print("Forecast crypto sync skipped: another run is already active.", flush=True)
            return 0

        required_paths = {
            "crypto_sync_venv_missing": PYTHON,
            "forecast_crypto_sync_script_missing": SYNC_SCRIPT,
            "coinbase_key_missing": COINBASE_KEY,
            "etherscan_env_missing": ETHERSCAN_ENV,
            "manual_crypto_values_missing": MANUAL_VALUES,
        }
        for reason, path in required_paths.items():
            if not path.is_file():
                write_status("error", started_at, 1, reason)
                print(f"Forecast crypto sync failed: {reason}.", flush=True)
                return 1

        environment = os.environ.copy()
        environment["HOME"] = str(HOME)
        try:
            result = subprocess.run(
                [
                    str(PYTHON),
                    str(SYNC_SCRIPT),
                    "--coinbase-key",
                    str(COINBASE_KEY),
                    "--etherscan-env",
                    str(ETHERSCAN_ENV),
                    "--manual-values",
                    str(MANUAL_VALUES),
                ],
                cwd=REPO_DIR,
                env=environment,
                check=False,
            )
        except OSError:
            write_status("error", started_at, 1, "sync_command_unavailable")
            print("Forecast crypto sync failed: command unavailable.", flush=True)
            return 1

        if result.returncode == 0:
            write_status("ok", started_at, 0)
            print("Forecast crypto sync completed.", flush=True)
            return 0

        write_status("error", started_at, result.returncode or 1, "sync_command_failed")
        print("Forecast crypto sync failed; inspect protected logs and status.", flush=True)
        return result.returncode or 1


if __name__ == "__main__":
    sys.exit(main())
