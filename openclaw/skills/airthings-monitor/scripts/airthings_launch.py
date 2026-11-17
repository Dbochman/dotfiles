#!/usr/bin/env python3
"""Bound Launch Services reads without changing the reader's Bluetooth identity.

The request journal survives a killed supervisor. Only same-user processes with
an exact, request-specific command are eligible for cleanup; no name-based kill.
"""
from __future__ import annotations

import asyncio
import contextlib
import fcntl
import json
import os
from pathlib import Path
import re
import signal
import stat
import subprocess
import sysconfig
import time
import uuid

from airthings import (
    AirthingsError, AirthingsMonitor, SAFE_READ_ERRORS, _atomic_json,
    _print_human, _private_dir, _private_file, _utc_now, build_parser, offline_reading,
)

READ_TIMEOUT = 35
CLEANUP_GRACE = 1.5


class ReaderSupervisor:
    def __init__(self, monitor: AirthingsMonitor, *, timeout: float = READ_TIMEOUT):
        self.monitor = monitor
        self.timeout = timeout
        root = Path(os.environ.get("OPENCLAW_HOME", Path.home() / ".openclaw"))
        self.script = Path(os.environ.get("AIRTHINGS_SCRIPT", Path(__file__).with_name("airthings.py")))
        self.app = Path(os.environ.get("AIRTHINGS_PYTHON_APP", root / "apps/Airthings Reader.app"))
        self.open_bin = os.environ.get("AIRTHINGS_OPEN_BIN", "/usr/bin/open")
        self.active = monitor.state_dir / "active-reader.json"
        self.health = monitor.state_dir / "reader-health.json"

    def result_path(self, request):
        # Launch Services can deliver an old request late. Its output must
        # never replace or corrupt the result of a subsequent request.
        return self.monitor.state_dir / f"reader-result-{request['token']}.json"

    def remove_result(self, request):
        result = self.result_path(request)
        if result.exists() or result.is_symlink():
            _private_file(result)
            result.unlink()

    def reader_args(self, request):
        return [str(self.script), "status", *([request["alias"]] if request["alias"] else []),
                "--refresh", "--json", "--reader-request", request["token"],
                "--reader-deadline", str(request["deadline"])]

    def reader_command(self, request):
        return " ".join([str(self.app / "Contents/MacOS/Airthings Reader"), *self.reader_args(request)])

    def launch_args(self, request):
        return [self.open_bin, "-g", "-W", "-n", "--stdout", str(self.result_path(request)),
                "--stderr", "/dev/null", "--env",
                "PYTHONPATH=" + sysconfig.get_paths()["purelib"],
                str(self.app), "--args", *self.reader_args(request)]

    def matching_processes(self, request):
        commands = {self.reader_command(request), " ".join(self.launch_args(request))}
        try:
            output = subprocess.run(
                ["/bin/ps", "-ww", "-axo", "pid=,uid=,command="],
                capture_output=True, text=True, timeout=2, check=True,
            ).stdout
        except (OSError, subprocess.SubprocessError):
            raise AirthingsError("reader_cleanup_failed", 12) from None
        matches = []
        for line in output.splitlines():
            fields = line.strip().split(None, 2)
            if (len(fields) == 3 and fields[0].isdigit() and fields[1].isdigit()
                    and int(fields[1]) == os.getuid() and fields[2] in commands):
                matches.append(int(fields[0]))
        return matches

    def signal_owned(self, request, pid, sig):
        # Recheck command, UID and unique request immediately before every
        # signal. A recycled PID alone never grants cleanup authority.
        if pid in self.matching_processes(request):
            with contextlib.suppress(ProcessLookupError):
                os.kill(pid, sig)

    def cleanup(self, request):
        for pid in self.matching_processes(request):
            self.signal_owned(request, pid, signal.SIGTERM)
            self.signal_owned(request, pid, signal.SIGCONT)
        deadline = time.monotonic() + CLEANUP_GRACE
        while self.matching_processes(request) and time.monotonic() < deadline:
            time.sleep(0.1)
        for pid in self.matching_processes(request):
            self.signal_owned(request, pid, signal.SIGKILL)
        deadline = time.monotonic() + CLEANUP_GRACE
        while self.matching_processes(request) and time.monotonic() < deadline:
            time.sleep(0.1)
        if self.matching_processes(request):
            raise AirthingsError("reader_cleanup_failed", 12)

    def previous_request(self):
        if not self.active.exists() and not self.active.is_symlink():
            return None
        _private_file(self.active)
        try:
            request = json.loads(self.active.read_text())
            valid = (isinstance(request, dict) and set(request) == {"token", "alias", "deadline"}
                     and isinstance(request["token"], str)
                     and re.fullmatch(r"[0-9a-f]{32}", request["token"])
                     and type(request["deadline"]) is int
                     and (request["alias"] is None or
                          (isinstance(request["alias"], str) and
                           re.fullmatch(r"[A-Za-z0-9_-]{1,80}", request["alias"]))))
        except (OSError, ValueError):
            valid = False
        if not valid:
            raise AirthingsError("reader_cleanup_failed", 12)
        return request

    def record_health(self, error, recovered):
        _atomic_json(self.health, {"ok": error is None, "error": error,
                                  "checked_at": _utc_now(), "recovered_previous_request": recovered})

    def fresh_payload(self, request, bindings):
        result = self.result_path(request)
        _private_file(result)
        if result.stat().st_size > 65536:
            raise AirthingsError("reader_failed", 12)
        try:
            payload = json.loads(result.read_text())
            devices = payload["devices"]
            if payload.get("ok") is not True or not isinstance(devices, list):
                raise ValueError()
            if len(devices) != len(bindings):
                raise ValueError()
            readings = []
            for binding, device in zip(bindings, devices):
                if not isinstance(device, dict) or device.get("alias") != binding["alias"]:
                    raise ValueError()
                if device.get("online") is True:
                    # Never substitute an earlier good sample after a failed
                    # refresh: the result must match this completed read.
                    cached = self.monitor._fresh_cached(binding["alias"])
                    if cached is None or cached["read_at"] != device.get("read_at"):
                        raise ValueError()
                    readings.append(cached)
                else:
                    error = device.get("error")
                    readings.append(offline_reading(binding, error if isinstance(error, str) and error in SAFE_READ_ERRORS else "reader_failed"))
            return {"ok": True, "devices": readings}
        except (ValueError, KeyError, TypeError):
            raise AirthingsError("reader_failed", 12) from None

    def refresh(self, alias, bindings, recovered):
        request = {"token": uuid.uuid4().hex, "alias": alias,
                   "deadline": int(time.time() + self.timeout)}
        _atomic_json(self.active, request)
        result = self.result_path(request)
        descriptor = os.open(result, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        os.close(descriptor)
        child = None
        error = None
        try:
            child = subprocess.Popen(self.launch_args(request), stdout=subprocess.DEVNULL,
                                     stderr=subprocess.DEVNULL, start_new_session=True)
            try:
                child.wait(timeout=self.timeout)
            except subprocess.TimeoutExpired:
                raise AirthingsError("reader_timeout", 12) from None
            if child.returncode != 0:
                raise AirthingsError("reader_launch_failed", 12)
            if self.matching_processes(request):
                raise AirthingsError("reader_failed", 12)
            payload = self.fresh_payload(request, bindings)
            error = next((r.get("error") for r in payload["devices"] if not r.get("online")), None)
            return payload
        except OSError:
            error = "reader_launch_failed"
            raise AirthingsError(error, 12) from None
        except AirthingsError as exc:
            error = exc.category
            raise
        finally:
            try:
                self.cleanup(request)
                self.remove_result(request)
                # Recheck timed-out/failed launches on the next run, including
                # delayed Launch Services delivery after the supervisor exited.
                if error is None or error in SAFE_READ_ERRORS - {"reader_timeout", "reader_failed", "reader_launch_failed"}:
                    self.active.unlink(missing_ok=True)
            except AirthingsError:
                error = "reader_cleanup_failed"
                raise
            finally:
                if child is not None:
                    if child.poll() is None:
                        child.kill()  # our own unreaped launcher
                    child.wait(timeout=2)
                self.record_health(error, recovered)

    def status(self, alias, refresh=False, cache_only=False):
        bindings = self.monitor.load_config()
        if alias is not None:
            bindings = [b for b in bindings if b["alias"] == alias]
            if not bindings:
                raise AirthingsError("unknown_alias", 2)
        if cache_only:
            return asyncio.run(self.monitor.status(alias, False, True))
        _private_dir(self.monitor.state_dir)
        lock = self.monitor.state_dir / "reader.lock"
        fd = os.open(lock, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        try:
            metadata = os.fstat(fd)
            if not stat.S_ISREG(metadata.st_mode) or metadata.st_uid != os.getuid():
                raise AirthingsError("unsafe_state_directory", 12)
            os.fchmod(fd, 0o600)
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                return {"ok": True, "devices": [offline_reading(b, "reader_busy") for b in bindings]}
            previous = None
            try:
                previous = self.previous_request()
                if previous:
                    self.cleanup(previous)
                    self.remove_result(previous)
                    self.active.unlink()
                cached = asyncio.run(self.monitor.status(alias, False, True))
                if not refresh and all(r.get("online") for r in cached["devices"]):
                    if previous:
                        self.record_health(None, True)
                    return cached
                return self.refresh(alias, bindings, bool(previous))
            except AirthingsError as exc:
                if exc.category not in SAFE_READ_ERRORS:
                    raise
                self.record_health(exc.category, bool(previous))
                return {"ok": True, "devices": [offline_reading(b, exc.category) for b in bindings]}
        finally:
            os.close(fd)


def main():
    args = build_parser().parse_args()
    if args.command != "status":
        raise SystemExit(2)
    try:
        payload = ReaderSupervisor(AirthingsMonitor()).status(args.alias, args.refresh, args.cache_only)
        if args.json:
            print(json.dumps(payload, separators=(",", ":"), sort_keys=True))
        else:
            _print_human(payload)
        return 0
    except AirthingsError as exc:
        print(json.dumps({"ok": False, "error": exc.category}))
        return exc.exit_code
    except (OSError, subprocess.SubprocessError):
        print('{"ok":false,"error":"reader_failed"}')
        return 12


if __name__ == "__main__":
    raise SystemExit(main())
