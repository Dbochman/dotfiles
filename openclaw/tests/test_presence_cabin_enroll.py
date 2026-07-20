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
PRIVACY_SENTINELS = (
    DYLAN_ID,
    JULIA_ID,
    "Private Phone Name",
    "02:00:00:00:00:01",
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


def snapshot(clients, *, total: int | None = None):
    return MODULE.Snapshot(
        clients=clients,
        total_clients=len(clients) if total is None else total,
        missing_ids=0,
        malformed_ids=0,
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
            mutation_jobs_stopped_provider=lambda: True,
            clock=self.clock,
            sleeper=lambda _seconds: None,
        )

    def provide(self, *snapshots) -> None:
        self.app.snapshot_provider = QueueProvider(snapshots)

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

    def test_idle_checkpoints_must_be_ordered_and_within_their_windows(self) -> None:
        self.finish_enrollment()
        self.provide(snapshot({DYLAN_ID: evidence(idle=1)}))
        self.app.idle_start("Dylan")
        self.clock.advance(10 * 60)
        with self.assertRaisesRegex(
            MODULE.EnrollmentError, "idle_checkpoint_out_of_order"
        ):
            self.app.idle_check("Dylan", 10)
        with self.assertRaisesRegex(MODULE.EnrollmentError, "idle_checkpoint_too_late"):
            self.app.idle_check("Dylan", 5)

    def test_incomplete_selected_rows_fail_even_when_expected_away(self) -> None:
        self.finish_enrollment()
        self.app.seal_candidate()
        self.provide(
            snapshot(
                {
                    DYLAN_ID: evidence(complete=False),
                    JULIA_ID: evidence(complete=False),
                }
            )
        )
        result = self.app.shadow_sample("both-away")
        self.assertFalse(result["matches_ground_truth"])
        self.assertEqual(self.app.shadow_report()["mismatch_count"], 1)

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
            "both-present",
            "dylan-only",
            "both-present",
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
            MODULE.EnrollmentError, "identity_changed_or_missing"
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


if __name__ == "__main__":
    unittest.main()
