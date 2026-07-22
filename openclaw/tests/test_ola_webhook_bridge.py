#!/usr/bin/env python3
"""Behavior and deployment contracts for the Ola webhook bridge."""

from __future__ import annotations

import hashlib
import hmac
import http.client
import importlib.util
import json
import plistlib
import sys
import threading
import unittest
from dataclasses import replace
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Optional
from unittest import mock


OPENCLAW_DIR = Path(__file__).resolve().parents[1]
BRIDGE_PATH = OPENCLAW_DIR / "bin" / "ola-webhook-bridge.py"
WRAPPER_PATH = OPENCLAW_DIR / "bin" / "ola-webhook-bridge-wrapper.sh"
PLIST_PATH = (
    OPENCLAW_DIR / "launchagents" / "ai.openclaw.ola-webhook-bridge.plist"
)
PULL_PATH = OPENCLAW_DIR / "bin" / "dotfiles-pull.command"

SPEC = importlib.util.spec_from_file_location("ola_webhook_bridge", BRIDGE_PATH)
assert SPEC is not None and SPEC.loader is not None
BRIDGE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = BRIDGE
SPEC.loader.exec_module(BRIDGE)


class CapturingUpstream(BaseHTTPRequestHandler):
    requests: list[dict[str, object]] = []
    response_status = 200

    def log_message(self, _format: str, *_args: object) -> None:
        return

    def do_POST(self) -> None:  # noqa: N802
        length = int(self.headers["Content-Length"])
        body = self.rfile.read(length)
        type(self).requests.append(
            {
                "path": self.path,
                "body": body,
                "token": self.headers.get("X-OpenClaw-Token"),
                "signature": self.headers.get("X-Hub-Signature-256"),
                "content_type": self.headers.get("Content-Type"),
            }
        )
        self.send_response(type(self).response_status)
        self.send_header("Content-Length", "0")
        self.end_headers()


class OlaWebhookBridgeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        CapturingUpstream.requests = []
        cls.upstream = ThreadingHTTPServer(("127.0.0.1", 0), CapturingUpstream)
        cls.upstream_thread = threading.Thread(
            target=cls.upstream.serve_forever,
            daemon=True,
        )
        cls.upstream_thread.start()

        cls.secret_text = "w" * 48
        cls.hook_token = "h" * 48
        base_config = BRIDGE.load_config(
            {
                "OLA_WEBHOOK_SECRET": cls.secret_text,
                "OPENCLAW_HOOK_TOKEN": cls.hook_token,
            }
        )
        cls.config = replace(
            base_config,
            listen_port=0,
            upstream_port=cls.upstream.server_address[1],
        )
        cls.bridge = BRIDGE.make_server(cls.config)
        cls.bridge_thread = threading.Thread(
            target=cls.bridge.serve_forever,
            daemon=True,
        )
        cls.bridge_thread.start()

    @classmethod
    def tearDownClass(cls) -> None:
        cls.bridge.shutdown()
        cls.bridge.server_close()
        cls.upstream.shutdown()
        cls.upstream.server_close()
        cls.bridge_thread.join(timeout=2)
        cls.upstream_thread.join(timeout=2)

    def setUp(self) -> None:
        CapturingUpstream.requests = []
        CapturingUpstream.response_status = 200

    def request(
        self,
        method: str,
        path: str,
        body: bytes = b"",
        *,
        signature: Optional[str] = None,
        content_type: str = "application/json",
    ) -> tuple[int, dict[str, object]]:
        connection = http.client.HTTPConnection(
            "127.0.0.1",
            self.bridge.server_address[1],
            timeout=2,
        )
        headers = {"Content-Type": content_type}
        if signature is not None:
            headers["X-Hub-Signature-256"] = signature
        connection.request(method, path, body=body, headers=headers)
        response = connection.getresponse()
        payload = json.loads(response.read())
        connection.close()
        return response.status, payload

    def signature_for(self, body: bytes) -> str:
        digest = hmac.new(
            self.secret_text.encode("utf-8"), body, hashlib.sha256
        ).hexdigest()
        return f"sha256={digest}"

    def test_valid_signed_body_causes_canonical_wake_with_internal_token(self) -> None:
        body = b'{"text":"untrusted Ola envelope text","mode":"next-heartbeat"}'

        status, payload = self.request(
            "POST",
            "/hooks/wake",
            body,
            signature=self.signature_for(body),
        )

        self.assertEqual(status, 200)
        self.assertEqual(payload, {"ok": True})
        self.assertEqual(len(CapturingUpstream.requests), 1)
        forwarded = CapturingUpstream.requests[0]
        self.assertEqual(forwarded["path"], "/hooks/wake")
        self.assertEqual(forwarded["body"], BRIDGE.CANONICAL_WAKE_BODY)
        self.assertNotIn(b"untrusted Ola envelope text", forwarded["body"])
        self.assertEqual(forwarded["token"], self.hook_token)
        self.assertIsNone(forwarded["signature"])

    def test_invalid_or_missing_signature_is_rejected_without_forwarding(self) -> None:
        body = b'{"text":"ignored"}'
        for signature in (None, "sha256=" + ("0" * 64), "not-a-signature"):
            with self.subTest(signature=signature):
                status, payload = self.request(
                    "POST", "/hooks/wake", body, signature=signature
                )
                self.assertEqual(status, 401)
                self.assertEqual(payload, {"ok": False})
        self.assertEqual(CapturingUpstream.requests, [])

    def test_wrong_path_type_and_oversize_body_are_rejected(self) -> None:
        body = b"{}"
        status, _ = self.request(
            "POST", "/hooks/agent", body, signature=self.signature_for(body)
        )
        self.assertEqual(status, 404)

        status, _ = self.request(
            "POST",
            "/hooks/wake",
            body,
            signature=self.signature_for(body),
            content_type="text/plain",
        )
        self.assertEqual(status, 415)

        large_body = b"x" * (self.config.max_body_bytes + 1)
        status, _ = self.request(
            "POST",
            "/hooks/wake",
            large_body,
            signature=self.signature_for(large_body),
        )
        self.assertEqual(status, 413)
        self.assertEqual(CapturingUpstream.requests, [])

    def test_signed_malformed_or_non_object_json_is_not_forwarded(self) -> None:
        for body in (b"{", b"[]", b'"text"'):
            with self.subTest(body=body):
                status, payload = self.request(
                    "POST",
                    "/hooks/wake",
                    body,
                    signature=self.signature_for(body),
                )
                self.assertEqual(status, 400)
                self.assertEqual(payload, {"ok": False})
        self.assertEqual(CapturingUpstream.requests, [])

    def test_duplicate_signature_header_is_rejected(self) -> None:
        body = b'{"text":"ignored"}'
        connection = http.client.HTTPConnection(
            "127.0.0.1",
            self.bridge.server_address[1],
            timeout=2,
        )
        connection.putrequest("POST", "/hooks/wake")
        connection.putheader("Content-Type", "application/json")
        connection.putheader("Content-Length", str(len(body)))
        connection.putheader("X-Hub-Signature-256", self.signature_for(body))
        connection.putheader("X-Hub-Signature-256", self.signature_for(body))
        connection.endheaders(body)
        response = connection.getresponse()
        payload = json.loads(response.read())
        connection.close()

        self.assertEqual(response.status, 400)
        self.assertEqual(payload, {"ok": False})
        self.assertEqual(CapturingUpstream.requests, [])

    def test_upstream_rejection_is_reported_as_retryable_gateway_failure(self) -> None:
        CapturingUpstream.response_status = 400
        body = b'{"text":"bad upstream payload"}'

        status, payload = self.request(
            "POST",
            "/hooks/wake",
            body,
            signature=self.signature_for(body),
        )

        self.assertEqual(status, 502)
        self.assertEqual(payload, {"ok": False})
        self.assertEqual(len(CapturingUpstream.requests), 1)

    def test_config_rejects_non_loopback_listener_and_invalid_ports(self) -> None:
        base = {
            "OLA_WEBHOOK_SECRET": self.secret_text,
            "OPENCLAW_HOOK_TOKEN": self.hook_token,
        }
        with self.assertRaises(BRIDGE.ConfigurationError):
            BRIDGE.load_config({**base, "OLA_BRIDGE_LISTEN_HOST": "0.0.0.0"})
        with self.assertRaises(BRIDGE.ConfigurationError):
            BRIDGE.load_config(
                {**base, "OLA_BRIDGE_UPSTREAM_PORT": "443"}
            )
        with self.assertRaises(BRIDGE.ConfigurationError):
            BRIDGE.load_config(
                {
                    "OLA_WEBHOOK_SECRET": self.secret_text,
                    "OPENCLAW_HOOK_TOKEN": self.secret_text,
                }
            )

    def test_expected_client_reset_does_not_emit_a_traceback(self) -> None:
        error = ConnectionResetError("client reset")
        with mock.patch.object(
            BRIDGE.sys, "exc_info", return_value=(ConnectionResetError, error, None)
        ), mock.patch.object(BRIDGE, "_safe_log") as safe_log:
            self.bridge.handle_error(mock.Mock(), ("127.0.0.1", 12345))

        safe_log.assert_not_called()

    def test_launchagent_and_wrapper_are_cache_only_and_loopback_scoped(self) -> None:
        wrapper = WRAPPER_PATH.read_text(encoding="utf-8")
        self.assertIn('source "$CACHE"', wrapper)
        self.assertIn("OLA_WEBHOOK_SECRET", wrapper)
        self.assertIn("OPENCLAW_HOOK_TOKEN", wrapper)
        self.assertIn('OPENCLAW_HOOK_TOKEN="$OLA_HOOK_TOKEN"', wrapper)
        self.assertNotIn("op read", wrapper)
        self.assertNotIn("0.0.0.0", wrapper)

        with PLIST_PATH.open("rb") as plist_file:
            plist = plistlib.load(plist_file)
        self.assertEqual(plist["Label"], "ai.openclaw.ola-webhook-bridge")
        self.assertTrue(plist["RunAtLoad"])
        self.assertTrue(plist["KeepAlive"])
        self.assertEqual(plist["Umask"], 0o77)
        self.assertNotIn("OLA_WEBHOOK_SECRET", plist.get("EnvironmentVariables", {}))

        pull = PULL_PATH.read_text(encoding="utf-8")
        managed_section = pull[pull.index("# Refresh the Ola HMAC bridge") :]
        managed_section = managed_section[: managed_section.index("# Refresh the Nest")]
        self.assertIn('if [ -e "$OLA_BRIDGE_DST" ]', managed_section)
        self.assertIn("remains unloaded pending explicit bootstrap", managed_section)
        self.assertIn("OLA_BRIDGE_HEALTH_OK", managed_section)
        self.assertIn("http://127.0.0.1:18790/healthz", managed_section)
        self.assertNotIn(
            'else\n    launchctl bootstrap "$OLA_BRIDGE_DOMAIN"', managed_section
        )


if __name__ == "__main__":
    unittest.main()
