#!/usr/bin/env python3
"""Refresh automated household finance sources before morning briefings."""

from __future__ import annotations

from datetime import datetime, timezone
import fcntl
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time


HOME = Path(os.environ.get("FINANCE_REFRESH_HOME", "/Users/dbochman"))
DOTFILES_DIR = HOME / "dotfiles"
STATE_DIR = HOME / ".openclaw" / "finance-refresh"
LOCK_PATH = STATE_DIR / ".refresh.lock"
STATUS_PATH = STATE_DIR / "status.json"
RETRY_DELAY_SECONDS = float(
    os.environ.get("FINANCE_REFRESH_RETRY_DELAY_SECONDS", "15")
)
COMPONENT_TIMEOUT_SECONDS = int(
    os.environ.get("FINANCE_REFRESH_COMPONENT_TIMEOUT_SECONDS", "600")
)

COMPONENTS = (
    {
        "name": "plaid",
        "script": DOTFILES_DIR / "openclaw" / "bin" / "financial-dashboard-plaid-sync.py",
        "status": HOME / ".openclaw" / "financial-dashboard" / "plaid-sync-status.json",
    },
    {
        "name": "crypto",
        "script": DOTFILES_DIR / "openclaw" / "bin" / "forecast-crypto-sync.py",
        "status": HOME / ".openclaw" / "forecast-dashboard" / "crypto-sync-status.json",
    },
)


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def write_status(
    status: str,
    started_at: str,
    exit_code: int,
    components: dict[str, dict[str, object]],
    reason: str | None = None,
) -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(STATE_DIR, 0o700)
    payload: dict[str, object] = {
        "version": 1,
        "status": status,
        "started_at": started_at,
        "finished_at": now_iso(),
        "exit_code": exit_code,
        "components": components,
    }
    if reason:
        payload["reason"] = reason

    descriptor, temporary_path = tempfile.mkstemp(
        dir=STATE_DIR,
        prefix=".status.",
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


def status_mtime(path: Path) -> int | None:
    try:
        return path.stat().st_mtime_ns
    except FileNotFoundError:
        return None


def load_component_status(path: Path) -> dict[str, object]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def run_component(component: dict[str, object]) -> dict[str, object]:
    name = str(component["name"])
    script = Path(component["script"])
    status_path = Path(component["status"])
    if not script.is_file():
        return {
            "status": "error",
            "exit_code": 1,
            "attempts": 0,
            "reason": "component_script_missing",
        }

    last_exit_code = 1
    last_reason = "component_failed"
    for attempt in range(1, 3):
        previous_mtime = status_mtime(status_path)
        result: subprocess.CompletedProcess[bytes] | None = None
        print(f"Finance refresh: starting {name} attempt {attempt}.", flush=True)
        try:
            result = subprocess.run(
                ["/usr/bin/python3", str(script)],
                cwd=DOTFILES_DIR,
                check=False,
                timeout=COMPONENT_TIMEOUT_SECONDS,
            )
            last_exit_code = result.returncode
            last_reason = "component_failed"
        except subprocess.TimeoutExpired:
            last_exit_code = 124
            last_reason = "component_timeout"
        except OSError:
            last_exit_code = 1
            last_reason = "component_command_unavailable"

        current_mtime = status_mtime(status_path)
        child_status = load_component_status(status_path) if current_mtime != previous_mtime else {}
        child_result = child_status.get("status")
        if child_result == "ok" and result is not None and result.returncode == 0:
            print(f"Finance refresh: {name} completed.", flush=True)
            return {"status": "ok", "exit_code": 0, "attempts": attempt}

        if child_result in {"error", "skipped"}:
            last_reason = str(child_status.get("reason") or f"component_{child_result}")
            last_exit_code = int(child_status.get("exit_code") or last_exit_code or 1)
        elif not child_status and last_reason == "component_failed":
            last_reason = "component_status_missing"

        if attempt == 1:
            print(
                f"Finance refresh: {name} failed ({last_reason}); retrying once.",
                flush=True,
            )
            time.sleep(RETRY_DELAY_SECONDS)

    print(f"Finance refresh: {name} failed ({last_reason}).", flush=True)
    return {
        "status": "error",
        "exit_code": last_exit_code,
        "attempts": 2,
        "reason": last_reason,
    }


def main() -> int:
    started_at = now_iso()
    STATE_DIR.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(STATE_DIR, 0o700)

    with LOCK_PATH.open("a+", encoding="utf-8") as lock_file:
        os.fchmod(lock_file.fileno(), 0o600)
        try:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            write_status("skipped", started_at, 0, {}, "another_refresh_is_running")
            print("Finance refresh skipped: another run is already active.", flush=True)
            return 0

        results = {
            str(component["name"]): run_component(component)
            for component in COMPONENTS
        }
        successful = sum(result["status"] == "ok" for result in results.values())
        if successful == len(results):
            status = "ok"
            exit_code = 0
        elif successful:
            status = "partial"
            exit_code = 1
        else:
            status = "error"
            exit_code = 1

        write_status(status, started_at, exit_code, results)
        print(f"Finance refresh completed with status: {status}.", flush=True)
        return exit_code


if __name__ == "__main__":
    sys.exit(main())
