#!/usr/bin/env python3
"""Bounded release watcher for website PR 339; never runs or enables CI."""

import argparse
import base64
import fcntl
import json
import os
from pathlib import Path
import re
import ssl
import subprocess
import tempfile
import time
import urllib.request

REPO = "Dbochman/personal-website"
PR = 339
STATE = Path.home() / ".local/state/github-runner-readiness"
CA = "/opt/homebrew/etc/openssl@3/cert.pem"
GH = "/opt/homebrew/bin/gh"
WINDOW = 7 * 86400
INTERVAL = 3600
TERMINAL = {"candidate", "expired", "closed"}


def api(path):
    # Public metadata only; no token, cookies, decompression or remote execution.
    request = urllib.request.Request(
        "https://api.github.com/" + path,
        headers={"Accept": "application/vnd.github+json", "User-Agent": "runner-readiness"},
    )
    with urllib.request.urlopen(request, context=ssl.create_default_context(cafile=CA), timeout=20) as response:
        payload = response.read(2_000_001)
    if len(payload) > 2_000_000:
        raise ValueError("oversized response")
    return json.loads(payload)


def inspect_release(fetch=api):
    release = fetch("repos/actions/runner/releases/latest")
    tag = release["tag_name"]
    if release.get("draft") or release.get("prerelease") or not re.fullmatch(r"v\d+\.\d+\.\d+", tag):
        raise ValueError("not a stable release")
    sha = fetch(f"repos/actions/runner/commits/{tag}")["sha"]
    if not re.fullmatch(r"[0-9a-f]{40}", sha):
        raise ValueError("invalid source commit")
    file = fetch(f"repos/actions/runner/contents/src/Misc/externals.sh?ref={sha}")
    if file.get("encoding") != "base64":
        raise ValueError("unknown encoding")
    source = base64.b64decode(file["content"], validate=False).decode("utf-8")
    versions = re.findall(r'^NODE24_VERSION="(\d+\.\d+\.\d+)"\s*$', source, re.M)
    if len(versions) != 1:
        raise ValueError("ambiguous or missing Node version")
    version = tuple(map(int, versions[0].split(".")))
    if version[0] != 24:
        raise ValueError("unexpected runtime major")
    return {"release": tag, "source_commit": sha, "node": versions[0],
            "patched_source": version >= (24, 22, 1)}


def gh(args, body=None):
    env = {"HOME": str(Path.home()), "PATH": "/opt/homebrew/bin:/usr/bin:/bin",
           "GH_PROMPT_DISABLED": "1", "SSL_CERT_FILE": CA}
    return subprocess.run([GH, *args], input=body, capture_output=True, text=True,
                          timeout=30, check=True, env=env).stdout


def notify(event, evidence):
    marker = f"<!-- runner-readiness-339-{event} -->"
    # Reconcile ambiguous POST outcomes before retrying. Never claim delivery on timeout.
    pages = json.loads(gh(["api", "--paginate", "--slurp",
                          f"repos/{REPO}/issues/{PR}/comments?per_page=100"]))
    if any(marker in item.get("body", "") and item.get("user", {}).get("login") == "Dbochman"
           for page in pages for item in page):
        return
    messages = {
        "started": "The Mini's hourly runner readiness watch is active for up to seven days. It only reports release candidates; it cannot merge or enable workflows. The CI hold remains in place.",
        "candidate": (f"Runner {evidence.get('release')} declares patched Node {evidence.get('node')} "
                      "at its release source commit. This is a candidate for attended validation, "
                      "not proof of the installed runner or CI success. Keep the workflow hold; "
                      "follow .github/security/README.md to verify the real action runtime, "
                      "run checks/review, merge, then restore guarded workflows."),
        "expired": "The seven-day runner watch expired. No clearance is granted. Keep the hold and review manually.",
        "errors": "The runner watch has failed three consecutive polls. Status is unknown, not ready; inspect its protected status file.",
    }
    gh(["pr", "comment", str(PR), "--repo", REPO, "--body-file", "-"], marker + "\n" + messages[event])


def tick(state, now, fetch=api, send=notify):
    state = dict(state)
    state.setdefault("started_at", now)
    state.setdefault("deadline", state["started_at"] + WINDOW)
    state.setdefault("notifications", [])
    # Once terminal, only retry an outstanding notification for at most one day.
    if state.get("status") not in TERMINAL:
        if now >= state["deadline"]:
            state.update(status="expired", terminal_at=now)
        elif now - state.get("last_attempt", 0) >= INTERVAL:
            state["last_attempt"] = now
            try:
                pr = fetch(f"repos/{REPO}/pulls/{PR}")
                if pr["state"] == "closed":
                    state.update(status="closed", terminal_at=now)
                elif pr["state"] != "open":
                    raise ValueError("unknown PR state")
                else:
                    evidence = inspect_release(fetch)
                    state.update(evidence=evidence, failures=0, last_success=now)
                    state.pop("error", None)
                    state["status"] = "candidate" if evidence["patched_source"] else "waiting"
                    if state["status"] == "candidate":
                        state["terminal_at"] = now
            except Exception as error:
                state.update(status="unknown", failures=state.get("failures", 0) + 1,
                             error=type(error).__name__)
    event = state.get("status") if state.get("status") in {"candidate", "expired"} else None
    if state.get("status") == "waiting":
        event = "started"
    if state.get("status") == "unknown" and state.get("failures", 0) >= 3:
        event = "errors"
    if event and event not in state["notifications"]:
        last = state.get("notification_attempt", 0)
        end = state.get("terminal_at", state["deadline"]) + 86400
        if now >= end:
            state["notification_error"] = "retry_window_expired"
        elif now - last >= INTERVAL:
            state["notification_attempt"] = now
            try:
                send(event, state.get("evidence", {}))
                state["notifications"] = [*state["notifications"], event]
                state.pop("notification_error", None)
            except Exception as error:
                state["notification_error"] = type(error).__name__
    return state


def save(path, state):
    descriptor, temporary = tempfile.mkstemp(dir=path.parent, prefix=".status-")
    try:
        with os.fdopen(descriptor, "w") as stream:
            json.dump(state, stream, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--status", action="store_true", help="Read state without network or notifications")
    args = parser.parse_args()
    os.umask(0o077)
    STATE.mkdir(mode=0o700, parents=True, exist_ok=True)
    if STATE.is_symlink() or STATE.stat().st_uid != os.getuid() or STATE.stat().st_mode & 0o077:
        raise ValueError("state directory must be private and owned")
    path = STATE / "status.json"
    with (STATE / "lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return
        # Invalid state is an error, not permission to reset the expiry clock.
        state = json.loads(path.read_text()) if path.exists() else {}
        if not args.status:
            state = tick(state, time.time())
            save(path, state)
        print(json.dumps(state, sort_keys=True))


if __name__ == "__main__":
    main()
