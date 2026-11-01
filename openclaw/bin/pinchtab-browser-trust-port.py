#!/usr/bin/env python3
"""Resolve only the selected managed instance's loopback CDP port; print no tokens."""
import hashlib
import json
import os
from pathlib import Path
import re
import sys


def resolve(instance, config, config_root, *, mode="headless"):
    if mode not in ("headless", "headed"):
        raise ValueError("Invalid expected instance mode")
    if instance.get("status") != "running" or instance.get("mode") != mode or instance.get("attached"):
        raise ValueError("Expected an exact-mode managed running instance")
    profile_id = instance.get("profileId", "")
    if not re.fullmatch(r"prof_[a-zA-Z0-9_-]+", profile_id):
        raise ValueError("Invalid managed profile identity")
    state = Path(config.get("server", {}).get("stateDir") or config_root).expanduser()
    base = Path(config.get("profiles", {}).get("baseDir") or state / "profiles").expanduser()
    child_path = base / profile_id / ".pinchtab-state/config.json"
    if not child_path.exists():
        # PinchTab also retains legacy directories named after the profile.
        # Bind this fallback to its deterministic ID, never an arbitrary path.
        name = instance.get("profileName", "")
        if (not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}", name)
                or "prof_" + hashlib.sha256(name.encode()).hexdigest()[:8] != profile_id):
            raise ValueError("Invalid named profile identity")
        child_path = base / name / ".pinchtab-state/config.json"
        if not child_path.resolve().is_relative_to(base.resolve()):
            raise ValueError("Profile configuration outside managed directory")
        child = json.loads(child_path.read_text())
        if Path(child.get("server", {}).get("stateDir", "")).resolve() != child_path.parent.resolve():
            raise ValueError("Stale named profile configuration")
    else:
        child = json.loads(child_path.read_text())
    if str(child["server"]["port"]) != str(instance["port"]):
        raise ValueError("Stale instance configuration")
    port = child["browser"]["remoteDebuggingPort"]
    if type(port) is not int or not 1 <= port <= 65535:
        raise ValueError("Invalid CDP port")
    return port


if __name__ == "__main__":
    try:
        root = Path.home() / ".pinchtab"
        config = json.loads(Path(os.environ.get("PINCHTAB_CONFIG") or root / "config.json").read_text())
        instance = next(row for row in json.load(sys.stdin) if row.get("id") == sys.argv[1])
        print(resolve(instance, config, root))
    except (OSError, ValueError, KeyError, IndexError, StopIteration, TypeError):
        print("Cannot resolve selected browser trust endpoint", file=sys.stderr)
        sys.exit(1)
