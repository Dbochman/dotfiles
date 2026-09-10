#!/usr/bin/env python3
"""Durable manifest-driven runner for one remote-splat workflow job."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import signal
import subprocess
import sys
import time
from typing import Any


NAME_RE = re.compile(r"\A[a-z0-9](?:[a-z0-9-]{0,46}[a-z0-9])?\Z")
REL_RE = re.compile(r"\A[A-Za-z0-9][A-Za-z0-9._/-]{0,239}\Z")
TOKEN_RE = re.compile(r"\A[A-Za-z0-9][A-Za-z0-9._-]{0,79}\Z")
SHA256_RE = re.compile(r"\A[0-9a-f]{64}\Z")
ARTIFACT_SUFFIXES = frozenset((".sog", ".ply", ".webp"))
DEPENDENCY_STATES = frozenset(("started", "succeeded"))
POWERSHELL = "/mnt/c/Windows/System32/WindowsPowerShell/v1.0/powershell.exe"
TASKKILL = "/mnt/c/Windows/System32/taskkill.exe"
CHECKPOINT_RECEIPT_SUFFIX = ".complete.json"
MAX_PLY_HEADER_BYTES = 1024 * 1024
PLY_SCALAR_BYTES = {
    "char": 1,
    "uchar": 1,
    "int8": 1,
    "uint8": 1,
    "short": 2,
    "ushort": 2,
    "int16": 2,
    "uint16": 2,
    "int": 4,
    "uint": 4,
    "int32": 4,
    "uint32": 4,
    "float": 4,
    "float32": 4,
    "double": 8,
    "float64": 8,
}


class WorkflowError(RuntimeError):
    """A bounded workflow-contract or execution failure."""


def relative_path(value: object, *, suffixes: frozenset[str] | None = None) -> str:
    if not isinstance(value, str) or not REL_RE.fullmatch(value):
        raise WorkflowError("workflow contains an invalid relative path")
    path = PurePosixPath(value)
    if path.is_absolute() or any(part in (".", "..") for part in path.parts):
        raise WorkflowError("workflow path escapes the managed job")
    if suffixes is not None and path.suffix.casefold() not in suffixes:
        raise WorkflowError("workflow path has an unsupported suffix")
    return value


def validate_manifest(payload: object) -> dict[str, Any]:
    if not isinstance(payload, dict) or payload.get("schemaVersion") != 1:
        raise WorkflowError("workflow manifest schemaVersion must be 1")
    job = payload.get("job")
    owner = payload.get("owner")
    if not isinstance(job, str) or not NAME_RE.fullmatch(job):
        raise WorkflowError("workflow job is invalid")
    if not isinstance(owner, str) or not NAME_RE.fullmatch(owner):
        raise WorkflowError("workflow owner is invalid")
    settings = payload.get("settings")
    if not isinstance(settings, dict) or not settings:
        raise WorkflowError("workflow settings must be declared")
    declared_dependencies = payload.get("dependencies")
    if not isinstance(declared_dependencies, dict) or not declared_dependencies:
        raise WorkflowError("workflow dependencies must be declared")
    quality = payload.get("qualityTiers")
    if not isinstance(quality, dict) or not {
        "target",
        "plausibleCandidate",
        "experimentalOnly",
    }.issubset(quality):
        raise WorkflowError("workflow quality tiers must be declared")
    target = quality["target"]
    plausible = quality["plausibleCandidate"]
    experimental = quality["experimentalOnly"]
    if not all(isinstance(tier, dict) for tier in (target, plausible, experimental)):
        raise WorkflowError("workflow quality tiers must be objects")
    target_views = target.get("minimumRegisteredViews")
    plausible_views = plausible.get("minimumRegisteredViews")
    if (
        not isinstance(target_views, int)
        or isinstance(target_views, bool)
        or target_views <= 0
        or not isinstance(plausible_views, int)
        or isinstance(plausible_views, bool)
        or not 0 < plausible_views <= target_views
        or experimental.get("promotionEligible") is not False
    ):
        raise WorkflowError("workflow quality tiers are invalid")

    phases = payload.get("phases")
    if not isinstance(phases, list):
        raise WorkflowError("workflow phases must be a list")
    names: set[str] = {"preflight"}
    normalized_phases: list[dict[str, Any]] = [
        {
            "name": "preflight",
            "script": "compute_canary.sh",
            "mode": "foreground",
            "dependsOn": [],
        }
    ]
    for raw in phases:
        if not isinstance(raw, dict):
            raise WorkflowError("workflow phase must be an object")
        name = raw.get("name")
        if not isinstance(name, str) or not NAME_RE.fullmatch(name) or name in names:
            raise WorkflowError("workflow phase names must be unique")
        names.add(name)
        script = relative_path(raw.get("script"), suffixes=frozenset((".sh", ".ps1")))
        mode = raw.get("mode", "foreground")
        if mode not in {"foreground", "background"}:
            raise WorkflowError("workflow phase mode is invalid")
        dependencies = raw.get("dependsOn", [])
        if not isinstance(dependencies, list):
            raise WorkflowError("workflow phase dependencies must be a list")
        normalized_dependencies: list[dict[str, str]] = []
        for dependency in dependencies:
            if not isinstance(dependency, dict):
                raise WorkflowError("workflow dependency must be an object")
            dependency_name = dependency.get("phase")
            dependency_state = dependency.get("state", "succeeded")
            if (
                not isinstance(dependency_name, str)
                or not NAME_RE.fullmatch(dependency_name)
                or dependency_state not in DEPENDENCY_STATES
            ):
                raise WorkflowError("workflow dependency is invalid")
            normalized_dependencies.append(
                {"phase": dependency_name, "state": dependency_state}
            )
        if not any(dependency["phase"] == "preflight" for dependency in normalized_dependencies):
            normalized_dependencies.append({"phase": "preflight", "state": "succeeded"})
        checkpoint = raw.get("waitForCheckpoint")
        normalized_checkpoint = None
        if checkpoint is not None:
            if not isinstance(checkpoint, dict):
                raise WorkflowError("checkpoint trigger must be an object")
            directory = relative_path(checkpoint.get("directory"))
            prefix = checkpoint.get("prefix")
            suffix = checkpoint.get("suffix", ".ply")
            receipt_suffix = checkpoint.get("receiptSuffix", CHECKPOINT_RECEIPT_SUFFIX)
            minimum_step = checkpoint.get("minimumStep")
            if (
                not isinstance(prefix, str)
                or not TOKEN_RE.fullmatch(prefix)
                or not isinstance(suffix, str)
                or not suffix.startswith(".")
                or not TOKEN_RE.fullmatch(suffix[1:])
                or not isinstance(receipt_suffix, str)
                or not receipt_suffix.startswith(".")
                or not TOKEN_RE.fullmatch(receipt_suffix[1:])
                or not isinstance(minimum_step, int)
                or isinstance(minimum_step, bool)
                or minimum_step <= 0
            ):
                raise WorkflowError("checkpoint trigger is invalid")
            normalized_checkpoint = {
                "directory": directory,
                "prefix": prefix,
                "suffix": suffix,
                "minimumStep": minimum_step,
                "receiptSuffix": receipt_suffix,
            }
        normalized = {
            "name": name,
            "script": script,
            "mode": mode,
            "dependsOn": normalized_dependencies,
        }
        if normalized_checkpoint is not None:
            normalized["waitForCheckpoint"] = normalized_checkpoint
        normalized_phases.append(normalized)

    known_names = {phase["name"] for phase in normalized_phases}
    for phase in normalized_phases:
        for dependency in phase["dependsOn"]:
            if dependency["phase"] not in known_names or dependency["phase"] == phase["name"]:
                raise WorkflowError("workflow dependency references an invalid phase")

    dependency_graph = {
        phase["name"]: [dependency["phase"] for dependency in phase["dependsOn"]]
        for phase in normalized_phases
    }
    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(name: str) -> None:
        if name in visiting:
            raise WorkflowError("workflow phase dependencies contain a cycle")
        if name in visited:
            return
        visiting.add(name)
        for dependency in dependency_graph[name]:
            visit(dependency)
        visiting.remove(name)
        visited.add(name)

    for name in dependency_graph:
        visit(name)

    artifacts = payload.get("artifacts", [])
    if not isinstance(artifacts, list):
        raise WorkflowError("workflow artifacts must be a list")
    normalized_artifacts = []
    artifact_names: set[str] = set()
    for artifact in artifacts:
        if not isinstance(artifact, dict):
            raise WorkflowError("workflow artifact must be an object")
        name = artifact.get("name")
        if not isinstance(name, str) or not NAME_RE.fullmatch(name) or name in artifact_names:
            raise WorkflowError("workflow artifact names must be unique")
        required = artifact.get("required", True)
        if not isinstance(required, bool):
            raise WorkflowError("workflow artifact required flag must be boolean")
        artifact_names.add(name)
        normalized_artifacts.append(
            {
                "name": name,
                "path": relative_path(artifact.get("path"), suffixes=ARTIFACT_SUFFIXES),
                "required": required,
            }
        )

    return {
        **payload,
        "job": job,
        "owner": owner,
        "phases": normalized_phases,
        "artifacts": normalized_artifacts,
    }


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(4 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        temporary.write_text(
            json.dumps(payload, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def environment_name_list(name: str, expression: re.Pattern[str], *, required: bool) -> list[str]:
    try:
        payload = json.loads(os.environ.get(name, ""))
    except (json.JSONDecodeError, UnicodeError) as error:
        raise WorkflowError("managed run contract is invalid") from error
    if (
        not isinstance(payload, list)
        or (required and not payload)
        or not all(isinstance(item, str) and expression.fullmatch(item) for item in payload)
        or payload != sorted(set(payload))
    ):
        raise WorkflowError("managed run contract is invalid")
    return payload


def readable_ply(path: Path) -> bool:
    try:
        size = path.stat().st_size
        with path.open("rb") as handle:
            prefix = handle.read(MAX_PLY_HEADER_BYTES + 1)
    except OSError:
        return False
    if (
        size <= 0
        or not (prefix.startswith(b"ply\n") or prefix.startswith(b"ply\r\n"))
    ):
        return False
    marker = b"end_header\n"
    marker_at = prefix.find(marker)
    if marker_at < 0:
        marker = b"end_header\r\n"
        marker_at = prefix.find(marker)
    if marker_at < 0:
        return False
    header_size = marker_at + len(marker)
    try:
        lines = prefix[:header_size].decode("ascii").splitlines()
    except UnicodeDecodeError:
        return False
    file_format = ""
    current_element = ""
    vertex_count: int | None = None
    vertex_stride = 0
    vertex_properties = 0
    for line in lines[1:]:
        fields = line.split()
        if not fields:
            continue
        if fields[0] == "format" and len(fields) == 3:
            file_format = fields[1]
        elif fields[0] == "element" and len(fields) == 3:
            current_element = fields[1]
            if current_element == "vertex":
                try:
                    vertex_count = int(fields[2])
                except ValueError:
                    return False
        elif fields[0] == "property" and current_element == "vertex":
            if len(fields) != 3 or fields[1] == "list" or fields[1] not in PLY_SCALAR_BYTES:
                return False
            vertex_stride += PLY_SCALAR_BYTES[fields[1]]
            vertex_properties += 1
    if vertex_count is None or vertex_count <= 0 or vertex_properties == 0:
        return False
    if file_format in {"binary_little_endian", "binary_big_endian"}:
        return size >= header_size + vertex_count * vertex_stride
    if file_format != "ascii":
        return False
    try:
        with path.open("rb") as handle:
            handle.seek(header_size)
            for _ in range(vertex_count):
                if len(handle.readline().split()) < vertex_properties:
                    return False
    except OSError:
        return False
    return True


def checkpoint_receipt_valid(path: Path, receipt_suffix: str) -> bool:
    receipt = path.with_name(path.name + receipt_suffix)
    if (
        not receipt.is_file()
        or receipt.is_symlink()
        or not 0 < receipt.stat().st_size <= 4096
    ):
        return False
    try:
        payload = json.loads(receipt.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return False
    return (
        isinstance(payload, dict)
        and payload.get("sizeBytes") == path.stat().st_size
        and isinstance(payload.get("sha256"), str)
        and SHA256_RE.fullmatch(payload["sha256"]) is not None
        and sha256(path) == payload["sha256"]
    )


def checkpoint_ready(job_root: Path, trigger: dict[str, Any]) -> tuple[bool, str | None, int | None]:
    directory = job_root / trigger["directory"]
    expression = re.compile(
        rf"\A{re.escape(trigger['prefix'])}(\d{{1,8}}){re.escape(trigger['suffix'])}\Z",
        flags=re.IGNORECASE,
    )
    found: list[tuple[int, str]] = []
    if directory.is_dir() and not directory.is_symlink():
        for path in directory.iterdir():
            match = expression.fullmatch(path.name)
            if not match or not path.is_file() or path.is_symlink():
                continue
            if path.suffix.casefold() == ".ply" and not readable_ply(path):
                continue
            if not checkpoint_receipt_valid(
                path, trigger.get("receiptSuffix", CHECKPOINT_RECEIPT_SUFFIX)
            ):
                continue
            found.append((int(match.group(1)), path.name))
    if not found:
        return False, None, None
    step, name = max(found)
    return step >= trigger["minimumStep"], name, step


def phase_command(
    script: Path,
    *,
    input_root: Path,
    native_pid_path: Path | None,
) -> list[str]:
    if script.suffix.casefold() == ".sh":
        return ["/bin/bash", str(script)]
    if native_pid_path is None:
        raise WorkflowError("Windows phase requires a native PID receipt")
    wrapper = input_root / "windows_phase_runner.ps1"
    if not wrapper.is_file() or wrapper.is_symlink():
        raise WorkflowError("Windows phase runner is unavailable")
    return [
        POWERSHELL,
        "-NoProfile",
        "-NonInteractive",
        "-ExecutionPolicy",
        "Bypass",
        "-File",
        subprocess.check_output(["wslpath", "-w", str(wrapper)], text=True).strip(),
        "-PhaseScript",
        subprocess.check_output(["wslpath", "-w", str(script)], text=True).strip(),
        "-PidFile",
        subprocess.check_output(["wslpath", "-w", str(native_pid_path)], text=True).strip(),
    ]


def process_group_exists(process_group: int) -> bool:
    try:
        os.killpg(process_group, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def wait_for_process_group_exit(process_group: int, timeout: float) -> bool:
    deadline = time.monotonic() + timeout
    while process_group_exists(process_group):
        if time.monotonic() >= deadline:
            return False
        time.sleep(0.05)
    return True


def windows_process_exists(pid: int) -> bool:
    completed = subprocess.run(
        [
            POWERSHELL,
            "-NoProfile",
            "-NonInteractive",
            "-Command",
            f"if (Get-Process -Id {pid} -ErrorAction SilentlyContinue) {{ exit 3 }}; exit 0",
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        timeout=15,
        check=False,
    )
    if completed.returncode not in {0, 3}:
        raise WorkflowError("native Windows process verification failed")
    return completed.returncode == 3


def terminate_windows_process_tree(pid: int) -> None:
    if not 1 <= pid <= 2**31 - 1:
        raise WorkflowError("native Windows PID receipt is invalid")
    completed = subprocess.run(
        [TASKKILL, "/PID", str(pid), "/T", "/F"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        timeout=30,
        check=False,
    )
    if completed.returncode != 0:
        raise WorkflowError("native Windows process-tree termination failed")
    deadline = time.monotonic() + 15
    while windows_process_exists(pid):
        if time.monotonic() >= deadline:
            raise WorkflowError("native Windows process tree is still running")
        time.sleep(0.25)


def terminate_process(
    process: subprocess.Popen[bytes],
    *,
    native_pid: int | None = None,
) -> None:
    native_error: BaseException | None = None
    if native_pid is not None:
        try:
            terminate_windows_process_tree(native_pid)
        except BaseException as error:
            native_error = error
    process_group = process.pid
    if process_group_exists(process_group):
        try:
            os.killpg(process_group, signal.SIGTERM)
        except ProcessLookupError:
            pass
        try:
            process.wait(timeout=2)
        except subprocess.TimeoutExpired:
            pass
        if not wait_for_process_group_exit(process_group, 2):
            try:
                os.killpg(process_group, signal.SIGKILL)
            except ProcessLookupError:
                pass
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                pass
            if not wait_for_process_group_exit(process_group, 5):
                raise WorkflowError("phase process tree is still running")
    if native_error is not None:
        raise WorkflowError("native Windows process tree could not be verified") from native_error


def read_native_receipt(
    path: Path,
    *,
    wait_seconds: float = 0,
) -> dict[str, Any] | None:
    deadline = time.monotonic() + wait_seconds
    while True:
        if path.is_file() and not path.is_symlink():
            try:
                text = path.read_text(encoding="utf-8").strip()
            except FileNotFoundError:
                if time.monotonic() >= deadline:
                    return None
                continue
            except (OSError, UnicodeError):
                raise WorkflowError("native Windows process receipt is invalid")
            if text.isdigit():
                pid = int(text)
                if 1 <= pid <= 2**31 - 1:
                    return {
                        "schemaVersion": 0,
                        "state": "running",
                        "rootPid": pid,
                        "treeCleanupVerified": False,
                    }
            try:
                payload = json.loads(text)
            except json.JSONDecodeError as error:
                raise WorkflowError("native Windows process receipt is invalid") from error
            if not isinstance(payload, dict):
                raise WorkflowError("native Windows process receipt is invalid")
            root_pid = payload.get("rootPid")
            target_pid = payload.get("targetPid")
            exit_code = payload.get("exitCode")
            state = payload.get("state")
            cleanup_verified = payload.get("treeCleanupVerified")
            if (
                payload.get("schemaVersion") != 1
                or state not in {"unused", "running", "complete"}
                or not isinstance(cleanup_verified, bool)
                or (state == "unused" and not cleanup_verified)
                or (state == "running" and cleanup_verified)
                or (state == "complete" and not cleanup_verified)
                or (
                    state == "unused"
                    and (root_pid is not None or target_pid is not None or exit_code is not None)
                )
                or (
                    state != "unused"
                    and (
                        not isinstance(root_pid, int)
                        or isinstance(root_pid, bool)
                        or not 1 <= root_pid <= 2**31 - 1
                    )
                )
                or (
                    target_pid is not None
                    and (
                        not isinstance(target_pid, int)
                        or isinstance(target_pid, bool)
                        or not 1 <= target_pid <= 2**31 - 1
                    )
                )
                or (state == "complete" and target_pid is None)
                or (
                    exit_code is not None
                    and (not isinstance(exit_code, int) or isinstance(exit_code, bool))
                )
                or (state == "complete" and exit_code is None)
            ):
                raise WorkflowError("native Windows process receipt is invalid")
            return payload
        if time.monotonic() >= deadline:
            return None
        time.sleep(0.05)


def process_tree_absent(
    process: subprocess.Popen[bytes],
    native_pid_path: Path | None,
    *,
    native_evidence_required: bool = False,
    native_termination_verified: bool = False,
) -> bool:
    if process_group_exists(process.pid):
        return False
    if native_pid_path is None:
        return not native_evidence_required
    receipt = read_native_receipt(native_pid_path)
    if receipt is None:
        return not native_evidence_required
    if receipt["state"] == "unused":
        return receipt["treeCleanupVerified"] is True
    root_pid = receipt["rootPid"]
    if receipt["state"] == "complete":
        return receipt["treeCleanupVerified"] is True and not windows_process_exists(
            root_pid
        )
    return native_termination_verified and not windows_process_exists(root_pid)


def require_manual_windows_release(state: dict[str, Any]) -> None:
    state["resourceReleaseVerified"] = False
    state["reservationState"] = "awaiting_manual_windows_verification"
    state["reservationVerificationRequiredUnixSeconds"] = int(time.time())


def run_workflow(manifest_path: Path) -> int:
    input_root = manifest_path.resolve().parent
    job_root = input_root.parent
    state_root = job_root / "state"
    output_root = job_root / "outputs"
    cancel_request = state_root / "cancel-request.json"
    state_path = state_root / "workflow-state.json"
    final_manifest_path = output_root / "run-manifest.json"
    manifest = validate_manifest(json.loads(manifest_path.read_text(encoding="utf-8")))
    approval_inventory = json.loads(
        (input_root / "approval-inventory.json").read_text(encoding="utf-8")
    )
    if (
        not isinstance(approval_inventory, dict)
        or approval_inventory.get("approvalSha256")
        != os.environ.get("OPENCLAW_WORKFLOW_APPROVAL_SHA256")
    ):
        raise WorkflowError("workflow approval inventory does not match the sealed run")
    if manifest["job"] != os.environ.get("OPENCLAW_JOB_NAME"):
        raise WorkflowError("workflow job does not match the managed run")
    if manifest["owner"] != os.environ.get("OPENCLAW_JOB_OWNER"):
        raise WorkflowError("workflow owner does not match the managed run")
    external_dependencies = environment_name_list(
        "OPENCLAW_JOB_DEPENDENCIES_JSON", NAME_RE, required=False
    )
    resources = environment_name_list(
        "OPENCLAW_JOB_RESOURCES_JSON", TOKEN_RE, required=True
    )
    approved_execution = approval_inventory.get("execution")
    actual_execution = {
        "job": manifest["job"],
        "owner": manifest["owner"],
        "externalDependencies": external_dependencies,
        "resources": resources,
    }
    if approved_execution != actual_execution:
        raise WorkflowError("managed execution contract does not match workflow approval")
    sealed_runner_sha256 = os.environ.get("OPENCLAW_RUNNER_SHA256", "")
    if not SHA256_RE.fullmatch(sealed_runner_sha256):
        raise WorkflowError("managed runner approval is invalid")

    phases = manifest["phases"]
    phase_state: dict[str, dict[str, Any]] = {
        phase["name"]: {"state": "pending"} for phase in phases
    }
    state: dict[str, Any] = {
        "schemaVersion": 1,
        "job": manifest["job"],
        "owner": manifest["owner"],
        "state": "running",
        "resourceReleaseVerified": False,
        "reservationState": "held_by_running_workflow",
        "approvalSha256": os.environ.get("OPENCLAW_WORKFLOW_APPROVAL_SHA256"),
        "sealedRunnerSha256": sealed_runner_sha256,
        "externalDependencies": external_dependencies,
        "resources": resources,
        "phases": phase_state,
    }
    write_json_atomic(state_path, state)
    running: dict[str, dict[str, Any]] = {}
    launched: list[dict[str, Any]] = []

    def terminate_phase(entry: dict[str, Any]) -> None:
        native_pid_path = entry["nativePidPath"]
        native_pid = None
        errors: list[BaseException] = []
        try:
            receipt = read_native_receipt(
                native_pid_path,
                wait_seconds=3 if entry["nativeEvidenceRequired"] else 0.25,
            )
            if receipt is not None:
                entry["nativePidObserved"] = True
                native_pid = (
                    receipt["rootPid"] if receipt["state"] == "running" else None
                )
            if receipt is None and (
                entry["nativeEvidenceRequired"] or entry["nativePidObserved"]
            ):
                raise WorkflowError("native Windows process receipt is unavailable")
        except BaseException as error:
            errors.append(error)
        try:
            terminate_process(entry["process"], native_pid=native_pid)
            if native_pid is not None:
                entry["nativeTerminationVerified"] = True
        except BaseException as error:
            errors.append(error)
        try:
            if not process_tree_absent(
                entry["process"],
                native_pid_path,
                native_evidence_required=(
                    entry["nativeEvidenceRequired"] or entry["nativePidObserved"]
                ),
                native_termination_verified=entry["nativeTerminationVerified"],
            ):
                raise WorkflowError("phase process tree is still running")
        except BaseException as error:
            errors.append(error)
        entry["cleanupVerified"] = not errors
        if errors:
            raise WorkflowError("phase process cleanup could not be verified") from errors[0]

    def cleanup_running_phases() -> bool:
        cleanup_verified = True
        for name, entry in running.items():
            try:
                terminate_phase(entry)
            except BaseException:
                cleanup_verified = False
            phase_state[name]["cleanupVerified"] = entry["cleanupVerified"]
        return cleanup_verified

    def all_process_cleanup_verified() -> bool:
        verified = True
        for entry in launched:
            if entry["cleanupVerified"] is False:
                verified = False
                continue
            if entry["cleanupVerified"] is not True:
                try:
                    entry["cleanupVerified"] = process_tree_absent(
                        entry["process"],
                        entry["nativePidPath"],
                        native_evidence_required=(
                            entry["nativeEvidenceRequired"]
                            or entry["nativePidObserved"]
                        ),
                        native_termination_verified=entry[
                            "nativeTerminationVerified"
                        ],
                    )
                except BaseException:
                    entry["cleanupVerified"] = False
            phase_state[entry["name"]]["cleanupVerified"] = entry[
                "cleanupVerified"
            ]
            verified = verified and entry["cleanupVerified"] is True
        return verified

    def unmet_dependencies(phase: dict[str, Any]) -> list[dict[str, str]]:
        unmet: list[dict[str, str]] = []
        for dependency in phase["dependsOn"]:
            actual = phase_state[dependency["phase"]]["state"]
            if dependency["state"] == "started" and actual not in {"running", "succeeded"}:
                unmet.append(dependency)
            if dependency["state"] == "succeeded" and actual != "succeeded":
                unmet.append(dependency)
        return unmet

    try:
        while True:
            if cancel_request.is_file() and not cancel_request.is_symlink():
                cleanup_verified = cleanup_running_phases()
                cleanup_verified = all_process_cleanup_verified() and cleanup_verified
                if not cleanup_verified:
                    raise WorkflowError("cancelled process cleanup could not be verified")
                state["state"] = "cancelled"
                require_manual_windows_release(state)
                write_json_atomic(state_path, state)
                return 130

            changed = False
            for name, entry in list(running.items()):
                process = entry["process"]
                native_pid_path = entry["nativePidPath"]
                receipt = read_native_receipt(native_pid_path)
                if receipt is not None:
                    entry["nativePidObserved"] = True
                    if (
                        receipt["rootPid"] is not None
                        and phase_state[name].get("nativePid") != receipt["rootPid"]
                    ):
                        phase_state[name]["nativePid"] = receipt["rootPid"]
                        changed = True
                    if phase_state[name].get("nativeState") != receipt["state"]:
                        phase_state[name]["nativeState"] = receipt["state"]
                        changed = True
                return_code = process.poll()
                if return_code is None:
                    continue
                phase_state[name]["exitCode"] = return_code
                if not process_tree_absent(
                    process,
                    native_pid_path,
                    native_evidence_required=(
                        entry["nativeEvidenceRequired"]
                        or entry["nativePidObserved"]
                    ),
                    native_termination_verified=entry["nativeTerminationVerified"],
                ):
                    phase_state[name]["state"] = "failed"
                    raise WorkflowError(f"phase {name} left a running process tree")
                entry["cleanupVerified"] = True
                phase_state[name]["cleanupVerified"] = True
                phase_state[name]["state"] = (
                    "succeeded" if return_code == 0 else "failed"
                )
                del running[name]
                changed = True
                if return_code != 0:
                    raise WorkflowError(f"phase {name} failed")

            for phase in phases:
                name = phase["name"]
                if phase_state[name]["state"] != "pending":
                    continue
                unmet = unmet_dependencies(phase)
                if unmet:
                    waiting = {"kind": "dependencies", "dependencies": unmet}
                    if phase_state[name].get("waitingOn") != waiting:
                        phase_state[name]["waitingOn"] = waiting
                        changed = True
                    continue
                if any(
                    phase_state[candidate["name"]]["state"] == "running"
                    and candidate["mode"] == "foreground"
                    for candidate in phases
                ):
                    break
                trigger = phase.get("waitForCheckpoint")
                if trigger is not None:
                    ready, checkpoint_name, checkpoint_step = checkpoint_ready(job_root, trigger)
                    if not ready:
                        waiting = {"kind": "checkpoint", **trigger}
                        if phase_state[name].get("waitingOn") != waiting:
                            phase_state[name]["waitingOn"] = waiting
                            changed = True
                        continue
                    phase_state[name]["checkpoint"] = {
                        "name": checkpoint_name,
                        "step": checkpoint_step,
                    }
                script = input_root / phase["script"]
                if not script.is_file() or script.is_symlink():
                    raise WorkflowError(f"phase {name} script is unavailable")
                native_pid_path = state_root / f"phase-{name}-native-process.json"
                write_json_atomic(
                    native_pid_path,
                    {
                        "schemaVersion": 1,
                        "state": "unused",
                        "rootPid": None,
                        "targetPid": None,
                        "treeCleanupVerified": True,
                        "exitCode": None,
                    },
                )
                phase_environment = os.environ.copy()
                phase_environment["OPENCLAW_NATIVE_PID_FILE"] = str(native_pid_path)
                process = subprocess.Popen(
                    phase_command(
                        script,
                        input_root=input_root,
                        native_pid_path=native_pid_path,
                    ),
                    cwd=job_root,
                    env=phase_environment,
                    start_new_session=True,
                )
                print(
                    "OPENCLAW_PROCESS "
                    + json.dumps({"kind": f"phase-{name}", "pid": process.pid}, separators=(",", ":")),
                    flush=True,
                )
                phase_state[name].pop("waitingOn", None)
                phase_state[name].update({"state": "running", "pid": process.pid})
                entry = {
                    "name": name,
                    "process": process,
                    "nativePidPath": native_pid_path,
                    "nativeEvidenceRequired": True,
                    "nativePidObserved": False,
                    "nativeTerminationVerified": False,
                    "cleanupVerified": None,
                }
                running[name] = entry
                launched.append(entry)
                changed = True
                if phase["mode"] == "foreground":
                    break

            if changed:
                write_json_atomic(state_path, state)
            if all(details["state"] == "succeeded" for details in phase_state.values()):
                break
            if not running and not changed:
                raise WorkflowError("workflow phases are deadlocked")
            time.sleep(2)

        artifacts = []
        for artifact in manifest["artifacts"]:
            path = output_root.joinpath(*PurePosixPath(artifact["path"]).parts)
            if not path.is_file() or path.is_symlink() or path.stat().st_size <= 0:
                if artifact["required"]:
                    raise WorkflowError(f"required artifact {artifact['name']} is unavailable")
                continue
            artifacts.append(
                {
                    **artifact,
                    "sizeBytes": path.stat().st_size,
                    "sha256": sha256(path),
                }
            )
        if not all_process_cleanup_verified():
            raise WorkflowError("successful workflow cleanup could not be verified")
        state["state"] = "succeeded"
        require_manual_windows_release(state)
        state["artifacts"] = artifacts
        write_json_atomic(state_path, state)
        write_json_atomic(
            final_manifest_path,
            {
                "schemaVersion": 1,
                "approvalScope": {
                    "sha256": state["approvalSha256"],
                    "fileCount": len(approval_inventory.get("files", [])),
                    "sizeBytes": sum(
                        item.get("sizeBytes", 0)
                        for item in approval_inventory.get("files", [])
                        if isinstance(item, dict) and isinstance(item.get("sizeBytes"), int)
                    ),
                },
                "inputs": approval_inventory.get("files", []),
                "execution": {
                    "job": manifest["job"],
                    "owner": manifest["owner"],
                    "externalDependencies": external_dependencies,
                    "resources": resources,
                    "sealedRunnerSha256": sealed_runner_sha256,
                },
                "manifest": manifest,
                "phases": phase_state,
                "artifacts": artifacts,
            },
        )
        return 0
    except BaseException:
        cleanup_running_phases()
        all_process_cleanup_verified()
        state["state"] = "failed"
        require_manual_windows_release(state)
        write_json_atomic(state_path, state)
        raise


def main(argv: list[str] | None = None) -> int:
    arguments = sys.argv[1:] if argv is None else argv
    if len(arguments) != 1:
        print("workflow_runner.py expects one workflow manifest", file=sys.stderr)
        return 2
    try:
        return run_workflow(Path(arguments[0]))
    except (WorkflowError, OSError, ValueError, json.JSONDecodeError) as error:
        print(str(error), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
