#!/usr/bin/env python3
"""Protected one-use approval state for the local August unlock boundary."""

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
LOCK_ID_PATTERN = re.compile(r"[A-Fa-f0-9]{32}")


def reply(payload: dict, code: int = 0) -> None:
    print(json.dumps(payload, separators=(",", ":"), sort_keys=True))
    raise SystemExit(code)


def fail(error_code: str, message: str, code: int = 3) -> None:
    reply({"success": False, "error_code": error_code, "message": message}, code)


def prepare_cache(cache: Path, *, create: bool) -> int:
    created = False
    if create and not cache.exists():
        try:
            cache.mkdir(mode=0o700, parents=True)
            created = True
        except FileExistsError:
            pass
    try:
        cache_stat = cache.lstat()
    except FileNotFoundError:
        fail("approval_not_found", "August unlock approval state is unavailable")
    if (
        not stat.S_ISDIR(cache_stat.st_mode)
        or stat.S_ISLNK(cache_stat.st_mode)
        or cache_stat.st_uid != os.getuid()
    ):
        fail("approval_store_unsafe", "August unlock approval cache is unsafe")
    if created:
        os.chmod(cache, 0o700)
    elif stat.S_IMODE(cache_stat.st_mode) != 0o700:
        fail("approval_store_unsafe", "August unlock approval cache is unsafe")

    lock_path = cache / ".lock"
    flags = os.O_CREAT | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0)
    try:
        lock_fd = os.open(lock_path, flags, 0o600)
        lock_stat = os.fstat(lock_fd)
    except OSError:
        fail("approval_store_unsafe", "August unlock approval lock is unsafe")
    if (
        not stat.S_ISREG(lock_stat.st_mode)
        or lock_stat.st_uid != os.getuid()
        or stat.S_IMODE(lock_stat.st_mode) != 0o600
    ):
        os.close(lock_fd)
        fail("approval_store_unsafe", "August unlock approval lock is unsafe")
    fcntl.flock(lock_fd, fcntl.LOCK_EX)
    return lock_fd


def atomic_write(cache: Path, path: Path, payload: dict) -> None:
    try:
        descriptor, temporary = tempfile.mkstemp(
            prefix=".august-approval-", dir=cache
        )
    except OSError:
        fail("approval_store_unavailable", "August unlock approval state could not be saved")
    try:
        try:
            os.fchmod(descriptor, 0o600)
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                descriptor = -1
                json.dump(payload, handle, separators=(",", ":"), sort_keys=True)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, path)
            directory_fd = os.open(cache, os.O_RDONLY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        except OSError:
            fail(
                "approval_store_unavailable",
                "August unlock approval state could not be saved",
            )
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        try:
            os.unlink(temporary)
        except OSError:
            pass


def approval_path(cache: Path, approval_id: str) -> Path:
    if not APPROVAL_ID_PATTERN.fullmatch(approval_id):
        fail("invalid_approval_id", "August unlock approval ID has an invalid format", 2)
    return cache / f"{approval_id}.json"


def load_state(path: Path) -> dict:
    try:
        before = path.lstat()
    except FileNotFoundError:
        fail("approval_not_found", "August unlock approval ID was not found")
    if (
        not stat.S_ISREG(before.st_mode)
        or stat.S_ISLNK(before.st_mode)
        or before.st_uid != os.getuid()
        or stat.S_IMODE(before.st_mode) != 0o600
    ):
        fail("approval_state_unsafe", "August unlock approval state is unsafe")

    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError:
        fail("approval_state_unsafe", "August unlock approval state is unsafe")
    try:
        opened = os.fstat(descriptor)
        if (
            not stat.S_ISREG(opened.st_mode)
            or opened.st_uid != os.getuid()
            or stat.S_IMODE(opened.st_mode) != 0o600
            or (opened.st_dev, opened.st_ino) != (before.st_dev, before.st_ino)
        ):
            fail("approval_state_unsafe", "August unlock approval state is unsafe")
        handle = os.fdopen(descriptor, "r", encoding="utf-8")
        descriptor = -1
        with handle:
            payload = json.load(handle)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        fail("approval_state_invalid", "August unlock approval state is invalid")
    finally:
        if descriptor >= 0:
            os.close(descriptor)
    if not isinstance(payload, dict) or payload.get("version") != 1:
        fail("approval_state_invalid", "August unlock approval state is invalid")
    return payload


def validate_claim_payload(claim_raw: str) -> dict:
    try:
        claim_payload = json.loads(claim_raw)
    except json.JSONDecodeError:
        fail("approval_state_invalid", "August unlock approval claim is invalid")
    if (
        not isinstance(claim_payload, dict)
        or claim_payload.get("success") is not True
        or not isinstance(claim_payload.get("approval_id"), str)
        or not APPROVAL_ID_PATTERN.fullmatch(claim_payload["approval_id"])
        or not isinstance(claim_payload.get("lock_id"), str)
        or not LOCK_ID_PATTERN.fullmatch(claim_payload["lock_id"])
        or claim_payload.get("observed_lock_state") != "locked"
        or claim_payload.get("observed_door_state") not in {"closed", "open"}
    ):
        fail("approval_state_invalid", "August unlock approval claim is invalid")
    return claim_payload


def resolve_exclusive(
    canonical: object,
    canonical_values: dict[str, str],
    nested: dict,
    first: str,
    second: str,
) -> str | None:
    candidates = {first, second}
    if canonical not in (None, ""):
        if not isinstance(canonical, str) or canonical not in canonical_values:
            return None
        candidates = {canonical_values[canonical]}
    for key in (first, second):
        if key not in nested:
            continue
        if type(nested[key]) is not bool:
            return None
        if nested[key]:
            candidates.intersection_update({key})
        else:
            candidates.discard(key)
    return next(iter(candidates)) if len(candidates) == 1 else None


def normalize_status(payload: object) -> dict[str, str]:
    if not isinstance(payload, dict):
        fail("status_invalid", "August returned an invalid physical state")
    nested = payload.get("state", {})
    if not isinstance(nested, dict):
        fail("status_invalid", "August returned an invalid physical state")
    lock_id = payload.get("lockID")
    if not isinstance(lock_id, str) or not LOCK_ID_PATTERN.fullmatch(lock_id):
        fail("status_invalid", "August returned an invalid lock identity")
    lock_state = resolve_exclusive(
        payload.get("status"),
        {
            "kAugLockState_Locked": "locked",
            "kAugLockState_Unlocked": "unlocked",
        },
        nested,
        "locked",
        "unlocked",
    )
    door_state = resolve_exclusive(
        payload.get("doorState"),
        {
            "kAugDoorState_Closed": "closed",
            "kAugDoorState_Open": "open",
        },
        nested,
        "closed",
        "open",
    )
    if lock_state is None or door_state is None:
        fail("status_invalid", "August returned an ambiguous physical state")
    return {
        "lock_id": lock_id,
        "lock_state": lock_state,
        "door_state": door_state,
    }


def read_stdin_object() -> dict:
    try:
        payload = json.load(sys.stdin)
    except (UnicodeDecodeError, json.JSONDecodeError):
        fail("status_invalid", "August returned invalid status data")
    if not isinstance(payload, dict):
        fail("status_invalid", "August returned invalid status data")
    return payload


def create(cache: Path, ttl_raw: str, requested_lock_id: str) -> None:
    try:
        ttl = int(ttl_raw)
    except ValueError:
        fail("approval_ttl_invalid", "August unlock approval TTL is invalid", 2)
    if not 1 <= ttl <= 600:
        fail("approval_ttl_invalid", "August unlock approval TTL must be 1-600 seconds", 2)
    if requested_lock_id and not LOCK_ID_PATTERN.fullmatch(requested_lock_id):
        fail("invalid_lock_id", "Lock ID must be exactly 32 hexadecimal characters", 2)

    observed = normalize_status(read_stdin_object())
    if requested_lock_id and observed["lock_id"].casefold() != requested_lock_id.casefold():
        fail("lock_identity_mismatch", "Observed August lock did not match the requested lock")
    if observed["lock_state"] != "locked":
        fail("already_unlocked", "August lock is already unlocked")

    now = int(time.time())
    for _ in range(10):
        approval_id = secrets.token_urlsafe(24)
        path = approval_path(cache, approval_id)
        if not path.exists():
            break
    else:
        fail("approval_store_unavailable", "Could not allocate an August unlock approval")

    state = {
        "version": 1,
        "approval_id": approval_id,
        "status": "pending",
        "created_at": now,
        "expires_at": now + ttl,
        **observed,
    }
    atomic_write(cache, path, state)
    reply(
        {
            "success": True,
            "mode": "preview",
            "status": "ready_to_confirm",
            "approval_id": approval_id,
            "approval_expires_at": now + ttl,
            "lock_id": observed["lock_id"],
            "observed_lock_state": observed["lock_state"],
            "observed_door_state": observed["door_state"],
        }
    )


def claim(cache: Path, approval_id: str) -> None:
    path = approval_path(cache, approval_id)
    state = load_state(path)
    created_at = state.get("created_at")
    expires_at = state.get("expires_at")
    if (
        not isinstance(state.get("approval_id"), str)
        or state["approval_id"] != approval_id
        or state.get("status") not in {"pending", "consumed", "expired"}
        or type(created_at) is not int
        or type(expires_at) is not int
        or not 1 <= expires_at - created_at <= 600
        or not isinstance(state.get("lock_id"), str)
        or not LOCK_ID_PATTERN.fullmatch(state["lock_id"])
        or state.get("lock_state") != "locked"
        or state.get("door_state") not in {"closed", "open"}
    ):
        fail("approval_state_invalid", "August unlock approval state is invalid")
    if state["status"] == "consumed" and type(state.get("consumed_at")) is not int:
        fail("approval_state_invalid", "August unlock approval state is invalid")

    if state["status"] != "pending":
        fail("approval_replayed", "August unlock approval ID has already been used")
    now = int(time.time())
    if expires_at <= now:
        state["status"] = "expired"
        atomic_write(cache, path, state)
        fail("approval_expired", "August unlock approval ID has expired")
    state["status"] = "consumed"
    state["consumed_at"] = now
    atomic_write(cache, path, state)
    reply(
        {
            "success": True,
            "approval_id": approval_id,
            "lock_id": state["lock_id"],
            "observed_lock_state": state["lock_state"],
            "observed_door_state": state["door_state"],
        }
    )


def verify_bound_observation(claim_raw: str) -> None:
    claim_payload = validate_claim_payload(claim_raw)
    current = normalize_status(read_stdin_object())
    expected = {
        "lock_id": claim_payload.get("lock_id"),
        "lock_state": claim_payload.get("observed_lock_state"),
        "door_state": claim_payload.get("observed_door_state"),
    }
    if current["lock_id"].casefold() != str(expected["lock_id"]).casefold() or any(
        current[key] != expected[key] for key in ("lock_state", "door_state")
    ):
        fail(
            "approval_facts_changed",
            "August lock or door state changed after preview; request a new approval",
        )
    reply({"success": True, **current})


def validate_unlock_result(claim_raw: str) -> None:
    claim_payload = validate_claim_payload(claim_raw)
    result = read_stdin_object()
    normalized = normalize_status(result)
    if (
        result.get("ok") is not True
        or result.get("action") != "unlock"
        or result.get("verified") is not True
        or normalized["lock_id"].casefold()
        != str(claim_payload.get("lock_id", "")).casefold()
        or normalized["lock_state"] != "unlocked"
    ):
        fail("unlock_outcome_unknown", "August unlock outcome is unknown")
    reply(
        {
            "success": True,
            "mode": "commit",
            "status": "confirmed",
            "approval_id": claim_payload.get("approval_id"),
            "lock_id": normalized["lock_id"],
            "lock_state": normalized["lock_state"],
            "door_state": normalized["door_state"],
        }
    )


def main() -> None:
    if len(sys.argv) < 3:
        fail("approval_state_invalid", "August approval helper arguments are invalid", 2)
    action = sys.argv[1]
    cache = Path(sys.argv[2])
    lock_fd = prepare_cache(cache, create=action == "create")
    try:
        if action == "create" and len(sys.argv) == 5:
            create(cache, sys.argv[3], sys.argv[4])
        if action == "claim" and len(sys.argv) == 4:
            claim(cache, sys.argv[3])
        if action == "verify" and len(sys.argv) == 4:
            verify_bound_observation(sys.argv[3])
        if action == "validate-result" and len(sys.argv) == 4:
            validate_unlock_result(sys.argv[3])
        fail("approval_state_invalid", "August approval helper arguments are invalid", 2)
    finally:
        os.close(lock_fd)


if __name__ == "__main__":
    main()
