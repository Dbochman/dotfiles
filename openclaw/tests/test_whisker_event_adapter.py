#!/usr/bin/env python3
"""Fake-only tests for the future-only Whisker home-event adapter."""

from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import stat
import sys
import tempfile
import unittest
from unittest.mock import patch


REPO_ROOT = Path(__file__).resolve().parents[2]
ADAPTER_PATH = REPO_ROOT / "openclaw" / "bin" / "whisker-event-adapter.py"
BUS_PATH = REPO_ROOT / "openclaw" / "bin" / "home_event_bus.py"


def load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


adapter = load_module("whisker_event_adapter_test", ADAPTER_PATH)
bus = load_module("whisker_event_bus_test", BUS_PATH)


class WhiskerEventAdapterTests(unittest.TestCase):
    NOW = "2026-08-26T22:00:00Z"

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.runtime = self.root / "home-events"
        self.state_dir = self.runtime / "state"
        self.runtime.mkdir(mode=0o700)
        self.state_dir.mkdir(mode=0o700)
        self.bin_dir = self.root / "bin"
        self.bin_dir.mkdir()
        self.observer = self.bin_dir / "litter-robot"
        self.publisher = self.bin_dir / "home-eventctl"
        self.calls = self.root / "calls.log"
        self.events = self.root / "events.jsonl"
        self._write_executable(
            self.observer,
            """#!/usr/bin/env python3
import os, sys
with open(os.environ['CALLS_LOG'], 'a', encoding='utf-8') as handle:
    handle.write(' '.join(sys.argv[1:]) + '\\n')
sys.stdout.write(os.environ['FAKE_OBSERVATION'])
raise SystemExit(int(os.environ.get('FAKE_OBSERVER_EXIT', '0')))
""",
        )
        self._write_executable(
            self.publisher,
            """#!/usr/bin/env python3
import json, os, sys
payload=json.load(sys.stdin)
with open(os.environ['EVENTS_LOG'], 'a', encoding='utf-8') as handle:
    handle.write(json.dumps({'args':sys.argv[1:],'payload':payload})+'\\n')
raise SystemExit(int(os.environ.get('FAKE_PUBLISH_EXIT', '0')))
""",
        )

    @staticmethod
    def _write_executable(path: Path, content: str) -> None:
        path.write_text(content, encoding="utf-8")
        path.chmod(path.stat().st_mode | stat.S_IXUSR)

    @staticmethod
    def activity(at: str, classification: str = "cat_detected") -> dict[str, str]:
        return {"occurredAt": at, "classification": classification}

    def observation(
        self,
        *,
        observed_at: str | None = None,
        cabin: list[dict[str, str]] | None = None,
        crosstown: list[dict[str, str]] | None = None,
        exhausted: bool = True,
    ) -> dict:
        return {
            "ok": True,
            "observedAt": observed_at or self.NOW,
            "robots": [
                {
                    "selector": "crosstown-litter-robot",
                    "site": "crosstown",
                    "historyExhausted": exhausted,
                    "activities": crosstown or [],
                },
                {
                    "selector": "cabin-litter-robot",
                    "site": "cabin",
                    "historyExhausted": exhausted,
                    "activities": cabin or [],
                },
            ],
        }

    def environment(self, observation: dict, **overrides: str) -> dict[str, str]:
        values = {
            "HOME_EVENTS_ROOT": str(self.runtime),
            "HOME_EVENTS_WHISKER_CABIN_ENABLED": "1",
            "HOME_EVENTS_WHISKER_CROSSTOWN_ENABLED": "1",
            "LITTER_ROBOT_BIN": str(self.observer),
            "HOME_EVENTCTL": str(self.publisher),
            "CALLS_LOG": str(self.calls),
            "EVENTS_LOG": str(self.events),
            "FAKE_OBSERVATION": json.dumps(observation),
        }
        values.update(overrides)
        return values

    @property
    def state_path(self) -> Path:
        return self.state_dir / "whisker-adapter.json"

    def run_adapter(self, observation: dict, **overrides: str) -> dict:
        with patch.dict(os.environ, self.environment(observation, **overrides), clear=False):
            return adapter.run_once(clock=lambda: self.NOW)

    def published(self) -> list[dict]:
        if not self.events.exists():
            return []
        return [json.loads(line) for line in self.events.read_text().splitlines()]

    def test_disabled_mode_has_no_observer_or_state_side_effect(self) -> None:
        result = self.run_adapter(
            self.observation(),
            HOME_EVENTS_WHISKER_CABIN_ENABLED="0",
            HOME_EVENTS_WHISKER_CROSSTOWN_ENABLED="0",
        )

        self.assertEqual(result["mode"], "disabled")
        self.assertFalse(self.calls.exists())
        self.assertFalse(self.state_path.exists())

    def test_first_run_baselines_both_sites_silently(self) -> None:
        result = self.run_adapter(
            self.observation(
                cabin=[self.activity("2026-08-26T09:15:39Z")],
                crosstown=[self.activity("2026-08-26T21:08:24Z")],
            )
        )

        self.assertEqual(result, {"ok": True, "mode": "baseline", "baselined": 2, "published": 0})
        self.assertEqual(self.published(), [])
        state = json.loads(self.state_path.read_text(encoding="utf-8"))
        self.assertTrue(all(item["baselined"] for item in state["sites"].values()))
        self.assertTrue(all(item["health"] == "ok" for item in state["sites"].values()))
        self.assertEqual(stat.S_IMODE(self.state_path.stat().st_mode), 0o600)

    def test_new_activity_publishes_once_and_validates_against_bus(self) -> None:
        old = self.activity("2026-08-26T21:08:24Z")
        self.run_adapter(self.observation(crosstown=[old]))
        newer = self.activity("2026-08-26T21:55:00Z", "cat_sensor_interrupted")

        result = self.run_adapter(self.observation(crosstown=[newer, old]))
        repeated = self.run_adapter(self.observation(crosstown=[newer, old]))

        self.assertEqual(result["published"], 1)
        self.assertEqual(repeated["published"], 0)
        published = self.published()
        self.assertEqual(len(published), 1)
        self.assertEqual(published[0]["args"], ["enqueue", "--source", "whisker"])
        payload = published[0]["payload"]
        self.assertEqual(payload["entity_alias"], "crosstown_litter_robot")
        self.assertEqual(payload["attributes"], {"classification": "cat_sensor_interrupted"})
        bus.normalize_input("whisker", payload, b"a" * 32, clock=lambda: self.NOW)

    def test_publisher_failure_does_not_checkpoint_new_activity(self) -> None:
        old = self.activity("2026-08-26T21:08:24Z")
        self.run_adapter(self.observation(crosstown=[old]))
        newer = self.activity("2026-08-26T21:55:00Z")

        with self.assertRaisesRegex(adapter.AdapterError, "publisher_failed"):
            self.run_adapter(
                self.observation(crosstown=[newer, old]),
                FAKE_PUBLISH_EXIT="8",
            )
        retried = self.run_adapter(self.observation(crosstown=[newer, old]))

        self.assertEqual(retried["published"], 1)

    def test_missing_overlap_marks_gap_and_publishes_nothing(self) -> None:
        cabin_old = self.activity("2026-08-26T20:00:00Z")
        crosstown_old = self.activity("2026-08-26T21:08:24Z")
        self.run_adapter(
            self.observation(
                cabin=[cabin_old], crosstown=[crosstown_old], exhausted=False
            )
        )
        self.events.unlink(missing_ok=True)

        with self.assertRaisesRegex(adapter.AdapterError, "history_gap"):
            self.run_adapter(
                self.observation(
                    cabin=[
                        self.activity("2026-08-26T21:58:00Z"), cabin_old
                    ],
                    crosstown=[self.activity("2026-08-26T21:55:00Z")],
                    exhausted=False,
                )
            )

        self.assertEqual(self.published(), [])
        state = json.loads(self.state_path.read_text(encoding="utf-8"))
        self.assertTrue(
            all(item["health"] == "history_gap" for item in state["sites"].values())
        )

    def test_partial_or_wrong_binding_fails_entire_observation(self) -> None:
        invalid = self.observation()
        invalid["robots"] = invalid["robots"][:1]

        with self.assertRaisesRegex(adapter.AdapterError, "observer_output_invalid"):
            self.run_adapter(invalid)

        self.assertEqual(self.published(), [])
        state = json.loads(self.state_path.read_text(encoding="utf-8"))
        self.assertTrue(
            all(item["health"] == "provider_unavailable" for item in state["sites"].values())
        )

    def test_unsafe_state_or_hard_linked_lock_fails_closed(self) -> None:
        self.run_adapter(self.observation())
        self.state_path.chmod(0o644)

        with self.assertRaisesRegex(adapter.AdapterError, "state_invalid"):
            self.run_adapter(self.observation())

        self.state_path.chmod(0o600)
        lock_path = self.state_dir / "whisker-adapter.lock"
        hard_link = self.state_dir / "whisker-adapter.lock.copy"
        os.link(lock_path, hard_link)
        with self.assertRaisesRegex(adapter.AdapterError, "lock_unsafe"):
            self.run_adapter(self.observation())

    def test_corrupt_private_state_is_quarantined_before_observation(self) -> None:
        self.state_path.write_text("{not-json}\n", encoding="utf-8")
        self.state_path.chmod(0o600)

        with self.assertRaisesRegex(adapter.AdapterError, "state_invalid"):
            self.run_adapter(self.observation())

        self.assertFalse(self.state_path.exists())
        quarantined = list(self.state_dir.glob(".whisker-adapter.json.invalid.*"))
        self.assertEqual(len(quarantined), 1)
        self.assertEqual(stat.S_IMODE(quarantined[0].stat().st_mode), 0o600)
        self.assertFalse(self.calls.exists())


if __name__ == "__main__":
    unittest.main()
