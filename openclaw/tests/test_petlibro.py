#!/usr/bin/env python3
"""Safety and failure-contract tests for the Petlibro skill."""

from __future__ import annotations

from contextlib import redirect_stdout
import importlib.util
from io import StringIO
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch
import urllib.error


REPO_ROOT = Path(__file__).resolve().parents[2]
SKILL_DIR = REPO_ROOT / "openclaw" / "skills" / "petlibro"
API_PATH = SKILL_DIR / "petlibro-api.py"
CLI_PATH = SKILL_DIR / "petlibro"
HOME_DASHBOARD_PATH = REPO_ROOT / "openclaw" / "bin" / "home-dashboard.py"


def load_api_module():
    spec = importlib.util.spec_from_file_location("petlibro_api", API_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Could not load {API_PATH}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def load_home_dashboard(temp_home: Path):
    spec = importlib.util.spec_from_file_location(
        "petlibro_home_dashboard_test", HOME_DASHBOARD_PATH
    )
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Could not load {HOME_DASHBOARD_PATH}")
    module = importlib.util.module_from_spec(spec)
    with patch.dict(os.environ, {"HOME": str(temp_home)}):
        spec.loader.exec_module(module)
    return module


petlibro_api = load_api_module()


class FakeResponse:
    def __init__(self, payload: object, *, encoded: bool = False) -> None:
        self.payload = payload if encoded else json.dumps(payload).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, *_args) -> None:
        return None

    def read(self) -> bytes:
        return self.payload


class PetlibroTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tempdir.cleanup)
        self.root = Path(self.tempdir.name)
        self.config_dir = self.root / "home" / ".config" / "petlibro"
        self.config_file = self.config_dir / "config.yaml"
        self.token_file = self.config_dir / "token-cache.json"
        self.state_dir = self.root / "home" / ".cache" / "petlibro"
        self.state_file = self.state_dir / "feed-state.json"
        self.lock_file = self.state_dir / ".feed.lock"
        self.schedule_state_file = self.state_dir / "schedule-state.json"
        self.schedule_lock_file = self.state_dir / ".schedule.lock"
        self.config_dir.mkdir(parents=True, mode=0o700)
        self.config_file.write_text(
            "\n".join(
                (
                    "email: test@example.invalid",
                    "password: fake-password",
                    "device_crosstown_feeder: Cross Feeder",
                    "device_crosstown_fountain: Cross Fountain",
                    "device_cabin_feeder: Cabin Feeder",
                    "device_cabin_fountain: Cabin Fountain",
                )
            )
            + "\n",
            encoding="utf-8",
        )
        self.config_file.chmod(0o600)

        patches = (
            patch.object(petlibro_api, "CONFIG_DIR", self.config_dir),
            patch.object(petlibro_api, "CONFIG_FILE", self.config_file),
            patch.object(petlibro_api, "TOKEN_FILE", self.token_file),
            patch.object(petlibro_api, "FEED_STATE_DIR", self.state_dir),
            patch.object(petlibro_api, "FEED_STATE_FILE", self.state_file),
            patch.object(petlibro_api, "FEED_LOCK_FILE", self.lock_file),
            patch.object(petlibro_api, "SCHEDULE_STATE_FILE", self.schedule_state_file),
            patch.object(petlibro_api, "SCHEDULE_LOCK_FILE", self.schedule_lock_file),
            patch.dict(
                os.environ,
                {
                    "PETLIBRO_APPSN": "fake-app-sn",
                    "PETLIBRO_MIN_PORTIONS": "1",
                    "PETLIBRO_MAX_PORTIONS": "3",
                    "PETLIBRO_FEED_COOLDOWN_SECONDS": "300",
                },
            ),
        )
        for active_patch in patches:
            active_patch.start()
            self.addCleanup(active_patch.stop)
        network_guard = patch.object(
            petlibro_api.urllib.request,
            "urlopen",
            side_effect=AssertionError("unexpected live Petlibro request"),
        )
        network_guard.start()
        self.addCleanup(network_guard.stop)

        self.devices = [
            {
                "name": "Cabin Feeder",
                "productName": "Granary Smart Feeder",
                "productIdentifier": "PLAF103",
                "deviceSn": "CABIN-FEEDER-SN",
                "online": False,
            },
            {
                "name": "Cross Feeder",
                "productName": "Granary Smart Feeder",
                "productIdentifier": "PLAF103",
                "deviceSn": "CROSS-FEEDER-SN",
                "online": True,
                "warehouseSurplusGrain": "enough",
            },
            {
                "name": "Cross Fountain",
                "productName": "Dockstream Smart Fountain",
                "productIdentifier": "PLWF116",
                "deviceSn": "CROSS-FOUNTAIN-SN",
                "online": True,
            },
        ]
        self.write_token("cached-token")

    def write_token(self, token: str, *, cached_at: float | None = None) -> None:
        self.token_file.write_text(
            json.dumps(
                {
                    "token": token,
                    "cached_at": time.time() if cached_at is None else cached_at,
                }
            ),
            encoding="utf-8",
        )
        self.token_file.chmod(0o600)

    @staticmethod
    def run_main(args: list[str]) -> tuple[int, dict]:
        output = StringIO()
        with redirect_stdout(output):
            return_code = petlibro_api.main(args)
        lines = output.getvalue().splitlines()
        if len(lines) != 1:
            raise AssertionError(f"Expected one JSON line, got {output.getvalue()!r}")
        return return_code, json.loads(lines[0])

    def device_list_response(self) -> FakeResponse:
        return FakeResponse({"code": 0, "data": self.devices})

    @staticmethod
    def schedule_state_response(enabled: bool) -> FakeResponse:
        return FakeResponse({"code": 0, "data": {"enableFeedingPlan": enabled}})

    def test_missing_environment_and_config_are_structured_without_traceback(self) -> None:
        self.token_file.unlink()
        with patch.dict(os.environ, {}, clear=True):
            code, payload = self.run_main(["devices"])
        self.assertEqual(code, 1)
        self.assertEqual(payload["error"], "environment_missing")

        self.config_file.unlink()
        code, payload = self.run_main(["devices"])
        self.assertEqual(code, 1)
        self.assertEqual(payload["error"], "config_missing")

    def test_config_must_be_owner_only_regular_mode_0600(self) -> None:
        self.config_file.chmod(0o644)
        with self.assertRaises(petlibro_api.PetlibroError) as insecure:
            petlibro_api.load_config()
        self.assertEqual(insecure.exception.code, "config_unsafe")

        self.config_file.chmod(0o600)
        with patch.object(petlibro_api.os, "getuid", return_value=os.getuid() + 1):
            with self.assertRaises(petlibro_api.PetlibroError) as wrong_owner:
                petlibro_api.load_config()
        self.assertEqual(wrong_owner.exception.code, "config_unsafe")

        target = self.root / "petlibro-config-target"
        target.write_text(self.config_file.read_text(encoding="utf-8"), encoding="utf-8")
        target.chmod(0o600)
        self.config_file.unlink()
        self.config_file.symlink_to(target)
        with self.assertRaises(petlibro_api.PetlibroError) as symlinked:
            petlibro_api.load_config()
        self.assertEqual(symlinked.exception.code, "config_unsafe")

    def test_transport_json_and_api_errors_are_structured_and_nonzero(self) -> None:
        failures = (
            (
                urllib.error.HTTPError(
                    "https://api.us.petlibro.com/test", 503, "unavailable", {}, None
                ),
                "http_error",
            ),
            (urllib.error.URLError("offline"), "network_error"),
            (FakeResponse(b"not-json", encoded=True), "invalid_response"),
            (FakeResponse({"code": 42, "message": "private detail"}), "api_error"),
        )
        for response, expected_error in failures:
            with self.subTest(expected_error=expected_error):
                with patch.object(
                    petlibro_api.urllib.request,
                    "urlopen",
                    side_effect=response if isinstance(response, Exception) else None,
                    return_value=None if isinstance(response, Exception) else response,
                ):
                    code, payload = self.run_main(["devices"])
                self.assertEqual(code, 1)
                self.assertEqual(payload["error"], expected_error)
                self.assertNotIn("private detail", json.dumps(payload))

    def test_failed_auth_preserves_existing_token_cache(self) -> None:
        original = b'{"token":"old-token","cached_at":0}'
        self.token_file.write_bytes(original)
        self.token_file.chmod(0o600)
        with patch.object(
            petlibro_api.urllib.request,
            "urlopen",
            return_value=FakeResponse({"code": 7, "message": "bad credentials"}),
        ):
            code, payload = self.run_main(["devices"])

        self.assertEqual(code, 1)
        self.assertEqual(payload["error"], "auth_failed")
        self.assertEqual(self.token_file.read_bytes(), original)

    def test_successful_auth_atomically_replaces_cache_with_mode_0600(self) -> None:
        self.write_token("stale-token", cached_at=0)
        responses = [
            FakeResponse({"code": 0, "data": {"token": "new-token"}}),
            self.device_list_response(),
        ]
        with patch.object(
            petlibro_api.urllib.request,
            "urlopen",
            side_effect=responses,
        ):
            code, payload = self.run_main(["devices"])

        self.assertEqual(code, 0)
        self.assertIsInstance(payload, list)
        cached = json.loads(self.token_file.read_text(encoding="utf-8"))
        self.assertEqual(cached["token"], "new-token")
        self.assertEqual(stat.S_IMODE(self.token_file.stat().st_mode), 0o600)
        self.assertEqual(list(self.config_dir.glob(".token-cache.json.*")), [])

    def test_exact_selector_chooses_location_mapping_not_first_feeder(self) -> None:
        responses = [
            self.device_list_response(),
            FakeResponse({"code": 0}),
        ]
        with patch.object(
            petlibro_api.urllib.request,
            "urlopen",
            side_effect=responses,
        ) as urlopen:
            code, payload = self.run_main(["feed", "crosstown-feeder", "2"])

        self.assertEqual(code, 0)
        self.assertEqual(payload["device"], "crosstown-feeder")
        self.assertEqual(payload["location"], "crosstown")
        manual_request = urlopen.call_args_list[1].args[0]
        request_body = json.loads(manual_request.data.decode("utf-8"))
        self.assertEqual(request_body["deviceSn"], "CROSS-FEEDER-SN")
        self.assertEqual(request_body["grainNum"], 2)

    def test_fuzzy_offline_and_non_feeder_targets_are_rejected(self) -> None:
        config = petlibro_api.load_config()
        cases = (
            ("feeder", "feeder", "invalid_device_selector"),
            ("cabin-feeder", "feeder", "device_offline"),
            ("crosstown-fountain", "feeder", "wrong_device_type"),
        )
        for selector, kind, expected_error in cases:
            with self.subTest(selector=selector):
                with self.assertRaises(petlibro_api.PetlibroError) as caught:
                    petlibro_api.resolve_device(config, self.devices, selector, kind)
                self.assertEqual(caught.exception.code, expected_error)

    def test_missing_or_duplicate_mapping_fails_closed(self) -> None:
        config = petlibro_api.load_config()
        config.pop("device_crosstown_feeder")
        with self.assertRaises(petlibro_api.PetlibroError) as missing:
            petlibro_api.resolve_device(config, self.devices, "crosstown-feeder", "feeder")
        self.assertEqual(missing.exception.code, "device_mapping_missing")

        duplicate = dict(self.devices[1])
        self.devices.append(duplicate)
        config["device_crosstown_feeder"] = "Cross Feeder"
        with self.assertRaises(petlibro_api.PetlibroError) as ambiguous:
            petlibro_api.resolve_device(config, self.devices, "crosstown-feeder", "feeder")
        self.assertEqual(ambiguous.exception.code, "device_ambiguous")

    def test_portions_are_bounded_before_any_network_call(self) -> None:
        for value in ("0", "4", "1.5", "lots"):
            with self.subTest(value=value):
                with patch.object(petlibro_api.urllib.request, "urlopen") as urlopen:
                    code, payload = self.run_main(["feed", "crosstown-feeder", value])
                self.assertEqual(code, 1)
                self.assertEqual(payload["error"], "invalid_portions")
                urlopen.assert_not_called()

    def test_status_includes_verified_schedule_state_for_online_mapped_feeders(self) -> None:
        with patch.object(
            petlibro_api.urllib.request,
            "urlopen",
            side_effect=[self.device_list_response(), self.schedule_state_response(False)],
        ) as urlopen:
            code, payload = self.run_main(["status"])

        self.assertEqual(code, 0)
        feeders = {item["selector"]: item for item in payload if item["type"] == "feeder"}
        self.assertIsNone(feeders["cabin-feeder"]["scheduleEnabled"])
        self.assertEqual(feeders["cabin-feeder"]["scheduleState"], "unavailable")
        self.assertFalse(feeders["crosstown-feeder"]["scheduleEnabled"])
        self.assertEqual(feeders["crosstown-feeder"]["scheduleState"], "disabled")
        state_request = urlopen.call_args_list[1].args[0]
        self.assertTrue(state_request.full_url.endswith("/device/device/baseInfo"))
        self.assertEqual(
            json.loads(state_request.data.decode("utf-8")),
            {"deviceSn": "CROSS-FEEDER-SN", "id": "CROSS-FEEDER-SN"},
        )

    def test_schedule_set_is_exact_durable_and_verified(self) -> None:
        responses = [
            self.device_list_response(),
            self.schedule_state_response(True),
            FakeResponse({"code": 0}),
            self.schedule_state_response(False),
        ]
        with patch.object(
            petlibro_api.urllib.request,
            "urlopen",
            side_effect=responses,
        ) as urlopen:
            code, payload = self.run_main(["schedule-set", "crosstown-feeder", "off"])

        self.assertEqual(code, 0)
        self.assertEqual(payload["device"], "crosstown-feeder")
        self.assertFalse(payload["scheduleEnabled"])
        self.assertTrue(payload["verified"])
        self.assertTrue(payload["mutation_attempted"])
        mutation_request = urlopen.call_args_list[2].args[0]
        self.assertTrue(
            mutation_request.full_url.endswith("/device/setting/updateFeedingPlanSwitch")
        )
        self.assertEqual(
            json.loads(mutation_request.data.decode("utf-8")),
            {"deviceSn": "CROSS-FEEDER-SN", "enable": False},
        )
        audit = json.loads(self.schedule_state_file.read_text(encoding="utf-8"))
        self.assertEqual(audit["selector"], "crosstown-feeder")
        self.assertFalse(audit["requested_enabled"])
        self.assertFalse(audit["observed_enabled"])
        self.assertEqual(audit["status"], "verified")
        self.assertNotIn("CROSS-FEEDER-SN", self.schedule_state_file.read_text(encoding="utf-8"))
        self.assertEqual(stat.S_IMODE(self.schedule_state_file.stat().st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(self.schedule_lock_file.stat().st_mode), 0o600)

    def test_schedule_set_same_state_is_verified_without_mutation(self) -> None:
        with patch.object(
            petlibro_api.urllib.request,
            "urlopen",
            side_effect=[self.device_list_response(), self.schedule_state_response(False)],
        ) as urlopen:
            code, payload = self.run_main(["schedule-set", "crosstown-feeder", "off"])

        self.assertEqual(code, 0)
        self.assertFalse(payload["mutation_attempted"])
        self.assertTrue(payload["verified"])
        self.assertEqual(urlopen.call_count, 2)
        self.assertFalse(self.schedule_state_file.exists())

    def test_schedule_state_is_validated_before_network(self) -> None:
        for state_value in ("pause", "OFF", "1"):
            with self.subTest(state=state_value), patch.object(
                petlibro_api.urllib.request, "urlopen"
            ) as urlopen:
                code, payload = self.run_main(
                    ["schedule-set", "crosstown-feeder", state_value]
                )
            self.assertEqual(code, 1)
            self.assertEqual(payload["error"], "invalid_schedule_state")
            urlopen.assert_not_called()

    def test_ambiguous_schedule_mutation_is_recorded_and_not_retried(self) -> None:
        responses = [
            self.device_list_response(),
            self.schedule_state_response(True),
            urllib.error.URLError("lost"),
        ]
        with patch.object(
            petlibro_api.urllib.request,
            "urlopen",
            side_effect=responses,
        ) as urlopen:
            code, payload = self.run_main(["schedule-set", "crosstown-feeder", "off"])

        self.assertEqual(code, 1)
        self.assertEqual(payload["error"], "schedule_outcome_unknown")
        self.assertTrue(payload["non_retryable"])
        self.assertTrue(payload["schedule_may_have_changed"])
        self.assertEqual(urlopen.call_count, 3)
        audit = json.loads(self.schedule_state_file.read_text(encoding="utf-8"))
        self.assertEqual(audit["status"], "unknown")

    def test_unverified_schedule_mutation_is_not_repeated(self) -> None:
        responses = [
            self.device_list_response(),
            self.schedule_state_response(True),
            FakeResponse({"code": 0}),
            self.schedule_state_response(True),
            self.schedule_state_response(True),
            self.schedule_state_response(True),
        ]
        with patch.object(
            petlibro_api.urllib.request,
            "urlopen",
            side_effect=responses,
        ) as urlopen, patch.object(petlibro_api.time, "sleep"):
            code, payload = self.run_main(["schedule-set", "crosstown-feeder", "off"])

        self.assertEqual(code, 1)
        self.assertEqual(payload["error"], "schedule_outcome_unknown")
        mutation_calls = [
            call
            for call in urlopen.call_args_list
            if call.args[0].full_url.endswith("/device/setting/updateFeedingPlanSwitch")
        ]
        self.assertEqual(len(mutation_calls), 1)
        audit = json.loads(self.schedule_state_file.read_text(encoding="utf-8"))
        self.assertEqual(audit["status"], "unknown")
        self.assertTrue(audit["observed_enabled"])

    def test_manual_feed_cooldown_blocks_duplicate_before_second_feed_call(self) -> None:
        first_responses = [self.device_list_response(), FakeResponse({"code": 0})]
        with patch.object(
            petlibro_api.urllib.request,
            "urlopen",
            side_effect=first_responses,
        ):
            first_code, first_payload = self.run_main(
                ["feed", "crosstown-feeder", "1"]
            )
        self.assertEqual(first_code, 0)
        self.assertTrue(first_payload["request_id"])

        with patch.object(
            petlibro_api.urllib.request,
            "urlopen",
            return_value=self.device_list_response(),
        ) as second_urlopen:
            second_code, second_payload = self.run_main(
                ["feed", "crosstown-feeder", "1"]
            )

        self.assertEqual(second_code, 1)
        self.assertEqual(second_payload["error"], "feed_cooldown")
        self.assertTrue(second_payload["non_retryable"])
        self.assertEqual(second_urlopen.call_count, 1)
        self.assertEqual(stat.S_IMODE(self.state_dir.stat().st_mode), 0o700)
        self.assertEqual(stat.S_IMODE(self.state_file.stat().st_mode), 0o600)

    def test_cooldown_follows_physical_feeder_not_alias(self) -> None:
        with patch.object(
            petlibro_api.urllib.request,
            "urlopen",
            side_effect=[self.device_list_response(), FakeResponse({"code": 0})],
        ):
            first_code, _ = self.run_main(["feed", "crosstown-feeder", "1"])
        self.assertEqual(first_code, 0)

        config_text = self.config_file.read_text(encoding="utf-8").replace(
            "device_cabin_feeder: Cabin Feeder",
            "device_cabin_feeder: CROSS-FEEDER-SN",
        )
        self.config_file.write_text(config_text, encoding="utf-8")
        self.config_file.chmod(0o600)
        with patch.object(
            petlibro_api.urllib.request,
            "urlopen",
            return_value=self.device_list_response(),
        ) as urlopen:
            code, payload = self.run_main(["feed", "cabin-feeder", "1"])

        self.assertEqual(code, 1)
        self.assertEqual(payload["error"], "feed_cooldown")
        self.assertEqual(urlopen.call_count, 1)

    def test_ambiguous_feed_failure_is_recorded_and_not_retryable(self) -> None:
        responses = [self.device_list_response(), urllib.error.URLError("lost")]
        with patch.object(
            petlibro_api.urllib.request,
            "urlopen",
            side_effect=responses,
        ):
            code, payload = self.run_main(["feed", "crosstown-feeder", "1"])

        self.assertEqual(code, 1)
        self.assertEqual(payload["error"], "feed_outcome_unknown")
        self.assertTrue(payload["non_retryable"])
        self.assertTrue(payload["feed_may_have_occurred"])
        state = json.loads(self.state_file.read_text(encoding="utf-8"))
        feed_record = next(iter(state["feeds"].values()))
        self.assertEqual(feed_record["selector"], "crosstown-feeder")
        self.assertEqual(feed_record["status"], "unknown")

        with patch.object(
            petlibro_api.urllib.request,
            "urlopen",
            return_value=self.device_list_response(),
        ) as retry_urlopen:
            retry_code, retry_payload = self.run_main(
                ["feed", "crosstown-feeder", "1"]
            )
        self.assertEqual(retry_code, 1)
        self.assertEqual(retry_payload["error"], "feed_cooldown")
        self.assertEqual(retry_urlopen.call_count, 1)

    def test_raw_api_command_is_not_agent_facing(self) -> None:
        with patch.object(petlibro_api.urllib.request, "urlopen") as urlopen:
            code, payload = self.run_main(["raw", "/device/device/manualFeeding"])
        self.assertEqual(code, 1)
        self.assertEqual(payload["error"], "unknown_command")
        urlopen.assert_not_called()

        cli_text = CLI_PATH.read_text(encoding="utf-8")
        self.assertNotIn("Raw API POST", cli_text)
        self.assertNotRegex(cli_text, r"(?m)^\s*raw\)")

    def test_real_python_entrypoint_returns_nonzero_structured_errors(self) -> None:
        subprocess_config = self.root / "subprocess-config"
        env = os.environ.copy()
        env.update(
            {
                "HOME": str(self.root / "subprocess-home"),
                "PETLIBRO_CONFIG_DIR": str(subprocess_config),
                "PETLIBRO_FEED_STATE_DIR": str(self.root / "subprocess-state"),
            }
        )
        cases = (
            (["raw", "/device/device/manualFeeding"], "unknown_command"),
            (["feed", "crosstown-feeder", "4"], "invalid_portions"),
            (["devices"], "config_missing"),
        )
        for args, expected_error in cases:
            with self.subTest(args=args):
                result = subprocess.run(
                    [sys.executable, str(API_PATH), *args],
                    check=False,
                    capture_output=True,
                    text=True,
                    env=env,
                )
                self.assertEqual(result.returncode, 1, result.stderr)
                self.assertEqual(result.stderr, "")
                payload = json.loads(result.stdout)
                self.assertEqual(payload["error"], expected_error)

    def test_real_bash_wrapper_requires_exact_arguments_before_api(self) -> None:
        cases = (
            ([], 1),
            (["status", "extra"], 2),
            (["devices", "extra"], 2),
            (["feed"], 2),
            (["feed", "crosstown-feeder"], 2),
            (["feed", "crosstown-feeder", "0"], 2),
            (["feed", "crosstown-feeder", "4"], 2),
            (["feed", "crosstown-feeder", "1", "extra"], 2),
            (["water"], 2),
            (["water", "crosstown-fountain", "extra"], 2),
            (["schedule"], 2),
            (["schedule", "crosstown-feeder", "extra"], 2),
            (["schedule-set"], 2),
            (["schedule-set", "crosstown-feeder"], 2),
            (["schedule-set", "crosstown-feeder", "pause"], 2),
            (["schedule-set", "crosstown-fountain", "off"], 2),
            (["schedule-set", "crosstown-feeder", "off", "extra"], 2),
        )
        for args, expected_code in cases:
            with self.subTest(args=args):
                result = subprocess.run(
                    ["bash", str(CLI_PATH), *args],
                    check=False,
                    capture_output=True,
                    text=True,
                    env={"HOME": str(self.root / "wrapper-home"), "PATH": os.environ["PATH"]},
                )
                self.assertEqual(result.returncode, expected_code)
                self.assertNotIn("Raw API POST", result.stdout + result.stderr)

    def test_json_wrapper_preserves_mutation_contracts(self) -> None:
        env = {"HOME": str(self.root / "wrapper-home"), "PATH": os.environ["PATH"]}
        for args in (
            ["--json", "feed", "crosstown-feeder"],
            ["feed", "crosstown-feeder", "4", "--json"],
            ["--json", "schedule-set", "crosstown-feeder", "pause"],
            ["schedule-set", "crosstown-fountain", "off", "--json"],
        ):
            with self.subTest(args=args):
                result = subprocess.run(
                    ["bash", str(CLI_PATH), *args],
                    check=False,
                    capture_output=True,
                    text=True,
                    env=env,
                )
                self.assertEqual(result.returncode, 2)
                self.assertEqual(json.loads(result.stdout)["error"], "invalid_arguments")

    def test_home_dashboard_builds_exact_crosstown_feed_command(self) -> None:
        dashboard = load_home_dashboard(self.root / "dashboard-home")
        builder = dashboard.COMMANDS["petlibro"]["feed"]

        with self.assertRaises(dashboard.CommandValidationError):
            builder({})
        self.assertEqual(
            builder({"portions": 2}),
            ["petlibro", "feed", "crosstown-feeder", "2"],
        )


if __name__ == "__main__":
    unittest.main()
