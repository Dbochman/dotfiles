#!/usr/bin/env python3

import contextlib
import importlib.util
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch


MODULE_PATH = (
    Path(__file__).resolve().parents[1]
    / "skills"
    / "8sleep"
    / "8sleep-api.py"
)

IMPORT_ENV = {
    "EIGHTSLEEP_CLIENT_ID": "test-client",
    "EIGHTSLEEP_CLIENT_SECRET": "test-secret",
    "EIGHTSLEEP_DYLAN_USER_ID": "dylan-user",
    "EIGHTSLEEP_JULIA_USER_ID": "julia-user",
    "EIGHTSLEEP_CROSSTOWN_DEVICE_ID": "pod-crosstown",
    "EIGHTSLEEP_CABIN_DEVICE_ID": "pod-cabin",
}


def load_module():
    spec = importlib.util.spec_from_file_location("openclaw_8sleep_api", MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    with patch.dict(os.environ, IMPORT_ENV, clear=False):
        spec.loader.exec_module(module)
    return module


eight_sleep = load_module()


class MultiPodHomeTests(unittest.TestCase):
    def setUp(self):
        self.token = {"access_token": "test-token", "userId": "dylan-user"}
        self.events = []
        self.current_set = "set-crosstown"
        self.away_state = False
        self.update_assignment_on_select = True
        self.household_sets = [
            {
                "setId": "set-crosstown",
                "devices": [{"deviceId": "pod-crosstown"}],
            },
            {
                "setId": "set-cabin",
                "devices": [{"deviceId": "pod-cabin"}],
            },
        ]
        self.device_assignments = {
            "pod-crosstown": {
                "leftUserId": "dylan-user",
                "rightUserId": "julia-user",
                "awaySides": {},
            },
            "pod-cabin": {
                "leftUserId": None,
                "rightUserId": None,
                "awaySides": {
                    "leftUserId": "dylan-user",
                    "rightUserId": "julia-user",
                },
            },
        }
        self.temp_dir = tempfile.TemporaryDirectory()
        self.original_config_dir = eight_sleep.CONFIG_DIR
        self.original_routing_lock = eight_sleep.ROUTING_LOCK_FILE
        eight_sleep.CONFIG_DIR = Path(self.temp_dir.name)
        eight_sleep.ROUTING_LOCK_FILE = eight_sleep.CONFIG_DIR / "routing.lock"

        # Keep the test independent of the importing process's environment.
        eight_sleep.LOCATIONS = {
            "crosstown": "pod-crosstown",
            "cabin": "pod-cabin",
        }

    def tearDown(self):
        eight_sleep.CONFIG_DIR = self.original_config_dir
        eight_sleep.ROUTING_LOCK_FILE = self.original_routing_lock
        self.temp_dir.cleanup()

    def fake_get_app(self, path, token_data=None):
        self.events.append(("get_app", path, None, None))
        if path.endswith("/summary"):
            return {"households": [{"sets": self.household_sets}]}
        if path.endswith("/current-set"):
            return {"setId": self.current_set}
        if path.endswith("/away-mode"):
            return {"isAway": self.away_state}
        self.fail(f"unexpected app API GET {path}")

    def fake_get(self, path, token_data=None):
        self.events.append(("get", path, None, None))
        if path.startswith("devices/"):
            device_id = path.removeprefix("devices/").split("?", 1)[0]
            return {"result": dict(self.device_assignments[device_id])}
        self.fail(f"unexpected client API GET {path}")

    def move_dylan_assignment(self, target_set):
        target_device = {
            "set-crosstown": "pod-crosstown",
            "set-cabin": "pod-cabin",
        }[target_set]
        other_device = (
            "pod-cabin" if target_device == "pod-crosstown" else "pod-crosstown"
        )
        self.device_assignments[target_device]["leftUserId"] = "dylan-user"
        self.device_assignments[target_device]["awaySides"].pop("leftUserId", None)
        self.device_assignments[other_device]["leftUserId"] = None
        self.device_assignments[other_device]["awaySides"]["leftUserId"] = (
            "dylan-user"
        )

    def fake_put(self, path, body, token_data=None, use_app_api=False):
        self.events.append(("put", path, body, use_app_api))
        if path.endswith("/current-set"):
            self.current_set = body["setId"]
            if self.update_assignment_on_select:
                self.move_dylan_assignment(self.current_set)
        elif path.endswith("/away-mode"):
            self.away_state = "start" in body["awayPeriod"]
        return {"success": True}

    @contextlib.contextmanager
    def mocked_api(self, put=None, get_app=None, get=None):
        with (
            patch.object(eight_sleep, "get_token", return_value=self.token),
            patch.object(
                eight_sleep,
                "api_get_app",
                side_effect=get_app or self.fake_get_app,
            ),
            patch.object(
                eight_sleep,
                "api_put",
                side_effect=put or self.fake_put,
            ),
            patch.object(
                eight_sleep,
                "api_get",
                side_effect=get or self.fake_get,
            ),
            patch.object(eight_sleep.time, "sleep", return_value=None),
        ):
            yield

    def run_cli(self, *args):
        output = io.StringIO()
        argv = [str(MODULE_PATH), *args]
        with patch.object(sys, "argv", argv), contextlib.redirect_stdout(output):
            eight_sleep.main()
        return output.getvalue()

    def run_cli_error(self, *args):
        output = io.StringIO()
        argv = [str(MODULE_PATH), *args]
        with patch.object(sys, "argv", argv), contextlib.redirect_stdout(output):
            with self.assertRaises(SystemExit) as raised:
                eight_sleep.main()
        self.assertNotEqual(raised.exception.code, 0)
        result = json.loads(output.getvalue())
        self.assertFalse(result.get("success", False))
        self.assertIn("error", result)
        return result

    def test_require_api_success_rejects_explicit_false(self):
        with self.assertRaises(eight_sleep.APICommandError) as raised:
            eight_sleep.require_api_success(
                {"success": False, "message": "explicit rejection"},
                "test operation",
            )

        self.assertIn("test operation failed", str(raised.exception))
        self.assertIn("explicit rejection", str(raised.exception))

    def test_same_location_home_is_idempotent_and_does_not_put(self):
        with self.mocked_api():
            output = self.run_cli(
                "--location", "crosstown", "home", "dylan"
            )

        self.assertFalse(any(event[0] == "put" for event in self.events))
        self.assertEqual(self.current_set, "set-crosstown")
        self.assertFalse(self.away_state)
        assignment_reads = [
            event for event in self.events if event[0] == "get" and event[1].startswith("devices/")
        ]
        self.assertEqual(len(assignment_reads), 2)

        result = json.loads(output)
        self.assertTrue(result["success"])
        self.assertEqual(result["location"], "crosstown")
        self.assertEqual(result["side"], "dylan")
        self.assertEqual(result["state"], "home")
        self.assertFalse(result["changed"])

    def test_relocation_selects_target_ends_away_and_proves_assignment(self):
        self.away_state = True
        with self.mocked_api():
            output = self.run_cli(
                "--location", "cabin", "home", "dylan"
            )

        current_path = "household/users/dylan-user/current-set"
        away_path = "users/dylan-user/away-mode"
        puts = [event for event in self.events if event[0] == "put"]
        self.assertEqual(
            puts[0][:3],
            ("put", current_path, {"setId": "set-cabin"}),
        )
        self.assertEqual(puts[1][:2], ("put", away_path))
        self.assertIn("end", puts[1][2]["awayPeriod"])
        self.assertEqual(len(puts), 2)
        self.assertFalse(
            any(
                event[0] == "put"
                and event[1] == current_path
                and event[2] == {"setId": "set-crosstown"}
                for event in self.events
            ),
            "home relocation must persist instead of restoring the old set",
        )
        assignment_paths = [
            event[1]
            for event in self.events
            if event[0] == "get" and event[1].startswith("devices/")
        ]
        self.assertTrue(any(path.startswith("devices/pod-cabin?") for path in assignment_paths))
        self.assertTrue(any(path.startswith("devices/pod-crosstown?") for path in assignment_paths))
        self.assertEqual(self.current_set, "set-cabin")
        self.assertFalse(self.away_state)
        self.assertEqual(
            self.device_assignments["pod-cabin"]["leftUserId"],
            "dylan-user",
        )
        self.assertIsNone(self.device_assignments["pod-crosstown"]["leftUserId"])
        self.assertEqual(
            self.device_assignments["pod-crosstown"]["awaySides"]["leftUserId"],
            "dylan-user",
        )

        result = json.loads(output)
        self.assertTrue(result["success"])
        self.assertEqual(result["state"], "home")
        self.assertTrue(result["changed"])

    def test_ordinary_write_to_non_current_location_fails_without_put(self):
        with self.mocked_api():
            result = self.run_cli_error(
                "--location", "cabin", "temp", "dylan", "-20"
            )

        self.assertFalse(any(event[0] == "put" for event in self.events))
        self.assertEqual(self.current_set, "set-crosstown")
        self.assertIn("not this user's current Pod", result["message"])
        self.assertIn("home command first", result["message"])

    def test_temp_to_current_but_away_location_fails_without_put(self):
        self.away_state = True
        with self.mocked_api():
            result = self.run_cli_error(
                "--location", "crosstown", "temp", "dylan", "-20"
            )

        self.assertFalse(any(event[0] == "put" for event in self.events))
        self.assertEqual(self.current_set, "set-crosstown")
        self.assertIn("still away for this user", result["message"])
        self.assertIn("home command first", result["message"])

    def test_home_requires_an_explicit_location(self):
        with self.mocked_api():
            result = self.run_cli_error("home", "dylan")

        self.assertEqual(self.events, [])
        self.assertEqual(result["error"], "missing_location")

    def test_missing_assignment_proof_fails_closed(self):
        self.update_assignment_on_select = False
        with self.mocked_api():
            result = self.run_cli_error(
                "--location", "cabin", "home", "dylan"
            )

        current_set_puts = [
            event
            for event in self.events
            if event[0] == "put" and event[1].endswith("/current-set")
        ]
        assignment_reads = [
            event
            for event in self.events
            if event[0] == "get" and event[1].startswith("devices/")
        ]
        self.assertGreaterEqual(len(current_set_puts), 2)
        self.assertGreaterEqual(len(assignment_reads), 4)
        self.assertEqual(self.current_set, "set-cabin")
        self.assertIsNone(self.device_assignments["pod-cabin"]["leftUserId"])
        self.assertIn("did not move left assignment to cabin", result["message"])


if __name__ == "__main__":
    unittest.main()
