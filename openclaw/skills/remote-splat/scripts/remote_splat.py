#!/usr/bin/env python3
"""Guarded Gaussian-splat specialization of the desktop-compute job layer."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import subprocess
import sys
from typing import Sequence


DESKTOP_COMPUTE = os.environ.get("DESKTOP_COMPUTE_BIN", "/opt/homebrew/bin/desktop-compute")
MAX_CAPTURE_BYTES = 128 * 1024
JOB_RE = re.compile(r"\A[a-z0-9](?:[a-z0-9-]{0,46}[a-z0-9])?\Z")
REL_RE = re.compile(r"\A[A-Za-z0-9][A-Za-z0-9._/-]{0,239}\Z")
SHA256_RE = re.compile(r"\A[0-9a-f]{64}\Z")
SCRIPT_SUFFIXES = frozenset((".ps1", ".sh"))
ARTIFACT_SUFFIXES = frozenset((".sog", ".ply"))


class PublicError(RuntimeError):
    """A bounded error safe to show to the caller."""


def emit(payload: dict[str, object]) -> None:
    print(json.dumps(payload, separators=(",", ":"), sort_keys=True))


def validate_job(value: str) -> str:
    if not JOB_RE.fullmatch(value):
        raise PublicError("job must be 1-48 lowercase letters, numbers, or hyphens")
    return value


def validate_relative(value: str, *, suffixes: frozenset[str], label: str) -> str:
    if not REL_RE.fullmatch(value):
        raise PublicError(f"{label} has an invalid relative path")
    path = PurePosixPath(value)
    if path.is_absolute() or ".." in path.parts or "." in path.parts:
        raise PublicError(f"{label} must stay inside its managed directory")
    if path.suffix.casefold() not in suffixes:
        choices = ", ".join(sorted(suffixes))
        raise PublicError(f"{label} must end in one of: {choices}")
    return value


def validate_sha256(value: str) -> str:
    normalized = value.casefold()
    if not SHA256_RE.fullmatch(normalized):
        raise PublicError("approved SHA-256 must be exactly 64 hexadecimal characters")
    return normalized


def desktop_arguments(command: str, arguments: Sequence[str]) -> list[str]:
    return [DESKTOP_COMPUTE, command, "--scope", "splat", *arguments]


def run_desktop(
    command: str,
    arguments: Sequence[str] = (),
    *,
    interactive: bool = False,
) -> dict[str, object] | None:
    try:
        if interactive:
            completed = subprocess.run(desktop_arguments(command, arguments), check=False)
            if completed.returncode != 0:
                raise PublicError("desktop splat tmux session is unavailable")
            return None
        completed = subprocess.run(
            desktop_arguments(command, arguments),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=24 * 60 * 60,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise PublicError("desktop compute host is unavailable") from error
    if completed.returncode != 0:
        raise PublicError("desktop splat operation failed")
    if len(completed.stdout.encode("utf-8")) > MAX_CAPTURE_BYTES:
        raise PublicError("desktop compute response exceeded the safety limit")
    try:
        payload = json.loads(completed.stdout)
    except (json.JSONDecodeError, UnicodeError) as error:
        raise PublicError("desktop compute returned an invalid response") from error
    if not isinstance(payload, dict):
        raise PublicError("desktop compute returned an invalid response")
    return payload


def command_status(_args: argparse.Namespace) -> None:
    payload = run_desktop("status")
    assert payload is not None
    emit(payload)


def command_stage(args: argparse.Namespace) -> None:
    job = validate_job(args.job)
    arguments = ["--job", job, "--source", args.source]
    if args.dry_run:
        arguments.append("--dry-run")
    payload = run_desktop("stage", arguments)
    assert payload is not None
    emit(payload)


def command_plan_run(args: argparse.Namespace) -> None:
    job = validate_job(args.job)
    script = validate_relative(args.script, suffixes=SCRIPT_SUFFIXES, label="script")
    payload = run_desktop("plan-run", ["--job", job, "--script", script])
    assert payload is not None
    emit(payload)


def command_run(args: argparse.Namespace) -> None:
    job = validate_job(args.job)
    script = validate_relative(args.script, suffixes=SCRIPT_SUFFIXES, label="script")
    approved = validate_sha256(args.approved_sha256)
    payload = run_desktop(
        "run",
        ["--job", job, "--script", script, "--approved-sha256", approved],
    )
    assert payload is not None
    emit(payload)


def command_progress(args: argparse.Namespace) -> None:
    job = validate_job(args.job)
    if not 1 <= args.lines <= 200:
        raise PublicError("lines must be between 1 and 200")
    payload = run_desktop("progress", ["--job", job, "--lines", str(args.lines)])
    assert payload is not None
    emit(payload)


def command_attach(args: argparse.Namespace) -> None:
    job = validate_job(args.job)
    run_desktop("attach", ["--job", job], interactive=True)


def command_fetch(args: argparse.Namespace) -> None:
    job = validate_job(args.job)
    artifact = validate_relative(args.artifact, suffixes=ARTIFACT_SUFFIXES, label="artifact")
    payload = run_desktop(
        "fetch",
        ["--job", job, "--artifact", artifact, "--destination", args.destination],
    )
    assert payload is not None
    emit(payload)


def hash_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def command_publish_plan(args: argparse.Namespace) -> None:
    input_path = Path(args.file).expanduser()
    if input_path.is_symlink():
        raise PublicError("publish file must be a regular .sog or .ply artifact")
    path = input_path.resolve()
    if not path.is_file() or path.suffix.casefold() not in ARTIFACT_SUFFIXES:
        raise PublicError("publish file must be a regular .sog or .ply artifact")
    size = path.stat().st_size
    if size <= 0:
        raise PublicError("publish artifact is empty")
    emit(
        {
            "ok": True,
            "artifact": path.name,
            "sizeBytes": size,
            "sha256": hash_file(path),
            "uploadPerformed": False,
            "requiresConfirmation": True,
        }
    )


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    sub = result.add_subparsers(dest="command", required=True)

    status = sub.add_parser("status", help="check bridge, GPU, tools, disk, and sessions")
    status.set_defaults(func=command_status)

    stage = sub.add_parser("stage", help="copy one source into a managed remote job")
    stage.add_argument("--job", required=True)
    stage.add_argument("--source", required=True)
    stage.add_argument("--dry-run", action="store_true")
    stage.set_defaults(func=command_stage)

    plan = sub.add_parser("plan-run", help="inspect a staged script and return its approval hash")
    plan.add_argument("--job", required=True)
    plan.add_argument("--script", required=True)
    plan.set_defaults(func=command_plan_run)

    run = sub.add_parser("run", help="start an exact hash-approved script in a job tmux session")
    run.add_argument("--job", required=True)
    run.add_argument("--script", required=True)
    run.add_argument("--approved-sha256", required=True)
    run.set_defaults(func=command_run)

    progress = sub.add_parser("progress", help="read bounded state and log output for one job")
    progress.add_argument("--job", required=True)
    progress.add_argument("--lines", type=int, default=40)
    progress.set_defaults(func=command_progress)

    attach = sub.add_parser("attach", help="interactively attach to one job's tmux session")
    attach.add_argument("--job", required=True)
    attach.set_defaults(func=command_attach)

    fetch = sub.add_parser("fetch", help="retrieve and verify one .sog or .ply output")
    fetch.add_argument("--job", required=True)
    fetch.add_argument("--artifact", required=True)
    fetch.add_argument("--destination", required=True)
    fetch.set_defaults(func=command_fetch)

    publish = sub.add_parser("publish-plan", help="hash a local artifact without uploading it")
    publish.add_argument("--file", required=True)
    publish.set_defaults(func=command_publish_plan)
    return result


def main(argv: Sequence[str] | None = None) -> int:
    try:
        args = parser().parse_args(argv)
        args.func(args)
    except PublicError as error:
        print(str(error), file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
