#!/usr/bin/env python3
"""Fake-only route durability and network-observation tests for dog-walk."""

from __future__ import annotations

import importlib.util
import inspect
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import types
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock


REPO_ROOT = Path(__file__).resolve().parents[2]
LISTENER_PATH = REPO_ROOT / "openclaw/skills/dog-walk/dog-walk-listener.py"


def load_listener(fake_home: Path):
    fake_aiohttp = types.ModuleType("aiohttp")
    fake_ring = types.ModuleType("ring_doorbell")
    fake_ring_listen = types.ModuleType("ring_doorbell.listen")
    fake_listener_config = types.ModuleType("ring_doorbell.listen.listenerconfig")

    class Placeholder:
        pass

    fake_ring.Auth = Placeholder
    fake_ring.Ring = Placeholder
    fake_ring.RingEvent = Placeholder
    fake_ring.RingEventListener = Placeholder
    fake_listener_config.RingEventListenerConfig = Placeholder

    replacements = {
        "aiohttp": fake_aiohttp,
        "ring_doorbell": fake_ring,
        "ring_doorbell.listen": fake_ring_listen,
        "ring_doorbell.listen.listenerconfig": fake_listener_config,
    }
    previous_modules = {name: sys.modules.get(name) for name in replacements}
    previous_home = os.environ.get("HOME")
    try:
        sys.modules.update(replacements)
        os.environ["HOME"] = str(fake_home)
        spec = importlib.util.spec_from_file_location(
            "dog_walk_listener_durability_test", LISTENER_PATH
        )
        assert spec and spec.loader
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    finally:
        if previous_home is None:
            os.environ.pop("HOME", None)
        else:
            os.environ["HOME"] = previous_home
        for name, previous in previous_modules.items():
            if previous is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = previous


class DogWalkDurabilityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.class_tempdir = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.class_tempdir.cleanup)
        cls.module = load_listener(Path(cls.class_tempdir.name))
        cls.module.log = lambda _message: None

    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tempdir.cleanup)
        self.root = Path(self.tempdir.name)
        self.module.ROUTES_DIR = self.root / "routes"
        self.module._route_locks.clear()
        self.walk_id = "walk-test-1"
        self.origin = "cabin"
        self.started_at = "2026-07-05T12:00:00Z"
        self.path = self.module._route_path(
            self.walk_id, self.origin, self.started_at
        )
        assert self.path is not None

    def write_route(self, **extra) -> dict:
        route = self.module._new_route(
            self.walk_id, self.origin, self.started_at
        )
        route.update(
            {
                "ended_at": "2026-07-05T12:30:00Z",
                "custom_metadata": {"preserve": True},
                **extra,
            }
        )
        self.module._write_json_file(self.path, route)
        return route

    def test_atomic_replace_fault_preserves_previous_route(self) -> None:
        original = self.write_route()
        replacement = {**original, "is_car_trip": True}

        with mock.patch.object(
            self.module.os, "replace", side_effect=OSError("injected replace fault")
        ):
            with self.assertRaises(OSError):
                self.module._write_json_file(self.path, replacement)

        self.assertEqual(json.loads(self.path.read_text()), original)
        self.assertEqual(list(self.path.parent.glob(f".{self.path.name}.*.tmp")), [])

    def test_all_route_mutators_use_the_locked_atomic_writer(self) -> None:
        mutators = [
            self.module._init_walk_route,
            self.module._append_walk_route_point,
            self.module._finalize_walk_route,
            self.module._enrich_route_with_fi_walks,
            self.module._mark_route_car_trip,
            self.module._merge_walk_path_into_route,
        ]
        for mutator in mutators:
            with self.subTest(mutator=mutator.__name__):
                source = inspect.getsource(mutator)
                self.assertIn("_route_lock(", source)
                self.assertIn("_write_json_file(", source)
                self.assertNotIn(".write_text(", source)

    def test_concurrent_enrichment_and_car_mark_preserve_each_others_fields(self) -> None:
        self.write_route()
        merge_entered = threading.Event()
        release_merge = threading.Event()
        car_started = threading.Event()
        errors: list[BaseException] = []

        merged = {
            "fi_start": "2026-07-05T11:59:00Z",
            "fi_end": "2026-07-05T12:31:00Z",
            "fi_distance_m": 1234,
            "fi_walker": "Dylan",
            "fi_walk_count": 2,
        }
        active_state = {
            "dog_walk": {
                "active": True,
                "walk_id": self.walk_id,
                "origin_location": self.origin,
                "departed_at": self.started_at,
            }
        }

        def blocking_merge(*_args, **_kwargs):
            merge_entered.set()
            if not release_merge.wait(2):
                raise TimeoutError("test did not release enrichment")
            return merged

        def capture_errors(function):
            try:
                function()
            except BaseException as error:  # surfaced in the parent test thread
                errors.append(error)

        def enrich() -> None:
            self.module._enrich_route_with_fi_walks(
                [{"fake": True}],
                walk_id=self.walk_id,
                origin=self.origin,
                started_at=self.started_at,
            )

        def mark_car() -> None:
            car_started.set()
            self.module._mark_route_car_trip(self.origin)

        with (
            mock.patch.object(self.module, "_merge_fi_walks", blocking_merge),
            mock.patch.object(self.module, "_read_state", return_value=active_state),
        ):
            enrich_thread = threading.Thread(
                target=lambda: capture_errors(enrich), daemon=True
            )
            car_thread = threading.Thread(
                target=lambda: capture_errors(mark_car), daemon=True
            )
            enrich_thread.start()
            self.assertTrue(merge_entered.wait(1), "enrichment never entered mutation")
            car_thread.start()
            self.assertTrue(car_started.wait(1), "car mutation never started")
            time.sleep(0.05)
            self.assertTrue(car_thread.is_alive(), "car mutation bypassed route lock")
            release_merge.set()
            enrich_thread.join(2)
            car_thread.join(2)

        self.assertFalse(enrich_thread.is_alive())
        self.assertFalse(car_thread.is_alive())
        self.assertEqual(errors, [])
        route = json.loads(self.path.read_text())
        self.assertTrue(route["is_car_trip"])
        self.assertEqual(route["fi_distance_m"], 1234)
        self.assertEqual(route["fi_walk_count"], 2)
        self.assertEqual(route["custom_metadata"], {"preserve": True})

    def fresh_observation(self, location: str, **presence) -> str:
        return json.dumps(
            {
                "location": location,
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "presence": presence
                or {
                    "Dylan": {"present": True},
                    "Julia": {"present": False},
                },
            }
        )

    def test_network_checks_call_only_read_only_observation_modes(self) -> None:
        completed = subprocess.CompletedProcess(
            args=[],
            returncode=0,
            stdout=self.fresh_observation("cabin"),
            stderr="",
        )
        with mock.patch.object(
            self.module.subprocess, "run", return_value=completed
        ) as fake_run:
            result = self.module._check_network_presence("cabin")

        self.assertTrue(result["ok"])
        self.assertTrue(result["any_present"])
        command = fake_run.call_args.args[0]
        self.assertEqual(command[-2:], ["observe", "cabin"])

        completed.stdout = self.fresh_observation("crosstown")
        with mock.patch.object(
            self.module.subprocess, "run", return_value=completed
        ) as fake_run:
            result = self.module._check_network_presence("crosstown")

        self.assertTrue(result["ok"])
        command = fake_run.call_args.args[0]
        self.assertIn("BatchMode=yes", command)
        self.assertEqual(command[-2:], ["observe", "crosstown"])

    def test_malformed_unavailable_and_stale_observations_fail_closed(self) -> None:
        stale = json.dumps(
            {
                "location": "cabin",
                "timestamp": (
                    datetime.now(timezone.utc) - timedelta(minutes=10)
                ).isoformat(),
                "presence": {"Dylan": {"present": True}},
            }
        )
        cases = [
            subprocess.CompletedProcess([], 1, "", "fake failure"),
            subprocess.CompletedProcess([], 0, "not-json", ""),
            subprocess.CompletedProcess([], 0, stale, ""),
            subprocess.CompletedProcess(
                [],
                0,
                self.fresh_observation(
                    "cabin", Dylan={"present": "yes"}
                ),
                "",
            ),
        ]
        for completed in cases:
            with self.subTest(stdout=completed.stdout, rc=completed.returncode):
                with mock.patch.object(
                    self.module.subprocess, "run", return_value=completed
                ):
                    result = self.module._check_network_presence("cabin")
                self.assertFalse(result["ok"])
                self.assertFalse(result["any_present"])
                self.assertEqual(result["people"], {})

    def test_walker_detection_does_not_guess_when_observation_fails_or_omits_people(self) -> None:
        with (
            mock.patch.object(
                self.module, "_people_at_location", return_value={"dylan", "julia"}
            ),
            mock.patch.object(
                self.module,
                "_recently_present_on_network",
                return_value={"dylan", "julia"},
            ),
            mock.patch.object(
                self.module,
                "_run_network_observation",
                return_value={"ok": False, "people": {}, "error": "fake"},
            ),
        ):
            self.assertEqual(self.module._detect_who_left("cabin"), [])

        with (
            mock.patch.object(
                self.module, "_people_at_location", return_value={"dylan", "julia"}
            ),
            mock.patch.object(
                self.module,
                "_recently_present_on_network",
                return_value={"dylan", "julia"},
            ),
            mock.patch.object(
                self.module,
                "_run_network_observation",
                return_value={
                    "ok": True,
                    "people": {"Dylan": {"present": False}},
                },
            ),
        ):
            self.assertEqual(self.module._detect_who_left("cabin"), [])


if __name__ == "__main__":
    unittest.main()
