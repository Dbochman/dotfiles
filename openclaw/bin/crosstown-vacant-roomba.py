#!/usr/bin/env python3
"""Run Crosstown Roombas once per local day while the house is vacant."""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
import re
import stat
import subprocess
import tempfile
from collections.abc import Callable, Mapping, Sequence
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo


SCHEMA_VERSION = 1
SITE = "crosstown"
LOCAL_TIMEZONE = ZoneInfo("America/New_York")
MAX_FILE_BYTES = 256 * 1024
MAX_PRESENCE_AGE = timedelta(minutes=30)
MAX_FUTURE_SKEW = timedelta(minutes=1)
RECENT_CAT_ACTIVITY = timedelta(hours=12)
MIN_START_BATTERY = 30
HISTORY_LIMIT = 100
ROBOT_ALIASES = ("roomba", "scoomba")
IDLE_PHASES = frozenset({"charge", "stop"})
ACTIVE_PHASES = frozenset({"run"})
CAT_ACTIVITY_ACTIONS = frozenset({"cat detected", "cat sensor interrupted"})
SOURCES = frozenset({"scheduled", "vacancy_transition"})
HASH_RE = re.compile(r"^[0-9a-f]{64}$")


class AutomationError(Exception):
    """A bounded, safe automation failure."""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def isoformat(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat(timespec="seconds").replace(
        "+00:00", "Z"
    )


def parse_time(value: object) -> datetime:
    if not isinstance(value, str) or not value:
        raise AutomationError("timestamp_invalid")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise AutomationError("timestamp_invalid") from exc
    if parsed.tzinfo is None:
        raise AutomationError("timestamp_invalid")
    return parsed.astimezone(timezone.utc)


def canonical_json(value: object) -> bytes:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise AutomationError("json_invalid") from exc


def state_hash(value: object) -> str:
    return hashlib.sha256(canonical_json(value)).hexdigest()


def default_command_runner(command: Sequence[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        list(command),
        check=False,
        capture_output=True,
        text=True,
        timeout=45,
    )


class CrosstownVacantRoomba:
    def __init__(
        self,
        *,
        home: Path | None = None,
        clock: Callable[[], datetime] = utc_now,
        command_runner: Callable[[Sequence[str]], subprocess.CompletedProcess[str]] = (
            default_command_runner
        ),
    ) -> None:
        self.home = home or Path.home()
        self.clock = clock
        self.command_runner = command_runner
        self.presence_dir = self.home / ".openclaw/presence"
        self.state_file = self.presence_dir / "state.json"
        self.producer_file = (
            self.presence_dir / "home-events-outbox/producer-state.json"
        )
        self.snooze_file = self.home / ".openclaw/dog-walk/snooze.json"
        self.root = self.home / ".openclaw/vacant-roomba/crosstown"
        self.runs_dir = self.root / "runs"
        self.latest_file = self.root / "latest-status.json"
        self.lock_file = self.root / "automation.lock"
        self.log_file = self.home / ".openclaw/logs/crosstown-vacant-roomba.log"

    @staticmethod
    def _validate_regular_file(
        path: Path,
        *,
        exact_mode: int | None = None,
        allow_missing: bool = False,
    ) -> os.stat_result | None:
        try:
            metadata = path.lstat()
        except FileNotFoundError:
            if allow_missing:
                return None
            raise AutomationError("protected_file_unavailable")
        except OSError as exc:
            raise AutomationError("protected_file_unavailable") from exc
        mode = stat.S_IMODE(metadata.st_mode)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or stat.S_ISLNK(metadata.st_mode)
            or metadata.st_uid != os.getuid()
            or metadata.st_size <= 0
            or metadata.st_size > MAX_FILE_BYTES
            or (exact_mode is not None and mode != exact_mode)
            or (exact_mode is None and mode & 0o022)
        ):
            raise AutomationError("protected_file_unsafe")
        return metadata

    def _read_json(
        self,
        path: Path,
        *,
        exact_mode: int | None = None,
        allow_missing: bool = False,
    ) -> Any:
        metadata = self._validate_regular_file(
            path, exact_mode=exact_mode, allow_missing=allow_missing
        )
        if metadata is None:
            return None
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise AutomationError("protected_json_invalid") from exc

    @staticmethod
    def _ensure_directory(path: Path) -> None:
        path.mkdir(mode=0o700, parents=True, exist_ok=True)
        metadata = path.lstat()
        if (
            not stat.S_ISDIR(metadata.st_mode)
            or stat.S_ISLNK(metadata.st_mode)
            or metadata.st_uid != os.getuid()
            or stat.S_IMODE(metadata.st_mode) != 0o700
        ):
            raise AutomationError("automation_directory_unsafe")

    def _ensure_layout(self) -> None:
        for directory in (self.root.parent, self.root, self.runs_dir):
            self._ensure_directory(directory)
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
                pass
            else:
                os.close(descriptor)
        metadata = self.lock_file.lstat()
        if (
            not stat.S_ISREG(metadata.st_mode)
            or stat.S_ISLNK(metadata.st_mode)
            or metadata.st_uid != os.getuid()
            or stat.S_IMODE(metadata.st_mode) != 0o600
        ):
            raise AutomationError("automation_lock_unsafe")

    def _atomic_write(self, path: Path, value: Mapping[str, Any]) -> None:
        encoded = canonical_json(value) + b"\n"
        if len(encoded) > MAX_FILE_BYTES:
            raise AutomationError("automation_record_oversize")
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

    def _log(self, message: str) -> None:
        self._ensure_directory(self.log_file.parent)
        if self.log_file.exists() and self.log_file.stat().st_size > MAX_FILE_BYTES:
            rotated = self.log_file.with_suffix(".log.1")
            os.replace(self.log_file, rotated)
            os.chmod(rotated, 0o600)
        descriptor = os.open(
            self.log_file,
            os.O_WRONLY
            | os.O_APPEND
            | os.O_CREAT
            | getattr(os, "O_NOFOLLOW", 0),
            0o600,
        )
        try:
            os.write(
                descriptor,
                f"{isoformat(self.clock())} {message}\n".encode("utf-8"),
            )
        finally:
            os.close(descriptor)

    def _validated_presence(self, now: datetime) -> Mapping[str, Any]:
        canonical = self._read_json(self.state_file, exact_mode=0o600)
        producer = self._read_json(self.producer_file, exact_mode=0o600)
        if not isinstance(canonical, dict) or not isinstance(producer, dict):
            raise AutomationError("presence_state_invalid")
        if frozenset(producer) != frozenset(
            {
                "schema_version",
                "sequence",
                "observation_id",
                "state_hash",
                "evaluated_at",
            }
        ):
            raise AutomationError("producer_state_invalid")
        if (
            producer.get("schema_version") != 1
            or not isinstance(producer.get("sequence"), int)
            or isinstance(producer.get("sequence"), bool)
            or producer["sequence"] < 1
            or not isinstance(producer.get("observation_id"), str)
            or HASH_RE.fullmatch(producer["observation_id"]) is None
            or not isinstance(producer.get("state_hash"), str)
            or HASH_RE.fullmatch(producer["state_hash"]) is None
        ):
            raise AutomationError("producer_state_invalid")
        evaluated_at = parse_time(producer.get("evaluated_at"))
        age = now.astimezone(timezone.utc) - evaluated_at
        if age < -MAX_FUTURE_SKEW or age > MAX_PRESENCE_AGE:
            raise AutomationError("presence_state_stale")
        if (
            canonical.get("timestamp") != producer["evaluated_at"]
            or state_hash(canonical) != producer["state_hash"]
        ):
            raise AutomationError("presence_state_mismatch")
        site_state = canonical.get(SITE)
        if not isinstance(site_state, dict):
            raise AutomationError("presence_state_invalid")
        if (
            site_state.get("occupancy") != "confirmed_vacant"
            or site_state.get("fresh") is not True
        ):
            raise AutomationError("site_not_confirmed_vacant")
        parse_time(site_state.get("stateChangedAt"))
        return site_state

    def _snoozed(self, now: datetime) -> bool:
        policy = self._read_json(self.snooze_file, allow_missing=True)
        if policy is None:
            return False
        if not isinstance(policy, dict):
            raise AutomationError("snooze_policy_invalid")
        value = policy.get(SITE)
        if value is None:
            return False
        expiry = parse_time(value)
        return expiry > now.astimezone(timezone.utc)

    def _command_json(self, command: Sequence[str], failure_code: str) -> dict[str, Any]:
        try:
            completed = self.command_runner(command)
        except (OSError, subprocess.SubprocessError) as exc:
            raise AutomationError(failure_code) from exc
        if completed.returncode != 0:
            raise AutomationError(failure_code)
        try:
            value = json.loads(completed.stdout)
        except (TypeError, json.JSONDecodeError) as exc:
            raise AutomationError(failure_code) from exc
        if not isinstance(value, dict):
            raise AutomationError(failure_code)
        return value

    def _recent_cat_activity(self, now: datetime) -> bool:
        history = self._command_json(
            [
                "litter-robot",
                "--json",
                "history",
                "crosstown-litter-robot",
                str(HISTORY_LIMIT),
            ],
            "litter_history_unavailable",
        )
        if (
            history.get("ok") is not True
            or history.get("alias") != "crosstown-litter-robot"
            or history.get("site") != SITE
            or not isinstance(history.get("history"), list)
        ):
            raise AutomationError("litter_history_invalid")
        cutoff = now.astimezone(timezone.utc) - RECENT_CAT_ACTIVITY
        for item in history["history"]:
            if not isinstance(item, dict):
                raise AutomationError("litter_history_invalid")
            action = item.get("action")
            timestamp = parse_time(item.get("timestamp"))
            if timestamp > now.astimezone(timezone.utc) + MAX_FUTURE_SKEW:
                raise AutomationError("litter_history_invalid")
            if (
                isinstance(action, str)
                and action.strip().lower() in CAT_ACTIVITY_ACTIONS
                and timestamp >= cutoff
            ):
                return True
        return False

    def _robot_state(self, alias: str) -> dict[str, Any]:
        value = self._command_json(
            ["crosstown-roomba", "state", alias], "robot_status_unavailable"
        )
        mission = value.get("cleanMissionStatus")
        bin_state = value.get("bin")
        battery = value.get("batPct")
        if (
            value.get("connected") is not True
            or not isinstance(mission, dict)
            or not isinstance(mission.get("phase"), str)
            or not mission["phase"]
            or not isinstance(mission.get("error"), int)
            or isinstance(mission.get("error"), bool)
            or not isinstance(battery, int)
            or isinstance(battery, bool)
            or battery < 0
            or battery > 100
            or not isinstance(bin_state, dict)
            or bin_state.get("present") is not True
            or not isinstance(bin_state.get("full"), bool)
        ):
            raise AutomationError("robot_status_invalid")
        return {
            "phase": mission["phase"],
            "error": mission["error"],
            "battery": battery,
            "bin_full": bin_state["full"],
        }

    def _start_robot(self, alias: str) -> None:
        value = self._command_json(
            ["crosstown-roomba", "start", alias], "robot_start_failed"
        )
        results = value.get("results")
        if (
            value.get("ok") is not True
            or value.get("action") != "start"
            or value.get("target") != alias
            or not isinstance(results, list)
            or len(results) != 1
            or not isinstance(results[0], dict)
            or results[0].get("ok") is not True
            or results[0].get("verification") != "passed"
            or results[0].get("phase") != "run"
        ):
            raise AutomationError("robot_start_unverified")

    def _finish(
        self,
        record_path: Path,
        record: dict[str, Any],
        *,
        outcome: str,
        ok: bool,
        started_robots: Sequence[str] = (),
        reason: str | None = None,
    ) -> tuple[int, dict[str, Any]]:
        record.update(
            {
                "completed_at": isoformat(self.clock()),
                "outcome": outcome,
                "started_robots": list(started_robots),
                "reason": reason,
            }
        )
        self._atomic_write(record_path, record)
        try:
            self._publish_latest(record)
        except (AutomationError, OSError):
            # The durable daily record remains authoritative. Dashboard
            # telemetry must not turn a completed decision into a command
            # failure or invite an unsafe retry.
            pass
        try:
            self._log(f"source={record['source']} outcome={outcome}")
        except (AutomationError, OSError):
            pass
        response: dict[str, Any] = {
            "ok": ok,
            "outcome": outcome,
            "site": SITE,
            "local_date": record["local_date"],
        }
        if started_robots:
            response["started_robots"] = list(started_robots)
        if reason:
            response["reason"] = reason
        return (0 if ok else 1), response

    def _publish_latest(
        self,
        record: Mapping[str, Any],
        *,
        outcome: str | None = None,
        reason: str | None = None,
        decision_outcome: str | None = None,
    ) -> None:
        value = {
            "schema_version": SCHEMA_VERSION,
            "site": SITE,
            "evaluated_at": isoformat(self.clock()),
            "local_date": record.get("local_date"),
            "source": record.get("source"),
            "outcome": outcome or record.get("outcome"),
            "reason": reason if reason is not None else record.get("reason"),
            "decision_outcome": decision_outcome,
            "vacancy_state_changed_at": record.get("vacancy_state_changed_at"),
            "started_robots": list(record.get("started_robots") or []),
            "checks": dict(record.get("checks") or {}),
        }
        self._atomic_write(self.latest_file, value)

    def run(self, source: str) -> tuple[int, dict[str, Any]]:
        if source not in SOURCES:
            return 2, {"ok": False, "outcome": "invalid_source"}
        now = self.clock().astimezone(timezone.utc)
        local_date = now.astimezone(LOCAL_TIMEZONE).date().isoformat()
        try:
            self._ensure_layout()
            descriptor = os.open(
                self.lock_file,
                os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0),
            )
            try:
                fcntl.flock(descriptor, fcntl.LOCK_EX)
                return self._run_locked(source, now, local_date)
            finally:
                fcntl.flock(descriptor, fcntl.LOCK_UN)
                os.close(descriptor)
        except (AutomationError, OSError) as exc:
            reason = exc.code if isinstance(exc, AutomationError) else "runtime_io_error"
            try:
                self._publish_latest(
                    {
                        "local_date": local_date,
                        "source": source,
                        "checks": {},
                    },
                    outcome="failed",
                    reason=reason,
                )
            except (AutomationError, OSError):
                pass
            try:
                self._log(f"source={source} outcome=failed reason={reason}")
            except (AutomationError, OSError):
                pass
            return 1, {
                "ok": False,
                "outcome": "failed",
                "site": SITE,
                "local_date": local_date,
                "reason": reason,
            }

    def _run_locked(
        self, source: str, now: datetime, local_date: str
    ) -> tuple[int, dict[str, Any]]:
        record_path = self.runs_dir / f"{local_date}.json"
        if record_path.exists():
            existing = self._read_json(record_path, exact_mode=0o600)
            if not isinstance(existing, dict):
                raise AutomationError("daily_record_invalid")
            self._publish_latest(
                {
                    **existing,
                    "source": source,
                },
                outcome="already_handled",
                reason="daily_already_handled",
                decision_outcome=existing.get("outcome"),
            )
            self._log(f"source={source} outcome=already_handled")
            return 0, {
                "ok": True,
                "outcome": "already_handled",
                "site": SITE,
                "local_date": local_date,
            }

        try:
            site_state = self._validated_presence(now)
        except AutomationError as exc:
            if exc.code == "site_not_confirmed_vacant":
                self._publish_latest(
                    {
                        "local_date": local_date,
                        "source": source,
                        "checks": {"presence": "not_confirmed_vacant"},
                    },
                    outcome="not_vacant",
                )
                self._log(f"source={source} outcome=not_vacant")
                return 0, {
                    "ok": True,
                    "outcome": "not_vacant",
                    "site": SITE,
                    "local_date": local_date,
                }
            raise

        record: dict[str, Any] = {
            "schema_version": SCHEMA_VERSION,
            "site": SITE,
            "local_date": local_date,
            "source": source,
            "vacancy_state_changed_at": site_state["stateChangedAt"],
            "started_at": isoformat(now),
            "completed_at": None,
            "outcome": "in_progress",
            "started_robots": [],
            "reason": None,
            "checks": {"presence": "confirmed_vacant"},
        }
        # Commit intent before any external read or command. An interruption
        # therefore cannot cause an uncertain physical start to be retried.
        self._atomic_write(record_path, record)

        try:
            if self._snoozed(now):
                record["checks"]["snooze"] = "active"
                return self._finish(
                    record_path,
                    record,
                    outcome="snoozed",
                    ok=True,
                    reason="snoozed",
                )
            record["checks"]["snooze"] = "clear"
            if self._recent_cat_activity(now):
                record["checks"]["recent_cat_activity"] = True
                return self._finish(
                    record_path,
                    record,
                    outcome="recent_cat_activity",
                    ok=True,
                    reason="recent_cat_activity",
                )
            record["checks"]["recent_cat_activity"] = False

            states = {alias: self._robot_state(alias) for alias in ROBOT_ALIASES}
            record["checks"]["robots"] = states
            not_ready = [
                alias
                for alias, state in states.items()
                if state["phase"] not in IDLE_PHASES | ACTIVE_PHASES
                or state["error"] != 0
                or state["battery"] < MIN_START_BATTERY
                or state["bin_full"]
            ]
            if not_ready:
                return self._finish(
                    record_path,
                    record,
                    outcome="robot_not_ready",
                    ok=True,
                    reason="robot_not_ready",
                )
            to_start = [
                alias for alias, state in states.items() if state["phase"] in IDLE_PHASES
            ]
            if not to_start:
                return self._finish(
                    record_path,
                    record,
                    outcome="already_cleaning",
                    ok=True,
                    reason="already_satisfied",
                )

            started: list[str] = []
            for alias in to_start:
                try:
                    self._start_robot(alias)
                except AutomationError as exc:
                    return self._finish(
                        record_path,
                        record,
                        outcome="failed",
                        ok=False,
                        started_robots=started,
                        reason=exc.code,
                    )
                started.append(alias)
            return self._finish(
                record_path,
                record,
                outcome="started",
                ok=True,
                started_robots=started,
            )
        except AutomationError as exc:
            return self._finish(
                record_path,
                record,
                outcome="failed",
                ok=False,
                reason=exc.code,
            )


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", choices=sorted(SOURCES), required=True)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    code, response = CrosstownVacantRoomba().run(args.source)
    print(canonical_json(response).decode("utf-8"))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
