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
    if not isinstance(phases, list) or not phases:
        raise WorkflowError("workflow must declare internal phases")
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
            minimum_step = checkpoint.get("minimumStep")
            if (
                not isinstance(prefix, str)
                or not TOKEN_RE.fullmatch(prefix)
                or not isinstance(suffix, str)
                or not suffix.startswith(".")
                or not TOKEN_RE.fullmatch(suffix[1:])
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
            if match and path.is_file() and not path.is_symlink() and path.stat().st_size > 0:
                found.append((int(match.group(1)), path.name))
    if not found:
        return False, None, None
    step, name = max(found)
    return step >= trigger["minimumStep"], name, step


def phase_command(script: Path) -> list[str]:
    if script.suffix.casefold() == ".sh":
        return ["/bin/bash", str(script)]
    return [
        "/mnt/c/Windows/System32/WindowsPowerShell/v1.0/powershell.exe",
        "-NoProfile",
        "-NonInteractive",
        "-ExecutionPolicy",
        "Bypass",
        "-File",
        subprocess.check_output(["wslpath", "-w", str(script)], text=True).strip(),
    ]


def terminate_process(process: subprocess.Popen[bytes]) -> None:
    if process.poll() is not None:
        return
    try:
        os.killpg(process.pid, signal.SIGTERM)
        process.wait(timeout=15)
    except (ProcessLookupError, subprocess.TimeoutExpired):
        if process.poll() is None:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            process.wait(timeout=10)


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
        "approvalSha256": os.environ.get("OPENCLAW_WORKFLOW_APPROVAL_SHA256"),
        "sealedRunnerSha256": sealed_runner_sha256,
        "externalDependencies": external_dependencies,
        "resources": resources,
        "phases": phase_state,
    }
    write_json_atomic(state_path, state)
    running: dict[str, subprocess.Popen[bytes]] = {}

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
                for process in running.values():
                    terminate_process(process)
                state["state"] = "cancelled"
                write_json_atomic(state_path, state)
                return 130

            changed = False
            for name, process in list(running.items()):
                return_code = process.poll()
                if return_code is None:
                    continue
                phase_state[name]["exitCode"] = return_code
                phase_state[name]["state"] = "succeeded" if return_code == 0 else "failed"
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
                process = subprocess.Popen(
                    phase_command(script),
                    cwd=job_root,
                    start_new_session=True,
                )
                print(
                    "OPENCLAW_PROCESS "
                    + json.dumps({"kind": f"phase-{name}", "pid": process.pid}, separators=(",", ":")),
                    flush=True,
                )
                phase_state[name].pop("waitingOn", None)
                phase_state[name].update({"state": "running", "pid": process.pid})
                running[name] = process
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
        state["state"] = "succeeded"
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
        for process in running.values():
            terminate_process(process)
        state["state"] = "failed"
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
