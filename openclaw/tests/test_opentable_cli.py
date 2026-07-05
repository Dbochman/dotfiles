#!/usr/bin/env python3
"""Fake-only tests for OpenTable mutation retry behavior."""

from __future__ import annotations

import importlib.util
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
        if self.ok:
            return self.payload
        return {"message": "fake error"}


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
