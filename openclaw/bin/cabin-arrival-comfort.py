#!/usr/bin/env python3
"""Restore Cabin heat once per verified arrival; never replay vacancy actions."""

import argparse
import fcntl
import hashlib
import json
import os
from pathlib import Path
import stat
import subprocess
import tempfile
import time
from datetime import datetime, timezone


ROOMS = (("Philly Living Room", "70"), ("Philly Bedroom", "68"))


class SafetyError(Exception):
    pass


def read_private(path):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(fd) as handle:
        info = os.fstat(handle.fileno())
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid()
                or stat.S_IMODE(info.st_mode) != 0o600 or info.st_size > 262144):
            raise SafetyError("unsafe_state")
        return json.load(handle)


def save(path, value):
    fd, temporary = tempfile.mkstemp(prefix=".comfort-", dir=path.parent)
    try:
        with os.fdopen(fd, "w") as handle:
            json.dump(value, handle, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def timestamp(value):
    result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if result.tzinfo is None:
        raise SafetyError("invalid_timestamp")
    return result


class Controller:
    def __init__(self, home, runner=subprocess.run, clock=None, sleeper=time.sleep):
        self.presence = home / ".openclaw/presence"
        self.root = self.presence / "cabin-arrival-comfort"
        self.state = self.root / "state.json"
        self.runner = runner
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self.sleep = sleeper
        self.nest = str(home / ".openclaw/bin/nest-agent")

    def evidence(self):
        state = read_private(self.presence / "state.json")
        producer = read_private(self.presence / "home-events-outbox/producer-state.json")
        digest = hashlib.sha256(json.dumps(
            state, ensure_ascii=False, separators=(",", ":"), sort_keys=True,
            allow_nan=False).encode()).hexdigest()
        if (producer.get("schema_version") != 1
                or producer.get("state_hash") != digest
                or producer.get("evaluated_at") != state.get("timestamp")):
            raise SafetyError("presence_state_mismatch")
        age = (self.clock() - timestamp(state["timestamp"])).total_seconds()
        if not 0 <= age <= 1800 or state["cabin"].get("fresh") is not True:
            raise SafetyError("presence_stale")
        cabin = state["cabin"]
        changed = cabin["stateChangedAt"]
        if timestamp(changed) > timestamp(state["timestamp"]):
            raise SafetyError("invalid_transition")
        residents = [state["people"][name] for name in ("Dylan", "Julia")]
        # Direct evidence at both houses is ambiguous even if sticky location
        # still says Cabin. Unknown or stale evidence cannot trigger heat.
        if any(p.get("cabin") is True and p.get("crosstown") is True for p in residents):
            return "uncertain", changed
        if cabin.get("occupancy") == "occupied" and any(
                p.get("location") == "cabin" and p.get("cabin") is True
                and p.get("crosstown") is False for p in residents):
            return "occupied", changed
        if (cabin.get("occupancy") == "confirmed_vacant"
                and state["crosstown"].get("fresh") is True
                and all(p.get("location") == "crosstown" for p in residents)):
            return "vacant", changed
        return "uncertain", changed

    def command(self, args):
        try:
            result = self.runner([self.nest, *args], stdout=subprocess.DEVNULL,
                                 stderr=subprocess.DEVNULL, timeout=90, check=False)
            return result.returncode == 0
        except (OSError, subprocess.TimeoutExpired):
            return False

    def evaluate(self, restore_current=False):
        self.root.mkdir(mode=0o700, parents=True, exist_ok=True)
        info = self.root.lstat()
        if (not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid()
                or stat.S_IMODE(info.st_mode) != 0o700):
            raise SafetyError("unsafe_directory")
        fd = os.open(self.root / "lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, "w") as lock:
            info = os.fstat(lock.fileno())
            if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid()
                    or stat.S_IMODE(info.st_mode) != 0o600):
                raise SafetyError("unsafe_lock")
            fcntl.flock(lock, fcntl.LOCK_EX)
            return self._evaluate(restore_current)

    def _evaluate(self, restore_current):
        occupancy, arrival = self.evidence()
        record = read_private(self.state) if self.state.exists() else None
        if record is not None and (record.get("schema_version") != 1
                                   or type(record.get("armed")) is not bool):
            raise SafetyError("invalid_comfort_state")
        if occupancy == "vacant":
            if record is None or not record["armed"]:
                save(self.state, {"schema_version": 1, "armed": True, "status": "armed"})
            return {"ok": True, "status": "armed"}
        if occupancy != "occupied":
            return {"ok": True, "status": "waiting_for_unambiguous_presence"}
        if record is None and not restore_current:
            save(self.state, {"schema_version": 1, "armed": False, "status": "baselined"})
            return {"ok": True, "status": "baselined"}
        if record and not record["armed"] and not restore_current:
            # An interrupted mutation is terminal; preserve evidence and never
            # turn a process crash into a repeated physical command.
            if record["status"] == "applying":
                record["status"] = "outcome_unknown"
                save(self.state, record)
            return {"ok": record["status"] not in ("failed", "outcome_unknown"),
                    "status": record["status"]}
        # Exact-site inventory and state validation, with no device writes.
        if not self.command(["eco-site", "cabin", "off", "--dry-run"]):
            return {"ok": False, "status": "preflight_failed"}
        if self.evidence() != ("occupied", arrival):
            raise SafetyError("presence_changed")
        record = {"schema_version": 1, "armed": False, "status": "applying",
                  "arrival": arrival, "started_at": self.clock().isoformat(),
                  "confirmed_steps": 0}
        save(self.state, record)
        for room, degrees in ROOMS:
            for args in (["eco", room, "off"], ["mode", room, "HEAT"],
                         ["set", room, degrees]):
                # Each CLI resolves inventory again. Nest Sandbox permits
                # only five devices.list/structures.list calls per minute.
                # Space commands (including after preflight) to leave room
                # for status checks, then revalidate presence after waiting.
                self.sleep(20)
                try:
                    valid = self.evidence() == ("occupied", arrival)
                except (SafetyError, OSError, ValueError, KeyError, TypeError):
                    valid = False
                if not valid or not self.command(args):
                    record["status"] = "failed"
                    record["reason"] = "presence_changed" if not valid else "command_unverified"
                    save(self.state, record)
                    return {"ok": False, "status": "failed", "reason": record["reason"]}
                record["confirmed_steps"] += 1
                save(self.state, record)
        record["status"] = "complete"
        record["completed_at"] = self.clock().isoformat()
        save(self.state, record)
        return {"ok": True, "status": "complete", "living_room_f": 70, "bedroom_f": 68}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--restore-current", action="store_true",
                        help="explicitly restore the current occupied stay, including a failed attempt")
    args = parser.parse_args()
    try:
        result = Controller(Path.home()).evaluate(args.restore_current)
    except (SafetyError, OSError, ValueError, KeyError, TypeError):
        result = {"ok": False, "status": "blocked", "reason": "state_validation_failed"}
    print(json.dumps(result, sort_keys=True))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
