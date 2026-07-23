from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import stat
import sys
import tempfile
from datetime import datetime, timezone
from unittest import TestCase, main, mock


REPO_ROOT = Path(__file__).resolve().parents[2]
MODULE_PATH = REPO_ROOT / "openclaw/bin/presence-local-event-adapter.py"
SPEC = importlib.util.spec_from_file_location("presence_local_event_adapter", MODULE_PATH)
assert SPEC and SPEC.loader
adapter = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(adapter)

BUS_MODULE_PATH = REPO_ROOT / "openclaw/bin/home_event_bus.py"
BUS_SPEC = importlib.util.spec_from_file_location(
    "presence_local_test_home_event_bus", BUS_MODULE_PATH
)
assert BUS_SPEC and BUS_SPEC.loader
home_event_bus = importlib.util.module_from_spec(BUS_SPEC)
sys.modules[BUS_SPEC.name] = home_event_bus
BUS_SPEC.loader.exec_module(home_event_bus)


def moment(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)


class PresenceLocalEventAdapterTests(TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.presence = self.root / "presence"
        self.events_root = self.root / "home-events"
        self.capture = self.root / "events.jsonl"
        self.publisher = self.root / "home-eventctl"
        self.presence.mkdir(mode=0o700)
        self.capture.write_text("", encoding="utf-8")
        self.publisher.write_text(
            """#!/usr/bin/env python3
import json
import os
import sys
assert sys.argv[1:] == ["enqueue", "--source", "presence"]
event = json.load(sys.stdin)
with open(os.environ["PRESENCE_LOCAL_CAPTURE"], "a", encoding="utf-8") as stream:
    stream.write(json.dumps(event, separators=(",", ":"), sort_keys=True) + "\\n")
""",
            encoding="utf-8",
        )
        self.publisher.chmod(0o700)
        self.now = moment("2026-07-23T12:01:00Z")
        self.environment = mock.patch.dict(
            os.environ,
            {
                "HOME_EVENTS_ROOT": str(self.events_root),
                "HOME_EVENTS_CABIN_SCAN": str(self.presence / "cabin-scan.json"),
                "HOME_EVENTS_CROSSTOWN_SCAN": str(
                    self.presence / "crosstown-scan.json"
                ),
                "HOME_EVENTS_PRESENCE_STATE": str(self.presence / "state.json"),
                "HOME_EVENTCTL": str(self.publisher),
                "PRESENCE_LOCAL_CAPTURE": str(self.capture),
                "HOME_EVENTS_LOCAL_PRESENCE_CABIN_ENABLED": "1",
                "HOME_EVENTS_LOCAL_PRESENCE_CROSSTOWN_ENABLED": "0",
            },
            clear=False,
        )
        self.environment.start()

    def tearDown(self) -> None:
        self.environment.stop()
        self.temporary.cleanup()

    @property
    def state_path(self) -> Path:
        return self.events_root / "state/presence-local-adapter.json"

    @property
    def pending_path(self) -> Path:
        return self.events_root / "state/presence-local-adapter.pending.json"

    def private_json(self, path: Path, value: dict) -> None:
        path.write_text(json.dumps(value), encoding="utf-8")
        path.chmod(0o600)

    def write_scan(
        self,
        observed_at: str,
        dylan: bool,
        julia: bool,
        *,
        site: str = "cabin",
        extra: dict | None = None,
    ) -> None:
        value = {
            "location": site,
            "timestamp": observed_at,
            "presence": {
                "Dylan": {"present": dylan},
                "Julia": {"present": julia},
            },
        }
        if extra:
            value.update(extra)
        self.private_json(self.presence / f"{site}-scan.json", value)

    def write_canonical(
        self,
        observed_at: str,
        dylan: str = "cabin",
        julia: str = "cabin",
        *,
        extra: dict | None = None,
    ) -> None:
        value = {
            "timestamp": observed_at,
            "people": {
                "Dylan": {"location": dylan},
                "Julia": {"location": julia},
            },
        }
        if extra:
            value.update(extra)
        self.private_json(self.presence / "state.json", value)

    def run_at(self, now: str) -> dict:
        self.now = moment(now)
        with mock.patch.object(adapter, "utc_now", return_value=self.now):
            return adapter.run_once()

    def events(self) -> list[dict]:
        events = [
            json.loads(line)
            for line in self.capture.read_text(encoding="utf-8").splitlines()
            if line
        ]
        for event in events:
            home_event_bus.normalize_input(
                "presence",
                event,
                b"p" * 32,
                clock=lambda event=event: event["observed_at"],
            )
        return events

    def state(self) -> dict:
        return json.loads(self.state_path.read_text(encoding="utf-8"))

    def baseline(
        self,
        *,
        observed_at: str = "2026-07-23T12:00:00Z",
        dylan: bool = True,
        julia: bool = True,
        dylan_location: str = "cabin",
        julia_location: str = "cabin",
    ) -> None:
        self.write_scan(observed_at, dylan, julia)
        self.write_canonical(observed_at, dylan_location, julia_location)
        result = self.run_at("2026-07-23T12:01:00Z")
        self.assertEqual(result["mode"], "baseline")
        self.assertEqual(result["event_count"], 0)

    def advance(
        self,
        observed_at: str,
        dylan: bool,
        julia: bool,
        *,
        now: str | None = None,
        dylan_location: str = "cabin",
        julia_location: str = "cabin",
    ) -> dict:
        self.write_scan(observed_at, dylan, julia)
        self.write_canonical(observed_at, dylan_location, julia_location)
        return self.run_at(now or observed_at)

    def establish_single_resident_excursion(self) -> str:
        self.baseline(julia=False, julia_location="crosstown")
        for observed in (
            "2026-07-23T12:15:00Z",
            "2026-07-23T12:30:00Z",
            "2026-07-23T12:45:00Z",
        ):
            self.advance(
                observed,
                False,
                False,
                julia_location="crosstown",
            )
        excursion = self.state()["sites"]["cabin"]["excursion"]
        self.assertIsNotNone(excursion)
        return excursion["excursion_id"]

    def test_disabled_flags_create_nothing_and_disabled_site_is_not_added(self) -> None:
        os.environ["HOME_EVENTS_LOCAL_PRESENCE_CABIN_ENABLED"] = "0"
        result = self.run_at("2026-07-23T12:01:00Z")
        self.assertEqual(result, {"ok": True, "mode": "disabled"})
        self.assertFalse(self.events_root.exists())

        os.environ["HOME_EVENTS_LOCAL_PRESENCE_CABIN_ENABLED"] = "1"
        self.baseline()
        self.write_scan(
            "2026-07-23T12:15:00Z", True, True, site="crosstown"
        )
        self.write_canonical("2026-07-23T12:15:00Z")
        self.run_at("2026-07-23T12:16:00Z")
        self.assertEqual(set(self.state()["sites"]), {"cabin"})

        os.environ["HOME_EVENTS_LOCAL_PRESENCE_CABIN_ENABLED"] = "0"
        os.environ["HOME_EVENTS_LOCAL_PRESENCE_CROSSTOWN_ENABLED"] = "1"
        self.write_canonical(
            "2026-07-23T12:17:00Z", "crosstown", "crosstown"
        )
        self.run_at("2026-07-23T12:18:00Z")
        self.assertEqual(set(self.state()["sites"]), {"cabin", "crosstown"})

    def test_false_baseline_duplicate_and_transient_negative_are_silent(self) -> None:
        self.baseline(dylan=False, julia=False)
        person = self.state()["sites"]["cabin"]["people"]["dylan"]
        self.assertEqual(person["status"], "uninitialized")

        self.advance("2026-07-23T12:15:00Z", False, False)
        before = self.state_path.read_bytes()
        self.advance("2026-07-23T12:15:00Z", False, False, now="2026-07-23T12:16:00Z")
        self.assertEqual(self.state_path.read_bytes(), before)
        self.assertEqual(self.events(), [])

        self.advance("2026-07-23T12:20:00Z", True, True)
        self.advance("2026-07-23T12:35:00Z", False, True)
        self.assertEqual(
            self.state()["sites"]["cabin"]["people"]["dylan"]["status"],
            "departure_candidate",
        )
        self.advance("2026-07-23T12:40:00Z", True, True)
        self.assertEqual(
            self.state()["sites"]["cabin"]["people"]["dylan"]["status"],
            "present",
        )
        self.assertEqual(self.events(), [])

    def test_departure_threshold_span_and_arrival_contract(self) -> None:
        self.baseline()
        self.advance("2026-07-23T12:15:00Z", False, True)
        self.advance("2026-07-23T12:30:00Z", False, True)
        self.assertEqual(self.events(), [])
        result = self.advance("2026-07-23T12:45:00Z", False, True)
        self.assertEqual(result["event_count"], 1)

        departure = self.events()[0]
        self.assertEqual(departure["event_type"], "presence.local_departure_inferred")
        self.assertEqual(departure["entity_alias"], "dylan")
        self.assertEqual(departure["attributes"]["not_before"], "2026-07-23T12:00:00Z")
        self.assertEqual(departure["attributes"]["not_after"], "2026-07-23T12:45:00Z")
        self.assertEqual(departure["attributes"]["distinct_observations"], 3)
        self.assertEqual(departure["attributes"]["observation_span_seconds"], 1800)

        # An established local absence survives a long quiet interval. The
        # first later exact positive is still the arrival observation.
        result = self.advance("2026-07-24T13:00:00Z", True, True)
        self.assertEqual(result["event_count"], 1)
        arrival = self.events()[-1]
        self.assertEqual(arrival["event_type"], "presence.local_arrival_observed")
        self.assertEqual(arrival["attributes"]["not_before"], "2026-07-23T12:45:00Z")
        self.assertEqual(arrival["attributes"]["distinct_observations"], 1)
        self.assertEqual(arrival["attributes"]["observation_span_seconds"], 0)

    def test_negative_observation_gap_cannot_complete_departure(self) -> None:
        self.baseline()
        self.advance("2026-07-23T12:15:00Z", False, True)
        self.advance("2026-07-23T12:30:00Z", False, True)
        self.advance("2026-07-23T12:56:00Z", False, True)
        person = self.state()["sites"]["cabin"]["people"]["dylan"]
        self.assertEqual(person["status"], "uninitialized")
        self.assertEqual(person["negative_observations"], 0)
        self.assertEqual(self.events(), [])

    def test_household_starts_only_when_every_resident_is_away_and_first_return_ends(self) -> None:
        self.baseline()
        for observed in (
            "2026-07-23T12:15:00Z",
            "2026-07-23T12:30:00Z",
            "2026-07-23T12:45:00Z",
        ):
            self.advance(observed, False, True)
        self.assertEqual(
            [event["event_type"] for event in self.events()],
            ["presence.local_departure_inferred"],
        )

        for observed in (
            "2026-07-23T13:00:00Z",
            "2026-07-23T13:15:00Z",
            "2026-07-23T13:30:00Z",
        ):
            self.advance(observed, False, False)
        types = [event["event_type"] for event in self.events()]
        self.assertEqual(
            types,
            [
                "presence.local_departure_inferred",
                "presence.local_departure_inferred",
                "presence.household_excursion_started",
            ],
        )
        started = self.events()[-1]
        self.assertEqual(started["attributes"]["people_count"], 2)
        excursion_id = started["attributes"]["excursion_id"]

        self.advance("2026-07-23T13:45:00Z", True, False)
        self.assertEqual(
            [event["event_type"] for event in self.events()][-2:],
            [
                "presence.local_arrival_observed",
                "presence.household_excursion_ended",
            ],
        )
        ended = self.events()[-1]
        self.assertEqual(ended["attributes"]["outcome"], "resident_returned")
        self.assertEqual(ended["attributes"]["excursion_id"], excursion_id)

        self.advance("2026-07-23T14:00:00Z", True, True)
        self.assertEqual(
            [event["event_type"] for event in self.events()].count(
                "presence.household_excursion_ended"
            ),
            1,
        )

    def test_split_household_one_resident_departure_starts_excursion(self) -> None:
        self.baseline(julia=False, julia_location="crosstown")
        for observed in (
            "2026-07-23T12:15:00Z",
            "2026-07-23T12:30:00Z",
            "2026-07-23T12:45:00Z",
        ):
            self.advance(
                observed,
                False,
                False,
                julia_location="crosstown",
            )
        self.assertEqual(
            [event["event_type"] for event in self.events()],
            [
                "presence.local_departure_inferred",
                "presence.household_excursion_started",
            ],
        )
        self.assertEqual(self.events()[-1]["attributes"]["people_count"], 1)

    def test_unknown_participant_does_not_claim_excursion_relocation(self) -> None:
        excursion_id = self.establish_single_resident_excursion()
        prior_types = [event["event_type"] for event in self.events()]
        self.write_scan("2026-07-23T12:45:00Z", False, False)
        self.write_canonical(
            "2026-07-23T12:46:00Z", "unknown", "crosstown"
        )
        self.run_at("2026-07-23T12:47:00Z")
        self.assertEqual(
            [event["event_type"] for event in self.events()], prior_types
        )
        excursion = self.state()["sites"]["cabin"]["excursion"]
        self.assertEqual(excursion["excursion_id"], excursion_id)

    def test_resident_added_to_site_does_not_claim_excursion_relocation(self) -> None:
        excursion_id = self.establish_single_resident_excursion()
        prior_types = [event["event_type"] for event in self.events()]
        self.write_scan("2026-07-23T12:45:00Z", False, False)
        self.write_canonical("2026-07-23T12:46:00Z", "cabin", "cabin")
        self.run_at("2026-07-23T12:47:00Z")
        self.assertEqual(
            [event["event_type"] for event in self.events()], prior_types
        )
        excursion = self.state()["sites"]["cabin"]["excursion"]
        self.assertEqual(excursion["excursion_id"], excursion_id)

    def test_canonical_relocation_cancels_candidate_and_closes_excursion(self) -> None:
        self.baseline()
        self.advance("2026-07-23T12:15:00Z", False, True)
        self.advance(
            "2026-07-23T12:16:00Z",
            False,
            True,
            dylan_location="crosstown",
        )
        self.assertEqual(
            self.state()["sites"]["cabin"]["people"]["dylan"]["status"],
            "uninitialized",
        )
        self.assertEqual(self.events(), [])

        # Establish a new two-person excursion after both residents return.
        self.advance("2026-07-23T12:20:00Z", True, True)
        self.advance(
            "2026-07-23T12:21:00Z",
            True,
            True,
            dylan_location="cabin",
        )
        for observed in (
            "2026-07-23T12:35:00Z",
            "2026-07-23T12:50:00Z",
            "2026-07-23T13:05:00Z",
        ):
            self.advance(observed, False, False)
        self.assertIsNotNone(self.state()["sites"]["cabin"]["excursion"])

        # A newer canonical move closes it as relocation without an arrival.
        self.write_scan("2026-07-23T13:05:00Z", False, False)
        self.write_canonical("2026-07-23T13:06:00Z", "crosstown", "crosstown")
        self.run_at("2026-07-23T13:07:00Z")
        self.assertEqual(self.events()[-1]["event_type"], "presence.household_excursion_ended")
        self.assertEqual(
            self.events()[-1]["attributes"]["outcome"], "residence_relocated"
        )
        self.assertNotEqual(
            self.events()[-1]["event_type"], "presence.local_arrival_observed"
        )

    def test_insecure_malformed_stale_future_and_replayed_scans_never_advance(self) -> None:
        self.baseline()
        before = self.state_path.read_bytes()
        scan_path = self.presence / "cabin-scan.json"

        self.write_scan("2026-07-23T12:15:00Z", False, False)
        scan_path.chmod(0o644)
        self.run_at("2026-07-23T12:16:00Z")
        self.assertEqual(self.state_path.read_bytes(), before)

        scan_path.write_text("{", encoding="utf-8")
        scan_path.chmod(0o600)
        self.run_at("2026-07-23T12:16:00Z")
        self.assertEqual(self.state_path.read_bytes(), before)

        self.write_scan("2026-07-23T11:00:00Z", False, False)
        self.run_at("2026-07-23T12:16:00Z")
        self.assertEqual(self.state_path.read_bytes(), before)

        self.write_scan("2026-07-23T12:22:00Z", False, False)
        self.run_at("2026-07-23T12:16:00Z")
        self.assertEqual(self.state_path.read_bytes(), before)

        self.write_scan("2026-07-23T11:59:00Z", False, False)
        self.run_at("2026-07-23T12:16:00Z")
        self.assertEqual(self.state_path.read_bytes(), before)
        self.assertEqual(self.events(), [])

    def test_invalid_canonical_state_blocks_all_scan_advancement(self) -> None:
        invalid_cases = ("missing", "malformed", "stale", "future")
        for case in invalid_cases:
            with self.subTest(case=case):
                # Restore a known-good baseline independently for every case.
                self.state_path.unlink(missing_ok=True)
                self.capture.write_text("", encoding="utf-8")
                self.baseline()
                before = self.state_path.read_bytes()
                self.write_scan("2026-07-23T12:15:00Z", False, False)
                canonical_path = self.presence / "state.json"
                if case == "missing":
                    canonical_path.unlink()
                elif case == "malformed":
                    canonical_path.write_text("{", encoding="utf-8")
                    canonical_path.chmod(0o600)
                elif case == "stale":
                    self.write_canonical("2026-07-23T11:00:00Z")
                else:
                    self.write_canonical("2026-07-23T12:22:00Z")
                result = self.run_at("2026-07-23T12:16:00Z")
                self.assertFalse(result["ok"])
                self.assertEqual(
                    result["error_code"], "canonical_state_unavailable"
                )
                self.assertEqual(self.state_path.read_bytes(), before)
                self.assertEqual(self.events(), [])

    def test_total_scan_outage_cannot_be_masked_by_canonical_checkpoint(self) -> None:
        self.baseline()
        before = self.state_path.read_bytes()
        self.write_scan("2026-07-23T11:00:00Z", False, False)
        self.write_canonical(
            "2026-07-23T12:16:00Z", "crosstown", "crosstown"
        )
        result = self.run_at("2026-07-23T12:17:00Z")
        self.assertEqual(
            result,
            {
                "ok": False,
                "mode": "failure",
                "error_code": "observations_unavailable",
            },
        )
        self.assertEqual(self.state_path.read_bytes(), before)
        self.assertEqual(self.events(), [])

    def test_small_future_skew_is_accepted_and_event_observed_at_is_ordered(self) -> None:
        self.write_scan("2026-07-23T12:00:30Z", True, False)
        self.write_canonical(
            "2026-07-23T12:00:30Z", "cabin", "crosstown"
        )
        result = self.run_at("2026-07-23T12:00:00Z")
        self.assertEqual(result["mode"], "baseline")
        for observed_at, now in (
            ("2026-07-23T12:15:30Z", "2026-07-23T12:15:00Z"),
            ("2026-07-23T12:30:30Z", "2026-07-23T12:30:00Z"),
            ("2026-07-23T12:45:30Z", "2026-07-23T12:45:00Z"),
        ):
            self.advance(
                observed_at,
                False,
                False,
                now=now,
                julia_location="crosstown",
            )
        for event in self.events():
            self.assertGreaterEqual(
                moment(event["observed_at"]), moment(event["occurred_at"])
            )
        self.assertEqual(
            self.events()[0]["observed_at"], "2026-07-23T12:45:30Z"
        )

    def test_pending_replay_uses_identical_ids_and_commits_once(self) -> None:
        self.baseline(julia=False, julia_location="crosstown")
        self.advance(
            "2026-07-23T12:15:00Z",
            False,
            False,
            julia_location="crosstown",
        )
        self.advance(
            "2026-07-23T12:30:00Z",
            False,
            False,
            julia_location="crosstown",
        )
        self.write_scan("2026-07-23T12:45:00Z", False, False)
        self.write_canonical("2026-07-23T12:45:00Z", "cabin", "crosstown")

        attempts: list[dict] = []

        def crash_after_first(_home_eventctl: str, event: dict) -> None:
            attempts.append(json.loads(json.dumps(event)))
            raise adapter.AdapterError("simulated_crash")

        with (
            mock.patch.object(adapter, "utc_now", return_value=moment("2026-07-23T12:46:00Z")),
            mock.patch.object(adapter, "publish", side_effect=crash_after_first),
        ):
            with self.assertRaises(adapter.AdapterError):
                adapter.run_once()
        self.assertTrue(self.pending_path.exists())
        self.assertEqual(len(attempts), 1)
        pending = json.loads(self.pending_path.read_text(encoding="utf-8"))
        self.assertEqual(len(pending["events"]), 2)

        replayed: list[dict] = []
        accepted_ids = {attempts[0]["source_event_id"]}

        def idempotent_replay(_binary: str, event: dict) -> None:
            replayed.append(json.loads(json.dumps(event)))
            accepted_ids.add(event["source_event_id"])

        with (
            mock.patch.object(adapter, "utc_now", return_value=moment("2026-07-23T12:46:00Z")),
            mock.patch.object(
                adapter,
                "publish",
                side_effect=idempotent_replay,
            ),
        ):
            result = adapter.run_once()
        self.assertTrue(result["ok"])
        self.assertFalse(self.pending_path.exists())
        self.assertEqual(len(replayed), 2)
        self.assertEqual(attempts[0]["source_event_id"], replayed[0]["source_event_id"])
        self.assertNotEqual(replayed[0]["source_event_id"], replayed[1]["source_event_id"])
        self.assertEqual(len(accepted_ids), 2)
        self.assertEqual(
            self.state()["sites"]["cabin"]["people"]["dylan"]["status"],
            "locally_away",
        )

    def test_cabin_pending_replays_after_crosstown_is_disabled(self) -> None:
        os.environ["HOME_EVENTS_LOCAL_PRESENCE_CROSSTOWN_ENABLED"] = "1"
        self.write_scan("2026-07-23T12:00:00Z", True, False)
        self.write_scan(
            "2026-07-23T12:00:00Z", False, True, site="crosstown"
        )
        self.write_canonical(
            "2026-07-23T12:00:00Z", "cabin", "crosstown"
        )
        self.run_at("2026-07-23T12:01:00Z")
        for observed_at in (
            "2026-07-23T12:15:00Z",
            "2026-07-23T12:30:00Z",
        ):
            self.write_scan(observed_at, False, False)
            self.write_scan(observed_at, False, True, site="crosstown")
            self.write_canonical(observed_at, "cabin", "crosstown")
            self.run_at(observed_at)

        self.write_scan("2026-07-23T12:45:00Z", False, False)
        self.write_scan(
            "2026-07-23T12:45:00Z", False, True, site="crosstown"
        )
        self.write_canonical(
            "2026-07-23T12:45:00Z", "cabin", "crosstown"
        )
        with (
            mock.patch.object(
                adapter, "utc_now", return_value=moment("2026-07-23T12:46:00Z")
            ),
            mock.patch.object(
                adapter,
                "publish",
                side_effect=adapter.AdapterError("publisher_failed"),
            ),
        ):
            with self.assertRaises(adapter.AdapterError):
                adapter.run_once()
        pending = json.loads(self.pending_path.read_text(encoding="utf-8"))
        self.assertEqual(pending["affected_sites"], ["cabin"])
        self.assertTrue(
            all(event["site"] == "cabin" for event in pending["events"])
        )

        os.environ["HOME_EVENTS_LOCAL_PRESENCE_CROSSTOWN_ENABLED"] = "0"
        replayed: list[dict] = []
        with (
            mock.patch.object(
                adapter, "utc_now", return_value=moment("2026-07-23T12:46:00Z")
            ),
            mock.patch.object(
                adapter,
                "publish",
                side_effect=lambda _binary, event: replayed.append(
                    json.loads(json.dumps(event))
                ),
            ),
        ):
            result = adapter.run_once()
        self.assertTrue(result["ok"])
        self.assertFalse(self.pending_path.exists())
        self.assertEqual(len(replayed), 2)
        self.assertTrue(all(event["site"] == "cabin" for event in replayed))

    def test_private_extras_never_reach_state_pending_events_or_output(self) -> None:
        sentinel = "PRIVATE-NETWORK-IDENTIFIER-SENTINEL"
        private_extras = {
            "totalClients": 999,
            "raw_provider_payload": sentinel,
            "ipAddress": sentinel,
        }
        self.write_scan(
            "2026-07-23T12:00:00Z", True, False, extra=private_extras
        )
        self.write_canonical(
            "2026-07-23T12:00:00Z",
            "cabin",
            "crosstown",
            extra={"private": sentinel},
        )
        result = self.run_at("2026-07-23T12:01:00Z")
        for observed_at in (
            "2026-07-23T12:15:00Z",
            "2026-07-23T12:30:00Z",
        ):
            self.write_scan(observed_at, False, False, extra=private_extras)
            self.write_canonical(
                observed_at,
                "cabin",
                "crosstown",
                extra={"private": sentinel},
            )
            result = self.run_at(observed_at)
        self.write_scan(
            "2026-07-23T12:45:00Z", False, False, extra=private_extras
        )
        self.write_canonical(
            "2026-07-23T12:45:00Z",
            "cabin",
            "crosstown",
            extra={"private": sentinel},
        )
        with (
            mock.patch.object(
                adapter, "utc_now", return_value=moment("2026-07-23T12:46:00Z")
            ),
            mock.patch.object(
                adapter,
                "publish",
                side_effect=adapter.AdapterError("publisher_failed"),
            ),
        ):
            with self.assertRaises(adapter.AdapterError):
                adapter.run_once()
        self.assertTrue(self.pending_path.exists())
        serialized = (
            json.dumps(result)
            + self.state_path.read_text(encoding="utf-8")
            + self.pending_path.read_text(encoding="utf-8")
            + self.capture.read_text(encoding="utf-8")
        )
        self.assertNotIn(sentinel, serialized)
        self.assertNotIn("totalClients", serialized)
        self.assertEqual(stat.S_IMODE(self.state_path.stat().st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(self.pending_path.stat().st_mode), 0o600)
        self.assertEqual(
            stat.S_IMODE((self.events_root / "state").stat().st_mode), 0o700
        )


if __name__ == "__main__":
    main()
