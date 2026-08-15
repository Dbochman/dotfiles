#!/usr/bin/env python3
"""Protected observation-only journal for legacy vacancy actions.

This helper never invokes a device command, changes canonical presence, or
controls the event bus.  It records the intent and observed terminal outcome of
commands that ``vacancy-actions.sh`` already owns.  Journal failure must remain
fail-open for those legacy actions.
"""

from __future__ import annotations

import argparse
import contextlib
import fcntl
import hashlib
import json
import os
import re
import secrets
import stat
import tempfile
from collections.abc import Callable, Mapping, Sequence
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any


SCHEMA_VERSION = 1
MAX_FILE_BYTES = 256 * 1024
MAX_RUN_FILES = 512
STALE_AFTER = timedelta(minutes=15)
SITES = frozenset({"cabin", "crosstown"})
OUTCOMES = frozenset(
    {"state_confirmed", "command_accepted", "failed", "skipped", "outcome_unknown"}
)
VERIFICATIONS = frozenset({"command_exit", "state_confirmed", "policy_decision", "none"})
REASON_CODES = frozenset(
    {
        "completed",
        "already_satisfied",
        "command_failed",
        "verification_failed",
        "snoozed",
        "policy_invalid_fail_closed",
        "interrupted",
        "ack_unverified",
    }
)
TARGET_ACTIONS: Mapping[str, Mapping[str, str]] = {
    "crosstown": {
        "all_lights": "turn_off",
        "central_hvac": "enable_eco",
        "cielo_bedroom": "turn_off",
        "cielo_office": "turn_off",
        "cielo_living_room": "turn_off",
        "front_door_lock": "lock",
        "crosstown_roombas": "start_cleaning",
    },
    "cabin": {
        "all_lights": "turn_off",
        "central_hvac": "enable_eco",
        "floomba": "start_cleaning",
        "philly": "start_cleaning",
    },
}
ID_RE = re.compile(r"^(?:cycle|run|attempt)_[0-9a-f]{32}$")
HASH_RE = re.compile(r"^[0-9a-f]{64}$")


class JournalError(Exception):
    """Expected safe journal failure."""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds").replace(
        "+00:00", "Z"
    )


def parse_time(value: object) -> datetime:
    if not isinstance(value, str) or not value:
        raise JournalError("timestamp_invalid")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise JournalError("timestamp_invalid") from exc
    if parsed.tzinfo is None:
        raise JournalError("timestamp_invalid")
    return parsed.astimezone(timezone.utc)


def canonical_json(value: object) -> bytes:
    try:
        encoded = json.dumps(
            value,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise JournalError("json_invalid") from exc
    return encoded


def state_hash(value: object) -> str:
    return hashlib.sha256(canonical_json(value)).hexdigest()


def require_exact_keys(value: object, expected: frozenset[str], code: str) -> dict[str, Any]:
    if not isinstance(value, dict) or frozenset(value) != expected:
        raise JournalError(code)
    return value


def safe_id(prefix: str) -> str:
    return f"{prefix}_{secrets.token_hex(16)}"


class VacancyActionJournal:
    def __init__(
        self,
        *,
        state_file: Path,
        producer_state_file: Path,
        marker_dir: Path,
        root: Path,
        clock: Callable[[], str] = utc_now,
    ) -> None:
        self.state_file = state_file
        self.producer_state_file = producer_state_file
        self.marker_dir = marker_dir
        self.root = root
        self.runs_dir = root / "runs"
        self.cycles_dir = root / "cycles"
        self.lock_file = root / "journal.lock"
        self.clock = clock

    @classmethod
    def defaults(cls) -> "VacancyActionJournal":
        home = Path.home()
        presence = home / ".openclaw/presence"
        return cls(
            state_file=presence / "state.json",
            producer_state_file=(
                presence / "home-events-outbox/producer-state.json"
            ),
            marker_dir=presence / "vacancy-dispatched",
            root=home / ".openclaw/vacancy-actions/journal",
        )

    def _ensure_layout(self) -> None:
        for directory in (
            self.root.parent,
            self.root,
            self.runs_dir,
            self.cycles_dir,
        ):
            directory.mkdir(mode=0o700, parents=True, exist_ok=True)
            metadata = directory.lstat()
            if not stat.S_ISDIR(metadata.st_mode) or stat.S_ISLNK(metadata.st_mode):
                raise JournalError("journal_directory_unsafe")
            if metadata.st_uid != os.getuid() or stat.S_IMODE(metadata.st_mode) != 0o700:
                raise JournalError("journal_directory_unsafe")
        if not self.lock_file.exists():
            try:
                descriptor = os.open(
                    self.lock_file,
                    os.O_WRONLY
                    | os.O_CREAT
                    | os.O_EXCL
                    | getattr(os, "O_NOFOLLOW", 0),
                    0o600,
                )
            except FileExistsError:
                # Another first-use process won the create race. The strict
                # validation below still rejects a symlink or unsafe file.
                pass
            else:
                os.close(descriptor)
        self._validate_private_file(self.lock_file, allow_empty=True)

    @contextlib.contextmanager
    def _locked(self):
        self._ensure_layout()
        descriptor = os.open(
            self.lock_file,
            os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0),
        )
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX)
            yield
        finally:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
            os.close(descriptor)

    @staticmethod
    def _validate_private_file(path: Path, *, allow_empty: bool = False) -> os.stat_result:
        try:
            metadata = path.lstat()
        except OSError as exc:
            raise JournalError("protected_file_unavailable") from exc
        if (
            not stat.S_ISREG(metadata.st_mode)
            or stat.S_ISLNK(metadata.st_mode)
            or metadata.st_uid != os.getuid()
            or stat.S_IMODE(metadata.st_mode) != 0o600
            or metadata.st_size > MAX_FILE_BYTES
            or (not allow_empty and metadata.st_size <= 0)
        ):
            raise JournalError("protected_file_unsafe")
        return metadata

    def _read_json(self, path: Path) -> Any:
        self._validate_private_file(path)
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise JournalError("protected_json_invalid") from exc

    def _atomic_write(self, path: Path, value: object) -> None:
        encoded = canonical_json(value) + b"\n"
        if len(encoded) > MAX_FILE_BYTES:
            raise JournalError("journal_record_oversize")
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{path.name}.", dir=path.parent
        )
        temporary = Path(temporary_name)
        try:
            os.fchmod(descriptor, 0o600)
            with os.fdopen(descriptor, "wb", closefd=True) as handle:
                handle.write(encoded)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, path)
            directory_descriptor = os.open(path.parent, os.O_RDONLY)
            try:
                os.fsync(directory_descriptor)
            finally:
                os.close(directory_descriptor)
        except Exception:
            try:
                os.close(descriptor)
            except OSError:
                pass
            try:
                temporary.unlink()
            except OSError:
                pass
            raise

    def _validated_presence(self, site: str) -> tuple[dict[str, Any], dict[str, Any]]:
        if site not in SITES:
            raise JournalError("site_invalid")
        canonical = self._read_json(self.state_file)
        producer = require_exact_keys(
            self._read_json(self.producer_state_file),
            frozenset(
                {
                    "schema_version",
                    "sequence",
                    "observation_id",
                    "state_hash",
                    "evaluated_at",
                }
            ),
            "producer_state_invalid",
        )
        if (
            producer["schema_version"] != 1
            or not isinstance(producer["sequence"], int)
            or isinstance(producer["sequence"], bool)
            or producer["sequence"] < 1
            or not isinstance(producer["observation_id"], str)
            or HASH_RE.fullmatch(producer["observation_id"]) is None
            or not isinstance(producer["state_hash"], str)
            or HASH_RE.fullmatch(producer["state_hash"]) is None
        ):
            raise JournalError("producer_state_invalid")
        parse_time(producer["evaluated_at"])
        site_state = canonical.get(site) if isinstance(canonical, dict) else None
        if not isinstance(site_state, dict):
            raise JournalError("canonical_state_invalid")
        if (
            canonical.get("timestamp") != producer["evaluated_at"]
            or state_hash(canonical) != producer["state_hash"]
        ):
            raise JournalError("presence_state_mismatch")
        if site_state.get("occupancy") != "confirmed_vacant" or site_state.get("fresh") is not True:
            raise JournalError("site_not_confirmed_vacant")
        state_changed_at = site_state.get("stateChangedAt")
        parse_time(state_changed_at)
        return canonical, producer

    def _run_path(self, run_id: str) -> Path:
        if ID_RE.fullmatch(run_id) is None or not run_id.startswith("run_"):
            raise JournalError("run_id_invalid")
        return self.runs_dir / f"{run_id}.json"

    def _read_run(self, run_id: str) -> dict[str, Any]:
        value = require_exact_keys(
            self._read_json(self._run_path(run_id)),
            frozenset(
                {
                    "schema_version",
                    "site",
                    "cycle_id",
                    "run_id",
                    "trigger_state_hash",
                    "triggered_at",
                    "started_at",
                    "completed_at",
                    "state",
                    "marker_committed",
                    "actions",
                }
            ),
            "run_record_invalid",
        )
        if (
            value["schema_version"] != SCHEMA_VERSION
            or value["site"] not in SITES
            or value["run_id"] != run_id
            or ID_RE.fullmatch(value["cycle_id"]) is None
            or not value["cycle_id"].startswith("cycle_")
            or not isinstance(value["trigger_state_hash"], str)
            or HASH_RE.fullmatch(value["trigger_state_hash"]) is None
            or value["state"] not in {"in_progress", "complete", "interrupted"}
            or not isinstance(value["marker_committed"], bool)
            or not isinstance(value["actions"], list)
            or len(value["actions"]) > 32
        ):
            raise JournalError("run_record_invalid")
        parse_time(value["triggered_at"])
        parse_time(value["started_at"])
        if value["state"] == "in_progress":
            if value["completed_at"] is not None or value["marker_committed"]:
                raise JournalError("run_record_invalid")
        elif value["completed_at"] is None:
            raise JournalError("run_record_invalid")
        else:
            parse_time(value["completed_at"])
            if (value["state"] == "complete") != value["marker_committed"]:
                raise JournalError("run_record_invalid")
        seen_targets: set[str] = set()
        seen_attempts: set[str] = set()
        for action in value["actions"]:
            action = require_exact_keys(
                action,
                frozenset(
                    {
                        "attempt_id",
                        "target",
                        "action",
                        "state",
                        "outcome",
                        "verification",
                        "reason_code",
                        "not_before",
                        "not_after",
                    }
                ),
                "action_record_invalid",
            )
            attempt_id = action["attempt_id"]
            target = action["target"]
            expected_action = TARGET_ACTIONS[value["site"]].get(target)
            if (
                not isinstance(attempt_id, str)
                or ID_RE.fullmatch(attempt_id) is None
                or not attempt_id.startswith("attempt_")
                or attempt_id in seen_attempts
                or not isinstance(target, str)
                or target in seen_targets
                or expected_action is None
                or action["action"] != expected_action
                or action["state"] not in {"in_progress", "terminal"}
            ):
                raise JournalError("action_record_invalid")
            parse_time(action["not_before"])
            if action["state"] == "in_progress":
                if any(
                    action[key] is not None
                    for key in ("outcome", "verification", "reason_code", "not_after")
                ):
                    raise JournalError("action_record_invalid")
            elif (
                action["outcome"] not in OUTCOMES
                or action["verification"] not in VERIFICATIONS
                or action["reason_code"] not in REASON_CODES
                or action["not_after"] is None
            ):
                raise JournalError("action_record_invalid")
            else:
                parse_time(action["not_after"])
            seen_attempts.add(attempt_id)
            seen_targets.add(target)
        return value

    def begin_run(self, site: str) -> Mapping[str, Any]:
        with self._locked():
            self._recover_stale_locked()
            if len(tuple(self.runs_dir.glob("run_*.json"))) >= MAX_RUN_FILES:
                raise JournalError("journal_capacity_exhausted")
            canonical, producer = self._validated_presence(site)
            changed_at = canonical[site]["stateChangedAt"]
            cycle_path = self.cycles_dir / f"{site}.json"
            cycle_id: str
            if cycle_path.exists():
                cycle = require_exact_keys(
                    self._read_json(cycle_path),
                    frozenset(
                        {"schema_version", "site", "state_changed_at", "cycle_id"}
                    ),
                    "cycle_record_invalid",
                )
                if (
                    cycle["schema_version"] != SCHEMA_VERSION
                    or cycle["site"] != site
                    or not isinstance(cycle["cycle_id"], str)
                    or ID_RE.fullmatch(cycle["cycle_id"]) is None
                    or not cycle["cycle_id"].startswith("cycle_")
                ):
                    raise JournalError("cycle_record_invalid")
                parse_time(cycle["state_changed_at"])
                if cycle["state_changed_at"] == changed_at:
                    cycle_id = cycle["cycle_id"]
                else:
                    cycle_id = safe_id("cycle")
            else:
                cycle_id = safe_id("cycle")
            self._atomic_write(
                cycle_path,
                {
                    "schema_version": SCHEMA_VERSION,
                    "site": site,
                    "state_changed_at": changed_at,
                    "cycle_id": cycle_id,
                },
            )
            now = self.clock()
            parse_time(now)
            run_id = safe_id("run")
            run = {
                "schema_version": SCHEMA_VERSION,
                "site": site,
                "cycle_id": cycle_id,
                "run_id": run_id,
                "trigger_state_hash": producer["state_hash"],
                "triggered_at": producer["evaluated_at"],
                "started_at": now,
                "completed_at": None,
                "state": "in_progress",
                "marker_committed": False,
                "actions": [],
            }
            self._atomic_write(self._run_path(run_id), run)
            return {"ok": True, "run_id": run_id, "cycle_id": cycle_id}

    def begin_action(self, run_id: str, target: str, action: str) -> Mapping[str, Any]:
        with self._locked():
            run = self._read_run(run_id)
            if run["state"] != "in_progress":
                raise JournalError("run_not_active")
            expected = TARGET_ACTIONS[run["site"]].get(target)
            if expected is None or expected != action:
                raise JournalError("action_target_invalid")
            if any(item.get("target") == target for item in run["actions"]):
                raise JournalError("action_target_duplicate")
            now = self.clock()
            parse_time(now)
            attempt_id = safe_id("attempt")
            run["actions"].append(
                {
                    "attempt_id": attempt_id,
                    "target": target,
                    "action": action,
                    "state": "in_progress",
                    "outcome": None,
                    "verification": None,
                    "reason_code": None,
                    "not_before": now,
                    "not_after": None,
                }
            )
            self._atomic_write(self._run_path(run_id), run)
            return {"ok": True, "attempt_id": attempt_id}

    def finish_action(
        self,
        run_id: str,
        attempt_id: str,
        outcome: str,
        verification: str,
        reason_code: str,
    ) -> Mapping[str, Any]:
        if outcome not in OUTCOMES:
            raise JournalError("outcome_invalid")
        if verification not in VERIFICATIONS:
            raise JournalError("verification_invalid")
        if reason_code not in REASON_CODES:
            raise JournalError("reason_code_invalid")
        if ID_RE.fullmatch(attempt_id) is None or not attempt_id.startswith("attempt_"):
            raise JournalError("attempt_id_invalid")
        with self._locked():
            run = self._read_run(run_id)
            if run["state"] != "in_progress":
                raise JournalError("run_not_active")
            matches = [item for item in run["actions"] if item.get("attempt_id") == attempt_id]
            if len(matches) != 1 or matches[0].get("state") != "in_progress":
                raise JournalError("attempt_not_active")
            now = self.clock()
            parse_time(now)
            matches[0].update(
                {
                    "state": "terminal",
                    "outcome": outcome,
                    "verification": verification,
                    "reason_code": reason_code,
                    "not_after": now,
                }
            )
            self._atomic_write(self._run_path(run_id), run)
            return {"ok": True, "outcome": outcome}

    def complete_run(self, run_id: str) -> Mapping[str, Any]:
        with self._locked():
            run = self._read_run(run_id)
            if run["state"] != "in_progress":
                raise JournalError("run_not_active")
            if any(item.get("state") != "terminal" for item in run["actions"]):
                raise JournalError("run_has_pending_actions")
            marker = self.marker_dir / run["site"]
            metadata = marker.lstat()
            if (
                not stat.S_ISREG(metadata.st_mode)
                or stat.S_ISLNK(metadata.st_mode)
                or metadata.st_uid != os.getuid()
            ):
                raise JournalError("vacancy_marker_invalid")
            now = self.clock()
            parse_time(now)
            run.update(
                {
                    "state": "complete",
                    "marker_committed": True,
                    "completed_at": now,
                }
            )
            self._atomic_write(self._run_path(run_id), run)
            return {"ok": True, "state": "complete"}

    def _recover_stale_locked(self) -> Mapping[str, int]:
        now_text = self.clock()
        now = parse_time(now_text)
        recovered = 0
        for path in sorted(self.runs_dir.glob("run_*.json")):
            run_id = path.stem
            run = self._read_run(run_id)
            if run["state"] != "in_progress":
                continue
            if now - parse_time(run["started_at"]) < STALE_AFTER:
                continue
            marker = self.marker_dir / run["site"]
            marker_committed = False
            try:
                metadata = marker.lstat()
                marker_committed = (
                    stat.S_ISREG(metadata.st_mode)
                    and not stat.S_ISLNK(metadata.st_mode)
                    and metadata.st_uid == os.getuid()
                )
            except OSError:
                pass
            for action in run["actions"]:
                if action.get("state") == "in_progress":
                    action.update(
                        {
                            "state": "terminal",
                            "outcome": "outcome_unknown",
                            "verification": "none",
                            "reason_code": "interrupted",
                            "not_after": now_text,
                        }
                    )
            run.update(
                {
                    "state": "complete" if marker_committed else "interrupted",
                    "marker_committed": marker_committed,
                    "completed_at": now_text,
                }
            )
            self._atomic_write(path, run)
            recovered += 1
        return {"recovered": recovered}

    def recover_stale(self) -> Mapping[str, Any]:
        with self._locked():
            return {"ok": True, **self._recover_stale_locked()}

    def status(self) -> Mapping[str, Any]:
        with self._locked():
            counts = {"in_progress": 0, "complete": 0, "interrupted": 0}
            outcomes = {key: 0 for key in sorted(OUTCOMES)}
            for path in sorted(self.runs_dir.glob("run_*.json")):
                run = self._read_run(path.stem)
                counts[run["state"]] += 1
                for action in run["actions"]:
                    outcome = action.get("outcome")
                    if outcome in outcomes:
                        outcomes[outcome] += 1
            return {
                "ok": True,
                "schema_version": SCHEMA_VERSION,
                "runs": counts,
                "outcomes": outcomes,
                "capacity": {"used": sum(counts.values()), "maximum": MAX_RUN_FILES},
            }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    begin_run = commands.add_parser("begin-run")
    begin_run.add_argument("--site", required=True, choices=sorted(SITES))
    begin_action = commands.add_parser("begin-action")
    begin_action.add_argument("--run-id", required=True)
    begin_action.add_argument("--target", required=True)
    begin_action.add_argument("--action", required=True)
    finish = commands.add_parser("finish-action")
    finish.add_argument("--run-id", required=True)
    finish.add_argument("--attempt-id", required=True)
    finish.add_argument("--outcome", required=True, choices=sorted(OUTCOMES))
    finish.add_argument("--verification", required=True, choices=sorted(VERIFICATIONS))
    finish.add_argument("--reason-code", required=True, choices=sorted(REASON_CODES))
    complete = commands.add_parser("complete-run")
    complete.add_argument("--run-id", required=True)
    commands.add_parser("recover")
    commands.add_parser("status")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    os.umask(0o077)
    args = build_parser().parse_args(argv)
    journal = VacancyActionJournal.defaults()
    try:
        if args.command == "begin-run":
            result = journal.begin_run(args.site)
        elif args.command == "begin-action":
            result = journal.begin_action(args.run_id, args.target, args.action)
        elif args.command == "finish-action":
            result = journal.finish_action(
                args.run_id,
                args.attempt_id,
                args.outcome,
                args.verification,
                args.reason_code,
            )
        elif args.command == "complete-run":
            result = journal.complete_run(args.run_id)
        elif args.command == "recover":
            result = journal.recover_stale()
        else:
            result = journal.status()
    except (JournalError, OSError) as exc:
        code = exc.code if isinstance(exc, JournalError) else "journal_io_failed"
        print(json.dumps({"ok": False, "error": code}, sort_keys=True))
        return 1
    print(json.dumps(result, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
