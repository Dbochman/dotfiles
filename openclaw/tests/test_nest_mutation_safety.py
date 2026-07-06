#!/usr/bin/env python3
"""Fake-only transport and mutation verification tests for the Nest wrapper."""

from __future__ import annotations

import json
import os
from pathlib import Path
import re
import shlex
import stat
import subprocess
import tempfile
import unittest


REPO_ROOT = Path(__file__).resolve().parents[2]
NEST = REPO_ROOT / "openclaw" / "bin" / "nest"


FAKE_CURL = r'''#!/usr/bin/env python3
import json
import os
from pathlib import Path
import sys

args = sys.argv[1:]
url = next((arg for arg in args if arg.startswith("https://")), "")
method = args[args.index("-X") + 1] if "-X" in args else "GET"
body = args[args.index("-d") + 1] if "-d" in args else None
record = {"url": url, "method": method, "body": body, "args": args}
with open(os.environ["FAKE_CURL_LOG"], "a", encoding="utf-8") as handle:
    handle.write(json.dumps(record) + "\n")

mode = os.environ.get("FAKE_CURL_MODE", "ok")
if "oauth2/v4/token" in url:
    if mode == "oauth_error":
        print('{"error":"private oauth detail"}')
    else:
        print('{"access_token":"fake-access-token"}')
elif "api.open-meteo.com" in url:
    print(json.dumps({
        "current": {
            "temperature_2m": 50,
            "apparent_temperature": 48,
            "relative_humidity_2m": 40,
            "wind_speed_10m": 3,
            "wind_gusts_10m": 5,
            "weather_code": 0,
        }
    }))
elif url.endswith("/structures"):
    print(json.dumps({
        "structures": [{
            "name": "enterprises/fake-project/structures/s1",
            "traits": {"sdm.structures.traits.Info": {"customName": "Home"}},
        }]
    }))
elif url.endswith("/devices"):
    print(json.dumps({
        "devices": [{
            "name": "enterprises/fake-project/devices/dev1",
            "parentRelations": [{
                "displayName": "Bedroom",
                "parent": "enterprises/fake-project/structures/s1/rooms/r1",
            }],
            "traits": {},
        }]
    }))
elif url.endswith(":executeCommand"):
    if mode == "post_invalid":
        print("not-json")
    elif mode == "post_error":
        print('{"error":{"message":"private API detail"}}')
    elif mode == "post_fail":
        print('{"error":{"message":"private transport detail"}}')
        sys.exit(22)
    else:
        print("{}")
elif url.endswith("/devices/dev1"):
    sys.stdout.write(Path(os.environ["FAKE_DEVICE_RESPONSE_FILE"]).read_text())
else:
    sys.exit(93)
'''


class NestMutationSafetyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tempdir.cleanup)
        self.root = Path(self.tempdir.name)
        self.home = self.root / "home"
        self.fake_bin = self.root / "bin"
        self.cache = self.home / ".openclaw" / ".secrets-cache"
        self.location_file = self.home / ".openclaw" / "nest-location.conf"
        self.token_dir = self.root / "nest-cache"
        self.access_token = self.token_dir / "access_token"
        self.log = self.root / "curl.jsonl"
        self.device_response = self.root / "device.json"
        self.home.mkdir()
        self.fake_bin.mkdir()
        self.token_dir.mkdir(mode=0o700)
        self._write_executable(self.fake_bin / "curl", FAKE_CURL)
        self._write_assignments(
            self.cache,
            {
                "NEST_CLIENT_ID": "fake-client",
                "NEST_CLIENT_SECRET": "fake-secret",
                "NEST_REFRESH_TOKEN": "fake-refresh",
                "NEST_PROJECT_ID": "fake-project",
                "CROSSTOWN_LAT": "40.1234",
                "CROSSTOWN_LON": "-70.5678",
            },
        )
        self.access_token.write_text("fake-cached-access", encoding="utf-8")
        self.access_token.chmod(0o600)
        self.write_device_response({"traits": {}})

    @staticmethod
    def _write_executable(path: Path, content: str) -> None:
        path.write_text(content, encoding="utf-8")
        path.chmod(path.stat().st_mode | stat.S_IXUSR)

    @staticmethod
    def _write_assignments(path: Path, values: dict[str, str]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            "".join(f"{key}={shlex.quote(value)}\n" for key, value in values.items()),
            encoding="utf-8",
        )
        path.chmod(0o600)

    def write_device_response(self, payload: object) -> None:
        self.device_response.write_text(json.dumps(payload), encoding="utf-8")

    def environment(self, **overrides: str) -> dict[str, str]:
        env = os.environ.copy()
        env.update(
            {
                "HOME": str(self.home),
                "PATH": f"{self.fake_bin}:{env['PATH']}",
                "OPENCLAW_SECRETS_CACHE": str(self.cache),
                "NEST_CACHE_DIR": str(self.token_dir),
                "NEST_VERIFY_ATTEMPTS": "2",
                "NEST_VERIFY_DELAY_SECONDS": "0",
                "FAKE_CURL_LOG": str(self.log),
                "FAKE_DEVICE_RESPONSE_FILE": str(self.device_response),
            }
        )
        env.update(overrides)
        return env

    def run_nest(self, *args: str, **env: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["bash", str(NEST), *args],
            check=False,
            capture_output=True,
            text=True,
            stdin=subprocess.DEVNULL,
            env=self.environment(**env),
        )

    def records(self) -> list[dict]:
        if not self.log.exists():
            return []
        return [json.loads(line) for line in self.log.read_text().splitlines()]

    def test_set_prints_success_only_after_matching_readback(self) -> None:
        self.write_device_response(
            {
                "traits": {
                    "sdm.devices.traits.ThermostatTemperatureSetpoint": {
                        "heatCelsius": 22.2222
                    }
                }
            }
        )

        result = self.run_nest("set", "Bedroom", "72")

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Set Bedroom to 72°F", result.stdout)
        post = next(record for record in self.records() if record["method"] == "POST")
        payload = json.loads(post["body"])
        self.assertEqual(
            payload["command"],
            "sdm.devices.commands.ThermostatTemperatureSetpoint.SetHeat",
        )
        self.assertAlmostEqual(payload["params"]["heatCelsius"], 22.222222, places=5)
        readbacks = [
            record for record in self.records() if record["url"].endswith("/devices/dev1")
        ]
        self.assertEqual(len(readbacks), 1)

    def test_all_mutation_types_verify_their_own_postcondition(self) -> None:
        cases = (
            (
                ("mode", "Bedroom", "OFF"),
                {"sdm.devices.traits.ThermostatMode": {"mode": "OFF"}},
                "Set Bedroom mode to OFF",
            ),
            (
                ("eco", "Bedroom", "off"),
                {"sdm.devices.traits.ThermostatEco": {"mode": "OFF"}},
                "Set Bedroom eco to OFF",
            ),
        )
        for arguments, traits, success_text in cases:
            with self.subTest(arguments=arguments):
                self.log.unlink(missing_ok=True)
                self.write_device_response({"traits": traits})
                result = self.run_nest(*arguments)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn(success_text, result.stdout)
                self.assertEqual(
                    len(
                        [
                            record
                            for record in self.records()
                            if record["url"].endswith("/devices/dev1")
                        ]
                    ),
                    1,
                )

    def test_mismatched_readback_is_bounded_and_never_prints_success(self) -> None:
        self.write_device_response(
            {
                "traits": {
                    "sdm.devices.traits.ThermostatTemperatureSetpoint": {
                        "heatCelsius": 10
                    }
                }
            }
        )

        result = self.run_nest("set", "Bedroom", "72")

        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn("Set Bedroom", result.stdout)
        self.assertIn("could not be verified", result.stderr)
        readbacks = [
            record for record in self.records() if record["url"].endswith("/devices/dev1")
        ]
        self.assertEqual(len(readbacks), 2)

    def test_invalid_error_and_failed_post_responses_never_print_success(self) -> None:
        for mode in ("post_invalid", "post_error", "post_fail"):
            with self.subTest(mode=mode):
                self.log.unlink(missing_ok=True)
                result = self.run_nest(
                    "mode",
                    "Bedroom",
                    "OFF",
                    FAKE_CURL_MODE=mode,
                )
                self.assertNotEqual(result.returncode, 0)
                self.assertNotIn("Set Bedroom", result.stdout)
                self.assertNotIn("private", result.stdout + result.stderr)
                self.assertEqual(
                    [
                        record
                        for record in self.records()
                        if record["url"].endswith("/devices/dev1")
                    ],
                    [],
                )

    def test_malformed_private_cache_fails_explicitly_before_curl(self) -> None:
        self.cache.write_text("NEST_PROJECT_ID='unterminated\n", encoding="utf-8")
        self.cache.chmod(0o600)

        result = self.run_nest("raw")

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("secrets cache is malformed", result.stderr)
        self.assertEqual(self.records(), [])

    def test_weather_has_no_literal_location_and_uses_protected_cache(self) -> None:
        source = NEST.read_text(encoding="utf-8")
        coordinate_literals = re.findall(
            r"(?<![A-Za-z0-9_.])-?\d{1,3}\.\d{4,}(?![A-Za-z0-9_.])",
            source,
        )
        self.assertEqual(coordinate_literals, [])
        self.assertNotIn("DEFAULT_LAT", source)
        self.assertNotIn("DEFAULT_LON", source)

        result = self.run_nest("weather")

        self.assertEqual(result.returncode, 0, result.stderr)
        request = next(
            record
            for record in self.records()
            if "api.open-meteo.com" in record["url"]
        )
        self.assertIn("latitude=40.1234", request["url"])
        self.assertIn("longitude=-70.5678", request["url"])

    def test_snapshot_keeps_cielo_and_mysa_sources(self) -> None:
        self._write_executable(
            self.fake_bin / "cielo",
            """#!/bin/sh
printf '%s\n' '[{"deviceName":"Bedroom","deviceStatus":1,"latEnv":{"temp":76,"humidity":55},"latestAction":{"temp":"72","mode":"cool","power":"on"}}]'
""",
        )
        self._write_executable(
            self.fake_bin / "mysa",
            """#!/bin/sh
printf '%s\n' '{"devices":[{"name":"Cat Room","temp_f":71.5,"humidity":42,"setpoint_f":68,"duty_pct":15}]}'
""",
        )

        result = self.run_nest("snapshot")

        self.assertEqual(result.returncode, 0, result.stderr)
        history_files = list((self.home / ".openclaw" / "nest-history").glob("*.jsonl"))
        self.assertEqual(len(history_files), 1)
        snapshot = json.loads(history_files[0].read_text(encoding="utf-8"))
        rooms = {room["room"]: room for room in snapshot["rooms"]}
        self.assertEqual(rooms["19Crosstown Bedroom"]["source"], "cielo")
        self.assertEqual(rooms["19Crosstown Bedroom"]["temp_f"], 76.0)
        self.assertEqual(rooms["19Crosstown Cat Room"]["source"], "mysa")
        self.assertEqual(rooms["19Crosstown Cat Room"]["duty_pct"], 15.0)

    def test_secure_location_file_overrides_protected_cache(self) -> None:
        self._write_assignments(
            self.location_file,
            {"NEST_LAT": "41.4321", "NEST_LON": "-71.8765"},
        )

        result = self.run_nest("weather")

        self.assertEqual(result.returncode, 0, result.stderr)
        request = next(
            record
            for record in self.records()
            if "api.open-meteo.com" in record["url"]
        )
        self.assertIn("latitude=41.4321", request["url"])
        self.assertIn("longitude=-71.8765", request["url"])

    def test_insecure_location_file_is_rejected_before_curl(self) -> None:
        self._write_assignments(
            self.location_file,
            {"NEST_LAT": "41.4321", "NEST_LON": "-71.8765"},
        )
        self.location_file.chmod(0o644)

        result = self.run_nest("weather")

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("owner-only with mode 0600", result.stderr)
        self.assertEqual(self.records(), [])

    def test_invalid_location_coordinates_are_rejected_before_curl(self) -> None:
        for values in (
            {"NEST_LAT": "north", "NEST_LON": "-71.8765"},
            {"NEST_LAT": "", "NEST_LON": "-71.8765"},
        ):
            with self.subTest(values=values):
                self._write_assignments(self.location_file, values)
                self.log.unlink(missing_ok=True)

                result = self.run_nest("weather")

                self.assertNotEqual(result.returncode, 0)
                self.assertIn("invalid fallback coordinates", result.stderr)
                self.assertEqual(self.records(), [])

    def test_oauth_error_body_is_rejected_without_disclosure_or_cache_write(self) -> None:
        self.access_token.unlink()

        result = self.run_nest("raw", FAKE_CURL_MODE="oauth_error")

        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(self.access_token.exists())
        self.assertNotIn("private oauth detail", result.stdout + result.stderr)
        self.assertIn("Error refreshing Nest access token", result.stderr)

    def test_camera_room_is_data_not_python_source(self) -> None:
        marker = self.root / "interpolation-ran"
        room = (
            "x';__import__('pathlib').Path("
            + repr(str(marker))
            + ").write_text('owned');#"
        )

        result = self.run_nest("camera", "snap", room)

        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(marker.exists())
        source = NEST.read_text(encoding="utf-8")
        self.assertNotIn("query = '$room'", source)
        self.assertNotIn("hours = int('$hours')", source)
        self.assertNotRegex(source, r"python3 -c \"print\(round\(\$1")


if __name__ == "__main__":
    unittest.main()
