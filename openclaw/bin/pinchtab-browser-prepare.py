#!/usr/bin/env python3
"""New-profile preparation or bounded trust verification of an exact instance."""
import argparse
import importlib.util
import json
import os
from pathlib import Path
import re
import signal
import subprocess
import sys
import time
import urllib.request

HERE = Path(__file__).resolve().parent
HOSTS = ["github.com", "pypi.org"] + [
    f"{kind}-{variant}-{chain}.we-have-a-problem.com"
    for chain in ("emudhra", "aaa-emudhra")
    for kind in ("dv", "ov", "ev") for variant in ("g3b", "g3c")
]


class PreparationError(Exception):
    pass


def gate_state(result):
    """Only an exact gate pass succeeds; only known trust-not-ready retries."""
    if result.returncode not in (0, 1):
        if "Browser trust check deadline exceeded" in (result.stderr or ""):
            raise PreparationError("trust_probe_deadline_exceeded")
        raise PreparationError("trust_probe_error")
    try:
        report = json.loads(result.stdout.strip().splitlines()[-1])
        rows = report["results"]
        if len(rows) != len(HOSTS) or {r["host"] for r in rows} != set(HOSTS):
            raise ValueError()
        expected = {h: "control_passed" if h in HOSTS[:2] else "rejected" for h in HOSTS}
        passed = all(r["result"] == expected[r["host"]] for r in rows)
        if result.returncode == 0 and passed:
            return True
        if result.returncode == 1 and not passed and all(
            r["result"] == "control_passed" if r["host"] in HOSTS[:2]
            else r["result"] in ("accepted", "rejected") for r in rows
        ):
            return False
    except (ValueError, KeyError, TypeError, IndexError):
        pass
    raise PreparationError("trust_probe_inconclusive")


class Backend:
    def __init__(self):
        self.deadline = None
        self.cli = os.environ.get("PINCHTAB_BIN", "/opt/homebrew/bin/pinchtab")
        self.node = os.environ.get("PINCHTAB_TRUST_NODE", str(
            Path.home() / ".local/share/openclaw/runtimes/node-v24.22.1-darwin-arm64/bin/node"))
        self.config_root = Path.home() / ".pinchtab"
        self.config = json.loads(Path(os.environ.get(
            "PINCHTAB_CONFIG", str(self.config_root / "config.json"))).read_text())

    def budget(self, maximum):
        remaining = self.deadline - time.monotonic() if self.deadline else maximum
        if remaining <= 0:
            raise PreparationError("preparation_deadline_exceeded")
        return min(maximum, remaining)

    def run(self, args, timeout=10):
        result = subprocess.run(args, capture_output=True, text=True, timeout=self.budget(timeout))
        if result.returncode or re.search(r"(?im)^\s*error(?:\s+[45]\d\d)?[: ]", result.stdout + result.stderr):
            raise PreparationError("pinchtab_command_failed")
        return result.stdout

    def cli_json(self, *args):
        return json.loads(self.run([self.cli, *args]))

    def create(self, name):
        self.run([self.cli, "health"])
        profiles = self.cli_json("profiles", "--json")
        if any(p.get("name") == name for p in profiles):
            raise PreparationError("profile_already_exists")
        # The CLI has no create-profile command. POST must create, never reuse a
        # conflict. Do not forward operator credentials through redirects/proxies.
        server = self.config.get("server", {})
        port = str(server.get("port") or "9867")
        if not port.isdecimal() or not 1 <= int(port) <= 65535:
            raise PreparationError("invalid_server_port")
        token = os.environ.get("PINCHTAB_TOKEN") or server.get("token", "")
        headers = {"Content-Type": "application/json"}
        if token:
            headers["Authorization"] = f"Bearer {token}"

        class NoRedirect(urllib.request.HTTPRedirectHandler):
            def redirect_request(self, *args, **kwargs):
                return None

        request = urllib.request.Request(f"http://127.0.0.1:{port}/profiles",
                                         data=json.dumps({"name": name}).encode(), headers=headers)
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
        with opener.open(request, timeout=self.budget(10)) as response:
            if response.status not in (200, 201):
                raise PreparationError("profile_creation_failed")
        profiles = self.cli_json("profiles", "--json")
        matches = [p for p in profiles if p.get("name") == name]
        if len(matches) != 1 or not re.fullmatch(r"prof_[a-zA-Z0-9_-]+", matches[0].get("id", "")):
            raise PreparationError("profile_identity_unavailable")
        return matches[0]["id"]

    def instances(self, profile_id):
        return [r for r in self.cli_json("instances", "--json")
                if r.get("profileId") == profile_id and r.get("status") != "stopped"]

    def start(self, name):
        args = [self.cli, "instance", "start", "--profile", name, "--mode", "headless"]
        for host in HOSTS:
            args += ["--allow-domain", host]
        self.run(args, timeout=15)

    def probe(self, instance, timeout):
        spec = importlib.util.spec_from_file_location("trust_port", HERE / "pinchtab-browser-trust-port.py")
        resolver = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(resolver)
        port = instance.get("port")
        if not str(port).isdecimal() or not 1 <= int(port) <= 65535:
            raise PreparationError("invalid_bridge_port")
        self.run([self.cli, "--server", f"http://127.0.0.1:{port}", "health"], timeout=5)
        cdp_port = resolver.resolve(instance, self.config, self.config_root)
        result = subprocess.run([self.node, str(HERE / "pinchtab-browser-trust.cjs"), str(cdp_port)],
                                capture_output=True, text=True, timeout=self.budget(timeout))
        return gate_state(result)

    def stop_owned(self, profile_id):
        self.deadline = time.monotonic() + 30
        # This profile was atomically created here; never touch another profile.
        rows = self.instances(profile_id)
        if len(rows) > 1:
            raise PreparationError("cleanup_identity_ambiguous")
        for row in rows:
            instance_id = row.get("id", "")
            if not re.fullmatch(r"inst_[a-zA-Z0-9_-]+", instance_id):
                raise PreparationError("cleanup_identity_invalid")
            self.run([self.cli, "instance", "stop", instance_id])
        if self.instances(profile_id):
            raise PreparationError("cleanup_unverified")


def prepare(name, backend, timeout=600, clock=time.monotonic, sleep=time.sleep):
    if not re.fullmatch(r"[a-z][a-z0-9-]{0,63}", name):
        raise PreparationError("invalid_new_profile_name")
    if not 60 <= timeout <= 600:
        raise PreparationError("invalid_deadline")
    started = clock()
    deadline = started + timeout
    backend.deadline = deadline
    profile_id = backend.create(name)
    passed = False
    start_attempted = False
    try:
        if backend.instances(profile_id):
            raise PreparationError("new_profile_already_running")
        start_attempted = True
        backend.start(name)
        attempt = 0
        deadline_retries = 0
        while clock() < deadline - 30:
            rows = backend.instances(profile_id)
            if len(rows) != 1 or rows[0].get("mode") != "headless" or rows[0].get("attached"):
                raise PreparationError("unexpected_preparation_instance")
            if rows[0].get("status") == "starting":
                sleep(1)
                continue
            if rows[0].get("status") != "running":
                raise PreparationError("preparation_instance_unavailable")
            attempt += 1
            retry_reason = None
            try:
                passed = backend.probe(rows[0], timeout=22)
            except PreparationError as exc:
                if str(exc) != "trust_probe_deadline_exceeded" or deadline_retries >= 1:
                    raise
                deadline_retries += 1
                passed = False
                retry_reason = "probe_deadline"
            progress = {"phase": "prepare", "attempt": attempt, "trust_verified": passed,
                        "elapsed_seconds": round(clock() - started)}
            if retry_reason:
                progress["retry_reason"] = retry_reason
            print(json.dumps(progress), file=sys.stderr, flush=True)
            if passed:
                break
            sleep(min(15, max(0, deadline - clock() - 30)))
        if not passed:
            raise PreparationError("preparation_deadline_exceeded")
    finally:
        if start_attempted:
            backend.stop_owned(profile_id)
    return {"status": "prepared", "profile": name, "instance_stopped": True,
            "requires_acquisition_check": True}


def verify_existing(instance_id, backend, timeout=120, clock=time.monotonic, sleep=time.sleep):
    """Wait without creating, navigating account tabs, or stopping any instance.

    Lifecycle ownership remains with acquire. No clearance is cached; only a
    complete fresh probe passes, and unknown/inconclusive results fail closed.
    """
    if not re.fullmatch(r"inst_[a-zA-Z0-9_-]+", instance_id) or not 60 <= timeout <= 120:
        raise PreparationError("invalid_verification_options")
    deadline = clock() + timeout
    backend.deadline = deadline
    profile_id = None
    deadline_retries = 0
    while clock() < deadline - 25:
        rows = [r for r in backend.cli_json("instances", "--json") if r.get("id") == instance_id]
        if len(rows) != 1:
            raise PreparationError("verification_instance_unavailable")
        row = rows[0]
        if (row.get("mode") != "headless" or row.get("status") != "running" or row.get("attached")
                or not re.fullmatch(r"prof_[a-zA-Z0-9_-]+", row.get("profileId", ""))):
            raise PreparationError("unexpected_verification_instance")
        if profile_id is not None and row["profileId"] != profile_id:
            raise PreparationError("verification_identity_changed")
        profile_id = row["profileId"]
        try:
            if backend.probe(row, timeout=22):
                return
        except PreparationError as exc:
            if str(exc) != "trust_probe_deadline_exceeded" or deadline_retries >= 1:
                raise
            deadline_retries += 1
        sleep(min(15, max(0, deadline - clock() - 25)))
    raise PreparationError("verification_deadline_exceeded")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("profile", nargs="?", help="New, unused profile name; never an account-bearing profile")
    parser.add_argument("--verify-instance", help="Verify only an exact running headless instance; caller owns cleanup")
    parser.add_argument("--timeout", type=int, default=600, help="Preparation budget, 60–600 seconds; cleanup up to 30 extra")
    args = parser.parse_args()
    if bool(args.profile) == bool(args.verify_instance):
        parser.error("choose a new profile or --verify-instance")
    # Never silently exchange a restricted agent session for operator authority.
    if os.environ.get("PINCHTAB_SESSION"):
        print("Preparation requires explicit operator authorization; agent session unchanged", file=sys.stderr)
        return 1
    def interrupted(signum, frame):
        raise PreparationError("preparation_interrupted")
    signal.signal(signal.SIGTERM, interrupted)
    signal.signal(signal.SIGINT, interrupted)
    try:
        if args.verify_instance:
            verify_existing(args.verify_instance, Backend(), args.timeout)
            result = {"status": "trust_verified", "requires_acquisition_check": True}
        else:
            result = prepare(args.profile, Backend(), args.timeout)
    except Exception as exc:
        code = str(exc) if isinstance(exc, PreparationError) else "preparation_failed"
        operation = 'verification' if args.verify_instance else 'preparation'
        print(f"PinchTab {operation} blocked: {code}; retained profile is not cleared", file=sys.stderr)
        return 1
    print(json.dumps(result))
    return 0


if __name__ == "__main__":
    sys.exit(main())
