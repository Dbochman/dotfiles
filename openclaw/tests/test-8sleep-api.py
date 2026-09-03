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
        self.current_sets = {
            "dylan-user": "set-crosstown",
            "julia-user": "set-crosstown",
        }
        self.current_devices = {
            "dylan-user": {"id": "pod-crosstown", "side": "left"},
            "julia-user": {"id": "pod-crosstown", "side": "right"},
        }
        self.away_states = {
            "dylan-user": False,
            "julia-user": False,
        }
        self.current_device_put_delays = {
            "dylan-user": 0,
            "julia-user": 0,
        }
        self.current_device_put_counts = {
            "dylan-user": 0,
            "julia-user": 0,
        }
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
            user_id = path.split("/")[2]
            return {"setId": self.current_sets[user_id]}
        if path.endswith("/away-mode"):
            user_id = path.split("/")[1]
            return {"isAway": self.away_states[user_id]}
        self.fail(f"unexpected app API GET {path}")

    def fake_get(self, path, token_data=None):
        self.events.append(("get", path, None, None))
        if path.startswith("users/") and path.endswith("/current-device"):
            user_id = path.split("/")[1]
            return dict(self.current_devices[user_id])
        if path.startswith("devices/"):
            device_id = path.removeprefix("devices/").split("?", 1)[0]
            return {"result": dict(self.device_assignments[device_id])}
        self.fail(f"unexpected client API GET {path}")

    def move_user_assignment(self, user_id, target_device, side):
        other_device = (
            "pod-cabin" if target_device == "pod-crosstown" else "pod-crosstown"
        )
        field = f"{side}UserId"
        self.current_devices[user_id] = {"id": target_device, "side": side}
        self.device_assignments[target_device][field] = user_id
        target_away = self.device_assignments[target_device].get("awaySides")
        if isinstance(target_away, dict):
            target_away.pop(field, None)
        if self.device_assignments[other_device].get(field) == user_id:
            self.device_assignments[other_device][field] = None
        other_away = self.device_assignments[other_device].get("awaySides")
        if isinstance(other_away, dict):
            other_away[field] = user_id

    def fake_put(self, path, body, token_data=None, use_app_api=False):
        self.events.append(("put", path, body, use_app_api))
        if path.endswith("/current-set"):
            user_id = path.split("/")[2]
            self.current_sets[user_id] = body["setId"]
        elif path.endswith("/current-device"):
            user_id = path.split("/")[1]
            self.current_device_put_counts[user_id] += 1
            if (
                self.current_device_put_counts[user_id]
                > self.current_device_put_delays[user_id]
            ):
                self.move_user_assignment(
                    user_id, body["id"], body["side"]
                )
        elif path.endswith("/away-mode"):
            user_id = path.split("/")[1]
            self.away_states[user_id] = "start" in body["awayPeriod"]
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

    def test_overview_reports_both_pods_and_authoritative_split_routing(self):
        self.current_devices["julia-user"] = {
            "id": "pod-cabin",
            "side": "right",
        }

        def overview_get(path, token_data=None):
            if path == "devices/pod-crosstown":
                return {
                    "result": {
                        "sensorInfo": {
                            "skuName": "king",
                            "model": "Pod3",
                            "connected": True,
                        },
                        "hasWater": True,
                        "leftHeatingLevel": -26,
                        "leftTargetHeatingLevel": 0,
                        "leftNowHeating": False,
                        "rightHeatingLevel": -23,
                        "rightTargetHeatingLevel": 0,
                        "rightNowHeating": False,
                    }
                }
            if path == "devices/pod-cabin":
                return {
                    "result": {
                        "sensorInfo": {
                            "skuName": "king",
                            "model": "Pod5",
                            "connected": True,
                        },
                        "hasWater": True,
                        "leftHeatingLevel": -33,
                        "leftTargetHeatingLevel": 0,
                        "leftNowHeating": False,
                        "rightHeatingLevel": -31,
                        "rightTargetHeatingLevel": 0,
                        "rightNowHeating": False,
                    }
                }
            return self.fake_get(path, token_data)

        with self.mocked_api(get=overview_get):
            output = self.run_cli("overview")

        result = json.loads(output)
        self.assertTrue(result["ok"])
        self.assertEqual(set(result["locations"]), {"crosstown", "cabin"})
        self.assertEqual(
            result["locations"]["crosstown"]["sides"]["dylan"]["routingState"],
            "home",
        )
        self.assertEqual(
            result["locations"]["crosstown"]["sides"]["julia"]["routingState"],
            "away",
        )
        self.assertEqual(
            result["locations"]["cabin"]["sides"]["dylan"]["routingState"],
            "away",
        )
        self.assertEqual(
            result["locations"]["cabin"]["sides"]["julia"]["routingState"],
            "home",
        )
        self.assertEqual(
            result["locations"]["cabin"]["sides"]["julia"]["temperatureF"],
            74,
        )
        self.assertFalse(any(event[0] == "put" for event in self.events))
        self.assertNotIn("pod-crosstown", output)
        self.assertNotIn("pod-cabin", output)

    def test_overview_marks_both_pods_away_for_manually_away_user(self):
        self.away_states["dylan-user"] = True

        def overview_get(path, token_data=None):
            if path.startswith("devices/") and "?" not in path:
                return {"result": {"sensorInfo": {}, "hasWater": None}}
            return self.fake_get(path, token_data)

        with self.mocked_api(get=overview_get):
            result = json.loads(self.run_cli("overview"))

        self.assertEqual(
            result["locations"]["crosstown"]["sides"]["dylan"]["routingState"],
            "away",
        )
        self.assertEqual(
            result["locations"]["cabin"]["sides"]["dylan"]["routingState"],
            "away",
        )

    def test_same_location_home_is_idempotent_and_does_not_put(self):
        with self.mocked_api():
            output = self.run_cli(
                "--location", "crosstown", "home", "dylan"
            )

        self.assertFalse(any(event[0] == "put" for event in self.events))
        self.assertEqual(self.current_sets["dylan-user"], "set-crosstown")
        self.assertEqual(
            self.current_devices["dylan-user"],
            {"id": "pod-crosstown", "side": "left"},
        )
        self.assertFalse(self.away_states["dylan-user"])
        assignment_reads = [
            event[1]
            for event in self.events
            if event[0] == "get" and event[1].startswith("devices/")
        ]
        self.assertEqual(
            assignment_reads,
            [
                "devices/pod-crosstown?filter=leftUserId",
                "devices/pod-crosstown?filter=leftUserId",
            ],
        )
        current_device_reads = [
            event[1]
            for event in self.events
            if event[0] == "get"
            and event[1].endswith("/current-device")
        ]
        self.assertEqual(
            current_device_reads,
            [
                "users/dylan-user/current-device",
                "users/dylan-user/current-device",
            ],
        )

        result = json.loads(output)
        self.assertTrue(result["success"])
        self.assertEqual(result["location"], "crosstown")
        self.assertEqual(result["side"], "dylan")
        self.assertEqual(result["state"], "home")
        self.assertEqual(result["coverage"], "own")
        self.assertFalse(result["changed"])

    def test_relocation_uses_exact_device_route_and_ends_away(self):
        self.away_states["dylan-user"] = True
        with self.mocked_api():
            output = self.run_cli(
                "--location", "cabin", "home", "dylan"
            )

        current_set_path = "household/users/dylan-user/current-set"
        current_device_path = "users/dylan-user/current-device"
        away_path = "users/dylan-user/away-mode"
        puts = [event for event in self.events if event[0] == "put"]
        self.assertEqual(
            puts[0],
            (
                "put",
                current_set_path,
                {"setId": "set-cabin"},
                True,
            ),
        )
        self.assertEqual(
            puts[1],
            (
                "put",
                current_device_path,
                {"id": "pod-cabin", "side": "left"},
                False,
            ),
        )
        self.assertEqual(puts[2][:2], ("put", away_path))
        self.assertIn("end", puts[2][2]["awayPeriod"])
        self.assertTrue(puts[2][3])
        self.assertEqual(len(puts), 3)
        self.assertFalse(
            any(
                event[0] == "put"
                and event[1] == current_set_path
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
        self.assertEqual(
            assignment_paths,
            [
                "devices/pod-cabin?filter=leftUserId",
                "devices/pod-cabin?filter=leftUserId",
            ],
        )
        self.assertEqual(self.current_sets["dylan-user"], "set-cabin")
        self.assertEqual(
            self.current_devices["dylan-user"],
            {"id": "pod-cabin", "side": "left"},
        )
        self.assertFalse(self.away_states["dylan-user"])
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
        self.assertEqual(result["coverage"], "own")
        self.assertTrue(result["changed"])

    def test_same_set_stale_assignment_is_repaired_via_current_device(self):
        self.current_sets["dylan-user"] = "set-cabin"

        with self.mocked_api():
            output = self.run_cli(
                "--location", "cabin", "home", "dylan"
            )

        puts = [event for event in self.events if event[0] == "put"]
        self.assertEqual(
            puts,
            [
                (
                    "put",
                    "users/dylan-user/current-device",
                    {"id": "pod-cabin", "side": "left"},
                    False,
                )
            ],
        )
        self.assertEqual(self.current_sets["dylan-user"], "set-cabin")
        self.assertEqual(
            self.current_devices["dylan-user"],
            {"id": "pod-cabin", "side": "left"},
        )
        self.assertEqual(
            self.device_assignments["pod-cabin"]["leftUserId"],
            "dylan-user",
        )
        self.assertTrue(json.loads(output)["changed"])

    def test_omitted_unassigned_side_is_treated_as_available(self):
        assignment_reads = 0

        def omitted_assignment_get(path, token_data=None):
            nonlocal assignment_reads
            if path == "devices/pod-cabin?filter=leftUserId":
                assignment_reads += 1
                if assignment_reads == 1:
                    self.events.append(("get", path, None, None))
                    return {"result": {}}
            return self.fake_get(path, token_data)

        with self.mocked_api(get=omitted_assignment_get):
            output = self.run_cli(
                "--location", "cabin", "home", "dylan"
            )

        self.assertEqual(
            self.device_assignments["pod-cabin"]["leftUserId"],
            "dylan-user",
        )
        self.assertTrue(json.loads(output)["success"])

    def test_julia_relocation_uses_right_side_without_mutating_dylan(self):
        self.away_states["julia-user"] = True

        with self.mocked_api():
            output = self.run_cli(
                "--location", "cabin", "home", "julia"
            )

        puts = [event for event in self.events if event[0] == "put"]
        self.assertEqual(
            puts[0],
            (
                "put",
                "household/users/julia-user/current-set",
                {"setId": "set-cabin"},
                True,
            ),
        )
        self.assertEqual(
            puts[1],
            (
                "put",
                "users/julia-user/current-device",
                {"id": "pod-cabin", "side": "right"},
                False,
            ),
        )
        self.assertEqual(puts[2][1], "users/julia-user/away-mode")
        self.assertEqual(len(puts), 3)
        self.assertEqual(self.current_sets["julia-user"], "set-cabin")
        self.assertEqual(
            self.current_devices["julia-user"],
            {"id": "pod-cabin", "side": "right"},
        )
        self.assertFalse(self.away_states["julia-user"])
        self.assertEqual(self.current_sets["dylan-user"], "set-crosstown")
        self.assertEqual(
            self.current_devices["dylan-user"],
            {"id": "pod-crosstown", "side": "left"},
        )
        self.assertEqual(
            self.device_assignments["pod-crosstown"]["leftUserId"],
            "dylan-user",
        )
        self.assertEqual(
            self.device_assignments["pod-cabin"]["rightUserId"],
            "julia-user",
        )
        result = json.loads(output)
        self.assertTrue(result["success"])
        self.assertEqual(result["side"], "julia")
        self.assertTrue(result["changed"])

    def test_target_side_conflict_fails_before_any_mutation(self):
        self.device_assignments["pod-cabin"]["leftUserId"] = "other-user"
        original_sets = dict(self.current_sets)
        original_devices = {
            user_id: dict(current)
            for user_id, current in self.current_devices.items()
        }
        original_away = dict(self.away_states)

        with self.mocked_api():
            result = self.run_cli_error(
                "--location", "cabin", "home", "dylan"
            )

        self.assertFalse(any(event[0] == "put" for event in self.events))
        self.assertEqual(self.current_sets, original_sets)
        self.assertEqual(self.current_devices, original_devices)
        self.assertEqual(self.away_states, original_away)
        self.assertFalse(
            any(
                event[0] == "get"
                and event[1].endswith("/current-device")
                for event in self.events
            )
        )
        self.assertIn(
            "cabin left side is assigned to another user",
            result["message"],
        )

    def test_reunion_reclaims_static_side_from_verified_solo_resident(self):
        self.current_sets["julia-user"] = "set-cabin"
        self.current_devices["julia-user"] = {
            "id": "pod-cabin",
            "side": "right",
        }
        self.device_assignments["pod-cabin"]["leftUserId"] = "julia-user"
        self.device_assignments["pod-cabin"]["rightUserId"] = "julia-user"

        with self.mocked_api():
            output = self.run_cli(
                "--location", "cabin", "home", "dylan", "own"
            )

        self.assertEqual(
            self.device_assignments["pod-cabin"]["leftUserId"],
            "dylan-user",
        )
        self.assertEqual(
            self.device_assignments["pod-cabin"]["rightUserId"],
            "julia-user",
        )
        self.assertEqual(
            self.current_devices["julia-user"],
            {"id": "pod-cabin", "side": "right"},
        )
        result = json.loads(output)
        self.assertTrue(result["success"])
        self.assertEqual(result["coverage"], "own")
        self.assertTrue(result["changed"])

    def test_reunion_rejects_unverified_duplicate_owner(self):
        self.device_assignments["pod-cabin"]["leftUserId"] = "julia-user"
        self.device_assignments["pod-cabin"]["rightUserId"] = "julia-user"

        with self.mocked_api():
            result = self.run_cli_error(
                "--location", "cabin", "home", "dylan", "own"
            )

        self.assertFalse(any(event[0] == "put" for event in self.events))
        self.assertIn(
            "cabin left side is assigned to another user",
            result["message"],
        )

    def test_split_resident_claims_both_sides_and_returns_to_static_side(self):
        self.current_sets["julia-user"] = "set-cabin"
        self.current_devices["julia-user"] = {
            "id": "pod-cabin",
            "side": "right",
        }
        self.device_assignments["pod-cabin"]["rightUserId"] = "julia-user"
        self.device_assignments["pod-crosstown"]["rightUserId"] = "julia-user"

        with self.mocked_api():
            output = self.run_cli(
                "--location", "crosstown", "home", "dylan", "both"
            )

        device_puts = [
            event
            for event in self.events
            if event[0] == "put"
            and event[1] == "users/dylan-user/current-device"
        ]
        self.assertEqual(
            [event[2]["side"] for event in device_puts],
            ["right", "left"],
        )
        self.assertEqual(
            self.device_assignments["pod-crosstown"]["leftUserId"],
            "dylan-user",
        )
        self.assertEqual(
            self.device_assignments["pod-crosstown"]["rightUserId"],
            "dylan-user",
        )
        self.assertEqual(
            self.current_devices["dylan-user"],
            {"id": "pod-crosstown", "side": "left"},
        )
        self.assertEqual(
            self.current_devices["julia-user"],
            {"id": "pod-cabin", "side": "right"},
        )
        result = json.loads(output)
        self.assertEqual(result["coverage"], "both")
        self.assertTrue(result["changed"])

    def test_both_side_claim_requires_other_resident_at_other_home(self):
        with self.mocked_api():
            result = self.run_cli_error(
                "--location", "crosstown", "home", "dylan", "both"
            )

        self.assertFalse(any(event[0] == "put" for event in self.events))
        self.assertIn(
            "both-side coverage requires the other resident home at the other location",
            result["message"],
        )

    def test_delayed_current_device_readback_retries_exact_write(self):
        self.current_sets["dylan-user"] = "set-cabin"
        self.current_device_put_delays["dylan-user"] = 1

        with self.mocked_api():
            output = self.run_cli(
                "--location", "cabin", "home", "dylan"
            )

        device_puts = [
            event
            for event in self.events
            if event[0] == "put"
            and event[1] == "users/dylan-user/current-device"
        ]
        self.assertEqual(
            device_puts,
            [
                (
                    "put",
                    "users/dylan-user/current-device",
                    {"id": "pod-cabin", "side": "left"},
                    False,
                ),
                (
                    "put",
                    "users/dylan-user/current-device",
                    {"id": "pod-cabin", "side": "left"},
                    False,
                ),
            ],
        )
        self.assertFalse(
            any(
                event[0] == "put" and event[1].endswith("/current-set")
                for event in self.events
            )
        )
        self.assertEqual(
            self.current_devices["dylan-user"],
            {"id": "pod-cabin", "side": "left"},
        )
        self.assertTrue(json.loads(output)["changed"])

    def test_current_device_put_api_failure_is_reported(self):
        self.current_sets["dylan-user"] = "set-cabin"

        def failing_put(path, body, token_data=None, use_app_api=False):
            if path == "users/dylan-user/current-device":
                self.events.append(("put", path, body, use_app_api))
                return {"error": 503, "message": "temporarily unavailable"}
            return self.fake_put(path, body, token_data, use_app_api)

        with self.mocked_api(put=failing_put):
            result = self.run_cli_error(
                "--location", "cabin", "home", "dylan"
            )

        device_puts = [
            event
            for event in self.events
            if event[0] == "put"
            and event[1] == "users/dylan-user/current-device"
        ]
        self.assertEqual(len(device_puts), 3)
        self.assertFalse(
            any(
                event[0] == "put"
                and (
                    event[1].endswith("/current-set")
                    or event[1].endswith("/away-mode")
                )
                for event in self.events
            )
        )
        self.assertEqual(
            self.current_devices["dylan-user"],
            {"id": "pod-crosstown", "side": "left"},
        )
        self.assertIn(
            "assigning cabin left side failed: temporarily unavailable",
            result["message"],
        )

    def test_stale_away_sides_do_not_block_authoritative_success(self):
        self.current_sets["dylan-user"] = "set-cabin"
        self.current_devices["dylan-user"] = {
            "id": "pod-cabin",
            "side": "left",
        }
        self.device_assignments["pod-cabin"]["leftUserId"] = "dylan-user"
        self.device_assignments["pod-cabin"]["awaySides"] = {
            "leftUserId": "stale-user"
        }
        self.device_assignments["pod-crosstown"]["awaySides"] = {
            "leftUserId": "dylan-user"
        }

        with self.mocked_api():
            output = self.run_cli(
                "--location", "cabin", "home", "dylan"
            )

        self.assertFalse(any(event[0] == "put" for event in self.events))
        self.assertFalse(json.loads(output)["changed"])

    def test_missing_away_sides_do_not_block_authoritative_success(self):
        self.current_sets["dylan-user"] = "set-cabin"
        self.current_devices["dylan-user"] = {
            "id": "pod-cabin",
            "side": "left",
        }
        self.device_assignments["pod-cabin"]["leftUserId"] = "dylan-user"
        self.device_assignments["pod-cabin"].pop("awaySides")
        self.device_assignments["pod-crosstown"].pop("awaySides")

        with self.mocked_api():
            output = self.run_cli(
                "--location", "cabin", "home", "dylan"
            )

        self.assertFalse(any(event[0] == "put" for event in self.events))
        self.assertFalse(json.loads(output)["changed"])

    def test_ordinary_write_to_non_current_location_fails_without_put(self):
        with self.mocked_api():
            result = self.run_cli_error(
                "--location", "cabin", "temp", "dylan", "-20"
            )

        self.assertFalse(any(event[0] == "put" for event in self.events))
        self.assertEqual(self.current_sets["dylan-user"], "set-crosstown")
        self.assertIn("not this user's current Pod", result["message"])
        self.assertIn("home command first", result["message"])

    def test_ordinary_write_requires_exact_current_device_and_side(self):
        self.current_devices["dylan-user"] = {
            "id": "pod-cabin",
            "side": "left",
        }

        with self.mocked_api():
            result = self.run_cli_error(
                "--location", "crosstown", "temp", "dylan", "-20"
            )

        self.assertFalse(any(event[0] == "put" for event in self.events))
        self.assertEqual(self.current_sets["dylan-user"], "set-crosstown")
        self.assertIn("not this user's current Pod", result["message"])
        self.assertIn("home command first", result["message"])

    def test_ordinary_write_detects_current_device_drift_after_put(self):
        def drifting_put(path, body, token_data=None, use_app_api=False):
            result = self.fake_put(path, body, token_data, use_app_api)
            if path == "users/dylan-user/temperature":
                self.current_devices["dylan-user"] = {
                    "id": "pod-cabin",
                    "side": "left",
                }
            return result

        with self.mocked_api(put=drifting_put):
            result = self.run_cli_error(
                "--location", "crosstown", "temp", "dylan", "-20"
            )

        temperature_puts = [
            event
            for event in self.events
            if event[0] == "put"
            and event[1] == "users/dylan-user/temperature"
        ]
        self.assertEqual(len(temperature_puts), 1)
        self.assertIn("current Pod changed", result["message"])

    def test_temp_to_current_but_away_location_fails_without_put(self):
        self.away_states["dylan-user"] = True
        with self.mocked_api():
            result = self.run_cli_error(
                "--location", "crosstown", "temp", "dylan", "-20"
            )

        self.assertFalse(any(event[0] == "put" for event in self.events))
        self.assertEqual(self.current_sets["dylan-user"], "set-crosstown")
        self.assertIn("still away for this user", result["message"])
        self.assertIn("home command first", result["message"])

    def test_home_requires_an_explicit_location(self):
        with self.mocked_api():
            result = self.run_cli_error("home", "dylan")

        self.assertEqual(self.events, [])
        self.assertEqual(result["error"], "missing_location")

    def test_missing_assignment_proof_fails_closed(self):
        self.current_device_put_delays["dylan-user"] = 10
        with self.mocked_api():
            result = self.run_cli_error(
                "--location", "cabin", "home", "dylan"
            )

        current_set_puts = [
            event
            for event in self.events
            if event[0] == "put" and event[1].endswith("/current-set")
        ]
        current_device_puts = [
            event
            for event in self.events
            if event[0] == "put"
            and event[1] == "users/dylan-user/current-device"
        ]
        current_device_reads = [
            event
            for event in self.events
            if event[0] == "get"
            and event[1] == "users/dylan-user/current-device"
        ]
        self.assertEqual(len(current_set_puts), 1)
        self.assertEqual(len(current_device_puts), 3)
        self.assertGreaterEqual(len(current_device_reads), 4)
        self.assertEqual(self.current_sets["dylan-user"], "set-cabin")
        self.assertEqual(
            self.current_devices["dylan-user"],
            {"id": "pod-crosstown", "side": "left"},
        )
        self.assertIsNone(self.device_assignments["pod-cabin"]["leftUserId"])
        self.assertIn(
            "did not assign the requested Pod side",
            result["message"],
        )


if __name__ == "__main__":
    unittest.main()
