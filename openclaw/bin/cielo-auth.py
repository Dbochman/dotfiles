#!/usr/bin/env python3
"""Refresh and inspect the protected Cielo authentication state.

This is the canonical refresh client for OpenClaw's Cielo integration.  It
serializes rotating refresh-token use, writes the credential file atomically,
and emits only safe operational metadata.
"""

from __future__ import annotations

import argparse
import contextlib
import fcntl
import json
import os
from pathlib import Path
import stat
import tempfile
import time
from typing import Any, Iterator
import urllib.error
import urllib.parse
import urllib.request


API_ORIGIN = "https://home.cielowigle.com"
DEFAULT_API_BASE = "https://api.smartcielo.com"
REFRESH_PATH = "/web/token/refresh/1"
DEFAULT_TIMEOUT_SECONDS = 15
DEFAULT_LOCK_TIMEOUT_SECONDS = 30
DEFAULT_REFRESH_SKEW_SECONDS = 300


class CieloAuthError(RuntimeError):
    def __init__(
        self,
        category: str,
        *,
        http_status: int | None = None,
        exit_code: int = 1,
    ) -> None:
        super().__init__(category)
        self.category = category
        self.http_status = http_status
        self.exit_code = exit_code


def config_path() -> Path:
    override = os.environ.get("CIELO_CONFIG_FILE")
    if override:
        return Path(override).expanduser()
    return Path.home() / ".config" / "cielo" / "config.json"


def lock_path(config: Path) -> Path:
    override = os.environ.get("CIELO_AUTH_LOCK_FILE")
    if override:
        return Path(override).expanduser()
    return config.with_name("auth.lock")


def positive_number(name: str, default: float) -> float:
    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        value = float(raw)
    except ValueError as exc:
        raise CieloAuthError("invalid_runtime_configuration", exit_code=12) from exc
    if value <= 0:
        raise CieloAuthError("invalid_runtime_configuration", exit_code=12)
    return value


def emit(payload: dict[str, Any], *, quiet: bool) -> None:
    if not quiet:
        print(json.dumps(payload, separators=(",", ":"), sort_keys=True))


def validate_api_base() -> str:
    base = os.environ.get("CIELO_API_BASE_URL", DEFAULT_API_BASE).rstrip("/")
    parsed = urllib.parse.urlparse(base)
    allow_http = os.environ.get("CIELO_AUTH_ALLOW_HTTP") == "true"
    if parsed.scheme != "https" and not (allow_http and parsed.scheme == "http"):
        raise CieloAuthError("invalid_api_base", exit_code=12)
    if not parsed.hostname:
        raise CieloAuthError("invalid_api_base", exit_code=12)
    if not allow_http and parsed.hostname != "api.smartcielo.com":
        raise CieloAuthError("invalid_api_base", exit_code=12)
    return base


def validate_protected_file(path: Path) -> None:
    try:
        info = path.lstat()
    except FileNotFoundError as exc:
        raise CieloAuthError("configuration_missing", exit_code=12) from exc
    if not stat.S_ISREG(info.st_mode) or path.is_symlink():
        raise CieloAuthError("configuration_unsafe", exit_code=12)
    if info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) & 0o077:
        raise CieloAuthError("configuration_unsafe", exit_code=12)


def load_config(path: Path) -> dict[str, Any]:
    validate_protected_file(path)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise CieloAuthError("configuration_invalid", exit_code=12) from exc
    if not isinstance(payload, dict):
        raise CieloAuthError("configuration_invalid", exit_code=12)
    return payload


def atomic_write_config(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary_path = Path(temporary)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_path, path)
        os.chmod(path, 0o600)
        directory_fd = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        with contextlib.suppress(FileNotFoundError):
            temporary_path.unlink()


@contextlib.contextmanager
def auth_lock(path: Path) -> Iterator[None]:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    flags = os.O_CREAT | os.O_RDWR
    if hasattr(os, "O_CLOEXEC"):
        flags |= os.O_CLOEXEC
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        fd = os.open(path, flags, 0o600)
    except OSError as exc:
        raise CieloAuthError("lock_unavailable", exit_code=13) from exc
    try:
        os.fchmod(fd, 0o600)
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid():
            raise CieloAuthError("lock_unsafe", exit_code=13)
        deadline = time.monotonic() + positive_number(
            "CIELO_AUTH_LOCK_TIMEOUT_SECONDS", DEFAULT_LOCK_TIMEOUT_SECONDS
        )
        while True:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    raise CieloAuthError("lock_timeout", exit_code=13)
                time.sleep(0.1)
        yield
    finally:
        with contextlib.suppress(OSError):
            fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)


def expiry_epoch(config: dict[str, Any]) -> float | None:
    raw = config.get("expiresIn")
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return None
    if value > 100_000_000_000:
        value /= 1000
    return value if value > 0 else None


def refresh_needed(config: dict[str, Any]) -> bool:
    expires = expiry_epoch(config)
    if expires is None:
        return True
    skew = positive_number(
        "CIELO_REFRESH_SKEW_SECONDS", DEFAULT_REFRESH_SKEW_SECONDS
    )
    return expires <= time.time() + skew


def api_key(config: dict[str, Any]) -> str:
    value = os.environ.get("CIELO_API_KEY") or config.get("apiKey")
    if not isinstance(value, str) or not value.strip():
        raise CieloAuthError("api_key_missing", exit_code=12)
    return value.strip()


def required_token(config: dict[str, Any], name: str) -> str:
    value = config.get(name)
    if not isinstance(value, str) or not value.strip():
        raise CieloAuthError(f"{name}_missing", exit_code=12)
    return value.strip()


def refresh_request(config: dict[str, Any]) -> tuple[int, dict[str, Any] | None]:
    base = validate_api_base()
    timeout = positive_number("CIELO_REFRESH_TIMEOUT_SECONDS", DEFAULT_TIMEOUT_SECONDS)
    request = urllib.request.Request(
        f"{base}{REFRESH_PATH}",
        data=json.dumps(
            {
                "locale": "en",
                "refreshToken": required_token(config, "refreshToken"),
            },
            separators=(",", ":"),
        ).encode("utf-8"),
        method="POST",
        headers={
            "Content-Type": "application/json; charset=UTF-8",
            "x-api-key": api_key(config),
            "authorization": required_token(config, "accessToken"),
            "Origin": API_ORIGIN,
            "Referer": f"{API_ORIGIN}/",
            "User-Agent": "OpenClaw-Cielo-Refresh/1",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            status = response.status
            body = response.read()
    except urllib.error.HTTPError as exc:
        status = exc.code
        body = exc.read()
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise CieloAuthError("network_error", exit_code=11) from exc
    try:
        parsed = json.loads(body)
    except (json.JSONDecodeError, UnicodeDecodeError):
        parsed = None
    return status, parsed if isinstance(parsed, dict) else None


def classify_http_failure(status: int) -> CieloAuthError:
    if status == 401:
        return CieloAuthError("refresh_rejected", http_status=status, exit_code=10)
    if status == 403:
        return CieloAuthError("refresh_forbidden", http_status=status, exit_code=10)
    if status == 429:
        return CieloAuthError("rate_limited", http_status=status, exit_code=11)
    if status >= 500:
        return CieloAuthError("server_error", http_status=status, exit_code=11)
    return CieloAuthError("api_error", http_status=status, exit_code=11)


def refresh(config_file: Path, *, force: bool) -> dict[str, Any]:
    with auth_lock(lock_path(config_file)):
        config = load_config(config_file)
        if not force and not refresh_needed(config):
            return {
                "ok": True,
                "method": "cached",
                "status": "fresh",
                "expires_at": int(expiry_epoch(config) or 0),
            }

        old_access = required_token(config, "accessToken")
        old_refresh = required_token(config, "refreshToken")
        status, payload = refresh_request(config)
        if status != 200:
            raise classify_http_failure(status)
        if payload is None or payload.get("status") != 200:
            raise CieloAuthError("protocol_error", http_status=status, exit_code=11)
        data = payload.get("data")
        if not isinstance(data, dict):
            raise CieloAuthError("protocol_error", http_status=status, exit_code=11)
        new_access = data.get("accessToken")
        new_refresh = data.get("refreshToken")
        if not isinstance(new_access, str) or not new_access.strip():
            raise CieloAuthError("protocol_error", http_status=status, exit_code=11)
        if not isinstance(new_refresh, str) or not new_refresh.strip():
            raise CieloAuthError("protocol_error", http_status=status, exit_code=11)

        now_ms = int(time.time() * 1000)
        updated = dict(config)
        updated["accessToken"] = new_access.strip()
        updated["refreshToken"] = new_refresh.strip()
        if data.get("expiresIn") is not None:
            updated["expiresIn"] = data["expiresIn"]
        updated["lastRefresh"] = now_ms
        updated["lastApiRefresh"] = now_ms
        updated["refreshContract"] = "web-token-refresh-v1"
        atomic_write_config(config_file, updated)
        return {
            "access_rotated": old_access != updated["accessToken"],
            "method": "api-refresh-v1",
            "ok": True,
            "refresh_rotated": old_refresh != updated["refreshToken"],
            "status": "refreshed",
        }


def check(config_file: Path) -> dict[str, Any]:
    with auth_lock(lock_path(config_file)):
        config = load_config(config_file)
        expires = expiry_epoch(config)
        return {
            "access_token_present": bool(config.get("accessToken")),
            "expires_at": int(expires) if expires is not None else None,
            "fresh": not refresh_needed(config),
            "last_api_refresh": config.get("lastApiRefresh"),
            "last_refresh": config.get("lastRefresh"),
            "ok": True,
            "refresh_contract": config.get("refreshContract"),
            "refresh_token_captured_at": config.get("refreshTokenCapturedAt"),
            "refresh_token_present": bool(config.get("refreshToken")),
            "status": "configured",
        }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    refresh_parser = subparsers.add_parser("refresh")
    refresh_parser.add_argument("--force", action="store_true")
    refresh_parser.add_argument("--quiet", action="store_true")
    check_parser = subparsers.add_parser("check")
    check_parser.add_argument("--quiet", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    quiet = bool(args.quiet)
    try:
        if args.command == "refresh":
            payload = refresh(config_path(), force=bool(args.force))
        else:
            payload = check(config_path())
        emit(payload, quiet=quiet)
        return 0
    except CieloAuthError as exc:
        payload: dict[str, Any] = {
            "category": exc.category,
            "ok": False,
            "status": "authentication_required"
            if exc.exit_code in (10, 12)
            else "retryable_error",
        }
        if exc.http_status is not None:
            payload["http_status"] = exc.http_status
        emit(payload, quiet=quiet)
        return exc.exit_code


if __name__ == "__main__":
    raise SystemExit(main())
