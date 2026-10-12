#!/usr/bin/env python3
"""Protected state for the exact Julia flower-bloom watch policy."""

from __future__ import annotations

import argparse
import json
import os
import stat
import tempfile
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path.home() / ".openclaw" / "plant-bloom-watch"
STATE = ROOT / "state.json"
RESULTS = {"yes", "no", "unclear"}
KEYS = {
    "schema",
    "rosesAlerted",
    "gladiolusAlerted",
    "failureCount",
    "failureNotified",
    "lastCheckAt",
    "lastResult",
}


def now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def default_state() -> dict:
    return {
        "schema": 1,
        "rosesAlerted": False,
        "gladiolusAlerted": False,
        "failureCount": 0,
        "failureNotified": False,
        "lastCheckAt": None,
        "lastResult": None,
    }


def validate_root(create: bool = True) -> None:
    if not ROOT.exists():
        if not create:
            return
        ROOT.mkdir(mode=0o700, parents=True)
    info = ROOT.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o700:
        raise SystemExit("protected bloom-watch state directory is unsafe")


def validate(value: object) -> dict:
    if not isinstance(value, dict) or set(value) != KEYS:
        raise SystemExit("protected bloom-watch state is invalid")
    if value["schema"] != 1:
        raise SystemExit("protected bloom-watch state has an unsupported schema")
    if type(value["rosesAlerted"]) is not bool or type(value["gladiolusAlerted"]) is not bool:
        raise SystemExit("protected bloom-watch alert state is invalid")
    if type(value["failureCount"]) is not int or value["failureCount"] < 0:
        raise SystemExit("protected bloom-watch failure count is invalid")
    if type(value["failureNotified"]) is not bool:
        raise SystemExit("protected bloom-watch failure notification state is invalid")
    if value["lastCheckAt"] is not None and not isinstance(value["lastCheckAt"], str):
        raise SystemExit("protected bloom-watch timestamp is invalid")
    result = value["lastResult"]
    if result is not None:
        if not isinstance(result, dict) or set(result) != {"roses", "gladiolus"}:
            raise SystemExit("protected bloom-watch result is invalid")
        if result["roses"] not in RESULTS or result["gladiolus"] not in RESULTS:
            raise SystemExit("protected bloom-watch result value is invalid")
    return value


def load() -> dict:
    validate_root()
    if not STATE.exists():
        return default_state()
    info = STATE.lstat()
    if (
        not stat.S_ISREG(info.st_mode)
        or info.st_uid != os.getuid()
        or stat.S_IMODE(info.st_mode) != 0o600
        or info.st_nlink != 1
    ):
        raise SystemExit("protected bloom-watch state file is unsafe")
    try:
        return validate(json.loads(STATE.read_text(encoding="utf-8")))
    except (OSError, json.JSONDecodeError) as exc:
        raise SystemExit("protected bloom-watch state could not be read") from exc


def save(value: dict) -> None:
    validate(value)
    validate_root()
    fd, path = tempfile.mkstemp(prefix="state.", suffix=".tmp", dir=ROOT)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(value, handle, separators=(",", ":"), sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(path, STATE)
        os.chmod(STATE, 0o600)
    finally:
        try:
            os.unlink(path)
        except FileNotFoundError:
            pass


def output(value: dict) -> None:
    result = dict(value)
    result["complete"] = bool(result["rosesAlerted"] and result["gladiolusAlerted"])
    print(json.dumps(result, separators=(",", ":"), sort_keys=True))


def main() -> None:
    parser = argparse.ArgumentParser()
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("read")
    check = commands.add_parser("record-check")
    check.add_argument("--roses", choices=sorted(RESULTS), required=True)
    check.add_argument("--gladiolus", choices=sorted(RESULTS), required=True)
    commands.add_parser("record-failure")
    mark = commands.add_parser("mark-alert")
    mark.add_argument("--kind", choices=("roses", "gladiolus", "failure"), required=True)
    args = parser.parse_args()

    value = load()
    if args.command == "read":
        output(value)
        return
    if args.command == "record-check":
        value["lastCheckAt"] = now()
        value["lastResult"] = {"roses": args.roses, "gladiolus": args.gladiolus}
        value["failureCount"] = 0
        value["failureNotified"] = False
        save(value)
        output(value)
        return
    if args.command == "record-failure":
        value["lastCheckAt"] = now()
        value["lastResult"] = None
        value["failureCount"] += 1
        save(value)
        result = dict(value)
        result["complete"] = bool(result["rosesAlerted"] and result["gladiolusAlerted"])
        result["shouldNotify"] = bool(result["failureCount"] >= 3 and not result["failureNotified"])
        print(json.dumps(result, separators=(",", ":"), sort_keys=True))
        return
    if args.kind == "roses":
        value["rosesAlerted"] = True
    elif args.kind == "gladiolus":
        value["gladiolusAlerted"] = True
    else:
        value["failureNotified"] = True
    save(value)
    output(value)


if __name__ == "__main__":
    main()
