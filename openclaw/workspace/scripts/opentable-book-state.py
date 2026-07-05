#!/usr/bin/env python3
"""Protected, one-use approval state for opentable-book.sh."""

from __future__ import annotations

import fcntl
import json
import os
from pathlib import Path
import re
import secrets
import stat
import sys
import tempfile
import time


APPROVAL_ID_PATTERN = re.compile(r"[A-Za-z0-9_-]{24,128}")


def reply(payload: dict, code: int = 0) -> None:
    print(json.dumps(payload, separators=(",", ":")))
    raise SystemExit(code)


def fail(error_code: str, message: str, code: int = 3) -> None:
    reply({"ok": False, "error_code": error_code, "message": message}, code)


def prepare_cache(cache: Path, *, create: bool) -> int:
    created = False
    if create:
        if not cache.exists():
            try:
                cache.mkdir(mode=0o700, parents=True)
                created = True
            except FileExistsError:
                pass
    try:
        cache_stat = cache.lstat()
    except FileNotFoundError:
        fail("approval_not_found", "Approval state is unavailable")
    if not stat.S_ISDIR(cache_stat.st_mode) or stat.S_ISLNK(cache_stat.st_mode):
        fail("approval_store_unavailable", "Approval cache is not a protected directory")
    if cache_stat.st_uid != os.getuid():
        fail("approval_store_unavailable", "Approval cache has the wrong owner")
    if not created and cache_stat.st_mode & 0o077:
        fail("approval_store_unavailable", "Approval cache has unsafe permissions")
    os.chmod(cache, 0o700)

    lock_path = cache / ".lock"
    flags = os.O_CREAT | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0)
    try:
        lock_fd = os.open(lock_path, flags, 0o600)
    except OSError:
        fail("approval_store_unavailable", "Approval cache lock is unavailable")
    os.fchmod(lock_fd, 0o600)
    fcntl.flock(lock_fd, fcntl.LOCK_EX)
    return lock_fd


def atomic_write(cache: Path, path: Path, payload: dict) -> None:
    fd, temporary = tempfile.mkstemp(prefix=".approval-", dir=cache)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, separators=(",", ":"), sort_keys=True)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        directory_fd = os.open(cache, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass


def approval_path(cache: Path, approval_id: str) -> Path:
    if not APPROVAL_ID_PATTERN.fullmatch(approval_id):
        fail("invalid_approval_id", "Approval ID has an invalid format", 2)
    return cache / f"{approval_id}.json"


def load_state(path: Path) -> dict:
    try:
        path_stat = path.lstat()
    except FileNotFoundError:
        fail("approval_not_found", "Approval ID was not found")
    if not stat.S_ISREG(path_stat.st_mode) or stat.S_ISLNK(path_stat.st_mode):
        fail("approval_store_unavailable", "Approval state is not a protected file")
    if path_stat.st_uid != os.getuid() or path_stat.st_mode & 0o077:
        fail("approval_store_unavailable", "Approval state has unsafe permissions")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        fail("approval_state_invalid", "Approval state is invalid")
    if not isinstance(payload, dict) or payload.get("version") != 1:
        fail("approval_state_invalid", "Approval state is invalid")
    return payload


def create(cache: Path, args: list[str]) -> None:
    if len(args) != 3:
        fail("approval_state_invalid", "Approval creation arguments are invalid")
    try:
        ttl = int(args[0])
        request = json.loads(args[1])
        facts = json.loads(args[2])
    except (TypeError, ValueError, json.JSONDecodeError):
        fail("approval_state_invalid", "Approval creation arguments are invalid")
    if not isinstance(request, dict) or not isinstance(facts, dict):
        fail("approval_state_invalid", "Approval creation arguments are invalid")

    now = int(time.time())
    for _ in range(10):
        approval_id = secrets.token_urlsafe(24)
        path = approval_path(cache, approval_id)
        if not path.exists():
            break
    else:
        fail("approval_store_unavailable", "Could not allocate an approval ID")

    state = {
        "version": 1,
        "approval_id": approval_id,
        "status": "pending",
        "created_at": now,
        "expires_at": now + ttl,
        "mutation_attempted": False,
        "request": request,
        "facts": facts,
    }
    atomic_write(cache, path, state)
    reply({"ok": True, "approval_id": approval_id, "expires_at": now + ttl})


def update(cache: Path, action: str, approval_id: str) -> None:
    path = approval_path(cache, approval_id)
    state = load_state(path)

    if action == "claim":
        now = int(time.time())
        if int(state.get("expires_at", 0)) <= now:
            state["status"] = "expired"
            atomic_write(cache, path, state)
            fail("approval_expired", "Approval ID has expired")
        if state.get("status") != "pending":
            fail("approval_replayed", "Approval ID has already been used")
        request = state.get("request")
        facts = state.get("facts")
        if not isinstance(request, dict) or not isinstance(facts, dict):
            fail("approval_state_invalid", "Approval state is invalid")
        state["status"] = "consumed"
        state["consumed_at"] = now
        atomic_write(cache, path, state)
        reply(
            {
                "ok": True,
                "approval_id": approval_id,
                "expires_at": state["expires_at"],
                **request,
                **facts,
            }
        )

    if state.get("status") != "consumed":
        fail("approval_state_invalid", "Approval is not in a consumed state")
    if action == "mutation":
        state["mutation_attempted"] = True
        state["mutation_attempted_at"] = int(time.time())
    elif action == "confirmed":
        state["outcome"] = "confirmed"
        state["confirmed_at"] = int(time.time())
    else:
        fail("approval_state_invalid", "Unknown approval state action")
    atomic_write(cache, path, state)
    reply({"ok": True})


def main() -> None:
    if len(sys.argv) < 3:
        fail("approval_state_invalid", "Usage: opentable-book-state.py ACTION CACHE [ARGS]", 2)
    action = sys.argv[1]
    cache = Path(sys.argv[2])
    lock_fd = prepare_cache(cache, create=action == "create")
    try:
        if action == "create":
            create(cache, sys.argv[3:])
        if len(sys.argv) != 4:
            fail("approval_state_invalid", "Approval state arguments are invalid")
        update(cache, action, sys.argv[3])
    finally:
        os.close(lock_fd)


if __name__ == "__main__":
    main()
