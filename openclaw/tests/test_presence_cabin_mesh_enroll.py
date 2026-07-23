from __future__ import annotations

import hashlib
import importlib.machinery
import importlib.util
import ipaddress
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


SCRIPT = Path(__file__).parents[1] / "bin" / "presence-cabin-mesh-enroll"
LOADER = importlib.machinery.SourceFileLoader("presence_cabin_mesh_enroll", str(SCRIPT))
SPEC = importlib.util.spec_from_loader(LOADER.name, LOADER)
assert SPEC is not None
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
LOADER.exec_module(MODULE)


def opaque_id(label: str) -> str:
    return hashlib.sha256(label.encode("utf-8")).hexdigest()


def private_ip(offset: int) -> str:
    base = int(ipaddress.IPv4Address(0xAC100000))
    return str(ipaddress.IPv4Address(base + offset))


class FakeClock:
    def __init__(self) -> None:
        self.value = 1000.0

    def __call__(self) -> float:
        return self.value

    def sleep(self, seconds: float) -> None:
        self.value += seconds

    def advance(self, seconds: float) -> None:
        self.value += seconds


class FakeQuery:
    def __init__(self) -> None:
        self.target = f"router:{opaque_id('mesh-target')[:24]}"
        self.node_address = private_ip(7)
        self.mesh_responses: list[dict[str, object]] = []
        self.controller_override: dict[str, object] | None = None

    def topology(self) -> dict[str, object]:
        return {
            "wifiGetClients": {
                "clients": [
                    {
                        "role": "REPEATER",
                        "hopsFromController": 1,
                        "deviceId": self.target,
                        "ipAddress": self.node_address,
                    }
                ]
            }
        }

    def __call__(self, target: str | None) -> dict[str, object]:
        if target is None:
            return self.controller_override or self.topology()
        if target != self.target:
            raise RuntimeError("unexpected_private_target")
        if not self.mesh_responses:
            raise RuntimeError("missing_test_response")
        return self.mesh_responses.pop(0)

    def enqueue(self, responses: list[dict[str, object]]) -> None:
        self.mesh_responses.extend(responses)


def client_row(
    binding: str,
    address: str,
    association: int,
    *,
    active: bool = False,
    include_active: bool = True,
    strict: bool = True,
) -> dict[str, object]:
    row: dict[str, object] = {
        "captiveClientId": binding,
        "ipAddress": address,
        "role": "CLIENT",
        "associatedTimeS": association,
        "signalStrength": -47.0,
        "rxStatsValid": strict,
        "txStatsValid": strict,
    }
    if include_active:
        row["active"] = active
    return row


def payload(*rows: dict[str, object]) -> dict[str, object]:
    return {"wifiGetClients": {"clients": list(rows)}}


class MeshEnrollmentTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.home = Path(self.temporary.name)
        self.openclaw = self.home / ".openclaw"
        self.openclaw.mkdir(mode=0o700)
        self.scanner = self.home / "runtime-scanner"
        self.write_ready_scanner()
        self.settings = MODULE.Settings(
            home=self.home,
            scanner=self.scanner,
        )
        self.controller_ids = {
            person: opaque_id(f"controller-{index}")
            for index, person in enumerate(MODULE.PEOPLE)
        }
        self.mesh_ids = {
            person: opaque_id(f"mesh-{index}")
            for index, person in enumerate(MODULE.PEOPLE)
        }
        self.addresses = {
            person: private_ip(40 + index)
            for index, person in enumerate(MODULE.PEOPLE)
        }
        self.v1 = {
            "schema_version": 1,
            "site": "cabin",
            "people": {
                person: {
                    "kind": "starlink_captive_client_id",
                    "value": self.controller_ids[person],
                }
                for person in MODULE.PEOPLE
            },
        }
        self.original_v1 = (
            json.dumps(self.v1, indent=2, sort_keys=False) + "\n"
        ).encode("utf-8")
        self.settings.production_config.write_bytes(self.original_v1)
        os.chmod(self.settings.production_config, 0o600)

        self.legacy_dir = self.openclaw / "presence-enrollment"
        self.legacy_dir.mkdir(mode=0o700)
        self.legacy_session = self.legacy_dir / "session.json"
        self.legacy_bytes = opaque_id("existing-session").encode("ascii")
        self.legacy_session.write_bytes(self.legacy_bytes)
        os.chmod(self.legacy_session, 0o600)

        self.clock = FakeClock()
        self.query = FakeQuery()
        self.app = MODULE.MeshEnrollment(
            self.settings,
            query=self.query,
            jobs_stopped=lambda: True,
            clock=self.clock,
            sleeper=self.clock.sleep,
        )

    def write_ready_scanner(self) -> None:
        self.scanner.write_text(
            "\n".join(
                (
                    "#!/bin/sh",
                    'PRESENCE_SCANNER_CONFIG_CONTRACT="cabin-sources-v2"',
                    '[ "$1" = "validate-config" ] || exit 11',
                    '[ "$2" = "cabin" ] || exit 12',
                    '[ -r "$PRESENCE_DEVICE_CONFIG" ] || exit 13',
                    "/usr/bin/grep -q '\"schema_version\":2' "
                    '"$PRESENCE_DEVICE_CONFIG" || exit 14',
                    "exit 0",
                    "",
                )
            )
        )
        os.chmod(self.scanner, 0o700)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def reader(self, person: str):
        values = iter([self.addresses[person], self.addresses[person]])
        return lambda _prompt: next(values)

    def positive_responses(
        self,
        person: str,
        *,
        binding: str | None = None,
        active: bool = False,
        include_active: bool = True,
        associations: list[int] | None = None,
    ) -> list[dict[str, object]]:
        candidate = binding or self.mesh_ids[person]
        values = associations or list(range(100, 107))
        return [
            payload(
                client_row(
                    candidate,
                    self.addresses[person],
                    association,
                    active=active,
                    include_active=include_active,
                )
            )
            for association in values
        ]

    def init(self) -> None:
        result = self.app.init()
        self.assertTrue(result["active_schema_preserved"])

    def enroll(self, person: str) -> None:
        self.query.enqueue(self.positive_responses(person))
        identified = self.app.identify(person, reader=self.reader(person))
        self.assertTrue(identified["strict_association_proven"])

        self.query.enqueue([payload() for _ in range(MODULE.OFF_SAMPLE_LIMIT)])
        disconnected = self.app.disconnect(person)
        self.assertEqual(
            disconnected["off_state_samples"],
            MODULE.MINIMUM_CONSECUTIVE_OFF_SAMPLES,
        )

        self.clock.advance(MODULE.MINIMUM_DISCONNECT_SECONDS)
        self.query.enqueue(self.positive_responses(person))
        reconnected = self.app.reconnect(person, reader=self.reader(person))
        self.assertTrue(reconnected["same_identity_proven"])

    def baseline_enroll(self, person: str) -> None:
        self.query.enqueue(
            [payload() for _ in range(MODULE.BASELINE_SAMPLE_COUNT)]
        )
        baseline = self.app.baseline(person)
        self.assertFalse(baseline["raw_identifiers_retained"])
        self.clock.advance(MODULE.MINIMUM_DISCONNECT_SECONDS)
        self.query.enqueue(self.positive_responses(person))
        identified = self.app.identify(person, reader=self.reader(person))
        self.assertTrue(identified["off_on_credited"])

    def complete_and_stage(self) -> dict[str, object]:
        self.init()
        for person in MODULE.PEOPLE:
            self.baseline_enroll(person)
        staged = self.app.stage()
        self.assertTrue(staged["ready_for_activation"])
        return staged

    def test_stage_preserves_active_v1_and_existing_enrollment_session(self) -> None:
        staged = self.complete_and_stage()

        self.assertEqual(self.settings.production_config.read_bytes(), self.original_v1)
        self.assertEqual(self.legacy_session.read_bytes(), self.legacy_bytes)
        self.assertEqual(staged["schema_version"], 2)
        candidate = json.loads(self.settings.staged_config.read_text())
        MODULE._validate_v2_config(candidate)
        self.assertEqual(candidate["schema_version"], 2)
        self.assertNotEqual(
            candidate["sources"][1]["bindings"][MODULE.PEOPLE[0]]["value"],
            candidate["sources"][1]["bindings"][MODULE.PEOPLE[1]]["value"],
        )

    def test_activation_replaces_atomically_and_retains_exact_backup(self) -> None:
        self.complete_and_stage()

        result = self.app.activate(
            confirm_safe_report=True,
            confirm_jobs_stopped=True,
            confirm_v2_consumer_ready=True,
            confirm_exact_rollback=True,
        )

        self.assertTrue(result["active_config_replaced_atomically"])
        self.assertEqual(self.settings.backup_file.read_bytes(), self.original_v1)
        installed = json.loads(self.settings.production_config.read_text())
        self.assertEqual(installed["schema_version"], 2)
        self.assertEqual(self.legacy_session.read_bytes(), self.legacy_bytes)

    def test_repeat_activation_validates_and_retains_exact_backup(self) -> None:
        self.complete_and_stage()
        self.app.activate(
            confirm_safe_report=True,
            confirm_jobs_stopped=True,
            confirm_v2_consumer_ready=True,
            confirm_exact_rollback=True,
        )

        result = self.app.activate(
            confirm_safe_report=True,
            confirm_jobs_stopped=True,
            confirm_v2_consumer_ready=True,
            confirm_exact_rollback=True,
        )

        self.assertTrue(result["already_active"])
        self.assertTrue(result["exact_backup_retained"])
        self.assertEqual(self.settings.backup_file.read_bytes(), self.original_v1)

    def test_repeat_activation_rejects_missing_backup(self) -> None:
        self.complete_and_stage()
        self.app.activate(
            confirm_safe_report=True,
            confirm_jobs_stopped=True,
            confirm_v2_consumer_ready=True,
            confirm_exact_rollback=True,
        )
        self.settings.backup_file.unlink()

        with self.assertRaisesRegex(
            MODULE.EnrollmentError, "^production_backup_invalid$"
        ):
            self.app.activate(
                confirm_safe_report=True,
                confirm_jobs_stopped=True,
                confirm_v2_consumer_ready=True,
                confirm_exact_rollback=True,
            )

    def test_repeat_activation_rejects_mismatched_backup(self) -> None:
        self.complete_and_stage()
        self.app.activate(
            confirm_safe_report=True,
            confirm_jobs_stopped=True,
            confirm_v2_consumer_ready=True,
            confirm_exact_rollback=True,
        )
        changed = json.loads(self.original_v1)
        changed["people"][MODULE.PEOPLE[0]]["value"] = opaque_id(
            "repeat-activation-backup-mismatch"
        )
        MODULE._atomic_replace_bytes(
            self.settings.backup_file,
            (json.dumps(changed) + "\n").encode("utf-8"),
            code="test_write_failed",
        )

        with self.assertRaisesRegex(
            MODULE.EnrollmentError, "^production_backup_invalid$"
        ):
            self.app.activate(
                confirm_safe_report=True,
                confirm_jobs_stopped=True,
                confirm_v2_consumer_ready=True,
                confirm_exact_rollback=True,
            )

    def test_repeat_activation_rejects_corrupt_backup(self) -> None:
        self.complete_and_stage()
        self.app.activate(
            confirm_safe_report=True,
            confirm_jobs_stopped=True,
            confirm_v2_consumer_ready=True,
            confirm_exact_rollback=True,
        )
        MODULE._atomic_replace_bytes(
            self.settings.backup_file,
            b"{not-json}\n",
            code="test_write_failed",
        )

        with self.assertRaisesRegex(
            MODULE.EnrollmentError, "^production_backup_invalid$"
        ):
            self.app.activate(
                confirm_safe_report=True,
                confirm_jobs_stopped=True,
                confirm_v2_consumer_ready=True,
                confirm_exact_rollback=True,
            )

    def test_fresh_absent_baseline_credits_off_to_on_without_second_toggle(self) -> None:
        self.init()
        person = MODULE.PEOPLE[0]
        self.baseline_enroll(person)

        status = self.app.status()
        self.assertEqual(status["people_completed"], 1)

    def test_candidate_present_in_baseline_is_rejected(self) -> None:
        self.init()
        person = MODULE.PEOPLE[0]
        baseline_row = {
            "captiveClientId": self.mesh_ids[person],
        }
        self.query.enqueue(
            [
                payload(baseline_row)
                for _ in range(MODULE.BASELINE_SAMPLE_COUNT)
            ]
        )
        self.app.baseline(person)
        self.clock.advance(MODULE.MINIMUM_DISCONNECT_SECONDS)
        self.query.enqueue(self.positive_responses(person))

        with self.assertRaisesRegex(
            MODULE.EnrollmentError, "^candidate_present_in_off_baseline$"
        ):
            self.app.identify(person, reader=self.reader(person))

    def test_off_baseline_expires(self) -> None:
        self.init()
        person = MODULE.PEOPLE[0]
        self.query.enqueue(
            [payload() for _ in range(MODULE.BASELINE_SAMPLE_COUNT)]
        )
        self.app.baseline(person)
        self.clock.advance(MODULE.MAXIMUM_BASELINE_AGE_SECONDS + 1)

        with self.assertRaisesRegex(
            MODULE.EnrollmentError, "^off_baseline_not_fresh$"
        ):
            self.app.identify(person, reader=self.reader(person))

    def test_baseline_retains_only_session_keyed_fingerprints(self) -> None:
        self.init()
        person = MODULE.PEOPLE[0]
        unrelated = [opaque_id("unrelated-a"), opaque_id("unrelated-b")]
        baseline_rows = [
            {"captiveClientId": candidate}
            for candidate in unrelated
        ]
        self.query.enqueue(
            [
                payload(*baseline_rows)
                for _ in range(MODULE.BASELINE_SAMPLE_COUNT)
            ]
        )
        result = self.app.baseline(person)
        session_bytes = self.settings.session_file.read_bytes()

        self.assertFalse(result["raw_identifiers_retained"])
        for candidate in unrelated:
            self.assertNotIn(candidate.encode("ascii"), session_bytes)

    def test_baseline_is_invalidated_by_source_config_change(self) -> None:
        self.init()
        person = MODULE.PEOPLE[0]
        self.query.enqueue(
            [payload() for _ in range(MODULE.BASELINE_SAMPLE_COUNT)]
        )
        self.app.baseline(person)
        self.clock.advance(MODULE.MINIMUM_DISCONNECT_SECONDS)
        changed = json.loads(self.original_v1)
        changed["people"][person]["value"] = opaque_id("changed-after-baseline")
        self.settings.production_config.write_text(json.dumps(changed) + "\n")
        os.chmod(self.settings.production_config, 0o600)

        with self.assertRaisesRegex(
            MODULE.EnrollmentError, "^production_v1_changed$"
        ):
            self.app.identify(person, reader=self.reader(person))

    def test_activation_failure_restores_exact_original_bytes(self) -> None:
        self.complete_and_stage()
        original_save = self.app._save

        def fail_after_replacement(session: dict[str, object]) -> None:
            if session.get("activated_epoch") is not None:
                raise MODULE.EnrollmentError("injected_failure")
            original_save(session)

        self.app._save = fail_after_replacement
        with self.assertRaisesRegex(MODULE.EnrollmentError, "^activation_failed$"):
            self.app.activate(
                confirm_safe_report=True,
                confirm_jobs_stopped=True,
                confirm_v2_consumer_ready=True,
                confirm_exact_rollback=True,
            )

        self.assertEqual(self.settings.production_config.read_bytes(), self.original_v1)
        self.assertEqual(self.settings.backup_file.read_bytes(), self.original_v1)
        self.assertEqual(self.legacy_session.read_bytes(), self.legacy_bytes)

    def test_abort_removes_only_new_pre_activation_artifacts(self) -> None:
        self.complete_and_stage()

        result = self.app.abort(confirm_abandon=True)

        self.assertTrue(result["session_removed"])
        self.assertFalse(self.settings.session_file.exists())
        self.assertFalse(self.settings.staged_config.exists())
        self.assertFalse(self.settings.safe_report.exists())
        self.assertEqual(self.settings.production_config.read_bytes(), self.original_v1)
        self.assertEqual(self.legacy_session.read_bytes(), self.legacy_bytes)

    def test_abort_refuses_after_activation(self) -> None:
        self.complete_and_stage()
        self.app.activate(
            confirm_safe_report=True,
            confirm_jobs_stopped=True,
            confirm_v2_consumer_ready=True,
            confirm_exact_rollback=True,
        )

        with self.assertRaisesRegex(
            MODULE.EnrollmentError, "^abort_after_activation_forbidden$"
        ):
            self.app.abort(confirm_abandon=True)
        self.assertTrue(self.settings.session_file.exists())
        self.assertEqual(self.legacy_session.read_bytes(), self.legacy_bytes)

    def test_cleanup_refuses_before_activation(self) -> None:
        self.complete_and_stage()
        with self.assertRaisesRegex(
            MODULE.EnrollmentError, "^cleanup_before_activation_forbidden$"
        ):
            self.app.cleanup(confirm_post_canary_success=True)
        self.assertEqual(self.settings.production_config.read_bytes(), self.original_v1)
        self.assertTrue(self.settings.session_file.exists())

    def test_cleanup_after_activation_retains_backup_and_safe_report(self) -> None:
        self.complete_and_stage()
        self.app.activate(
            confirm_safe_report=True,
            confirm_jobs_stopped=True,
            confirm_v2_consumer_ready=True,
            confirm_exact_rollback=True,
        )

        result = self.app.cleanup(confirm_post_canary_success=True)

        self.assertTrue(result["exact_v1_backup_retained"])
        self.assertFalse(self.settings.session_file.exists())
        self.assertFalse(self.settings.staged_config.exists())
        self.assertEqual(self.settings.backup_file.read_bytes(), self.original_v1)
        self.assertTrue(self.settings.safe_report.exists())
        self.assertEqual(
            json.loads(self.settings.production_config.read_text())["schema_version"],
            2,
        )
        self.assertEqual(self.legacy_session.read_bytes(), self.legacy_bytes)

    def test_active_false_is_diagnostic_not_a_negative_or_positive_gate(self) -> None:
        self.init()
        person = MODULE.PEOPLE[0]
        self.query.enqueue(self.positive_responses(person, active=False))
        self.app.identify(person, reader=self.reader(person))

        self.query.enqueue(self.positive_responses(person, active=False))
        with self.assertRaisesRegex(
            MODULE.EnrollmentError, "^disconnect_not_proven$"
        ):
            self.app.disconnect(person)

    def test_missing_active_diagnostic_is_ignored(self) -> None:
        self.init()
        person = MODULE.PEOPLE[0]
        self.query.enqueue(
            self.positive_responses(person, include_active=False)
        )
        result = self.app.identify(person, reader=self.reader(person))
        self.assertTrue(result["strict_association_proven"])

    def test_nonadvancing_association_cannot_identify(self) -> None:
        self.init()
        person = MODULE.PEOPLE[0]
        self.query.enqueue(
            self.positive_responses(person, associations=[100] * 7)
        )
        with self.assertRaisesRegex(
            MODULE.EnrollmentError, "^mesh_positive_not_proven$"
        ):
            self.app.identify(person, reader=self.reader(person))

    def test_decreasing_association_cannot_identify(self) -> None:
        self.init()
        person = MODULE.PEOPLE[0]
        self.query.enqueue(
            self.positive_responses(
                person,
                associations=list(range(107, 100, -1)),
            )
        )
        with self.assertRaisesRegex(
            MODULE.EnrollmentError, "^mesh_positive_not_proven$"
        ):
            self.app.identify(person, reader=self.reader(person))

    def test_present_static_association_cannot_prove_disconnect(self) -> None:
        self.init()
        person = MODULE.PEOPLE[0]
        self.query.enqueue(self.positive_responses(person))
        self.app.identify(person, reader=self.reader(person))
        self.query.enqueue(
            self.positive_responses(person, associations=[777] * 7)
        )
        with self.assertRaisesRegex(
            MODULE.EnrollmentError, "^disconnect_not_proven$"
        ):
            self.app.disconnect(person)

    def test_earlier_positive_streak_followed_by_missing_tail_is_rejected(self) -> None:
        self.init()
        person = MODULE.PEOPLE[0]
        responses = self.positive_responses(
            person, associations=[100, 101, 102, 103]
        )
        responses.extend([payload(), payload(), payload()])
        self.query.enqueue(responses)

        with self.assertRaisesRegex(
            MODULE.EnrollmentError, "^mesh_positive_not_proven$"
        ):
            self.app.identify(person, reader=self.reader(person))

    def test_only_final_consecutive_positive_window_authorizes_identify(self) -> None:
        self.init()
        person = MODULE.PEOPLE[0]
        responses = self.positive_responses(
            person, associations=[100, 101]
        )
        responses.append(payload())
        responses.extend(
            self.positive_responses(
                person, associations=[200, 201, 202, 203]
            )
        )
        self.query.enqueue(responses)

        result = self.app.identify(person, reader=self.reader(person))
        self.assertTrue(result["strict_association_proven"])

    def test_earlier_absence_streak_followed_by_present_tail_is_rejected(self) -> None:
        self.init()
        person = MODULE.PEOPLE[0]
        self.query.enqueue(self.positive_responses(person))
        self.app.identify(person, reader=self.reader(person))
        responses = [payload() for _ in range(4)]
        responses.extend(
            self.positive_responses(
                person, associations=[300, 301, 302]
            )
        )
        self.query.enqueue(responses)

        with self.assertRaisesRegex(
            MODULE.EnrollmentError, "^disconnect_not_proven$"
        ):
            self.app.disconnect(person)

    def test_reconnect_requires_same_node_local_identity(self) -> None:
        self.init()
        person = MODULE.PEOPLE[0]
        self.query.enqueue(self.positive_responses(person))
        self.app.identify(person, reader=self.reader(person))
        self.query.enqueue([payload() for _ in range(MODULE.OFF_SAMPLE_LIMIT)])
        self.app.disconnect(person)
        self.clock.advance(MODULE.MINIMUM_DISCONNECT_SECONDS)
        self.query.enqueue(
            self.positive_responses(person, binding=opaque_id("different"))
        )
        with self.assertRaisesRegex(
            MODULE.EnrollmentError, "^reconnect_identity_changed$"
        ):
            self.app.reconnect(person, reader=self.reader(person))

    def test_mesh_binding_cannot_collide_with_other_person_controller(self) -> None:
        self.init()
        person = MODULE.PEOPLE[0]
        other = MODULE.PEOPLE[1]
        self.query.enqueue(
            self.positive_responses(
                person, binding=self.controller_ids[other]
            )
        )
        with self.assertRaisesRegex(MODULE.EnrollmentError, "^binding_collision$"):
            self.app.identify(person, reader=self.reader(person))

    def test_controller_identity_continuity_credit_succeeds(self) -> None:
        self.init()
        person = MODULE.PEOPLE[1]
        self.query.enqueue(
            self.positive_responses(
                person, binding=self.controller_ids[person]
            )
        )

        result = self.app.credit_controller_identity(
            person,
            confirm_existing_controller_identity=True,
        )

        self.assertTrue(result["controller_identity_continuity_credited"])
        self.assertFalse(result["private_identifiers_exposed"])
        self.assertEqual(self.app.status()["people_completed"], 1)
        self.assertEqual(self.settings.production_config.read_bytes(), self.original_v1)

    def test_controller_identity_continuity_requires_confirmation(self) -> None:
        self.init()
        with self.assertRaisesRegex(
            MODULE.EnrollmentError,
            "^controller_identity_confirmation_missing$",
        ):
            self.app.credit_controller_identity(
                MODULE.PEOPLE[1],
                confirm_existing_controller_identity=False,
            )

    def test_controller_identity_continuity_rejects_absent_row(self) -> None:
        self.init()
        self.query.enqueue(
            [payload() for _ in range(MODULE.POSITIVE_SAMPLE_LIMIT)]
        )
        with self.assertRaisesRegex(
            MODULE.EnrollmentError,
            "^controller_identity_continuity_not_proven$",
        ):
            self.app.credit_controller_identity(
                MODULE.PEOPLE[1],
                confirm_existing_controller_identity=True,
            )

    def test_controller_identity_continuity_rejects_flapping_tail(self) -> None:
        self.init()
        person = MODULE.PEOPLE[1]
        responses = self.positive_responses(
            person,
            binding=self.controller_ids[person],
            associations=[100, 101, 102],
        )
        responses.append(payload())
        responses.extend(
            self.positive_responses(
                person,
                binding=self.controller_ids[person],
                associations=[200, 201],
            )
        )
        responses.append(payload())
        self.query.enqueue(responses)

        with self.assertRaisesRegex(
            MODULE.EnrollmentError,
            "^controller_identity_continuity_not_proven$",
        ):
            self.app.credit_controller_identity(
                person,
                confirm_existing_controller_identity=True,
            )

    def test_controller_identity_continuity_rejects_nonstrict_rows(self) -> None:
        self.init()
        person = MODULE.PEOPLE[1]
        self.query.enqueue(
            [
                payload(
                    client_row(
                        self.controller_ids[person],
                        self.addresses[person],
                        association,
                        strict=False,
                    )
                )
                for association in range(100, 107)
            ]
        )
        with self.assertRaisesRegex(
            MODULE.EnrollmentError,
            "^controller_identity_continuity_not_proven$",
        ):
            self.app.credit_controller_identity(
                person,
                confirm_existing_controller_identity=True,
            )

    def test_controller_identity_continuity_rejects_other_person_collision(
        self,
    ) -> None:
        self.init()
        person = MODULE.PEOPLE[1]
        other = MODULE.PEOPLE[0]
        session = json.loads(self.settings.session_file.read_text())
        session["people"][other]["mesh_id"] = self.controller_ids[person]
        session["people"][other]["identified_epoch"] = self.clock()
        MODULE._atomic_replace_bytes(
            self.settings.session_file,
            MODULE._encode_json(session),
            code="test_write_failed",
        )

        with self.assertRaisesRegex(MODULE.EnrollmentError, "^binding_collision$"):
            self.app.credit_controller_identity(
                person,
                confirm_existing_controller_identity=True,
            )

    def test_controller_identity_continuity_rejects_source_change(self) -> None:
        self.init()
        person = MODULE.PEOPLE[1]
        changed = json.loads(self.original_v1)
        changed["people"][person]["value"] = opaque_id("continuity-change")
        self.settings.production_config.write_text(json.dumps(changed) + "\n")
        os.chmod(self.settings.production_config, 0o600)

        with self.assertRaisesRegex(
            MODULE.EnrollmentError, "^production_v1_changed$"
        ):
            self.app.credit_controller_identity(
                person,
                confirm_existing_controller_identity=True,
            )

    def test_controller_identity_continuity_output_is_private(self) -> None:
        self.init()
        person = MODULE.PEOPLE[1]
        self.query.enqueue(
            self.positive_responses(
                person, binding=self.controller_ids[person]
            )
        )
        result = self.app.credit_controller_identity(
            person,
            confirm_existing_controller_identity=True,
        )
        output = json.dumps(result, sort_keys=True)

        for private_value in (
            self.query.target,
            *self.controller_ids.values(),
            *self.mesh_ids.values(),
            *self.addresses.values(),
        ):
            self.assertNotIn(private_value, output)

    def test_requires_exactly_one_direct_repeater(self) -> None:
        repeated = self.query.topology()["wifiGetClients"]["clients"][0]
        self.query.controller_override = payload(dict(repeated), dict(repeated))
        with self.assertRaisesRegex(
            MODULE.EnrollmentError, "^mesh_topology_invalid$"
        ):
            self.app.init()
        self.assertEqual(self.settings.production_config.read_bytes(), self.original_v1)

    def test_rejects_non_direct_repeater_and_unsafe_target(self) -> None:
        cases = []
        non_direct = self.query.topology()["wifiGetClients"]["clients"][0].copy()
        non_direct["hopsFromController"] = 2
        cases.append(payload(non_direct))
        unsafe_target = self.query.topology()["wifiGetClients"]["clients"][0].copy()
        unsafe_target["deviceId"] = "bad target"
        cases.append(payload(unsafe_target))
        for controller_payload in cases:
            with self.subTest():
                self.query.controller_override = controller_payload
                with self.assertRaisesRegex(
                    MODULE.EnrollmentError, "^mesh_topology_invalid$"
                ):
                    self.app.init()
        self.assertFalse(self.settings.session_file.exists())

    def test_private_ipv4_accepts_other_rfc1918_subnet_but_is_not_retained(self) -> None:
        self.init()
        person = MODULE.PEOPLE[0]
        self.query.enqueue(self.positive_responses(person))
        result = self.app.identify(person, reader=self.reader(person))

        self.assertFalse(result["address_retained"])
        private_value = self.addresses[person].encode("ascii")
        for path in (
            self.settings.session_file,
            self.settings.safe_report,
            self.settings.staged_config,
        ):
            if path.exists():
                self.assertNotIn(private_value, path.read_bytes())

    def test_report_and_command_results_do_not_expose_private_values(self) -> None:
        staged = self.complete_and_stage()
        report = self.app.report()
        output = json.dumps([staged, report], sort_keys=True)
        private_values = [
            self.query.target,
            self.query.node_address,
            *self.controller_ids.values(),
            *self.mesh_ids.values(),
            *self.addresses.values(),
        ]
        for value in private_values:
            self.assertNotIn(value, output)

    def test_running_protected_job_blocks_mutation(self) -> None:
        blocked = MODULE.MeshEnrollment(
            self.settings,
            query=self.query,
            jobs_stopped=lambda: False,
            clock=self.clock,
            sleeper=self.clock.sleep,
        )
        with self.assertRaisesRegex(
            MODULE.EnrollmentError, "^protected_jobs_running$"
        ):
            blocked.init()
        self.assertFalse(self.settings.session_file.exists())

    def test_activation_requires_all_confirmations(self) -> None:
        self.complete_and_stage()
        with self.assertRaisesRegex(
            MODULE.EnrollmentError, "^activation_confirmations_missing$"
        ):
            self.app.activate(
                confirm_safe_report=True,
                confirm_jobs_stopped=True,
                confirm_v2_consumer_ready=False,
                confirm_exact_rollback=True,
            )
        self.assertEqual(self.settings.production_config.read_bytes(), self.original_v1)

    def test_activation_requires_runtime_scanner_contract_marker(self) -> None:
        self.complete_and_stage()
        self.scanner.write_text("#!/bin/sh\nexit 0\n")
        os.chmod(self.scanner, 0o700)

        with self.assertRaisesRegex(
            MODULE.EnrollmentError, "^runtime_consumer_not_ready$"
        ):
            self.app.activate(
                confirm_safe_report=True,
                confirm_jobs_stopped=True,
                confirm_v2_consumer_ready=True,
                confirm_exact_rollback=True,
            )
        self.assertEqual(self.settings.production_config.read_bytes(), self.original_v1)

    def test_activation_requires_runtime_validate_config_success(self) -> None:
        self.complete_and_stage()
        self.scanner.write_text(
            "\n".join(
                (
                    "#!/bin/sh",
                    'PRESENCE_SCANNER_CONFIG_CONTRACT="cabin-sources-v2"',
                    "exit 23",
                    "",
                )
            )
        )
        os.chmod(self.scanner, 0o700)

        with self.assertRaisesRegex(
            MODULE.EnrollmentError, "^runtime_consumer_not_ready$"
        ):
            self.app.activate(
                confirm_safe_report=True,
                confirm_jobs_stopped=True,
                confirm_v2_consumer_ready=True,
                confirm_exact_rollback=True,
            )
        self.assertEqual(self.settings.production_config.read_bytes(), self.original_v1)

    def test_activation_rejects_writable_runtime_scanner(self) -> None:
        self.complete_and_stage()
        os.chmod(self.scanner, 0o720)

        with self.assertRaisesRegex(
            MODULE.EnrollmentError, "^runtime_consumer_not_ready$"
        ):
            self.app.activate(
                confirm_safe_report=True,
                confirm_jobs_stopped=True,
                confirm_v2_consumer_ready=True,
                confirm_exact_rollback=True,
            )
        self.assertEqual(self.settings.production_config.read_bytes(), self.original_v1)

    def test_post_activation_rollback_restores_exact_v1_and_state(self) -> None:
        self.complete_and_stage()
        self.app.activate(
            confirm_safe_report=True,
            confirm_jobs_stopped=True,
            confirm_v2_consumer_ready=True,
            confirm_exact_rollback=True,
        )

        result = self.app.rollback(
            confirm_post_activation_rollback=True
        )

        self.assertFalse(result["already_rolled_back"])
        self.assertEqual(self.settings.production_config.read_bytes(), self.original_v1)
        self.assertFalse(self.app.status()["activated"])
        self.assertTrue(self.settings.session_file.exists())
        self.assertTrue(self.settings.staged_config.exists())
        self.assertTrue(self.settings.safe_report.exists())
        self.assertEqual(self.settings.backup_file.read_bytes(), self.original_v1)
        self.assertEqual(self.legacy_session.read_bytes(), self.legacy_bytes)

    def test_post_activation_rollback_is_idempotent(self) -> None:
        self.complete_and_stage()
        self.app.activate(
            confirm_safe_report=True,
            confirm_jobs_stopped=True,
            confirm_v2_consumer_ready=True,
            confirm_exact_rollback=True,
        )
        self.app.rollback(confirm_post_activation_rollback=True)

        result = self.app.rollback(
            confirm_post_activation_rollback=True
        )

        self.assertTrue(result["already_rolled_back"])
        self.assertEqual(self.settings.production_config.read_bytes(), self.original_v1)
        self.assertFalse(self.app.status()["activated"])

    def test_post_activation_rollback_recovers_interrupted_v1_state(self) -> None:
        self.complete_and_stage()
        self.app.activate(
            confirm_safe_report=True,
            confirm_jobs_stopped=True,
            confirm_v2_consumer_ready=True,
            confirm_exact_rollback=True,
        )
        MODULE._atomic_replace_bytes(
            self.settings.production_config,
            self.original_v1,
            code="test_write_failed",
        )
        self.assertTrue(self.app.status()["activated"])

        result = self.app.rollback(
            confirm_post_activation_rollback=True
        )

        self.assertTrue(result["already_rolled_back"])
        self.assertFalse(self.app.status()["activated"])
        self.assertEqual(self.settings.production_config.read_bytes(), self.original_v1)

    def test_post_activation_rollback_rejects_unsafe_current_config(self) -> None:
        self.complete_and_stage()
        self.app.activate(
            confirm_safe_report=True,
            confirm_jobs_stopped=True,
            confirm_v2_consumer_ready=True,
            confirm_exact_rollback=True,
        )
        MODULE._atomic_replace_bytes(
            self.settings.production_config,
            b"{}\n",
            code="test_write_failed",
        )

        with self.assertRaisesRegex(
            MODULE.EnrollmentError, "^rollback_current_config_unsafe$"
        ):
            self.app.rollback(confirm_post_activation_rollback=True)

    def test_post_activation_rollback_rejects_backup_mismatch(self) -> None:
        self.complete_and_stage()
        self.app.activate(
            confirm_safe_report=True,
            confirm_jobs_stopped=True,
            confirm_v2_consumer_ready=True,
            confirm_exact_rollback=True,
        )
        changed = json.loads(self.original_v1)
        changed["people"][MODULE.PEOPLE[0]]["value"] = opaque_id(
            "backup-mismatch"
        )
        MODULE._atomic_replace_bytes(
            self.settings.backup_file,
            (json.dumps(changed) + "\n").encode("utf-8"),
            code="test_write_failed",
        )

        with self.assertRaisesRegex(
            MODULE.EnrollmentError, "^production_backup_invalid$"
        ):
            self.app.rollback(confirm_post_activation_rollback=True)

    def test_rollback_write_failure_reconfirms_v2(self) -> None:
        self.complete_and_stage()
        self.app.activate(
            confirm_safe_report=True,
            confirm_jobs_stopped=True,
            confirm_v2_consumer_ready=True,
            confirm_exact_rollback=True,
        )
        expected_v2 = self.settings.production_config.read_bytes()
        original_replace = MODULE._atomic_replace_bytes

        def fail_v1_restore(path, value, *, code):
            if path == self.settings.production_config and value == self.original_v1:
                raise MODULE.EnrollmentError("injected_write_failure")
            return original_replace(path, value, code=code)

        with mock.patch.object(
            MODULE, "_atomic_replace_bytes", side_effect=fail_v1_restore
        ):
            with self.assertRaisesRegex(
                MODULE.EnrollmentError,
                "^rollback_restore_failed_v2_preserved$",
            ):
                self.app.rollback(confirm_post_activation_rollback=True)

        self.assertEqual(self.settings.production_config.read_bytes(), expected_v2)
        self.assertTrue(self.app.status()["activated"])

    def test_rollback_observed_post_write_error_completes_verified_v1(self) -> None:
        self.complete_and_stage()
        self.app.activate(
            confirm_safe_report=True,
            confirm_jobs_stopped=True,
            confirm_v2_consumer_ready=True,
            confirm_exact_rollback=True,
        )
        original_replace = MODULE._atomic_replace_bytes
        injected = False

        def replace_then_raise(path, value, *, code):
            nonlocal injected
            if (
                not injected
                and path == self.settings.production_config
                and value == self.original_v1
            ):
                injected = True
                original_replace(path, value, code=code)
                raise MODULE.EnrollmentError("injected_post_write_failure")
            return original_replace(path, value, code=code)

        with mock.patch.object(
            MODULE, "_atomic_replace_bytes", side_effect=replace_then_raise
        ):
            result = self.app.rollback(
                confirm_post_activation_rollback=True
            )

        self.assertTrue(injected)
        self.assertFalse(result["already_rolled_back"])
        self.assertEqual(self.settings.production_config.read_bytes(), self.original_v1)
        self.assertFalse(self.app.status()["activated"])

    def test_rollback_session_failure_restores_and_reconfirms_v2(self) -> None:
        self.complete_and_stage()
        self.app.activate(
            confirm_safe_report=True,
            confirm_jobs_stopped=True,
            confirm_v2_consumer_ready=True,
            confirm_exact_rollback=True,
        )
        expected_v2 = self.settings.production_config.read_bytes()

        with mock.patch.object(
            self.app,
            "_save",
            side_effect=MODULE.EnrollmentError("injected_session_failure"),
        ):
            with self.assertRaisesRegex(
                MODULE.EnrollmentError,
                "^rollback_session_update_failed_v2_restored$",
            ):
                self.app.rollback(confirm_post_activation_rollback=True)

        self.assertEqual(self.settings.production_config.read_bytes(), expected_v2)
        self.assertTrue(self.app.status()["activated"])

    def test_source_v1_change_blocks_staging(self) -> None:
        self.init()
        for person in MODULE.PEOPLE:
            self.enroll(person)
        changed = json.loads(self.original_v1)
        changed["people"][MODULE.PEOPLE[0]]["value"] = opaque_id("changed")
        self.settings.production_config.write_text(json.dumps(changed) + "\n")
        os.chmod(self.settings.production_config, 0o600)

        with self.assertRaisesRegex(
            MODULE.EnrollmentError, "^production_v1_changed$"
        ):
            self.app.stage()
        self.assertFalse(self.settings.staged_config.exists())

    def test_protected_config_permissions_fail_closed(self) -> None:
        os.chmod(self.settings.production_config, 0o644)
        with self.assertRaisesRegex(
            MODULE.EnrollmentError, "^production_v1_invalid$"
        ):
            self.app.init()
        self.assertFalse(self.settings.session_file.exists())


if __name__ == "__main__":
    unittest.main()
