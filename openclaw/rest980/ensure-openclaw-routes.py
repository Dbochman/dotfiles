#!/usr/bin/env python3
"""Install the bounded OpenClaw action route into the local rest980 app."""

from __future__ import annotations

import fcntl
import os
from pathlib import Path
import stat
import tempfile


API_FILE = Path(
    os.environ.get(
        "REST980_API_ROUTE_FILE",
        str(Path.home() / ".openclaw/rest980-app/routes/api.js"),
    )
).expanduser()
LOCK_FILE = Path(
    os.environ.get(
        "REST980_ROUTE_PATCH_LOCK",
        str(Path.home() / ".openclaw/rest980/.route-patch.lock"),
    )
).expanduser()
MAX_API_BYTES = 1024 * 1024
RESUME_ROUTE = "router.get('/local/action/resume', map2dorita('local', 'resume'));"
FIND_ROUTE = "router.get('/local/action/find', map2dorita('local', 'find'));"


def validate_api_file(path: Path) -> os.stat_result:
    metadata = path.lstat()
    if (
        stat.S_ISLNK(metadata.st_mode)
        or not stat.S_ISREG(metadata.st_mode)
        or metadata.st_uid != os.geteuid()
        or metadata.st_nlink != 1
        or metadata.st_size <= 0
        or metadata.st_size > MAX_API_BYTES
    ):
        raise RuntimeError("rest980_api_file_unsafe")
    return metadata


def install_route() -> None:
    LOCK_FILE.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    flags = os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(LOCK_FILE, flags, 0o600)
    try:
        lock_metadata = os.fstat(descriptor)
        if (
            not stat.S_ISREG(lock_metadata.st_mode)
            or lock_metadata.st_uid != os.geteuid()
            or stat.S_IMODE(lock_metadata.st_mode) != 0o600
        ):
            raise RuntimeError("rest980_route_lock_unsafe")
        fcntl.flock(descriptor, fcntl.LOCK_EX)

        metadata = validate_api_file(API_FILE)
        text = API_FILE.read_text(encoding="utf-8")
        if text.count(FIND_ROUTE) == 1:
            return
        if "/local/action/find" in text or text.count(RESUME_ROUTE) != 1:
            raise RuntimeError("rest980_api_contract_unexpected")
        updated = text.replace(RESUME_ROUTE, f"{RESUME_ROUTE}\n{FIND_ROUTE}")

        temporary_name: str | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                dir=API_FILE.parent,
                prefix=".api.js.openclaw.",
                delete=False,
            ) as temporary:
                temporary_name = temporary.name
                temporary.write(updated)
                temporary.flush()
                os.fsync(temporary.fileno())
            os.chmod(temporary_name, stat.S_IMODE(metadata.st_mode))
            os.replace(temporary_name, API_FILE)
            temporary_name = None
            parent_descriptor = os.open(API_FILE.parent, os.O_RDONLY)
            try:
                os.fsync(parent_descriptor)
            finally:
                os.close(parent_descriptor)
        finally:
            if temporary_name is not None:
                try:
                    os.unlink(temporary_name)
                except FileNotFoundError:
                    pass
    finally:
        os.close(descriptor)


if __name__ == "__main__":
    os.umask(0o077)
    install_route()
