#!/usr/bin/env python3
"""Guarded remote Gaussian-splat jobs on the private RTX 5090 desktop."""

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
from typing import Sequence


HOST = "desktop-compute"
SSH = "/usr/bin/ssh"
RSYNC = "/usr/bin/rsync"
REMOTE_ROOT = "/mnt/c/Users/Owner/Documents/OpenClaw/remote-splat/jobs"
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


def validate_source_tree(path: Path) -> None:
    if path.is_file():
        return
    for directory, names, files in os.walk(path, followlinks=False):
        current = Path(directory)
        for name in (*names, *files):
            candidate = current / name
            if candidate.is_symlink() or not (candidate.is_file() or candidate.is_dir()):
                raise PublicError("source directories may contain only regular files and directories")


def remote_command(script: str, args: Sequence[str] = (), *, timeout: int = 30) -> str:
    command = "/bin/bash -s --"
    if args:
        command += " " + " ".join(shlex.quote(value) for value in args)
    try:
        completed = subprocess.run(
            [SSH, HOST, command],
            input=script,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise PublicError("desktop compute host is unavailable") from error
    if completed.returncode != 0:
        if completed.returncode == 255:
            raise PublicError("desktop compute host is unavailable")
        raise PublicError(f"desktop compute command failed (exit {completed.returncode})")
    if len(completed.stdout.encode("utf-8")) > MAX_CAPTURE_BYTES:
        raise PublicError("desktop compute response exceeded the safety limit")
    return completed.stdout


def run_rsync(arguments: Sequence[str], *, timeout: int = 24 * 60 * 60) -> None:
    try:
        completed = subprocess.run(
            # Windows' WSL-mounted filesystem permits ordinary writes but does
            # not consistently allow rsync's archive-time or temp-file updates.
            # In-place recursive transfer avoids both; exact run hashes and
            # post-fetch SHA-256 checks remain the integrity boundary.
            [RSYNC, "-r", "--inplace", "-e", SSH, *arguments],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise PublicError("secure desktop transfer failed") from error
    if completed.returncode != 0:
        raise PublicError("secure desktop transfer failed")


def parse_json_output(output: str) -> dict[str, object]:
    try:
        payload = json.loads(output)
    except (json.JSONDecodeError, UnicodeError) as error:
        raise PublicError("desktop compute returned an invalid response") from error
    if not isinstance(payload, dict):
        raise PublicError("desktop compute returned an invalid response")
    return payload


STATUS_SCRIPT = r'''set -eu
python3 - <<'PY'
import json
from pathlib import Path
import shutil
import subprocess

root = Path("/mnt/c/Users/Owner/Documents/OpenClaw/remote-splat/jobs")
tool_root = Path("/mnt/c/Users/Owner/Documents/Codex/2026-09-05/c/work")
gpu = {"available": False}
probe = Path("/usr/lib/wsl/lib/nvidia-smi")
if probe.is_file():
    result = subprocess.run(
        [str(probe), "--query-gpu=name,memory.total,driver_version", "--format=csv,noheader,nounits"],
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
        timeout=10,
        check=False,
    )
    if result.returncode == 0:
        fields = [part.strip() for part in result.stdout.strip().split(",")]
        if len(fields) == 3:
            gpu = {
                "available": True,
                "name": fields[0],
                "memoryMiB": int(fields[1]),
                "driver": fields[2],
            }

sessions = []
result = subprocess.run(
    ["tmux", "list-sessions", "-F", "#{session_name}"],
    stdout=subprocess.PIPE,
    stderr=subprocess.DEVNULL,
    text=True,
    check=False,
)
if result.returncode == 0:
    sessions = sorted(line for line in result.stdout.splitlines() if line.startswith("splat-"))

usage = shutil.disk_usage("/mnt/c")
payload = {
    "ok": True,
    "host": subprocess.check_output(["hostname"], text=True).strip(),
    "user": subprocess.check_output(["id", "-un"], text=True).strip(),
    "gpu": gpu,
    "diskFreeBytes": usage.free,
    "sessions": sessions,
    "tools": {
        "tmux": shutil.which("tmux") is not None,
        "rsync": shutil.which("rsync") is not None,
        "colmap": (tool_root / "colmap/bin/colmap.exe").is_file(),
        "brush": (tool_root / "brush/brush_app.exe").is_file(),
        "splatTransform": (tool_root / "conversion/node_modules/.bin/splat-transform.cmd").is_file(),
    },
    "jobRootReady": root.is_dir(),
}
print(json.dumps(payload, separators=(",", ":"), sort_keys=True))
PY
'''


MAKE_JOB_SCRIPT = r'''set -eu
root=$1
job=$2
umask 077
mkdir -p "$root/$job/input" "$root/$job/outputs" "$root/$job/logs" "$root/$job/state"
'''


PLAN_SCRIPT = r'''set -eu
root=$1
job=$2
relative=$3
path="$root/$job/input/$relative"
test -f "$path" && test ! -L "$path"
sha=$(sha256sum "$path" | awk '{print $1}')
size=$(stat -c '%s' "$path")
printf '{"exists":true,"sha256":"%s","sizeBytes":%s}\n' "$sha" "$size"
'''


RUN_SCRIPT = r'''set -eu
root=$1
job=$2
relative=$3
approved=$4
stage=initializing
on_exit() {
  rc=$?
  if test "$rc" -ne 0; then
    trap - EXIT
    printf '{"started":false,"stage":"%s","exitCode":%s}\n' "$stage" "$rc"
    exit 0
  fi
}
trap on_exit EXIT
job_dir="$root/$job"
runtime_dir="$HOME/.local/state/remote-splat/$job"
script="$job_dir/input/$relative"
session="splat-$job"
stage=checking_script
test -f "$script" && test ! -L "$script"
actual=$(sha256sum "$script" | awk '{print $1}')
stage=checking_approval
test "$actual" = "$approved"
stage=checking_session
if tmux has-session -t "$session" 2>/dev/null; then
  exit 23
fi
stage=preparing_job
umask 077
mkdir -p "$job_dir/logs" "$job_dir/state" "$job_dir/outputs" "$runtime_dir"
runner="$runtime_dir/run-approved-script.sh"
log="$job_dir/logs/run.log"
status="$job_dir/state/exit-code"
rm -f "$status"
stage=writing_runner
cat > "$runner" <<'RUNNER'
#!/usr/bin/env bash
set -uo pipefail
script=$1
log=$2
status=$3
exec > >(tee -a "$log") 2>&1
printf 'remote-splat started %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
rc=0
case "$script" in
  *.ps1)
    windows_script="$(wslpath -w "$script")"
    /mnt/c/Windows/System32/WindowsPowerShell/v1.0/powershell.exe \
      -NoProfile -NonInteractive -ExecutionPolicy Bypass -File "$windows_script" || rc=$?
    ;;
  *.sh)
    /bin/bash "$script" || rc=$?
    ;;
  *)
    rc=64
    ;;
esac
printf '%s\n' "$rc" > "$status"
printf 'remote-splat finished %s exit=%s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$rc"
exit "$rc"
RUNNER
chmod 700 "$runner"
stage=creating_session
tmux new-session -d -s "$session" -c "$job_dir" /bin/bash
stage=configuring_session
tmux set-option -t "$session" remain-on-exit on >/dev/null
stage=starting_script
tmux send-keys -t "$session" "exec /bin/bash $runner $script $log $status" C-m
trap - EXIT
printf '{"started":true,"session":"%s","sha256":"%s"}\n' "$session" "$actual"
'''


PROGRESS_SCRIPT = r'''set -eu
root=$1
job=$2
lines=$3
python3 - "$root" "$job" "$lines" <<'PY'
import json
from pathlib import Path
import subprocess
import sys

root, job, lines_raw = sys.argv[1:]
lines = max(1, min(200, int(lines_raw)))
job_dir = Path(root) / job
session = "splat-" + job
exists = subprocess.run(
    ["tmux", "has-session", "-t", session],
    stdout=subprocess.DEVNULL,
    stderr=subprocess.DEVNULL,
    check=False,
).returncode == 0
state = "not_started"
if exists:
    result = subprocess.run(
        ["tmux", "list-panes", "-t", session, "-F", "#{pane_dead}"],
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
        check=False,
    )
    pane_dead = result.stdout.strip().splitlines()[0] == "1" if result.returncode == 0 else False
    state = "finished" if pane_dead else "running"

exit_code = None
status_path = job_dir / "state/exit-code"
if status_path.is_file():
    try:
        exit_code = int(status_path.read_text(encoding="ascii").strip())
        state = "succeeded" if exit_code == 0 else "failed"
    except (OSError, ValueError):
        state = "invalid_status"

tail = []
log_path = job_dir / "logs/run.log"
if log_path.is_file():
    data = log_path.read_bytes()[-65536:]
    tail = data.decode("utf-8", errors="replace").splitlines()[-lines:]

print(json.dumps({
    "ok": True,
    "job": job,
    "session": session,
    "sessionExists": exists,
    "state": state,
    "exitCode": exit_code,
    "logTail": tail,
}, separators=(",", ":"), sort_keys=True))
PY
'''


ARTIFACT_SCRIPT = r'''set -eu
root=$1
job=$2
relative=$3
path="$root/$job/outputs/$relative"
test -f "$path" && test ! -L "$path"
sha=$(sha256sum "$path" | awk '{print $1}')
size=$(stat -c '%s' "$path")
printf '{"exists":true,"sha256":"%s","sizeBytes":%s}\n' "$sha" "$size"
'''


def command_status(_args: argparse.Namespace) -> None:
    emit(parse_json_output(remote_command(STATUS_SCRIPT)))


def command_stage(args: argparse.Namespace) -> None:
    job = validate_job(args.job)
    source_input = Path(args.source).expanduser()
    if source_input.is_symlink():
        raise PublicError("source must be an existing regular file or directory")
    source = source_input.resolve()
    if not source.exists() or not (source.is_file() or source.is_dir()):
        raise PublicError("source must be an existing regular file or directory")
    validate_source_tree(source)
    if args.dry_run:
        emit({"dryRun": True, "job": job, "sourceType": "directory" if source.is_dir() else "file"})
        return
    remote_command(MAKE_JOB_SCRIPT, (REMOTE_ROOT, job))
    local_source = str(source) + ("/" if source.is_dir() else "")
    run_rsync((local_source, f"{HOST}:{REMOTE_ROOT}/{job}/input/"))
    emit({"ok": True, "job": job, "staged": True, "sourceType": "directory" if source.is_dir() else "file"})


def command_plan_run(args: argparse.Namespace) -> None:
    job = validate_job(args.job)
    script = validate_relative(args.script, suffixes=SCRIPT_SUFFIXES, label="script")
    payload = parse_json_output(remote_command(PLAN_SCRIPT, (REMOTE_ROOT, job, script)))
    payload.update({"job": job, "script": script, "requiresApproval": True})
    emit(payload)


def command_run(args: argparse.Namespace) -> None:
    job = validate_job(args.job)
    script = validate_relative(args.script, suffixes=SCRIPT_SUFFIXES, label="script")
    approved = validate_sha256(args.approved_sha256)
    payload = parse_json_output(remote_command(RUN_SCRIPT, (REMOTE_ROOT, job, script, approved)))
    if payload.get("started") is not True:
        stage = payload.get("stage")
        if not isinstance(stage, str) or not re.fullmatch(r"[a-z_]{1,32}", stage):
            stage = "unknown"
        raise PublicError(f"desktop compute job did not start ({stage})")
    payload.update({"job": job, "script": script})
    emit(payload)


def command_progress(args: argparse.Namespace) -> None:
    job = validate_job(args.job)
    if not 1 <= args.lines <= 200:
        raise PublicError("lines must be between 1 and 200")
    emit(parse_json_output(remote_command(PROGRESS_SCRIPT, (REMOTE_ROOT, job, str(args.lines)))))


def command_attach(args: argparse.Namespace) -> None:
    job = validate_job(args.job)
    session = f"splat-{job}"
    try:
        completed = subprocess.run(
            [SSH, "-tt", HOST, f"exec tmux attach-session -t {session}"],
            check=False,
        )
    except OSError as error:
        raise PublicError("desktop compute tmux session is unavailable") from error
    if completed.returncode != 0:
        raise PublicError("desktop compute tmux session is unavailable")


def hash_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def command_fetch(args: argparse.Namespace) -> None:
    job = validate_job(args.job)
    artifact = validate_relative(args.artifact, suffixes=ARTIFACT_SUFFIXES, label="artifact")
    destination_input = Path(args.destination).expanduser()
    if destination_input.is_symlink():
        raise PublicError("destination must be a directory")
    destination = destination_input.resolve()
    if destination.exists() and not destination.is_dir():
        raise PublicError("destination must be a directory")
    destination.mkdir(mode=0o700, parents=True, exist_ok=True)
    local_path = destination / PurePosixPath(artifact).name
    payload = parse_json_output(remote_command(ARTIFACT_SCRIPT, (REMOTE_ROOT, job, artifact)))
    expected_hash = payload.get("sha256")
    expected_size = payload.get("sizeBytes")
    if not isinstance(expected_hash, str) or not SHA256_RE.fullmatch(expected_hash):
        raise PublicError("desktop artifact metadata is invalid")
    if not isinstance(expected_size, int) or expected_size <= 0:
        raise PublicError("desktop artifact metadata is invalid")
    if local_path.exists():
        if not local_path.is_file() or local_path.is_symlink() or hash_file(local_path) != expected_hash:
            raise PublicError("destination already contains a different artifact")
        emit({"ok": True, "job": job, "artifact": local_path.name, "sizeBytes": expected_size, "sha256": expected_hash, "alreadyPresent": True})
        return
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(prefix=".remote-splat-", dir=destination, delete=False) as handle:
            temporary = Path(handle.name)
        run_rsync((f"{HOST}:{REMOTE_ROOT}/{job}/outputs/{artifact}", str(temporary)))
        if temporary.stat().st_size != expected_size or hash_file(temporary) != expected_hash:
            raise PublicError("transferred artifact failed verification")
        os.chmod(temporary, 0o600)
        os.replace(temporary, local_path)
        temporary = None
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    emit({"ok": True, "job": job, "artifact": local_path.name, "sizeBytes": expected_size, "sha256": expected_hash, "alreadyPresent": False})


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
    emit({
        "ok": True,
        "artifact": path.name,
        "sizeBytes": size,
        "sha256": hash_file(path),
        "uploadPerformed": False,
        "requiresConfirmation": True,
    })


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
