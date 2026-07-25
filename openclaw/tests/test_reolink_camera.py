#!/usr/bin/env python3
"""Offline contract and safety tests for the Reolink camera skill."""

from __future__ import annotations

from contextlib import redirect_stderr, redirect_stdout
import hashlib
import importlib.machinery
import importlib.util
import io
import json
import os
from pathlib import Path
import stat
import sys
import tempfile
import time
import unittest
from unittest import mock


REPO_ROOT = Path(__file__).resolve().parents[2]
SKILL_DIR = REPO_ROOT / "openclaw" / "skills" / "reolink-camera"
HELPER = SKILL_DIR / "reolink-camera"
SKILL = SKILL_DIR / "SKILL.md"

ALIAS = "Cabin Driveway"
SECOND_ALIAS = "Cabin Porch"
SITE = "Cabin"
HOST = "192.168.50.20"
USERNAME = "openclaw-camera"
PASSWORD = "private-camera-password"
PRIVATE_TOKEN = "private-login-token"
PRIVATE_SERIAL = "private-hub-serial"
PRIVATE_ADMIN_NAME = "private-admin-name"
PRIVATE_SECONDARY_NAME = "private-secondary-name"
PRIVATE_ADMIN_PASSWORD = "private-admin-password"
GENERATED_GUEST_USERNAME = "openclaw0123456789abcdef"
GENERATED_GUEST_PASSWORD = "0123456789abcdef0123456789abcd"
PRIVATE_GUEST_TOKEN = "private-guest-token"
PEER_CERTIFICATE = b"offline-fixture-reolink-certificate"
CERTIFICATE_SHA256 = hashlib.sha256(PEER_CERTIFICATE).hexdigest()
GENERATION = "1" * 32
JPEG = bytes.fromhex(
    "ffd8"
    "ffe000104a46494600010100000100010000"
    "ffc0000b080001000101011100"
    "ffda0008010100003f00"
    "00"
    "ffd9"
)


def load_helper():
    module_name = f"reolink_camera_for_test_{time.time_ns()}"
    loader = importlib.machinery.SourceFileLoader(module_name, str(HELPER))
    spec = importlib.util.spec_from_loader(module_name, loader)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    try:
        spec.loader.exec_module(module)
    except BaseException:
        sys.modules.pop(module_name, None)
        raise
    return module


class FakeResponse:
    """Small ``http.client.HTTPResponse`` stand-in."""

    def __init__(
        self,
        payload: object,
        *,
        status: int = 200,
        content_type: str = "application/json",
    ) -> None:
        self.status = status
        self.reason = "fixture"
        self._payload = (
            payload
            if isinstance(payload, bytes)
            else json.dumps(payload, separators=(",", ":")).encode("utf-8")
        )
        self._headers = {
            "content-type": content_type,
            "content-length": str(len(self._payload)),
        }

    def read(self, amount: int | None = None) -> bytes:
        if amount is None:
            payload, self._payload = self._payload, b""
            return payload
        payload, self._payload = self._payload[:amount], self._payload[amount:]
        return payload

    def getheader(self, name: str, default: object = None) -> object:
        return self._headers.get(name.casefold(), default)


class FakeSocket:
    def __init__(self, peer_certificate: bytes) -> None:
        self.peer_certificate = peer_certificate

    def getpeercert(self, binary_form: bool = False):
        if not binary_form:
            raise AssertionError("certificate pinning must use the DER certificate")
        return self.peer_certificate


class FakeHTTPSConnection:
    """One connection produced by :class:`FakeConnectionFactory`."""

    def __init__(
        self,
        factory: "FakeConnectionFactory",
        host: str,
        port: int | None = None,
        **kwargs,
    ) -> None:
        self.factory = factory
        self.host = host
        self.port = port
        self.kwargs = kwargs
        self.sock: FakeSocket | None = None
        self.connected = False
        self.closed = False

    def connect(self) -> None:
        self.connected = True
        self.sock = FakeSocket(self.factory.peer_certificate)

    def request(
        self,
        method: str,
        url: str,
        body: bytes | str | None = None,
        headers: dict[str, str] | None = None,
        **kwargs,
    ) -> None:
        if not self.connected:
            self.connect()
        self.factory.requests.append(
            {
                "host": self.host,
                "port": self.port,
                "method": method,
                "url": url,
                "body": body,
                "headers": dict(headers or {}),
                "kwargs": kwargs,
            }
        )

    def getresponse(self) -> FakeResponse:
        if not self.factory.responses:
            raise AssertionError("unexpected Reolink request")
        return self.factory.responses.pop(0)

    def close(self) -> None:
        self.closed = True


class FakeConnectionFactory:
    """Callable compatible with ``http.client.HTTPSConnection``."""

    def __init__(
        self,
        responses: list[FakeResponse],
        *,
        peer_certificate: bytes = PEER_CERTIFICATE,
    ) -> None:
        self.responses = list(responses)
        self.peer_certificate = peer_certificate
        self.connections: list[FakeHTTPSConnection] = []
        self.requests: list[dict[str, object]] = []

    def __call__(
        self, host: str, port: int | None = None, **kwargs
    ) -> FakeHTTPSConnection:
        connection = FakeHTTPSConnection(self, host, port, **kwargs)
        self.connections.append(connection)
        return connection


class TTYBuffer(io.StringIO):
    def __init__(self, initial_value: str = "", *, tty: bool = True) -> None:
        super().__init__(initial_value)
        self.tty = tty

    def isatty(self) -> bool:
        return self.tty


class ScriptedInput:
    def __init__(self, values: list[str]) -> None:
        self.values = list(values)
        self.prompts: list[str] = []

    def __call__(self, prompt: str) -> str:
        self.prompts.append(prompt)
        if not self.values:
            raise AssertionError(f"unexpected enrollment prompt: {prompt}")
        return self.values.pop(0)


def login_response() -> FakeResponse:
    return FakeResponse(
        [
            {
                "cmd": "Login",
                "code": 0,
                "value": {
                    "Token": {
                        "name": PRIVATE_TOKEN,
                        "leaseTime": 3600,
                    }
                },
            }
        ]
    )


def status_response() -> FakeResponse:
    return FakeResponse(
        [
            {
                "cmd": "GetChannelstatus",
                "code": 0,
                "value": {
                    "status": [
                        {
                            "channel": 0,
                            "online": 1,
                            "name": "private-device-name",
                            "uid": "private-device-uid",
                        }
                    ]
                },
            }
        ]
    )


def battery_response(
    *,
    battery_percent: int = 74,
    charge_status: int = 1,
    temperature: int = 22,
) -> FakeResponse:
    return FakeResponse(
        [
            {
                "cmd": "GetBatteryInfo",
                "code": 0,
                "value": {
                    "Battery": {
                        "batteryPercent": battery_percent,
                        "chargeStatus": charge_status,
                        "channel": 0,
                        "temperature": temperature,
                        "privateDiagnostic": PRIVATE_SERIAL,
                    }
                },
            }
        ]
    )


def spotlight_response(
    *,
    state: int = 0,
    brightness: int = 10,
    mode: int = 5,
) -> FakeResponse:
    return FakeResponse(
        [
            {
                "cmd": "GetWhiteLed",
                "code": 0,
                "value": {
                    "WhiteLed": {
                        "channel": 0,
                        "state": state,
                        "bright": brightness,
                        "mode": mode,
                        "privateDiagnostic": PRIVATE_SERIAL,
                    }
                },
            }
        ]
    )


def set_spotlight_response(*, response_code: int = 200) -> FakeResponse:
    return FakeResponse(
        [
            {
                "cmd": "SetWhiteLed",
                "code": 0,
                "value": {"rspCode": response_code},
            }
        ]
    )


def logout_response() -> FakeResponse:
    return FakeResponse([{"cmd": "Logout", "code": 0, "value": {}}])


def ability_response(*, permit: object = 6, version: object = 1) -> FakeResponse:
    return FakeResponse(
        [
            {
                "cmd": "GetAbility",
                "code": 0,
                "value": {
                    "Ability": {
                        "user": {
                            "permit": permit,
                            "ver": version,
                        }
                    }
                },
            }
        ]
    )


_DEFAULT_USER_RANGE = object()


def users_response(
    users: object,
    *,
    user_range: object = _DEFAULT_USER_RANGE,
) -> FakeResponse:
    item: dict[str, object] = {
        "cmd": "GetUser",
        "code": 0,
        "value": {"User": users},
    }
    if user_range is _DEFAULT_USER_RANGE:
        user_range = {
            "level": ["admin", "guest"],
            "userName": {"minLen": 1, "maxLen": 31},
            "password": {"minLen": 1, "maxLen": 31},
        }
    if user_range is not None:
        item["range"] = {"User": user_range}
    return FakeResponse([item])


def user_mutation_response(command: str) -> FakeResponse:
    return FakeResponse(
        [
            {
                "cmd": command,
                "code": 0,
                "value": {"rspCode": 200},
            }
        ]
    )


class ReolinkCameraTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.helper = load_helper()

    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tempdir.cleanup)
        self.root = Path(self.tempdir.name)
        self.protected = self.root / "protected"
        self.protected.mkdir(mode=0o700)
        self.config_path = self.protected / "bindings.json"
        self.credentials_path = self.protected / "credentials.json"
        self.media_directory = self.root / "media"
        self.write_protected_files()

    @staticmethod
    def binding_entry(
        *,
        alias: str = ALIAS,
        host: str = HOST,
        site: str = SITE,
        channel: int = 0,
        tls_sha256: str = CERTIFICATE_SHA256,
    ) -> dict[str, object]:
        return {
            "alias": alias,
            "site": site,
            "host": host,
            "port": 443,
            "channel": channel,
            "tlsSha256": tls_sha256,
        }

    @staticmethod
    def credentials_entry(
        *,
        alias: str = ALIAS,
        username: str = USERNAME,
        password: str = PASSWORD,
    ) -> dict[str, str]:
        return {
            "alias": alias,
            "username": username,
            "password": password,
        }

    def write_json(self, path: Path, payload: object) -> None:
        if path.exists() or path.is_symlink():
            path.unlink()
        path.write_text(
            json.dumps(payload, separators=(",", ":")),
            encoding="utf-8",
        )
        path.chmod(0o600)

    def write_protected_files(
        self,
        *,
        bindings: list[dict[str, object]] | None = None,
        credentials: list[dict[str, str]] | None = None,
        config_version: int = 1,
        credentials_version: int = 1,
        config_generation: str = GENERATION,
        credentials_generation: str = GENERATION,
    ) -> None:
        if bindings is None:
            bindings = [self.binding_entry()]
        if credentials is None:
            credentials = [self.credentials_entry()]
        self.write_json(
            self.config_path,
            {
                "version": config_version,
                "generation": config_generation,
                "cameras": bindings,
            },
        )
        self.write_json(
            self.credentials_path,
            {
                "version": credentials_version,
                "generation": credentials_generation,
                "cameras": credentials,
            },
        )

    def load_binding(self, alias: str = ALIAS):
        return self.helper.load_binding(
            alias,
            config_path=self.config_path,
            credentials_path=self.credentials_path,
        )

    def capture_image(self, alias: str = ALIAS, **kwargs):
        defaults = {
            "config_path": self.config_path,
            "credentials_path": self.credentials_path,
            "media_directory": self.media_directory,
            "reaper": lambda _token, _path: None,
        }
        defaults.update(kwargs)
        return self.helper.capture_image(alias, **defaults)

    def assert_public_error_without_secrets(
        self, context: unittest.case._AssertRaisesContext
    ) -> None:
        error = str(context.exception)
        self.assertTrue(error)
        for secret in (
            HOST,
            USERNAME,
            PASSWORD,
            PRIVATE_TOKEN,
            PRIVATE_SERIAL,
            CERTIFICATE_SHA256,
        ):
            self.assertNotIn(secret, error)

    def test_unknown_and_fuzzy_aliases_fail_before_client_or_network(self) -> None:
        calls = []

        def forbidden_client(*args, **kwargs):
            calls.append((args, kwargs))
            raise AssertionError("network client created for an invalid alias")

        for alias in ("cabin driveway", "Driveway", "Cabin", ALIAS + " ", ""):
            with self.subTest(alias=alias):
                with self.assertRaises(self.helper.PublicError) as caught:
                    self.capture_image(alias, client_factory=forbidden_client)
                self.assert_public_error_without_secrets(caught)
        self.assertEqual(calls, [])

    def test_protected_files_require_owner_only_regular_mode_0600(self) -> None:
        for selected in ("config", "credentials"):
            for corruption in ("mode", "owner", "symlink", "hardlink"):
                with self.subTest(selected=selected, corruption=corruption):
                    self.write_protected_files()
                    path = (
                        self.config_path
                        if selected == "config"
                        else self.credentials_path
                    )
                    patcher = None
                    if corruption == "mode":
                        path.chmod(0o644)
                    elif corruption == "owner":
                        patcher = mock.patch.object(
                            self.helper.os,
                            "geteuid",
                            return_value=os.geteuid() + 1,
                        )
                        patcher.start()
                    else:
                        target = path.with_suffix(
                            ".target" if corruption == "symlink" else ".hardlink"
                        )
                        if target.exists() or target.is_symlink():
                            target.unlink()
                        if corruption == "symlink":
                            path.replace(target)
                            path.symlink_to(target)
                        else:
                            os.link(path, target)
                    try:
                        with self.assertRaises(self.helper.PublicError) as caught:
                            self.load_binding()
                    finally:
                        if patcher is not None:
                            patcher.stop()
                    self.assert_public_error_without_secrets(caught)

    def test_protected_file_schema_is_closed_and_alias_sets_are_equal(self) -> None:
        cases = []

        def add_case(label: str, config: object, credentials: object) -> None:
            cases.append((label, config, credentials))

        valid_config = {
            "version": 1,
            "generation": GENERATION,
            "cameras": [self.binding_entry()],
        }
        valid_credentials = {
            "version": 1,
            "generation": GENERATION,
            "cameras": [self.credentials_entry()],
        }
        add_case(
            "config-version",
            {**valid_config, "version": 2},
            valid_credentials,
        )
        add_case(
            "credentials-version",
            valid_config,
            {**valid_credentials, "version": 2},
        )
        add_case(
            "generation-mismatch",
            valid_config,
            {**valid_credentials, "generation": "2" * 32},
        )
        add_case(
            "invalid-generation",
            {**valid_config, "generation": "A" * 32},
            valid_credentials,
        )
        add_case(
            "config-extra-key",
            {**valid_config, "unexpected": PASSWORD},
            valid_credentials,
        )
        add_case(
            "credentials-extra-key",
            valid_config,
            {**valid_credentials, "unexpected": PASSWORD},
        )
        add_case(
            "binding-extra-key",
            {
                "version": 1,
                "generation": GENERATION,
                "cameras": [
                    {
                        **self.binding_entry(),
                        "uid": "private-device-uid",
                    }
                ],
            },
            valid_credentials,
        )
        add_case(
            "credential-extra-key",
            valid_config,
            {
                "version": 1,
                "generation": GENERATION,
                "cameras": [
                    {
                        **self.credentials_entry(),
                        "token": PRIVATE_TOKEN,
                    }
                ],
            },
        )
        add_case(
            "missing-credential-alias",
            {
                "version": 1,
                "generation": GENERATION,
                "cameras": [
                    self.binding_entry(),
                    self.binding_entry(alias=SECOND_ALIAS, channel=1),
                ],
            },
            valid_credentials,
        )
        add_case(
            "extra-credential-alias",
            valid_config,
            {
                "version": 1,
                "generation": GENERATION,
                "cameras": [
                    self.credentials_entry(),
                    self.credentials_entry(alias=SECOND_ALIAS),
                ],
            },
        )
        add_case(
            "duplicate-config-alias",
            {
                "version": 1,
                "generation": GENERATION,
                "cameras": [self.binding_entry(), self.binding_entry()],
            },
            valid_credentials,
        )
        add_case(
            "non-list-camera-set",
            {**valid_config, "cameras": {}},
            valid_credentials,
        )
        add_case(
            "non-list-credentials-set",
            valid_config,
            {**valid_credentials, "cameras": {}},
        )

        for label, config, credentials in cases:
            with self.subTest(label=label):
                self.write_json(self.config_path, config)
                self.write_json(self.credentials_path, credentials)
                with self.assertRaises(self.helper.PublicError) as caught:
                    self.load_binding()
                self.assert_public_error_without_secrets(caught)

    def test_protected_parent_directory_must_be_real_and_mode_0700(self) -> None:
        self.protected.chmod(0o755)
        with self.assertRaises(self.helper.PublicError) as permissive:
            self.load_binding()
        self.assert_public_error_without_secrets(permissive)

        self.protected.chmod(0o700)
        real = self.root / "real-protected"
        self.protected.replace(real)
        self.protected.symlink_to(real, target_is_directory=True)
        with self.assertRaises(self.helper.PublicError) as symlinked:
            self.load_binding()
        self.assert_public_error_without_secrets(symlinked)

    def test_only_rfc1918_and_ula_literal_hosts_are_accepted(self) -> None:
        accepted = (
            "10.1.2.3",
            "172.16.0.1",
            "172.31.255.254",
            "192.168.1.2",
            "fc00::20",
            "fd12:3456:789a::20",
        )
        rejected = (
            "8.8.8.8",
            "172.15.255.254",
            "172.32.0.1",
            "127.0.0.1",
            "169.254.1.1",
            "224.0.0.1",
            "0.0.0.0",
            "::1",
            "fe80::1",
            "ff02::1",
            "::ffff:192.168.1.2",
            "reolink.local",
            "192.168.1.2:443",
            "https://192.168.1.2",
        )

        for host in accepted:
            with self.subTest(host=host, expected="accepted"):
                self.write_protected_files(
                    bindings=[self.binding_entry(host=host)]
                )
                self.load_binding()

        for host in rejected:
            with self.subTest(host=host, expected="rejected"):
                self.write_protected_files(
                    bindings=[self.binding_entry(host=host)]
                )
                with self.assertRaises(self.helper.PublicError) as caught:
                    self.load_binding()
                self.assert_public_error_without_secrets(caught)
                self.assertNotIn(host, str(caught.exception))

    def test_binding_rejects_invalid_pin_port_channel_and_credentials(self) -> None:
        invalid_bindings = (
            self.binding_entry(tls_sha256="A" * 64),
            self.binding_entry(tls_sha256="0" * 63),
            {**self.binding_entry(), "port": 0},
            {**self.binding_entry(), "port": 65536},
            {**self.binding_entry(), "port": True},
            self.binding_entry(channel=-1),
            self.binding_entry(channel=True),
            self.binding_entry(site=""),
        )
        for binding in invalid_bindings:
            with self.subTest(binding=binding):
                self.write_protected_files(bindings=[binding])
                with self.assertRaises(self.helper.PublicError):
                    self.load_binding()

        invalid_credentials = (
            self.credentials_entry(username=""),
            self.credentials_entry(password=""),
            self.credentials_entry(username="bad\nuser"),
        )
        for credentials in invalid_credentials:
            with self.subTest(credentials=credentials):
                self.write_protected_files(credentials=[credentials])
                with self.assertRaises(self.helper.PublicError):
                    self.load_binding()

    def make_client(self, factory: FakeConnectionFactory):
        return self.helper.ReolinkClient(
            self.load_binding(),
            connection_factory=factory,
        )

    def enrollment_paths(self, label: str = "enrollment") -> tuple[Path, Path]:
        directory = self.root / label
        return directory / "config.json", directory / "credentials.json"

    def enrollment_reader(self, confirmation: str = "TRUST") -> ScriptedInput:
        return ScriptedInput(
            [
                ALIAS,
                SITE,
                HOST,
                "443",
                "0",
                USERNAME,
                confirmation,
            ]
        )

    def operator_probe_reader(
        self, confirmation: str = "TRUST"
    ) -> ScriptedInput:
        return ScriptedInput(
            [
                HOST,
                "443",
                PRIVATE_ADMIN_NAME,
                confirmation,
            ]
        )

    def operator_probe_client_factory(
        self,
        events: list[str],
        *,
        probe_error: BaseException | None = None,
    ):
        class FakeOperatorProbeClient:
            def __init__(self, binding) -> None:
                self.binding = binding
                events.append("client")

            def login(self) -> str:
                events.append("login")
                return PRIVATE_TOKEN

            def probe_local_user_capability(
                self,
                token: str,
                username: str,
            ) -> dict[str, int | str | bool]:
                if token != PRIVATE_TOKEN or username != PRIVATE_ADMIN_NAME:
                    raise AssertionError("unexpected operator probe identity")
                events.append("probe")
                if probe_error is not None:
                    raise probe_error
                return {
                    "authenticated": True,
                    "guestAccountAdvertised": True,
                    "localUserManagement": "supported",
                    "userListingAccessible": True,
                    "userWriteAdvertised": True,
                    "existingLocalUsers": 2,
                }

            def logout(self, token: str) -> None:
                if token != PRIVATE_TOKEN:
                    raise AssertionError("unexpected operator probe token")
                events.append("logout")

        return FakeOperatorProbeClient

    def run_operator_probe(
        self,
        *,
        reader: ScriptedInput,
        client_factory,
    ) -> tuple[dict[str, int | str | bool], str, str, list[str]]:
        stdout = TTYBuffer()
        stderr = TTYBuffer()
        stdin = TTYBuffer()
        password_prompts: list[str] = []

        def read_password(prompt: str) -> str:
            password_prompts.append(prompt)
            return PRIVATE_ADMIN_PASSWORD

        with mock.patch.object(self.helper.sys, "stdin", stdin), mock.patch.object(
            self.helper.sys, "stdout", stdout
        ), mock.patch.object(
            self.helper.sys, "stderr", stderr
        ), mock.patch.object(
            self.helper.sys,
            "argv",
            ["reolink-camera", "operator-probe-local-user"],
        ):
            result = self.helper.operator_probe_local_user(
                [],
                password_reader=read_password,
                input_reader=reader,
                fingerprint_probe=lambda host, port: (
                    CERTIFICATE_SHA256
                    if (host, port) == (HOST, 443)
                    else (_ for _ in ()).throw(
                        AssertionError("unexpected fingerprint endpoint")
                    )
                ),
                client_factory=client_factory,
            )
        return (
            result,
            stdout.getvalue(),
            stderr.getvalue(),
            password_prompts,
        )

    def run_operator_probe_failure(
        self,
        probe_error: BaseException,
        events: list[str],
    ) -> tuple[BaseException, str]:
        stdout = TTYBuffer()
        stderr = TTYBuffer()
        stdin = TTYBuffer()
        reader = self.operator_probe_reader()
        password_prompts: list[str] = []

        def read_password(prompt: str) -> str:
            password_prompts.append(prompt)
            return PRIVATE_ADMIN_PASSWORD

        with mock.patch.object(self.helper.sys, "stdin", stdin), mock.patch.object(
            self.helper.sys, "stdout", stdout
        ), mock.patch.object(
            self.helper.sys, "stderr", stderr
        ):
            try:
                self.helper.operator_probe_local_user(
                    [],
                    password_reader=read_password,
                    input_reader=reader,
                    fingerprint_probe=lambda _host, _port: (
                        CERTIFICATE_SHA256
                    ),
                    client_factory=self.operator_probe_client_factory(
                        events,
                        probe_error=probe_error,
                    ),
                )
            except BaseException as caught:
                captured = (
                    stdout.getvalue()
                    + stderr.getvalue()
                    + "\n".join(reader.prompts + password_prompts)
                )
                return caught, captured
        self.fail("operator capability probe unexpectedly succeeded")

    def enrollment_client_factory(
        self,
        *,
        config_path: Path,
        credentials_path: Path,
        events: list[str],
        fail_at: str | None = None,
        failure_detail: str | None = None,
    ):
        helper = self.helper

        class FakeEnrollmentClient:
            def __init__(self, binding) -> None:
                self.binding = binding
                events.append("client")

            @staticmethod
            def assert_not_activated() -> None:
                if config_path.exists() or credentials_path.exists():
                    raise AssertionError(
                        "enrollment activated files before verification"
                    )

            def login(self) -> str:
                self.assert_not_activated()
                events.append("login")
                if fail_at == "login":
                    raise helper.PublicError(
                        failure_detail or "Camera authentication failed"
                    )
                return PRIVATE_TOKEN

            def status(self, token: str) -> bool:
                self.assert_not_activated()
                if token != PRIVATE_TOKEN:
                    raise AssertionError("unexpected enrollment token")
                events.append("status")
                if fail_at == "status":
                    raise helper.PublicError(
                        failure_detail or "Camera status is unavailable"
                    )
                return True

            def snapshot(self, token: str) -> bytes:
                self.assert_not_activated()
                if token != PRIVATE_TOKEN:
                    raise AssertionError("unexpected enrollment token")
                events.append("snapshot")
                if fail_at == "snapshot":
                    raise helper.PublicError(
                        failure_detail
                        or "Captured camera image is invalid"
                    )
                return JPEG

            def logout(self, token: str) -> None:
                if token != PRIVATE_TOKEN:
                    raise AssertionError("unexpected enrollment token")
                events.append("logout")

        return FakeEnrollmentClient

    def run_enrollment(
        self,
        *,
        config_path: Path,
        credentials_path: Path,
        reader: ScriptedInput,
        client_factory,
        arguments: list[str] | None = None,
    ) -> tuple[dict[str, str | bool], str, list[str]]:
        stdout = TTYBuffer()
        stdin = TTYBuffer()
        password_prompts: list[str] = []

        def read_password(prompt: str) -> str:
            password_prompts.append(prompt)
            return PASSWORD

        with mock.patch.object(self.helper.sys, "stdin", stdin), mock.patch.object(
            self.helper.sys, "stdout", stdout
        ), mock.patch.object(
            self.helper.sys,
            "argv",
            ["reolink-camera", "operator-enroll"],
        ):
            result = self.helper.operator_enroll(
                [] if arguments is None else arguments,
                config_path=config_path,
                credentials_path=credentials_path,
                password_reader=read_password,
                input_reader=reader,
                fingerprint_probe=lambda host, port: (
                    CERTIFICATE_SHA256
                    if (host, port) == (HOST, 443)
                    else (_ for _ in ()).throw(
                        AssertionError("unexpected fingerprint endpoint")
                    )
                ),
                client_factory=client_factory,
            )
        return result, stdout.getvalue(), password_prompts

    def bootstrap_paths(self, label: str) -> tuple[Path, Path]:
        directory = self.root / label
        return directory / "config.json", directory / "credentials.json"

    def bootstrap_reader(
        self,
        *,
        trust: str = "TRUST",
        create: str = "CREATE",
    ) -> ScriptedInput:
        return ScriptedInput(
            [
                ALIAS,
                SITE,
                HOST,
                "443",
                "0",
                PRIVATE_ADMIN_NAME,
                trust,
                create,
            ]
        )

    def bootstrap_client_factories(
        self,
        events: list[str],
        *,
        levels: tuple[str, ...] | None = ("admin", "guest"),
        can_read: bool = True,
        can_write: bool = True,
        version: int = 1,
        existing_users: list[tuple[str, str]] | None = None,
        admin_login_error: BaseException | None = None,
        inspection_error: BaseException | None = None,
        add_error: BaseException | None = None,
        ambiguous_add_committed: bool = True,
        confirm_created: bool = True,
        guest_login_error: BaseException | None = None,
        guest_status_error: BaseException | None = None,
        guest_online: bool = True,
        guest_snapshot_error: BaseException | None = None,
        guest_snapshot: bytes = JPEG,
        delete_error: BaseException | None = None,
        delete_removes: bool = True,
    ):
        helper = self.helper
        users = list(
            existing_users
            if existing_users is not None
            else [(PRIVATE_ADMIN_NAME, "admin")]
        )
        state: dict[str, object] = {
            "users": users,
            "add_calls": 0,
            "delete_calls": 0,
            "guest_username": None,
            "guest_password": None,
            "delete_attempted": False,
        }

        def directory():
            return helper._LocalUserDirectory(
                users=tuple(state["users"]),
                levels=levels,
                username_minimum=1,
                username_maximum=31,
                password_minimum=1,
                password_maximum=31,
            )

        class FakeOperatorClient:
            def __init__(self, binding) -> None:
                self.binding = binding
                if (
                    binding.alias != ALIAS
                    or binding.site != SITE
                    or binding.host != HOST
                    or binding.channel != 0
                    or binding.username != PRIVATE_ADMIN_NAME
                    or binding.password != PRIVATE_ADMIN_PASSWORD
                ):
                    raise AssertionError("unexpected administrator binding")
                events.append("operator-client")

            def login(self) -> str:
                events.append("admin-login")
                if admin_login_error is not None:
                    raise admin_login_error
                return PRIVATE_TOKEN

            def _inspect_local_user_capability(
                self,
                token: str,
                username: str,
            ):
                if token != PRIVATE_TOKEN or username != PRIVATE_ADMIN_NAME:
                    raise AssertionError("unexpected capability identity")
                events.append("inspect")
                if inspection_error is not None:
                    raise inspection_error
                return helper._LocalUserCapability(
                    version=version,
                    permit=(4 if can_read else 0) | (2 if can_write else 0),
                    can_read=can_read,
                    can_write=can_write,
                    directory=directory() if can_read else None,
                )

            def add_guest_user(
                self,
                token: str,
                username: str,
                password: str,
            ) -> None:
                if token != PRIVATE_TOKEN:
                    raise AssertionError("unexpected administrator token")
                if not helper.GENERATED_GUEST_USERNAME_PATTERN.fullmatch(
                    username
                ):
                    raise AssertionError("invalid generated guest username")
                if not helper.GENERATED_GUEST_PASSWORD_PATTERN.fullmatch(
                    password
                ):
                    raise AssertionError("invalid generated guest password")
                events.append("add")
                state["add_calls"] = int(state["add_calls"]) + 1
                state["guest_username"] = username
                state["guest_password"] = password
                if add_error is not None:
                    if ambiguous_add_committed:
                        state["users"].append((username, "guest"))
                    raise add_error
                if confirm_created:
                    state["users"].append((username, "guest"))

            def _get_local_user_directory(self, token: str):
                if token != PRIVATE_TOKEN:
                    raise AssertionError("unexpected administrator token")
                if state["delete_attempted"]:
                    events.append("confirm-absent")
                else:
                    events.append("confirm-created")
                return directory()

            def delete_guest_user(self, token: str, username: str) -> None:
                if token != PRIVATE_TOKEN:
                    raise AssertionError("unexpected administrator token")
                if username != state["guest_username"]:
                    raise AssertionError("rollback targeted another account")
                events.append("delete")
                state["delete_calls"] = int(state["delete_calls"]) + 1
                state["delete_attempted"] = True
                if delete_error is not None:
                    raise delete_error
                if delete_removes:
                    state["users"] = [
                        user
                        for user in state["users"]
                        if user[0] != username
                    ]

            def logout(self, token: str) -> None:
                if token != PRIVATE_TOKEN:
                    raise AssertionError("unexpected administrator token")
                events.append("admin-logout")

        class FakeGuestClient:
            def __init__(self, binding) -> None:
                self.binding = binding
                if (
                    binding.username != state["guest_username"]
                    or binding.password != state["guest_password"]
                    or binding.username == PRIVATE_ADMIN_NAME
                    or binding.password == PRIVATE_ADMIN_PASSWORD
                ):
                    raise AssertionError("unexpected guest binding")
                events.append("guest-client")

            def login(self) -> str:
                events.append("guest-login")
                if guest_login_error is not None:
                    raise guest_login_error
                return PRIVATE_GUEST_TOKEN

            def status(self, token: str) -> bool:
                if token != PRIVATE_GUEST_TOKEN:
                    raise AssertionError("unexpected guest token")
                events.append("guest-status")
                if guest_status_error is not None:
                    raise guest_status_error
                return guest_online

            def snapshot(self, token: str) -> bytes:
                if token != PRIVATE_GUEST_TOKEN:
                    raise AssertionError("unexpected guest token")
                events.append("guest-snapshot")
                if guest_snapshot_error is not None:
                    raise guest_snapshot_error
                return guest_snapshot

            def logout(self, token: str) -> None:
                if token != PRIVATE_GUEST_TOKEN:
                    raise AssertionError("unexpected guest token")
                events.append("guest-logout")

        return FakeOperatorClient, FakeGuestClient, state

    def run_guest_bootstrap(
        self,
        *,
        config_path: Path,
        credentials_path: Path,
        reader: ScriptedInput,
        events: list[str],
        operator_client_factory,
        guest_client_factory,
        username_random_values: list[str] | None = None,
        activation_failure: bool = False,
        stdin_tty: bool = True,
        stdout_tty: bool = True,
        arguments: list[str] | None = None,
    ) -> tuple[
        object | None,
        BaseException | None,
        str,
        str,
        list[str],
        str,
    ]:
        stdout = TTYBuffer(tty=stdout_tty)
        stderr = TTYBuffer()
        stdin = TTYBuffer(tty=stdin_tty)
        password_prompts: list[str] = []
        random_16 = list(
            username_random_values
            if username_random_values is not None
            else ["0123456789abcdef0123456789abcdef"]
        )
        random_16.append("2" * 32)

        def token_hex(byte_count: int) -> str:
            if byte_count == 16:
                if not random_16:
                    raise AssertionError("unexpected 16-byte random request")
                return random_16.pop(0)
            if byte_count == 15:
                return GENERATED_GUEST_PASSWORD
            raise AssertionError(
                f"unexpected {byte_count}-byte random request"
            )

        def read_password(prompt: str) -> str:
            events.append("admin-password")
            password_prompts.append(prompt)
            return PRIVATE_ADMIN_PASSWORD

        def probe_fingerprint(host: str, port: int) -> str:
            if (host, port) != (HOST, 443):
                raise AssertionError("unexpected fingerprint endpoint")
            events.append("fingerprint")
            return CERTIFICATE_SHA256

        real_activate = self.helper._activate_registry_pair
        activation_calls = 0

        def activate(config, credentials, **kwargs) -> None:
            nonlocal activation_calls
            activation_calls += 1
            events.append(
                "activate" if activation_calls == 1 else "restore"
            )
            real_activate(config, credentials, **kwargs)
            if activation_failure and activation_calls == 1:
                raise self.helper.PublicError(
                    "private activation detail "
                    + PRIVATE_ADMIN_PASSWORD
                )

        result: object | None = None
        caught: BaseException | None = None
        argv = "reolink-camera operator-bootstrap-guest"
        with mock.patch.object(
            self.helper.sys, "stdin", stdin
        ), mock.patch.object(
            self.helper.sys, "stdout", stdout
        ), mock.patch.object(
            self.helper.sys, "stderr", stderr
        ), mock.patch.object(
            self.helper.sys, "argv", argv.split()
        ), mock.patch.object(
            self.helper.secrets, "token_hex", side_effect=token_hex
        ), mock.patch.object(
            self.helper, "_activate_registry_pair", side_effect=activate
        ):
            try:
                result = self.helper.operator_bootstrap_guest(
                    [] if arguments is None else arguments,
                    config_path=config_path,
                    credentials_path=credentials_path,
                    password_reader=read_password,
                    input_reader=reader,
                    fingerprint_probe=probe_fingerprint,
                    operator_client_factory=operator_client_factory,
                    guest_client_factory=guest_client_factory,
                )
            except BaseException as error:
                caught = error
        return (
            result,
            caught,
            stdout.getvalue(),
            stderr.getvalue(),
            password_prompts,
            argv,
        )

    def test_certificate_pin_is_checked_before_sending_credentials(self) -> None:
        factory = FakeConnectionFactory(
            [login_response()],
            peer_certificate=b"wrong-peer-certificate",
        )
        client = self.make_client(factory)

        with self.assertRaises(self.helper.PublicError) as caught:
            client.login()

        self.assert_public_error_without_secrets(caught)
        self.assertGreaterEqual(len(factory.connections), 1)
        self.assertEqual(factory.requests, [])

    def test_login_credentials_appear_only_in_post_body(self) -> None:
        factory = FakeConnectionFactory([login_response(), status_response()])
        client = self.make_client(factory)
        stdout = io.StringIO()
        stderr = io.StringIO()

        with redirect_stdout(stdout), redirect_stderr(stderr):
            token = client.login()
            client.status(token)

        self.assertGreaterEqual(len(factory.requests), 2)
        login = factory.requests[0]
        self.assertEqual(login["method"], "POST")
        body = login["body"]
        if isinstance(body, str):
            body_bytes = body.encode("utf-8")
        else:
            self.assertIsInstance(body, bytes)
            body_bytes = body
        login_payload = json.loads(body_bytes.decode("utf-8"))
        self.assertEqual(
            login_payload,
            [
                {
                    "cmd": "Login",
                    "action": 0,
                    "param": {
                        "User": {
                            "userName": USERNAME,
                            "password": PASSWORD,
                            "Version": "0",
                        }
                    },
                }
            ],
        )

        all_urls = "\n".join(str(call["url"]) for call in factory.requests)
        all_headers = json.dumps(
            [call["headers"] for call in factory.requests],
            sort_keys=True,
        )
        for secret in (USERNAME, PASSWORD):
            self.assertNotIn(secret, all_urls)
            self.assertNotIn(secret, all_headers)
            self.assertNotIn(secret, stdout.getvalue() + stderr.getvalue())
            self.assertNotIn(secret, " ".join(sys.argv))
        self.assertIn(PRIVATE_TOKEN, all_urls)
        for later in factory.requests[1:]:
            body_text = (
                later["body"].decode("utf-8")
                if isinstance(later["body"], bytes)
                else str(later["body"] or "")
            )
            self.assertNotIn(USERNAME, body_text)
            self.assertNotIn(PASSWORD, body_text)

    def test_operator_user_mutations_have_exact_set_action_bodies(
        self,
    ) -> None:
        generated_username = "openclaw01234567"
        generated_password = "0123456789abcdef0123456789abcd"
        factory = FakeConnectionFactory(
            [
                user_mutation_response("AddUser"),
                user_mutation_response("DelUser"),
            ]
        )
        client = self.helper.ReolinkOperatorClient(
            self.load_binding(),
            connection_factory=factory,
        )

        client.add_guest_user(
            PRIVATE_TOKEN,
            generated_username,
            generated_password,
        )
        client.delete_guest_user(PRIVATE_TOKEN, generated_username)

        self.assertEqual(len(factory.requests), 2)
        bodies = [
            json.loads(
                (
                    request["body"].decode("utf-8")
                    if isinstance(request["body"], bytes)
                    else str(request["body"])
                )
            )
            for request in factory.requests
        ]
        self.assertEqual(
            bodies,
            [
                [
                    {
                        "cmd": "AddUser",
                        "action": 0,
                        "param": {
                            "User": {
                                "userName": generated_username,
                                "password": generated_password,
                                "level": "guest",
                            }
                        },
                    }
                ],
                [
                    {
                        "cmd": "DelUser",
                        "action": 0,
                        "param": {
                            "User": {
                                "userName": generated_username,
                            }
                        },
                    }
                ],
            ],
        )
        self.assertIn("cmd=AddUser", str(factory.requests[0]["url"]))
        self.assertIn("cmd=DelUser", str(factory.requests[1]["url"]))

    def test_local_user_capability_supported_is_sanitized_and_exact(self) -> None:
        factory = FakeConnectionFactory(
            [
                ability_response(permit=6, version=1),
                users_response(
                    [
                        {
                            "userName": PRIVATE_ADMIN_NAME,
                            "level": "admin",
                        },
                        {
                            "userName": PRIVATE_SECONDARY_NAME,
                            "level": "guest",
                        },
                    ]
                ),
            ]
        )
        client = self.make_client(factory)
        stdout = io.StringIO()
        stderr = io.StringIO()

        with redirect_stdout(stdout), redirect_stderr(stderr):
            result = client.probe_local_user_capability(
                PRIVATE_TOKEN,
                PRIVATE_ADMIN_NAME,
            )

        self.assertEqual(
            result,
            {
                "authenticated": True,
                "guestAccountAdvertised": True,
                "localUserManagement": "supported",
                "userListingAccessible": True,
                "userWriteAdvertised": True,
                "existingLocalUsers": 2,
            },
        )
        self.assertEqual(len(factory.requests), 2)
        self.assertEqual(
            [request["method"] for request in factory.requests],
            ["POST", "POST"],
        )
        bodies = [
            json.loads(
                (
                    request["body"].decode("utf-8")
                    if isinstance(request["body"], bytes)
                    else str(request["body"])
                )
            )
            for request in factory.requests
        ]
        self.assertEqual(
            bodies,
            [
                [
                    {
                        "cmd": "GetAbility",
                        "action": 0,
                        "param": {
                            "User": {
                                "userName": PRIVATE_ADMIN_NAME,
                            }
                        },
                    }
                ],
                [{"cmd": "GetUser", "action": 1}],
            ],
        )
        self.assertIn("cmd=GetAbility", str(factory.requests[0]["url"]))
        self.assertIn("cmd=GetUser", str(factory.requests[1]["url"]))

        public_text = (
            json.dumps(result, sort_keys=True)
            + stdout.getvalue()
            + stderr.getvalue()
        )
        request_urls = "\n".join(
            str(request["url"]) for request in factory.requests
        )
        for private in (
            HOST,
            PASSWORD,
            PRIVATE_TOKEN,
            PRIVATE_ADMIN_NAME,
            PRIVATE_SECONDARY_NAME,
            CERTIFICATE_SHA256,
        ):
            self.assertNotIn(private, public_text)
        for private in (PASSWORD, PRIVATE_ADMIN_NAME, PRIVATE_SECONDARY_NAME):
            self.assertNotIn(private, request_urls)
        self.assertNotIn(
            PASSWORD,
            json.dumps(factory.requests, default=str),
        )

    def test_local_user_capability_unsupported_skips_user_listing(self) -> None:
        factory = FakeConnectionFactory(
            [ability_response(permit=0, version=0)]
        )

        result = self.make_client(factory).probe_local_user_capability(
            PRIVATE_TOKEN,
            PRIVATE_ADMIN_NAME,
        )

        self.assertEqual(
            result,
            {
                "authenticated": True,
                "guestAccountAdvertised": False,
                "localUserManagement": "unsupported",
                "userListingAccessible": False,
                "userWriteAdvertised": False,
            },
        )
        self.assertEqual(len(factory.requests), 1)
        self.assertIn("cmd=GetAbility", str(factory.requests[0]["url"]))
        self.assertNotIn(
            "GetUser",
            json.dumps(factory.requests, default=str),
        )

    def test_local_user_range_without_guest_is_unsupported(self) -> None:
        factory = FakeConnectionFactory(
            [
                ability_response(),
                users_response(
                    [
                        {
                            "userName": PRIVATE_ADMIN_NAME,
                            "level": "admin",
                        }
                    ],
                    user_range={
                        "level": ["admin"],
                        "userName": {"minLen": 1, "maxLen": 31},
                        "password": {"minLen": 1, "maxLen": 31},
                    },
                ),
            ]
        )

        result = self.make_client(factory).probe_local_user_capability(
            PRIVATE_TOKEN,
            PRIVATE_ADMIN_NAME,
        )

        self.assertEqual(
            result,
            {
                "authenticated": True,
                "guestAccountAdvertised": False,
                "localUserManagement": "unsupported",
                "userListingAccessible": True,
                "userWriteAdvertised": True,
                "existingLocalUsers": 1,
            },
        )

    def test_missing_local_user_range_is_inconclusive(self) -> None:
        factory = FakeConnectionFactory(
            [
                ability_response(),
                users_response(
                    [
                        {
                            "userName": PRIVATE_ADMIN_NAME,
                            "level": "admin",
                        }
                    ],
                    user_range=None,
                ),
            ]
        )

        result = self.make_client(factory).probe_local_user_capability(
            PRIVATE_TOKEN,
            PRIVATE_ADMIN_NAME,
        )

        self.assertEqual(
            result,
            {
                "authenticated": True,
                "guestAccountAdvertised": False,
                "localUserManagement": "inconclusive",
                "userListingAccessible": True,
                "userWriteAdvertised": True,
                "existingLocalUsers": 1,
            },
        )

    def test_omitted_level_range_parses_as_unknown_and_projects_inconclusive(
        self,
    ) -> None:
        factory = FakeConnectionFactory(
            [
                ability_response(),
                users_response(
                    [
                        {
                            "userName": PRIVATE_ADMIN_NAME,
                            "level": "admin",
                        }
                    ],
                    user_range={
                        "userName": {"minLen": 1, "maxLen": 31},
                        "password": {"minLen": 1, "maxLen": 31},
                    },
                ),
            ]
        )
        client = self.make_client(factory)

        inspection = client._inspect_local_user_capability(
            PRIVATE_TOKEN,
            PRIVATE_ADMIN_NAME,
        )

        self.assertIsNotNone(inspection.directory)
        assert inspection.directory is not None
        self.assertIsNone(inspection.directory.levels)
        self.assertEqual(
            client._project_local_user_capability(inspection),
            {
                "authenticated": True,
                "guestAccountAdvertised": False,
                "localUserManagement": "inconclusive",
                "userListingAccessible": True,
                "userWriteAdvertised": True,
                "existingLocalUsers": 1,
            },
        )

    def test_invalid_local_user_ranges_fail_safely(self) -> None:
        valid_username_range = {"minLen": 1, "maxLen": 31}
        valid_password_range = {"minLen": 1, "maxLen": 31}
        invalid_ranges = (
            {
                "level": ["admin", "guest"],
                "userName": {"minLen": 0, "maxLen": 31},
                "password": valid_password_range,
            },
            {
                "level": ["admin", "guest"],
                "userName": valid_username_range,
                "password": {"minLen": 32, "maxLen": 31},
            },
            {
                "level": [],
                "userName": valid_username_range,
                "password": valid_password_range,
            },
            {
                "level": ["admin", "guest"],
                "userName": valid_username_range,
            },
            {
                "level": ["admin", "guest"],
                "userName": {"minLen": True, "maxLen": 31},
                "password": valid_password_range,
                "privateDetail": PRIVATE_SERIAL,
            },
        )
        for user_range in invalid_ranges:
            with self.subTest(user_range=user_range):
                factory = FakeConnectionFactory(
                    [
                        ability_response(),
                        users_response(
                            [
                                {
                                    "userName": PRIVATE_ADMIN_NAME,
                                    "level": "admin",
                                }
                            ],
                            user_range=user_range,
                        ),
                    ]
                )

                with self.assertRaises(self.helper.PublicError) as caught:
                    self.make_client(factory).probe_local_user_capability(
                        PRIVATE_TOKEN,
                        PRIVATE_ADMIN_NAME,
                    )

                error = str(caught.exception)
                self.assertTrue(error)
                for private in (
                    HOST,
                    PASSWORD,
                    PRIVATE_TOKEN,
                    PRIVATE_ADMIN_NAME,
                    PRIVATE_SERIAL,
                    CERTIFICATE_SHA256,
                ):
                    self.assertNotIn(private, error)

    def test_local_user_capacity_at_twenty_is_full_not_supported(self) -> None:
        users = [
            {
                "userName": f"fixture-user-{index:02d}",
                "level": "admin" if index == 0 else "guest",
            }
            for index in range(20)
        ]
        factory = FakeConnectionFactory(
            [ability_response(), users_response(users)]
        )

        result = self.make_client(factory).probe_local_user_capability(
            PRIVATE_TOKEN,
            PRIVATE_ADMIN_NAME,
        )

        self.assertEqual(
            result,
            {
                "authenticated": True,
                "guestAccountAdvertised": True,
                "localUserManagement": "full",
                "userListingAccessible": True,
                "userWriteAdvertised": True,
                "existingLocalUsers": 20,
            },
        )
        self.assertNotEqual(result["localUserManagement"], "supported")

    def test_local_user_capability_rejects_malformed_safe_responses(self) -> None:
        malformed_cases = (
            (
                "missing-ability",
                [
                    FakeResponse(
                        [
                            {
                                "cmd": "GetAbility",
                                "code": 0,
                                "value": {"private": PRIVATE_SERIAL},
                            }
                        ]
                    )
                ],
            ),
            (
                "boolean-permit",
                [ability_response(permit=True, version=1)],
            ),
            (
                "user-not-list",
                [
                    ability_response(),
                    users_response(
                        {
                            "userName": PRIVATE_SECONDARY_NAME,
                            "level": "admin",
                        }
                    ),
                ],
            ),
            (
                "invalid-user-entry",
                [
                    ability_response(),
                    users_response(
                        [
                            {
                                "userName": PRIVATE_SECONDARY_NAME,
                                "level": "owner",
                            }
                        ]
                    ),
                ],
            ),
        )
        for label, responses in malformed_cases:
            with self.subTest(label=label):
                factory = FakeConnectionFactory(responses)
                with self.assertRaises(self.helper.PublicError) as caught:
                    self.make_client(factory).probe_local_user_capability(
                        PRIVATE_TOKEN,
                        PRIVATE_ADMIN_NAME,
                    )
                error = str(caught.exception)
                self.assertTrue(error)
                for private in (
                    HOST,
                    PASSWORD,
                    PRIVATE_TOKEN,
                    PRIVATE_ADMIN_NAME,
                    PRIVATE_SECONDARY_NAME,
                    PRIVATE_SERIAL,
                    CERTIFICATE_SHA256,
                ):
                    self.assertNotIn(private, error)

    def test_capability_request_failures_use_exact_fixed_error(self) -> None:
        failures = (
            FakeResponse(
                PRIVATE_ADMIN_PASSWORD.encode("utf-8"),
                status=503,
                content_type="text/plain",
            ),
            FakeResponse(
                [
                    {
                        "cmd": "GetAbility",
                        "code": 1,
                        "error": {
                            "detail": PRIVATE_ADMIN_PASSWORD,
                            "rspCode": -6,
                        },
                    }
                ]
            ),
        )
        for failure in failures:
            with self.subTest(status=failure.status):
                factory = FakeConnectionFactory([failure])
                with self.assertRaises(self.helper.PublicError) as caught:
                    self.make_client(factory).probe_local_user_capability(
                        PRIVATE_TOKEN,
                        PRIVATE_ADMIN_NAME,
                    )

                self.assertEqual(
                    str(caught.exception),
                    "Camera capability request failed",
                )
                self.assertNotIn(
                    PRIVATE_ADMIN_PASSWORD,
                    str(caught.exception),
                )

    def test_malformed_capability_response_keeps_exact_response_error(
        self,
    ) -> None:
        malformed = (
            FakeResponse(
                [
                    {
                        "cmd": "GetAbility",
                        "code": 0,
                        "value": {"privateDetail": PRIVATE_SERIAL},
                    }
                ]
            ),
            ability_response(permit=True, version=1),
        )
        for response in malformed:
            with self.subTest(response=response):
                factory = FakeConnectionFactory([response])
                with self.assertRaises(self.helper.PublicError) as caught:
                    self.make_client(factory).probe_local_user_capability(
                        PRIVATE_TOKEN,
                        PRIVATE_ADMIN_NAME,
                    )

                self.assertEqual(
                    str(caught.exception),
                    "Camera capability response is invalid",
                )
                self.assertNotIn(PRIVATE_SERIAL, str(caught.exception))

    def test_user_list_request_failures_use_exact_fixed_error(self) -> None:
        failures = (
            FakeResponse(
                PRIVATE_ADMIN_PASSWORD.encode("utf-8"),
                status=503,
                content_type="text/plain",
            ),
            FakeResponse(
                [
                    {
                        "cmd": "GetUser",
                        "code": 1,
                        "error": {
                            "detail": PRIVATE_ADMIN_PASSWORD,
                            "rspCode": -6,
                        },
                    }
                ]
            ),
        )
        for failure in failures:
            with self.subTest(status=failure.status):
                factory = FakeConnectionFactory(
                    [ability_response(), failure]
                )
                with self.assertRaises(self.helper.PublicError) as caught:
                    self.make_client(factory).probe_local_user_capability(
                        PRIVATE_TOKEN,
                        PRIVATE_ADMIN_NAME,
                    )

                self.assertEqual(
                    str(caught.exception),
                    "Camera user-list request failed",
                )
                self.assertNotIn(
                    PRIVATE_ADMIN_PASSWORD,
                    str(caught.exception),
                )

    def test_malformed_user_response_keeps_exact_response_error(self) -> None:
        malformed = (
            users_response(
                {
                    "userName": PRIVATE_SECONDARY_NAME,
                    "level": "guest",
                }
            ),
            users_response(
                [
                    {
                        "userName": PRIVATE_SECONDARY_NAME,
                        "level": "owner",
                    }
                ]
            ),
        )
        for response in malformed:
            with self.subTest(response=response):
                factory = FakeConnectionFactory(
                    [ability_response(), response]
                )
                with self.assertRaises(self.helper.PublicError) as caught:
                    self.make_client(factory).probe_local_user_capability(
                        PRIVATE_TOKEN,
                        PRIVATE_ADMIN_NAME,
                    )

                self.assertEqual(
                    str(caught.exception),
                    "Camera user response is invalid",
                )
                self.assertNotIn(
                    PRIVATE_SECONDARY_NAME,
                    str(caught.exception),
                )

    def test_status_exposes_safe_availability_and_power_fields(self) -> None:
        factory = FakeConnectionFactory(
            [
                login_response(),
                status_response(),
                battery_response(),
                logout_response(),
            ]
        )
        status_payload = self.helper.camera_status(
            ALIAS,
            config_path=self.config_path,
            credentials_path=self.credentials_path,
            client_factory=lambda binding: self.helper.ReolinkClient(
                binding,
                connection_factory=factory,
            ),
        )

        self.assertEqual(
            status_payload,
            {
                "alias": ALIAS,
                "site": SITE,
                "available": True,
                "batteryPercent": 74,
                "chargeStatus": "charging",
                "temperatureC": 22,
            },
        )
        serialized = json.dumps(status_payload, sort_keys=True)
        for private in (
            HOST,
            USERNAME,
            PASSWORD,
            PRIVATE_TOKEN,
            PRIVATE_SERIAL,
            "private-device-name",
            "private-device-uid",
            CERTIFICATE_SHA256,
            '"channel"',
        ):
            self.assertNotIn(private, serialized)
        self.assertIn("Logout", json.dumps(factory.requests, default=str))

    def test_spotlight_control_changes_only_manual_state_and_verifies(self) -> None:
        factory = FakeConnectionFactory(
            [
                login_response(),
                spotlight_response(state=0),
                set_spotlight_response(),
                spotlight_response(state=1),
                logout_response(),
            ]
        )
        sleeps: list[float] = []

        result = self.helper.camera_spotlight(
            ALIAS,
            "on",
            config_path=self.config_path,
            credentials_path=self.credentials_path,
            client_factory=lambda binding: self.helper.ReolinkClient(
                binding,
                connection_factory=factory,
            ),
            sleeper=sleeps.append,
        )

        self.assertEqual(
            result,
            {
                "alias": ALIAS,
                "site": SITE,
                "control": "spotlight",
                "state": "on",
                "changed": True,
            },
        )
        self.assertEqual(
            sleeps,
            [
                self.helper.SPOTLIGHT_WAKE_DELAY_SECONDS,
                self.helper.SPOTLIGHT_READBACK_DELAY_SECONDS,
            ],
        )
        requests = [
            json.loads(request["body"].decode("utf-8"))[0]
            for request in factory.requests
            if request["method"] == "POST"
        ]
        mutation = next(
            request for request in requests if request["cmd"] == "SetWhiteLed"
        )
        self.assertEqual(
            mutation,
            {
                "cmd": "SetWhiteLed",
                "action": 0,
                "param": {
                    "WhiteLed": {
                        "channel": 0,
                        "state": 1,
                    }
                },
            },
        )
        serialized = json.dumps(result, sort_keys=True)
        for private in (
            HOST,
            USERNAME,
            PASSWORD,
            PRIVATE_TOKEN,
            PRIVATE_SERIAL,
            '"channel"',
            '"bright"',
            '"mode"',
        ):
            self.assertNotIn(private, serialized)
        self.assertEqual(requests[-1]["cmd"], "Logout")

    def test_spotlight_status_and_same_state_do_not_mutate(self) -> None:
        for requested in ("status", "off"):
            with self.subTest(requested=requested):
                factory = FakeConnectionFactory(
                    [
                        login_response(),
                        spotlight_response(state=0),
                        logout_response(),
                    ]
                )
                result = self.helper.camera_spotlight(
                    ALIAS,
                    requested,
                    config_path=self.config_path,
                    credentials_path=self.credentials_path,
                    client_factory=lambda binding: self.helper.ReolinkClient(
                        binding,
                        connection_factory=factory,
                    ),
                    sleeper=lambda _seconds: self.fail(
                        "same-state spotlight request slept"
                    ),
                )

                self.assertEqual(result["state"], "off")
                if requested == "off":
                    self.assertIs(result["changed"], False)
                else:
                    self.assertNotIn("changed", result)
                self.assertNotIn(
                    "SetWhiteLed",
                    json.dumps(factory.requests, default=str),
                )

    def test_spotlight_rejects_unverified_readback(self) -> None:
        factory = FakeConnectionFactory(
            [
                login_response(),
                spotlight_response(state=0),
                set_spotlight_response(),
                spotlight_response(state=0),
                logout_response(),
            ]
        )

        with self.assertRaises(self.helper.PublicError) as caught:
            self.helper.camera_spotlight(
                ALIAS,
                "on",
                config_path=self.config_path,
                credentials_path=self.credentials_path,
                client_factory=lambda binding: self.helper.ReolinkClient(
                    binding,
                    connection_factory=factory,
                ),
                sleeper=lambda _seconds: None,
            )

        self.assertEqual(
            str(caught.exception),
            "Camera spotlight control could not be verified",
        )
        self.assertIn("Logout", json.dumps(factory.requests, default=str))

    def test_transport_and_api_errors_are_fixed_and_sanitized(self) -> None:
        failures = (
            FakeConnectionFactory(
                [
                    FakeResponse(
                        PASSWORD.encode("utf-8"),
                        status=503,
                        content_type="text/plain",
                    )
                ]
            ),
            FakeConnectionFactory(
                [
                    FakeResponse(
                        b"not-json-" + PASSWORD.encode("utf-8"),
                        content_type="application/json",
                    )
                ]
            ),
            FakeConnectionFactory(
                [
                    FakeResponse(
                        [
                            {
                                "cmd": "Login",
                                "code": 1,
                                "error": {
                                    "detail": PASSWORD,
                                    "rspCode": -6,
                                },
                            }
                        ]
                    )
                ]
            ),
        )
        for factory in failures:
            with self.subTest(factory=factory):
                with self.assertRaises(self.helper.PublicError) as caught:
                    self.make_client(factory).login()
                self.assert_public_error_without_secrets(caught)

    def test_operator_enrollment_refuses_without_an_attended_tty(self) -> None:
        config_path, credentials_path = self.enrollment_paths("non-tty")
        calls: list[str] = []

        def forbidden(*_args, **_kwargs):
            calls.append("called")
            raise AssertionError("enrollment continued without a TTY")

        with mock.patch.object(
            self.helper.sys,
            "stdin",
            TTYBuffer(tty=False),
        ), mock.patch.object(
            self.helper.sys,
            "stdout",
            TTYBuffer(tty=False),
        ):
            with self.assertRaises(self.helper.PublicError) as caught:
                self.helper.operator_enroll(
                    [],
                    config_path=config_path,
                    credentials_path=credentials_path,
                    password_reader=forbidden,
                    input_reader=forbidden,
                    fingerprint_probe=forbidden,
                    client_factory=forbidden,
                )

        self.assert_public_error_without_secrets(caught)
        self.assertEqual(calls, [])
        self.assertFalse(config_path.exists())
        self.assertFalse(credentials_path.exists())

    def test_operator_enrollment_prompts_for_private_values_not_argv(self) -> None:
        config_path, credentials_path = self.enrollment_paths("prompted")
        events: list[str] = []
        reader = self.enrollment_reader()
        client_factory = self.enrollment_client_factory(
            config_path=config_path,
            credentials_path=credentials_path,
            events=events,
        )

        _result, output, password_prompts = self.run_enrollment(
            config_path=config_path,
            credentials_path=credentials_path,
            reader=reader,
            client_factory=client_factory,
        )

        self.assertEqual(reader.values, [])
        self.assertEqual(len(password_prompts), 1)
        prompts = "\n".join(reader.prompts + password_prompts)
        argv = "reolink-camera operator-enroll"
        for private in (HOST, USERNAME, PASSWORD):
            self.assertNotIn(private, argv)
            self.assertNotIn(private, prompts)
            self.assertNotIn(private, output)
        for unsupported in (
            ["--host", HOST],
            ["--username", USERNAME],
            ["--password", PASSWORD],
        ):
            with self.subTest(unsupported=unsupported):
                with self.assertRaises(self.helper.PublicError):
                    self.helper._parse_operator_enroll(unsupported)

    def test_operator_enrollment_requires_exact_trust_confirmation(self) -> None:
        for index, confirmation in enumerate(
            ("trust", "TRUST ", "yes", "")
        ):
            with self.subTest(confirmation=confirmation):
                config_path, credentials_path = self.enrollment_paths(
                    f"trust-{index}"
                )
                reader = self.enrollment_reader(confirmation)
                client_calls: list[object] = []

                def forbidden_client(binding):
                    client_calls.append(binding)
                    raise AssertionError(
                        "client created without exact TRUST confirmation"
                    )

                stdout = TTYBuffer()
                with mock.patch.object(
                    self.helper.sys, "stdin", TTYBuffer()
                ), mock.patch.object(
                    self.helper.sys, "stdout", stdout
                ):
                    with self.assertRaises(self.helper.PublicError) as caught:
                        self.helper.operator_enroll(
                            [],
                            config_path=config_path,
                            credentials_path=credentials_path,
                            password_reader=lambda _prompt: PASSWORD,
                            input_reader=reader,
                            fingerprint_probe=lambda host, port: (
                                CERTIFICATE_SHA256
                            ),
                            client_factory=forbidden_client,
                        )

                self.assert_public_error_without_secrets(caught)
                self.assertEqual(client_calls, [])
                self.assertFalse(config_path.exists())
                self.assertFalse(credentials_path.exists())

    def test_enrollment_verifies_then_activates_private_reloadable_pair(
        self,
    ) -> None:
        config_path, credentials_path = self.enrollment_paths("success")
        events: list[str] = []
        client_factory = self.enrollment_client_factory(
            config_path=config_path,
            credentials_path=credentials_path,
            events=events,
        )
        original_activate = self.helper._activate_registry_pair

        def record_activation(*args, **kwargs):
            events.append("activate")
            return original_activate(*args, **kwargs)

        with mock.patch.object(
            self.helper,
            "_activate_registry_pair",
            side_effect=record_activation,
        ):
            result, _output, _password_prompts = self.run_enrollment(
                config_path=config_path,
                credentials_path=credentials_path,
                reader=self.enrollment_reader(),
                client_factory=client_factory,
            )

        self.assertEqual(
            events,
            ["client", "login", "status", "snapshot", "logout", "activate"],
        )
        self.assertEqual(
            result,
            {"alias": ALIAS, "site": SITE, "configured": True},
        )
        self.assertEqual(stat.S_IMODE(config_path.stat().st_mode), 0o600)
        self.assertEqual(
            stat.S_IMODE(credentials_path.stat().st_mode),
            0o600,
        )
        self.assertEqual(config_path.stat().st_nlink, 1)
        self.assertEqual(credentials_path.stat().st_nlink, 1)
        config = json.loads(config_path.read_text(encoding="utf-8"))
        credentials = json.loads(
            credentials_path.read_text(encoding="utf-8")
        )
        self.assertRegex(config["generation"], r"^[0-9a-f]{32}$")
        self.assertEqual(config["generation"], credentials["generation"])
        self.assertEqual(config["version"], 1)
        self.assertEqual(credentials["version"], 1)

        reloaded = self.helper.load_binding(
            ALIAS,
            config_path=config_path,
            credentials_path=credentials_path,
        )
        self.assertEqual(
            (
                reloaded.alias,
                reloaded.site,
                reloaded.host,
                reloaded.port,
                reloaded.channel,
                reloaded.tls_sha256,
                reloaded.username,
                reloaded.password,
            ),
            (
                ALIAS,
                SITE,
                HOST,
                443,
                0,
                CERTIFICATE_SHA256,
                USERNAME,
                PASSWORD,
            ),
        )

    def test_failed_enrollment_verification_writes_nothing(self) -> None:
        for fail_at in ("login", "status", "snapshot"):
            with self.subTest(fail_at=fail_at):
                config_path, credentials_path = self.enrollment_paths(
                    f"failure-{fail_at}"
                )
                events: list[str] = []
                client_factory = self.enrollment_client_factory(
                    config_path=config_path,
                    credentials_path=credentials_path,
                    events=events,
                    fail_at=fail_at,
                )
                reader = self.enrollment_reader()
                with self.assertRaises(self.helper.PublicError) as caught:
                    self.run_enrollment(
                        config_path=config_path,
                        credentials_path=credentials_path,
                        reader=reader,
                        client_factory=client_factory,
                    )

                self.assert_public_error_without_secrets(caught)
                self.assertFalse(config_path.exists())
                self.assertFalse(credentials_path.exists())
                self.assertNotIn("activate", events)
                if fail_at == "login":
                    self.assertNotIn("logout", events)
                else:
                    self.assertIn("logout", events)

    def test_enrollment_translates_live_failures_to_stage_safe_messages(
        self,
    ) -> None:
        expected_by_stage = {
            "login": "Camera login verification failed",
            "status": "Camera status verification failed",
            "snapshot": "Camera snapshot verification failed",
        }
        for fail_at, expected in expected_by_stage.items():
            with self.subTest(fail_at=fail_at):
                config_path, credentials_path = self.enrollment_paths(
                    f"translated-{fail_at}"
                )
                events: list[str] = []
                private_detail = (
                    f"{fail_at}: {HOST} {USERNAME} {PASSWORD} "
                    f"{PRIVATE_TOKEN} {PRIVATE_SERIAL}"
                )
                client_factory = self.enrollment_client_factory(
                    config_path=config_path,
                    credentials_path=credentials_path,
                    events=events,
                    fail_at=fail_at,
                    failure_detail=private_detail,
                )

                with self.assertRaises(self.helper.PublicError) as caught:
                    self.run_enrollment(
                        config_path=config_path,
                        credentials_path=credentials_path,
                        reader=self.enrollment_reader(),
                        client_factory=client_factory,
                    )

                self.assertEqual(str(caught.exception), expected)
                self.assertNotIn(private_detail, str(caught.exception))
                self.assertFalse(config_path.exists())
                self.assertFalse(credentials_path.exists())

    def test_operator_capability_probe_refuses_without_tty(self) -> None:
        calls: list[str] = []

        def forbidden(*_args, **_kwargs):
            calls.append("called")
            raise AssertionError("operator probe continued without a TTY")

        with mock.patch.object(
            self.helper.sys,
            "stdin",
            TTYBuffer(tty=False),
        ), mock.patch.object(
            self.helper.sys,
            "stdout",
            TTYBuffer(tty=False),
        ):
            with self.assertRaises(self.helper.PublicError) as caught:
                self.helper.operator_probe_local_user(
                    [],
                    password_reader=forbidden,
                    input_reader=forbidden,
                    fingerprint_probe=forbidden,
                    client_factory=forbidden,
                )

        self.assert_public_error_without_secrets(caught)
        self.assertEqual(calls, [])

    def test_operator_capability_probe_requires_exact_trust(self) -> None:
        for confirmation in ("trust", "TRUST ", "yes", ""):
            with self.subTest(confirmation=confirmation):
                reader = self.operator_probe_reader(confirmation)
                client_calls: list[object] = []
                stdout = TTYBuffer()
                stderr = TTYBuffer()
                password_prompts: list[str] = []

                def read_password(prompt: str) -> str:
                    password_prompts.append(prompt)
                    return PRIVATE_ADMIN_PASSWORD

                def forbidden_client(binding):
                    client_calls.append(binding)
                    raise AssertionError(
                        "client created without exact TRUST confirmation"
                    )

                with mock.patch.object(
                    self.helper.sys, "stdin", TTYBuffer()
                ), mock.patch.object(
                    self.helper.sys, "stdout", stdout
                ), mock.patch.object(
                    self.helper.sys, "stderr", stderr
                ):
                    with self.assertRaises(self.helper.PublicError) as caught:
                        self.helper.operator_probe_local_user(
                            [],
                            password_reader=read_password,
                            input_reader=reader,
                            fingerprint_probe=lambda _host, _port: (
                                CERTIFICATE_SHA256
                            ),
                            client_factory=forbidden_client,
                        )

                self.assert_public_error_without_secrets(caught)
                self.assertEqual(client_calls, [])
                captured = (
                    stdout.getvalue()
                    + stderr.getvalue()
                    + "\n".join(reader.prompts + password_prompts)
                )
                self.assertNotIn(PRIVATE_ADMIN_PASSWORD, captured)
                self.assertNotIn(PRIVATE_TOKEN, captured)

    def test_operator_capability_probe_surfaces_only_allowlisted_diagnostics(
        self,
    ) -> None:
        allowlisted = (
            "Camera capability request failed",
            "Camera capability response is invalid",
            "Camera user-list request failed",
            "Camera user response is invalid",
        )
        for message in allowlisted:
            with self.subTest(message=message):
                events: list[str] = []
                caught, captured = self.run_operator_probe_failure(
                    self.helper.PublicError(message),
                    events,
                )

                self.assertIsInstance(caught, self.helper.PublicError)
                self.assertEqual(str(caught), message)
                self.assertEqual(
                    events,
                    ["client", "login", "probe", "logout"],
                )
                self.assertNotIn(PRIVATE_ADMIN_PASSWORD, captured)
                self.assertNotIn(PRIVATE_TOKEN, captured)

    def test_operator_capability_probe_sanitizes_unexpected_failures(
        self,
    ) -> None:
        private_detail = (
            f"{HOST} {PRIVATE_ADMIN_NAME} {PRIVATE_ADMIN_PASSWORD} "
            f"{PRIVATE_TOKEN} {PRIVATE_SERIAL}"
        )
        cases = (
            (
                self.helper.PublicError(private_detail),
                "Camera local-user capability probe failed",
            ),
            (
                RuntimeError(private_detail),
                "Camera capability probe failed",
            ),
        )
        for failure, expected in cases:
            with self.subTest(expected=expected):
                events: list[str] = []
                caught, captured = self.run_operator_probe_failure(
                    failure,
                    events,
                )

                self.assertIsInstance(caught, self.helper.PublicError)
                self.assertEqual(str(caught), expected)
                self.assertNotIn(private_detail, str(caught))
                self.assertEqual(
                    events,
                    ["client", "login", "probe", "logout"],
                )
                for private in (
                    HOST,
                    PRIVATE_ADMIN_NAME,
                    PRIVATE_ADMIN_PASSWORD,
                    PRIVATE_TOKEN,
                    PRIVATE_SERIAL,
                ):
                    self.assertNotIn(private, str(caught))
                    self.assertNotIn(private, captured)

    def test_operator_capability_probe_is_sanitized_logout_only(self) -> None:
        events: list[str] = []
        reader = self.operator_probe_reader()
        temporary_home = self.root / "operator-probe-home"
        grouped_fingerprint = ":".join(
            CERTIFICATE_SHA256[index : index + 2]
            for index in range(0, len(CERTIFICATE_SHA256), 2)
        )

        with mock.patch.dict(
            os.environ,
            {"HOME": str(temporary_home)},
            clear=False,
        ), mock.patch.object(
            self.helper,
            "_activate_registry_pair",
            side_effect=AssertionError("operator probe wrote registry files"),
        ), mock.patch.object(
            self.helper,
            "_load_registries",
            side_effect=AssertionError("operator probe read registry files"),
        ):
            result, stdout, stderr, password_prompts = (
                self.run_operator_probe(
                    reader=reader,
                    client_factory=self.operator_probe_client_factory(events),
                )
            )

        self.assertEqual(events, ["client", "login", "probe", "logout"])
        self.assertEqual(
            result,
            {
                "authenticated": True,
                "guestAccountAdvertised": True,
                "localUserManagement": "supported",
                "userListingAccessible": True,
                "userWriteAdvertised": True,
                "existingLocalUsers": 2,
            },
        )
        self.assertIn(grouped_fingerprint, stdout)
        self.assertEqual(stderr, "")
        final_json = json.dumps(result, sort_keys=True)
        prompts = "\n".join(reader.prompts + password_prompts)
        for private in (
            HOST,
            PRIVATE_ADMIN_NAME,
            PRIVATE_ADMIN_PASSWORD,
            PRIVATE_TOKEN,
            CERTIFICATE_SHA256,
            grouped_fingerprint,
        ):
            self.assertNotIn(private, final_json)
        for secret in (PRIVATE_ADMIN_PASSWORD, PRIVATE_TOKEN):
            self.assertNotIn(secret, stdout)
            self.assertNotIn(secret, stderr)
            self.assertNotIn(secret, prompts)
        for private in (HOST, PRIVATE_ADMIN_NAME):
            self.assertNotIn(private, stdout + stderr + prompts)
        self.assertFalse(
            (
                temporary_home
                / ".openclaw"
                / "reolink-camera"
                / "config.json"
            ).exists()
        )
        self.assertFalse(
            (
                temporary_home
                / ".openclaw"
                / "reolink-camera"
                / "credentials.json"
            ).exists()
        )

    def test_guest_bootstrap_happy_path_has_exact_event_order_and_safe_state(
        self,
    ) -> None:
        config_path, credentials_path = self.bootstrap_paths("bootstrap-happy")
        events: list[str] = []
        operator_factory, guest_factory, state = (
            self.bootstrap_client_factories(events)
        )
        reader = self.bootstrap_reader()

        result, caught, stdout, stderr, password_prompts, argv = (
            self.run_guest_bootstrap(
                config_path=config_path,
                credentials_path=credentials_path,
                reader=reader,
                events=events,
                operator_client_factory=operator_factory,
                guest_client_factory=guest_factory,
            )
        )

        self.assertIsNone(caught)
        self.assertEqual(
            result,
            {"alias": ALIAS, "site": SITE, "configured": True},
        )
        self.assertEqual(
            events,
            [
                "fingerprint",
                "admin-password",
                "operator-client",
                "admin-login",
                "inspect",
                "add",
                "confirm-created",
                "guest-client",
                "guest-login",
                "guest-status",
                "guest-snapshot",
                "activate",
                "guest-logout",
                "admin-logout",
            ],
        )
        self.assertEqual(state["add_calls"], 1)
        self.assertEqual(state["delete_calls"], 0)
        self.assertEqual(state["guest_username"], GENERATED_GUEST_USERNAME)
        self.assertEqual(state["guest_password"], GENERATED_GUEST_PASSWORD)
        self.assertEqual(stat.S_IMODE(config_path.stat().st_mode), 0o600)
        self.assertEqual(
            stat.S_IMODE(credentials_path.stat().st_mode),
            0o600,
        )
        self.assertEqual(
            stat.S_IMODE(config_path.parent.stat().st_mode),
            0o700,
        )
        config = json.loads(config_path.read_text(encoding="utf-8"))
        credentials = json.loads(
            credentials_path.read_text(encoding="utf-8")
        )
        self.assertEqual(
            credentials["cameras"],
            [
                {
                    "alias": ALIAS,
                    "username": GENERATED_GUEST_USERNAME,
                    "password": GENERATED_GUEST_PASSWORD,
                }
            ],
        )
        self.assertEqual(
            self.helper.load_binding(
                ALIAS,
                config_path=config_path,
                credentials_path=credentials_path,
            ).username,
            GENERATED_GUEST_USERNAME,
        )
        persisted = json.dumps(
            {"config": config, "credentials": credentials},
            sort_keys=True,
        )
        captured = (
            stdout
            + stderr
            + "\n".join(reader.prompts + password_prompts)
            + argv
        )
        for private_admin in (
            PRIVATE_ADMIN_NAME,
            PRIVATE_ADMIN_PASSWORD,
            PRIVATE_TOKEN,
        ):
            self.assertNotIn(private_admin, persisted)
            self.assertNotIn(private_admin, captured)

    def test_guest_bootstrap_allows_unknown_levels_only_with_verified_access(
        self,
    ) -> None:
        config_path, credentials_path = self.bootstrap_paths(
            "bootstrap-level-unknown"
        )
        events: list[str] = []
        operator_factory, guest_factory, _state = (
            self.bootstrap_client_factories(events, levels=None)
        )

        result, caught, _stdout, _stderr, _prompts, _argv = (
            self.run_guest_bootstrap(
                config_path=config_path,
                credentials_path=credentials_path,
                reader=self.bootstrap_reader(),
                events=events,
                operator_client_factory=operator_factory,
                guest_client_factory=guest_factory,
            )
        )

        self.assertIsNone(caught)
        self.assertEqual(result["configured"], True)
        self.assertIn("add", events)

        access_cases = (
            ("no-read", False, True, 1),
            ("no-write", True, False, 1),
            ("zero-version", True, True, 0),
        )
        for label, can_read, can_write, version in access_cases:
            with self.subTest(label=label):
                blocked_config, blocked_credentials = self.bootstrap_paths(
                    "bootstrap-level-unknown-" + label
                )
                blocked_events: list[str] = []
                blocked_operator, blocked_guest, _blocked_state = (
                    self.bootstrap_client_factories(
                        blocked_events,
                        levels=None,
                        can_read=can_read,
                        can_write=can_write,
                        version=version,
                    )
                )
                _result, error, *_rest = self.run_guest_bootstrap(
                    config_path=blocked_config,
                    credentials_path=blocked_credentials,
                    reader=self.bootstrap_reader(),
                    events=blocked_events,
                    operator_client_factory=blocked_operator,
                    guest_client_factory=blocked_guest,
                )

                self.assertIsInstance(error, self.helper.PublicError)
                self.assertEqual(
                    str(error),
                    "Camera guest-account bootstrap is unsupported",
                )
                self.assertNotIn("add", blocked_events)
                self.assertFalse(blocked_config.exists())
                self.assertFalse(blocked_credentials.exists())

    def test_guest_bootstrap_capability_and_capacity_gates_precede_mutation(
        self,
    ) -> None:
        full_users = [(PRIVATE_ADMIN_NAME, "admin")] + [
            (f"user{index}", "guest") for index in range(1, 20)
        ]
        cases = (
            {
                "label": "known-no-guest",
                "levels": ("admin",),
            },
            {
                "label": "full",
                "existing_users": full_users,
            },
            {
                "label": "no-read",
                "can_read": False,
            },
            {
                "label": "no-write",
                "can_write": False,
            },
        )
        for case in cases:
            label = str(case["label"])
            options = {
                key: value
                for key, value in case.items()
                if key != "label"
            }
            with self.subTest(label=label):
                config_path, credentials_path = self.bootstrap_paths(
                    "bootstrap-gate-" + label
                )
                events: list[str] = []
                operator_factory, guest_factory, state = (
                    self.bootstrap_client_factories(events, **options)
                )

                _result, caught, *_captured = self.run_guest_bootstrap(
                    config_path=config_path,
                    credentials_path=credentials_path,
                    reader=self.bootstrap_reader(),
                    events=events,
                    operator_client_factory=operator_factory,
                    guest_client_factory=guest_factory,
                )

                self.assertIsInstance(caught, self.helper.PublicError)
                self.assertEqual(
                    str(caught),
                    "Camera guest-account bootstrap is unsupported",
                )
                self.assertEqual(state["add_calls"], 0)
                self.assertNotIn("guest-client", events)
                self.assertNotIn("activate", events)
                self.assertFalse(config_path.exists())
                self.assertFalse(credentials_path.exists())

    def test_guest_bootstrap_generation_skips_existing_account_collision(
        self,
    ) -> None:
        config_path, credentials_path = self.bootstrap_paths(
            "bootstrap-collision"
        )
        events: list[str] = []
        operator_factory, guest_factory, state = (
            self.bootstrap_client_factories(
                events,
                existing_users=[
                    (PRIVATE_ADMIN_NAME, "admin"),
                    (GENERATED_GUEST_USERNAME, "guest"),
                ],
            )
        )

        _result, caught, *_captured = self.run_guest_bootstrap(
            config_path=config_path,
            credentials_path=credentials_path,
            reader=self.bootstrap_reader(),
            events=events,
            operator_client_factory=operator_factory,
            guest_client_factory=guest_factory,
            username_random_values=[
                "0123456789abcdef0123456789abcdef",
                "fedcba9876543210fedcba9876543210",
            ],
        )

        self.assertIsNone(caught)
        self.assertEqual(
            state["guest_username"],
            "openclawfedcba9876543210",
        )
        self.assertNotEqual(
            state["guest_username"],
            GENERATED_GUEST_USERNAME,
        )
        self.assertEqual(state["add_calls"], 1)
        credentials = json.loads(
            credentials_path.read_text(encoding="utf-8")
        )
        self.assertEqual(
            credentials["cameras"][0]["username"],
            "openclawfedcba9876543210",
        )

    def test_guest_validation_failures_roll_back_before_activation(self) -> None:
        cases = (
            (
                "login",
                {
                    "guest_login_error": self.helper.PublicError(
                        "private login detail " + PRIVATE_ADMIN_PASSWORD
                    )
                },
                "Camera guest login verification failed",
                "guest-login",
            ),
            (
                "offline",
                {"guest_online": False},
                "Camera status verification failed",
                "guest-status",
            ),
            (
                "invalid-jpeg",
                {"guest_snapshot": b"not-a-jpeg"},
                "Camera snapshot verification failed",
                "guest-snapshot",
            ),
        )
        for label, options, expected_error, validation_event in cases:
            with self.subTest(label=label):
                config_path, credentials_path = self.bootstrap_paths(
                    "bootstrap-guest-" + label
                )
                events: list[str] = []
                operator_factory, guest_factory, state = (
                    self.bootstrap_client_factories(events, **options)
                )

                _result, caught, stdout, stderr, prompts, argv = (
                    self.run_guest_bootstrap(
                        config_path=config_path,
                        credentials_path=credentials_path,
                        reader=self.bootstrap_reader(),
                        events=events,
                        operator_client_factory=operator_factory,
                        guest_client_factory=guest_factory,
                    )
                )

                self.assertIsInstance(caught, self.helper.PublicError)
                self.assertEqual(str(caught), expected_error)
                self.assertLess(
                    events.index("confirm-created"),
                    events.index(validation_event),
                )
                self.assertLess(
                    events.index(validation_event),
                    events.index("delete"),
                )
                self.assertEqual(state["delete_calls"], 1)
                self.assertIn("confirm-absent", events)
                self.assertNotIn("activate", events)
                self.assertFalse(config_path.exists())
                self.assertFalse(credentials_path.exists())
                captured = stdout + stderr + "\n".join(prompts) + argv
                self.assertNotIn(PRIVATE_ADMIN_PASSWORD, captured)
                self.assertNotIn(PRIVATE_ADMIN_PASSWORD, str(caught))

    def test_definite_post_add_confirmation_failure_rolls_back_and_proves_absence(
        self,
    ) -> None:
        config_path, credentials_path = self.bootstrap_paths(
            "bootstrap-confirmation"
        )
        events: list[str] = []
        operator_factory, guest_factory, state = (
            self.bootstrap_client_factories(
                events,
                confirm_created=False,
            )
        )

        _result, caught, *_captured = self.run_guest_bootstrap(
            config_path=config_path,
            credentials_path=credentials_path,
            reader=self.bootstrap_reader(),
            events=events,
            operator_client_factory=operator_factory,
            guest_client_factory=guest_factory,
        )

        self.assertIsInstance(caught, self.helper.PublicError)
        self.assertEqual(
            str(caught),
            "Camera guest-account confirmation failed",
        )
        self.assertEqual(
            events[-5:],
            [
                "add",
                "confirm-created",
                "delete",
                "confirm-absent",
                "admin-logout",
            ],
        )
        self.assertEqual(state["add_calls"], 1)
        self.assertEqual(state["delete_calls"], 1)
        self.assertFalse(config_path.exists())
        self.assertFalse(credentials_path.exists())

    def test_ambiguous_add_is_never_retried_or_blindly_rolled_back(self) -> None:
        config_path, credentials_path = self.bootstrap_paths(
            "bootstrap-ambiguous"
        )
        events: list[str] = []
        operator_factory, guest_factory, state = (
            self.bootstrap_client_factories(
                events,
                add_error=TimeoutError(
                    "private mutation detail " + PRIVATE_ADMIN_PASSWORD
                ),
                ambiguous_add_committed=True,
            )
        )

        _result, caught, stdout, stderr, prompts, argv = (
            self.run_guest_bootstrap(
                config_path=config_path,
                credentials_path=credentials_path,
                reader=self.bootstrap_reader(),
                events=events,
                operator_client_factory=operator_factory,
                guest_client_factory=guest_factory,
            )
        )

        self.assertIsInstance(caught, self.helper.PublicError)
        self.assertEqual(
            str(caught),
            "Camera guest bootstrap requires manual cleanup",
        )
        self.assertEqual(state["add_calls"], 1)
        self.assertEqual(state["delete_calls"], 0)
        self.assertEqual(events.count("add"), 1)
        self.assertNotIn("confirm-created", events)
        self.assertNotIn("delete", events)
        self.assertNotIn("guest-client", events)
        self.assertNotIn("activate", events)
        self.assertFalse(config_path.exists())
        self.assertFalse(credentials_path.exists())
        captured = stdout + stderr + "\n".join(prompts) + argv + str(caught)
        for private_admin in (
            PRIVATE_ADMIN_NAME,
            PRIVATE_ADMIN_PASSWORD,
            PRIVATE_TOKEN,
        ):
            self.assertNotIn(private_admin, captured)

    def test_unconfirmed_rollback_requires_manual_cleanup(self) -> None:
        config_path, credentials_path = self.bootstrap_paths(
            "bootstrap-unconfirmed-rollback"
        )
        events: list[str] = []
        operator_factory, guest_factory, state = (
            self.bootstrap_client_factories(
                events,
                guest_login_error=self.helper.PublicError(
                    "private guest login failure"
                ),
                delete_removes=False,
            )
        )

        _result, caught, *_captured = self.run_guest_bootstrap(
            config_path=config_path,
            credentials_path=credentials_path,
            reader=self.bootstrap_reader(),
            events=events,
            operator_client_factory=operator_factory,
            guest_client_factory=guest_factory,
        )

        self.assertIsInstance(caught, self.helper.PublicError)
        self.assertEqual(
            str(caught),
            "Camera guest bootstrap requires manual cleanup",
        )
        self.assertEqual(state["delete_calls"], 1)
        self.assertIn("confirm-absent", events)
        self.assertFalse(config_path.exists())
        self.assertFalse(credentials_path.exists())

    def test_activation_failure_restores_prior_state_then_removes_guest(
        self,
    ) -> None:
        config_path, credentials_path = self.bootstrap_paths(
            "bootstrap-activation"
        )
        config_path.parent.mkdir(mode=0o700)
        previous_config = {
            "version": 1,
            "generation": GENERATION,
            "cameras": [
                self.binding_entry(
                    alias=SECOND_ALIAS,
                    channel=1,
                )
            ],
        }
        previous_credentials = {
            "version": 1,
            "generation": GENERATION,
            "cameras": [
                self.credentials_entry(alias=SECOND_ALIAS)
            ],
        }
        self.write_json(config_path, previous_config)
        self.write_json(credentials_path, previous_credentials)
        events: list[str] = []
        operator_factory, guest_factory, state = (
            self.bootstrap_client_factories(events)
        )

        _result, caught, stdout, stderr, prompts, argv = (
            self.run_guest_bootstrap(
                config_path=config_path,
                credentials_path=credentials_path,
                reader=self.bootstrap_reader(),
                events=events,
                operator_client_factory=operator_factory,
                guest_client_factory=guest_factory,
                activation_failure=True,
            )
        )

        self.assertIsInstance(caught, self.helper.PublicError)
        self.assertEqual(
            str(caught),
            "Camera configuration activation failed",
        )
        self.assertLess(events.index("activate"), events.index("restore"))
        self.assertLess(events.index("restore"), events.index("delete"))
        self.assertLess(events.index("delete"), events.index("confirm-absent"))
        self.assertEqual(state["delete_calls"], 1)
        self.assertEqual(
            json.loads(config_path.read_text(encoding="utf-8")),
            previous_config,
        )
        self.assertEqual(
            json.loads(credentials_path.read_text(encoding="utf-8")),
            previous_credentials,
        )
        with self.assertRaises(self.helper.PublicError):
            self.helper.load_binding(
                ALIAS,
                config_path=config_path,
                credentials_path=credentials_path,
            )
        self.assertEqual(
            self.helper.load_binding(
                SECOND_ALIAS,
                config_path=config_path,
                credentials_path=credentials_path,
            ).username,
            USERNAME,
        )
        captured = stdout + stderr + "\n".join(prompts) + argv + str(caught)
        self.assertNotIn(PRIVATE_ADMIN_NAME, captured)
        self.assertNotIn(PRIVATE_ADMIN_PASSWORD, captured)

    def test_guest_bootstrap_requires_attended_tty_and_exact_confirmations(
        self,
    ) -> None:
        for label, stdin_tty, stdout_tty in (
            ("stdin", False, True),
            ("stdout", True, False),
        ):
            with self.subTest(gate=label):
                config_path, credentials_path = self.bootstrap_paths(
                    "bootstrap-tty-" + label
                )
                events: list[str] = []
                operator_factory, guest_factory, state = (
                    self.bootstrap_client_factories(events)
                )
                _result, caught, *_captured = self.run_guest_bootstrap(
                    config_path=config_path,
                    credentials_path=credentials_path,
                    reader=self.bootstrap_reader(),
                    events=events,
                    operator_client_factory=operator_factory,
                    guest_client_factory=guest_factory,
                    stdin_tty=stdin_tty,
                    stdout_tty=stdout_tty,
                )

                self.assertIsInstance(caught, self.helper.PublicError)
                self.assertEqual(
                    str(caught),
                    "Operator guest bootstrap requires an attended terminal",
                )
                self.assertEqual(events, [])
                self.assertEqual(state["add_calls"], 0)
                self.assertFalse(config_path.parent.exists())

        for index, confirmation in enumerate(("trust", "TRUST ", "yes", "")):
            with self.subTest(gate="trust", confirmation=confirmation):
                config_path, credentials_path = self.bootstrap_paths(
                    f"bootstrap-trust-{index}"
                )
                events = []
                operator_factory, guest_factory, state = (
                    self.bootstrap_client_factories(events)
                )
                _result, caught, *_captured = self.run_guest_bootstrap(
                    config_path=config_path,
                    credentials_path=credentials_path,
                    reader=self.bootstrap_reader(trust=confirmation),
                    events=events,
                    operator_client_factory=operator_factory,
                    guest_client_factory=guest_factory,
                )

                self.assertIsInstance(caught, self.helper.PublicError)
                self.assertEqual(
                    str(caught),
                    "Operator guest bootstrap cancelled",
                )
                self.assertEqual(events, ["fingerprint"])
                self.assertEqual(state["add_calls"], 0)
                self.assertFalse(config_path.exists())
                self.assertFalse(credentials_path.exists())

        for index, confirmation in enumerate(
            ("create", "CREATE ", "yes", "")
        ):
            with self.subTest(gate="create", confirmation=confirmation):
                config_path, credentials_path = self.bootstrap_paths(
                    f"bootstrap-create-{index}"
                )
                events = []
                operator_factory, guest_factory, state = (
                    self.bootstrap_client_factories(events)
                )
                _result, caught, *_captured = self.run_guest_bootstrap(
                    config_path=config_path,
                    credentials_path=credentials_path,
                    reader=self.bootstrap_reader(create=confirmation),
                    events=events,
                    operator_client_factory=operator_factory,
                    guest_client_factory=guest_factory,
                )

                self.assertIsInstance(caught, self.helper.PublicError)
                self.assertEqual(
                    str(caught),
                    "Operator guest bootstrap cancelled",
                )
                self.assertEqual(state["add_calls"], 0)
                self.assertEqual(
                    events[-4:],
                    [
                        "operator-client",
                        "admin-login",
                        "inspect",
                        "admin-logout",
                    ],
                )
                self.assertFalse(config_path.exists())
                self.assertFalse(credentials_path.exists())

    def test_valid_jpeg_capture_is_private_and_returns_only_safe_fields(self) -> None:
        factory = FakeConnectionFactory(
            [
                login_response(),
                FakeResponse(JPEG, content_type="image/jpeg"),
                logout_response(),
            ]
        )
        reaper_calls: list[tuple[str, Path]] = []

        result = self.capture_image(
            client_factory=lambda binding: self.helper.ReolinkClient(
                binding,
                connection_factory=factory,
            ),
            reaper=lambda token, path: reaper_calls.append((token, path)),
        )

        self.assertEqual(set(result), {"alias", "mediaPath", "cleanupToken"})
        self.assertEqual(result["alias"], ALIAS)
        token = result["cleanupToken"]
        self.assertRegex(token, r"^[0-9a-f]{48}$")
        image = Path(result["mediaPath"])
        self.assertEqual(image, self.media_directory / f"{token}.jpg")
        self.assertEqual(image.read_bytes(), JPEG)
        self.assertEqual(stat.S_IMODE(image.stat().st_mode), 0o600)
        self.assertEqual(
            stat.S_IMODE(self.media_directory.stat().st_mode),
            0o700,
        )
        self.assertEqual(reaper_calls, [(token, self.media_directory)])
        requests = json.dumps(factory.requests, default=str)
        self.assertIn("Logout", requests)
        self.assertNotIn(PASSWORD, json.dumps(result))

    def test_bounded_command_passes_rpc_input_without_inheriting_stdin(self) -> None:
        completed = mock.Mock(returncode=0, stdout=b'{"ok":true}', stderr=b"")
        with mock.patch.object(
            self.helper.subprocess,
            "run",
            return_value=completed,
        ) as runner:
            result = self.helper._run_bounded_command(
                ["/fixed/helper", "rpc"],
                timeout=20,
                failure_message="Camera image delivery failed",
                stdin_data=b'{"request":true}\n',
            )

        self.assertEqual(result, b'{"ok":true}')
        self.assertEqual(
            runner.call_args.args,
            (["/fixed/helper", "rpc"],),
        )
        options = runner.call_args.kwargs
        self.assertEqual(options["input"], b'{"request":true}\n')
        self.assertNotIn("stdin", options)
        self.assertTrue(options["start_new_session"])

    def test_image_analysis_uses_token_scoped_media_and_fixed_inference(self) -> None:
        self.media_directory.mkdir(mode=0o700)
        token = "a" * 48
        image = self.media_directory / f"{token}.jpg"
        image.write_bytes(JPEG)
        image.chmod(0o600)
        calls: list[tuple[list[str], dict[str, object]]] = []

        def command_runner(command, **kwargs):
            calls.append((command, kwargs))
            commentary = {
                "category": "animal",
                "confidence": "high",
                "notable": True,
                "summary": "A dog is standing beside the flower bed.",
            }
            envelope = {
                "ok": True,
                "capability": "image.describe",
                "transport": "local",
                "provider": "codex",
                "model": "gpt-5.6-sol",
                "outputs": [
                    {
                        "kind": "image.description",
                        "provider": "codex",
                        "model": "gpt-5.6-sol",
                        "path": str(image),
                        "text": json.dumps(commentary),
                    }
                ],
            }
            return json.dumps(envelope).encode("utf-8")

        result = self.helper.analyze_image(
            token,
            media_directory=self.media_directory,
            command_runner=command_runner,
        )

        self.assertEqual(
            result,
            {
                "category": "animal",
                "confidence": "high",
                "notable": True,
                "summary": "A dog is standing beside the flower bed.",
            },
        )
        self.assertTrue(image.exists())
        self.assertEqual(len(calls), 1)
        command, kwargs = calls[0]
        self.assertEqual(command[0], str(self.helper.OPENCLAW_BINARY))
        self.assertEqual(
            command[command.index("--model") + 1],
            self.helper.VISION_MODEL,
        )
        self.assertEqual(command[command.index("--file") + 1], str(image))
        self.assertEqual(
            kwargs["failure_message"],
            "Camera image analysis failed",
        )

    def test_owner_share_is_semantic_and_always_cleans_media(self) -> None:
        self.media_directory.mkdir(mode=0o700)
        token = "b" * 48
        image = self.media_directory / f"{token}.jpg"
        image.write_bytes(JPEG)
        image.chmod(0o600)
        deliveries: list[tuple[object, ...]] = []

        def capture(alias, **_kwargs):
            return {
                "alias": alias,
                "mediaPath": str(image),
                "cleanupToken": token,
            }

        def analyze(_token, **_kwargs):
            return {
                "category": "environment",
                "confidence": "high",
                "notable": False,
                "summary": "The flower bed is visible in clear daylight.",
            }

        def deliver(recipient, delivered_token, caption, **_kwargs):
            deliveries.append((recipient, delivered_token, caption))
            return "Julia", True

        result = self.helper.share_image(
            ALIAS,
            "julia",
            media_directory=self.media_directory,
            capture_function=capture,
            analysis_function=analyze,
            delivery_function=deliver,
        )

        self.assertEqual(result["recipient"], "Julia")
        self.assertIs(result["delivered"], True)
        self.assertIs(result["commentaryDelivered"], True)
        self.assertEqual(
            deliveries,
            [
                (
                    "julia",
                    token,
                    f"{ALIAS} — The flower bed is visible in clear daylight.",
                )
            ],
        )
        self.assertFalse(image.exists())

        calls: list[str] = []
        with self.assertRaises(self.helper.PublicError):
            self.helper.share_image(
                ALIAS,
                "arbitrary-chat",
                capture_function=lambda *_args, **_kwargs: calls.append("capture"),
            )
        self.assertEqual(calls, [])

    def test_owner_route_reads_one_exact_private_assignment(self) -> None:
        cache = self.protected / ".secrets-cache"
        cache.write_bytes(
            b"UNRELATED_SECRET=private\n"
            b"DYLAN_CHAT_ID=7\n"
            b"JULIA_CHAT_ID=8\n"
            b"HOUSEHOLD_CHAT_ID=9\n"
        )
        cache.chmod(0o600)

        self.assertEqual(
            self.helper._read_owner_chat_id(
                "julia",
                secrets_cache=cache,
            ),
            ("Julia", "8"),
        )
        self.media_directory.mkdir(mode=0o700)
        token = "c" * 48
        image = self.media_directory / f"{token}.jpg"
        image.write_bytes(JPEG)
        image.chmod(0o600)
        commands: list[tuple[list[str], dict[str, object]]] = []

        def command_runner(command, **kwargs):
            commands.append((command, kwargs))
            if command[1] == "group":
                return (
                    b'{"id":8,"identifier":"private","guid":'
                    b'"iMessage;-;private","name":"","service":"iMessage",'
                    b'"is_group":false,"participants":["private"]}'
                )
            if command[1] == "send-attachment":
                return (
                    b'{"chatGuid":"iMessage;-;private","messageGuid":'
                    b'"message-receipt","selectedMessageGuid":"",'
                    b'"transferGuid":"transfer-receipt"}'
                )
            return (
                b'{"jsonrpc":"2.0","id":"reolink-camera-share",'
                b'"result":{"ok":true,"transport":"bridge",'
                b'"chat_guid":"iMessage;-;private",'
                b'"guid":"opaque-receipt"}}'
            )

        self.assertEqual(
            self.helper._deliver_owner_image(
                "julia",
                token,
                "A fresh flower-camera view.",
                media_directory=self.media_directory,
                secrets_cache=cache,
                command_runner=command_runner,
            ),
            ("Julia", True),
        )
        self.assertEqual(len(commands), 3)
        command, options = commands[0]
        self.assertEqual(
            command,
            [
                str(self.helper.IMSG_BINARY),
                "group",
                "--chat-id",
                "8",
                "--json",
            ],
        )
        self.assertNotIn("stdin_data", options)
        command, options = commands[1]
        self.assertEqual(
            command,
            [
                str(self.helper.IMSG_BINARY),
                "send-attachment",
                "--chat",
                "iMessage;-;private",
                "--file",
                str(image),
                "--transport",
                "dylib",
                "--json",
            ],
        )
        self.assertNotIn("stdin_data", options)
        command, options = commands[2]
        self.assertEqual(
            command,
            [str(self.helper.IMSG_BINARY), "rpc"],
        )
        request = json.loads(options["stdin_data"])
        self.assertEqual(
            request,
            {
                "jsonrpc": "2.0",
                "id": "reolink-camera-share",
                "method": "send",
                "params": {
                    "chat_guid": "iMessage;-;private",
                    "text": "A fresh flower-camera view.",
                    "transport": "bridge",
                },
            },
        )
        self.assertEqual(
            options["timeout"],
            self.helper.DELIVERY_TIMEOUT_SECONDS,
        )
        self.assertEqual(
            options["failure_message"],
            "Camera image commentary delivery failed",
        )
        cache.write_bytes(cache.read_bytes() + b"JULIA_CHAT_ID=10\n")
        cache.chmod(0o600)
        with self.assertRaises(self.helper.PublicError):
            self.helper._read_owner_chat_id(
                "julia",
                secrets_cache=cache,
            )

    def test_owner_delivery_requires_exact_bridge_attachment_receipt(self) -> None:
        cache = self.protected / ".secrets-cache"
        cache.write_bytes(b"JULIA_CHAT_ID=8\n")
        cache.chmod(0o600)
        self.media_directory.mkdir(mode=0o700)
        token = "d" * 48
        image = self.media_directory / f"{token}.jpg"
        image.write_bytes(JPEG)
        image.chmod(0o600)
        invalid_receipts = (
            b'{"status":"sent","guid":"legacy-applescript"}',
            (
                b'{"chatGuid":"iMessage;-;wrong","messageGuid":"message",'
                b'"selectedMessageGuid":"","transferGuid":"transfer"}'
            ),
            (
                b'{"chatGuid":"iMessage;-;private","messageGuid":"message",'
                b'"selectedMessageGuid":"","transferGuid":""}'
            ),
            (
                b'{"chatGuid":"iMessage;-;private","messageGuid":"message",'
                b'"selectedMessageGuid":"reply","transferGuid":"transfer"}'
            ),
            (
                b'{"chatGuid":"iMessage;-;private","messageGuid":"message",'
                b'"selectedMessageGuid":"","transferGuid":"transfer"}\n{}'
            ),
        )
        for receipt in invalid_receipts:
            with self.subTest(receipt=receipt):
                calls = 0

                def command_runner(command, **_kwargs):
                    nonlocal calls
                    calls += 1
                    if command[1] == "group":
                        return (
                            b'{"id":8,"identifier":"private","guid":'
                            b'"iMessage;-;private","name":"","service":"iMessage",'
                            b'"is_group":false,"participants":["private"]}'
                        )
                    return receipt

                with self.assertRaises(self.helper.PublicError) as caught:
                    self.helper._deliver_owner_image(
                        "julia",
                        token,
                        "A fresh flower-camera view.",
                        media_directory=self.media_directory,
                        secrets_cache=cache,
                        command_runner=command_runner,
                    )
                self.assertEqual(
                    str(caught.exception),
                    "Camera image delivery failed",
                )
                self.assertEqual(calls, 2)

    def test_owner_delivery_does_not_retry_image_when_commentary_fails(self) -> None:
        cache = self.protected / ".secrets-cache"
        cache.write_bytes(b"JULIA_CHAT_ID=8\n")
        cache.chmod(0o600)
        self.media_directory.mkdir(mode=0o700)
        token = "e" * 48
        image = self.media_directory / f"{token}.jpg"
        image.write_bytes(JPEG)
        image.chmod(0o600)
        attachment_calls = 0

        def command_runner(command, **_kwargs):
            nonlocal attachment_calls
            if command[1] == "group":
                return (
                    b'{"id":8,"identifier":"private","guid":'
                    b'"iMessage;-;private","name":"","service":"iMessage",'
                    b'"is_group":false,"participants":["private"]}'
                )
            if command[1] == "send-attachment":
                attachment_calls += 1
                return (
                    b'{"chatGuid":"iMessage;-;private","messageGuid":'
                    b'"message-receipt","selectedMessageGuid":"",'
                    b'"transferGuid":"transfer-receipt"}'
                )
            return b'{"jsonrpc":"2.0","id":"wrong","result":{"ok":true}}'

        self.assertEqual(
            self.helper._deliver_owner_image(
                "julia",
                token,
                "A fresh flower-camera view.",
                media_directory=self.media_directory,
                secrets_cache=cache,
                command_runner=command_runner,
            ),
            ("Julia", False),
        )
        self.assertEqual(attachment_calls, 1)

    def test_invalid_or_oversized_capture_is_removed_and_sanitized(self) -> None:
        scenarios = (
            b"not-a-jpeg-" + PASSWORD.encode("utf-8"),
            JPEG[:-2],
            JPEG + (b"x" * (16 * 1024 * 1024)),
        )
        for payload in scenarios:
            with self.subTest(size=len(payload)):
                factory = FakeConnectionFactory(
                    [
                        login_response(),
                        FakeResponse(payload, content_type="image/jpeg"),
                        logout_response(),
                    ]
                )
                with self.assertRaises(self.helper.PublicError) as caught:
                    self.capture_image(
                        client_factory=lambda binding: self.helper.ReolinkClient(
                            binding,
                            connection_factory=factory,
                        )
                    )
                self.assert_public_error_without_secrets(caught)
                self.assertIn("Logout", json.dumps(factory.requests, default=str))
                if self.media_directory.exists():
                    self.assertEqual(list(self.media_directory.iterdir()), [])

    def test_media_directory_must_be_real_owner_only_mode_0700(self) -> None:
        permissive = self.root / "permissive"
        permissive.mkdir(mode=0o755)
        permissive.chmod(0o755)
        real = self.root / "real"
        real.mkdir(mode=0o700)
        linked = self.root / "linked"
        linked.symlink_to(real, target_is_directory=True)
        linked_parent_target = self.root / "linked-parent-target"
        linked_parent_target.mkdir(mode=0o700)
        linked_parent = self.root / "linked-parent"
        linked_parent.symlink_to(linked_parent_target, target_is_directory=True)

        for media in (permissive, linked, linked_parent / "media"):
            with self.subTest(media=media):
                factory = FakeConnectionFactory(
                    [
                        login_response(),
                        FakeResponse(JPEG, content_type="image/jpeg"),
                        logout_response(),
                    ]
                )
                with self.assertRaises(self.helper.PublicError):
                    self.capture_image(
                        media_directory=media,
                        client_factory=lambda binding: self.helper.ReolinkClient(
                            binding,
                            connection_factory=factory,
                        ),
                    )

    def test_cleanup_is_token_only_path_safe_and_idempotent(self) -> None:
        self.media_directory.mkdir(mode=0o700)
        token = "a" * 48
        image = self.media_directory / f"{token}.jpg"
        image.write_bytes(JPEG)
        image.chmod(0o600)
        protected = self.root / "protected.jpg"
        protected.write_bytes(b"keep")

        for invalid in (
            "../protected",
            str(protected),
            token + ".jpg",
            "A" * 48,
            "a" * 47,
            "",
        ):
            with self.subTest(invalid=invalid):
                with self.assertRaises(self.helper.PublicError):
                    self.helper.cleanup_image(
                        invalid,
                        media_directory=self.media_directory,
                    )
        self.assertEqual(protected.read_bytes(), b"keep")
        self.assertTrue(image.exists())

        self.helper.cleanup_image(token, media_directory=self.media_directory)
        self.helper.cleanup_image(token, media_directory=self.media_directory)
        self.assertFalse(image.exists())
        self.assertEqual(protected.read_bytes(), b"keep")

    def test_sweep_removes_only_expired_owned_image_shapes(self) -> None:
        self.media_directory.mkdir(mode=0o700)
        now = time.time()
        old_image = self.media_directory / (("b" * 48) + ".jpg")
        old_temp = self.media_directory / (
            "." + ("c" * 48) + ".jpg." + ("e" * 16) + ".tmp"
        )
        fresh_image = self.media_directory / (("d" * 48) + ".jpg")
        unrelated = self.media_directory / "notes.txt"
        for path in (old_image, old_temp, fresh_image, unrelated):
            path.write_bytes(b"fixture")
            path.chmod(0o600)
        for path in (old_image, old_temp, unrelated):
            os.utime(path, (now - 1000, now - 1000))

        removed = self.helper.sweep_images(
            media_directory=self.media_directory,
            now=now,
            ttl_seconds=900,
        )

        self.assertEqual(removed, 2)
        self.assertFalse(old_image.exists())
        self.assertFalse(old_temp.exists())
        self.assertTrue(fresh_image.exists())
        self.assertTrue(unrelated.exists())

    def test_cli_errors_are_single_line_sanitized_and_nonzero(self) -> None:
        stdout = io.StringIO()
        stderr = io.StringIO()
        with mock.patch.object(
            self.helper,
            "capture_image",
            side_effect=self.helper.PublicError(
                "Reolink camera configuration is unavailable"
            ),
        ), redirect_stdout(stdout), redirect_stderr(stderr):
            status = self.helper.main(["capture", ALIAS])

        self.assertNotEqual(status, 0)
        self.assertEqual(stdout.getvalue(), "")
        self.assertEqual(len(stderr.getvalue().splitlines()), 1)
        for private in (
            HOST,
            USERNAME,
            PASSWORD,
            PRIVATE_TOKEN,
            PRIVATE_SERIAL,
        ):
            self.assertNotIn(private, stderr.getvalue())

    def test_cli_capture_json_has_exact_contract(self) -> None:
        result = {
            "alias": ALIAS,
            "mediaPath": "/safe/image.jpg",
            "cleanupToken": "f" * 48,
        }
        stdout = io.StringIO()
        with mock.patch.object(
            self.helper,
            "capture_image",
            return_value=result,
        ), redirect_stdout(stdout):
            status = self.helper.main(["capture", ALIAS])

        self.assertEqual(status, 0)
        self.assertEqual(json.loads(stdout.getvalue()), result)
        self.assertEqual(len(stdout.getvalue().splitlines()), 1)

    def test_skill_exposes_v2_owner_tasks_and_scoped_automation(self) -> None:
        text = SKILL.read_text(encoding="utf-8")
        for required in (
            "Trusted current task",
            "Standing automation",
            "exact alias",
            "`dylan`, `julia`, and `household` aliases",
            "reolink-camera describe '<cleanupToken>'",
            "reolink-camera share '<exact alias>'",
            "reolink-camera spotlight '<exact alias>' on",
            'message(action="send"',
            "reolink-camera cleanup '<cleanupToken>'",
            "finally",
            "NO_REPLY",
            "do not categorically refuse proactive use",
            "does not require another confirmation",
            "Do not manually invoke another authorization tool",
            "Do not redirect or",
            "bridge-only native attachment command",
            "`commentaryDelivered`",
            "never call `share` again",
            "`Flower Cam #2` is reserved",
            "preserves the camera's existing brightness",
        ):
            with self.subTest(required=required):
                self.assertIn(required, text)


if __name__ == "__main__":
    unittest.main()
