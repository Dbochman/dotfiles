import importlib.util
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / "openclaw" / "skills" / "midea-ac" / "scripts" / "midea_ac.py"


def load_module():
    spec = importlib.util.spec_from_file_location("midea_ac_for_test", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


midea_ac = load_module()


class MideaACTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.private = self.root / "midea-ac"
        self.private.mkdir(mode=0o700)
        self.config = self.private / "bindings.json"
        self.environment = patch.dict(
            os.environ,
            {"MIDEA_AC_CONFIG": str(self.config)},
            clear=False,
        )
        self.environment.start()
        self.binding = {
            "alias": "cabin-bedroom",
            "site": "cabin",
            "device_id": "123456789",
            "type": 0xAC,
            "protocol": 3,
            "model": "00000Q13",
            "subtype": 0,
            "token": "a" * 128,
            "key": "b" * 64,
        }

    def tearDown(self):
        self.environment.stop()
        self.temporary.cleanup()

    def write_config(self, binding=None):
        payload = {"schema_version": 1, "devices": [binding or self.binding]}
        self.config.write_text(json.dumps(payload), encoding="utf-8")
        self.config.chmod(0o600)

    def test_private_config_round_trip(self):
        midea_ac.write_config({"schema_version": 1, "devices": [self.binding]})
        self.assertEqual(midea_ac.load_config()["devices"], [self.binding])
        self.assertEqual(self.config.stat().st_mode & 0o777, 0o600)

    def test_config_rejects_unsafe_mode(self):
        self.write_config()
        self.config.chmod(0o644)
        with self.assertRaises(midea_ac.MideaACError) as caught:
            midea_ac.load_config()
        self.assertEqual(caught.exception.code, "config_unsafe")

    def test_config_rejects_cross_site_alias(self):
        binding = {**self.binding, "alias": "crosstown-bedroom"}
        self.write_config(binding)
        with self.assertRaises(midea_ac.MideaACError) as caught:
            midea_ac.load_config()
        self.assertEqual(caught.exception.code, "config_site_invalid")

    def test_resolve_binding_requires_exact_alias(self):
        config = {"schema_version": 1, "devices": [self.binding]}
        self.assertEqual(
            midea_ac.resolve_binding(config, "cabin-bedroom")["device_id"],
            "123456789",
        )
        with self.assertRaises(midea_ac.MideaACError) as caught:
            midea_ac.resolve_binding(config, "bedroom")
        self.assertEqual(caught.exception.code, "device_not_found")

    def test_status_projection_is_safe_and_fahrenheit(self):
        status = midea_ac.status_from_attributes(
            self.binding,
            {
                "power": True,
                "mode": 2,
                "target_temperature": 22.0,
                "indoor_temperature": 21.5,
                "outdoor_temperature": 30,
                "indoor_humidity": 45,
                "fan_speed": 102,
                "swing_vertical": True,
                "swing_horizontal": False,
                "error_code": 0,
                "realtime_power": 460.0,
                "current_energy_consumption": 1.2,
                "total_energy_consumption": 14.3,
            },
        )
        self.assertEqual(status["mode"], "cool")
        self.assertEqual(status["target_temperature_f"], 71.6)
        self.assertEqual(status["fan"], "auto")
        self.assertNotIn("device_id", status)
        self.assertNotIn("token", status)

    def test_control_values_and_verification(self):
        self.assertEqual(
            midea_ac.intended_value("temperature", 72),
            ("target_temperature", 22.0),
        )
        self.assertEqual(midea_ac.intended_value("mode", "heat"), ("mode", 4))
        self.assertTrue(
            midea_ac.value_matches(
                {"swing_vertical": True, "swing_horizontal": True},
                "swing",
                (True, True),
            )
        )
        self.assertTrue(midea_ac.value_matches({"fan_speed": 127}, "fan_speed", 102))

    def test_mapping_requires_every_discovered_candidate(self):
        with self.assertRaises(midea_ac.MideaACError) as caught:
            midea_ac.parse_maps(["device-1=cabin-bedroom"])
        self.assertEqual(caught.exception.code, "mapping_candidate_invalid")
        mappings = midea_ac.parse_maps(
            ["candidate-1=cabin-bedroom", "candidate-2=cabin-living-room"]
        )
        self.assertEqual(mappings["candidate-2"], "cabin-living-room")

    def test_discovery_unions_multiple_udp_windows(self):
        first = {1: {"device_id": 1}}
        second = {2: {"device_id": 2}}
        discover = unittest.mock.Mock(side_effect=[first, second, {}])
        with patch.object(midea_ac, "import_midea", return_value=(discover, None)):
            devices = midea_ac.discover_local(attempts=3)
        self.assertEqual([device["device_id"] for device in devices], [1, 2])
        self.assertEqual(discover.call_count, 3)

    def test_cli_rejects_temperature_outside_safe_range_before_network(self):
        with patch.object(midea_ac, "send_control") as send:
            code = midea_ac.main(["temperature", "cabin-bedroom", "55", "--json"])
        self.assertEqual(code, 1)
        send.assert_not_called()

    def test_operator_commands_require_separate_attended_wrapper(self):
        with patch.object(midea_ac, "inspect_cloud") as inspect:
            code = midea_ac.main(["operator-inspect", "--json"])
        self.assertEqual(code, 1)
        inspect.assert_not_called()

    def test_missing_config_has_stable_error(self):
        self.private.rmdir()
        with self.assertRaises(midea_ac.MideaACError) as caught:
            midea_ac.load_config()
        self.assertEqual(caught.exception.code, "config_missing")

    def test_enrollment_requires_expected_device_count(self):
        with self.assertRaises(SystemExit):
            midea_ac.parser().parse_args(
                ["operator-enroll", "--cloud", "SmartHome", "--site", "cabin"]
            )


if __name__ == "__main__":
    unittest.main()
