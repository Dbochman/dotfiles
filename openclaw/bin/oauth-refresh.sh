#!/bin/bash
set -euo pipefail
exec /usr/bin/python3 - "$@" <<'PYTHON'
import fcntl
import json
import math
import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
import time


def private_directory(path):
    path.mkdir(mode=0o700, exist_ok=True)
    metadata = path.lstat()
    if not stat.S_ISDIR(metadata.st_mode) or metadata.st_uid != os.getuid() or stat.S_IMODE(metadata.st_mode) != 0o700:
        raise ValueError("unsafe automation directory")


def read_credentials(path, fresh=False):
    if not path.exists() and not path.is_symlink():
        raise ValueError("isolated credentials missing; run oauth-refresh.sh --login")
    metadata = path.lstat()
    if not stat.S_ISREG(metadata.st_mode) or metadata.st_uid != os.getuid() or stat.S_IMODE(metadata.st_mode) != 0o600 or metadata.st_size > 65536:
        raise ValueError("unsafe credential file")
    data = json.loads(path.read_text())
    oauth = data["claudeAiOauth"]
    if oauth.get("subscriptionType") != "enterprise":
        raise ValueError("enterprise login required")
    for field in ("accessToken", "refreshToken"):
        if not isinstance(oauth.get(field), str) or not oauth[field]:
            raise ValueError("incomplete credentials")
    scopes = oauth.get("scopes")
    if not isinstance(scopes, list) or not scopes or not all(isinstance(scope, str) and scope and not any(character.isspace() for character in scope) for scope in scopes):
        raise ValueError("invalid scopes")
    expiry = oauth.get("expiresAt")
    if fresh and (isinstance(expiry, bool) or not isinstance(expiry, (int, float)) or not math.isfinite(expiry) or expiry <= (time.time() + 60) * 1000):
        raise ValueError("fresh credentials were not written")
    return data


def main():
    if sys.argv[1:] not in ([], ["--login"]):
        raise ValueError("usage: oauth-refresh.sh [--login]")
    attended = sys.argv[1:] == ["--login"]
    root = Path.home() / ".openclaw"
    directory = root / "claude-automation"
    private_directory(root)
    private_directory(directory)
    credentials = directory / ".credentials.json"
    cache = root / ".anthropic-oauth-cache"
    lock_fd = os.open(str(directory / ".refresh.lock"), os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    with os.fdopen(lock_fd, "w") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise ValueError("automation login or refresh already running") from None
        before = None
        if credentials.exists() or credentials.is_symlink():
            read_credentials(credentials)
            before = credentials.read_bytes()
        environment = os.environ.copy()
        for key in ("CLAUDE_CODE_OAUTH_REFRESH_TOKEN", "CLAUDE_CODE_OAUTH_SCOPES", "CLAUDE_CODE_OAUTH_TOKEN", "ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "CLAUDE_CODE_USE_BEDROCK", "CLAUDE_CODE_USE_VERTEX", "CLAUDE_CODE_USE_FOUNDRY"):
            environment.pop(key, None)
        environment["CLAUDE_CONFIG_DIR"] = str(directory)
        environment["PATH"] = "/opt/homebrew/opt/node@22/bin:/opt/homebrew/bin:/usr/local/bin:/bin"
        if attended:
            browser_bin = directory / "browser-bin"
            private_directory(browser_bin)
            opener = browser_bin / "open"
            if not opener.is_symlink():
                opener.symlink_to("/usr/bin/open")
            if os.readlink(opener) != "/usr/bin/open":
                raise ValueError("unexpected browser opener")
            environment["PATH"] = str(browser_bin) + ":" + environment["PATH"]
        else:
            oauth = read_credentials(credentials)["claudeAiOauth"]
            environment["CLAUDE_CODE_OAUTH_REFRESH_TOKEN"] = oauth["refreshToken"]
            environment["CLAUDE_CODE_OAUTH_SCOPES"] = " ".join(oauth["scopes"])
        command = [os.environ.get("OPENCLAW_CLAUDE_BIN", "/opt/homebrew/bin/claude"), "auth", "login"]
        if attended:
            command.append("--sso")
        with tempfile.TemporaryFile(dir=directory) as output:
            result = subprocess.run(command, env=environment, stdout=output, stderr=subprocess.STDOUT, timeout=900 if attended else 120)
        if result.returncode:
            raise ValueError("Claude login failed; attended enterprise sign-in may be required")
        data = read_credentials(credentials, fresh=True)
        if before is not None and credentials.read_bytes() == before:
            raise ValueError("credential file was not refreshed")
        if cache.is_symlink() or (cache.exists() and not cache.is_file()):
            raise ValueError("unsafe cache destination")
        temporary = None
        try:
            descriptor, temporary = tempfile.mkstemp(prefix=".anthropic-oauth-", dir=root)
            with os.fdopen(descriptor, "w") as destination:
                json.dump(data, destination)
                destination.flush()
                os.fsync(destination.fileno())
            os.replace(temporary, cache)
        finally:
            if temporary and os.path.exists(temporary):
                os.unlink(temporary)
        print("OK: isolated enterprise OAuth refreshed; private cache published")


try:
    os.umask(0o077)
    main()
except (ValueError, KeyError, TypeError, OSError, subprocess.TimeoutExpired) as error:
    if isinstance(error, ValueError) and not isinstance(error, json.JSONDecodeError):
        message = str(error)
    else:
        message = type(error).__name__
    print("ERROR: " + message, file=sys.stderr)
    sys.exit(1)
PYTHON
