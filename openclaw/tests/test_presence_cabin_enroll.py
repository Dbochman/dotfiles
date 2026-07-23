#!/usr/bin/env python3

from __future__ import annotations

import importlib.machinery
import importlib.util
import io
import json
import os
import stat
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


SCRIPT = Path(__file__).resolve().parents[1] / "bin" / "presence-cabin-enroll"
LOADER = importlib.machinery.SourceFileLoader("presence_cabin_enroll", str(SCRIPT))
SPEC = importlib.util.spec_from_loader(LOADER.name, LOADER)
assert SPEC is not None
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
LOADER.exec_module(MODULE)


DYLAN_ID = "a" * 64
JULIA_ID = "b" * 64
BACKGROUND_ID = "c" * 64
CHURN_ID = "d" * 64
TEST_WIFI_ADDRESS = "02:00:00:00:00:01"
TEST_IP_ADDRESS = "192.168.1.42"
PRIVACY_SENTINELS = (
    DYLAN_ID,
    JULIA_ID,
    "Private Phone Name",
    TEST_WIFI_ADDRESS,
    TEST_IP_ADDRESS,
    "192.168.1.90",
)


class FakeClock:
    def __init__(self, initial: float = 1_700_000_000.0) -> None:
        self.value = initial

    def __call__(self) -> float:
        return self.value

    def advance(self, seconds: float) -> None:
        self.value += seconds


class QueueProvider:
    def __init__(self, snapshots):
        self.snapshots = list(snapshots)

    def __call__(self):
        if not self.snapshots:
            raise AssertionError("snapshot queue exhausted")
        return self.snapshots.pop(0)


class QueueExactProvider:
    def __init__(self, results):
        self.results = list(results)

    def __call__(self, _wifi_address):
        if not self.results:
            raise AssertionError("exact-address queue exhausted")
        return self.results.pop(0)


class FakeProcess:
    def __init__(self, payload: bytes, *, returncode: int = 0) -> None:
        self.stdout = tempfile.TemporaryFile()
        self.stdout.write(payload)
        self.stdout.seek(0)
        self.returncode = returncode

    def wait(self, *, timeout=None):
        return self.returncode

    def kill(self) -> None:
        return None


class HangingProcess:
    def __init__(self) -> None:
        read_descriptor, self.write_descriptor = os.pipe()
        self.stdout = os.fdopen(read_descriptor, "rb", buffering=0)

    def wait(self, *, timeout=None):
        return 0

    def kill(self) -> None:
        os.close(self.write_descriptor)


def evidence(*, idle: int = 1, active: bool = True, complete: bool = True):
    if not complete:
        return MODULE.Evidence(False, None, None, None, None)
    return MODULE.Evidence(True, active, active, 900.0 if active else 0.0, idle)


def snapshot(
    clients,
    *,
    total: int | None = None,
    tuple_absent_ids=(),
    liveness_shapes=None,
):
    return MODULE.Snapshot(
        clients=clients,
        total_clients=len(clients) if total is None else total,
        missing_ids=0,
        malformed_ids=0,
        liveness_tuple_absent_ids=frozenset(tuple_absent_ids),
        liveness_shapes={} if liveness_shapes is None else liveness_shapes,
    )


class PresenceCabinEnrollTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.home = Path(self.temporary.name)
        self.openclaw = self.home / ".openclaw"
        self.openclaw.mkdir(mode=0o700)
        os.chmod(self.openclaw, 0o700)
        self.settings = MODULE.Settings(home=self.home)
        self.clock = FakeClock()
        self.app = MODULE.EnrollmentApp(
            self.settings,
            snapshot_provider=QueueProvider([]),
            exact_address_provider=QueueExactProvider([]),
            exact_ip_provider=QueueExactProvider([]),
            mutation_jobs_stopped_provider=lambda: True,
            clock=self.clock,
            sleeper=lambda _seconds: None,
        )

    def provide(self, *snapshots) -> None:
        self.app.snapshot_provider = QueueProvider(snapshots)

    def provide_exact(self, *results) -> None:
        self.app.exact_address_provider = QueueExactProvider(results)

    def provide_exact_ip(self, *results) -> None:
        self.app.exact_ip_provider = QueueExactProvider(results)

    def repeated(self, value):
        return [value] * MODULE.IDENTIFY_SAMPLE_COUNT

    def start(self) -> None:
        result = self.app.start()
        self.assertTrue(result["ok"])
        self.assertEqual(stat.S_IMODE(self.settings.session_dir.stat().st_mode), 0o700)
        self.assertEqual(stat.S_IMODE(self.settings.session_file.stat().st_mode), 0o600)

    def enroll_person(self, person: str, raw_id: str, other_ids=()) -> None:
        baseline_clients = {
            BACKGROUND_ID: evidence(idle=10),
            raw_id: evidence(idle=700),
            **{value: evidence(idle=700) for value in other_ids},
        }
        baseline = snapshot(baseline_clients)
        self.provide(*self.repeated(baseline))
        self.app.baseline(person)

        active = snapshot(
            {
                BACKGROUND_ID: evidence(idle=10),
                raw_id: evidence(idle=1),
                **{value: evidence(idle=700) for value in other_ids},
            }
        )
        self.provide(*self.repeated(active))
        identified = self.app.identify(person)
        self.assertRegex(identified["candidate"], r"^candidate-[0-9a-f]{12}$")

        for cycle in (1, 2):
            off = snapshot(
                {
                    BACKGROUND_ID: evidence(idle=10),
                    **{value: evidence(idle=700) for value in other_ids},
                }
            )
            self.provide(off)
            self.app.disconnect(person, cycle)
            self.clock.advance(MODULE.MINIMUM_DISCONNECT_SECONDS)
            self.provide(*self.repeated(active))
            self.app.reconnect(person, cycle)

        self.provide(active)
        self.app.idle_start(person)
        checkpoints = ((5, 300), (10, 600), (20, 1200))
        elapsed = 0
        for minutes, idle in checkpoints:
            target = minutes * 60
            self.clock.advance(target - elapsed)
            elapsed = target
            idle_snapshot = snapshot(
                {
                    BACKGROUND_ID: evidence(idle=10),
                    raw_id: evidence(idle=min(idle, 240)),
                    **{value: evidence(idle=700) for value in other_ids},
                }
            )
            self.provide(idle_snapshot)
            self.app.idle_check(person, minutes)

    def finish_enrollment(self) -> None:
        self.start()
        self.enroll_person("Dylan", DYLAN_ID, (JULIA_ID,))
        self.enroll_person("Julia", JULIA_ID, (DYLAN_ID,))

    def enroll_person_exact(
        self,
        person: str,
        raw_id: str,
        other_id: str,
        *,
        other_present: bool,
        baseline_incomplete_ids: bool = False,
    ) -> None:
        other_idle = 1 if other_present else 700
        baseline_clients = {BACKGROUND_ID: evidence(idle=10)}
        if other_present:
            baseline_clients[other_id] = evidence(idle=other_idle)
        elif baseline_incomplete_ids:
            baseline_clients[raw_id] = evidence(complete=False)
            baseline_clients[other_id] = evidence(complete=False)
        baseline = snapshot(baseline_clients)
        self.provide(*self.repeated(baseline))
        self.app.baseline(person)
        active = snapshot(
            {
                BACKGROUND_ID: evidence(idle=10),
                raw_id: evidence(idle=1),
                other_id: evidence(idle=other_idle),
            }
        )
        self.provide_exact_ip(
            *[(active, raw_id)] * MODULE.IDENTIFY_SAMPLE_COUNT
        )
        address = MODULE._normalize_cabin_ip_address(TEST_IP_ADDRESS)
        self.assertIsNotNone(address)
        self.app.identify_exact_ip(person, address)
        off = snapshot(
            {
                BACKGROUND_ID: evidence(idle=10),
                other_id: evidence(idle=other_idle),
            }
        )
        for cycle in (1, 2):
            self.provide(off)
            self.app.disconnect(person, cycle)
            self.clock.advance(MODULE.MINIMUM_DISCONNECT_SECONDS)
            self.provide(*self.repeated(active))
            self.app.reconnect(person, cycle)
        self.app.idle_start(person)
        elapsed = 0
        for minutes in MODULE.IDLE_MINUTES:
            target = minutes * 60
            self.clock.advance(target - elapsed)
            elapsed = target
            self.provide(
                snapshot(
                    {
                        BACKGROUND_ID: evidence(idle=10),
                        raw_id: evidence(idle=240),
                        other_id: evidence(idle=other_idle),
                    }
                )
            )
            self.app.idle_check(person, minutes)

    def finish_exact_enrollment_with_compact_credits(
        self, *, julia_baseline_incomplete_ids: bool = False
    ) -> None:
        self.start()
        self.enroll_person_exact(
            "Julia",
            JULIA_ID,
            DYLAN_ID,
            other_present=False,
            baseline_incomplete_ids=julia_baseline_incomplete_ids,
        )
        self.enroll_person_exact(
            "Dylan", DYLAN_ID, JULIA_ID, other_present=True
        )

    def shadow_snapshot(self, scenario: str):
        expected = MODULE.SHADOW_SCENARIOS[scenario]
        clients = {BACKGROUND_ID: evidence(idle=10)}
        if expected["Dylan"]:
            clients[DYLAN_ID] = evidence(idle=1)
        if expected["Julia"]:
            clients[JULIA_ID] = evidence(idle=1)
        return snapshot(clients)

    def stage_ready_shadow(self):
        self.app.seal_candidate()
        scenarios = (
            "both-present",
            "dylan-only",
            "julia-only",
            "both-away",
            "return-both",
            "both-present",
            "dylan-only",
            "both-present",
        )
        results = []
        for scenario in scenarios:
            self.provide(self.shadow_snapshot(scenario))
            results.append(self.app.shadow_sample(scenario))
            self.clock.advance(15 * 60)
        return results

    def test_parse_ignores_provider_identity_fields_and_safe_counts_are_redacted(self) -> None:
        payload = {
            "wifiGetClients": {
                "clients": [
                    {
                        "name": "Private Phone Name",
                        "macAddress": "02:00:00:00:00:01",
                        "ipAddress": "192.168.1.90",
                        "captiveClientId": DYLAN_ID.upper(),
                        "dhcpLeaseFound": True,
                        "dhcpLeaseActive": True,
                        "secondsUntilDhcpLeaseExpires": 900,
                        "noDataIdleS": 2,
                        "active": False,
                    }
                ]
            }
        }
        parsed = MODULE.parse_starlink_payload(payload)
        self.assertEqual(list(parsed.clients), [DYLAN_ID])
        self.assertTrue(parsed.clients[DYLAN_ID].strict_present)
        serialized = json.dumps(parsed.safe_counts(), sort_keys=True)
        for sentinel in PRIVACY_SENTINELS:
            self.assertNotIn(sentinel, serialized)

    def test_wifi_address_normalization_is_strict_and_non_identifying(self) -> None:
        expected = bytes.fromhex("020000000001")
        for value in (
            "02:00:00:00:00:01",
            "02-00-00-00-00-01",
            "020000000001",
            "02:00:00:00:00:01".upper(),
        ):
            with self.subTest(value=value):
                self.assertEqual(MODULE._normalize_wifi_address(value), expected)

        for value in (
            " 02:00:00:00:00:01",
            "02:00:00:00:00:01 ",
            "02:00-00:00:00:01",
            "０2:00:00:00:00:01",
            "01:00:00:00:00:01",
            "00:00:00:00:00:00",
            "ff:ff:ff:ff:ff:ff",
            "not-an-address",
            None,
        ):
            with self.subTest(value=value):
                self.assertIsNone(MODULE._normalize_wifi_address(value))

    def test_exact_address_match_returns_only_the_valid_captive_id(self) -> None:
        payload = {
            "wifiGetClients": {
                "clients": [
                    {
                        "macAddress": TEST_WIFI_ADDRESS,
                        "captiveClientId": JULIA_ID.upper(),
                        "dhcpLeaseFound": True,
                        "dhcpLeaseActive": True,
                        "secondsUntilDhcpLeaseExpires": 900,
                        "noDataIdleS": 1,
                    },
                    {
                        "macAddress": "02:00:00:00:00:02",
                        "captiveClientId": BACKGROUND_ID,
                        "dhcpLeaseFound": True,
                        "dhcpLeaseActive": True,
                        "secondsUntilDhcpLeaseExpires": 900,
                        "noDataIdleS": 1,
                    },
                ]
            }
        }
        parsed = MODULE.parse_starlink_payload(payload)
        address = MODULE._normalize_wifi_address(TEST_WIFI_ADDRESS)
        self.assertIsNotNone(address)

        matched = MODULE._match_exact_wifi_address(payload, parsed, address)

        self.assertEqual(matched, JULIA_ID)

    def test_exact_address_match_rejects_duplicate_or_invalid_rows(self) -> None:
        address = MODULE._normalize_wifi_address(TEST_WIFI_ADDRESS)
        self.assertIsNotNone(address)
        duplicate = {
            "wifiGetClients": {
                "clients": [
                    {
                        "macAddress": TEST_WIFI_ADDRESS,
                        "captiveClientId": DYLAN_ID,
                    },
                    {
                        "macAddress": TEST_WIFI_ADDRESS,
                        "captiveClientId": JULIA_ID,
                    },
                ]
            }
        }
        with self.assertRaisesRegex(
            MODULE.EnrollmentError, "exact_address_ambiguous"
        ):
            MODULE._match_exact_wifi_address(
                duplicate, MODULE.parse_starlink_payload(duplicate), address
            )

        invalid = {
            "wifiGetClients": {
                "clients": [{"macAddress": TEST_WIFI_ADDRESS}]
            }
        }
        with self.assertRaisesRegex(
            MODULE.EnrollmentError, "exact_address_row_invalid"
        ):
            MODULE._match_exact_wifi_address(
                invalid, MODULE.parse_starlink_payload(invalid), address
            )

    def test_cabin_ip_normalization_is_strict_and_site_scoped(self) -> None:
        self.assertEqual(
            MODULE._normalize_cabin_ip_address(TEST_IP_ADDRESS),
            bytes((192, 168, 1, 42)),
        )
        for value in (
            f" {TEST_IP_ADDRESS}",
            f"{TEST_IP_ADDRESS} ",
            "192.168.1.001",
            "192.168.1.0",
            "192.168.1.1",
            "192.168.1.255",
            "192.168.2.42",
            "2001:db8::1",
            "not-an-ip",
            None,
        ):
            with self.subTest(value=value):
                self.assertIsNone(MODULE._normalize_cabin_ip_address(value))

    def test_exact_ip_match_returns_only_the_valid_captive_id(self) -> None:
        payload = {
            "wifiGetClients": {
                "clients": [
                    {
                        "ipAddress": TEST_IP_ADDRESS,
                        "captiveClientId": JULIA_ID.upper(),
                        "dhcpLeaseFound": True,
                        "dhcpLeaseActive": True,
                        "secondsUntilDhcpLeaseExpires": 900,
                        "noDataIdleS": 1,
                    },
                    {
                        "ipAddress": "192.168.1.43",
                        "captiveClientId": BACKGROUND_ID,
                        "dhcpLeaseFound": True,
                        "dhcpLeaseActive": True,
                        "secondsUntilDhcpLeaseExpires": 900,
                        "noDataIdleS": 1,
                    },
                ]
            }
        }
        parsed = MODULE.parse_starlink_payload(payload)
        address = MODULE._normalize_cabin_ip_address(TEST_IP_ADDRESS)
        self.assertIsNotNone(address)

        matched = MODULE._match_exact_ip_address(payload, parsed, address)

        self.assertEqual(matched, JULIA_ID)

    def test_exact_ip_match_rejects_duplicate_or_invalid_rows(self) -> None:
        address = MODULE._normalize_cabin_ip_address(TEST_IP_ADDRESS)
        self.assertIsNotNone(address)
        duplicate = {
            "wifiGetClients": {
                "clients": [
                    {
                        "ipAddress": TEST_IP_ADDRESS,
                        "captiveClientId": DYLAN_ID,
                    },
                    {
                        "ipAddress": TEST_IP_ADDRESS,
                        "captiveClientId": JULIA_ID,
                    },
                ]
            }
        }
        with self.assertRaisesRegex(MODULE.EnrollmentError, "exact_ip_ambiguous"):
            MODULE._match_exact_ip_address(
                duplicate, MODULE.parse_starlink_payload(duplicate), address
            )

        invalid = {
            "wifiGetClients": {
                "clients": [{"ipAddress": TEST_IP_ADDRESS}]
            }
        }
        with self.assertRaisesRegex(
            MODULE.EnrollmentError, "exact_ip_row_invalid"
        ):
            MODULE._match_exact_ip_address(
                invalid, MODULE.parse_starlink_payload(invalid), address
            )

    def test_duplicate_provider_id_fails_closed(self) -> None:
        row = {
            "captiveClientId": DYLAN_ID,
            "dhcpLeaseFound": True,
            "dhcpLeaseActive": True,
            "secondsUntilDhcpLeaseExpires": 900,
            "noDataIdleS": 1,
        }
        with self.assertRaisesRegex(MODULE.EnrollmentError, "starlink_duplicate_id"):
            MODULE.parse_starlink_payload(
                {"wifiGetClients": {"clients": [row, dict(row)]}}
            )

    def test_parser_distinguishes_absent_from_partial_liveness_tuple(self) -> None:
        payload = {
            "wifiGetClients": {
                "clients": [
                    {"captiveClientId": DYLAN_ID},
                    {
                        "captiveClientId": JULIA_ID,
                        "dhcpLeaseFound": True,
                        "dhcpLeaseActive": True,
                        "secondsUntilDhcpLeaseExpires": 900,
                    },
                ]
            }
        }

        parsed = MODULE.parse_starlink_payload(payload)

        self.assertEqual(parsed.liveness_tuple_absent_ids, frozenset({DYLAN_ID}))
        self.assertFalse(parsed.clients[DYLAN_ID].complete)
        self.assertFalse(parsed.clients[JULIA_ID].complete)
        self.assertEqual(
            parsed.liveness_shapes[DYLAN_ID],
            {
                "lease_found": "missing",
                "lease_active": "missing",
                "lease_seconds": "missing",
                "idle_seconds": "missing",
                "active": "missing",
            },
        )
        self.assertEqual(
            parsed.liveness_shapes[JULIA_ID],
            {
                "lease_found": "true",
                "lease_active": "true",
                "lease_seconds": "positive",
                "idle_seconds": "missing",
                "active": "missing",
            },
        )

    def test_starlink_query_bounds_output_and_normalizes_process_failures(self) -> None:
        valid = b'{"wifiGetClients":{"clients":[]}}'
        settings = MODULE.Settings(
            home=self.home,
            grpcurl=Path("/bin/sh"),
            timeout_seconds=1,
            max_grpc_bytes=64,
        )
        with mock.patch.object(MODULE.subprocess, "Popen", return_value=FakeProcess(valid)):
            self.assertEqual(MODULE.query_starlink(settings).total_clients, 0)

        with mock.patch.object(
            MODULE.subprocess,
            "Popen",
            return_value=FakeProcess(valid, returncode=1),
        ):
            with self.assertRaisesRegex(MODULE.EnrollmentError, "starlink_unavailable"):
                MODULE.query_starlink(settings)

        with mock.patch.object(
            MODULE.subprocess, "Popen", return_value=FakeProcess(b"not-json")
        ):
            with self.assertRaisesRegex(MODULE.EnrollmentError, "starlink_schema_invalid"):
                MODULE.query_starlink(settings)

        with mock.patch.object(
            MODULE.subprocess, "Popen", return_value=FakeProcess(b"x" * 65)
        ):
            with self.assertRaisesRegex(
                MODULE.EnrollmentError, "starlink_response_oversized"
            ):
                MODULE.query_starlink(settings)

        timeout_settings = MODULE.Settings(
            home=self.home,
            grpcurl=Path("/bin/sh"),
            timeout_seconds=0.01,
            max_grpc_bytes=64,
        )
        with mock.patch.object(
            MODULE.subprocess, "Popen", return_value=HangingProcess()
        ):
            with self.assertRaisesRegex(MODULE.EnrollmentError, "starlink_unavailable"):
                MODULE.query_starlink(timeout_settings)

    def test_happy_path_stages_soaks_and_atomically_activates(self) -> None:
        self.finish_enrollment()
        status = self.app.status()
        self.assertTrue(status["enrollment_ready"])
        status_json = json.dumps(status, sort_keys=True)
        for sentinel in PRIVACY_SENTINELS:
            self.assertNotIn(sentinel, status_json)

        sealed = self.app.seal_candidate()
        self.assertFalse(sealed["production_config_written"])
        self.assertTrue(self.settings.staged_config.is_file())
        self.assertFalse(self.settings.production_config.exists())
        self.assertEqual(stat.S_IMODE(self.settings.staged_config.stat().st_mode), 0o600)

        scenarios = (
            "both-present",
            "dylan-only",
            "julia-only",
            "both-away",
            "return-both",
            "both-present",
            "dylan-only",
            "both-present",
        )
        safe_results = []
        for scenario in scenarios:
            self.provide(self.shadow_snapshot(scenario))
            safe_results.append(self.app.shadow_sample(scenario))
            self.clock.advance(15 * 60)
        report = self.app.shadow_report()
        self.assertTrue(report["ready_for_activation"])
        self.assertGreaterEqual(report["span_hours"], 1)
        self.assertGreaterEqual(report["cadence_intervals"], 3)

        activated = self.app.activate()
        self.assertTrue(activated["production_config_written"])
        self.assertFalse(self.settings.staged_config.exists())
        self.assertEqual(stat.S_IMODE(self.settings.production_config.stat().st_mode), 0o600)
        config = json.loads(self.settings.production_config.read_text(encoding="utf-8"))
        self.assertEqual(config["site"], "cabin")
        self.assertEqual(config["people"]["Dylan"]["value"], DYLAN_ID)
        self.assertEqual(config["people"]["Julia"]["value"], JULIA_ID)
        self.assertFalse((self.openclaw / "presence").exists())

        cleaned = self.app.cleanup()
        self.assertTrue(cleaned["sensitive_session_removed"])
        self.assertFalse(self.settings.session_file.exists())
        self.assertTrue(self.settings.production_config.exists())
        safe_report = json.loads(self.settings.safe_report.read_text(encoding="utf-8"))
        self.assertEqual(safe_report["session_outcome"], "completed")

        serialized = json.dumps(
            safe_results + [report, activated, cleaned, safe_report], sort_keys=True
        )
        for sentinel in PRIVACY_SENTINELS:
            self.assertNotIn(sentinel, serialized)

    def test_preidentification_outputs_and_session_never_store_raw_ids(self) -> None:
        raw_snapshot = snapshot(
            {
                DYLAN_ID: evidence(idle=700),
                BACKGROUND_ID: evidence(idle=10),
            }
        )
        self.provide(raw_snapshot)
        preflight = self.app.preflight()
        self.start()
        self.provide(*self.repeated(raw_snapshot))
        baseline = self.app.baseline("Dylan")
        serialized = json.dumps([preflight, baseline], sort_keys=True)
        persisted = self.settings.session_file.read_text(encoding="utf-8")
        for raw_id in (DYLAN_ID, BACKGROUND_ID):
            self.assertNotIn(raw_id, serialized)
            self.assertNotIn(raw_id, persisted)

    def test_helper_preserves_canonical_and_downstream_state_byte_for_byte(self) -> None:
        protected = {
            self.openclaw / "presence" / "state.json": b"presence-sentinel\n",
            self.openclaw
            / "presence"
            / "vacancy-dispatched"
            / "cabin": b"vacancy-sentinel\n",
            self.openclaw
            / "home-events"
            / "state"
            / "events.sqlite3": b"bus-sentinel\n",
            self.openclaw
            / "nest-events"
            / "state"
            / "activity-reviewer.json": b"camera-message-sentinel\n",
        }
        for path, payload in protected.items():
            path.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
            path.write_bytes(payload)
            os.chmod(path, 0o600)

        self.finish_enrollment()
        self.stage_ready_shadow()
        self.app.activate()
        self.app.cleanup()

        for path, payload in protected.items():
            self.assertEqual(path.read_bytes(), payload)

    def test_idle_profile_fails_if_known_present_phone_crosses_absence_threshold(self) -> None:
        self.finish_enrollment()
        active = snapshot({DYLAN_ID: evidence(idle=1)})
        self.provide(active)
        self.app.idle_start("Dylan")
        elapsed = 0
        for minutes, idle in ((5, 240), (10, 600), (20, 240)):
            target = minutes * 60
            self.clock.advance(target - elapsed)
            elapsed = target
            self.provide(snapshot({DYLAN_ID: evidence(idle=idle)}))
            self.app.idle_check("Dylan", minutes)
        self.assertFalse(self.app.status()["people"]["Dylan"]["ready"])

    def test_idle_checkpoints_must_be_ordered_but_late_evidence_is_valid(self) -> None:
        self.finish_enrollment()
        self.provide(snapshot({DYLAN_ID: evidence(idle=1)}))
        self.app.idle_start("Dylan")
        self.clock.advance(10 * 60)
        with self.assertRaisesRegex(
            MODULE.EnrollmentError, "idle_checkpoint_out_of_order"
        ):
            self.app.idle_check("Dylan", 10)
        self.provide(snapshot({DYLAN_ID: evidence(idle=240)}))
        result = self.app.idle_check("Dylan", 5)
        self.assertTrue(result["observation"]["evidence"]["current_rule_present"])
        self.clock.advance(1)
        self.provide(snapshot({DYLAN_ID: evidence(idle=240)}))
        result = self.app.idle_check("Dylan", 10)
        self.assertTrue(result["observation"]["evidence"]["current_rule_present"])

    def test_incomplete_idle_checkpoint_is_retryable(self) -> None:
        self.finish_enrollment()
        self.provide(snapshot({DYLAN_ID: evidence(idle=1)}))
        self.app.idle_start("Dylan")
        self.clock.advance(5 * 60)
        self.provide(snapshot({DYLAN_ID: evidence(complete=False)}))
        with self.assertRaisesRegex(
            MODULE.EnrollmentError, "idle_checkpoint_incomplete"
        ):
            self.app.idle_check("Dylan", 5)
        self.assertIsNone(self.app.status()["people"]["Dylan"]["idle_profile"]["5m"])
        self.provide(snapshot({DYLAN_ID: evidence(idle=240)}))
        result = self.app.idle_check("Dylan", 5)
        self.assertTrue(result["observation"]["evidence"]["current_rule_present"])

    def test_incomplete_selected_rows_fail_even_when_expected_away(self) -> None:
        self.finish_enrollment()
        self.app.seal_candidate()
        session_before = self.settings.session_file.read_bytes()
        self.provide(
            snapshot(
                {
                    DYLAN_ID: evidence(complete=False),
                    JULIA_ID: evidence(complete=False),
                }
            )
        )
        with self.assertRaisesRegex(
            MODULE.EnrollmentError, "shadow_sample_incomplete"
        ):
            self.app.shadow_sample("both-away")
        report = self.app.shadow_report()
        self.assertEqual(report["sample_count"], 0)
        self.assertEqual(report["mismatch_count"], 0)
        self.assertEqual(self.settings.session_file.read_bytes(), session_before)
        self.assertFalse(self.settings.safe_report.exists())
        self.provide(self.shadow_snapshot("both-away"))
        result = self.app.shadow_sample("both-away")
        self.assertTrue(result["matches_ground_truth"])

    def test_compact_joint_sequence_satisfies_shadow_gate(self) -> None:
        self.finish_exact_enrollment_with_compact_credits()
        self.app.seal_candidate()
        for scenario in MODULE.COMPACT_JOINT_SEQUENCE:
            self.provide(self.shadow_snapshot(scenario))
            self.app.shadow_sample(scenario)
            self.clock.advance(1)

        report = self.app.shadow_report()

        self.assertTrue(report["compact_sequence_proven"])
        self.assertEqual(
            report["compact_enrollment_credits"],
            {
                "both_away": True,
                "exact_bindings": True,
                "julia_only": True,
            },
        )
        self.assertFalse(report["legacy_gate_proven"])
        self.assertTrue(report["ready_for_activation"])

    def test_compact_joint_sequence_requires_order_and_zero_prior_mismatches(self) -> None:
        self.finish_exact_enrollment_with_compact_credits()
        self.app.seal_candidate()
        for scenario in ("dylan-only", "both-present", "dylan-only"):
            self.provide(self.shadow_snapshot(scenario))
            self.app.shadow_sample(scenario)
            self.clock.advance(1)
        report = self.app.shadow_report()
        self.assertFalse(report["compact_live_sequence_proven"])
        self.assertFalse(report["ready_for_activation"])

        self.provide(self.shadow_snapshot("dylan-only"))
        mismatch = self.app.shadow_sample("both-present")
        self.assertFalse(mismatch["matches_ground_truth"])
        self.clock.advance(1)
        for scenario in MODULE.COMPACT_JOINT_SEQUENCE:
            self.provide(self.shadow_snapshot(scenario))
            self.app.shadow_sample(scenario)
            self.clock.advance(1)
        report = self.app.shadow_report()
        self.assertTrue(report["compact_sequence_proven"])
        self.assertEqual(report["mismatch_count"], 1)
        self.assertFalse(report["ready_for_activation"])

    def test_compact_live_sequence_without_exact_credits_is_not_ready(self) -> None:
        self.finish_enrollment()
        self.app.seal_candidate()
        for scenario in MODULE.COMPACT_JOINT_SEQUENCE:
            self.provide(self.shadow_snapshot(scenario))
            self.app.shadow_sample(scenario)
            self.clock.advance(1)

        report = self.app.shadow_report()

        self.assertTrue(report["compact_live_sequence_proven"])
        self.assertFalse(report["compact_enrollment_credits"]["exact_bindings"])
        self.assertFalse(report["compact_sequence_proven"])
        self.assertFalse(report["ready_for_activation"])

    def test_compact_both_away_credit_rejects_incomplete_baseline_rows(self) -> None:
        self.finish_exact_enrollment_with_compact_credits(
            julia_baseline_incomplete_ids=True
        )
        self.app.seal_candidate()
        for scenario in MODULE.COMPACT_JOINT_SEQUENCE:
            self.provide(self.shadow_snapshot(scenario))
            self.app.shadow_sample(scenario)
            self.clock.advance(1)

        report = self.app.shadow_report()

        self.assertTrue(report["compact_live_sequence_proven"])
        self.assertTrue(report["compact_enrollment_credits"]["exact_bindings"])
        self.assertFalse(report["compact_enrollment_credits"]["both_away"])
        self.assertFalse(report["compact_sequence_proven"])
        self.assertFalse(report["ready_for_activation"])

    def test_legacy_shadow_gate_without_compact_sequence_remains_valid(self) -> None:
        self.finish_enrollment()
        self.app.seal_candidate()
        scenarios = (
            "both-present",
            "julia-only",
            "dylan-only",
            "both-away",
            "return-both",
            "julia-only",
            "dylan-only",
            "julia-only",
        )
        for scenario in scenarios:
            self.provide(self.shadow_snapshot(scenario))
            self.app.shadow_sample(scenario)
            self.clock.advance(15 * 60)

        report = self.app.shadow_report()

        self.assertFalse(report["compact_live_sequence_proven"])
        self.assertTrue(report["legacy_gate_proven"])
        self.assertTrue(report["ready_for_activation"])

    def test_return_scenario_requires_immediately_preceding_matching_away(self) -> None:
        self.finish_enrollment()
        self.app.seal_candidate()
        with self.assertRaisesRegex(
            MODULE.EnrollmentError, "return_requires_matching_both_away"
        ):
            self.app.shadow_sample("return-both")

    def test_multi_hour_gaps_do_not_count_as_real_cadence(self) -> None:
        self.finish_enrollment()
        self.app.seal_candidate()
        scenarios = (
            "both-present",
            "dylan-only",
            "julia-only",
            "both-away",
            "return-both",
            "julia-only",
            "dylan-only",
            "julia-only",
        )
        for scenario in scenarios:
            self.provide(self.shadow_snapshot(scenario))
            self.app.shadow_sample(scenario)
            self.clock.advance(2 * 60 * 60)
        report = self.app.shadow_report()
        self.assertEqual(report["cadence_intervals"], 0)
        self.assertFalse(report["ready_for_activation"])

    def test_activation_refuses_incomplete_sample_span_and_scenarios(self) -> None:
        self.finish_enrollment()
        self.app.seal_candidate()
        self.provide(self.shadow_snapshot("both-present"))
        self.app.shadow_sample("both-present")
        report = self.app.shadow_report()
        self.assertLess(report["sample_count"], report["minimum_samples"])
        self.assertLess(report["span_hours"], report["minimum_span_hours"])
        self.assertTrue(report["missing_scenarios"])
        with self.assertRaisesRegex(MODULE.EnrollmentError, "shadow_gate_incomplete"):
            self.app.activate()
        self.assertFalse(self.settings.production_config.exists())

    def test_failed_final_activation_create_leaves_no_production_capability(self) -> None:
        self.finish_enrollment()
        self.stage_ready_shadow()
        original_create = MODULE._atomic_create_json

        def fail_production(path, value, *, code):
            if path == self.settings.production_config:
                raise MODULE.EnrollmentError("production_config_create_failed")
            return original_create(path, value, code=code)

        with mock.patch.object(MODULE, "_atomic_create_json", side_effect=fail_production):
            with self.assertRaisesRegex(
                MODULE.EnrollmentError, "production_config_create_failed"
            ):
                self.app.activate()
        self.assertFalse(self.settings.production_config.exists())
        self.assertTrue(self.settings.staged_config.exists())
        self.assertTrue(self.app.status()["activation_prepared"])
        self.assertFalse(self.app.status()["activated"])
        with self.assertRaisesRegex(MODULE.EnrollmentError, "activation_already_started"):
            self.app.shadow_sample("both-present")

        activated = self.app.activate()
        self.assertTrue(activated["production_config_written"])
        self.assertTrue(self.app.status()["activated"])

    def test_post_link_activation_failure_rolls_back_exact_production_file(self) -> None:
        self.finish_enrollment()
        self.stage_ready_shadow()
        original_fsync_directory = MODULE._fsync_directory

        def fail_production_directory(path):
            if path == self.settings.openclaw_dir:
                raise OSError("injected directory fsync failure")
            return original_fsync_directory(path)

        with mock.patch.object(
            MODULE, "_fsync_directory", side_effect=fail_production_directory
        ):
            with self.assertRaisesRegex(
                MODULE.EnrollmentError, "production_config_create_failed"
            ):
                self.app.activate()
        self.assertFalse(self.settings.production_config.exists())
        self.assertTrue(self.settings.staged_config.exists())
        self.assertTrue(self.app.status()["activation_prepared"])
        self.assertFalse(self.app.status()["activated"])

    def test_activation_checks_mutation_jobs_are_really_unloaded(self) -> None:
        self.finish_enrollment()
        self.stage_ready_shadow()
        self.app.mutation_jobs_stopped_provider = lambda: False
        with self.assertRaisesRegex(
            MODULE.EnrollmentError, "mutation_jobs_still_loaded"
        ):
            self.app.activate()
        self.assertFalse(self.settings.production_config.exists())
        self.assertFalse(self.app.status()["activation_prepared"])

    def test_start_and_physical_enrollment_require_jobs_unloaded(self) -> None:
        self.app.mutation_jobs_stopped_provider = lambda: False
        with self.assertRaisesRegex(
            MODULE.EnrollmentError, "mutation_jobs_still_loaded"
        ):
            self.app.start()
        self.assertFalse(self.settings.session_dir.exists())

    def test_launchctl_probe_fails_closed_on_unexpected_service_error(self) -> None:
        missing = mock.Mock(returncode=113)
        with mock.patch.object(MODULE, "LAUNCHCTL", Path("/bin/sh")):
            with mock.patch.object(
                MODULE.subprocess,
                "run",
                side_effect=[mock.Mock(returncode=0), missing, missing, missing, missing],
            ):
                self.assertTrue(MODULE.mutation_jobs_stopped())

            with mock.patch.object(
                MODULE.subprocess,
                "run",
                side_effect=[mock.Mock(returncode=0), mock.Mock(returncode=0)],
            ):
                self.assertFalse(MODULE.mutation_jobs_stopped())

            with mock.patch.object(
                MODULE.subprocess,
                "run",
                side_effect=[mock.Mock(returncode=0), mock.Mock(returncode=2)],
            ):
                with self.assertRaisesRegex(
                    MODULE.EnrollmentError, "launchctl_check_failed"
                ):
                    MODULE.mutation_jobs_stopped()

    def test_abort_refuses_after_activation_has_started(self) -> None:
        self.finish_enrollment()
        self.stage_ready_shadow()
        self.app.activate()
        with self.assertRaisesRegex(MODULE.EnrollmentError, "activation_already_started"):
            self.app.abort()
        self.assertTrue(self.settings.session_file.exists())
        self.assertTrue(self.settings.production_config.exists())

    def test_abort_rejects_broken_staging_symlink(self) -> None:
        self.start()
        self.settings.staged_config.symlink_to(self.settings.session_dir / "missing")
        with self.assertRaisesRegex(
            MODULE.EnrollmentError, "session_permissions_invalid"
        ):
            self.app.abort()
        self.assertTrue(self.settings.session_file.exists())
        self.assertTrue(self.settings.staged_config.is_symlink())

    def test_identification_with_two_new_fresh_clients_is_ambiguous(self) -> None:
        self.start()
        baseline = snapshot({BACKGROUND_ID: evidence(idle=10)})
        self.provide(*self.repeated(baseline))
        self.app.baseline("Dylan")
        ambiguous = snapshot(
            {
                BACKGROUND_ID: evidence(idle=10),
                DYLAN_ID: evidence(idle=1),
                CHURN_ID: evidence(idle=1),
            }
        )
        self.provide(*self.repeated(ambiguous))
        with self.assertRaisesRegex(MODULE.EnrollmentError, "identity_ambiguous"):
            self.app.identify("Dylan")
        self.assertFalse(self.app.status()["people"]["Dylan"]["identified"])

    def test_transition_identify_rejects_any_baseline_present_candidate(
        self,
    ) -> None:
        self.start()
        present_first = snapshot(
            {
                BACKGROUND_ID: evidence(idle=10),
                DYLAN_ID: evidence(idle=1),
            }
        )
        stale_later = snapshot(
            {
                BACKGROUND_ID: evidence(idle=10),
                DYLAN_ID: evidence(idle=700),
            }
        )
        self.provide(present_first, stale_later, stale_later)
        self.app.baseline("Dylan")
        active = snapshot(
            {
                BACKGROUND_ID: evidence(idle=10),
                DYLAN_ID: evidence(idle=1),
            }
        )
        self.provide(*self.repeated(active))

        with self.assertRaisesRegex(
            MODULE.EnrollmentError, "identity_not_isolated"
        ):
            self.app.identify("Dylan")

        self.assertFalse(self.app.status()["people"]["Dylan"]["identified"])

    def test_exact_address_identification_is_stable_and_never_persists_address(
        self,
    ) -> None:
        self.start()
        off = snapshot(
            {
                BACKGROUND_ID: evidence(idle=10),
                JULIA_ID: evidence(idle=700),
            }
        )
        self.provide(*self.repeated(off))
        self.app.baseline("Julia")
        active = snapshot(
            {
                BACKGROUND_ID: evidence(idle=10),
                JULIA_ID: evidence(idle=1),
            }
        )
        self.provide_exact(*[(active, JULIA_ID)] * MODULE.IDENTIFY_SAMPLE_COUNT)
        address = MODULE._normalize_wifi_address(TEST_WIFI_ADDRESS)
        self.assertIsNotNone(address)

        result = self.app.identify_exact_address("Julia", address)

        self.assertEqual(result["method"], "exact_wifi_address")
        self.assertEqual(result["stable_samples"], MODULE.IDENTIFY_SAMPLE_COUNT)
        self.assertFalse(result["address_retained"])
        self.assertEqual(
            result["off_baseline_evidence"],
            {
                "lease_state": "valid",
                "idle_bucket": "idle_5_15m",
                "current_rule_present": False,
            },
        )
        persisted = self.settings.session_file.read_text(encoding="utf-8")
        serialized = json.dumps(result, sort_keys=True)
        self.assertIn(JULIA_ID, persisted)
        self.assertIn('"identify_method":"exact_wifi_address"', persisted)
        for address_form in (
            TEST_WIFI_ADDRESS,
            TEST_WIFI_ADDRESS.replace(":", "-"),
            TEST_WIFI_ADDRESS.replace(":", ""),
        ):
            self.assertNotIn(address_form, persisted)
            self.assertNotIn(address_form, serialized)
        self.assertEqual(
            self.app.status()["people"]["Julia"]["reconnect_cycles_proven"], []
        )

    def test_exact_address_mapping_drift_leaves_session_unchanged(self) -> None:
        self.start()
        off = snapshot({BACKGROUND_ID: evidence(idle=10)})
        self.provide(*self.repeated(off))
        self.app.baseline("Julia")
        session_before = self.settings.session_file.read_bytes()
        first = snapshot({JULIA_ID: evidence(idle=1)})
        changed = snapshot({CHURN_ID: evidence(idle=1)})
        self.provide_exact(
            (first, JULIA_ID),
            (changed, CHURN_ID),
            (changed, CHURN_ID),
        )
        address = MODULE._normalize_wifi_address(TEST_WIFI_ADDRESS)
        self.assertIsNotNone(address)

        with self.assertRaisesRegex(
            MODULE.EnrollmentError, "exact_address_unstable"
        ):
            self.app.identify_exact_address("Julia", address)

        self.assertEqual(self.settings.session_file.read_bytes(), session_before)

    def test_exact_address_requires_fresh_complete_liveness(self) -> None:
        self.start()
        off = snapshot({BACKGROUND_ID: evidence(idle=10)})
        self.provide(*self.repeated(off))
        self.app.baseline("Julia")
        session_before = self.settings.session_file.read_bytes()
        stale = snapshot({JULIA_ID: evidence(idle=61)})
        self.provide_exact((stale, JULIA_ID))
        address = MODULE._normalize_wifi_address(TEST_WIFI_ADDRESS)
        self.assertIsNotNone(address)

        with self.assertRaisesRegex(
            MODULE.EnrollmentError, "exact_address_not_fresh"
        ):
            self.app.identify_exact_address("Julia", address)

        self.assertEqual(self.settings.session_file.read_bytes(), session_before)

    def test_exact_address_rejects_row_present_in_phone_off_baseline(self) -> None:
        self.start()
        unsafe_off = snapshot(
            {
                BACKGROUND_ID: evidence(idle=10),
                JULIA_ID: evidence(idle=1),
            }
        )
        self.provide(*self.repeated(unsafe_off))
        self.app.baseline("Julia")
        session_before = self.settings.session_file.read_bytes()
        self.provide_exact(
            *[(unsafe_off, JULIA_ID)] * MODULE.IDENTIFY_SAMPLE_COUNT
        )
        address = MODULE._normalize_wifi_address(TEST_WIFI_ADDRESS)
        self.assertIsNotNone(address)

        with self.assertRaisesRegex(
            MODULE.EnrollmentError, "exact_address_present_in_off_baseline"
        ):
            self.app.identify_exact_address("Julia", address)

        self.assertEqual(self.settings.session_file.read_bytes(), session_before)
        self.assertFalse(self.app.status()["people"]["Julia"]["identified"])

    def test_exact_ip_identification_is_stable_and_never_persists_ip(self) -> None:
        self.start()
        off = snapshot(
            {
                BACKGROUND_ID: evidence(idle=10),
                JULIA_ID: evidence(idle=700),
            }
        )
        self.provide(*self.repeated(off))
        self.app.baseline("Julia")
        active = snapshot(
            {
                BACKGROUND_ID: evidence(idle=10),
                JULIA_ID: evidence(idle=1),
            }
        )
        self.provide_exact_ip(
            *[(active, JULIA_ID)] * MODULE.IDENTIFY_SAMPLE_COUNT
        )
        address = MODULE._normalize_cabin_ip_address(TEST_IP_ADDRESS)
        self.assertIsNotNone(address)

        result = self.app.identify_exact_ip("Julia", address)

        self.assertEqual(result["method"], "exact_ip_address")
        self.assertEqual(result["stable_samples"], MODULE.IDENTIFY_SAMPLE_COUNT)
        self.assertFalse(result["ip_retained"])
        self.assertEqual(
            result["off_baseline_evidence"],
            {
                "lease_state": "valid",
                "idle_bucket": "idle_5_15m",
                "current_rule_present": False,
            },
        )
        persisted = self.settings.session_file.read_text(encoding="utf-8")
        serialized = json.dumps(result, sort_keys=True)
        self.assertIn(JULIA_ID, persisted)
        self.assertIn('"identify_method":"exact_ip_address"', persisted)
        for ip_form in (
            TEST_IP_ADDRESS,
            bytes((192, 168, 1, 42)).hex(),
            str(int(MODULE.ipaddress.IPv4Address(TEST_IP_ADDRESS))),
        ):
            self.assertNotIn(ip_form, persisted)
            self.assertNotIn(ip_form, serialized)
        self.assertEqual(
            self.app.status()["people"]["Julia"]["reconnect_cycles_proven"], []
        )

    def test_exact_ip_mapping_drift_leaves_session_unchanged(self) -> None:
        self.start()
        off = snapshot({BACKGROUND_ID: evidence(idle=10)})
        self.provide(*self.repeated(off))
        self.app.baseline("Julia")
        session_before = self.settings.session_file.read_bytes()
        first = snapshot({JULIA_ID: evidence(idle=1)})
        changed = snapshot({CHURN_ID: evidence(idle=1)})
        self.provide_exact_ip(
            (first, JULIA_ID),
            (changed, CHURN_ID),
            (changed, CHURN_ID),
        )
        address = MODULE._normalize_cabin_ip_address(TEST_IP_ADDRESS)
        self.assertIsNotNone(address)

        with self.assertRaisesRegex(MODULE.EnrollmentError, "exact_ip_unstable"):
            self.app.identify_exact_ip("Julia", address)

        self.assertEqual(self.settings.session_file.read_bytes(), session_before)

    def test_exact_ip_tolerates_whole_tuple_omission_for_same_stable_row(self) -> None:
        self.start()
        off = snapshot({BACKGROUND_ID: evidence(idle=10)})
        self.provide(*self.repeated(off))
        self.app.baseline("Julia")
        active = snapshot({JULIA_ID: evidence(idle=1)})
        incomplete = snapshot(
            {JULIA_ID: evidence(complete=False)},
            tuple_absent_ids=(JULIA_ID,),
        )
        self.provide_exact_ip(
            (active, JULIA_ID),
            (incomplete, JULIA_ID),
            (incomplete, JULIA_ID),
        )
        address = MODULE._normalize_cabin_ip_address(TEST_IP_ADDRESS)
        self.assertIsNotNone(address)

        result = self.app.identify_exact_ip("Julia", address)

        self.assertTrue(result["ok"])
        self.assertEqual(result["stable_samples"], MODULE.IDENTIFY_SAMPLE_COUNT)

    def test_exact_ip_tolerates_missing_idle_for_valid_lease(self) -> None:
        self.start()
        off = snapshot({BACKGROUND_ID: evidence(idle=10)})
        self.provide(*self.repeated(off))
        self.app.baseline("Julia")
        active = snapshot({JULIA_ID: evidence(idle=1)})
        missing_idle = snapshot(
            {JULIA_ID: evidence(complete=False)},
            liveness_shapes={
                JULIA_ID: {
                    "lease_found": "true",
                    "lease_active": "true",
                    "lease_seconds": "positive",
                    "idle_seconds": "missing",
                    "active": "missing",
                }
            },
        )
        self.provide_exact_ip(
            (missing_idle, JULIA_ID),
            (active, JULIA_ID),
            (missing_idle, JULIA_ID),
        )
        address = MODULE._normalize_cabin_ip_address(TEST_IP_ADDRESS)
        self.assertIsNotNone(address)

        result = self.app.identify_exact_ip("Julia", address)

        self.assertTrue(result["ok"])
        self.assertEqual(result["stable_samples"], MODULE.IDENTIFY_SAMPLE_COUNT)

    def test_exact_ip_rejects_other_partial_liveness_without_saving(self) -> None:
        self.start()
        off = snapshot({BACKGROUND_ID: evidence(idle=10)})
        self.provide(*self.repeated(off))
        self.app.baseline("Julia")
        session_before = self.settings.session_file.read_bytes()
        active = snapshot({JULIA_ID: evidence(idle=1)})
        unsafe_partial = snapshot(
            {JULIA_ID: evidence(complete=False)},
            liveness_shapes={
                JULIA_ID: {
                    "lease_found": "true",
                    "lease_active": "missing",
                    "lease_seconds": "positive",
                    "idle_seconds": "missing",
                    "active": "missing",
                }
            },
        )
        self.provide_exact_ip(
            (active, JULIA_ID),
            (unsafe_partial, JULIA_ID),
        )
        address = MODULE._normalize_cabin_ip_address(TEST_IP_ADDRESS)
        self.assertIsNotNone(address)

        with self.assertRaisesRegex(
            MODULE.EnrollmentError, "exact_ip_not_present"
        ):
            self.app.identify_exact_ip("Julia", address)

        self.assertEqual(self.settings.session_file.read_bytes(), session_before)

    def test_exact_ip_rejects_all_incomplete_samples_without_saving(self) -> None:
        self.start()
        off = snapshot({BACKGROUND_ID: evidence(idle=10)})
        self.provide(*self.repeated(off))
        self.app.baseline("Julia")
        session_before = self.settings.session_file.read_bytes()
        incomplete = snapshot(
            {JULIA_ID: evidence(complete=False)},
            tuple_absent_ids=(JULIA_ID,),
        )
        self.provide_exact_ip(
            *[(incomplete, JULIA_ID)] * MODULE.EXACT_IP_MAX_SAMPLE_COUNT
        )
        address = MODULE._normalize_cabin_ip_address(TEST_IP_ADDRESS)
        self.assertIsNotNone(address)

        with self.assertRaisesRegex(
            MODULE.EnrollmentError, "exact_ip_not_present"
        ):
            self.app.identify_exact_ip("Julia", address)

        self.assertEqual(self.settings.session_file.read_bytes(), session_before)

    def test_exact_ip_waits_for_one_present_sample_in_bounded_window(self) -> None:
        self.start()
        off = snapshot({BACKGROUND_ID: evidence(idle=10)})
        self.provide(*self.repeated(off))
        self.app.baseline("Julia")
        incomplete = snapshot(
            {JULIA_ID: evidence(complete=False)},
            tuple_absent_ids=(JULIA_ID,),
        )
        active = snapshot({JULIA_ID: evidence(idle=1)})
        omitted_count = MODULE.EXACT_IP_MAX_SAMPLE_COUNT - 1
        self.provide_exact_ip(
            *[(incomplete, JULIA_ID)] * omitted_count,
            (active, JULIA_ID),
        )
        address = MODULE._normalize_cabin_ip_address(TEST_IP_ADDRESS)
        self.assertIsNotNone(address)

        result = self.app.identify_exact_ip("Julia", address)

        self.assertTrue(result["ok"])
        self.assertEqual(
            result["stable_samples"], MODULE.EXACT_IP_MAX_SAMPLE_COUNT
        )

    def test_exact_ip_rejects_complete_stale_sample_without_saving(self) -> None:
        self.start()
        off = snapshot({BACKGROUND_ID: evidence(idle=10)})
        self.provide(*self.repeated(off))
        self.app.baseline("Julia")
        session_before = self.settings.session_file.read_bytes()
        active = snapshot({JULIA_ID: evidence(idle=1)})
        stale = snapshot({JULIA_ID: evidence(idle=301)})
        self.provide_exact_ip(
            (active, JULIA_ID),
            (stale, JULIA_ID),
            (active, JULIA_ID),
        )
        address = MODULE._normalize_cabin_ip_address(TEST_IP_ADDRESS)
        self.assertIsNotNone(address)

        with self.assertRaisesRegex(
            MODULE.EnrollmentError, "exact_ip_not_present"
        ):
            self.app.identify_exact_ip("Julia", address)

        self.assertEqual(self.settings.session_file.read_bytes(), session_before)

    def test_exact_ip_accepts_production_present_row_over_sixty_seconds(self) -> None:
        self.start()
        off = snapshot({BACKGROUND_ID: evidence(idle=10)})
        self.provide(*self.repeated(off))
        self.app.baseline("Julia")
        production_present = snapshot({JULIA_ID: evidence(idle=120)})
        self.provide_exact_ip(
            *[(production_present, JULIA_ID)] * MODULE.IDENTIFY_SAMPLE_COUNT
        )
        address = MODULE._normalize_cabin_ip_address(TEST_IP_ADDRESS)
        self.assertIsNotNone(address)

        result = self.app.identify_exact_ip("Julia", address)

        self.assertTrue(result["ok"])
        self.assertEqual(result["method"], "exact_ip_address")

    def test_exact_ip_rejects_stale_phone_off_baseline(self) -> None:
        self.start()
        off = snapshot({BACKGROUND_ID: evidence(idle=10)})
        self.provide(*self.repeated(off))
        self.app.baseline("Julia")
        session_before = self.settings.session_file.read_bytes()
        self.clock.advance(MODULE.MAXIMUM_EXACT_BASELINE_AGE_SECONDS + 1)
        address = MODULE._normalize_cabin_ip_address(TEST_IP_ADDRESS)
        self.assertIsNotNone(address)

        with self.assertRaisesRegex(
            MODULE.EnrollmentError, "exact_baseline_stale"
        ):
            self.app.identify_exact_ip("Julia", address)

        self.assertEqual(self.settings.session_file.read_bytes(), session_before)

    def test_exact_ip_rejects_row_present_in_phone_off_baseline(self) -> None:
        self.start()
        unsafe_first = snapshot(
            {
                BACKGROUND_ID: evidence(idle=10),
                JULIA_ID: evidence(idle=1),
            }
        )
        stale_later = snapshot(
            {
                BACKGROUND_ID: evidence(idle=10),
                JULIA_ID: evidence(idle=700),
            }
        )
        self.provide(unsafe_first, stale_later, stale_later)
        self.app.baseline("Julia")
        session_before = self.settings.session_file.read_bytes()
        active = snapshot(
            {
                BACKGROUND_ID: evidence(idle=10),
                JULIA_ID: evidence(idle=1),
            }
        )
        self.provide_exact_ip(
            *[(active, JULIA_ID)] * MODULE.IDENTIFY_SAMPLE_COUNT
        )
        address = MODULE._normalize_cabin_ip_address(TEST_IP_ADDRESS)
        self.assertIsNotNone(address)

        with self.assertRaisesRegex(
            MODULE.EnrollmentError, "exact_ip_present_in_off_baseline"
        ):
            self.app.identify_exact_ip("Julia", address)

        self.assertEqual(self.settings.session_file.read_bytes(), session_before)
        self.assertFalse(self.app.status()["people"]["Julia"]["identified"])

    def test_reconnect_rotation_fails_closed(self) -> None:
        self.start()
        baseline = snapshot({BACKGROUND_ID: evidence(idle=10)})
        self.provide(*self.repeated(baseline))
        self.app.baseline("Dylan")
        active = snapshot(
            {BACKGROUND_ID: evidence(idle=10), DYLAN_ID: evidence(idle=1)}
        )
        self.provide(*self.repeated(active))
        self.app.identify("Dylan")
        self.provide(snapshot({BACKGROUND_ID: evidence(idle=10)}))
        self.app.disconnect("Dylan", 1)
        self.clock.advance(MODULE.MINIMUM_DISCONNECT_SECONDS)
        rotated = snapshot(
            {BACKGROUND_ID: evidence(idle=10), CHURN_ID: evidence(idle=1)}
        )
        self.provide(*self.repeated(rotated))
        with self.assertRaisesRegex(
            MODULE.EnrollmentError, "unexpected_fresh_identity"
        ):
            self.app.reconnect("Dylan", 1)
        with self.assertRaisesRegex(
            MODULE.EnrollmentError, "disconnect_context_consumed"
        ):
            self.app.reconnect("Dylan", 1)

    def test_reconnect_rejects_additional_fresh_rotated_identity(self) -> None:
        self.start()
        baseline = snapshot(
            {BACKGROUND_ID: evidence(idle=10), CHURN_ID: evidence(idle=700)}
        )
        self.provide(*self.repeated(baseline))
        self.app.baseline("Dylan")
        active = snapshot(
            {
                BACKGROUND_ID: evidence(idle=10),
                CHURN_ID: evidence(idle=700),
                DYLAN_ID: evidence(idle=1),
            }
        )
        self.provide(*self.repeated(active))
        self.app.identify("Dylan")
        self.provide(snapshot({BACKGROUND_ID: evidence(idle=10)}))
        self.app.disconnect("Dylan", 1)
        self.clock.advance(MODULE.MINIMUM_DISCONNECT_SECONDS)
        dual = snapshot(
            {
                BACKGROUND_ID: evidence(idle=10),
                DYLAN_ID: evidence(idle=1),
                CHURN_ID: evidence(idle=1),
            }
        )
        self.provide(*self.repeated(dual))
        with self.assertRaisesRegex(
            MODULE.EnrollmentError, "unexpected_fresh_identity"
        ):
            self.app.reconnect("Dylan", 1)

    def test_exact_ip_reconnect_ignores_unrelated_fresh_peer(self) -> None:
        self.start()
        baseline = snapshot({BACKGROUND_ID: evidence(idle=10)})
        self.provide(*self.repeated(baseline))
        self.app.baseline("Julia")
        active = snapshot(
            {
                BACKGROUND_ID: evidence(idle=10),
                JULIA_ID: evidence(idle=120),
            }
        )
        self.provide_exact_ip(
            *[(active, JULIA_ID)] * MODULE.IDENTIFY_SAMPLE_COUNT
        )
        address = MODULE._normalize_cabin_ip_address(TEST_IP_ADDRESS)
        self.assertIsNotNone(address)
        self.app.identify_exact_ip("Julia", address)
        self.provide(snapshot({BACKGROUND_ID: evidence(idle=10)}))
        self.app.disconnect("Julia", 1)
        self.clock.advance(MODULE.MINIMUM_DISCONNECT_SECONDS)
        reconnected_with_peer = snapshot(
            {
                BACKGROUND_ID: evidence(idle=10),
                JULIA_ID: evidence(idle=120),
                CHURN_ID: evidence(idle=1),
            }
        )
        self.provide(*self.repeated(reconnected_with_peer))

        result = self.app.reconnect("Julia", 1)

        self.assertTrue(result["observable_off_and_same_id_proven"])
        self.assertEqual(
            self.app.status()["people"]["Julia"]["reconnect_cycles_proven"], [1]
        )
        self.provide(snapshot({BACKGROUND_ID: evidence(idle=10)}))
        self.app.disconnect("Julia", 2)
        self.clock.advance(MODULE.MINIMUM_DISCONNECT_SECONDS)
        self.provide(*self.repeated(reconnected_with_peer))
        self.app.reconnect("Julia", 2)
        self.provide(reconnected_with_peer)

        idle_start = self.app.idle_start("Julia")

        self.assertTrue(idle_start["ok"])
        self.assertEqual(
            self.app.status()["people"]["Julia"]["reconnect_cycles_proven"],
            [1, 2],
        )

    def test_exact_ip_reconnect_tolerates_missing_idle_for_valid_lease(self) -> None:
        self.start()
        baseline = snapshot({BACKGROUND_ID: evidence(idle=10)})
        self.provide(*self.repeated(baseline))
        self.app.baseline("Julia")
        active = snapshot(
            {
                BACKGROUND_ID: evidence(idle=10),
                JULIA_ID: evidence(idle=120),
            }
        )
        self.provide_exact_ip(
            *[(active, JULIA_ID)] * MODULE.IDENTIFY_SAMPLE_COUNT
        )
        address = MODULE._normalize_cabin_ip_address(TEST_IP_ADDRESS)
        self.assertIsNotNone(address)
        self.app.identify_exact_ip("Julia", address)
        self.provide(snapshot({BACKGROUND_ID: evidence(idle=10)}))
        self.app.disconnect("Julia", 1)
        self.clock.advance(MODULE.MINIMUM_DISCONNECT_SECONDS)
        missing_idle = snapshot(
            {
                BACKGROUND_ID: evidence(idle=10),
                JULIA_ID: evidence(complete=False),
            },
            liveness_shapes={
                JULIA_ID: {
                    "lease_found": "true",
                    "lease_active": "true",
                    "lease_seconds": "positive",
                    "idle_seconds": "missing",
                    "active": "missing",
                }
            },
        )
        self.provide(
            *[missing_idle] * 5,
            active,
        )

        result = self.app.reconnect("Julia", 1)

        self.assertTrue(result["observable_off_and_same_id_proven"])
        self.assertEqual(result["stable_samples"], 6)
        self.assertEqual(result["strict_present_samples"], 1)
        self.assertEqual(
            self.app.status()["people"]["Julia"]["reconnect_cycles_proven"], [1]
        )

    def test_exact_ip_idle_start_reuses_recent_reconnect_proof(self) -> None:
        self.start()
        baseline = snapshot({BACKGROUND_ID: evidence(idle=10)})
        self.provide(*self.repeated(baseline))
        self.app.baseline("Julia")
        active = snapshot({JULIA_ID: evidence(idle=1)})
        self.provide_exact_ip(
            *[(active, JULIA_ID)] * MODULE.IDENTIFY_SAMPLE_COUNT
        )
        address = MODULE._normalize_cabin_ip_address(TEST_IP_ADDRESS)
        self.assertIsNotNone(address)
        self.app.identify_exact_ip("Julia", address)
        for cycle in (1, 2):
            self.provide(snapshot({BACKGROUND_ID: evidence(idle=10)}))
            self.app.disconnect("Julia", cycle)
            self.clock.advance(MODULE.MINIMUM_DISCONNECT_SECONDS)
            self.provide(*self.repeated(active))
            self.app.reconnect("Julia", cycle)
        self.clock.advance(30)
        idle_start_epoch = self.clock()
        self.provide()

        result = self.app.idle_start("Julia")

        self.assertEqual(result["start_source"], "recent_reconnect")
        session = json.loads(self.settings.session_file.read_text(encoding="utf-8"))
        self.assertEqual(
            session["people"]["Julia"]["idle"]["start_epoch"], idle_start_epoch
        )
        self.assertEqual(self.app.status()["command"], "status")

        wrong_method = json.loads(json.dumps(session))
        wrong_method["people"]["Julia"]["identify_method"] = "new"
        wrong_source = json.loads(json.dumps(session))
        wrong_source["people"]["Julia"]["idle"]["start"] = wrong_source[
            "people"
        ]["Julia"]["reconnect"]["1"]["on"]
        stale_source = json.loads(json.dumps(session))
        stale_idle = stale_source["people"]["Julia"]["idle"]
        stale_idle["start_epoch"] = (
            stale_idle["start"]["captured_epoch"]
            + MODULE.MAXIMUM_RECONNECT_IDLE_START_AGE_SECONDS
            + 1
        )
        backwards_epoch = json.loads(json.dumps(session))
        backwards_idle = backwards_epoch["people"]["Julia"]["idle"]
        backwards_idle["start_epoch"] = (
            backwards_idle["start"]["captured_epoch"] - 1
        )
        for name, candidate in (
            ("wrong_method", wrong_method),
            ("wrong_source", wrong_source),
            ("stale_source", stale_source),
            ("backwards_epoch", backwards_epoch),
        ):
            with self.subTest(name=name), self.assertRaisesRegex(
                MODULE.EnrollmentError, "session_invalid"
            ):
                MODULE._validate_session(candidate)

    def test_reconnect_allows_peer_already_fresh_in_off_snapshot(self) -> None:
        self.start()
        baseline = snapshot(
            {BACKGROUND_ID: evidence(idle=10), CHURN_ID: evidence(idle=700)}
        )
        self.provide(*self.repeated(baseline))
        self.app.baseline("Dylan")
        active = snapshot(
            {
                BACKGROUND_ID: evidence(idle=10),
                CHURN_ID: evidence(idle=700),
                DYLAN_ID: evidence(idle=1),
            }
        )
        self.provide(*self.repeated(active))
        self.app.identify("Dylan")
        off = snapshot(
            {
                BACKGROUND_ID: evidence(idle=10),
                CHURN_ID: evidence(idle=20),
            }
        )
        self.provide(off)
        self.app.disconnect("Dylan", 1)
        self.clock.advance(MODULE.MINIMUM_DISCONNECT_SECONDS)
        reconnected = snapshot(
            {
                BACKGROUND_ID: evidence(idle=15),
                CHURN_ID: evidence(idle=25),
                DYLAN_ID: evidence(idle=1),
            }
        )
        self.provide(*self.repeated(reconnected))

        result = self.app.reconnect("Dylan", 1)

        self.assertTrue(result["observable_off_and_same_id_proven"])
        self.assertEqual(
            self.app.status()["people"]["Dylan"]["reconnect_cycles_proven"], [1]
        )

    def test_reconnect_rejects_peer_that_resets_after_off_snapshot(self) -> None:
        self.start()
        baseline = snapshot(
            {BACKGROUND_ID: evidence(idle=10), CHURN_ID: evidence(idle=700)}
        )
        self.provide(*self.repeated(baseline))
        self.app.baseline("Dylan")
        active = snapshot(
            {
                BACKGROUND_ID: evidence(idle=10),
                CHURN_ID: evidence(idle=700),
                DYLAN_ID: evidence(idle=1),
            }
        )
        self.provide(*self.repeated(active))
        self.app.identify("Dylan")
        off = snapshot(
            {
                BACKGROUND_ID: evidence(idle=10),
                CHURN_ID: evidence(idle=50),
            }
        )
        self.provide(off)
        self.app.disconnect("Dylan", 1)
        self.clock.advance(MODULE.MINIMUM_DISCONNECT_SECONDS)
        no_material_reset = snapshot(
            {
                BACKGROUND_ID: evidence(idle=15),
                CHURN_ID: evidence(idle=25),
                DYLAN_ID: evidence(idle=1),
            }
        )
        material_reset = snapshot(
            {
                BACKGROUND_ID: evidence(idle=15),
                CHURN_ID: evidence(idle=1),
                DYLAN_ID: evidence(idle=1),
            }
        )
        self.provide(no_material_reset, material_reset, material_reset)

        with self.assertRaisesRegex(
            MODULE.EnrollmentError, "unexpected_fresh_identity"
        ):
            self.app.reconnect("Dylan", 1)
        self.assertEqual(
            self.app.status()["people"]["Dylan"]["reconnect_cycles_proven"], []
        )
        with self.assertRaisesRegex(
            MODULE.EnrollmentError, "disconnect_context_consumed"
        ):
            self.app.reconnect("Dylan", 1)

    def test_reconnect_rejects_peer_stale_off_even_if_fresh_at_baseline(self) -> None:
        self.start()
        baseline = snapshot(
            {BACKGROUND_ID: evidence(idle=10), CHURN_ID: evidence(idle=10)}
        )
        self.provide(*self.repeated(baseline))
        self.app.baseline("Dylan")
        active = snapshot(
            {
                BACKGROUND_ID: evidence(idle=10),
                CHURN_ID: evidence(idle=10),
                DYLAN_ID: evidence(idle=1),
            }
        )
        self.provide(*self.repeated(active))
        self.app.identify("Dylan")
        off = snapshot(
            {
                BACKGROUND_ID: evidence(idle=10),
                CHURN_ID: evidence(idle=700),
            }
        )
        self.provide(off)
        self.app.disconnect("Dylan", 1)
        self.clock.advance(MODULE.MINIMUM_DISCONNECT_SECONDS)
        fresh_again = snapshot(
            {
                BACKGROUND_ID: evidence(idle=15),
                CHURN_ID: evidence(idle=1),
                DYLAN_ID: evidence(idle=1),
            }
        )
        self.provide(*self.repeated(fresh_again))

        with self.assertRaisesRegex(
            MODULE.EnrollmentError, "unexpected_fresh_identity"
        ):
            self.app.reconnect("Dylan", 1)

    def test_reconnect_not_fresh_keeps_off_context_retryable(self) -> None:
        self.start()
        baseline = snapshot({BACKGROUND_ID: evidence(idle=10)})
        self.provide(*self.repeated(baseline))
        self.app.baseline("Dylan")
        active = snapshot(
            {BACKGROUND_ID: evidence(idle=10), DYLAN_ID: evidence(idle=1)}
        )
        self.provide(*self.repeated(active))
        self.app.identify("Dylan")
        self.provide(snapshot({BACKGROUND_ID: evidence(idle=10)}))
        self.app.disconnect("Dylan", 1)
        self.clock.advance(MODULE.MINIMUM_DISCONNECT_SECONDS)
        not_fresh = snapshot(
            {BACKGROUND_ID: evidence(idle=15), DYLAN_ID: evidence(idle=61)}
        )
        self.provide(*self.repeated(not_fresh))
        with self.assertRaisesRegex(MODULE.EnrollmentError, "reconnect_not_fresh"):
            self.app.reconnect("Dylan", 1)

        self.provide(*self.repeated(active))
        result = self.app.reconnect("Dylan", 1)

        self.assertTrue(result["observable_off_and_same_id_proven"])

    def test_later_ambiguity_consumes_context_after_not_fresh_sample(self) -> None:
        self.start()
        baseline = snapshot({BACKGROUND_ID: evidence(idle=10)})
        self.provide(*self.repeated(baseline))
        self.app.baseline("Dylan")
        active = snapshot(
            {BACKGROUND_ID: evidence(idle=10), DYLAN_ID: evidence(idle=1)}
        )
        self.provide(*self.repeated(active))
        self.app.identify("Dylan")
        self.provide(snapshot({BACKGROUND_ID: evidence(idle=10)}))
        self.app.disconnect("Dylan", 1)
        self.clock.advance(MODULE.MINIMUM_DISCONNECT_SECONDS)
        first_not_fresh = snapshot(
            {BACKGROUND_ID: evidence(idle=15), DYLAN_ID: evidence(idle=61)}
        )
        second_fresh = snapshot(
            {BACKGROUND_ID: evidence(idle=16), DYLAN_ID: evidence(idle=1)}
        )
        later_ambiguous = snapshot(
            {
                BACKGROUND_ID: evidence(idle=17),
                DYLAN_ID: evidence(idle=1),
                CHURN_ID: evidence(idle=1),
            }
        )
        self.provide(first_not_fresh, second_fresh, later_ambiguous)

        with self.assertRaisesRegex(
            MODULE.EnrollmentError, "unexpected_fresh_identity"
        ):
            self.app.reconnect("Dylan", 1)
        with self.assertRaisesRegex(
            MODULE.EnrollmentError, "disconnect_context_consumed"
        ):
            self.app.reconnect("Dylan", 1)
        session = json.loads(self.settings.session_file.read_text(encoding="utf-8"))
        cycle = session["people"]["Dylan"]["reconnect"]["1"]
        self.assertIsNone(cycle["on"])
        self.assertFalse(cycle["proven"])
        self.assertTrue(cycle["off_context_consumed"])

    def test_disconnect_context_uses_only_session_keyed_fingerprints(self) -> None:
        self.start()
        baseline = snapshot(
            {BACKGROUND_ID: evidence(idle=700), DYLAN_ID: evidence(idle=700)}
        )
        self.provide(*self.repeated(baseline))
        self.app.baseline("Dylan")
        active = snapshot(
            {BACKGROUND_ID: evidence(idle=700), DYLAN_ID: evidence(idle=1)}
        )
        self.provide(*self.repeated(active))
        self.app.identify("Dylan")
        off = snapshot({BACKGROUND_ID: evidence(idle=10)})
        self.provide(off)
        result = self.app.disconnect("Dylan", 1)

        persisted = self.settings.session_file.read_text(encoding="utf-8")
        session = json.loads(persisted)
        peer_context = session["people"]["Dylan"]["reconnect"]["1"][
            "off_peer_fingerprints"
        ]

        self.assertEqual(len(peer_context), 1)
        self.assertEqual(
            set(peer_context), {MODULE._fingerprint(session, BACKGROUND_ID)}
        )
        serialized = json.dumps([result, peer_context], sort_keys=True)
        for raw_id in (BACKGROUND_ID, DYLAN_ID):
            self.assertNotIn(raw_id, serialized)

    def test_observe_selected_is_read_only_and_redacted(self) -> None:
        protected = self.openclaw / "presence" / "state.json"
        protected.parent.mkdir(mode=0o700)
        protected.write_bytes(b"presence-sentinel\n")
        os.chmod(protected, 0o600)
        self.start()
        baseline = snapshot(
            {BACKGROUND_ID: evidence(idle=700), DYLAN_ID: evidence(idle=700)}
        )
        self.provide(*self.repeated(baseline))
        self.app.baseline("Dylan")
        active = snapshot(
            {BACKGROUND_ID: evidence(idle=700), DYLAN_ID: evidence(idle=1)}
        )
        self.provide(*self.repeated(active))
        self.app.identify("Dylan")
        session_before = self.settings.session_file.read_bytes()
        observed = MODULE.parse_starlink_payload(
            {
                "wifiGetClients": {
                    "clients": [
                        {
                            "captiveClientId": DYLAN_ID,
                            "dhcpLeaseFound": True,
                            "dhcpLeaseActive": True,
                            "secondsUntilDhcpLeaseExpires": 900,
                            "noDataIdleS": 120,
                            "active": False,
                        }
                    ]
                }
            }
        )
        self.provide(observed)

        result = self.app.observe_selected("Dylan")

        self.assertEqual(
            result["observation"]["evidence"],
            {
                "lease_state": "valid",
                "idle_bucket": "recent_31_300s",
                "current_rule_present": True,
            },
        )
        self.assertFalse(result["observation"]["liveness_tuple_absent"])
        self.assertEqual(
            result["observation"]["liveness_shape"],
            {
                "lease_found": "true",
                "lease_active": "true",
                "lease_seconds": "positive",
                "idle_seconds": "nonnegative",
                "active": "false",
            },
        )
        self.assertEqual(self.settings.session_file.read_bytes(), session_before)
        self.assertEqual(protected.read_bytes(), b"presence-sentinel\n")
        serialized = json.dumps(result, sort_keys=True)
        for sentinel in PRIVACY_SENTINELS:
            self.assertNotIn(sentinel, serialized)

    def test_reconnect_context_session_invariants_fail_closed(self) -> None:
        self.start()
        baseline = snapshot(
            {BACKGROUND_ID: evidence(idle=700), DYLAN_ID: evidence(idle=700)}
        )
        self.provide(*self.repeated(baseline))
        self.app.baseline("Dylan")
        active = snapshot(
            {BACKGROUND_ID: evidence(idle=700), DYLAN_ID: evidence(idle=1)}
        )
        self.provide(*self.repeated(active))
        self.app.identify("Dylan")
        self.provide(snapshot({BACKGROUND_ID: evidence(idle=10)}))
        self.app.disconnect("Dylan", 1)
        pending = json.loads(
            self.settings.session_file.read_text(encoding="utf-8")
        )

        malformed_evidence = json.loads(json.dumps(pending))
        malformed_cycle = malformed_evidence["people"]["Dylan"]["reconnect"]["1"]
        peer_key = next(iter(malformed_cycle["off_peer_fingerprints"]))
        malformed_cycle["off_peer_fingerprints"][peer_key] = {"complete": True}

        target_fingerprint = json.loads(json.dumps(pending))
        target_cycle = target_fingerprint["people"]["Dylan"]["reconnect"]["1"]
        target_cycle["off_peer_fingerprints"] = {
            MODULE._fingerprint(target_fingerprint, DYLAN_ID): evidence(
                idle=10
            ).private_json()
        }

        missing_context = json.loads(json.dumps(pending))
        missing_context["people"]["Dylan"]["reconnect"]["1"][
            "off_peer_fingerprints"
        ] = None

        orphan_context = json.loads(json.dumps(pending))
        orphan_cycle = orphan_context["people"]["Dylan"]["reconnect"]["1"]
        orphan_cycle["off"] = None

        for name, candidate in (
            ("malformed_evidence", malformed_evidence),
            ("target_fingerprint", target_fingerprint),
            ("missing_context", missing_context),
            ("orphan_context", orphan_context),
        ):
            with self.subTest(name=name), self.assertRaisesRegex(
                MODULE.EnrollmentError, "session_invalid"
            ):
                MODULE._validate_session(candidate)

        self.clock.advance(MODULE.MINIMUM_DISCONNECT_SECONDS)
        self.provide(*self.repeated(active))
        self.app.reconnect("Dylan", 1)
        completed = json.loads(
            self.settings.session_file.read_text(encoding="utf-8")
        )

        unconsumed_on = json.loads(json.dumps(completed))
        unconsumed_on["people"]["Dylan"]["reconnect"]["1"][
            "off_context_consumed"
        ] = False
        with self.assertRaisesRegex(MODULE.EnrollmentError, "session_invalid"):
            MODULE._validate_session(unconsumed_on)

        false_proof = json.loads(json.dumps(completed))
        false_proof["people"]["Dylan"]["reconnect"]["1"]["on"] = None
        with self.assertRaisesRegex(MODULE.EnrollmentError, "session_invalid"):
            MODULE._validate_session(false_proof)

    def test_older_sessions_are_intentionally_rejected_by_v4_helper(self) -> None:
        self.start()
        session = json.loads(self.settings.session_file.read_text(encoding="utf-8"))
        for older_version in (1, 2, 3):
            with self.subTest(schema_version=older_version):
                candidate = json.loads(json.dumps(session))
                candidate["schema_version"] = older_version
                with self.assertRaisesRegex(
                    MODULE.EnrollmentError, "session_invalid"
                ):
                    MODULE._validate_session(candidate)

    def test_baseline_strict_present_fingerprint_invariants_fail_closed(
        self,
    ) -> None:
        self.start()
        baseline = snapshot({BACKGROUND_ID: evidence(idle=1)})
        self.provide(*self.repeated(baseline))
        self.app.baseline("Julia")
        session = json.loads(
            self.settings.session_file.read_text(encoding="utf-8")
        )
        record = session["people"]["Julia"]["baseline"]
        present_fingerprint = MODULE._fingerprint(session, BACKGROUND_ID)
        self.assertEqual(
            record["strict_present_fingerprints"], [present_fingerprint]
        )

        missing = json.loads(json.dumps(session))
        missing["people"]["Julia"]["baseline"][
            "strict_present_fingerprints"
        ] = []
        duplicate = json.loads(json.dumps(session))
        duplicate["people"]["Julia"]["baseline"][
            "strict_present_fingerprints"
        ] = [present_fingerprint, present_fingerprint]
        unknown = json.loads(json.dumps(session))
        unknown["people"]["Julia"]["baseline"][
            "strict_present_fingerprints"
        ] = ["f" * 64]

        for name, candidate in (
            ("missing", missing),
            ("duplicate", duplicate),
            ("unknown", unknown),
        ):
            with self.subTest(name=name), self.assertRaisesRegex(
                MODULE.EnrollmentError, "session_invalid"
            ):
                MODULE._validate_session(candidate)

    def test_disconnect_requires_observable_off_evidence(self) -> None:
        self.start()
        baseline = snapshot({BACKGROUND_ID: evidence(idle=10)})
        self.provide(*self.repeated(baseline))
        self.app.baseline("Dylan")
        active = snapshot(
            {BACKGROUND_ID: evidence(idle=10), DYLAN_ID: evidence(idle=1)}
        )
        self.provide(*self.repeated(active))
        self.app.identify("Dylan")
        self.provide(active)
        with self.assertRaisesRegex(
            MODULE.EnrollmentError, "disconnect_not_observed"
        ):
            self.app.disconnect("Dylan", 1)

    def test_disconnect_accepts_incomplete_target_as_enrollment_only_off(self) -> None:
        self.start()
        baseline = snapshot({BACKGROUND_ID: evidence(idle=10)})
        self.provide(*self.repeated(baseline))
        self.app.baseline("Dylan")
        active = snapshot(
            {BACKGROUND_ID: evidence(idle=10), DYLAN_ID: evidence(idle=1)}
        )
        self.provide(*self.repeated(active))
        self.app.identify("Dylan")
        incomplete_off = snapshot(
            {
                BACKGROUND_ID: evidence(idle=10),
                DYLAN_ID: evidence(complete=False),
            },
            tuple_absent_ids={DYLAN_ID},
        )
        self.provide(incomplete_off)

        result = self.app.disconnect("Dylan", 1)

        self.assertEqual(
            result["observation"]["evidence"],
            {
                "lease_state": "incomplete",
                "idle_bucket": "unknown",
                "current_rule_present": None,
            },
        )
        self.clock.advance(MODULE.MINIMUM_DISCONNECT_SECONDS)
        self.provide(*self.repeated(incomplete_off))
        with self.assertRaisesRegex(MODULE.EnrollmentError, "reconnect_not_fresh"):
            self.app.reconnect("Dylan", 1)

    def test_disconnect_rejects_partial_or_malformed_liveness_tuple(self) -> None:
        self.start()
        baseline = snapshot({BACKGROUND_ID: evidence(idle=10)})
        self.provide(*self.repeated(baseline))
        self.app.baseline("Dylan")
        active = snapshot(
            {BACKGROUND_ID: evidence(idle=10), DYLAN_ID: evidence(idle=1)}
        )
        self.provide(*self.repeated(active))
        self.app.identify("Dylan")
        partial = snapshot(
            {
                BACKGROUND_ID: evidence(idle=10),
                DYLAN_ID: evidence(complete=False),
            }
        )
        self.provide(partial)

        with self.assertRaisesRegex(
            MODULE.EnrollmentError, "disconnect_not_observed"
        ):
            self.app.disconnect("Dylan", 1)

    def test_idle_checkpoint_cannot_be_recorded_early(self) -> None:
        self.start()
        baseline = snapshot({BACKGROUND_ID: evidence(idle=700)})
        self.provide(*self.repeated(baseline))
        self.app.baseline("Dylan")
        active = snapshot(
            {BACKGROUND_ID: evidence(idle=700), DYLAN_ID: evidence(idle=1)}
        )
        self.provide(*self.repeated(active))
        self.app.identify("Dylan")
        for cycle in (1, 2):
            self.provide(snapshot({BACKGROUND_ID: evidence(idle=700)}))
            self.app.disconnect("Dylan", cycle)
            self.clock.advance(MODULE.MINIMUM_DISCONNECT_SECONDS)
            self.provide(*self.repeated(active))
            self.app.reconnect("Dylan", cycle)
        self.provide(active)
        self.app.idle_start("Dylan")
        self.clock.advance(60)
        with self.assertRaisesRegex(MODULE.EnrollmentError, "idle_checkpoint_too_early"):
            self.app.idle_check("Dylan", 5)

    def test_activation_refuses_existing_production_config(self) -> None:
        self.finish_enrollment()
        self.app.seal_candidate()
        self.settings.production_config.write_text("{}\n", encoding="utf-8")
        os.chmod(self.settings.production_config, 0o600)
        with self.assertRaisesRegex(
            MODULE.EnrollmentError, "production_config_already_exists"
        ):
            self.app.seal_candidate()

    def test_unsafe_openclaw_parent_is_rejected(self) -> None:
        os.chmod(self.openclaw, 0o755)
        self.provide(snapshot({}))
        with self.assertRaisesRegex(
            MODULE.EnrollmentError, "openclaw_permissions_invalid"
        ):
            self.app.preflight()

    def test_hardlinked_session_is_rejected(self) -> None:
        self.start()
        second = self.settings.session_dir / "session-copy.json"
        os.link(self.settings.session_file, second)
        with self.assertRaisesRegex(MODULE.EnrollmentError, "session_permissions_invalid"):
            self.app.status()

    def test_mutating_cli_requires_attended_tty(self) -> None:
        parser = MODULE._build_parser()
        args = parser.parse_args(["--attended", "start"])
        fake_stdin = mock.Mock()
        fake_stdin.isatty.return_value = False
        with mock.patch.object(MODULE.sys, "stdin", fake_stdin):
            with self.assertRaisesRegex(MODULE.EnrollmentError, "attended_tty_required"):
                MODULE._require_attended(args)

    def test_hidden_wifi_address_requires_matching_double_entry(self) -> None:
        prompts = []
        supplied = iter((TEST_WIFI_ADDRESS, TEST_WIFI_ADDRESS.lower()))

        address = MODULE._read_confirmed_wifi_address(
            lambda prompt: prompts.append(prompt) or next(supplied)
        )

        self.assertEqual(address, bytes.fromhex("020000000001"))
        self.assertEqual(len(prompts), 2)
        self.assertNotIn(TEST_WIFI_ADDRESS, "".join(prompts))

        mismatch = iter((TEST_WIFI_ADDRESS, "02:00:00:00:00:02"))
        with self.assertRaisesRegex(
            MODULE.EnrollmentError, "wifi_address_confirmation_mismatch"
        ):
            MODULE._read_confirmed_wifi_address(lambda _prompt: next(mismatch))

        with self.assertRaisesRegex(
            MODULE.EnrollmentError, "wifi_address_input_unavailable"
        ):
            with mock.patch.object(
                MODULE.getpass,
                "getpass",
                side_effect=lambda _prompt: MODULE.warnings.warn(
                    "echo fallback blocked", MODULE.getpass.GetPassWarning
                ),
            ):
                MODULE._read_confirmed_wifi_address()

    def test_hidden_ip_address_requires_matching_double_entry(self) -> None:
        prompts = []
        supplied = iter((TEST_IP_ADDRESS, TEST_IP_ADDRESS))

        address = MODULE._read_confirmed_ip_address(
            lambda prompt: prompts.append(prompt) or next(supplied)
        )

        self.assertEqual(address, bytes((192, 168, 1, 42)))
        self.assertEqual(len(prompts), 2)
        self.assertNotIn(TEST_IP_ADDRESS, "".join(prompts))

        mismatch = iter((TEST_IP_ADDRESS, "192.168.1.43"))
        with self.assertRaisesRegex(
            MODULE.EnrollmentError, "ip_address_confirmation_mismatch"
        ):
            MODULE._read_confirmed_ip_address(lambda _prompt: next(mismatch))

        with self.assertRaisesRegex(
            MODULE.EnrollmentError, "ip_address_input_unavailable"
        ):
            with mock.patch.object(
                MODULE.getpass,
                "getpass",
                side_effect=lambda _prompt: MODULE.warnings.warn(
                    "echo fallback blocked", MODULE.getpass.GetPassWarning
                ),
            ):
                MODULE._read_confirmed_ip_address()

    def test_error_output_contains_only_safe_code(self) -> None:
        stderr = io.StringIO()
        with mock.patch.object(MODULE.sys, "stderr", stderr):
            with mock.patch.object(MODULE.sys, "stdin", io.StringIO("")):
                result = MODULE.main(["--attended", "start"])
        self.assertEqual(result, 1)
        output = stderr.getvalue()
        self.assertIn("attended_tty_required", output)
        for sentinel in PRIVACY_SENTINELS:
            self.assertNotIn(sentinel, output)

    def test_invalid_argument_does_not_echo_the_supplied_value(self) -> None:
        stderr = io.StringIO()
        with mock.patch.object(MODULE.sys, "stderr", stderr):
            result = MODULE.main(["baseline", DYLAN_ID])
        self.assertEqual(result, 1)
        self.assertEqual(
            json.loads(stderr.getvalue()),
            {"error": "arguments_invalid", "ok": False},
        )
        self.assertNotIn(DYLAN_ID, stderr.getvalue())

        stderr = io.StringIO()
        with mock.patch.object(MODULE.sys, "stderr", stderr):
            result = MODULE.main(
                [
                    "--attended",
                    "identify-exact-address",
                    "Julia",
                    TEST_WIFI_ADDRESS,
                ]
            )
        self.assertEqual(result, 1)
        self.assertEqual(
            json.loads(stderr.getvalue()),
            {"error": "arguments_invalid", "ok": False},
        )
        self.assertNotIn(TEST_WIFI_ADDRESS, stderr.getvalue())

        stderr = io.StringIO()
        with mock.patch.object(MODULE.sys, "stderr", stderr):
            result = MODULE.main(
                [
                    "--attended",
                    "identify-exact-ip",
                    "Julia",
                    TEST_IP_ADDRESS,
                ]
            )
        self.assertEqual(result, 1)
        self.assertEqual(
            json.loads(stderr.getvalue()),
            {"error": "arguments_invalid", "ok": False},
        )
        self.assertNotIn(TEST_IP_ADDRESS, stderr.getvalue())


if __name__ == "__main__":
    unittest.main()
