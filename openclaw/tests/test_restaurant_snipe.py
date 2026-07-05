#!/usr/bin/env python3

import importlib.util
import json
import os
import plistlib
import shlex
import shutil
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock


REPO_ROOT = Path(__file__).resolve().parents[2]
HELPER_PATH = REPO_ROOT / "openclaw/skills/restaurant-snipe/scripts/restaurant_snipe.py"
SPEC = importlib.util.spec_from_file_location("restaurant_snipe_helper", HELPER_PATH)
assert SPEC and SPEC.loader
restaurant_snipe = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(restaurant_snipe)


class RestaurantSnipeTest(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name)
        self.home = self.root / "home"
        self.home.mkdir()
        self.stage_root = self.root / "staged"
        self.existing_path = self.root / "existing.json"
        self.existing_path.write_text('{"reservations": []}\n', encoding="utf-8")
        self.launchctl_log = self.root / "launchctl.log"
        self.imsg_log = self.root / "imsg.log"
        self.book_log = self.root / "book.log"
        self.op_sentinel = self.root / "op-was-called"
        self.authorized_at = datetime.now(timezone.utc).replace(microsecond=0)
        self.expires_at = self.authorized_at + timedelta(hours=1)
        self.reservation_date = (self.authorized_at + timedelta(days=2)).date().isoformat()
        self.run_now = self.authorized_at + timedelta(minutes=10)
        self.fake_launchctl = self._write_executable(
            "fake-launchctl",
            f"#!/bin/sh\nprintf '%s\\n' \"$*\" >> {shlex.quote(str(self.launchctl_log))}\nexit 0\n",
        )
        self.fake_imsg = self._write_executable(
            "fake-imsg",
            f"#!/bin/sh\nprintf '%s\\n' \"$*\" >> {shlex.quote(str(self.imsg_log))}\nexit 0\n",
        )
        self.original_environment = os.environ.copy()

    def tearDown(self):
        os.environ.clear()
        os.environ.update(self.original_environment)
        self.tempdir.cleanup()

    def _write_executable(self, name, contents):
        path = self.root / name
        path.write_text(contents, encoding="utf-8")
        path.chmod(0o755)
        return path

    def _write_opentable_module(self):
        path = self.root / "fake_opentable.py"
        path.write_text(
            """
import json
import os
from pathlib import Path

class OpenTableCredentials:
    def _op_read(self, field):
        Path(os.environ["OP_SENTINEL"]).write_text(field, encoding="utf-8")
        return "forbidden"

class OpenTableAPI:
    def __init__(self):
        self.creds = OpenTableCredentials()
        self.creds._op_read("auth_token")

    def find_availability(self, venue_id, day, target, party_size):
        return json.loads(os.environ["FAKE_AVAILABILITY"])

    def book(self, venue_id, config_token, slot_hash, slot_start, party_size, dining_area_id):
        with open(os.environ["BOOK_LOG"], "a", encoding="utf-8") as handle:
            handle.write("book\\n")
        return json.loads(os.environ["FAKE_BOOK_RESULT"])
""".lstrip(),
            encoding="utf-8",
        )
        return path

    def _write_resy_module(self):
        path = self.root / "fake_resy.py"
        path.write_text(
            """
import json
import os
from pathlib import Path

class ResyCredentials:
    def _op_read(self, field):
        Path(os.environ["OP_SENTINEL"]).write_text(field, encoding="utf-8")
        return "forbidden"

    def get(self, field):
        return "cached-payment"

class ResyAPI:
    def __init__(self):
        self.creds = ResyCredentials()
        self.creds._op_read("api_key")

    def get_reservations(self):
        return json.loads(os.environ["FAKE_RESERVATIONS"])

    def find_availability(self, venue_id, day, party_size):
        raise AssertionError("availability must not run when an existing reservation matches")

    def get_details(self, config_token, day, party_size):
        raise AssertionError("details must not run when an existing reservation matches")

    def book(self, book_token, payment_id):
        with open(os.environ["BOOK_LOG"], "a", encoding="utf-8") as handle:
            handle.write("book\\n")
        return {"reservation": {"resy_token": "SHOULD_NOT_BOOK"}}
""".lstrip(),
            encoding="utf-8",
        )
        return path

    def _stage_args(self, platform="opentable", existing_path=None, output_name="job"):
        module = self._write_opentable_module() if platform == "opentable" else self._write_resy_module()
        argv = [
            "stage",
            "--output-dir",
            str(self.stage_root / output_name),
            "--slug",
            output_name,
            "--authorization-id",
            f"approval-{output_name}",
            "--approved-by",
            "test-user",
            "--authorized-at",
            restaurant_snipe.timestamp(self.authorized_at),
            "--platform",
            platform,
            "--venue-id",
            "venue-123",
            "--venue-name",
            "Fixture Restaurant",
            "--date",
            self.reservation_date,
            "--party-size",
            "2",
            "--target-time",
            "19:00",
            "--window-start",
            "18:30",
            "--window-end",
            "19:30",
            "--max-delta-minutes",
            "30",
            "--duration-seconds",
            "3600",
            "--expires-at",
            restaurant_snipe.timestamp(self.expires_at),
            "--poll-interval-seconds",
            "600",
            "--notification-target",
            "test-chat-target",
            "--existing-reservations-json",
            str(existing_path or self.existing_path),
            "--existing-reservation-checked-at",
            restaurant_snipe.timestamp(self.authorized_at - timedelta(minutes=5)),
            "--existing-reservation-source",
            "fixture-account-view",
            "--home",
            str(self.home),
            "--python-path",
            sys.executable,
            "--runner-path",
            str(HELPER_PATH),
            "--platform-module",
            str(module),
            "--launchctl-path",
            str(self.fake_launchctl),
            "--imsg-path",
            str(self.fake_imsg),
        ]
        return restaurant_snipe.make_parser().parse_args(argv)

    def _stage_and_deploy_fixture(self, platform="opentable", output_name="job"):
        args = self._stage_args(platform=platform, output_name=output_name)
        result = restaurant_snipe.stage(args)
        staged_config = Path(result["authorization_path"])
        staged_plist = Path(result["plist_path"])
        payload = json.loads(staged_config.read_text(encoding="utf-8"))
        runtime_config = Path(payload["runtime"]["config"])
        runtime_plist = Path(payload["runtime"]["plist"])
        runtime_config.parent.mkdir(parents=True, exist_ok=True)
        runtime_plist.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(staged_config, runtime_config)
        shutil.copy2(staged_plist, runtime_plist)
        return payload, runtime_config, runtime_plist

    def _set_fake_environment(self):
        os.environ["OP_SENTINEL"] = str(self.op_sentinel)
        os.environ["BOOK_LOG"] = str(self.book_log)

    def _set_confirmable_opentable_environment(self):
        self._set_fake_environment()
        os.environ["FAKE_AVAILABILITY"] = json.dumps(
            {
                "suggestedAvailability": [
                    {
                        "timeslots": [
                            {
                                "available": True,
                                "dateTime": f"{self.reservation_date}T19:00:00-05:00",
                                "token": "CONFIG_TOKEN_CANARY",
                                "slotHash": "SLOT_HASH_CANARY",
                                "diningAreas": [{"id": "area-1"}],
                            }
                        ]
                    }
                ]
            }
        )
        os.environ["FAKE_BOOK_RESULT"] = json.dumps(
            {"reservation": {"reservationId": "BOOKING_TOKEN_CANARY", "status": "confirmed"}}
        )

    def test_atomic_json_fsyncs_parent_after_replace(self):
        parent = self.root / "durable-json"
        parent.mkdir()
        target = parent / "state.json"
        events = []
        real_replace = restaurant_snipe.os.replace
        real_fsync_directory = restaurant_snipe.fsync_directory

        def recording_replace(source, destination):
            events.append(("replace", Path(destination)))
            real_replace(source, destination)

        def recording_fsync_directory(path):
            events.append(("fsync-directory", Path(path)))
            real_fsync_directory(path)

        with mock.patch.object(restaurant_snipe.os, "replace", side_effect=recording_replace), \
             mock.patch.object(
                 restaurant_snipe,
                 "fsync_directory",
                 side_effect=recording_fsync_directory,
             ):
            restaurant_snipe.atomic_json(target, {"status": "ready"})

        self.assertEqual(
            events,
            [("replace", target), ("fsync-directory", parent)],
        )
        self.assertEqual(target.stat().st_mode & 0o777, 0o600)
        self.assertEqual(json.loads(target.read_text(encoding="utf-8")), {"status": "ready"})

    def test_durable_unlink_fsyncs_parent(self):
        parent = self.root / "durable-unlink"
        parent.mkdir()
        target = parent / "marker.json"
        target.write_text("{}\n", encoding="utf-8")
        synced = []
        real_fsync_directory = restaurant_snipe.fsync_directory

        def recording_fsync_directory(path):
            synced.append(Path(path))
            real_fsync_directory(path)

        with mock.patch.object(
            restaurant_snipe,
            "fsync_directory",
            side_effect=recording_fsync_directory,
        ):
            removed = restaurant_snipe.durable_unlink(target)

        self.assertTrue(removed)
        self.assertFalse(target.exists())
        self.assertEqual(synced, [parent])

    def test_marker_directory_fsync_failure_prevents_booking(self):
        payload, runtime_config, runtime_plist = self._stage_and_deploy_fixture(
            output_name="marker-fsync-failure"
        )
        self._set_confirmable_opentable_environment()
        state_dir = Path(payload["runtime"]["state_dir"])
        state_dir.mkdir(parents=True)
        real_fsync_directory = restaurant_snipe.fsync_directory

        def fail_marker_sync(path):
            if Path(path) == state_dir and (state_dir / "booking-attempt.json").exists():
                raise OSError("simulated marker directory fsync failure")
            real_fsync_directory(path)

        with mock.patch.object(
            restaurant_snipe,
            "fsync_directory",
            side_effect=fail_marker_sync,
        ):
            result = restaurant_snipe.run_job(runtime_config, now=self.run_now)

        self.assertEqual(result["status"], "manual_review_required")
        self.assertEqual(result["reason"], "booking_marker_not_durable")
        self.assertFalse(self.book_log.exists())
        self.assertTrue((state_dir / "booking-attempt.json").exists())
        self.assertTrue(runtime_config.exists())
        self.assertTrue(runtime_plist.exists())

    def test_receipt_directory_fsync_failure_preserves_marker_and_stops_cleanup(self):
        payload, runtime_config, runtime_plist = self._stage_and_deploy_fixture(
            output_name="receipt-fsync-failure"
        )
        self._set_confirmable_opentable_environment()
        state_dir = Path(payload["runtime"]["state_dir"])
        state_dir.mkdir(parents=True)
        real_fsync_directory = restaurant_snipe.fsync_directory
        state_sync_count = 0

        def fail_second_state_sync(path):
            nonlocal state_sync_count
            if Path(path) == state_dir:
                state_sync_count += 1
                if state_sync_count == 2:
                    raise OSError("simulated receipt directory fsync failure")
            real_fsync_directory(path)

        with mock.patch.object(
            restaurant_snipe,
            "fsync_directory",
            side_effect=fail_second_state_sync,
        ):
            result = restaurant_snipe.run_job(runtime_config, now=self.run_now)

        self.assertEqual(result["status"], "manual_review_required")
        self.assertEqual(result["reason"], "receipt_not_durable")
        self.assertEqual(self.book_log.read_text(encoding="utf-8"), "book\n")
        self.assertTrue((state_dir / "booking-attempt.json").exists())
        self.assertTrue(runtime_config.exists())
        self.assertTrue(runtime_plist.exists())
        self.assertFalse(self.launchctl_log.exists())

    def test_marker_removal_fsync_failure_keeps_durable_receipt_and_reports_cleanup(self):
        payload, runtime_config, runtime_plist = self._stage_and_deploy_fixture(
            output_name="marker-removal-fsync-failure"
        )
        self._set_confirmable_opentable_environment()
        state_dir = Path(payload["runtime"]["state_dir"])
        state_dir.mkdir(parents=True)
        real_fsync_directory = restaurant_snipe.fsync_directory
        state_sync_count = 0

        def fail_third_state_sync(path):
            nonlocal state_sync_count
            if Path(path) == state_dir:
                state_sync_count += 1
                if state_sync_count == 3:
                    raise OSError("simulated marker removal directory fsync failure")
            real_fsync_directory(path)

        with mock.patch.object(
            restaurant_snipe,
            "fsync_directory",
            side_effect=fail_third_state_sync,
        ):
            result = restaurant_snipe.run_job(runtime_config, now=self.run_now)

        self.assertEqual(result["status"], "booking_confirmed")
        self.assertFalse(result["cleanup_complete"])
        self.assertEqual(self.book_log.read_text(encoding="utf-8"), "book\n")
        self.assertTrue((state_dir / "confirmed.json").exists())
        self.assertFalse((state_dir / "booking-attempt.json").exists())
        self.assertFalse(runtime_config.exists())
        self.assertFalse(runtime_plist.exists())

    def test_stage_writes_review_only_artifacts_and_cache_only_plist(self):
        result = restaurant_snipe.stage(self._stage_args())
        self.assertEqual(result["status"], "staged")
        self.assertFalse(result["deployed"])
        config_path = Path(result["authorization_path"])
        plist_path = Path(result["plist_path"])
        self.assertTrue(config_path.is_file())
        self.assertTrue(plist_path.is_file())
        self.assertEqual(config_path.stat().st_mode & 0o777, 0o600)
        self.assertEqual(plist_path.stat().st_mode & 0o777, 0o600)
        lint = subprocess.run(
            ["/usr/bin/plutil", "-lint", str(plist_path)],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(lint.returncode, 0, lint.stderr)

        plist = plistlib.loads(plist_path.read_bytes())
        self.assertEqual(plist["EnvironmentVariables"]["PATH"], "/usr/bin:/bin")
        rendered = plist_path.read_text(encoding="utf-8")
        self.assertNotIn("OP_SERVICE_ACCOUNT_TOKEN", rendered)
        self.assertNotIn(".env-token", rendered)
        self.assertNotIn("launchctl", rendered)
        self.assertNotIn("test-chat-target", rendered)
        self.assertFalse(Path(plist["ProgramArguments"][-1]).exists())

    def test_stage_rejects_existing_reservation_in_authorized_window(self):
        conflict_path = self.root / "conflict.json"
        conflict_path.write_text(
            json.dumps(
                {
                    "reservations": [
                        {
                            "platform": "opentable",
                            "venue_id": "venue-123",
                            "date": self.reservation_date,
                            "time": "19:15",
                            "party_size": 2,
                            "status": "confirmed",
                            "config_token": "MUST_NOT_BE_COPIED",
                        }
                    ]
                }
            ),
            encoding="utf-8",
        )
        with self.assertRaisesRegex(restaurant_snipe.SnipeError, "existing reservation conflicts"):
            restaurant_snipe.stage(self._stage_args(existing_path=conflict_path, output_name="conflict"))
        self.assertFalse((self.stage_root / "conflict").exists())

    def test_confirmed_booking_is_token_silent_idempotent_and_cleans_exact_artifacts(self):
        payload, runtime_config, runtime_plist = self._stage_and_deploy_fixture()
        self._set_confirmable_opentable_environment()

        result = restaurant_snipe.run_job(
            runtime_config,
            now=self.run_now,
        )
        self.assertEqual(result["status"], "booking_confirmed")
        self.assertTrue(result["cleanup_complete"])
        self.assertFalse(runtime_config.exists())
        self.assertFalse(runtime_plist.exists())
        self.assertFalse(self.op_sentinel.exists())
        self.assertEqual(self.book_log.read_text(encoding="utf-8"), "book\n")

        state_dir = Path(payload["runtime"]["state_dir"])
        receipt_text = (state_dir / "confirmed.json").read_text(encoding="utf-8")
        combined = json.dumps(result) + receipt_text + self.imsg_log.read_text(encoding="utf-8")
        self.assertNotIn("CONFIG_TOKEN_CANARY", combined)
        self.assertNotIn("SLOT_HASH_CANARY", combined)
        self.assertNotIn("BOOKING_TOKEN_CANARY", combined)
        self.assertIn(payload["runtime"]["label"], self.launchctl_log.read_text(encoding="utf-8"))

    def test_ambiguous_booking_blocks_retry_and_does_not_cleanup(self):
        payload, runtime_config, runtime_plist = self._stage_and_deploy_fixture(output_name="ambiguous")
        self._set_fake_environment()
        os.environ["FAKE_AVAILABILITY"] = json.dumps(
            {
                "suggestedAvailability": [
                    {
                        "timeslots": [
                            {
                                "available": True,
                                "dateTime": f"{self.reservation_date}T19:00:00-05:00",
                                "token": "AMBIGUOUS_CONFIG_CANARY",
                                "slotHash": "AMBIGUOUS_HASH_CANARY",
                            }
                        ]
                    }
                ]
            }
        )
        os.environ["FAKE_BOOK_RESULT"] = json.dumps(
            {"success": True, "token": "AMBIGUOUS_BOOKING_CANARY"}
        )
        now = self.run_now
        first = restaurant_snipe.run_job(runtime_config, now=now)
        second = restaurant_snipe.run_job(runtime_config, now=now)

        self.assertEqual(first["status"], "manual_review_required")
        self.assertEqual(second["status"], "manual_review_required")
        self.assertTrue(runtime_config.exists())
        self.assertTrue(runtime_plist.exists())
        self.assertFalse(self.launchctl_log.exists())
        self.assertEqual(self.book_log.read_text(encoding="utf-8"), "book\n")
        self.assertTrue(Path(payload["runtime"]["state_dir"], "booking-attempt.json").is_file())
        self.assertNotIn("CANARY", json.dumps(first) + json.dumps(second))

    def test_runtime_rejects_tampered_authorization_before_platform_access(self):
        _, runtime_config, runtime_plist = self._stage_and_deploy_fixture(output_name="tampered")
        payload = json.loads(runtime_config.read_text(encoding="utf-8"))
        payload["notification"]["target"] = "different-target"
        runtime_config.write_text(json.dumps(payload), encoding="utf-8")
        self._set_fake_environment()

        with self.assertRaisesRegex(restaurant_snipe.SnipeError, "authorization digest"):
            restaurant_snipe.run_job(
                runtime_config,
                now=self.run_now,
            )
        self.assertTrue(runtime_config.exists())
        self.assertTrue(runtime_plist.exists())
        self.assertFalse(self.book_log.exists())
        self.assertFalse(self.op_sentinel.exists())

    def test_expired_authorization_never_calls_platform_or_cleans_artifacts(self):
        _, runtime_config, runtime_plist = self._stage_and_deploy_fixture(output_name="expired")
        self._set_fake_environment()
        result = restaurant_snipe.run_job(
            runtime_config,
            now=self.expires_at + timedelta(seconds=1),
        )
        self.assertEqual(result["status"], "expired")
        self.assertFalse(result["cleanup_complete"])
        self.assertTrue(runtime_config.exists())
        self.assertTrue(runtime_plist.exists())
        self.assertFalse(self.book_log.exists())
        self.assertFalse(self.op_sentinel.exists())
        self.assertFalse(self.launchctl_log.exists())

    def test_resy_existing_reservation_prevents_booking_and_finishes_job(self):
        payload, runtime_config, runtime_plist = self._stage_and_deploy_fixture(
            platform="resy", output_name="existing-resy"
        )
        self._set_fake_environment()
        os.environ["FAKE_RESERVATIONS"] = json.dumps(
            [
                {
                    "venue": {"id": "venue-123"},
                    "day": self.reservation_date,
                    "time_slot": "19:00:00",
                    "num_seats": 2,
                    "status": {"finished": 0},
                    "resy_token": "EXISTING_TOKEN_CANARY",
                }
            ]
        )
        result = restaurant_snipe.run_job(
            runtime_config,
            now=self.run_now,
        )
        self.assertEqual(result["status"], "existing_reservation_confirmed")
        self.assertTrue(result["cleanup_complete"])
        self.assertFalse(runtime_config.exists())
        self.assertFalse(runtime_plist.exists())
        self.assertFalse(self.book_log.exists())
        self.assertFalse(self.op_sentinel.exists())
        receipt = Path(payload["runtime"]["state_dir"], "confirmed.json").read_text(encoding="utf-8")
        self.assertNotIn("EXISTING_TOKEN_CANARY", receipt + json.dumps(result))

    def test_repo_wrapper_resolves_skill_local_helper(self):
        environment = os.environ.copy()
        environment["OPENCLAW_SKILLS_DIR"] = str(REPO_ROOT / "openclaw/skills")
        environment["RESTAURANT_SNIPE_PYTHON"] = sys.executable
        environment["PYTHONDONTWRITEBYTECODE"] = "1"
        completed = subprocess.run(
            [str(REPO_ROOT / "openclaw/bin/restaurant-snipe"), "--help"],
            capture_output=True,
            text=True,
            check=False,
            env=environment,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertIn("stage", completed.stdout)
        self.assertIn("run", completed.stdout)

        rejected = subprocess.run(
            [str(REPO_ROOT / "openclaw/bin/restaurant-snipe"), "run", "--config", "/tmp/not-used"],
            capture_output=True,
            text=True,
            check=False,
            env=environment,
        )
        self.assertEqual(rejected.returncode, 64)
        self.assertIn("LaunchAgent-internal", rejected.stderr)


if __name__ == "__main__":
    unittest.main()
