#!/usr/bin/env python3
"""Capture one aggregate forecast observation after the daily source refreshes."""

from __future__ import annotations

from datetime import datetime, timezone
import fcntl
import json
import os
from pathlib import Path
import sys
import tempfile
import time
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


HOME = Path(os.environ.get("FORECAST_LEDGER_HOME", "/Users/dbochman")).expanduser()
STATE_DIR = HOME / ".openclaw" / "forecast-dashboard"
LOCK_PATH = STATE_DIR / ".forecast-ledger-capture.lock"
STATUS_PATH = STATE_DIR / "forecast-ledger-capture-status.json"
LEDGER_ENDPOINT = os.environ.get(
    "FORECAST_LEDGER_ENDPOINT",
    "http://127.0.0.1:8586/api/forecast-ledger/observations",
)
REQUEST_TIMEOUT_SECONDS = 30
MAX_ATTEMPTS = 3
RETRY_DELAY_SECONDS = 10


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def write_status(
    status: str,
    started_at: str,
    exit_code: int,
    reason: str | None = None,
    observation: dict | None = None,
) -> None:
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
    if observation:
        payload["observation_id"] = observation.get("id")
        payload["observation_date"] = observation.get("observation_date")
        payload["created"] = bool(observation.get("created"))

    descriptor, temporary_path = tempfile.mkstemp(
        dir=STATE_DIR,
        prefix=".forecast-ledger-capture-status.",
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


def capture_once() -> dict:
    body = json.dumps({"capture_kind": "scheduled"}).encode("utf-8")
    request = Request(
        LEDGER_ENDPOINT,
        data=body,
        headers={"Content-Type": "application/json", "User-Agent": "forecast-ledger-capture/1.0"},
        method="POST",
    )
    with urlopen(request, timeout=REQUEST_TIMEOUT_SECONDS) as response:
        payload = json.loads(response.read().decode("utf-8"))
    if payload.get("status") != "ok" or not isinstance(payload.get("observation"), dict):
        raise RuntimeError("ledger_response_invalid")
    return {
        "id": payload["observation"].get("id"),
        "observation_date": payload["observation"].get("observation_date"),
        "created": bool(payload.get("created")),
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
            write_status("skipped", started_at, 0, "another_capture_is_running")
            print("Forecast ledger capture skipped: another run is already active.", flush=True)
            return 0

        for attempt in range(1, MAX_ATTEMPTS + 1):
            try:
                observation = capture_once()
            except (HTTPError, URLError, OSError, ValueError, RuntimeError):
                if attempt < MAX_ATTEMPTS:
                    time.sleep(RETRY_DELAY_SECONDS)
                    continue
                write_status("error", started_at, 1, "forecast_ledger_api_unavailable")
                print("Forecast ledger capture failed: forecast_ledger_api_unavailable.", flush=True)
                return 1

            reason = None if observation["created"] else "unchanged_source_snapshot"
            write_status("ok", started_at, 0, reason, observation)
            outcome = "recorded" if observation["created"] else "unchanged"
            print(
                f"Forecast ledger capture {outcome}: {observation['observation_date']} (id {observation['id']}).",
                flush=True,
            )
            return 0

    return 1


if __name__ == "__main__":
    sys.exit(main())
