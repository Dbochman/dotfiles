#!/usr/bin/env python3
"""Fake-only tests for OpenTable mutation retry behavior."""

from __future__ import annotations

import importlib.util
import hashlib
import io
import json
import os
import tempfile
import types
import unittest
from importlib.machinery import SourceFileLoader
from pathlib import Path
from unittest import mock


REPO_ROOT = Path(__file__).resolve().parents[2]
OPENTABLE_CLI = REPO_ROOT / "bin" / "opentable"


class FakeResponse:
    def __init__(self, status_code: int = 200, payload: object | None = None):
        self.status_code = status_code
        self.ok = status_code < 400
        self.payload = {} if payload is None else payload
        self.text = "fake response"
        self.headers = {"content-type": "application/json"}

    def json(self) -> object:
        return self.payload


class FakeRequests:
    def __init__(self, outcomes: list[object]):
        self.outcomes = list(outcomes)
        self.calls = 0
        self.call_kwargs: list[dict[str, object]] = []

    def request(self, *_args: object, **_kwargs: object) -> FakeResponse:
        self.calls += 1
        self.call_kwargs.append(dict(_kwargs))
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, BaseException):
            raise outcome
        assert isinstance(outcome, FakeResponse)
        return outcome


class OpenTableCliTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tempdir.cleanup)
        self.root = Path(self.tempdir.name)

    def load_module(self):
        module_name = f"opentable_cli_test_{id(self)}"
        loader = SourceFileLoader(module_name, str(OPENTABLE_CLI))
        spec = importlib.util.spec_from_loader(module_name, loader)
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        with mock.patch.dict(os.environ, {"HOME": str(self.root)}, clear=False):
            spec.loader.exec_module(module)
        return module

    @staticmethod
    def api_with_requests(module, fake_requests: FakeRequests):
        api = module.OpenTableAPI.__new__(module.OpenTableAPI)
        api.limiter = types.SimpleNamespace(wait=lambda: None)
        api.creds = types.SimpleNamespace(clear=lambda _name: None)
        api._headers = lambda: {}
        module.requests = fake_requests
        module.HAS_CURL_CFFI = False
        return api

    def test_booking_transport_failure_is_never_retried(self) -> None:
        module = self.load_module()
        fake_requests = FakeRequests([RuntimeError("lost response")])
        api = self.api_with_requests(module, fake_requests)

        with self.assertRaises(SystemExit) as raised:
            api._request("POST", "/api/v3/reservation/book", data="{}")

        self.assertEqual(raised.exception.code, 2)
        self.assertEqual(fake_requests.calls, 1)

    def test_booking_deadline_callback_runs_after_limiter_before_send(self) -> None:
        module = self.load_module()
        fake_requests = FakeRequests([FakeResponse(payload={"ok": True})])
        api = self.api_with_requests(module, fake_requests)
        events = []
        api.limiter = types.SimpleNamespace(wait=lambda: events.append("waited"))

        def reject_expired() -> None:
            self.assertEqual(events, ["waited"])
            events.append("checked")
            raise RuntimeError("expired")

        api._pre_mutation_check = reject_expired
        with self.assertRaisesRegex(RuntimeError, "expired"):
            api._request("POST", "/api/v3/reservation/book", data="{}")

        self.assertEqual(events, ["waited", "checked"])
        self.assertEqual(fake_requests.calls, 0)

    def test_booking_guard_precedes_limiter_and_fresh_headers(self) -> None:
        module = self.load_module()
        fake_requests = FakeRequests(
            [
                FakeResponse(payload={"reservations": []}),
                FakeResponse(payload={"ok": True}),
            ]
        )
        api = self.api_with_requests(module, fake_requests)
        events = []
        api.limiter = module.RateLimiter()
        api._headers = lambda: events.append("headers") or {}
        api._pre_mutation_check = lambda: events.append("deadline")

        def live_guard() -> None:
            events.append("guarded")
            self.assertEqual(api.limiter.reserved_slots, 2)
            api._restaurant_snipe_final_guard = True
            try:
                api._request("GET", "/api/v3/reservation/guard")
            finally:
                api._restaurant_snipe_final_guard = False
            self.assertEqual(api.limiter.reserved_slots, 1)

        api._pre_booking_guard = live_guard

        result = api._request("POST", "/api/v3/reservation/book", data="{}")

        self.assertEqual(result, {"ok": True})
        self.assertEqual(events, ["guarded", "headers", "headers", "deadline"])
        self.assertEqual(api.limiter.reserved_slots, 0)
        self.assertEqual(len(api.limiter.timestamps), 2)
        self.assertEqual(fake_requests.calls, 2)

    def test_external_booking_guard_reserves_only_the_mutation_request(self) -> None:
        module = self.load_module()
        fake_requests = FakeRequests([FakeResponse(payload={"ok": True})])
        api = self.api_with_requests(module, fake_requests)
        api.limiter = module.RateLimiter()
        events = []

        def external_guard() -> None:
            events.append("guarded")
            self.assertEqual(api.limiter.reserved_slots, 1)

        api._pre_booking_guard = external_guard
        api._external_pre_booking_guard_contract = api.EXTERNAL_PRE_BOOKING_GUARD_CONTRACT
        api._pre_mutation_check = lambda: events.append("boundary")

        result = api._request("POST", "/api/v3/reservation/book", data="{}")

        self.assertEqual(result, {"ok": True})
        self.assertEqual(events, ["guarded", "boundary"])
        self.assertEqual(api.limiter.reserved_slots, 0)
        self.assertEqual(len(api.limiter.timestamps), 1)
        self.assertEqual(fake_requests.calls, 1)

    def test_cache_only_credentials_never_invoke_op_fallback(self) -> None:
        module = self.load_module()
        credentials = module.OpenTableCredentials()
        with mock.patch.dict(os.environ, {"OPENTABLE_CACHE_ONLY": "1"}, clear=False), mock.patch.object(
            credentials, "_op_read", side_effect=AssertionError("op fallback invoked")
        ):
            with self.assertRaises(SystemExit):
                credentials.get("auth_token")

    def write_account_binding(self, module, token: str) -> None:
        module.CACHE_DIR.mkdir(parents=True, exist_ok=True)
        account_hash = hashlib.sha256(b"expected@example.invalid").hexdigest()
        module.EXPECTED_ACCOUNT_FILE.write_text(account_hash + "\n", encoding="utf-8")
        module.EXPECTED_ACCOUNT_FILE.chmod(0o600)
        module.ACCOUNT_BINDING_FILE.write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "profile": "opentable",
                    "verified_via": "email_otp",
                    "account_sha256": account_hash,
                    "token_sha256": hashlib.sha256(token.encode("utf-8")).hexdigest(),
                }
            ),
            encoding="utf-8",
        )
        module.ACCOUNT_BINDING_FILE.chmod(0o600)

    def test_headers_require_expected_account_and_exact_token_binding(self) -> None:
        module = self.load_module()
        token = "bound-browser-token-for-expected-account"
        api = module.OpenTableAPI()
        api.creds.get = lambda name: (
            token if name == "auth_token" else "expected@example.invalid"
        )

        with self.assertRaises(SystemExit):
            api._headers()

        self.write_account_binding(module, token)
        self.assertEqual(api._headers()["authorization"], f"Bearer {token}")

        module.ACCOUNT_BINDING_FILE.write_text(
            module.ACCOUNT_BINDING_FILE.read_text(encoding="utf-8").replace(
                hashlib.sha256(token.encode("utf-8")).hexdigest(), "0" * 64
            ),
            encoding="utf-8",
        )
        module.ACCOUNT_BINDING_FILE.chmod(0o600)
        with self.assertRaises(SystemExit):
            api._headers()

    def test_binding_rejects_account_mismatch_unsafe_mode_and_symlink(self) -> None:
        module = self.load_module()
        token = "bound-browser-token-for-expected-account"
        credentials = module.OpenTableCredentials()

        for variant in ("account-mismatch", "unsafe-mode", "symlink"):
            with self.subTest(variant=variant):
                self.write_account_binding(module, token)
                if variant == "account-mismatch":
                    binding = json.loads(
                        module.ACCOUNT_BINDING_FILE.read_text(encoding="utf-8")
                    )
                    binding["account_sha256"] = "0" * 64
                    module.ACCOUNT_BINDING_FILE.write_text(
                        json.dumps(binding), encoding="utf-8"
                    )
                    module.ACCOUNT_BINDING_FILE.chmod(0o600)
                elif variant == "unsafe-mode":
                    module.ACCOUNT_BINDING_FILE.chmod(0o644)
                else:
                    target = module.CACHE_DIR / "binding-target.json"
                    module.ACCOUNT_BINDING_FILE.rename(target)
                    module.ACCOUNT_BINDING_FILE.symlink_to(target)

                with self.assertRaises(SystemExit):
                    credentials.require_account_binding(
                        token, "expected@example.invalid"
                    )

    def test_binding_rejects_mismatched_cached_expected_identity(self) -> None:
        module = self.load_module()
        token = "bound-browser-token-for-expected-account"
        self.write_account_binding(module, token)

        with self.assertRaises(SystemExit):
            module.OpenTableCredentials().require_account_binding(
                token, "different@example.invalid"
            )

    def test_refresh_candidate_validation_is_read_only_and_stdin_only(self) -> None:
        module = self.load_module()
        token = "refresh-candidate-browser-token-123456"
        with mock.patch.object(module.sys, "stdin", io.StringIO(token)), mock.patch.object(
            module.OpenTableAPI, "get_restaurant", return_value={"name": "fixture"}
        ) as get_restaurant:
            module.cmd_validate_refresh_candidate([])
        get_restaurant.assert_called_once_with("1267699")

        with mock.patch.object(module.sys, "stdin", io.StringIO("short")), mock.patch.object(
            module.OpenTableAPI, "get_restaurant"
        ) as get_restaurant:
            with self.assertRaises(SystemExit):
                module.cmd_validate_refresh_candidate([])
        get_restaurant.assert_not_called()

    def test_final_guard_request_is_single_attempt_and_never_redirects(self) -> None:
        module = self.load_module()
        for outcome in (RuntimeError("lost response"), FakeResponse(429)):
            with self.subTest(outcome=outcome):
                fake_requests = FakeRequests([outcome])
                api = self.api_with_requests(module, fake_requests)
                api._restaurant_snipe_final_guard = True
                with self.assertRaises(SystemExit):
                    api._request("GET", "/api/v3/reservation/guard")
                self.assertEqual(fake_requests.calls, 1)
                self.assertIs(fake_requests.call_kwargs[0]["allow_redirects"], False)

    def test_booking_auth_and_rate_limit_responses_are_never_retried(self) -> None:
        module = self.load_module()
        for status_code in (401, 429):
            with self.subTest(status_code=status_code):
                fake_requests = FakeRequests([FakeResponse(status_code)])
                api = self.api_with_requests(module, fake_requests)
                with self.assertRaises(SystemExit):
                    api._request("POST", "/api/v3/reservation/book", data="{}")
                self.assertEqual(fake_requests.calls, 1)
                self.assertIs(fake_requests.call_kwargs[0]["allow_redirects"], False)

    def test_booking_redirects_are_disabled_and_reported_unknown(self) -> None:
        module = self.load_module()
        for status_code in (307, 308):
            with self.subTest(status_code=status_code):
                fake_requests = FakeRequests([FakeResponse(status_code)])
                api = self.api_with_requests(module, fake_requests)
                with self.assertRaises(SystemExit) as raised:
                    api._request("POST", "/api/v3/reservation/book", data="{}")
                self.assertEqual(raised.exception.code, 2)
                self.assertEqual(fake_requests.calls, 1)
                self.assertIs(fake_requests.call_kwargs[0]["allow_redirects"], False)

    def test_non_2xx_body_is_never_logged_or_printed(self) -> None:
        module = self.load_module()
        canary = "PRIVATE-CONTACT-SLOT-ID-CANARY"
        response = FakeResponse(500, {"message": canary})
        response.text = canary
        api = self.api_with_requests(module, FakeRequests([response]))
        stderr = io.StringIO()

        with mock.patch.object(module.log, "error") as logged, mock.patch.object(
            module.sys, "stderr", stderr
        ):
            with self.assertRaises(SystemExit):
                api._request("GET", "/api/v3/restaurant/private")

        self.assertNotIn(canary, str(logged.call_args))
        self.assertNotIn(canary, stderr.getvalue())

    def test_non_booking_transport_failure_retains_single_retry(self) -> None:
        module = self.load_module()
        fake_requests = FakeRequests(
            [RuntimeError("temporary failure"), FakeResponse(payload={"ok": True})]
        )
        api = self.api_with_requests(module, fake_requests)

        with mock.patch.object(module.time, "sleep"):
            result = api._request("GET", "/api/v3/restaurant/123")

        self.assertEqual(result, {"ok": True})
        self.assertEqual(fake_requests.calls, 2)

    def test_non_booking_rate_limit_retains_backoff_retry(self) -> None:
        module = self.load_module()
        fake_requests = FakeRequests(
            [FakeResponse(429), FakeResponse(payload={"ok": True})]
        )
        api = self.api_with_requests(module, fake_requests)

        with mock.patch.object(module.time, "sleep"):
            result = api._request("GET", "/api/v3/restaurant/123")

        self.assertEqual(result, {"ok": True})
        self.assertEqual(fake_requests.calls, 2)


if __name__ == "__main__":
    unittest.main()
