#!/usr/bin/env python3
"""Refresh due licensed property values from protected local credentials."""

from __future__ import annotations

from datetime import datetime, timezone
import fcntl
import json
import os
from pathlib import Path
import shlex
import stat
import subprocess
import sys
import tempfile


HOME = Path(os.environ.get("FINANCE_REFRESH_HOME", "/Users/dbochman"))
REPO_DIR = HOME / "repos" / "financial-dashboard"
PYTHON = REPO_DIR / "venv" / "bin" / "python3"
SECRETS_CACHE = HOME / ".openclaw" / ".secrets-cache"
STATE_DIR = HOME / ".openclaw" / "financial-dashboard"
LOCK_PATH = STATE_DIR / ".property-value-sync.lock"
STATUS_PATH = STATE_DIR / "property-value-sync-status.json"
SAFE_METRICS = (
    "configured",
    "missing_address",
    "due",
    "current",
    "refreshed",
    "failed",
)


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def write_status(
    status: str,
    started_at: str,
    exit_code: int | None,
    reason: str | None = None,
    metrics: dict[str, int] | None = None,
) -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(STATE_DIR, 0o700)
    payload: dict[str, object] = {
        "version": 1,
        "status": status,
        "started_at": started_at,
        "finished_at": now_iso(),
        "exit_code": exit_code,
    }
    if reason:
        payload["reason"] = reason
    if metrics:
        payload["metrics"] = {key: int(metrics[key]) for key in SAFE_METRICS if key in metrics}
        payload["refresh_performed"] = int(metrics.get("refreshed", 0)) > 0

    descriptor, temporary_path = tempfile.mkstemp(
        dir=STATE_DIR,
        prefix=".property-value-sync-status.",
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


def read_cached_secret(name: str) -> str:
    file_stat = SECRETS_CACHE.stat()
    if stat.S_IMODE(file_stat.st_mode) & 0o077:
        raise PermissionError("secrets cache permissions are too broad")
    for raw_line in SECRETS_CACHE.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        try:
            fields = shlex.split(line, posix=True)
        except ValueError:
            continue
        if len(fields) != 1 or "=" not in fields[0]:
            continue
        key, value = fields[0].split("=", 1)
        if key == name:
            return value.strip()
    return ""


def parse_metrics(output: str) -> dict[str, int]:
    lines = [line for line in output.splitlines() if line.strip()]
    if not lines:
        raise ValueError("missing command output")
    payload = json.loads(lines[-1])
    if not isinstance(payload, dict):
        raise ValueError("invalid command output")
    metrics = {}
    for key in SAFE_METRICS:
        value = payload.get(key)
        if not isinstance(value, int) or isinstance(value, bool) or value < 0:
            raise ValueError("invalid command metrics")
        metrics[key] = value
    return metrics


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
            print("Property value sync skipped: another run is already active.", flush=True)
            return 0

        if not PYTHON.is_file():
            write_status("error", started_at, 1, "financial_dashboard_venv_missing")
            print("Property value sync failed: financial-dashboard Python environment is missing.", flush=True)
            return 1

        try:
            api_key = read_cached_secret("RENTCAST_API_KEY")
        except (OSError, PermissionError):
            write_status("error", started_at, 1, "protected_secret_cache_unavailable")
            print("Property value sync failed: protected secret cache is unavailable.", flush=True)
            return 1
        if not api_key:
            write_status("error", started_at, 1, "rentcast_api_key_missing")
            print("Property value sync failed: RentCast API key is not cached.", flush=True)
            return 1

        environment = os.environ.copy()
        environment["HOME"] = str(HOME)
        environment["RENTCAST_API_KEY"] = api_key
        try:
            result = subprocess.run(
                [
                    str(PYTHON),
                    str(REPO_DIR / "update_data.py"),
                    "refresh-property-values",
                    "--json",
                ],
                cwd=REPO_DIR,
                env=environment,
                check=False,
                capture_output=True,
                text=True,
            )
        except OSError:
            write_status("error", started_at, 1, "sync_command_unavailable")
            print("Property value sync failed: command unavailable.", flush=True)
            return 1

        try:
            metrics = parse_metrics(result.stdout)
        except (ValueError, json.JSONDecodeError):
            metrics = None
        if result.returncode == 0 and metrics is not None:
            write_status("ok", started_at, 0, metrics=metrics)
            print(
                "Property value sync completed: "
                f"{metrics['refreshed']} refreshed, {metrics['current']} current.",
                flush=True,
            )
            return 0

        write_status("error", started_at, result.returncode or 1, "sync_command_failed", metrics)
        print("Property value sync failed; last-known-good values were preserved.", flush=True)
        return result.returncode or 1


if __name__ == "__main__":
    sys.exit(main())
