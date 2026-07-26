#!/usr/bin/env python3
"""Focused tests for the ordered Ring-to-Kitchen entry verifier."""

from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import tempfile
import time
import unittest


OPENCLAW = Path(__file__).resolve().parents[1]
BIN_DIR = OPENCLAW / "bin"


def load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    import sys

    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


bus = load_module("cabin_entry_bus", BIN_DIR / "home_event_bus.py")
verifier_module = load_module(
    "cabin_entry_verifier", BIN_DIR / "cabin-entry-verifier.py"
)


class FakeClock:
    def __init__(self, value: float):
        self.value = value

    def __call__(self) -> float:
        return self.value

    def advance(self, seconds: float) -> None:
        self.value += seconds


class FakeCommands:
    def __init__(
        self,
        clock: FakeClock,
        decisions: tuple[verifier_module.VisionDecision, ...] | None = None,
    ):
        self.clock = clock
        self.capture_times: list[float] = []
        self.analyzed: list[str] = []
        self.messages = 0
        self.decisions = list(
            decisions
            or (
                verifier_module.VisionDecision(False, "high"),
                verifier_module.VisionDecision(False, "high"),
            )
        )

    def capture(self, path: Path) -> None:
        self.capture_times.append(self.clock())
        path.write_bytes(b"\xff\xd8\xfffixture\xff\xd9")
        path.chmod(0o600)
        os.utime(path, (self.clock(), self.clock()))

    def analyze_person(self, path: Path) -> verifier_module.VisionDecision:
        self.analyzed.append(path.name)
        return self.decisions.pop(0)

    def send_confirmation(self):
        self.messages += 1
        return verifier_module.reviewer.DeliveryReceipt("bridge", "fixture")


class CabinEntryVerifierTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.home = Path(self.temporary.name) / "home"
        self.openclaw = self.home / ".openclaw"
        self.openclaw.mkdir(parents=True, mode=0o700)
        self.bus_root = self.openclaw / "home-events"
        self.clock = FakeClock(
            time.mktime(time.strptime("2026-07-26T16:00:00Z", "%Y-%m-%dT%H:%M:%SZ"))
        )
        bus.initialize_runtime(
            self.bus_root,
            clock=lambda: verifier_module.timestamp(self.clock()),
        )
        self.presence_dir = self.openclaw / "presence"
        self.presence_dir.mkdir(mode=0o755)
        self.presence_state = self.presence_dir / "state.json"
        self.cabin_scan = self.presence_dir / "cabin-scan.json"
        self.crosstown_scan = self.presence_dir / "crosstown-scan.json"
        self.write_presence()
        self.state_dir = self.openclaw / "cabin-entry-verifier"
        self.state_dir.mkdir(mode=0o700)
        (self.state_dir / "images").mkdir(mode=0o700)
        self.environ = {
            "HOME": str(self.home),
            "CABIN_ENTRY_MODE": verifier_module.MODE,
            "HOME_EVENTS_ROOT": str(self.bus_root),
            "CABIN_ENTRY_STATE_DIR": str(self.state_dir),
            "CABIN_ENTRY_DATABASE": str(self.state_dir / "state.sqlite3"),
            "CABIN_ENTRY_IMAGE_DIR": str(self.state_dir / "images"),
            "CABIN_ENTRY_LOCK_FILE": str(self.state_dir / "service.lock"),
            "OPENCLAW_PRESENCE_STATE": str(self.presence_state),
            "OPENCLAW_PRESENCE_CABIN_SCAN": str(self.cabin_scan),
            "OPENCLAW_PRESENCE_CROSSTOWN_SCAN": str(self.crosstown_scan),
            "OPENCLAW_DYLAN_IMESSAGE_TARGET": "chat_id:7",
        }
        self.settings = verifier_module.load_settings(self.environ)
        self.commands = FakeCommands(self.clock)
        self.verifier = verifier_module.CabinEntryVerifier(
            self.settings,
            commands=self.commands,
            clock=self.clock,
        )
        self.verifier.initialize()
        self.verifier.register()
        self.sequence = 0

    def write_presence(self, *, occupancy: str = "confirmed_vacant") -> None:
        now = verifier_module.timestamp(self.clock())
        people = {
            person: {
                "cabin": False,
                "crosstown": True,
                "location": "crosstown",
            }
            for person in ("Dylan", "Julia")
        }
        state = {
            "timestamp": now,
            "people": people,
            "cabin": {
                "occupancy": occupancy,
                "stateChangedAt": verifier_module.timestamp(self.clock() - 3600),
                "fresh": True,
            },
            "crosstown": {
                "occupancy": "occupied",
                "stateChangedAt": verifier_module.timestamp(self.clock() - 3600),
                "fresh": True,
            },
        }
        cabin_scan = {
            "location": "cabin",
            "timestamp": now,
            "presence": {
                person: {"present": occupancy == "occupied"}
                for person in ("Dylan", "Julia")
            },
        }
        crosstown_scan = {
            "location": "crosstown",
            "timestamp": now,
            "presence": {
                person: {"present": True}
                for person in ("Dylan", "Julia")
            },
        }
        for path, value, mode in (
            (self.presence_state, state, 0o600),
            (self.cabin_scan, cabin_scan, 0o600),
            (self.crosstown_scan, crosstown_scan, 0o600),
        ):
            path.write_text(json.dumps(value), encoding="utf-8")
            path.chmod(mode)

    def enqueue_ring(self, alias: str, event_type: str = "entry.motion_detected") -> None:
        self.sequence += 1
        now = verifier_module.timestamp(self.clock())
        payload = {
            "schema_version": 1,
            "source_event_id": f"ring-fixture-{self.sequence}",
            "event_type": event_type,
            "site": "cabin",
            "entity_kind": "doorbell",
            "entity_alias": alias,
            "occurred_at": now,
            "observed_at": now,
            "time_precision": "source",
            "attributes": {
                "classification": (
                    "person" if event_type == "entry.person_detected" else "motion"
                ),
                "backfill": False,
            },
        }
        result = bus.enqueue_event(
            self.bus_root,
            "ring",
            json.dumps(payload).encode("utf-8"),
            clock=lambda: now,
        )
        self.assertEqual(result.source, "ring")
        ingested = bus.ingest_once(
            self.bus_root,
            clock=lambda: now,
        )
        self.assertEqual(ingested.accepted, 1)

    def pending_jobs(self) -> int:
        return int(
            self.verifier.state.status(registered=True)["pendingJobs"]
        )

    def test_requires_driveway_then_front_door_and_uses_front_door_offsets(self) -> None:
        self.enqueue_ring("front_door")
        self.verifier.run_once()
        self.assertEqual(self.pending_jobs(), 0)

        self.clock.advance(15)
        self.write_presence()
        self.enqueue_ring("driveway")
        self.verifier.run_once()
        self.assertEqual(self.pending_jobs(), 0)

        self.clock.advance(45)
        self.write_presence()
        front_at = self.clock()
        self.enqueue_ring("front_door", "entry.person_detected")
        scheduled = self.verifier.run_once()
        self.assertEqual(scheduled["outcome"], "waiting")
        self.assertEqual(self.pending_jobs(), 1)

        self.clock.advance(29)
        self.assertEqual(self.verifier.run_once()["outcome"], "waiting")
        self.assertEqual(self.commands.capture_times, [])

        self.clock.advance(1)
        self.assertEqual(self.verifier.run_once()["outcome"], "captured")
        self.assertEqual(self.commands.capture_times, [front_at + 30])

        self.clock.advance(29)
        self.assertEqual(self.verifier.run_once()["outcome"], "waiting")
        self.clock.advance(1)
        self.assertEqual(self.verifier.run_once()["outcome"], "captured")
        self.assertEqual(
            self.commands.capture_times,
            [front_at + 30, front_at + 60],
        )

        completed = self.verifier.run_once()
        self.assertEqual(completed["outcome"], "no_person_visible")
        self.assertEqual(self.commands.messages, 0)
        self.assertEqual(list((self.state_dir / "images").iterdir()), [])

    def test_positive_either_snapshot_sends_one_fixed_confirmation(self) -> None:
        self.commands.decisions = [
            verifier_module.VisionDecision(False, "high"),
            verifier_module.VisionDecision(True, "medium"),
        ]
        self.enqueue_ring("driveway")
        self.verifier.run_once()
        self.clock.advance(20)
        self.write_presence()
        self.enqueue_ring("front_door")
        self.verifier.run_once()
        self.clock.advance(30)
        self.verifier.run_once()
        self.clock.advance(30)
        self.verifier.run_once()

        result = self.verifier.run_once()
        self.assertEqual(result["outcome"], "sent")
        self.assertEqual(self.commands.messages, 1)
        status = self.verifier.state.status(registered=True)
        self.assertEqual(status["lastResult"], "person_visible")
        self.assertEqual(status["lastNotificationStatus"], "sent")

    def test_driveway_candidate_expires_and_front_door_remains_unmatched(self) -> None:
        self.enqueue_ring("driveway")
        self.verifier.run_once()
        self.clock.advance(verifier_module.ARRIVAL_SEQUENCE_SECONDS + 1)
        self.write_presence()
        self.enqueue_ring("front_door")
        self.verifier.run_once()

        status = self.verifier.state.status(registered=True)
        self.assertEqual(status["pendingJobs"], 0)
        self.assertEqual(status["lastDecision"], "front_door_unmatched")

    def test_occupied_sequence_needs_one_shot_canary(self) -> None:
        self.write_presence(occupancy="occupied")
        self.enqueue_ring("driveway")
        self.verifier.run_once()
        self.clock.advance(10)
        self.write_presence(occupancy="occupied")
        self.enqueue_ring("front_door")
        self.verifier.run_once()
        self.assertEqual(self.pending_jobs(), 0)

        self.verifier.state.arm_canary(10)
        self.clock.advance(10)
        self.write_presence(occupancy="occupied")
        self.enqueue_ring("driveway")
        self.verifier.run_once()
        self.clock.advance(10)
        self.write_presence(occupancy="occupied")
        self.enqueue_ring("front_door")
        self.verifier.run_once()

        status = self.verifier.state.status(registered=True)
        self.assertEqual(status["pendingJobs"], 1)
        self.assertIsNone(status["canaryArmedUntil"])
        self.assertEqual(status["counters"]["canary_triggers"], 1)

    def test_registration_does_not_backfill_existing_events(self) -> None:
        other_home = Path(self.temporary.name) / "other-home"
        other_openclaw = other_home / ".openclaw"
        other_openclaw.mkdir(parents=True, mode=0o700)
        other_bus = other_openclaw / "home-events"
        now = verifier_module.timestamp(self.clock())
        bus.initialize_runtime(other_bus, clock=lambda: now)
        payload = {
            "schema_version": 1,
            "source_event_id": "historical-ring",
            "event_type": "entry.motion_detected",
            "site": "cabin",
            "entity_kind": "doorbell",
            "entity_alias": "driveway",
            "occurred_at": now,
            "observed_at": now,
            "time_precision": "source",
            "attributes": {"classification": "motion", "backfill": False},
        }
        bus.enqueue_event(
            other_bus,
            "ring",
            json.dumps(payload).encode("utf-8"),
            clock=lambda: now,
        )
        bus.ingest_once(other_bus, clock=lambda: now)

        other_presence = other_openclaw / "presence"
        other_presence.mkdir(mode=0o755)
        other_state = other_openclaw / "cabin-entry-verifier"
        other_state.mkdir(mode=0o700)
        (other_state / "images").mkdir(mode=0o700)
        for source, name in (
            (self.presence_state, "state.json"),
            (self.cabin_scan, "cabin-scan.json"),
            (self.crosstown_scan, "crosstown-scan.json"),
        ):
            destination = other_presence / name
            destination.write_bytes(source.read_bytes())
            destination.chmod(0o600)
        environment = dict(self.environ)
        environment.update(
            {
                "HOME": str(other_home),
                "HOME_EVENTS_ROOT": str(other_bus),
                "CABIN_ENTRY_STATE_DIR": str(other_state),
                "CABIN_ENTRY_DATABASE": str(other_state / "state.sqlite3"),
                "CABIN_ENTRY_IMAGE_DIR": str(other_state / "images"),
                "CABIN_ENTRY_LOCK_FILE": str(other_state / "service.lock"),
                "OPENCLAW_PRESENCE_STATE": str(other_presence / "state.json"),
                "OPENCLAW_PRESENCE_CABIN_SCAN": str(
                    other_presence / "cabin-scan.json"
                ),
                "OPENCLAW_PRESENCE_CROSSTOWN_SCAN": str(
                    other_presence / "crosstown-scan.json"
                ),
            }
        )
        instance = verifier_module.CabinEntryVerifier(
            verifier_module.load_settings(environment),
            commands=FakeCommands(self.clock),
            clock=self.clock,
        )
        instance.initialize()
        instance.register()
        result = instance.run_once()
        self.assertEqual(result["claimed"], 0)


if __name__ == "__main__":
    unittest.main()
