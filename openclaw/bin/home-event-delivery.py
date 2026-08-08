#!/usr/bin/env python3
"""Deliver one fixed-template Stage 3 home-event reservation at most once."""

from __future__ import annotations

import argparse
from contextlib import closing
from datetime import datetime, timedelta, timezone
import fcntl
import json
import os
from pathlib import Path
import re
import sqlite3
import stat
import subprocess
import sys
from typing import Any, Callable, Mapping


sys.path.insert(0, str(Path(__file__).resolve().parent))
from home_event_bus import (  # noqa: E402
    EventStore,
    HomeEventError,
    RuntimePaths,
    load_delivery_policy,
    utc_now,
    validate_runtime,
)


OPENCLAW_BIN = "/opt/homebrew/bin/openclaw"
TARGET_RE = re.compile(r"^chat_id:[1-9][0-9]{0,17}$")
SAFE_CODE_RE = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
RESERVATION_RE = re.compile(r"^res_[0-9a-f]{32}$")
PRESENCE_MAX_AGE = timedelta(minutes=30)
SEND_TIMEOUT_SECONDS = 20
MAX_RECEIPT_BYTES = 64 * 1024

TEMPLATES = {
    "person_activity": (
        "{site} is marked vacant. Person activity was detected, and no resident "
        "arrival was detected during the following 15 minutes. Do you recognize "
        "this activity?"
    ),
    "access_activity": (
        "{site} is marked vacant. The front entry is recorded as unlocked or open, "
        "and no resident presence was confirmed during the following 15 minutes. "
        "Do you recognize this state?"
    ),
    "person_and_access": (
        "{site} is marked vacant. Person activity and an unlock or door opening "
        "were detected, and no resident arrival was detected during the following "
        "15 minutes. Do you recognize this activity?"
    ),
}

CAMERA_CLAUSES = {
    "person_visible": (
        "A person was visible in at least one time-aligned interior camera check."
    ),
    "no_person_visible": (
        "Neither time-aligned interior camera check showed a visible person."
    ),
    "uncertain": "The time-aligned interior camera checks were inconclusive.",
    "unavailable": "The time-aligned interior camera checks were unavailable.",
}


class DeliveryError(Exception):
    def __init__(self, code: str, *, uncertain: bool = False):
        super().__init__(code)
        self.code = code
        self.uncertain = uncertain


def parse_time(value: Any) -> datetime:
    if not isinstance(value, str) or len(value) > 64:
        raise DeliveryError("invalid_timestamp")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise DeliveryError("invalid_timestamp") from exc
    if parsed.tzinfo is None:
        raise DeliveryError("invalid_timestamp")
    return parsed.astimezone(timezone.utc)


def format_time(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat(timespec="seconds").replace(
        "+00:00", "Z"
    )


def read_presence(path: Path, now: datetime) -> Mapping[str, str]:
    unknown = {"cabin": "uncertain", "crosstown": "uncertain"}
    try:
        metadata = path.lstat()
        if (
            stat.S_ISLNK(metadata.st_mode)
            or not stat.S_ISREG(metadata.st_mode)
            or metadata.st_uid != os.geteuid()
            or stat.S_IMODE(metadata.st_mode) & 0o077
            or metadata.st_size <= 0
            or metadata.st_size > 1024 * 1024
        ):
            return unknown
        payload = json.loads(path.read_text(encoding="utf-8"))
        observed = parse_time(payload.get("timestamp"))
        if observed > now + timedelta(minutes=5) or now - observed > PRESENCE_MAX_AGE:
            return unknown
        result: dict[str, str] = {}
        for site in ("cabin", "crosstown"):
            site_state = payload.get(site)
            if not isinstance(site_state, dict) or site_state.get("fresh") is not True:
                result[site] = "uncertain"
            elif site_state.get("occupancy") == "confirmed_vacant":
                result[site] = "vacant"
            elif site_state.get("occupancy") == "occupied":
                result[site] = "occupied"
            else:
                result[site] = "uncertain"
        return result
    except (OSError, ValueError, TypeError, json.JSONDecodeError, DeliveryError):
        return unknown


def validate_receipt(stdout: str, target: str) -> None:
    if not stdout or len(stdout.encode("utf-8")) > MAX_RECEIPT_BYTES:
        raise DeliveryError("message_receipt_invalid", uncertain=True)
    try:
        lines = stdout.splitlines()
        if len(lines) != 1 or not lines[0]:
            raise ValueError
        payload = json.loads(lines[0])
    except (json.JSONDecodeError, ValueError) as exc:
        raise DeliveryError("message_receipt_invalid", uncertain=True) from exc
    expected = {"action", "channel", "dryRun", "handledBy", "payload"}
    channel_payload = payload.get("payload") if isinstance(payload, dict) else None
    result = (
        channel_payload.get("result") if isinstance(channel_payload, dict) else None
    )
    top_id = payload.get("messageId") if isinstance(payload, dict) else None
    nested_id = result.get("messageId") if isinstance(result, dict) else None
    message_id = top_id or nested_id
    if (
        not isinstance(payload, dict)
        or not expected.issubset(payload)
        or set(payload) - (expected | {"messageId"})
        or payload.get("action") != "send"
        or payload.get("channel") != "imessage"
        or payload.get("dryRun") is not False
        or payload.get("handledBy") != "core"
        or not isinstance(channel_payload, dict)
        or channel_payload.get("channel") != "imessage"
        or channel_payload.get("to") != target
        or channel_payload.get("via") != "gateway"
        or not isinstance(result, dict)
        or not isinstance(message_id, str)
        or not 1 <= len(message_id) <= 128
        or any(ord(character) < 0x20 for character in message_id)
        or (top_id is not None and nested_id is not None and top_id != nested_id)
    ):
        raise DeliveryError("message_receipt_invalid", uncertain=True)


def send_message(target: str, message: str) -> None:
    child_env = {
        "HOME": str(Path.home()),
        "PATH": "/opt/homebrew/opt/node@22/bin:/opt/homebrew/bin:/usr/bin:/bin",
        "LANG": "en_US.UTF-8",
    }
    for key in ("OPENCLAW_GATEWAY_TOKEN", "OPENCLAW_GATEWAY_PASSWORD"):
        value = os.environ.get(key, "")
        if value:
            child_env[key] = value
    try:
        result = subprocess.run(
            [
                OPENCLAW_BIN,
                "message",
                "send",
                "--channel",
                "imessage",
                "--target",
                target,
                "--message",
                message,
                "--json",
            ],
            capture_output=True,
            text=True,
            timeout=SEND_TIMEOUT_SECONDS,
            env=child_env,
        )
    except subprocess.TimeoutExpired as exc:
        raise DeliveryError("message_send_timeout", uncertain=True) from exc
    except OSError as exc:
        raise DeliveryError("message_command_unavailable") from exc
    if result.returncode != 0:
        raise DeliveryError("message_send_failed")
    if result.stderr:
        raise DeliveryError("message_receipt_invalid", uncertain=True)
    validate_receipt(result.stdout, target)


class DeliveryWorker:
    def __init__(
        self,
        root: Path,
        presence_state: Path,
        target: str,
        *,
        clock: Callable[[], str] = utc_now,
    ) -> None:
        self.paths = validate_runtime(root)
        self.store = EventStore(self.paths, clock=clock)
        self.presence_state = presence_state
        self.target = target
        self.clock = clock

    def now(self) -> datetime:
        return parse_time(self.clock())

    @staticmethod
    def _runtime_update(
        connection: sqlite3.Connection,
        *,
        now: str,
        health: str,
        attempt: bool = False,
        success: bool = False,
        error_code: str | None = None,
    ) -> None:
        connection.execute(
            """
            UPDATE delivery_runtime SET
                health = ?, updated_at = ?,
                last_attempt_at = CASE WHEN ? THEN ? ELSE last_attempt_at END,
                last_success_at = CASE WHEN ? THEN ? ELSE last_success_at END,
                last_error_at = CASE WHEN ? IS NOT NULL THEN ? ELSE last_error_at END,
                last_error_code = CASE
                    WHEN ? IS NOT NULL THEN ? ELSE last_error_code END
            WHERE singleton = 1
            """,
            (
                health,
                now,
                int(attempt),
                now,
                int(success),
                now,
                error_code,
                now,
                error_code,
                error_code,
            ),
        )

    def _claim(self) -> Mapping[str, Any] | None:
        now = format_time(self.now())
        with closing(self.store.connect()) as connection:
            connection.execute("BEGIN IMMEDIATE")
            mode = connection.execute(
                "SELECT mode FROM runtime_status WHERE singleton = 1"
            ).fetchone()["mode"]
            if mode != "limited_delivery":
                self._runtime_update(connection, now=now, health="disabled")
                connection.commit()
                return None
            try:
                policy = load_delivery_policy(self.paths)
            except HomeEventError as exc:
                self._runtime_update(
                    connection,
                    now=now,
                    health="degraded",
                    error_code="delivery_policy_unavailable",
                )
                connection.commit()
                raise DeliveryError("delivery_policy_unavailable") from exc
            if policy["active"] is not True:
                self._runtime_update(
                    connection,
                    now=now,
                    health="degraded",
                    error_code="delivery_policy_inactive",
                )
                connection.commit()
                raise DeliveryError("delivery_policy_inactive")
            connection.execute(
                """
                UPDATE notification_outbox
                SET status = 'unknown', error_code = 'prior_attempt_uncertain',
                    reviewed_at = NULL, review_outcome = NULL, updated_at = ?
                WHERE status = 'reserved' AND attempt_count > 0
                """,
                (now,),
            )
            connection.execute(
                """
                UPDATE notification_outbox
                SET status = 'burned', error_code = 'reservation_expired',
                    updated_at = ?
                WHERE status = 'reserved' AND attempt_count = 0
                  AND reserved_until < ?
                """,
                (now, now),
            )
            row = connection.execute(
                """
                SELECT * FROM notification_outbox
                WHERE status = 'reserved' AND attempt_count = 0
                  AND reserved_until >= ?
                ORDER BY id LIMIT 1
                """,
                (now,),
            ).fetchone()
            if row is None:
                self._runtime_update(connection, now=now, health="ok")
                connection.commit()
                return None
            if (
                row["recipient_route"] != "dylan"
                or row["template_code"] not in TEMPLATES
                or row["site"] not in policy["sites"]
                or row["template_code"] not in policy["incident_classes"]
                or row["recipient_route"] not in policy["recipient_routes"]
                or not isinstance(row["reservation_token"], str)
                or RESERVATION_RE.fullmatch(row["reservation_token"]) is None
                or (
                    row["camera_result"] is not None
                    and (
                        policy["camera_enabled"] is not True
                        or row["camera_result"] not in CAMERA_CLAUSES
                        or type(row["camera_evaluation_id"]) is not int
                    )
                )
                or (
                    row["camera_result"] is None
                    and row["camera_evaluation_id"] is not None
                )
            ):
                connection.execute(
                    """
                    UPDATE notification_outbox
                    SET status = 'dead_letter', error_code = 'reservation_invalid',
                        updated_at = ? WHERE id = ?
                    """,
                    (now, row["id"]),
                )
                self._runtime_update(
                    connection,
                    now=now,
                    health="degraded",
                    error_code="reservation_invalid",
                )
                connection.commit()
                raise DeliveryError("reservation_invalid")
            connection.execute(
                """
                UPDATE notification_outbox
                SET attempt_count = 1, last_attempt_at = ?, updated_at = ?
                WHERE id = ? AND status = 'reserved' AND attempt_count = 0
                """,
                (now, now, row["id"]),
            )
            self._runtime_update(
                connection, now=now, health="ok", attempt=True
            )
            connection.commit()
            return dict(row)

    def _configuration_error(self, code: str) -> None:
        now = format_time(self.now())
        with closing(self.store.connect()) as connection:
            connection.execute("BEGIN IMMEDIATE")
            self._runtime_update(
                connection, now=now, health="degraded", error_code=code
            )
            connection.commit()
        self.store.write_status_best_effort()

    def _finish_without_send(self, row_id: int, code: str) -> None:
        now = format_time(self.now())
        with closing(self.store.connect()) as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                """
                UPDATE notification_outbox
                SET status = 'burned', error_code = ?, updated_at = ?
                WHERE id = ? AND status = 'reserved' AND attempt_count = 1
                """,
                (code, now, row_id),
            )
            self._runtime_update(connection, now=now, health="ok")
            connection.commit()

    def _send_claimed(self, row: Mapping[str, Any]) -> str:
        now_dt = self.now()
        presence = read_presence(self.presence_state, now_dt)
        if presence.get(row["site"]) != "vacant":
            self._finish_without_send(int(row["id"]), "presence_not_vacant")
            return "burned"
        if not TARGET_RE.fullmatch(self.target):
            raise DeliveryError("delivery_target_unavailable")
        message = TEMPLATES[row["template_code"]].format(
            site="Cabin" if row["site"] == "cabin" else "Crosstown"
        )
        camera_result = row.get("camera_result")
        if camera_result is not None:
            message += " " + CAMERA_CLAUSES[camera_result]
        now = format_time(now_dt)
        with closing(self.store.connect()) as connection:
            connection.execute("BEGIN IMMEDIATE")
            current = connection.execute(
                "SELECT * FROM notification_outbox WHERE id = ?",
                (row["id"],),
            ).fetchone()
            mode = connection.execute(
                "SELECT mode FROM runtime_status WHERE singleton = 1"
            ).fetchone()["mode"]
            if (
                current is None
                or current["status"] != "reserved"
                or current["attempt_count"] != 1
                or current["reservation_token"] != row["reservation_token"]
                or mode != "limited_delivery"
            ):
                if current is not None and current["status"] == "reserved":
                    connection.execute(
                        """
                        UPDATE notification_outbox
                        SET status = 'burned', error_code = 'mode_rollback',
                            updated_at = ? WHERE id = ?
                        """,
                        (now, row["id"]),
                    )
                connection.commit()
                return "burned"
            connection.commit()
        try:
            send_message(self.target, message)
        except DeliveryError as exc:
            status_value = "unknown" if exc.uncertain else "dead_letter"
            with closing(self.store.connect()) as connection:
                connection.execute("BEGIN IMMEDIATE")
                connection.execute(
                    """
                    UPDATE notification_outbox
                    SET status = ?, error_code = ?,
                        reviewed_at = CASE WHEN ? = 'unknown' THEN NULL ELSE reviewed_at END,
                        review_outcome = CASE WHEN ? = 'unknown' THEN NULL ELSE review_outcome END,
                        updated_at = ?
                    WHERE id = ? AND status = 'reserved' AND attempt_count = 1
                    """,
                    (
                        status_value,
                        exc.code,
                        status_value,
                        status_value,
                        now,
                        row["id"],
                    ),
                )
                self._runtime_update(
                    connection,
                    now=now,
                    health="degraded",
                    error_code=exc.code,
                )
                connection.commit()
            return status_value
        with closing(self.store.connect()) as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                """
                UPDATE notification_outbox
                SET status = 'sent', sent_at = ?, error_code = NULL, updated_at = ?
                WHERE id = ? AND status = 'reserved' AND attempt_count = 1
                """,
                (now, now, row["id"]),
            )
            self._runtime_update(
                connection, now=now, health="ok", success=True
            )
            connection.commit()
            return "sent"

    def run_once(self) -> Mapping[str, Any]:
        descriptor = os.open(
            self.paths.delivery_lock,
            os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0),
        )
        try:
            try:
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                return {"ok": True, "outcome": "busy"}
            if self.store.runtime_mode() == "limited_delivery":
                if TARGET_RE.fullmatch(self.target) is None:
                    self._configuration_error("delivery_target_unavailable")
                    raise DeliveryError("delivery_target_unavailable")
                if not any(
                    os.environ.get(key)
                    for key in (
                        "OPENCLAW_GATEWAY_TOKEN",
                        "OPENCLAW_GATEWAY_PASSWORD",
                    )
                ):
                    self._configuration_error("delivery_auth_unavailable")
                    raise DeliveryError("delivery_auth_unavailable")
            row = self._claim()
            outcome = "idle" if row is None else self._send_claimed(row)
            self.store.write_status_best_effort()
            return {"ok": True, "outcome": outcome}
        finally:
            try:
                fcntl.flock(descriptor, fcntl.LOCK_UN)
            finally:
                os.close(descriptor)


def parser() -> argparse.ArgumentParser:
    value = argparse.ArgumentParser(description=__doc__)
    value.add_argument(
        "--root",
        type=Path,
        default=Path(
            os.environ.get("HOME_EVENTS_ROOT", "~/.openclaw/home-events")
        ).expanduser(),
    )
    value.add_argument(
        "--presence-state",
        type=Path,
        default=Path(
            os.environ.get(
                "HOME_EVENTS_PRESENCE_STATE", "~/.openclaw/presence/state.json"
            )
        ).expanduser(),
    )
    value.add_argument(
        "--target", default=os.environ.get("OPENCLAW_DYLAN_IMESSAGE_TARGET", "")
    )
    return value


def main(argv: list[str] | None = None) -> int:
    os.umask(0o077)
    args = parser().parse_args(argv)
    try:
        result = DeliveryWorker(
            args.root, args.presence_state, args.target
        ).run_once()
    except (DeliveryError, HomeEventError, OSError, sqlite3.Error) as exc:
        candidate = getattr(exc, "code", "delivery_failed")
        code = candidate if SAFE_CODE_RE.fullmatch(candidate) else "delivery_failed"
        print(json.dumps({"ok": False, "error_code": code}, separators=(",", ":")))
        return 1
    print(json.dumps(result, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    sys.exit(main())
