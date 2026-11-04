#!/usr/bin/env python3
"""Private Nest OAuth contexts. Never print provider responses or credential errors."""
from __future__ import annotations

import argparse
import contextlib
import fcntl
import hashlib
import hmac
import json
import os
from pathlib import Path
import re
import secrets
import shlex
import ssl
import stat
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request

LIMIT = 65536
STATE_TTL = 600
TOKEN_URL = "https://oauth2.googleapis.com/token"
ROOT = Path.home() / ".openclaw" / "nest-agent"


class AuthError(RuntimeError):
    pass


def directory(path: Path, create: bool = False) -> None:
    # Check ancestors too: a private leaf behind a substituted parent is unsafe.
    for parent in [*reversed(path.parents), path]:
        if parent.is_symlink():
            raise AuthError("unsafe_directory")
    if create:
        path.mkdir(mode=0o700, parents=True, exist_ok=True)
    info = path.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) != 0o700:
        raise AuthError("unsafe_directory")


def read_private(path: Path, optional: bool = False) -> bytes | None:
    directory(path.parent)
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except FileNotFoundError:
        if optional:
            return None
        raise
    with os.fdopen(fd, "rb") as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) != 0o600 or info.st_nlink != 1:
            raise AuthError("unsafe_file")
        raw = stream.read(LIMIT + 1)
    if len(raw) > LIMIT:
        raise AuthError("oversized_file")
    return raw


def read_json(path: Path, optional: bool = False) -> dict | None:
    raw = read_private(path, optional)
    if raw is None:
        return None
    value = json.loads(raw)
    if not isinstance(value, dict):
        raise AuthError("invalid_file")
    return value


def write_json(path: Path, value: dict) -> None:
    directory(path.parent, create=True)
    if path.exists() or path.is_symlink():
        read_private(path)
    fd, name = tempfile.mkstemp(prefix=".oauth-", dir=path.parent)
    try:
        with os.fdopen(fd, "w") as stream:
            os.fchmod(stream.fileno(), 0o600)
            json.dump(value, stream)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, path)
    finally:
        with contextlib.suppress(FileNotFoundError):
            os.unlink(name)


@contextlib.contextmanager
def locked(path: Path):
    directory(path, create=True)
    fd = os.open(path / ".oauth.lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600)
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) != 0o600 or info.st_nlink != 1:
            raise AuthError("unsafe_lock")
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise AuthError("oauth_busy") from None
        yield
    finally:
        os.close(fd)


def text_value(value) -> str:
    if not isinstance(value, str) or not value or len(value) > 8192 or any(c.isspace() or ord(c) < 32 for c in value):
        raise AuthError("invalid_credential")
    return value


def redirect(value: str) -> str:
    parsed = urllib.parse.urlsplit(text_value(value))
    if parsed.query or parsed.fragment or parsed.username or parsed.password:
        raise AuthError("invalid_redirect")
    if value not in ("https://www.google.com", "https://www.google.com/"):
        if parsed.scheme != "http" or parsed.hostname not in ("127.0.0.1", "localhost") or not parsed.port or parsed.path != "/oauth/callback":
            raise AuthError("invalid_redirect")
    return value


def legacy_identity() -> dict:
    # Parse only assignments; never evaluate the combined secrets file as code.
    raw = read_private(Path.home() / ".openclaw" / ".secrets-cache")
    result = {}
    for line in raw.decode().splitlines():
        match = re.fullmatch(r"(?:export\s+)?(NEST_CLIENT_ID|NEST_PROJECT_ID)=(.*)", line)
        if match:
            values = shlex.split(match[2])
            if len(values) != 1 or match[1] in result:
                raise AuthError("invalid_legacy_identity")
            result[match[1]] = text_value(values[0])
    if set(result) != {"NEST_CLIENT_ID", "NEST_PROJECT_ID"}:
        raise AuthError("missing_legacy_identity")
    return result


def validate(config: dict, agent: bool) -> dict:
    required = {"version", "client_id", "client_secret", "project_id", "redirect_uri"}
    if set(config) not in (required, required | {"refresh_token"}) or type(config.get("version")) is not int or config["version"] != 1:
        raise AuthError("invalid_context")
    for key in ("client_id", "client_secret", "project_id"):
        text_value(config[key])
    if not re.fullmatch(r"[A-Za-z0-9._-]+\.apps\.googleusercontent\.com", config["client_id"]) or not re.fullmatch(r"[-A-Za-z0-9._~]+", config["project_id"]):
        raise AuthError("invalid_identity")
    redirect(config["redirect_uri"])
    if "refresh_token" in config:
        text_value(config["refresh_token"])
    if agent:
        old = legacy_identity()
        if config["client_id"] == old["NEST_CLIENT_ID"] or config["project_id"] == old["NEST_PROJECT_ID"]:
            raise AuthError("agent_identity_not_separate")
    return config


def identity(config: dict) -> str:
    return hashlib.sha256(json.dumps([config["client_id"], config["project_id"]]).encode()).hexdigest()


def load_context(kind: str) -> tuple[dict, Path]:
    if kind == "agent":
        directory(ROOT)
        config = validate(read_json(ROOT / "credentials" / "client.json"), True)
        return config, ROOT / "tokens"
    # Conventional credentials arrive on a private pipe, never argv or stdout.
    with os.fdopen(os.dup(3), "rb") as stream:
        fields = stream.read(LIMIT + 1).split(b"\0")
    if len(fields) != 7 or fields[-1] != b"":
        raise AuthError("invalid_context_pipe")
    client, secret, project, cache, uri, refresh = [x.decode() for x in fields[:-1]]
    config = dict(version=1, client_id=client, client_secret=secret, project_id=project, redirect_uri=uri)
    if refresh:
        config["refresh_token"] = refresh
    cache_path = Path(cache)
    if not cache_path.is_absolute() or cache_path == ROOT / "tokens":
        raise AuthError("invalid_cache_path")
    return validate(config, False), cache_path


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise AuthError("oauth_redirect_rejected")


def exchange(form: dict) -> dict:
    certificate = Path("/opt/homebrew/etc/ca-certificates/cert.pem")
    context = ssl.create_default_context(cafile=str(certificate) if certificate.exists() else None)
    opener = urllib.request.build_opener(NoRedirect(), urllib.request.HTTPSHandler(context=context), urllib.request.ProxyHandler({}))
    req = urllib.request.Request(TOKEN_URL, data=urllib.parse.urlencode(form).encode(), headers={"Content-Type": "application/x-www-form-urlencoded"}, method="POST")
    try:
        with opener.open(req, timeout=20) as response:
            raw = response.read(LIMIT + 1)
        if len(raw) > LIMIT:
            raise AuthError("oauth_response_invalid")
        result = json.loads(raw)
    except (OSError, urllib.error.URLError, ValueError):
        raise AuthError("oauth_exchange_failed") from None
    if not isinstance(result, dict) or "error" in result:
        raise AuthError("oauth_exchange_failed")
    return result


def save_tokens(config: dict, cache: Path, response: dict, refresh: str | None = None) -> dict:
    access = text_value(response.get("access_token"))
    refresh = text_value(response.get("refresh_token", refresh))
    expires = response.get("expires_in")
    if type(expires) is not int or not 60 <= expires <= 86400:
        raise AuthError("oauth_response_invalid")
    record = dict(version=1, identity=identity(config), access_token=access,
                  refresh_token=refresh, expires_at=time.time() + expires - 60)
    write_json(cache / "oauth-tokens.json", record)
    return record


def begin(config: dict, cache: Path) -> str:
    state = secrets.token_urlsafe(32)
    write_json(cache / "pending-authorization.json", dict(version=1, identity=identity(config), state=state,
        redirect_uri=config["redirect_uri"], created_at=time.time()))
    query = urllib.parse.urlencode(dict(client_id=config["client_id"], redirect_uri=config["redirect_uri"],
        access_type="offline", prompt="consent", response_type="code", scope="https://www.googleapis.com/auth/sdm.service", state=state))
    return "https://nestservices.google.com/partnerconnections/" + config["project_id"] + "/auth?" + query


def finish(config: dict, cache: Path, callback: str) -> None:
    pending_path = cache / "pending-authorization.json"
    pending = read_json(pending_path)
    age = time.time() - pending.get("created_at", 0)
    if pending.get("identity") != identity(config) or pending.get("redirect_uri") != config["redirect_uri"] or not 0 <= age <= STATE_TTL:
        raise AuthError("authorization_expired_or_changed")
    parsed = urllib.parse.urlsplit(callback.strip())
    expected = urllib.parse.urlsplit(config["redirect_uri"])
    if parsed.fragment or parsed.username or parsed.password or (parsed.scheme, parsed.netloc, parsed.path or "/") != (expected.scheme, expected.netloc, expected.path or "/"):
        raise AuthError("authorization_redirect_mismatch")
    query = urllib.parse.parse_qs(parsed.query, keep_blank_values=True)
    if "error" in query or len(query.get("code", [])) != 1 or len(query.get("state", [])) != 1:
        raise AuthError("authorization_response_invalid")
    state = text_value(query["state"][0])
    if not hmac.compare_digest(state, text_value(pending.get("state"))):
        raise AuthError("authorization_state_mismatch")
    code = text_value(query["code"][0])
    # Consume before the network request. Ambiguous errors require a new flow.
    pending_path.unlink()
    result = exchange(dict(client_id=config["client_id"], client_secret=config["client_secret"],
        code=code, redirect_uri=config["redirect_uri"], grant_type="authorization_code"))
    save_tokens(config, cache, result)


def token(config: dict, cache: Path) -> str:
    record = read_json(cache / "oauth-tokens.json", optional=True)
    if record is not None:
        if record.get("version") != 1 or record.get("identity") != identity(config):
            raise AuthError("token_identity_mismatch")
        refresh = text_value(record.get("refresh_token"))
        expiry = record.get("expires_at")
        if not isinstance(expiry, (int, float)) or not 0 <= expiry <= time.time() + 86400:
            raise AuthError("invalid_token_cache")
        if expiry > time.time():
            return text_value(record.get("access_token"))
    else:
        refresh = text_value(config.get("refresh_token"))
    result = exchange(dict(client_id=config["client_id"], client_secret=config["client_secret"],
        refresh_token=refresh, grant_type="refresh_token"))
    return save_tokens(config, cache, result, refresh)["access_token"]


def configure_agent() -> None:
    if not sys.stdin.isatty() or not sys.stdout.isatty():
        raise AuthError("configuration_requires_terminal")
    directory(ROOT)
    refs = read_json(ROOT / "credentials" / "references.json")
    required = {"client_id", "client_secret", "project_id", "redirect_uri"}
    if set(refs) not in (required, required | {"refresh_token"}):
        raise AuthError("invalid_references")
    uri = redirect(refs["redirect_uri"])
    for key in refs.keys() - {"redirect_uri"}:
        reference = refs[key]
        if (not isinstance(reference, str) or len(reference) > 2048
                or not re.fullmatch(r"op://[^/]+/[^/]+/(?:[^/]+/)?[^/]+", reference)
                or any(ord(c) < 32 or ord(c) == 127 for c in reference)
                or any(not part.strip() for part in reference[5:].split("/"))):
            raise AuthError("invalid_reference")
    if input("Type CONFIGURE to read the exact Nest agent fields: ") != "CONFIGURE":
        raise AuthError("configuration_cancelled")
    config = dict(version=1, redirect_uri=uri)
    for key in refs.keys() - {"redirect_uri"}:
        result = subprocess.run(["op", "read", refs[key]], capture_output=True, text=True, timeout=60)
        if result.returncode:
            raise AuthError("credential_read_failed")
        config[key] = text_value(result.stdout.strip())
    validate(config, True)
    with locked(ROOT / "tokens"):
        old = read_json(ROOT / "tokens" / "oauth-tokens.json", optional=True)
        if old is not None and old.get("identity") != identity(config):
            raise AuthError("existing_tokens_have_different_identity")
        write_json(ROOT / "credentials" / "client.json", config)
    print("Nest agent credentials saved separately; routing unchanged.")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["validate", "project", "token", "begin", "finish", "configure"])
    parser.add_argument("--context", choices=["conventional", "agent"], required=True)
    args = parser.parse_args()
    try:
        if args.action == "configure":
            if args.context != "agent":
                raise AuthError("invalid_context")
            configure_agent()
            return 0
        config, cache = load_context(args.context)
        if args.action == "validate":
            return 0
        if args.action == "project":
            print(config["project_id"])
            return 0
        with locked(cache):
            if args.action == "begin":
                print(begin(config, cache))
                print("Complete consent, then pass the full callback URL to reauth --stdin within 10 minutes.")
            elif args.action == "finish":
                callback = sys.stdin.read(16385)
                if len(callback) > 16384:
                    raise AuthError("authorization_response_invalid")
                finish(config, cache, callback)
                print("Nest authorization saved. Verify devices with the selected context's raw command.")
            else:
                print(token(config, cache))
        return 0
    except AuthError as error:
        print("Nest OAuth: " + str(error), file=sys.stderr)
    except (OSError, ValueError, TypeError, KeyError, subprocess.SubprocessError):
        print("Nest OAuth: configuration_or_storage_unavailable", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
