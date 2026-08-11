#!/usr/bin/env python3
"""Move one Reachy JPEG through the trusted relay into private local media."""

from __future__ import annotations

import argparse
import json
import os
import re
import stat
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Optional

MAX_SNAPSHOT_BYTES = 4 * 1024 * 1024
CAPTURE_TIMEOUT_SECONDS = 45
TOKEN_PATTERN = re.compile(r"reachy-[A-Za-z0-9_-]{6,64}\.jpg")
RELAY_PATTERN = re.compile(r"[A-Za-z0-9._-]+(?:@[A-Za-z0-9._:-]+)?")


class MediaError(RuntimeError):
    """A safe error suitable for the reachyctl JSON contract."""


def _media_root(*, create: bool) -> Path:
    root = Path(
        os.environ.get(
            "REACHY_MEDIA_DIR",
            str(Path.home() / ".openclaw" / "media" / "reachy"),
        )
    ).expanduser()
    if not root.is_absolute():
        raise MediaError("Reachy media directory must be absolute")

    if create:
        root.mkdir(mode=0o700, parents=True, exist_ok=True)
    try:
        metadata = root.lstat()
    except FileNotFoundError as exc:
        raise MediaError("Reachy media directory is unavailable") from exc
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode):
        raise MediaError("Reachy media directory is invalid")
    if metadata.st_uid != os.getuid():
        raise MediaError("Reachy media directory has the wrong owner")
    os.chmod(root, 0o700)
    return root


def _relay_host() -> Optional[str]:
    relay_file = Path(
        os.environ.get(
            "REACHY_CONTROL_RELAY_FILE",
            str(Path.home() / ".openclaw" / "reachy-control-relay"),
        )
    ).expanduser()
    if not relay_file.exists():
        return None
    metadata = relay_file.lstat()
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
        raise MediaError("Reachy relay selector is invalid")
    if metadata.st_uid != os.getuid():
        raise MediaError("Reachy relay selector has the wrong owner")
    lines = relay_file.read_text(encoding="utf-8").splitlines()
    if not lines:
        raise MediaError("Reachy relay selector is invalid")
    relay = lines[0].strip()
    if not RELAY_PATTERN.fullmatch(relay):
        raise MediaError("Reachy relay selector is invalid")
    return relay


def _stream_command() -> list[str]:
    common = [
        "/usr/bin/ssh",
        "-o",
        "BatchMode=yes",
        "-o",
        "ConnectTimeout=8",
        "-o",
        "IdentityAgent=none",
        "-o",
        "StrictHostKeyChecking=yes",
    ]
    relay = _relay_host()
    if relay is not None:
        return [
            *common,
            relay,
            str(Path.home() / ".openclaw" / "bin" / "reachyctl"),
            "capture-stream",
        ]
    return [
        *common,
        "-i",
        str(Path.home() / ".ssh" / "openclaw-reachy"),
        "pollen@192.168.165.129",
        "/home/pollen/clawbody/bin/clawbody-control",
        "capture-stream",
    ]


def _validate_jpeg(path: Path) -> int:
    size = path.stat().st_size
    if not 4 <= size <= MAX_SNAPSHOT_BYTES:
        raise MediaError("Reachy returned an invalid snapshot size")
    with path.open("rb") as handle:
        if handle.read(2) != b"\xff\xd8":
            raise MediaError("Reachy returned invalid JPEG data")
        handle.seek(-2, os.SEEK_END)
        if handle.read(2) != b"\xff\xd9":
            raise MediaError("Reachy returned incomplete JPEG data")
    return size


def capture() -> dict[str, object]:
    root = _media_root(create=True)
    descriptor, raw_path = tempfile.mkstemp(
        prefix="reachy-",
        suffix=".jpg",
        dir=root,
    )
    path = Path(raw_path)
    os.fchmod(descriptor, 0o600)
    try:
        with os.fdopen(descriptor, "wb") as output:
            try:
                result = subprocess.run(
                    _stream_command(),
                    stdout=output,
                    stderr=subprocess.PIPE,
                    check=False,
                    timeout=CAPTURE_TIMEOUT_SECONDS,
                )
            except subprocess.TimeoutExpired as exc:
                raise MediaError("Reachy snapshot capture timed out") from exc
            output.flush()
            os.fsync(output.fileno())
        if result.returncode != 0:
            raise MediaError("Reachy snapshot capture failed")
        _validate_jpeg(path)
        return {
            "status": "success",
            "mediaPath": str(path),
            "cleanupToken": path.name,
        }
    except Exception:
        path.unlink(missing_ok=True)
        raise


def cleanup(token: str) -> dict[str, object]:
    if not TOKEN_PATTERN.fullmatch(token):
        raise MediaError("Invalid Reachy cleanup token")
    root = _media_root(create=False)
    path = root / token
    try:
        metadata = path.lstat()
    except FileNotFoundError as exc:
        raise MediaError("Reachy snapshot is already absent") from exc
    if (
        stat.S_ISLNK(metadata.st_mode)
        or not stat.S_ISREG(metadata.st_mode)
        or metadata.st_uid != os.getuid()
    ):
        raise MediaError("Reachy snapshot cleanup target is invalid")
    path.unlink()
    return {"status": "success", "cleaned": True}


def capture_stream() -> None:
    command = _stream_command()
    os.execv(command[0], command)


def main() -> int:
    parser = argparse.ArgumentParser(prog="reachy-media")
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("capture")
    subparsers.add_parser("capture-stream", help=argparse.SUPPRESS)
    cleanup_parser = subparsers.add_parser("cleanup")
    cleanup_parser.add_argument("token")
    args = parser.parse_args()

    try:
        if args.command == "capture-stream":
            capture_stream()
            return 0
        payload = capture() if args.command == "capture" else cleanup(args.token)
    except (MediaError, OSError) as exc:
        destination = sys.stderr if args.command == "capture-stream" else sys.stdout
        print(json.dumps({"error": str(exc)}, separators=(",", ":")), file=destination)
        return 1

    print(json.dumps(payload, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
