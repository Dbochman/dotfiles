#!/usr/bin/env python3
"""Behavioral tests for the canonical Cielo refresh client."""

from __future__ import annotations

import http.server
import json
import os
from pathlib import Path
import stat
import subprocess
import tempfile
import threading
import time
import unittest


REPO_ROOT = Path(__file__).resolve().parents[2]
AUTH_HELPER = REPO_ROOT / "openclaw" / "bin" / "cielo-auth.py"


class RefreshHandler(http.server.BaseHTTPRequestHandler):
    response_status = 200
    response_payload: dict[str, object] = {}
    requests: list[dict[str, object]] = []

    def do_POST(self) -> None:  # noqa: N802 - stdlib hook name
        length = int(self.headers.get("Content-Length", "0"))
        body = json.loads(self.rfile.read(length))
        type(self).requests.append(
            {
                "authorization": self.headers.get("authorization"),
                "body": body,
                "path": self.path,
            }
        )
        encoded = json.dumps(type(self).response_payload).encode("utf-8")
        self.send_response(type(self).response_status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def log_message(self, _format: str, *_args: object) -> None:
        pass


class CieloAuthTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.config = self.root / "config.json"
        self.lock = self.root / "auth.lock"
        RefreshHandler.requests = []

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def write_config(self, **overrides: object) -> bytes:
        payload: dict[str, object] = {
            "accessToken": "old-access",
            "apiKey": "public-client-key",
            "expiresIn": int(time.time()) - 60,
            "refreshToken": "old-refresh",
            "unrelated": "preserved",
        }
        payload.update(overrides)
        raw = (json.dumps(payload, indent=2) + "\n").encode("utf-8")
        self.config.write_bytes(raw)
        self.config.chmod(0o600)
        return raw

    def environment(self, server: http.server.HTTPServer | None = None) -> dict[str, str]:
        environment = os.environ.copy()
        environment["CIELO_CONFIG_FILE"] = str(self.config)
        environment["CIELO_AUTH_LOCK_FILE"] = str(self.lock)
        if server is not None:
            host, port = server.server_address
            environment["CIELO_API_BASE_URL"] = f"http://{host}:{port}"
            environment["CIELO_AUTH_ALLOW_HTTP"] = "true"
        return environment

    def run_helper(
        self,
        *arguments: str,
        server: http.server.HTTPServer | None = None,
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [str(AUTH_HELPER), *arguments],
            capture_output=True,
            check=False,
            env=self.environment(server),
            text=True,
            timeout=10,
        )

    def start_server(self) -> tuple[http.server.HTTPServer, threading.Thread]:
        server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), RefreshHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        return server, thread

    def test_success_uses_current_contract_and_rotates_atomically(self) -> None:
        self.write_config()
        RefreshHandler.response_status = 200
        RefreshHandler.response_payload = {
            "status": 200,
            "message": "SUCCESS",
            "data": {
                "accessToken": "new-access",
                "refreshToken": "new-refresh",
                "expiresIn": int(time.time()) + 3600,
            },
        }
        server, thread = self.start_server()
        try:
            result = self.run_helper("refresh", "--force", server=server)
        finally:
            server.shutdown()
            thread.join(timeout=2)
            server.server_close()

        self.assertEqual(result.returncode, 0, result.stderr)
        public = json.loads(result.stdout)
        self.assertEqual(public["method"], "api-refresh-v1")
        self.assertTrue(public["access_rotated"])
        self.assertTrue(public["refresh_rotated"])
        self.assertNotIn("new-access", result.stdout)
        request = RefreshHandler.requests[0]
        self.assertEqual(request["path"], "/web/token/refresh/1")
        self.assertEqual(request["authorization"], "old-access")
        self.assertEqual(
            request["body"],
            {"locale": "en", "refreshToken": "old-refresh"},
        )
        saved = json.loads(self.config.read_text(encoding="utf-8"))
        self.assertEqual(saved["accessToken"], "new-access")
        self.assertEqual(saved["refreshToken"], "new-refresh")
        self.assertEqual(saved["unrelated"], "preserved")
        self.assertEqual(stat.S_IMODE(self.config.stat().st_mode), 0o600)

    def test_rejected_refresh_preserves_existing_config(self) -> None:
        original = self.write_config()
        RefreshHandler.response_status = 401
        RefreshHandler.response_payload = {"status": 401, "message": "Unauthorized"}
        server, thread = self.start_server()
        try:
            result = self.run_helper("refresh", "--force", server=server)
        finally:
            server.shutdown()
            thread.join(timeout=2)
            server.server_close()

        self.assertEqual(result.returncode, 10)
        public = json.loads(result.stdout)
        self.assertEqual(public["category"], "refresh_rejected")
        self.assertEqual(public["http_status"], 401)
        self.assertEqual(self.config.read_bytes(), original)

    def test_fresh_token_skips_network_refresh(self) -> None:
        self.write_config(expiresIn=int(time.time()) + 3600)
        result = self.run_helper("refresh")

        self.assertEqual(result.returncode, 0, result.stderr)
        public = json.loads(result.stdout)
        self.assertEqual(public["method"], "cached")
        self.assertEqual(public["status"], "fresh")

    def test_insecure_config_is_rejected(self) -> None:
        self.write_config()
        self.config.chmod(0o644)
        result = self.run_helper("check")

        self.assertEqual(result.returncode, 12)
        self.assertEqual(json.loads(result.stdout)["category"], "configuration_unsafe")


if __name__ == "__main__":
    unittest.main()
