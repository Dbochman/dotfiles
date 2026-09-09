#!/usr/bin/env python3
"""Guarded Gaussian-splat specialization of the desktop-compute job layer."""

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


SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))
import video_prep  # noqa: E402
import workflow_runner  # noqa: E402


DESKTOP_COMPUTE = os.environ.get("DESKTOP_COMPUTE_BIN", "/opt/homebrew/bin/desktop-compute")
TAILSCALE = os.environ.get("TAILSCALE_BIN", "/opt/homebrew/bin/tailscale")
TAILDROP_TARGET = "desktop-r9js0ok:"
WORKSTATION_SSH = os.environ.get("WORKSTATION_SSH_BIN", "/usr/bin/ssh")
WORKSTATION_HOST = "dylans-mac"
WORKSTATION_DOWNLOADS = PurePosixPath("/Users/dylanbochman/Downloads")
WORKSTATION_TAILSCALE_CANDIDATES = (
    os.environ.get("WORKSTATION_TAILSCALE_BIN", "/usr/local/bin/tailscale"),
    "/Applications/Tailscale.app/Contents/MacOS/Tailscale",
    "/opt/homebrew/bin/tailscale",
)
MAX_CAPTURE_BYTES = 128 * 1024
JOB_RE = re.compile(r"\A[a-z0-9](?:[a-z0-9-]{0,46}[a-z0-9])?\Z")
REL_RE = re.compile(r"\A[A-Za-z0-9][A-Za-z0-9._/-]{0,239}\Z")
INBOX_NAME_RE = re.compile(r"\A[A-Za-z0-9][A-Za-z0-9._-]{0,159}\Z")
SHA256_RE = re.compile(r"\A[0-9a-f]{64}\Z")
PROGRESS_NAME_RE = re.compile(r"\A[a-z0-9](?:[a-z0-9-]{0,46}[a-z0-9])?\Z")
PROGRESS_UNIT_RE = re.compile(r"\A[A-Za-z0-9][A-Za-z0-9._ -]{0,31}\Z")
OWNER_RE = re.compile(r"\A[a-z0-9](?:[a-z0-9-]{0,46}[a-z0-9])?\Z")
RESOURCE_RE = re.compile(r"\A[a-z0-9](?:[a-z0-9-]{0,30}[a-z0-9])?\Z")
SEGMENT_RE = re.compile(r"\A[A-Za-z0-9](?:[A-Za-z0-9._-]{0,62}[A-Za-z0-9])?\Z")
CHECKPOINT_RE = re.compile(r"\A[A-Za-z0-9][A-Za-z0-9._-]*?(\d{1,8})\.[A-Za-z0-9]+\Z")
PROGRESS_PREFIX = "OPENCLAW_PROGRESS "
PROCESS_PREFIX = "OPENCLAW_PROCESS "
SCRIPT_SUFFIXES = frozenset((".ps1", ".sh"))
FETCH_SUFFIXES = frozenset((".sog", ".ply", ".webp"))
PUBLISH_SUFFIXES = frozenset((".sog", ".ply"))
WORKFLOW_PHASE_SUFFIXES = (
    "preflight",
    "prepare",
    "connect",
    "train",
    "preview",
    "convert",
    "validate",
)
SUPPORT_FILES = (
    SCRIPT_DIR / "compute_canary.sh",
    SCRIPT_DIR / "windows_io.ps1",
    SCRIPT_DIR / "windows_phase_runner.ps1",
    SCRIPT_DIR / "windows_process_runner.ps1",
    SCRIPT_DIR / "workflow_runner.py",
)
SAFE_DESKTOP_ERRORS = frozenset(
    (
        "a declared dependency has not succeeded",
        "job already has immutable run provenance",
        "explicit human Windows process confirmation is required",
        "manual Windows release receipt is invalid",
        "native Windows process receipt is invalid",
        "a recorded Windows process is still active",
        "a recorded workflow phase is still active",
        "Windows process verification failed",
        "workflow process inventory is invalid",
        "workflow is not awaiting manual Windows verification",
        "workflow runner is still active",
        "workflow runner PID receipt is invalid",
        "workflow is interrupted; inspect recorded process IDs before operator recovery",
        "workflow owner does not match",
    )
)
RESOURCE_BLOCK_PREFIX = "requested resources are held by unfinished jobs: "
WORKFLOW_PROFILES: dict[str, dict[str, object]] = {
    "preview": {
        "objective": "return a fast private visual candidate",
        "registeredViewSelection": "quality-bucketed",
        "trainingBudget": "reduced-explicit",
        "comparativeQa": "small-fixed-set-after-delivery",
        "fallbackStartCondition": "no-trainable-reconstruction",
        "fallbackCriticalUntil": "trainable-primary-reconstruction",
        "promotionEligible": False,
    },
    "best-current": {
        "objective": "return the strongest private candidate within a declared resource bound",
        "registeredViewSelection": "all-registered-within-declared-bound",
        "trainingBudget": "full-explicit",
        "comparativeQa": "non-blocking-after-delivery",
        "fallbackStartCondition": "no-trainable-reconstruction",
        "fallbackCriticalUntil": "trainable-primary-reconstruction",
        "promotionEligible": False,
    },
    "promotion-candidate": {
        "objective": "evaluate a candidate for explicit master replacement or publication",
        "registeredViewSelection": "quality-and-coverage-aware",
        "trainingBudget": "baseline-or-explicit",
        "comparativeQa": "full-before-promotion",
        "fallbackStartCondition": "named-promotion-gate-deficit",
        "fallbackCriticalUntil": "promotion-gates-pass-or-candidate-is-rejected",
        "promotionEligible": True,
    },
}


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


def validate_owner(value: str) -> str:
    if not OWNER_RE.fullmatch(value):
        raise PublicError("owner must be 1-48 lowercase letters, numbers, or hyphens")
    return value


def validate_resource(value: str) -> str:
    if not RESOURCE_RE.fullmatch(value):
        raise PublicError("resource must be 1-32 lowercase letters, numbers, or hyphens")
    return value


def quality_tiers(
    *,
    target_registered_views: int,
    plausible_registered_views: int,
    minimum_points: int,
    maximum_reprojection_error_px: float,
    segment_floors: Sequence[str],
) -> dict[str, object]:
    if target_registered_views <= 0:
        raise PublicError("target registered views must be positive")
    if not 0 < plausible_registered_views <= target_registered_views:
        raise PublicError("plausible registered views must be positive and no greater than target")
    if minimum_points <= 0:
        raise PublicError("minimum points must be positive")
    if not 0 < maximum_reprojection_error_px <= 10:
        raise PublicError("maximum reprojection error must be between 0 and 10 pixels")
    parsed_floors: dict[str, float] = {}
    for encoded in segment_floors:
        name, separator, value = encoded.partition("=")
        if not separator or not SEGMENT_RE.fullmatch(name) or name in parsed_floors:
            raise PublicError("segment floors must be unique NAME=PERCENT values")
        try:
            percent = float(value)
        except ValueError as error:
            raise PublicError("segment floor percentages must be numeric") from error
        if not 0 < percent <= 100:
            raise PublicError("segment floor percentages must be between 0 and 100")
        parsed_floors[name] = percent
    if not parsed_floors:
        raise PublicError("at least one segment floor must be declared")
    plausible = {
        "minimumRegisteredViews": plausible_registered_views,
        "minimumSparsePoints": minimum_points,
        "maximumMeanReprojectionErrorPx": maximum_reprojection_error_px,
        "minimumCoveragePercentBySegment": parsed_floors,
    }
    return {
        "declaredBeforePreparation": True,
        "target": {
            **plausible,
            "minimumRegisteredViews": target_registered_views,
        },
        "plausibleCandidate": plausible,
        "experimentalOnly": {
            "requires": [
                "single-readable-model",
                "positive-registered-view-count",
                "positive-sparse-point-count",
            ],
            "promotionEligible": False,
        },
        "targetMissMustBeDisclosed": True,
    }


def desktop_arguments(command: str, arguments: Sequence[str]) -> list[str]:
    return [DESKTOP_COMPUTE, command, "--scope", "splat", *arguments]


def safe_desktop_error(stderr: str) -> str | None:
    text = stderr.strip()
    if len(text.encode("utf-8")) > 1024 or "\n" in text or "\r" in text:
        return None
    if text in SAFE_DESKTOP_ERRORS:
        return text
    if text.startswith(RESOURCE_BLOCK_PREFIX):
        holders = text.removeprefix(RESOURCE_BLOCK_PREFIX).split(",")
        if holders and len(holders) <= 32 and all(JOB_RE.fullmatch(job) for job in holders):
            return text
    return None


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
        desktop_error = safe_desktop_error(completed.stderr)
        if desktop_error is not None:
            raise PublicError(desktop_error)
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


def build_workflow_plan(
    job_prefix: str,
    profile_name: str,
    declared_quality_tiers: dict[str, object],
) -> dict[str, object]:
    validate_job(job_prefix)
    jobs: dict[str, str] = {}
    for suffix in WORKFLOW_PHASE_SUFFIXES:
        job = f"{job_prefix}-{suffix}"
        try:
            jobs[suffix] = validate_job(job)
        except PublicError as error:
            raise PublicError("job prefix is too long for immutable phase job names") from error

    profile = WORKFLOW_PROFILES[profile_name]
    return {
        "ok": True,
        "profile": profile_name,
        "jobPrefix": job_prefix,
        "jobs": jobs,
        "objective": profile["objective"],
        "registeredViewSelection": profile["registeredViewSelection"],
        "training": {
            "budget": profile["trainingBudget"],
            "defaultsApplied": False,
            "declare": ["iterations", "maxResolution", "maxSplats", "shDegree", "seed"],
            "checkpointFilenameStep": "normalized-integer-accepts-zero-padding",
        },
        "qualityTiers": declared_quality_tiers,
        "runOwnership": {
            "ownerRequired": True,
            "dependenciesRecorded": True,
            "resourceReservationsRequired": True,
            "reviewersDefaultToReadOnly": True,
            "nativeProcessMarkersRequired": True,
        },
        "scheduling": {
            "parallelBeforeGpu": [
                "source-transfer",
                "source-hashing",
                "video-review",
                "semantic-extraction-planning",
                "job-script-preparation",
            ],
            "parallelOnlyWhenInputsAreIndependent": True,
            "exclusiveDesktopOrder": ["connect", "train"],
            "phaseDependencies": {
                "preflight": [],
                "prepare": ["preflight"],
                "connect": ["preflight", "prepare"],
                "train": ["preflight", "connect"],
                "preview": ["train:first-readable-checkpoint"],
                "convert": ["train"],
                "validate": ["convert"],
            },
            "persistCheckpoints": [
                "after-feature-matching",
                "after-each-mapper-attempt",
                "after-training-export",
                "after-conversion",
            ],
            "reservations": {
                "connect": ["desktop-heavy"],
                "train": ["desktop-heavy"],
                "preview": ["splat-converter"],
                "convert": ["splat-converter"],
                "validate": ["splat-converter"],
            },
        },
        "monitoring": {
            "command": "remote-splat inspect",
            "ownedByWorkflowRunner": True,
            "createsDiagnosticJobs": False,
            "checkpointStepsNormalizeZeroPadding": True,
        },
        "fallback": {
            "startOnlyWhen": profile["fallbackStartCondition"],
            "criticalUntil": profile["fallbackCriticalUntil"],
        },
        "incrementalExtension": {
            "basePolicy": "immutable-versioned-feature-compatible",
            "preflightRequires": [
                "base-manifest-and-hashes",
                "base-image-inventory",
                "feature-schema",
                "seed-binary-and-text-models",
                "new-source-manifest-and-hashes",
                "declared-training-selection-bound",
            ],
            "matching": [
                "sequential-within-new-segments",
                "targeted-cross-segment-connectors",
                "targeted-new-to-known-anchors",
            ],
            "registration": [
                "normal-thresholds",
                "triangulate",
                "relaxed-unregistered-connectors-only",
                "repeat-until-count-stable",
            ],
            "preview": "convert-first-readable-training-checkpoint-without-stopping-final",
        },
        "privateDelivery": {
            "when": "immediately-after-basic-validation",
            "requires": [
                "nonempty-export",
                "converter-readable",
                "positive-gaussian-count",
                "sha256-verified",
            ],
            "comparativeQa": profile["comparativeQa"],
        },
        "previewDelivery": {
            "when": "first-readable-training-checkpoint",
            "continuesFinalTraining": True,
            "label": "private-preview-not-final",
            "promotionEligible": False,
        },
        "promotion": {
            "eligible": profile["promotionEligible"],
            "requiresExplicitConfirmation": True,
            "requiresFullComparativeValidation": True,
        },
    }


def command_workflow_plan(args: argparse.Namespace) -> None:
    declared_quality_tiers = quality_tiers(
        target_registered_views=args.target_registered_views,
        plausible_registered_views=args.plausible_registered_views,
        minimum_points=args.minimum_points,
        maximum_reprojection_error_px=args.maximum_reprojection_error_px,
        segment_floors=args.segment_floor,
    )
    emit(build_workflow_plan(args.job_prefix, args.profile, declared_quality_tiers))


def command_stage(args: argparse.Namespace) -> None:
    job = validate_job(args.job)
    arguments = ["--job", job, "--source", args.source]
    if args.dry_run:
        arguments.append("--dry-run")
    payload = run_desktop("stage", arguments)
    assert payload is not None
    emit(payload)


def command_preflight_plan(args: argparse.Namespace) -> None:
    """Build a sealed preflight-only workflow using the normal lifecycle runner."""
    job = validate_job(args.job)
    owner = validate_owner(args.owner)
    with tempfile.TemporaryDirectory(prefix="remote-splat-preflight-") as directory:
        bundle = Path(directory)
        manifest = {
            "schemaVersion": 1,
            "job": job,
            "owner": owner,
            "settings": {"mode": "preflight-only"},
            "dependencies": {"managedCanary": "sealed-support-bundle"},
            "qualityTiers": {
                "target": {"minimumRegisteredViews": 1},
                "plausibleCandidate": {"minimumRegisteredViews": 1},
                "experimentalOnly": {"promotionEligible": False},
            },
            "phases": [],
            "artifacts": [],
        }
        (bundle / "workflow.json").write_text(
            json.dumps(manifest, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        lifecycle_args = argparse.Namespace(
            job=job,
            bundle=str(bundle),
            owner=owner,
            depends_on=args.depends_on,
            reserve=args.reserve or ["desktop-heavy"],
            dry_run=args.dry_run,
        )
        result = prepare_workflow(lifecycle_args)
    result["preflightOnly"] = True
    emit(result)


def workflow_bundle_files(bundle: Path) -> list[tuple[Path, str]]:
    if bundle.is_symlink() or not bundle.is_dir():
        raise PublicError("workflow bundle must be an existing directory")
    files: list[tuple[Path, str]] = []
    reserved = {path.name for path in SUPPORT_FILES} | {
        "approval-inventory.json",
        "run-workflow.sh",
    }
    for directory, names, filenames in os.walk(bundle, followlinks=False):
        current = Path(directory)
        for name in names:
            candidate = current / name
            if candidate.is_symlink() or not candidate.is_dir():
                raise PublicError("workflow bundles may contain only regular files and directories")
        for name in filenames:
            candidate = current / name
            if candidate.is_symlink() or not candidate.is_file():
                raise PublicError("workflow bundles may contain only regular files and directories")
            relative = candidate.relative_to(bundle).as_posix()
            if not REL_RE.fullmatch(relative) or any(
                part in (".", "..") for part in PurePosixPath(relative).parts
            ):
                raise PublicError("workflow bundle contains an invalid relative path")
            if relative in reserved:
                raise PublicError("workflow bundle collides with a reserved support file")
            files.append((candidate, relative))
            if len(files) > 10_000:
                raise PublicError("workflow bundle contains too many files")
    return sorted(files, key=lambda item: item[1])


def workflow_inventory(
    files: Sequence[tuple[Path, str]],
    execution: dict[str, object],
) -> tuple[list[dict[str, object]], str]:
    inventory = [
        {
            "path": relative,
            "sizeBytes": path.stat().st_size,
            "sha256": hash_file(path),
        }
        for path, relative in files
    ]
    encoded = json.dumps(
        {"execution": execution, "files": inventory},
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return inventory, hashlib.sha256(encoded).hexdigest()


def sealed_workflow_script(approval_sha256: str, inventory_sha256: str) -> str:
    return f'''#!/usr/bin/env bash
set -euo pipefail
script_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd -P)
inventory="$script_dir/approval-inventory.json"
expected_inventory_sha="{inventory_sha256}"
actual_inventory_sha=$(sha256sum "$inventory" | awk '{{print $1}}')
if [[ "$actual_inventory_sha" != "$expected_inventory_sha" ]]; then
  echo "workflow approval inventory changed" >&2
  exit 2
fi
python3 - "$inventory" "$script_dir" <<'PY'
from pathlib import Path
import hashlib
import json
import sys

inventory = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
root = Path(sys.argv[2]).resolve()
approved = set()
for item in inventory["files"]:
    path = root.joinpath(*Path(item["path"]).parts)
    approved.add(item["path"])
    if not path.is_file() or path.is_symlink() or path.stat().st_size != item["sizeBytes"]:
        raise SystemExit("workflow approval input is unavailable")
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(4 * 1024 * 1024), b""):
            digest.update(chunk)
    if digest.hexdigest() != item["sha256"]:
        raise SystemExit("workflow approval input changed")
for path in root.rglob("*"):
    if path.is_symlink():
        raise SystemExit("workflow input contains a symbolic link")
    if path.is_file():
        relative = path.relative_to(root).as_posix()
        if relative not in approved and relative not in {"approval-inventory.json", "run-workflow.sh"}:
            raise SystemExit("workflow input contains an unapproved file")
PY
export OPENCLAW_WORKFLOW_APPROVAL_SHA256="{approval_sha256}"
exec python3 "$script_dir/workflow_runner.py" "$script_dir/workflow.json"
'''


def prepare_workflow(
    args: argparse.Namespace,
    *,
    expected_approval_sha256: str | None = None,
) -> dict[str, object]:
    job = validate_job(args.job)
    bundle_input = Path(args.bundle).expanduser()
    if bundle_input.is_symlink():
        raise PublicError("workflow bundle must be an existing directory")
    bundle = bundle_input.resolve()
    files = workflow_bundle_files(bundle)
    manifest_path = bundle / "workflow.json"
    if not manifest_path.is_file() or manifest_path.is_symlink():
        raise PublicError("workflow bundle must contain workflow.json")
    try:
        manifest = workflow_runner.validate_manifest(
            json.loads(manifest_path.read_text(encoding="utf-8"))
        )
    except (OSError, UnicodeError, json.JSONDecodeError, workflow_runner.WorkflowError) as error:
        raise PublicError(str(error)) from error
    if manifest["job"] != job:
        raise PublicError("workflow manifest job does not match --job")
    dependencies = sorted({validate_job(value) for value in args.depends_on})
    if job in dependencies:
        raise PublicError("a job cannot depend on itself")
    resources = sorted({validate_resource(value) for value in args.reserve})
    if not resources:
        raise PublicError("at least one resource reservation is required")
    execution: dict[str, object] = {
        "job": job,
        "owner": manifest["owner"],
        "externalDependencies": dependencies,
        "resources": resources,
    }
    file_map = {relative: path for path, relative in files}
    for phase in manifest["phases"]:
        if phase["name"] == "preflight":
            continue
        if phase["script"] not in file_map:
            raise PublicError("workflow phase script is missing from the bundle")
    for support in SUPPORT_FILES:
        if not support.is_file() or support.is_symlink():
            raise PublicError("remote-splat workflow support bundle is incomplete")
        files.append((support, support.name))
    files = sorted(files, key=lambda item: item[1])
    inventory, approval_sha256 = workflow_inventory(files, execution)
    approval_payload = {
        "schemaVersion": 1,
        "approvalSha256": approval_sha256,
        "execution": execution,
        "files": inventory,
    }
    inventory_text = json.dumps(approval_payload, indent=2, sort_keys=True) + "\n"
    inventory_sha256 = hashlib.sha256(inventory_text.encode("utf-8")).hexdigest()
    seal_text = sealed_workflow_script(approval_sha256, inventory_sha256)
    seal_sha256 = hashlib.sha256(seal_text.encode("utf-8")).hexdigest()
    result: dict[str, object] = {
        "ok": True,
        "job": job,
        "owner": manifest["owner"],
        "externalDependencies": dependencies,
        "resources": resources,
        "approvalSha256": approval_sha256,
        "sealedRunnerSha256": seal_sha256,
        "fileCount": len(inventory),
        "sizeBytes": sum(int(item["sizeBytes"]) for item in inventory),
        "dryRun": args.dry_run,
        "runPerformed": False,
    }
    if expected_approval_sha256 is not None and approval_sha256 != expected_approval_sha256:
        raise PublicError("workflow bundle changed after approval")
    if args.dry_run:
        return result
    payload = run_desktop("stage", ["--job", job, "--source", str(bundle)])
    assert payload is not None
    for support in SUPPORT_FILES:
        payload = run_desktop("stage", ["--job", job, "--source", str(support)])
        assert payload is not None
    with tempfile.TemporaryDirectory(prefix="remote-splat-seal-") as temporary_directory:
        temporary = Path(temporary_directory)
        (temporary / "approval-inventory.json").write_text(inventory_text, encoding="utf-8")
        seal = temporary / "run-workflow.sh"
        seal.write_text(seal_text, encoding="utf-8")
        seal.chmod(0o700)
        payload = run_desktop("stage", ["--job", job, "--source", str(temporary)])
        assert payload is not None
    plan = run_desktop("plan-run", ["--job", job, "--script", "run-workflow.sh"])
    assert plan is not None
    if plan.get("sha256") != seal_sha256:
        raise PublicError("staged workflow seal hash changed")
    result["canaryRequired"] = True
    result["plan"] = plan
    return result


def command_start_plan(args: argparse.Namespace) -> None:
    emit(prepare_workflow(args))


def command_start(args: argparse.Namespace) -> None:
    if args.dry_run:
        emit(prepare_workflow(args))
        return
    if args.approved_workflow_sha256 is None:
        raise PublicError("start requires --approved-workflow-sha256 after a dry run")
    approved_workflow = validate_sha256(args.approved_workflow_sha256)
    result = prepare_workflow(args, expected_approval_sha256=approved_workflow)
    owner = validate_owner(str(result["owner"]))
    dependencies = list(result["externalDependencies"])
    resources = list(result["resources"])
    payload = run_desktop(
        "run",
        [
            "--job",
            validate_job(args.job),
            "--script",
            "run-workflow.sh",
            "--approved-sha256",
            str(result["sealedRunnerSha256"]),
            "--owner",
            owner,
            *[item for dependency in dependencies for item in ("--depends-on", dependency)],
            *[item for resource in resources for item in ("--reserve", resource)],
        ],
    )
    assert payload is not None
    if payload.get("started") is not True:
        raise PublicError("desktop splat workflow did not start")
    result["runPerformed"] = True
    result["run"] = payload
    emit(result)


def command_inspect(args: argparse.Namespace) -> None:
    command_progress(args)


def command_cancel(args: argparse.Namespace) -> None:
    job = validate_job(args.job)
    owner = validate_owner(args.owner)
    payload = run_desktop("cancel", ["--job", job, "--owner", owner])
    assert payload is not None
    emit(payload)


def command_verify_release(args: argparse.Namespace) -> None:
    job = validate_job(args.job)
    owner = validate_owner(args.owner)
    approval = validate_sha256(args.approved_workflow_sha256)
    payload = run_desktop(
        "verify-release",
        [
            "--job",
            job,
            "--owner",
            owner,
            "--approved-workflow-sha256",
            approval,
            "--confirm-windows-processes-absent",
        ],
    )
    assert payload is not None
    emit(payload)


def command_retrieve(args: argparse.Namespace) -> None:
    command_fetch(args)


def command_inbox_stage(args: argparse.Namespace) -> None:
    """Send one large file directly to the desktop's private Taildrop inbox."""
    job = validate_job(args.job)
    input_path = Path(args.source).expanduser()
    if input_path.is_symlink():
        raise PublicError("inbox source must be a regular file")
    source = input_path.resolve()
    if not source.is_file() or source.stat().st_size <= 0:
        raise PublicError("inbox source must be a non-empty regular file")
    if not INBOX_NAME_RE.fullmatch(source.name):
        raise PublicError("inbox source name may contain only letters, numbers, dots, dashes, and underscores")

    inbox_name = f"openclaw-splat-{job}-{source.name}"
    payload: dict[str, object] = {
        "ok": True,
        "job": job,
        "source": source.name,
        "sizeBytes": source.stat().st_size,
        "sha256": hash_file(source),
        "transport": "taildrop",
        "inboxName": inbox_name,
        "requiresHashVerifiedIngest": True,
    }
    if args.dry_run:
        payload["dryRun"] = True
        emit(payload)
        return

    try:
        with source.open("rb") as input_handle:
            completed = subprocess.run(
                [
                    TAILSCALE,
                    "file",
                    "cp",
                    "--name",
                    inbox_name,
                    "--update-interval",
                    "5s",
                    "-",
                    TAILDROP_TARGET,
                ],
                stdin=input_handle,
                check=False,
                timeout=24 * 60 * 60,
            )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise PublicError("desktop Taildrop transfer is unavailable") from error
    if completed.returncode != 0:
        raise PublicError("desktop Taildrop transfer failed")
    emit(payload)


def validate_workstation_video(value: str) -> PurePosixPath:
    source = PurePosixPath(value)
    if (
        not source.is_absolute()
        or source.parent != WORKSTATION_DOWNLOADS
        or not INBOX_NAME_RE.fullmatch(source.name)
        or source.suffix.casefold() not in video_prep.VIDEO_SUFFIXES
    ):
        raise PublicError("workstation source must be a supported video directly inside Downloads")
    return source


def run_workstation(arguments: Sequence[str], *, timeout: int = 120) -> str:
    try:
        completed = subprocess.run(
            [
                WORKSTATION_SSH,
                "-o",
                "BatchMode=yes",
                "-o",
                "ConnectTimeout=8",
                WORKSTATION_HOST,
                shlex.join(arguments),
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise PublicError("Dylan's workstation is unavailable") from error
    if completed.returncode != 0:
        raise PublicError("workstation splat operation failed")
    if len(completed.stdout.encode("utf-8")) > MAX_CAPTURE_BYTES:
        raise PublicError("workstation response exceeded the safety limit")
    return completed.stdout


def workstation_metadata(source: PurePosixPath) -> tuple[int, str]:
    size_output = run_workstation(["/usr/bin/stat", "-f", "%z", str(source)])
    digest_output = run_workstation(["/usr/bin/shasum", "-a", "256", str(source)], timeout=3600)
    try:
        size = int(size_output.strip())
        digest = digest_output.split(maxsplit=1)[0].casefold()
    except (ValueError, IndexError) as error:
        raise PublicError("workstation video metadata is invalid") from error
    if size <= 0 or not SHA256_RE.fullmatch(digest):
        raise PublicError("workstation video metadata is invalid")
    return size, digest


def workstation_tailscale_path() -> str:
    for candidate in dict.fromkeys(WORKSTATION_TAILSCALE_CANDIDATES):
        try:
            run_workstation(["/usr/bin/test", "-x", candidate], timeout=15)
        except PublicError:
            continue
        return candidate
    raise PublicError("Tailscale CLI is unavailable on Dylan's workstation")


def relay_workstation_taildrop(
    source: PurePosixPath,
    *,
    inbox_name: str,
) -> None:
    ssh_command = [
        WORKSTATION_SSH,
        "-o",
        "BatchMode=yes",
        "-o",
        "ConnectTimeout=8",
        WORKSTATION_HOST,
        shlex.join(["/bin/cat", str(source)]),
    ]
    try:
        producer = subprocess.Popen(
            ssh_command,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
        )
    except OSError as error:
        raise PublicError("Dylan's workstation is unavailable") from error
    assert producer.stdout is not None
    try:
        completed = subprocess.run(
            [
                TAILSCALE,
                "file",
                "cp",
                "--name",
                inbox_name,
                "--update-interval",
                "5s",
                "-",
                TAILDROP_TARGET,
            ],
            stdin=producer.stdout,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=24 * 60 * 60,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise PublicError("workstation relay transfer failed") from error
    finally:
        producer.stdout.close()
        try:
            producer.wait(timeout=30)
        except subprocess.TimeoutExpired:
            producer.terminate()
            try:
                producer.wait(timeout=10)
            except subprocess.TimeoutExpired:
                producer.kill()
                producer.wait(timeout=10)
    if completed.returncode != 0 or producer.returncode != 0:
        raise PublicError("workstation relay transfer failed")


def command_workstation_inbox_stage(args: argparse.Namespace) -> None:
    """Send one workstation video directly to the desktop Taildrop inbox."""
    job = validate_job(args.job)
    source = validate_workstation_video(args.source)
    size, digest = workstation_metadata(source)
    inbox_name = f"openclaw-splat-{job}-{source.name}"
    payload: dict[str, object] = {
        "ok": True,
        "job": job,
        "source": source.name,
        "sizeBytes": size,
        "sha256": digest,
        "transport": "workstation-taildrop",
        "inboxName": inbox_name,
        "requiresHashVerifiedIngest": True,
    }
    if args.dry_run:
        payload["dryRun"] = True
        emit(payload)
        return

    try:
        tailscale = workstation_tailscale_path()
    except PublicError:
        payload["transport"] = "workstation-relay-taildrop"
        relay_workstation_taildrop(source, inbox_name=inbox_name)
    else:
        run_workstation(
            [
                tailscale,
                "file",
                "cp",
                "--name",
                inbox_name,
                "--update-interval",
                "5s",
                str(source),
                TAILDROP_TARGET,
            ],
            timeout=24 * 60 * 60,
        )
    final_size, final_digest = workstation_metadata(source)
    if (final_size, final_digest) != (size, digest):
        raise PublicError("workstation video changed during transfer; remote ingest is not approved")
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
    owner = validate_owner(args.owner)
    dependencies = sorted({validate_job(value) for value in args.depends_on})
    if job in dependencies:
        raise PublicError("a job cannot depend on itself")
    resources = sorted({validate_resource(value) for value in args.reserve})
    if not resources:
        raise PublicError("at least one resource reservation is required")
    arguments = [
        "--job",
        job,
        "--script",
        script,
        "--approved-sha256",
        approved,
        "--owner",
        owner,
    ]
    for dependency in dependencies:
        arguments.extend(("--depends-on", dependency))
    for resource in resources:
        arguments.extend(("--reserve", resource))
    payload = run_desktop(
        "run",
        arguments,
    )
    assert payload is not None
    emit(payload)


def progress_receipt(payload: dict[str, object]) -> dict[str, object]:
    state = payload.get("state")
    receipt: dict[str, object] = {
        "state": state if isinstance(state, str) else "unknown",
        "reported": False,
    }
    tail = payload.get("logTail")
    if not isinstance(tail, list):
        return receipt

    for line in reversed(tail):
        if not isinstance(line, str) or not line.startswith(PROGRESS_PREFIX):
            continue
        encoded = line.removeprefix(PROGRESS_PREFIX)
        if not encoded or len(encoded.encode("utf-8")) > 2048:
            continue
        try:
            report = json.loads(encoded)
        except (json.JSONDecodeError, UnicodeError):
            continue
        if not isinstance(report, dict):
            continue

        phase = report.get("phase")
        if not isinstance(phase, str) or not PROGRESS_NAME_RE.fullmatch(phase):
            continue
        candidate: dict[str, object] = {
            "state": receipt["state"],
            "reported": True,
            "phase": phase,
        }

        completed = report.get("completed")
        total = report.get("total")
        if (
            isinstance(completed, int)
            and not isinstance(completed, bool)
            and isinstance(total, int)
            and not isinstance(total, bool)
            and 0 <= completed <= total
            and total > 0
        ):
            candidate["completed"] = completed
            candidate["total"] = total
            candidate["percent"] = round(completed * 100 / total, 1)

        unit = report.get("unit")
        if isinstance(unit, str) and PROGRESS_UNIT_RE.fullmatch(unit):
            candidate["unit"] = unit

        eta_seconds = report.get("etaSeconds")
        if (
            isinstance(eta_seconds, int)
            and not isinstance(eta_seconds, bool)
            and 0 <= eta_seconds <= 30 * 24 * 60 * 60
        ):
            candidate["etaSeconds"] = eta_seconds

        message = report.get("message")
        if (
            isinstance(message, str)
            and len(message) <= 160
            and "\n" not in message
            and "\r" not in message
        ):
            candidate["message"] = message

        checkpoint = report.get("checkpoint")
        if isinstance(checkpoint, str):
            step = checkpoint_step(checkpoint)
            if step is not None:
                candidate["checkpoint"] = {"name": checkpoint, "step": step}
        return candidate
    return receipt


def checkpoint_step(value: str) -> int | None:
    if "/" in value or "\\" in value or len(value) > 160:
        return None
    match = CHECKPOINT_RE.fullmatch(value)
    if match is None:
        return None
    return int(match.group(1))


def process_receipt(payload: dict[str, object]) -> dict[str, object]:
    tail = payload.get("logTail")
    if not isinstance(tail, list):
        return {"reported": False}
    for line in reversed(tail):
        if not isinstance(line, str) or not line.startswith(PROCESS_PREFIX):
            continue
        encoded = line.removeprefix(PROCESS_PREFIX)
        if not encoded or len(encoded.encode("utf-8")) > 1024:
            continue
        try:
            report = json.loads(encoded)
        except (json.JSONDecodeError, UnicodeError):
            continue
        if not isinstance(report, dict):
            continue
        kind = report.get("kind")
        pid = report.get("pid")
        if (
            not isinstance(kind, str)
            or not RESOURCE_RE.fullmatch(kind)
            or not isinstance(pid, int)
            or isinstance(pid, bool)
            or not 1 <= pid <= 2**31 - 1
        ):
            continue
        return {"reported": True, "kind": kind, "pid": pid}
    return {"reported": False}


def command_progress(args: argparse.Namespace) -> None:
    job = validate_job(args.job)
    if not 1 <= args.lines <= 200:
        raise PublicError("lines must be between 1 and 200")
    payload = run_desktop("progress", ["--job", job, "--lines", str(args.lines)])
    assert payload is not None
    payload["progressReceipt"] = progress_receipt(payload)
    payload["processReceipt"] = process_receipt(payload)
    emit(payload)


def command_attach(args: argparse.Namespace) -> None:
    job = validate_job(args.job)
    run_desktop("attach", ["--job", job], interactive=True)


def command_fetch(args: argparse.Namespace) -> None:
    job = validate_job(args.job)
    artifact = validate_relative(args.artifact, suffixes=FETCH_SUFFIXES, label="artifact")
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
    if not path.is_file() or path.suffix.casefold() not in PUBLISH_SUFFIXES:
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


def command_video_probe(args: argparse.Namespace) -> None:
    try:
        source = video_prep.regular_video(args.source)
        emit(video_prep.probe_video(source))
    except video_prep.VideoPrepError as error:
        raise PublicError(str(error)) from error


def command_video_review(args: argparse.Namespace) -> None:
    try:
        emit(
            video_prep.review_videos(
                args.source,
                args.output,
                interval_seconds=args.interval_seconds,
                proxy=args.proxy,
                scene_suggestions=args.scene_suggestions,
            )
        )
    except video_prep.VideoPrepError as error:
        raise PublicError(str(error)) from error


def command_video_extract(args: argparse.Namespace) -> None:
    try:
        emit(video_prep.extract_manifest(args.manifest, args.output, dry_run=args.dry_run))
    except video_prep.VideoPrepError as error:
        raise PublicError(str(error)) from error


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    sub = result.add_subparsers(dest="command", required=True)

    workflow_plan = sub.add_parser(
        "workflow-plan",
        help="choose delivery, scheduling, and fallback policy before starting a run",
    )
    workflow_plan.add_argument("--job-prefix", required=True)
    workflow_plan.add_argument("--profile", choices=tuple(WORKFLOW_PROFILES), required=True)
    workflow_plan.add_argument("--target-registered-views", required=True, type=int)
    workflow_plan.add_argument("--plausible-registered-views", required=True, type=int)
    workflow_plan.add_argument("--minimum-points", required=True, type=int)
    workflow_plan.add_argument("--maximum-reprojection-error-px", required=True, type=float)
    workflow_plan.add_argument("--segment-floor", action="append", required=True)
    workflow_plan.set_defaults(func=command_workflow_plan)

    start_plan = sub.add_parser(
        "start-plan",
        help="validate and stage one sealed manifest-driven workflow",
    )
    start_plan.add_argument("--job", required=True)
    start_plan.add_argument("--bundle", required=True)
    start_plan.add_argument("--depends-on", action="append", default=[])
    start_plan.add_argument("--reserve", action="append", required=True)
    start_plan.add_argument("--dry-run", action="store_true")
    start_plan.set_defaults(func=command_start_plan)

    start = sub.add_parser(
        "start",
        help="plan or start one sealed manifest-driven workflow bundle",
    )
    start.add_argument("--job", required=True)
    start.add_argument("--bundle", required=True)
    start.add_argument("--approved-workflow-sha256")
    start.add_argument("--depends-on", action="append", default=[])
    start.add_argument("--reserve", action="append", required=True)
    start.add_argument("--dry-run", action="store_true")
    start.set_defaults(func=command_start)

    inspect = sub.add_parser("inspect", help="inspect workflow state without creating a job")
    inspect.add_argument("--job", required=True)
    inspect.add_argument("--lines", type=int, default=40)
    inspect.set_defaults(func=command_inspect)

    cancel = sub.add_parser("cancel", help="request cancellation of an owned workflow")
    cancel.add_argument("--job", required=True)
    cancel.add_argument("--owner", required=True)
    cancel.set_defaults(func=command_cancel)

    verify_release = sub.add_parser(
        "verify-release",
        help="record explicit human verification before releasing a Windows reservation",
    )
    verify_release.add_argument("--job", required=True)
    verify_release.add_argument("--owner", required=True)
    verify_release.add_argument("--approved-workflow-sha256", required=True)
    verify_release.add_argument(
        "--confirm-windows-processes-absent", action="store_true", required=True
    )
    verify_release.set_defaults(func=command_verify_release)

    retrieve = sub.add_parser("retrieve", help="retrieve one verified workflow artifact")
    retrieve.add_argument("--job", required=True)
    retrieve.add_argument("--artifact", required=True)
    retrieve.add_argument("--destination", required=True)
    retrieve.set_defaults(func=command_retrieve)

    status = sub.add_parser("status", help="check bridge, GPU, tools, disk, and sessions")
    status.set_defaults(func=command_status)

    stage = sub.add_parser("stage", help="copy one source into a managed remote job")
    stage.add_argument("--job", required=True)
    stage.add_argument("--source", required=True)
    stage.add_argument("--dry-run", action="store_true")
    stage.set_defaults(func=command_stage)

    preflight = sub.add_parser(
        "preflight-plan",
        help="stage the shared lightweight canary/support bundle and return its approval hash",
    )
    preflight.add_argument("--job", required=True)
    preflight.add_argument("--owner", default="sol")
    preflight.add_argument("--depends-on", action="append", default=[])
    preflight.add_argument("--reserve", action="append", default=[])
    preflight.add_argument("--dry-run", action="store_true")
    preflight.set_defaults(func=command_preflight_plan)

    inbox_stage = sub.add_parser(
        "inbox-stage",
        help="send one large file directly to the desktop Taildrop inbox for hash-verified ingest",
    )
    inbox_stage.add_argument("--job", required=True)
    inbox_stage.add_argument("--source", required=True)
    inbox_stage.add_argument("--dry-run", action="store_true")
    inbox_stage.set_defaults(func=command_inbox_stage)

    workstation_inbox_stage = sub.add_parser(
        "workstation-inbox-stage",
        help="send one Downloads video directly from Dylan's Mac to the desktop inbox",
    )
    workstation_inbox_stage.add_argument("--job", required=True)
    workstation_inbox_stage.add_argument("--source", required=True)
    workstation_inbox_stage.add_argument("--dry-run", action="store_true")
    workstation_inbox_stage.set_defaults(func=command_workstation_inbox_stage)

    plan = sub.add_parser("plan-run", help="inspect a staged script and return its approval hash")
    plan.add_argument("--job", required=True)
    plan.add_argument("--script", required=True)
    plan.set_defaults(func=command_plan_run)

    run = sub.add_parser("run", help="start an exact hash-approved script in a job tmux session")
    run.add_argument("--job", required=True)
    run.add_argument("--script", required=True)
    run.add_argument("--approved-sha256", required=True)
    run.add_argument("--owner", required=True)
    run.add_argument("--depends-on", action="append", default=[])
    run.add_argument("--reserve", action="append", required=True)
    run.set_defaults(func=command_run)

    progress = sub.add_parser("progress", help="read bounded state and log output for one job")
    progress.add_argument("--job", required=True)
    progress.add_argument("--lines", type=int, default=40)
    progress.set_defaults(func=command_progress)

    attach = sub.add_parser("attach", help="interactively attach to one job's tmux session")
    attach.add_argument("--job", required=True)
    attach.set_defaults(func=command_attach)

    fetch = sub.add_parser("fetch", help="retrieve and verify one .sog, .ply, or .webp output")
    fetch.add_argument("--job", required=True)
    fetch.add_argument("--artifact", required=True)
    fetch.add_argument("--destination", required=True)
    fetch.set_defaults(func=command_fetch)

    publish = sub.add_parser("publish-plan", help="hash a local artifact without uploading it")
    publish.add_argument("--file", required=True)
    publish.set_defaults(func=command_publish_plan)

    video_probe = sub.add_parser("video-probe", help="inspect one source video without modifying it")
    video_probe.add_argument("--source", required=True)
    video_probe.set_defaults(func=command_video_probe)

    video_review = sub.add_parser(
        "video-review",
        help="create timecoded thumbnails, an optional proxy, and a segment-manifest template",
    )
    video_review.add_argument("--source", action="append", required=True)
    video_review.add_argument("--output", required=True)
    video_review.add_argument("--interval-seconds", type=float, default=30.0)
    video_review.add_argument("--proxy", action="store_true")
    video_review.add_argument("--scene-suggestions", action="store_true")
    video_review.set_defaults(func=command_video_review)

    video_extract = sub.add_parser(
        "video-extract",
        help="validate a named segment manifest and extract clips plus modeling frames",
    )
    video_extract.add_argument("--manifest", required=True)
    video_extract.add_argument("--output", required=True)
    video_extract.add_argument("--dry-run", action="store_true")
    video_extract.set_defaults(func=command_video_extract)
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
