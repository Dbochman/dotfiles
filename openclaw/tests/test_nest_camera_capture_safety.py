#!/usr/bin/env python3
"""Offline security and atomicity tests for Nest camera capture."""

from __future__ import annotations

import asyncio
from contextlib import redirect_stderr
import importlib.util
import io
import json
import os
from pathlib import Path
import shlex
import stat
import subprocess
import sys
import tempfile
import types
import unittest
import urllib.error


REPO_ROOT = Path(__file__).resolve().parents[2]
NEST = REPO_ROOT / "bin" / "nest"
OPENCLAW_NEST = REPO_ROOT / "openclaw" / "bin" / "nest"
CAMERA_HELPER = REPO_ROOT / "openclaw" / "bin" / "nest-camera-snap.py"


def protected_camera_config() -> dict:
    return {
        "version": 1,
        "cameras": [
            {
                "alias": "Kitchen",
                "site": "Cabin",
                "resource": (
                    "enterprises/private-project/devices/protected-kitchen-id"
                ),
                "capture": "live",
            },
            {
                "alias": "Living Room",
                "site": "Crosstown",
                "resource": (
                    "enterprises/private-project/devices/crosstown-living-id"
                ),
                "capture": "event",
            },
            {
                "alias": "Living Room Wired",
                "site": "Crosstown",
                "resource": (
                    "enterprises/private-project/devices/crosstown-wired-id"
                ),
                "capture": "event",
            },
        ],
    }


FAKE_CURL = r'''#!/usr/bin/python3
import json
import os
from pathlib import Path
import sys
import urllib.parse

args = sys.argv[1:]
url = next((arg for arg in args if arg.startswith("https://")), "")
config_stdin = False
if "--config" in args:
    config_stdin = args[args.index("--config") + 1] == "-"
    for line in sys.stdin.read().splitlines():
        if line.startswith('url = "') and line.endswith('"'):
            url = line[len('url = "'):-1]
            break

record = {
    "args": args,
    "config_stdin": config_stdin,
    "endpoint": None,
    "oauth_form_valid": None,
}
if "oauth2/v4/token" in url:
    record["endpoint"] = "oauth"
    form = urllib.parse.parse_qs(sys.stdin.read(), strict_parsing=True)
    record["oauth_form_valid"] = form == {
        "client_id": ["private-client"],
        "client_secret": ["private-secret"],
        "refresh_token": ["private-refresh"],
        "grant_type": ["refresh_token"],
    }
    response = {"access_token": "private-access-token"}
elif url.endswith("/devices"):
    record["endpoint"] = "devices"
    devices = [{
            "name": "enterprises/private-project/devices/private-camera-id",
            "type": "sdm.devices.types.CAMERA",
            "parentRelations": [{"displayName": "Kitchen"}],
            "traits": {
                "sdm.devices.traits.Info": {"customName": "Kitchen"}
            },
        }]
    if os.environ.get("FAKE_CAMERA_MATCH_MODE") == "ambiguous":
        devices.append({
            "name": "enterprises/private-project/devices/second-camera-id",
            "type": "sdm.devices.types.CAMERA",
            "parentRelations": [{"displayName": "Kitchen"}],
            "traits": {},
        })
    response = {"devices": devices}
else:
    raise SystemExit(93)

with open(os.environ["FAKE_CURL_LOG"], "a", encoding="utf-8") as handle:
    handle.write(json.dumps(record) + "\n")
print(json.dumps(response))
'''


FAKE_CAMERA_PYTHON = r'''#!/usr/bin/python3
import json
import os
from pathlib import Path
import sys
import tempfile

request = json.load(sys.stdin)
destination = Path(request["output_path"])
destination.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
descriptor, temporary = tempfile.mkstemp(dir=str(destination.parent))
os.fchmod(descriptor, 0o600)
with os.fdopen(descriptor, "wb") as handle:
    handle.write(b"fake-jpeg")
os.replace(temporary, destination)
record = {
    "argv": sys.argv[1:],
    "request_valid": (
        request["device_id"] == os.environ.get(
            "EXPECTED_CAMERA_ID", "private-camera-id"
        )
        and request["access_token"] == "private-access-token"
        and request["project_id"] == "private-project"
    ),
}
Path(os.environ["FAKE_CAMERA_LOG"]).write_text(json.dumps(record), encoding="utf-8")
print(destination)
'''


def load_camera_helper():
    fake_aiortc = types.ModuleType("aiortc")
    fake_aiortc.RTCPeerConnection = object
    fake_aiortc.RTCSessionDescription = object
    fake_aiortc.RTCRtpReceiver = object
    previous = sys.modules.get("aiortc")
    sys.modules["aiortc"] = fake_aiortc
    try:
        spec = importlib.util.spec_from_file_location(
            "nest_camera_snap_for_test", CAMERA_HELPER
        )
        module = importlib.util.module_from_spec(spec)
        assert spec.loader is not None
        spec.loader.exec_module(module)
        return module
    finally:
        if previous is None:
            sys.modules.pop("aiortc", None)
        else:
            sys.modules["aiortc"] = previous


class FakeImage:
    def __init__(self, payload: bytes = b"jpeg-data", fail: bool = False) -> None:
        self.payload = payload
        self.fail = fail

    def save(self, handle, image_format: str, quality: int) -> None:
        if image_format != "JPEG" or quality != 90:
            raise AssertionError("unexpected image encoding")
        handle.write(self.payload)
        if self.fail:
            raise OSError("private encoder detail")


class CameraHelperSafetyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.helper = load_camera_helper()

    def test_request_is_stdin_only_and_strictly_validated(self) -> None:
        request = {
            "device_id": "device-1",
            "access_token": "token._~+/=",
            "project_id": "project-1",
            "output_path": "relative/frame.jpg",
        }
        parsed = self.helper.read_capture_request(
            io.BytesIO(json.dumps(request).encode("utf-8"))
        )
        self.assertEqual(parsed[:3], ("device-1", "token._~+/=", "project-1"))
        self.assertTrue(os.path.isabs(parsed[3]))

        invalid_requests = (
            {**request, "unexpected": "private"},
            {**request, "access_token": "token\nheader-injection"},
            {**request, "output_path": "bad\npath.jpg"},
        )
        for invalid in invalid_requests:
            with self.subTest(invalid=invalid):
                with self.assertRaises(self.helper.CaptureRequestError):
                    self.helper.read_capture_request(
                        io.BytesIO(json.dumps(invalid).encode("utf-8"))
                    )

    def test_atomic_save_replaces_destination_with_mode_0600(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            destination = Path(tempdir) / "nested" / "frame.jpg"

            self.helper.save_frame_atomic(FakeImage(), str(destination))

            destination.write_bytes(b"old")
            destination.chmod(0o644)
            self.helper.save_frame_atomic(FakeImage(), str(destination))

            self.assertEqual(destination.read_bytes(), b"jpeg-data")
            self.assertEqual(stat.S_IMODE(destination.stat().st_mode), 0o600)
            self.assertEqual(
                stat.S_IMODE(destination.parent.stat().st_mode),
                0o700,
            )
            self.assertEqual(list(destination.parent.glob(".frame.jpg.*.tmp")), [])

    def test_atomic_save_does_not_follow_destination_symlink(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            protected_target = root / "protected.txt"
            protected_target.write_bytes(b"keep")
            destination = root / "frame.jpg"
            destination.symlink_to(protected_target)

            self.helper.save_frame_atomic(FakeImage(), str(destination))

            self.assertFalse(destination.is_symlink())
            self.assertEqual(destination.read_bytes(), b"jpeg-data")
            self.assertEqual(protected_target.read_bytes(), b"keep")

    def test_failed_encode_preserves_previous_file_and_removes_temporary(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            destination = Path(tempdir) / "frame.jpg"
            destination.write_bytes(b"known-good")

            with self.assertRaises(OSError):
                self.helper.save_frame_atomic(
                    FakeImage(payload=b"partial", fail=True), str(destination)
                )

            self.assertEqual(destination.read_bytes(), b"known-good")
            self.assertEqual(list(destination.parent.glob(".frame.jpg.*.tmp")), [])

    def test_cli_rejects_argv_credentials_without_echoing_them(self) -> None:
        marker = "private-token-must-not-be-logged"
        result = subprocess.run(
            ["/usr/bin/python3", str(CAMERA_HELPER), marker],
            input="",
            check=False,
            capture_output=True,
            text=True,
        )

        self.assertEqual(result.returncode, 2)
        self.assertNotIn(marker, result.stdout + result.stderr)
        self.assertIn("provided on stdin", result.stderr)

    def test_http_failure_is_sanitized_and_keeps_required_h264_profile(self) -> None:
        selected_codecs = []
        test_case = self

        class FakeCodec:
            mimeType = "video/H264"
            parameters = {"profile-level-id": "42e01f"}

        class FakeCapabilities:
            codecs = [FakeCodec()]

        class FakeReceiver:
            @staticmethod
            def getCapabilities(kind):
                test_case.assertEqual(kind, "video")
                return FakeCapabilities()

        class FakeTransceiver:
            def setCodecPreferences(self, codecs):
                selected_codecs.extend(codecs)

        class FakePeerConnection:
            def __init__(self):
                self.localDescription = None

            def addTransceiver(self, kind, direction):
                test_case.assertEqual(direction, "recvonly")
                return FakeTransceiver()

            def createDataChannel(self, label):
                return object()

            def on(self, event):
                return lambda callback: callback

            async def createOffer(self):
                return object()

            async def setLocalDescription(self, offer):
                self.localDescription = types.SimpleNamespace(sdp="private-offer")

            async def close(self):
                return None

        def fail_post(url, body, access_token, timeout):
            raise urllib.error.HTTPError(
                url,
                403,
                "private-http-detail",
                None,
                io.BytesIO(b"private-response-body"),
            )

        originals = (
            self.helper.RTCPeerConnection,
            self.helper.RTCRtpReceiver,
            self.helper.post_json,
        )
        self.helper.RTCPeerConnection = FakePeerConnection
        self.helper.RTCRtpReceiver = FakeReceiver
        self.helper.post_json = fail_post
        stderr = io.StringIO()
        try:
            with redirect_stderr(stderr):
                ok = asyncio.run(
                    self.helper.capture_frame(
                        "private-device",
                        "private-token",
                        "private-project",
                        "/private/output.jpg",
                    )
                )
        finally:
            (
                self.helper.RTCPeerConnection,
                self.helper.RTCRtpReceiver,
                self.helper.post_json,
            ) = originals

        self.assertFalse(ok)
        self.assertEqual(len(selected_codecs), 1)
        self.assertEqual(
            selected_codecs[0].parameters["profile-level-id"],
            "42e01f",
        )
        error_text = stderr.getvalue()
        self.assertIn("HTTP 403", error_text)
        for private_value in (
            "private-http-detail",
            "private-response-body",
            "private-device",
            "private-token",
            "private-project",
            "private-offer",
        ):
            self.assertNotIn(private_value, error_text)


class NestCameraWrapperSafetyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tempdir.cleanup)
        self.root = Path(self.tempdir.name)
        self.home = self.root / "home"
        self.fake_bin = self.root / "bin"
        self.cache_dir = self.root / "nest-cache"
        self.secrets_cache = self.home / ".openclaw" / ".secrets-cache"
        self.event_config = (
            self.home / ".openclaw" / "nest-events" / "config" / "cameras.json"
        )
        self.curl_log = self.root / "curl.jsonl"
        self.camera_log = self.root / "camera.json"
        self.output = self.root / "capture" / "frame.jpg"
        self.home.mkdir()
        self.fake_bin.mkdir()
        self.cache_dir.mkdir(mode=0o700)
        self.write_executable(self.fake_bin / "curl", FAKE_CURL)
        self.camera_python = self.root / "fake-camera-python"
        self.write_executable(self.camera_python, FAKE_CAMERA_PYTHON)
        self.fake_helper = self.root / "fake-helper.py"
        self.fake_helper.write_text("# test seam\n", encoding="utf-8")
        self.write_assignments(
            self.secrets_cache,
            {
                "NEST_CLIENT_ID": "private-client",
                "NEST_CLIENT_SECRET": "private-secret",
                "NEST_REFRESH_TOKEN": "private-refresh",
                "NEST_PROJECT_ID": "private-project",
            },
        )
        self.write_camera_config(protected_camera_config())

    def environment(self, **overrides: str) -> dict[str, str]:
        environment = os.environ.copy()
        environment.update(
            {
                "HOME": str(self.home),
                "PATH": f"{self.fake_bin}:{environment['PATH']}",
                "OPENCLAW_SECRETS_CACHE": str(self.secrets_cache),
                "NEST_CACHE_DIR": str(self.cache_dir),
                "NEST_CAMERA_PYTHON": str(self.camera_python),
                "NEST_CAMERA_HELPER": str(self.fake_helper),
                "NEST_EVENT_CONFIG": str(self.event_config),
                "FAKE_CURL_LOG": str(self.curl_log),
                "FAKE_CAMERA_LOG": str(self.camera_log),
            }
        )
        environment.update(overrides)
        return environment

    def run_capture(
        self, room: str, **environment: str
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["bash", str(NEST), "camera", "snap", room, str(self.output)],
            stdin=subprocess.DEVNULL,
            check=False,
            capture_output=True,
            text=True,
            env=self.environment(**environment),
        )

    def run_config_capture(
        self, alias: str = "Kitchen", **environment: str
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [
                "bash",
                str(NEST),
                "camera",
                "snap-config",
                alias,
                str(self.output),
            ],
            stdin=subprocess.DEVNULL,
            check=False,
            capture_output=True,
            text=True,
            env=self.environment(**environment),
        )

    @staticmethod
    def write_executable(path: Path, content: str) -> None:
        path.write_text(content, encoding="utf-8")
        path.chmod(path.stat().st_mode | stat.S_IXUSR)

    @staticmethod
    def write_assignments(path: Path, values: dict[str, str]) -> None:
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        path.write_text(
            "".join(f"{key}={shlex.quote(value)}\n" for key, value in values.items()),
            encoding="utf-8",
        )
        path.chmod(0o600)

    def write_camera_config(self, config: dict) -> None:
        self.event_config.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.event_config.parent.chmod(0o700)
        self.event_config.write_text(json.dumps(config), encoding="utf-8")
        self.event_config.chmod(0o600)

    def test_mutable_name_capture_is_refused_when_unattended(self) -> None:
        result = self.run_capture("  kItChEn  ")

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("protected exact alias", result.stderr)
        self.assertFalse(self.output.exists())
        self.assertFalse(self.curl_log.exists())
        self.assertFalse(self.camera_log.exists())

    def test_camera_selection_rejects_substring_and_ambiguous_matches(self) -> None:
        substring = self.run_capture("kit")

        self.assertNotEqual(substring.returncode, 0)
        self.assertIn("protected exact alias", substring.stderr)
        self.assertFalse(self.camera_log.exists())
        self.assertFalse(self.output.exists())

        ambiguous = self.run_capture("Kitchen", FAKE_CAMERA_MATCH_MODE="ambiguous")

        self.assertNotEqual(ambiguous.returncode, 0)
        self.assertIn("protected exact alias", ambiguous.stderr)
        self.assertFalse(self.camera_log.exists())
        self.assertFalse(self.output.exists())
        for private_value in ("private-camera-id", "second-camera-id"):
            self.assertNotIn(private_value, ambiguous.stdout + ambiguous.stderr)

    def test_config_bound_capture_uses_each_exact_protected_resource(self) -> None:
        expected_ids = {
            "Kitchen": "protected-kitchen-id",
            "Living Room": "crosstown-living-id",
            "Living Room Wired": "crosstown-wired-id",
        }
        for alias, expected_id in expected_ids.items():
            with self.subTest(alias=alias):
                for cached_token in self.cache_dir.iterdir():
                    cached_token.unlink()
                result = self.run_config_capture(
                    alias,
                    EXPECTED_CAMERA_ID=expected_id,
                    # If this path accidentally performs a device-name lookup,
                    # the fake API still maps mutable display names elsewhere.
                    FAKE_CAMERA_MATCH_MODE="ambiguous",
                )

                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(self.output.read_bytes(), b"fake-jpeg")
                camera_record = json.loads(self.camera_log.read_text())
                self.assertTrue(camera_record["request_valid"])
                self.assertEqual(camera_record["argv"], [str(self.fake_helper)])
                curl_records = [
                    json.loads(line)
                    for line in self.curl_log.read_text().splitlines()
                ]
                self.assertEqual(
                    [record["endpoint"] for record in curl_records],
                    ["oauth"],
                )

                process_arguments = json.dumps(
                    [record["args"] for record in curl_records]
                    + [camera_record["argv"]]
                )
                for private_value in (
                    "protected-kitchen-id",
                    "crosstown-living-id",
                    "crosstown-wired-id",
                    "private-project",
                    "private-access-token",
                ):
                    self.assertNotIn(private_value, process_arguments)
                    self.assertNotIn(
                        private_value, result.stdout + result.stderr
                    )

                self.output.unlink()
                self.camera_log.unlink()
                self.curl_log.unlink()

    def test_config_bound_capture_rejects_policy_misroutes(self) -> None:
        mutations = {
            "site": lambda config: config["cameras"][0].update(
                {"site": "Crosstown"}
            ),
            "capture": lambda config: config["cameras"][0].update(
                {"capture": "event"}
            ),
            "enterprise": lambda config: config["cameras"][0].update(
                {
                    "resource": (
                        "enterprises/other-project/devices/protected-kitchen-id"
                    )
                }
            ),
        }
        for name, mutate in mutations.items():
            with self.subTest(name=name):
                config = protected_camera_config()
                mutate(config)
                self.write_camera_config(config)
                result = self.run_config_capture(
                    EXPECTED_CAMERA_ID="protected-kitchen-id"
                )

                self.assertNotEqual(result.returncode, 0)
                self.assertIn("does not authorize capture", result.stderr)
                self.assertFalse(self.camera_log.exists())
                self.assertFalse(self.output.exists())
                self.assertFalse(self.curl_log.exists())

    def test_config_bound_capture_rejects_alias_and_resource_collisions(self) -> None:
        collision_mutations = {
            "alias": lambda config: config["cameras"][2].update(
                {"alias": "Kitchen", "site": "Cabin", "capture": "live"}
            ),
            "resource": lambda config: config["cameras"][2].update(
                {"resource": config["cameras"][0]["resource"]}
            ),
        }
        for name, mutate in collision_mutations.items():
            with self.subTest(name=name):
                config = protected_camera_config()
                mutate(config)
                self.write_camera_config(config)
                result = self.run_config_capture(
                    EXPECTED_CAMERA_ID="protected-kitchen-id"
                )

                self.assertNotEqual(result.returncode, 0)
                self.assertFalse(self.camera_log.exists())
                self.assertFalse(self.output.exists())
                self.assertFalse(self.curl_log.exists())

    def test_config_bound_capture_requires_exact_alias_and_owner_only_config(self) -> None:
        wrong_alias = self.run_config_capture("living room")
        self.assertNotEqual(wrong_alias.returncode, 0)
        self.assertFalse(self.camera_log.exists())
        self.assertFalse(self.curl_log.exists())

        self.event_config.chmod(0o644)
        insecure = self.run_config_capture("Kitchen")
        self.assertNotEqual(insecure.returncode, 0)
        self.assertFalse(self.camera_log.exists())
        self.assertFalse(self.curl_log.exists())

        symlink_target = self.root / "alternate-cameras.json"
        symlink_target.write_text(
            json.dumps(protected_camera_config()), encoding="utf-8"
        )
        symlink_target.chmod(0o600)
        self.event_config.unlink()
        self.event_config.symlink_to(symlink_target)
        symlinked = self.run_config_capture("Kitchen")
        self.assertNotEqual(symlinked.returncode, 0)
        self.assertFalse(self.camera_log.exists())
        self.assertFalse(self.curl_log.exists())

        self.event_config.unlink()
        os.mkfifo(self.event_config, mode=0o600)
        non_regular = self.run_config_capture("Kitchen")
        self.assertNotEqual(non_regular.returncode, 0)
        self.assertFalse(self.camera_log.exists())
        self.assertFalse(self.curl_log.exists())

    def test_tracked_nest_copies_remain_identical(self) -> None:
        self.assertEqual(NEST.read_bytes(), OPENCLAW_NEST.read_bytes())


if __name__ == "__main__":
    unittest.main()
