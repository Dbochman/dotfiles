#!/usr/bin/env python3
"""Restricted client for managed jobs on the private RTX desktop."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shlex
import subprocess
import sys
import tempfile
from typing import BinaryIO, Sequence


HOST = "desktop-jobs"
SSH = "/usr/bin/ssh"
PROTOCOL = "openclaw-job-v1"
MAX_CAPTURE_BYTES = 128 * 1024
MAX_STAGE_FILES = 100_000
SCOPES = ("linux", "windows", "splat")
JOB_RE = re.compile(r"\A[a-z0-9](?:[a-z0-9-]{0,46}[a-z0-9])?\Z")
REL_RE = re.compile(r"\A[A-Za-z0-9][A-Za-z0-9._/-]{0,239}\Z")
SHA256_RE = re.compile(r"\A[0-9a-f]{64}\Z")
OWNER_RE = re.compile(r"\A[a-z0-9](?:[a-z0-9-]{0,46}[a-z0-9])?\Z")
RESOURCE_RE = re.compile(r"\A[a-z0-9](?:[a-z0-9-]{0,30}[a-z0-9])?\Z")
SCRIPT_SUFFIXES = {
    "linux": frozenset((".sh",)),
    "windows": frozenset((".ps1", ".sh")),
    "splat": frozenset((".ps1", ".sh")),
}
SAFE_REMOTE_ERRORS = frozenset(
    (
        "a declared dependency has not succeeded",
        "job already has immutable run provenance",
        "workflow is interrupted; inspect recorded process IDs before operator recovery",
        "workflow owner does not match",
    )
)
RESOURCE_BLOCK_PREFIX = "requested resources are held by unfinished jobs: "


class PublicError(RuntimeError):
    """A bounded error safe to show to the caller."""


def emit(payload: dict[str, object]) -> None:
    print(json.dumps(payload, separators=(",", ":"), sort_keys=True))


def validate_scope(value: str) -> str:
    if value not in SCOPES:
        raise PublicError("scope must be linux or windows")
    return value


def validate_job(value: str) -> str:
    if not JOB_RE.fullmatch(value):
        raise PublicError("job must be 1-48 lowercase letters, numbers, or hyphens")
    return value


def validate_relative(value: str, *, label: str) -> str:
    if not REL_RE.fullmatch(value):
        raise PublicError(f"{label} has an invalid relative path")
    relative = PurePosixPath(value)
    if relative.is_absolute() or any(part in (".", "..") for part in relative.parts):
        raise PublicError(f"{label} must stay inside its managed directory")
    return value


def validate_script(value: str, scope: str) -> str:
    relative = validate_relative(value, label="script")
    if PurePosixPath(relative).suffix.casefold() not in SCRIPT_SUFFIXES[scope]:
        allowed = ", ".join(sorted(SCRIPT_SUFFIXES[scope]))
        raise PublicError(f"script must end in one of: {allowed}")
    return relative


def validate_sha256(value: str) -> str:
    normalized = value.casefold()
    if not SHA256_RE.fullmatch(normalized):
        raise PublicError("approved SHA-256 must be exactly 64 hexadecimal characters")
    return normalized


def validate_owner(value: str) -> str:
    if not OWNER_RE.fullmatch(value):
        raise PublicError("owner must be 1-48 lowercase letters, numbers, or hyphens")
    return value


def validate_resource(value: str) -> str:
    if not RESOURCE_RE.fullmatch(value):
        raise PublicError("resource must be 1-32 lowercase letters, numbers, or hyphens")
    return value


def protocol_command(operation: str, *arguments: str) -> str:
    return shlex.join((PROTOCOL, operation, *arguments))


def safe_remote_error(stderr: bytes) -> str | None:
    if len(stderr) > 1024:
        return None
    text = stderr.decode("utf-8", errors="replace").strip()
    if "\n" in text or "\r" in text:
        return None
    if text in SAFE_REMOTE_ERRORS:
        return text
    if text.startswith(RESOURCE_BLOCK_PREFIX):
        holders = text.removeprefix(RESOURCE_BLOCK_PREFIX).split(",")
        if holders and len(holders) <= 32 and all(JOB_RE.fullmatch(job) for job in holders):
            return text
    return None


def run_ssh(
    operation: str,
    arguments: Sequence[str],
    *,
    stdin: BinaryIO | None = None,
    stdout: BinaryIO | int = subprocess.PIPE,
    timeout: int = 30,
) -> subprocess.CompletedProcess[bytes]:
    command = protocol_command(operation, *arguments)
    try:
        completed = subprocess.run(
            [SSH, HOST, command],
            stdin=stdin,
            stdout=stdout,
            stderr=subprocess.PIPE,
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise PublicError("desktop compute host is unavailable") from error
    if completed.returncode != 0:
        if completed.returncode == 255:
            raise PublicError("desktop compute host is unavailable")
        remote_error = safe_remote_error(completed.stderr)
        if remote_error is not None:
            raise PublicError(remote_error)
        raise PublicError(f"desktop compute request failed (exit {completed.returncode})")
    return completed


def call_json(
    operation: str,
    *arguments: str,
    stdin: BinaryIO | None = None,
    timeout: int = 30,
) -> dict[str, object]:
    completed = run_ssh(operation, arguments, stdin=stdin, timeout=timeout)
    output = completed.stdout
    if not isinstance(output, bytes) or len(output) > MAX_CAPTURE_BYTES:
        raise PublicError("desktop compute response exceeded the safety limit")
    try:
        payload = json.loads(output)
    except (json.JSONDecodeError, UnicodeError) as error:
        raise PublicError("desktop compute returned an invalid response") from error
    if not isinstance(payload, dict):
        raise PublicError("desktop compute returned an invalid response")
    return payload


def hash_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def source_files(source: Path) -> list[tuple[Path, str]]:
    if source.is_file():
        return [(source, source.name)]
    result: list[tuple[Path, str]] = []
    for directory, names, files in os.walk(source, followlinks=False):
        current = Path(directory)
        for name in names:
            candidate = current / name
            if candidate.is_symlink() or not candidate.is_dir():
                raise PublicError("source directories may contain only regular files and directories")
        for name in files:
            candidate = current / name
            if candidate.is_symlink() or not candidate.is_file():
                raise PublicError("source directories may contain only regular files and directories")
            relative = candidate.relative_to(source).as_posix()
            validate_relative(relative, label="source file")
            result.append((candidate, relative))
            if len(result) > MAX_STAGE_FILES:
                raise PublicError("source contains too many files for one stage operation")
    return sorted(result, key=lambda item: item[1])


def validate_local_source(value: str) -> tuple[Path, list[tuple[Path, str]]]:
    source_input = Path(value).expanduser()
    if source_input.is_symlink():
        raise PublicError("source must be an existing regular file or directory")
    source = source_input.resolve()
    if not source.exists() or not (source.is_file() or source.is_dir()):
        raise PublicError("source must be an existing regular file or directory")
    return source, source_files(source)


def command_status(args: argparse.Namespace) -> None:
    emit(call_json("status", validate_scope(args.scope)))


def command_stage(args: argparse.Namespace) -> None:
    scope = validate_scope(args.scope)
    job = validate_job(args.job)
    source, files = validate_local_source(args.source)
    total_bytes = sum(path.stat().st_size for path, _relative in files)
    if args.dry_run:
        emit(
            {
                "dryRun": True,
                "scope": scope,
                "job": job,
                "sourceType": "directory" if source.is_dir() else "file",
                "fileCount": len(files),
                "sizeBytes": total_bytes,
            }
        )
        return
    call_json("mkdir", scope, job)
    copied = 0
    reused = 0
    for path, relative in files:
        size = path.stat().st_size
        digest = hash_file(path)
        with path.open("rb") as handle:
            payload = call_json(
                "put",
                scope,
                job,
                relative,
                str(size),
                digest,
                stdin=handle,
                timeout=24 * 60 * 60,
            )
        if payload.get("alreadyPresent") is True:
            reused += 1
        else:
            copied += 1
    emit(
        {
            "ok": True,
            "scope": scope,
            "job": job,
            "staged": True,
            "sourceType": "directory" if source.is_dir() else "file",
            "fileCount": len(files),
            "copiedFiles": copied,
            "reusedFiles": reused,
            "sizeBytes": total_bytes,
        }
    )


def command_plan_run(args: argparse.Namespace) -> None:
    scope = validate_scope(args.scope)
    job = validate_job(args.job)
    script = validate_script(args.script, scope)
    payload = call_json("stat-input", scope, job, script)
    payload.update(
        {
            "scope": scope,
            "job": job,
            "script": script,
            "requiresApproval": True,
        }
    )
    emit(payload)


def command_run(args: argparse.Namespace) -> None:
    scope = validate_scope(args.scope)
    job = validate_job(args.job)
    script = validate_script(args.script, scope)
    approved = validate_sha256(args.approved_sha256)
    owner = validate_owner(args.owner)
    dependencies = sorted({validate_job(value) for value in args.depends_on})
    if job in dependencies:
        raise PublicError("a job cannot depend on itself")
    resources = sorted({validate_resource(value) for value in args.reserve})
    if not resources:
        raise PublicError("at least one resource reservation is required")
    payload = call_json(
        "run",
        scope,
        job,
        script,
        approved,
        owner,
        json.dumps(dependencies, separators=(",", ":")),
        json.dumps(resources, separators=(",", ":")),
    )
    if payload.get("started") is not True:
        raise PublicError("desktop compute job did not start")
    emit(payload)


def command_progress(args: argparse.Namespace) -> None:
    scope = validate_scope(args.scope)
    job = validate_job(args.job)
    if not 1 <= args.lines <= 200:
        raise PublicError("lines must be between 1 and 200")
    emit(call_json("progress", scope, job, str(args.lines)))


def command_cancel(args: argparse.Namespace) -> None:
    scope = validate_scope(args.scope)
    job = validate_job(args.job)
    owner = validate_owner(args.owner)
    emit(call_json("cancel", scope, job, owner))


def command_attach(args: argparse.Namespace) -> None:
    scope = validate_scope(args.scope)
    job = validate_job(args.job)
    command = protocol_command("attach", scope, job)
    try:
        completed = subprocess.run([SSH, "-tt", HOST, command], check=False)
    except OSError as error:
        raise PublicError("desktop compute tmux session is unavailable") from error
    if completed.returncode != 0:
        raise PublicError("desktop compute tmux session is unavailable")


def output_metadata(scope: str, job: str, artifact: str) -> tuple[int, str]:
    payload = call_json("stat-output", scope, job, artifact)
    size = payload.get("sizeBytes")
    digest = payload.get("sha256")
    if not isinstance(size, int) or size < 0:
        raise PublicError("desktop artifact metadata is invalid")
    if not isinstance(digest, str) or not SHA256_RE.fullmatch(digest):
        raise PublicError("desktop artifact metadata is invalid")
    return size, digest


def command_fetch(args: argparse.Namespace) -> None:
    scope = validate_scope(args.scope)
    job = validate_job(args.job)
    artifact = validate_relative(args.artifact, label="artifact")
    destination_input = Path(args.destination).expanduser()
    if destination_input.is_symlink():
        raise PublicError("destination must be a directory")
    destination = destination_input.resolve()
    if destination.exists() and not destination.is_dir():
        raise PublicError("destination must be a directory")
    destination.mkdir(mode=0o700, parents=True, exist_ok=True)
    local_path = destination / PurePosixPath(artifact).name
    expected_size, expected_hash = output_metadata(scope, job, artifact)
    if local_path.exists():
        if not local_path.is_file() or local_path.is_symlink() or hash_file(local_path) != expected_hash:
            raise PublicError("destination already contains a different artifact")
        emit(
            {
                "ok": True,
                "scope": scope,
                "job": job,
                "artifact": local_path.name,
                "sizeBytes": expected_size,
                "sha256": expected_hash,
                "alreadyPresent": True,
            }
        )
        return

    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(prefix=".desktop-compute-", dir=destination, delete=False) as handle:
            temporary = Path(handle.name)
            run_ssh(
                "get-output",
                (scope, job, artifact, expected_hash),
                stdout=handle,
                timeout=24 * 60 * 60,
            )
            handle.flush()
            os.fsync(handle.fileno())
        if temporary.stat().st_size != expected_size or hash_file(temporary) != expected_hash:
            raise PublicError("transferred artifact failed verification")
        os.chmod(temporary, 0o600)
        os.replace(temporary, local_path)
        temporary = None
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    emit(
        {
            "ok": True,
            "scope": scope,
            "job": job,
            "artifact": local_path.name,
            "sizeBytes": expected_size,
            "sha256": expected_hash,
            "alreadyPresent": False,
        }
    )


def add_scope(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--scope", choices=SCOPES, default="linux", help=argparse.SUPPRESS)


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    sub = result.add_subparsers(dest="command", required=True)

    status = sub.add_parser("status", help="check the bridge, GPU, tools, disk, and job sessions")
    add_scope(status)
    status.set_defaults(func=command_status)

    stage = sub.add_parser("stage", help="copy a source into one managed job input directory")
    add_scope(stage)
    stage.add_argument("--job", required=True)
    stage.add_argument("--source", required=True)
    stage.add_argument("--dry-run", action="store_true")
    stage.set_defaults(func=command_stage)

    plan = sub.add_parser("plan-run", help="return the exact approval hash for a staged script")
    add_scope(plan)
    plan.add_argument("--job", required=True)
    plan.add_argument("--script", required=True)
    plan.set_defaults(func=command_plan_run)

    run = sub.add_parser("run", help="start an exact hash-approved script in a durable tmux session")
    add_scope(run)
    run.add_argument("--job", required=True)
    run.add_argument("--script", required=True)
    run.add_argument("--approved-sha256", required=True)
    run.add_argument("--owner", required=True)
    run.add_argument("--depends-on", action="append", default=[])
    run.add_argument("--reserve", action="append", required=True)
    run.set_defaults(func=command_run)

    progress = sub.add_parser("progress", help="read bounded state and log output for one job")
    add_scope(progress)
    progress.add_argument("--job", required=True)
    progress.add_argument("--lines", type=int, default=40)
    progress.set_defaults(func=command_progress)

    cancel = sub.add_parser("cancel", help="request cancellation of an owned managed workflow")
    add_scope(cancel)
    cancel.add_argument("--job", required=True)
    cancel.add_argument("--owner", required=True)
    cancel.set_defaults(func=command_cancel)

    attach = sub.add_parser("attach", help="interactively attach to one job's tmux session")
    add_scope(attach)
    attach.add_argument("--job", required=True)
    attach.set_defaults(func=command_attach)

    fetch = sub.add_parser("fetch", help="retrieve and verify one managed job output")
    add_scope(fetch)
    fetch.add_argument("--job", required=True)
    fetch.add_argument("--artifact", required=True)
    fetch.add_argument("--destination", required=True)
    fetch.set_defaults(func=command_fetch)
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
