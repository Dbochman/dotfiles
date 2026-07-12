#!/usr/bin/env python3
"""Persistent, shadow-only incident correlation for normalized home events."""

from __future__ import annotations

import argparse
from contextlib import closing
import json
import os
from pathlib import Path
import re
import secrets
import sqlite3
import stat
import sys
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Mapping


sys.path.insert(0, str(Path(__file__).resolve().parent))
from home_event_bus import (  # noqa: E402
    EventStore,
    HomeEventError,
    RuntimePaths,
    utc_now,
    validate_runtime,
)


CONSUMER = "correlator"
PRESENCE_MAX_AGE = timedelta(minutes=30)
INCIDENT_GRACE = timedelta(seconds=90)
ROUTINE_QUIET = timedelta(minutes=15)
ACCESS_MAX_AGE = timedelta(hours=24)
RATE_LIMIT = timedelta(hours=1)
SAFE_CODE_RE = re.compile(r"^[a-z][a-z0-9_]{0,63}$")


class CorrelatorError(Exception):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


def parse_time(value: Any) -> datetime:
    if not isinstance(value, str) or len(value) > 64:
        raise CorrelatorError("invalid_timestamp")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise CorrelatorError("invalid_timestamp") from exc
    if parsed.tzinfo is None:
        raise CorrelatorError("invalid_timestamp")
    return parsed.astimezone(timezone.utc)


def format_time(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def read_presence(path: Path, now: datetime) -> Mapping[str, str]:
    """Return fail-closed per-site modes without exposing resident details."""

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
                continue
            occupancy = site_state.get("occupancy")
            if occupancy == "confirmed_vacant":
                result[site] = "vacant"
            elif occupancy == "occupied":
                result[site] = "occupied"
            else:
                result[site] = "uncertain"
        return result
    except (OSError, ValueError, TypeError, json.JSONDecodeError, CorrelatorError):
        return unknown


class ShadowCorrelator:
    def __init__(
        self,
        root: Path,
        presence_state: Path,
        *,
        clock: Callable[[], str] = utc_now,
    ) -> None:
        self.paths = validate_runtime(root)
        self.store = EventStore(self.paths, clock=clock)
        self.presence_state = presence_state
        self.clock = clock

    def now(self) -> datetime:
        return parse_time(self.clock())

    @staticmethod
    def _increment(connection: sqlite3.Connection, name: str) -> None:
        connection.execute(
            """
            INSERT INTO service_counters(name, value) VALUES (?, 1)
            ON CONFLICT(name) DO UPDATE SET value = value + 1
            """,
            (name,),
        )

    @staticmethod
    def _open_incident(
        connection: sqlite3.Connection, site: str, category: str
    ) -> sqlite3.Row | None:
        return connection.execute(
            """
            SELECT * FROM incidents
            WHERE site = ? AND category = ? AND state = 'open'
            ORDER BY id DESC LIMIT 1
            """,
            (site, category),
        ).fetchone()

    def _ensure_incident(
        self,
        connection: sqlite3.Connection,
        *,
        site: str,
        category: str,
        summary_code: str,
        event_time: str,
    ) -> sqlite3.Row:
        incident = self._open_incident(connection, site, category)
        now = format_time(self.now())
        if incident is None:
            incident_uid = "inc_" + secrets.token_hex(16)
            connection.execute(
                """
                INSERT INTO incidents(
                    incident_uid, site, state, category, summary_code,
                    opened_at, updated_at
                ) VALUES (?, ?, 'open', ?, ?, ?, ?)
                """,
                (incident_uid, site, category, summary_code, event_time, now),
            )
            self._increment(connection, "incidents_opened")
            incident = self._open_incident(connection, site, category)
            assert incident is not None
        else:
            connection.execute(
                """
                UPDATE incidents SET summary_code = ?, updated_at = ?
                WHERE id = ?
                """,
                (summary_code, now, incident["id"]),
            )
            incident = connection.execute(
                "SELECT * FROM incidents WHERE id = ?", (incident["id"],)
            ).fetchone()
            assert incident is not None
        return incident

    def _attach(
        self,
        connection: sqlite3.Connection,
        incident_id: int,
        event_id: int,
        relation: str,
    ) -> None:
        connection.execute(
            """
            INSERT OR IGNORE INTO incident_events(
                incident_id, event_id, relation, created_at
            ) VALUES (?, ?, ?, ?)
            """,
            (incident_id, event_id, relation, format_time(self.now())),
        )

    def _record_decision(
        self,
        connection: sqlite3.Connection,
        incident_id: int,
        status: str,
        reason_code: str,
    ) -> bool:
        cursor = connection.execute(
            """
            INSERT OR IGNORE INTO incident_decisions(
                incident_id, status, reason_code, created_at
            ) VALUES (?, ?, ?, ?)
            """,
            (incident_id, status, reason_code, format_time(self.now())),
        )
        return cursor.rowcount == 1

    def _resolve(
        self,
        connection: sqlite3.Connection,
        incident: sqlite3.Row,
        summary_code: str,
        *,
        state: str = "resolved",
    ) -> None:
        now = format_time(self.now())
        connection.execute(
            """
            UPDATE incidents SET state = ?, summary_code = ?,
                updated_at = ?, resolved_at = ?
            WHERE id = ? AND state = 'open'
            """,
            (state, summary_code, now, now, incident["id"]),
        )
        self._increment(connection, "incidents_resolved")

    @staticmethod
    def _access_state(
        connection: sqlite3.Connection, incident_id: int
    ) -> tuple[bool, bool | None, bool | None]:
        rows = connection.execute(
            """
            SELECT e.event_type
            FROM incident_events ie
            JOIN events e ON e.id = ie.event_id
            WHERE ie.incident_id = ?
              AND e.event_type IN (
                'lock.unlocked', 'lock.locked', 'door.opened', 'door.closed'
              )
            ORDER BY e.observed_at, e.id
            """,
            (incident_id,),
        ).fetchall()
        has_open_evidence = False
        lock_open: bool | None = None
        door_open: bool | None = None
        for row in rows:
            event_type = row["event_type"]
            if event_type == "lock.unlocked":
                has_open_evidence = True
                lock_open = True
            elif event_type == "lock.locked":
                lock_open = False
            elif event_type == "door.opened":
                has_open_evidence = True
                door_open = True
            elif event_type == "door.closed":
                door_open = False
        return has_open_evidence, lock_open, door_open

    @staticmethod
    def _presence_summary(mode: str, prefix: str) -> str:
        if mode == "vacant":
            return f"vacant_{prefix}_shadowed"
        if mode == "occupied":
            return f"occupied_{prefix}_shadowed"
        return "presence_uncertain_shadowed"

    def _process_delivery(
        self,
        delivery: Mapping[str, Any],
        presence: Mapping[str, str],
        lease_token: str,
    ) -> None:
        event_type = delivery["event_type"]
        site = delivery["site"]
        event_id = int(delivery["event_id"])
        event_time = delivery["observed_at"]
        mode = presence.get(site, "uncertain")
        with closing(self.store.connect()) as connection:
            connection.execute("BEGIN IMMEDIATE")
            if delivery.get("time_precision") == "backfill":
                self._increment(connection, "ring_backfill_shadowed")
            elif event_type in {"entry.doorbell_rang", "entry.person_detected"}:
                incident = self._ensure_incident(
                    connection,
                    site=site,
                    category="activity",
                    summary_code=self._presence_summary(mode, "activity"),
                    event_time=event_time,
                )
                self._attach(connection, incident["id"], event_id, "activity")
            elif event_type == "entry.motion_detected":
                self._increment(connection, "generic_motion_shadowed")
            elif event_type in {"lock.unlocked", "door.opened"}:
                incident = self._ensure_incident(
                    connection,
                    site=site,
                    category="activity",
                    summary_code=self._presence_summary(mode, "entry"),
                    event_time=event_time,
                )
                self._attach(connection, incident["id"], event_id, "access_open")
            elif event_type in {"lock.locked", "door.closed"}:
                incident = self._open_incident(connection, site, "activity")
                if incident is not None:
                    self._attach(connection, incident["id"], event_id, "access_closed")
                    has_access, lock_open, door_open = self._access_state(
                        connection, int(incident["id"])
                    )
                    if has_access and lock_open is not True and door_open is not True:
                        self._resolve(connection, incident, "access_resolved_silently")
                    elif has_access:
                        connection.execute(
                            "UPDATE incidents SET summary_code = ?, updated_at = ? WHERE id = ?",
                            (
                                "access_still_open_shadowed",
                                format_time(self.now()),
                                incident["id"],
                            ),
                        )
            elif event_type == "presence.occupancy_changed":
                incident = self._open_incident(connection, site, "activity")
                if incident is not None:
                    self._attach(connection, incident["id"], event_id, "presence_context")
                    current = delivery.get("attributes", {}).get("current")
                    if current == "occupied" and mode == "occupied":
                        self._resolve(connection, incident, "resident_arrival_silent")
                    elif current != "confirmed_vacant":
                        connection.execute(
                            "UPDATE incidents SET summary_code = ?, updated_at = ? WHERE id = ?",
                            (
                                "presence_uncertain_shadowed",
                                format_time(self.now()),
                                incident["id"],
                            ),
                        )
            elif event_type == "presence.person_relocated":
                incident = self._open_incident(connection, site, "activity")
                if incident is not None:
                    self._attach(connection, incident["id"], event_id, "presence_context")
                    if mode == "occupied":
                        self._resolve(connection, incident, "resident_arrival_silent")
            elif event_type == "source.unavailable":
                incident = self._ensure_incident(
                    connection,
                    site=site,
                    category="source_health",
                    summary_code="source_unavailable_shadowed",
                    event_time=event_time,
                )
                self._attach(connection, incident["id"], event_id, "health_open")
            elif event_type == "source.recovered":
                incident = self._open_incident(connection, site, "source_health")
                if incident is not None:
                    self._attach(connection, incident["id"], event_id, "health_recovery")
                    self._resolve(connection, incident, "source_recovered_silently")
            elif event_type == "device.battery_low":
                incident = self._ensure_incident(
                    connection,
                    site=site,
                    category="battery",
                    summary_code="battery_low_shadowed",
                    event_time=event_time,
                )
                self._attach(connection, incident["id"], event_id, "battery_low")
            elif event_type == "device.battery_recovered":
                incident = self._open_incident(connection, site, "battery")
                if incident is not None:
                    self._attach(connection, incident["id"], event_id, "battery_recovery")
                    self._resolve(connection, incident, "battery_recovered_silently")
            else:
                raise CorrelatorError("unsupported_event")
            acknowledged = connection.execute(
                """
                UPDATE consumer_deliveries SET
                    status = 'acknowledged', lease_token = NULL,
                    lease_until = NULL, error_code = NULL, updated_at = ?
                WHERE id = ? AND consumer_name = ?
                  AND status = 'leased' AND lease_token = ?
                """,
                (
                    format_time(self.now()),
                    int(delivery["delivery_id"]),
                    CONSUMER,
                    lease_token,
                ),
            )
            if acknowledged.rowcount != 1:
                raise CorrelatorError("delivery_lease_mismatch")
            connection.commit()

    def _expire_incidents(self) -> int:
        now = self.now()
        changed = 0
        with closing(self.store.connect()) as connection:
            connection.execute("BEGIN IMMEDIATE")
            incidents = connection.execute(
                "SELECT * FROM incidents WHERE state = 'open'"
            ).fetchall()
            for incident in incidents:
                backlog = connection.execute(
                    """
                    SELECT 1
                    FROM consumer_deliveries d
                    JOIN events e ON e.id = d.event_id
                    WHERE d.consumer_name = ?
                      AND d.status IN ('pending', 'leased')
                      AND e.site = ?
                    LIMIT 1
                    """,
                    (CONSUMER, incident["site"]),
                ).fetchone()
                if backlog is not None:
                    continue
                age = now - parse_time(incident["opened_at"])
                latest_event = connection.execute(
                    """
                    SELECT MAX(e.observed_at) AS observed_at
                    FROM incident_events ie
                    JOIN events e ON e.id = ie.event_id
                    WHERE ie.incident_id = ?
                    """,
                    (incident["id"],),
                ).fetchone()
                quiet_at = (
                    latest_event["observed_at"]
                    if latest_event is not None and latest_event["observed_at"]
                    else incident["opened_at"]
                )
                quiet = now - parse_time(quiet_at)
                if incident["category"] == "activity":
                    access_open = connection.execute(
                        """
                        SELECT 1 FROM incident_events ie
                        JOIN events e ON e.id = ie.event_id
                        WHERE ie.incident_id = ?
                          AND e.event_type IN ('lock.unlocked', 'door.opened')
                        LIMIT 1
                        """,
                        (incident["id"],),
                    ).fetchone()
                    if access_open and age >= ACCESS_MAX_AGE:
                        self._resolve(
                            connection,
                            incident,
                            "access_expired_unresolved",
                            state="expired_unresolved",
                        )
                        status_time = format_time(now)
                        connection.execute(
                            """
                            UPDATE runtime_status SET
                                health = 'degraded', updated_at = ?,
                                last_error_at = ?,
                                last_error_code = 'access_expired_unresolved'
                            WHERE singleton = 1
                            """,
                            (status_time, status_time),
                        )
                        self._increment(connection, "access_incidents_expired")
                        changed += 1
                    elif not access_open and quiet >= ROUTINE_QUIET:
                        self._resolve(connection, incident, "routine_quiet_silent")
                        changed += 1
            connection.commit()
        return changed

    def _finalize_shadow_decisions(self, presence: Mapping[str, str]) -> int:
        now = self.now()
        decisions = 0
        with closing(self.store.connect()) as connection:
            connection.execute("BEGIN IMMEDIATE")
            incidents = connection.execute(
                """
                SELECT * FROM incidents
                WHERE state = 'open' AND category = 'activity'
                ORDER BY id
                """
            ).fetchall()
            for incident in incidents:
                if now - parse_time(incident["opened_at"]) < INCIDENT_GRACE:
                    continue
                existing = connection.execute(
                    "SELECT 1 FROM notification_outbox WHERE incident_id = ? LIMIT 1",
                    (incident["id"],),
                ).fetchone()
                if existing:
                    continue
                mode = presence.get(incident["site"], "uncertain")
                if mode != "vacant":
                    summary = (
                        "occupied_activity_shadowed"
                        if mode == "occupied"
                        else "presence_uncertain_shadowed"
                    )
                    connection.execute(
                        "UPDATE incidents SET summary_code = ?, updated_at = ? WHERE id = ?",
                        (summary, format_time(now), incident["id"]),
                    )
                    self._record_decision(
                        connection,
                        int(incident["id"]),
                        "suppressed",
                        summary,
                    )
                    continue
                recent = connection.execute(
                    """
                    SELECT 1 FROM notification_outbox
                    WHERE site = ? AND created_at >= ?
                    LIMIT 1
                    """,
                    (incident["site"], format_time(now - RATE_LIMIT)),
                ).fetchone()
                if recent:
                    connection.execute(
                        "UPDATE incidents SET summary_code = ?, updated_at = ? WHERE id = ?",
                        ("rate_limited_shadowed", format_time(now), incident["id"]),
                    )
                    if self._record_decision(
                        connection,
                        int(incident["id"]),
                        "rate_limited",
                        "rate_limited_shadowed",
                    ):
                        self._increment(connection, "shadow_rate_limited")
                    continue
                self._record_decision(
                    connection,
                    int(incident["id"]),
                    "shadowed",
                    "vacant_activity_shadowed",
                )
                connection.execute(
                    """
                    INSERT INTO notification_outbox(
                        incident_id, site, status, attempt_count, created_at, updated_at
                    ) VALUES (?, ?, 'shadowed', 0, ?, ?)
                    """,
                    (incident["id"], incident["site"], format_time(now), format_time(now)),
                )
                connection.execute(
                    "UPDATE incidents SET summary_code = ?, updated_at = ? WHERE id = ?",
                    ("vacant_activity_shadowed", format_time(now), incident["id"]),
                )
                self._increment(connection, "shadow_delivery_decisions")
                decisions += 1
            connection.commit()
        return decisions

    def run_once(self, *, limit: int = 20) -> Mapping[str, Any]:
        presence = read_presence(self.presence_state, self.now())
        claimed = self.store.claim_deliveries(CONSUMER, limit=limit)
        acknowledged = 0
        dead = 0
        deliveries = sorted(
            claimed["deliveries"],
            key=lambda item: (item["observed_at"], int(item["event_id"])),
        )
        for delivery in deliveries:
            try:
                self._process_delivery(delivery, presence, claimed["lease_token"])
                acknowledged += 1
            except (CorrelatorError, HomeEventError, sqlite3.Error):
                if int(delivery.get("attempts", 1)) >= 5:
                    self.store.dead_letter_delivery(
                        CONSUMER,
                        delivery["delivery_id"],
                        claimed["lease_token"],
                        "correlation_failed",
                    )
                    dead += 1
                else:
                    break
        expired = self._expire_incidents()
        decisions = self._finalize_shadow_decisions(presence)
        self.store.write_status_best_effort()
        return {
            "ok": True,
            "mode": "shadow",
            "claimed": len(claimed["deliveries"]),
            "acknowledged": acknowledged,
            "dead_lettered": dead,
            "expired": expired,
            "shadow_decisions": decisions,
        }


def parser() -> argparse.ArgumentParser:
    value = argparse.ArgumentParser(description="Shadow-only home event correlator")
    value.add_argument(
        "--root",
        type=Path,
        default=Path(os.environ.get("HOME_EVENTS_ROOT", "~/.openclaw/home-events")).expanduser(),
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
    value.add_argument("--limit", type=int, default=20)
    return value


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        result = ShadowCorrelator(args.root, args.presence_state).run_once(limit=args.limit)
    except (CorrelatorError, HomeEventError, sqlite3.Error, OSError) as exc:
        code = exc.code if hasattr(exc, "code") and SAFE_CODE_RE.fullmatch(exc.code) else "correlator_failed"
        print(json.dumps({"ok": False, "error_code": code}, separators=(",", ":")))
        return 1
    print(json.dumps(result, separators=(",", ":"), sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
