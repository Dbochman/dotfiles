#!/usr/bin/env python3
"""Fake-only tests for unattended Resy credential and booking behavior."""

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
RESY_CLI = REPO_ROOT / "bin" / "resy"


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


class FakeSession:
    def __init__(self, outcomes: list[object]):
        self.outcomes = list(outcomes)
        self.calls = 0

    def request(self, *_args: object, **_kwargs: object) -> FakeResponse:
        self.calls += 1
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, BaseException):
            raise outcome
        assert isinstance(outcome, FakeResponse)
        return outcome


class ResyCliTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tempdir.cleanup)
        self.root = Path(self.tempdir.name)

    def load_module(self, **environment: str):
        module_name = f"resy_cli_test_{id(self)}_{len(environment)}"
        loader = SourceFileLoader(module_name, str(RESY_CLI))
        spec = importlib.util.spec_from_loader(module_name, loader)
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        with mock.patch.dict(
            os.environ,
            {"HOME": str(self.root), **environment},
            clear=False,
        ):
            spec.loader.exec_module(module)
        return module

    @staticmethod
    def api_with_session(module, session: FakeSession):
        api = module.ResyAPI.__new__(module.ResyAPI)
        api.session = session
        api.limiter = types.SimpleNamespace(wait=lambda: None)
        api._ensure_auth = lambda: None
        api._headers = lambda: {}
        return api

    def test_cache_only_missing_field_never_invokes_op(self) -> None:
        module = self.load_module(RESY_CACHE_ONLY="1")
        credentials = module.ResyCredentials()
        with mock.patch.object(
            credentials,
            "_op_read",
            side_effect=AssertionError("1Password must not run"),
        ) as op_read:
            with self.assertRaises(SystemExit) as raised:
                credentials.get("api_key")
        self.assertEqual(raised.exception.code, 1)
        op_read.assert_not_called()

    def test_explicit_supervised_opt_in_preserves_1password_refresh(self) -> None:
        module = self.load_module(
            RESY_ALLOW_1PASSWORD="1",
            RESY_CACHE_ONLY="0",
        )
        credentials = module.ResyCredentials()
        with mock.patch.object(credentials, "_op_read", return_value="fake-value"):
            self.assertEqual(credentials.get("api_key"), "fake-value")

    def test_booking_transport_failure_is_never_retried(self) -> None:
        module = self.load_module(RESY_CACHE_ONLY="1")
        session = FakeSession([module.requests.ConnectionError("lost response")])
        api = self.api_with_session(module, session)

        with self.assertRaises(SystemExit) as raised:
            api._request("POST", "/3/book", auth_required=False, data={})

        self.assertEqual(raised.exception.code, 2)
        self.assertEqual(session.calls, 1)

    def test_booking_401_and_rate_limit_are_never_retried(self) -> None:
        module = self.load_module(RESY_CACHE_ONLY="1")
        for status_code in (401, 429):
            with self.subTest(status_code=status_code):
                session = FakeSession([FakeResponse(status_code)])
                api = self.api_with_session(module, session)
                with self.assertRaises(SystemExit):
                    api._request("POST", "/3/book", data={})
                self.assertEqual(session.calls, 1)

    def test_non_booking_transport_failure_retains_single_retry(self) -> None:
        module = self.load_module(RESY_CACHE_ONLY="1")
        session = FakeSession(
            [
                module.requests.ConnectionError("temporary failure"),
                FakeResponse(payload={"ok": True}),
            ]
        )
        api = self.api_with_session(module, session)

        with mock.patch.object(module.time, "sleep"):
            result = api._request("GET", "/4/find", auth_required=False)

        self.assertEqual(result, {"ok": True})
        self.assertEqual(session.calls, 2)


if __name__ == "__main__":
    unittest.main()
