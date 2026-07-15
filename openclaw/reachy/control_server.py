"""Owner-only local control interface for a running ClawBody app."""

import asyncio
import json
import logging
import os
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

from reachy_mini_openclaw.tools.core_tools import dispatch_tool_call

logger = logging.getLogger(__name__)

DEFAULT_SOCKET_PATH = f"/run/user/{os.getuid()}/clawbody-control.sock"
COMMAND_ALIASES = {"see": "camera", "stop": "stop_moves"}
ALLOWED_COMMANDS = {
    "look", "camera", "dance", "emotion", "presets", "stop_moves", "idle"
}


class ClawBodyControlServer:
    """Serve constrained robot commands over an owner-only Unix socket."""

    def __init__(self, deps: Any, socket_path: str | None = None):
        self.deps = deps
        self.socket_path = Path(socket_path or os.getenv("CLAWBODY_CONTROL_SOCKET", DEFAULT_SOCKET_PATH))
        self._server: asyncio.AbstractServer | None = None
        self._speak_callback: Callable[[str], Awaitable[dict[str, Any]]] | None = None

    def set_speak_callback(
        self, callback: Callable[[str], Awaitable[dict[str, Any]]]
    ) -> None:
        self._speak_callback = callback

    async def start(self) -> None:
        self.socket_path.parent.mkdir(parents=True, exist_ok=True)
        if self.socket_path.exists() or self.socket_path.is_socket():
            self.socket_path.unlink()
        self._server = await asyncio.start_unix_server(self._handle_client, path=str(self.socket_path), limit=65536)
        os.chmod(self.socket_path, 0o600)
        logger.info("ClawBody control socket ready at %s", self.socket_path)

    def stop(self) -> None:
        if self._server is not None:
            self._server.close()
            self._server = None
        try:
            self.socket_path.unlink(missing_ok=True)
        except OSError as exc:
            logger.debug("Could not remove control socket: %s", exc)

    async def _status(self) -> dict[str, Any]:
        face_detected = False
        try:
            face_detected = bool(self.deps.robot.get_tracked_face(wait=False).detected)
        except Exception as exc:
            logger.debug("Could not read tracked face: %s", exc)
        return {
            "status": "success",
            "app": "clawbody",
            "control": "ready",
            "face_tracking_mode": (
                "speech_activated" if self.deps.daemon_face_tracking else "disabled"
            ),
            "face_detected": face_detected,
        }

    async def _execute(self, request: Any) -> dict[str, Any]:
        if not isinstance(request, dict):
            return {"error": "Request must be a JSON object"}
        command = request.get("command")
        arguments = request.get("arguments", {})
        if command == "status":
            return await self._status()
        if not isinstance(command, str):
            return {"error": "Missing command"}
        if not isinstance(arguments, dict):
            return {"error": "Arguments must be a JSON object"}
        if command == "speak":
            text = arguments.get("text")
            if not isinstance(text, str) or not text.strip():
                return {"error": "Speech text must be a non-empty string"}
            if len(text) > 4000:
                return {"error": "Speech text exceeds 4000 characters"}
            if self._speak_callback is None:
                return {"error": "Speech transport is not ready"}
            return await self._speak_callback(text)
        command = COMMAND_ALIASES.get(command, command)
        if command not in ALLOWED_COMMANDS:
            return {"error": f"Unsupported command: {command}"}
        return await dispatch_tool_call(command, json.dumps(arguments), self.deps)

    async def _handle_client(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            raw = await asyncio.wait_for(reader.readline(), timeout=10)
            if not raw or len(raw) > 65535:
                response = {"error": "Empty or oversized request"}
            else:
                try:
                    request = json.loads(raw)
                except (json.JSONDecodeError, UnicodeDecodeError):
                    response = {"error": "Invalid JSON request"}
                else:
                    response = await self._execute(request)
            writer.write(json.dumps(response, separators=(",", ":")).encode() + b"\n")
            await writer.drain()
        except Exception as exc:
            logger.error("Control request failed: %s", exc, exc_info=True)
        finally:
            writer.close()
            try:
                await writer.wait_closed()
            except Exception:
                pass
