#!/usr/bin/env python3
"""Fake-only tests for the Nest-to-home-events bridge."""

from __future__ import annotations

import contextlib
import hashlib
import importlib.util
import json
import os
import sqlite3
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
BRIDGE = REPO_ROOT / "openclaw" / "bin" / "nest-home-event-bridge.py"
BUS_PATH = REPO_ROOT / "openclaw" / "bin" / "home_event_bus.py"

BUS_SPEC = importlib.util.spec_from_file_location("nest_bridge_test_bus", BUS_PATH)
assert BUS_SPEC and BUS_SPEC.loader
BUS = importlib.util.module_from_spec(BUS_SPEC)
sys.modules[BUS_SPEC.name] = BUS
BUS_SPEC.loader.exec_module(BUS)


class NestHomeEventBridgeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.home = self.root / "home"
        self.home_events_root = self.home / ".openclaw" / "home-events"
        self.state_directory = self.home_events_root / "state"
        self.nest_state = self.home / ".openclaw" / "nest-events" / "state"
        self.database = self.nest_state / "events.sqlite3"
        self.bin_directory = self.root / "bin"
        self.home_eventctl = self.bin_directory / "home-eventctl"
        self.events_log = self.root / "events.jsonl"
        for directory in (
            self.home,
            self.home / ".openclaw",
            self.home_events_root,
            self.state_directory,
            self.home / ".openclaw" / "nest-events",
            self.nest_state,
            self.bin_directory,
        ):
            directory.mkdir(exist_ok=True, mode=0o700)
            directory.chmod(0o700)
        self._create_database()
        self.home_eventctl.write_text(
            """#!/usr/bin/env python3
import json, os, sys
payload = json.load(sys.stdin)
with open(os.environ['EVENTS_LOG'], 'a', encoding='utf-8') as handle:
    handle.write(json.dumps({'args': sys.argv[1:], 'payload': payload}) + '\\n')
raise SystemExit(9 if os.environ.get('FAKE_PUBLISH_FAIL') == '1' else 0)
""",
            encoding="utf-8",
        )
        self.home_eventctl.chmod(0o700)

    def _create_database(self) -> None:
        with contextlib.closing(sqlite3.connect(self.database)) as connection, connection:
            connection.executescript(
                """
                CREATE TABLE schema_meta(version INTEGER NOT NULL);
                INSERT INTO schema_meta(version) VALUES (3);
                CREATE TABLE event_records(
                    id INTEGER PRIMARY KEY,
                    dedupe_key TEXT NOT NULL UNIQUE,
                    alias TEXT NOT NULL,
                    site TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    first_occurred_at TEXT NOT NULL
                );
                CREATE TABLE outbox(
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    event_record_id INTEGER NOT NULL UNIQUE,
                    alias TEXT NOT NULL,
                    site TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    event_at TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                """
            )
        self.database.chmod(0o600)

    @property
    def cursor_path(self) -> Path:
        return self.state_directory / "nest-bridge.json"

    def environment(self, **overrides: str) -> dict[str, str]:
        environment = os.environ.copy()
        environment.update(
            {
                "HOME": str(self.home),
                "HOME_EVENTS_NEST_ENABLED": "1",
                "HOME_EVENTS_ROOT": str(self.home_events_root),
                "NEST_EVENT_DATABASE": str(self.database),
                "HOME_EVENTCTL": str(self.home_eventctl),
                "EVENTS_LOG": str(self.events_log),
            }
        )
        environment.update(overrides)
        return environment

    def run_bridge(self, **overrides: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, str(BRIDGE)],
            capture_output=True,
            text=True,
            check=False,
            env=self.environment(**overrides),
        )

    def insert_event(
        self,
        row_id: int,
        *,
        alias: str = "Kitchen",
        site: str = "Cabin",
        event_type: str = "person",
        event_at: str = "2026-07-14T12:00:00.000000Z",
        created_at: str = "2026-07-14T12:00:01.000000Z",
        record_alias: str | None = None,
        record_site: str | None = None,
        record_event_type: str | None = None,
    ) -> str:
        dedupe_key = hashlib.sha256(f"fixture-{row_id}".encode()).hexdigest()
        with contextlib.closing(sqlite3.connect(self.database)) as connection, connection:
            connection.execute(
                """
                INSERT INTO event_records(
                    id, dedupe_key, alias, site, event_type, first_occurred_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    row_id,
                    dedupe_key,
                    record_alias or alias,
                    record_site or site,
                    record_event_type or event_type,
                    event_at,
                ),
            )
            connection.execute(
                """
                INSERT INTO outbox(
                    id, event_record_id, alias, site, event_type,
                    event_at, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    row_id,
                    row_id,
                    alias,
                    site,
                    event_type,
                    event_at,
                    created_at,
                ),
            )
        self.database.chmod(0o600)
        return dedupe_key

    def cursor(self) -> dict:
        return json.loads(self.cursor_path.read_text(encoding="utf-8"))

    def write_cursor(self, value: dict) -> None:
        self.cursor_path.write_text(
            json.dumps(value, sort_keys=True) + "\n", encoding="utf-8"
        )
        self.cursor_path.chmod(0o600)

    def database_birthtime_us(self) -> int:
        return int(self.database.stat().st_birthtime * 1_000_000)

    def published(self) -> list[dict]:
        if not self.events_log.exists():
            return []
        return [json.loads(line) for line in self.events_log.read_text().splitlines()]

    def test_disabled_by_default_touches_nothing(self) -> None:
        absent_root = self.root / "absent-home-events"
        absent_database = self.root / "absent.sqlite3"

        result = self.run_bridge(
            HOME_EVENTS_NEST_ENABLED="0",
            HOME_EVENTS_ROOT=str(absent_root),
            NEST_EVENT_DATABASE=str(absent_database),
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)["mode"], "disabled")
        self.assertFalse(absent_root.exists())
        self.assertFalse(self.events_log.exists())

    def test_first_enable_silently_baselines_current_outbox(self) -> None:
        self.insert_event(1)

        result = self.run_bridge()

        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["mode"], "baseline")
        self.assertEqual(payload["last_outbox_id"], 1)
        self.assertEqual(self.published(), [])
        cursor = self.cursor()
        database_stat = self.database.stat()
        self.assertEqual(cursor["version"], 2)
        self.assertEqual(cursor["database_device"], database_stat.st_dev)
        self.assertEqual(cursor["database_inode"], database_stat.st_ino)
        self.assertEqual(
            cursor["database_birthtime_us"], self.database_birthtime_us()
        )
        self.assertEqual(cursor["last_outbox_id"], 1)
        self.assertEqual(stat.S_IMODE(self.cursor_path.stat().st_mode), 0o600)

    def test_listener_v1_schema_is_rejected_before_cursor_creation(self) -> None:
        with contextlib.closing(sqlite3.connect(self.database)) as connection, connection:
            connection.execute("UPDATE schema_meta SET version = 1")

        result = self.run_bridge()

        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertEqual(json.loads(result.stdout)["error_code"], "database_schema")
        self.assertFalse(self.cursor_path.exists())

    def test_listener_v2_schema_remains_supported_for_rollback(self) -> None:
        with contextlib.closing(sqlite3.connect(self.database)) as connection, connection:
            connection.execute("UPDATE schema_meta SET version = 2")
        self.insert_event(1)

        result = self.run_bridge()

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)["mode"], "baseline")
        self.assertEqual(self.cursor()["last_outbox_id"], 1)
        self.assertEqual(self.published(), [])

    def test_future_listener_schema_is_rejected_before_cursor_creation(self) -> None:
        with contextlib.closing(sqlite3.connect(self.database)) as connection, connection:
            connection.execute("UPDATE schema_meta SET version = 4")

        result = self.run_bridge()

        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertEqual(json.loads(result.stdout)["error_code"], "database_schema")
        self.assertFalse(self.cursor_path.exists())

    def test_pruned_empty_outbox_retains_autoincrement_watermark(self) -> None:
        self.insert_event(1)
        self.assertEqual(self.run_bridge().returncode, 0)
        with contextlib.closing(sqlite3.connect(self.database)) as connection, connection:
            connection.execute("DELETE FROM outbox")
            connection.execute("DELETE FROM event_records")

        result = self.run_bridge()

        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["mode"], "published")
        self.assertEqual(payload["event_count"], 0)
        self.assertEqual(payload["last_outbox_id"], 1)
        self.assertEqual(self.cursor()["last_outbox_id"], 1)

    def test_future_camera_events_publish_exact_safe_contract(self) -> None:
        self.insert_event(1)
        self.assertEqual(self.run_bridge().returncode, 0)
        expected = [
            (
                2,
                "Kitchen",
                "Cabin",
                "person",
                "kitchen",
                "cabin",
                "camera.person_detected",
                "person",
            ),
            (
                3,
                "Living Room",
                "Crosstown",
                "motion",
                "living_room",
                "crosstown",
                "camera.motion_detected",
                "motion",
            ),
            (
                4,
                "Living Room Wired",
                "Crosstown",
                "person",
                "living_room_wired",
                "crosstown",
                "camera.person_detected",
                "person",
            ),
        ]
        source_ids = {}
        for row_id, alias, site, event_type, *_ in expected:
            source_ids[row_id] = self.insert_event(
                row_id,
                alias=alias,
                site=site,
                event_type=event_type,
                event_at=f"2026-07-14T12:0{row_id}:00.000000Z",
                created_at=f"2026-07-14T12:0{row_id}:01.000000Z",
            )

        result = self.run_bridge()

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)["event_count"], 3)
        published = self.published()
        self.assertEqual(len(published), 3)
        for entry, expectation in zip(published, expected):
            (
                row_id,
                _source_alias,
                _source_site,
                _source_type,
                alias,
                site,
                event_type,
                classification,
            ) = expectation
            self.assertEqual(entry["args"], ["enqueue", "--source", "nest"])
            event = entry["payload"]
            self.assertEqual(
                set(event),
                {
                    "source_event_id",
                    "event_type",
                    "site",
                    "entity_kind",
                    "entity_alias",
                    "occurred_at",
                    "observed_at",
                    "time_precision",
                    "attributes",
                },
            )
            self.assertEqual(event["source_event_id"], source_ids[row_id])
            self.assertEqual(event["event_type"], event_type)
            self.assertEqual(event["site"], site)
            self.assertEqual(event["entity_kind"], "camera")
            self.assertEqual(event["entity_alias"], alias)
            self.assertEqual(event["time_precision"], "source")
            self.assertEqual(event["attributes"], {"classification": classification})
            self.assertNotIn("resource", json.dumps(event).lower())
            self.assertNotIn("image", json.dumps(event).lower())
            BUS.normalize_input(
                "nest",
                event,
                b"n" * 32,
                clock=lambda: "2026-07-14T12:10:00Z",
            )
        self.assertEqual(self.cursor()["last_outbox_id"], 4)

    def test_publish_failure_retries_same_stable_source_id(self) -> None:
        self.insert_event(1)
        self.assertEqual(self.run_bridge().returncode, 0)
        source_id = self.insert_event(2)

        failed = self.run_bridge(FAKE_PUBLISH_FAIL="1")

        self.assertNotEqual(failed.returncode, 0)
        self.assertEqual(json.loads(failed.stdout)["error_code"], "publisher_failed")
        self.assertEqual(self.cursor()["last_outbox_id"], 1)
        retried = self.run_bridge()
        self.assertEqual(retried.returncode, 0, retried.stderr)
        attempts = self.published()
        self.assertEqual(len(attempts), 2)
        self.assertEqual(
            [attempt["payload"]["source_event_id"] for attempt in attempts],
            [source_id, source_id],
        )
        self.assertEqual(self.cursor()["last_outbox_id"], 2)

    def test_legacy_cursor_survives_reboot_device_change(self) -> None:
        self.insert_event(1)
        self.assertEqual(self.run_bridge().returncode, 0)
        current_cursor = self.cursor()
        self.write_cursor(
            {
                "version": 1,
                "database_device": current_cursor["database_device"] + 1,
                "database_inode": current_cursor["database_inode"],
                "last_outbox_id": current_cursor["last_outbox_id"],
            }
        )
        source_id = self.insert_event(2)

        result = self.run_bridge()

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)["event_count"], 1)
        self.assertEqual(
            [entry["payload"]["source_event_id"] for entry in self.published()],
            [source_id],
        )
        migrated = self.cursor()
        database_stat = self.database.stat()
        self.assertEqual(migrated["version"], 2)
        self.assertEqual(migrated["database_device"], database_stat.st_dev)
        self.assertEqual(migrated["database_inode"], database_stat.st_ino)
        self.assertEqual(
            migrated["database_birthtime_us"], self.database_birthtime_us()
        )
        self.assertEqual(migrated["last_outbox_id"], 2)

    def test_current_cursor_refreshes_reboot_device_change(self) -> None:
        self.insert_event(1)
        self.assertEqual(self.run_bridge().returncode, 0)
        drifted = self.cursor()
        drifted["database_device"] += 1
        self.write_cursor(drifted)

        result = self.run_bridge()

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)["event_count"], 0)
        refreshed = self.cursor()
        self.assertEqual(
            refreshed["database_device"], self.database.stat().st_dev
        )
        self.assertEqual(refreshed["last_outbox_id"], 1)
        self.assertEqual(self.published(), [])

    def test_legacy_cursor_rejects_database_newer_than_cursor(self) -> None:
        self.insert_event(1)
        self.assertEqual(self.run_bridge().returncode, 0)
        current_cursor = self.cursor()
        legacy = {
            "version": 1,
            "database_device": current_cursor["database_device"] + 1,
            "database_inode": current_cursor["database_inode"],
            "last_outbox_id": current_cursor["last_outbox_id"],
        }
        self.write_cursor(legacy)
        database_birthtime = self.database.stat().st_birthtime
        os.utime(
            self.cursor_path,
            (database_birthtime - 1, database_birthtime - 1),
        )
        before = self.cursor_path.read_bytes()

        result = self.run_bridge()

        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertEqual(
            json.loads(result.stdout)["error_code"], "database_replaced"
        )
        self.assertEqual(self.cursor_path.read_bytes(), before)
        self.assertEqual(self.published(), [])

    def test_current_cursor_rejects_birthtime_change(self) -> None:
        self.insert_event(1)
        self.assertEqual(self.run_bridge().returncode, 0)
        replaced = self.cursor()
        replaced["database_birthtime_us"] += 1
        self.write_cursor(replaced)
        before = self.cursor_path.read_bytes()

        result = self.run_bridge()

        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertEqual(
            json.loads(result.stdout)["error_code"], "database_replaced"
        )
        self.assertEqual(self.cursor_path.read_bytes(), before)
        self.assertEqual(self.published(), [])

    def test_legacy_cursor_does_not_migrate_across_rewind(self) -> None:
        self.insert_event(1)
        self.assertEqual(self.run_bridge().returncode, 0)
        current_cursor = self.cursor()
        legacy = {
            "version": 1,
            "database_device": current_cursor["database_device"] + 1,
            "database_inode": current_cursor["database_inode"],
            "last_outbox_id": 2,
        }
        self.write_cursor(legacy)
        before = self.cursor_path.read_bytes()

        result = self.run_bridge()

        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertEqual(
            json.loads(result.stdout)["error_code"], "database_rewound"
        )
        self.assertEqual(self.cursor_path.read_bytes(), before)
        self.assertEqual(self.published(), [])

    def test_replaced_database_fails_closed_without_moving_cursor(self) -> None:
        self.insert_event(1)
        self.assertEqual(self.run_bridge().returncode, 0)
        old_cursor = self.cursor()
        old_inode = old_cursor["database_inode"]
        self.database.unlink()
        self._create_database()
        self.insert_event(1)
        self.insert_event(2)
        if self.database.stat().st_ino == old_inode:
            self.skipTest("filesystem immediately reused the database inode")

        result = self.run_bridge()

        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(json.loads(result.stdout)["error_code"], "database_replaced")
        self.assertEqual(self.published(), [])
        self.assertEqual(self.cursor(), old_cursor)

    def test_same_database_sequence_rewind_fails_closed_without_moving_cursor(self) -> None:
        self.insert_event(1)
        self.insert_event(2)
        self.assertEqual(self.run_bridge().returncode, 0)
        old_cursor = self.cursor()
        with contextlib.closing(sqlite3.connect(self.database)) as connection, connection:
            connection.execute("DELETE FROM outbox")
            connection.execute("DELETE FROM event_records")
            connection.execute(
                "UPDATE sqlite_sequence SET seq = 0 WHERE name = 'outbox'"
            )

        result = self.run_bridge()

        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(json.loads(result.stdout)["error_code"], "database_rewound")
        self.assertEqual(self.cursor(), old_cursor)
        self.assertEqual(self.published(), [])

    def test_unbound_or_inconsistent_camera_row_fails_closed(self) -> None:
        self.insert_event(1)
        self.assertEqual(self.run_bridge().returncode, 0)
        self.insert_event(2, alias="Kitchen", site="Crosstown")

        unbound = self.run_bridge()

        self.assertNotEqual(unbound.returncode, 0)
        self.assertEqual(json.loads(unbound.stdout)["error_code"], "unbound_outbox_row")
        self.assertEqual(self.cursor()["last_outbox_id"], 1)
        self.assertEqual(self.published(), [])

    def test_unsafe_database_permissions_fail_closed(self) -> None:
        self.database.chmod(0o644)

        result = self.run_bridge()

        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(json.loads(result.stdout)["error_code"], "unsafe_database")
        self.assertFalse(self.cursor_path.exists())
        self.assertEqual(self.published(), [])


if __name__ == "__main__":
    unittest.main()
