#!/usr/bin/env python3
"""Verify Ola webhook signatures and relay valid wakes to loopback OpenClaw."""

from __future__ import annotations

import hashlib
import hmac
import http.client
import json
import os
import socket
import sys
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Dict, Final, Optional, Tuple, Type


DEFAULT_LISTEN_HOST: Final = "127.0.0.1"
DEFAULT_LISTEN_PORT: Final = 18790
DEFAULT_CALLBACK_PATH: Final = "/hooks/wake"
DEFAULT_UPSTREAM_PORT: Final = 18789
DEFAULT_MAX_BODY_BYTES: Final = 16_384
DEFAULT_UPSTREAM_TIMEOUT_SECONDS: Final = 10.0
DEFAULT_CONNECTION_TIMEOUT_SECONDS: Final = 5.0
DEFAULT_MAX_WORKERS: Final = 8
BRIDGE_USER_AGENT: Final = "OpenClaw-Ola-Webhook-Bridge/1.0"
CANONICAL_WAKE_BODY: Final = json.dumps(
    {
        "text": (
            "Ola reports new activity. Perform the bounded Ola inbox check "
            "defined in HEARTBEAT.md."
        ),
        "mode": "now",
    },
    separators=(",", ":"),
).encode("utf-8")


class ConfigurationError(ValueError):
    """Raised when the bridge environment is unsafe or incomplete."""


@dataclass(frozen=True)
class BridgeConfig:
    webhook_secret: bytes
    openclaw_hook_token: str
    listen_host: str = DEFAULT_LISTEN_HOST
    listen_port: int = DEFAULT_LISTEN_PORT
    callback_path: str = DEFAULT_CALLBACK_PATH
    upstream_port: int = DEFAULT_UPSTREAM_PORT
    max_body_bytes: int = DEFAULT_MAX_BODY_BYTES
    upstream_timeout_seconds: float = DEFAULT_UPSTREAM_TIMEOUT_SECONDS
    connection_timeout_seconds: float = DEFAULT_CONNECTION_TIMEOUT_SECONDS
    max_workers: int = DEFAULT_MAX_WORKERS


def _required_secret(environment: dict[str, str], key: str) -> str:
    value = environment.get(key, "")
    if not value or value.strip() != value or len(value) < 32:
        raise ConfigurationError(f"{key} is missing or invalid")
    return value


def load_config(environment: Optional[Dict[str, str]] = None) -> BridgeConfig:
    env = dict(os.environ if environment is None else environment)
    webhook_secret = _required_secret(env, "OLA_WEBHOOK_SECRET")
    hook_token = _required_secret(env, "OPENCLAW_HOOK_TOKEN")
    if hmac.compare_digest(webhook_secret, hook_token):
        raise ConfigurationError("public and loopback hook secrets must be distinct")
    listen_host = env.get("OLA_BRIDGE_LISTEN_HOST", DEFAULT_LISTEN_HOST)
    callback_path = env.get("OLA_BRIDGE_CALLBACK_PATH", DEFAULT_CALLBACK_PATH)

    if listen_host not in {"127.0.0.1", "::1"}:
        raise ConfigurationError("OLA_BRIDGE_LISTEN_HOST must be loopback")
    if callback_path != DEFAULT_CALLBACK_PATH:
        raise ConfigurationError("OLA_BRIDGE_CALLBACK_PATH must be /hooks/wake")

    try:
        listen_port = int(env.get("OLA_BRIDGE_LISTEN_PORT", str(DEFAULT_LISTEN_PORT)))
        upstream_port = int(
            env.get("OLA_BRIDGE_UPSTREAM_PORT", str(DEFAULT_UPSTREAM_PORT))
        )
        max_body_bytes = int(
            env.get("OLA_BRIDGE_MAX_BODY_BYTES", str(DEFAULT_MAX_BODY_BYTES))
        )
        upstream_timeout_seconds = float(
            env.get(
                "OLA_BRIDGE_UPSTREAM_TIMEOUT_SECONDS",
                str(DEFAULT_UPSTREAM_TIMEOUT_SECONDS),
            )
        )
        connection_timeout_seconds = float(
            env.get(
                "OLA_BRIDGE_CONNECTION_TIMEOUT_SECONDS",
                str(DEFAULT_CONNECTION_TIMEOUT_SECONDS),
            )
        )
        max_workers = int(env.get("OLA_BRIDGE_MAX_WORKERS", str(DEFAULT_MAX_WORKERS)))
    except ValueError as exc:
        raise ConfigurationError("numeric bridge settings are invalid") from exc

    if not 1024 <= listen_port <= 65535:
        raise ConfigurationError("OLA_BRIDGE_LISTEN_PORT is invalid")
    if not 1024 <= upstream_port <= 65535:
        raise ConfigurationError("OLA_BRIDGE_UPSTREAM_PORT is invalid")
    if not 1024 <= max_body_bytes <= 1_048_576:
        raise ConfigurationError("OLA_BRIDGE_MAX_BODY_BYTES is invalid")
    if not 0.5 <= upstream_timeout_seconds <= 30:
        raise ConfigurationError("OLA_BRIDGE_UPSTREAM_TIMEOUT_SECONDS is invalid")
    if not 0.5 <= connection_timeout_seconds <= 30:
        raise ConfigurationError("OLA_BRIDGE_CONNECTION_TIMEOUT_SECONDS is invalid")
    if not 1 <= max_workers <= 32:
        raise ConfigurationError("OLA_BRIDGE_MAX_WORKERS is invalid")

    return BridgeConfig(
        webhook_secret=webhook_secret.encode("utf-8"),
        openclaw_hook_token=hook_token,
        listen_host=listen_host,
        listen_port=listen_port,
        callback_path=callback_path,
        upstream_port=upstream_port,
        max_body_bytes=max_body_bytes,
        upstream_timeout_seconds=upstream_timeout_seconds,
        connection_timeout_seconds=connection_timeout_seconds,
        max_workers=max_workers,
    )


def verify_signature(secret: bytes, body: bytes, signature: Optional[str]) -> bool:
    if signature is None or len(signature) != 71 or not signature.startswith("sha256="):
        return False
    supplied_digest = signature[7:]
    if any(character not in "0123456789abcdef" for character in supplied_digest):
        return False
    expected = "sha256=" + hmac.new(secret, body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature)


def _safe_log(event: str, **fields: object) -> None:
    parts = [
        datetime.now(timezone.utc).isoformat(timespec="seconds"),
        f"event={event}",
    ]
    for key, value in fields.items():
        rendered = str(value).replace("\n", " ").replace("\r", " ")[:128]
        parts.append(f"{key}={rendered}")
    print(" ".join(parts), flush=True)


def _forward_to_openclaw(config: BridgeConfig) -> int:
    connection = http.client.HTTPConnection(
        "127.0.0.1",
        config.upstream_port,
        timeout=config.upstream_timeout_seconds,
    )
    try:
        connection.request(
            "POST",
            DEFAULT_CALLBACK_PATH,
            body=CANONICAL_WAKE_BODY,
            headers={
                "Content-Type": "application/json",
                "Content-Length": str(len(CANONICAL_WAKE_BODY)),
                "User-Agent": BRIDGE_USER_AGENT,
                "X-OpenClaw-Token": config.openclaw_hook_token,
            },
        )
        response = connection.getresponse()
        response.read(config.max_body_bytes + 1)
        return response.status
    finally:
        connection.close()


class BoundedThreadingHTTPServer(ThreadingHTTPServer):
    daemon_threads = True
    request_queue_size = 16

    def __init__(
        self,
        server_address: Tuple[str, int],
        request_handler_class: Type[BaseHTTPRequestHandler],
        *,
        connection_timeout_seconds: float,
        max_workers: int,
    ) -> None:
        self.connection_timeout_seconds = connection_timeout_seconds
        self._worker_slots = threading.BoundedSemaphore(max_workers)
        super().__init__(server_address, request_handler_class)

    def get_request(self) -> Tuple[socket.socket, object]:
        client_socket, address = super().get_request()
        client_socket.settimeout(self.connection_timeout_seconds)
        return client_socket, address

    def process_request(self, request_socket: socket.socket, client_address: object) -> None:
        if not self._worker_slots.acquire(blocking=False):
            self.shutdown_request(request_socket)
            return
        try:
            super().process_request(request_socket, client_address)
        except BaseException:
            self._worker_slots.release()
            raise

    def process_request_thread(
        self, request_socket: socket.socket, client_address: object
    ) -> None:
        try:
            super().process_request_thread(request_socket, client_address)
        finally:
            self._worker_slots.release()

    def handle_error(self, request_socket: socket.socket, client_address: object) -> None:
        error_type = sys.exc_info()[0]
        if error_type is not None and issubclass(
            error_type, (ConnectionError, TimeoutError, OSError)
        ):
            return
        _safe_log(
            "handler_failure",
            reason=error_type.__name__ if error_type is not None else "unknown",
        )


def make_handler(config: BridgeConfig) -> Type[BaseHTTPRequestHandler]:
    class OlaWebhookHandler(BaseHTTPRequestHandler):
        server_version = "OlaWebhookBridge"
        sys_version = ""

        def log_message(self, _format: str, *_args: object) -> None:
            return

        def _send_json(self, status: int, payload: dict[str, object]) -> None:
            encoded = json.dumps(payload, separators=(",", ":")).encode("utf-8")
            try:
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(encoded)))
                self.send_header("Cache-Control", "no-store")
                self.send_header("X-Content-Type-Options", "nosniff")
                self.end_headers()
                self.wfile.write(encoded)
            except (BrokenPipeError, ConnectionResetError, TimeoutError, OSError):
                return

        def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API.
            if self.path == "/healthz":
                self._send_json(HTTPStatus.OK, {"ok": True})
                return
            self._send_json(HTTPStatus.NOT_FOUND, {"ok": False})

        def do_POST(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API.
            if self.path != config.callback_path:
                self._send_json(HTTPStatus.NOT_FOUND, {"ok": False})
                return
            if self.headers.get_all("Transfer-Encoding", []):
                self._send_json(HTTPStatus.BAD_REQUEST, {"ok": False})
                return

            length_headers = self.headers.get_all("Content-Length", [])
            if len(length_headers) != 1:
                self._send_json(HTTPStatus.BAD_REQUEST, {"ok": False})
                return
            raw_length = length_headers[0]
            try:
                content_length = int(raw_length)
            except ValueError:
                self._send_json(HTTPStatus.BAD_REQUEST, {"ok": False})
                return
            if content_length <= 0:
                self._send_json(HTTPStatus.BAD_REQUEST, {"ok": False})
                return
            if content_length > config.max_body_bytes:
                self._send_json(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, {"ok": False})
                return

            content_type_headers = self.headers.get_all("Content-Type", [])
            if len(content_type_headers) != 1:
                self._send_json(HTTPStatus.BAD_REQUEST, {"ok": False})
                return
            content_type = content_type_headers[0]
            if content_type.split(";", 1)[0].strip().lower() != "application/json":
                self._send_json(HTTPStatus.UNSUPPORTED_MEDIA_TYPE, {"ok": False})
                return

            try:
                body = self.rfile.read(content_length)
            except (TimeoutError, OSError):
                self._send_json(HTTPStatus.REQUEST_TIMEOUT, {"ok": False})
                return
            if len(body) != content_length:
                self._send_json(HTTPStatus.BAD_REQUEST, {"ok": False})
                return
            signature_headers = self.headers.get_all("X-Hub-Signature-256", [])
            if not signature_headers:
                self._send_json(HTTPStatus.UNAUTHORIZED, {"ok": False})
                return
            if len(signature_headers) != 1:
                self._send_json(HTTPStatus.BAD_REQUEST, {"ok": False})
                return
            signature = signature_headers[0]
            if not verify_signature(config.webhook_secret, body, signature):
                self._send_json(HTTPStatus.UNAUTHORIZED, {"ok": False})
                return
            try:
                payload = json.loads(body)
            except (json.JSONDecodeError, UnicodeDecodeError):
                self._send_json(HTTPStatus.BAD_REQUEST, {"ok": False})
                return
            if not isinstance(payload, dict):
                self._send_json(HTTPStatus.BAD_REQUEST, {"ok": False})
                return

            try:
                upstream_status = _forward_to_openclaw(config)
            except (http.client.HTTPException, TimeoutError, OSError) as exc:
                _safe_log("upstream_failure", reason=type(exc).__name__)
                self._send_json(HTTPStatus.BAD_GATEWAY, {"ok": False})
                return

            if not 200 <= upstream_status < 300:
                _safe_log("upstream_failure", status=upstream_status)
                self._send_json(HTTPStatus.BAD_GATEWAY, {"ok": False})
                return

            _safe_log("accepted", upstream_status=upstream_status, body_bytes=len(body))
            self._send_json(HTTPStatus.OK, {"ok": True})

    return OlaWebhookHandler


def make_server(config: BridgeConfig) -> BoundedThreadingHTTPServer:
    server = BoundedThreadingHTTPServer(
        (config.listen_host, config.listen_port),
        make_handler(config),
        connection_timeout_seconds=config.connection_timeout_seconds,
        max_workers=config.max_workers,
    )
    return server


def main() -> int:
    try:
        config = load_config()
        server = make_server(config)
    except (ConfigurationError, OSError) as exc:
        _safe_log("startup_failure", reason=type(exc).__name__)
        return 1

    _safe_log("ready", host=config.listen_host, port=config.listen_port)
    try:
        server.serve_forever(poll_interval=0.5)
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
