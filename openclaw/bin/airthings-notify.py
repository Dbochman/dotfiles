#!/usr/bin/env python3
"""Evaluate Cabin ventilation nudges from existing samples; shadow by default."""

from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import argparse
import copy
import fcntl
import importlib.util
import json
import math
import os
from pathlib import Path
import re
import shlex
import stat
import sys
import tempfile
import time
from zoneinfo import ZoneInfo

ROOT = Path.home() / ".openclaw/airthings/notifications"
PRESENCE = Path.home() / ".openclaw/presence"
CACHE = Path.home() / ".openclaw/.secrets-cache"
LIMIT = 65536
ZONE = ZoneInfo("America/New_York")
ORIGIN = "airthings_ble_sampler_v1"
PEOPLE = {"dylan": "Dylan", "julia": "Julia"}
THRESHOLDS = {"co2": (1000, 600), "voc": (2000, 900)}
GOOD_LIMITS = {"co2": 800, "voc": 250}
GAP_SECONDS = 450
RECOVERY_SECONDS = 900
TARGET = re.compile(r"^[1-9][0-9]{0,17}$")


class NotifyError(Exception):
    pass


def stamp(value):
    return value.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def timestamp(value):
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            raise ValueError()
        return parsed.astimezone(timezone.utc)
    except (AttributeError, TypeError, ValueError):
        raise NotifyError("invalid_timestamp") from None


def fresh(value, now, maximum):
    return -60 <= (now - timestamp(value)).total_seconds() <= maximum


def strict_object(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise NotifyError("duplicate_json_key")
        value[key] = item
    return value


def decode(raw):
    if len(raw) > LIMIT:
        raise NotifyError("input_too_large")
    try:
        value = json.loads(raw, object_pairs_hook=strict_object)
    except (ValueError, UnicodeError):
        raise NotifyError("invalid_json") from None
    if not isinstance(value, dict):
        raise NotifyError("invalid_json")
    return value


def private_bytes(path):
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(descriptor, "rb") as handle:
        info = os.fstat(handle.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_nlink != 1 or stat.S_IMODE(info.st_mode) != 0o600:
            raise NotifyError("unsafe_private_file")
        raw = handle.read(LIMIT + 1)
    if len(raw) > LIMIT:
        raise NotifyError("input_too_large")
    return raw


def read_json(path):
    return decode(private_bytes(path))


def validate_root(root):
    info = root.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o700:
        raise NotifyError("unsafe_notification_directory")


def atomic_json(path, value):
    validate_root(path.parent)
    descriptor, temporary = tempfile.mkstemp(prefix=".notify-", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w") as handle:
            json.dump(value, handle, separators=(",", ":"), sort_keys=True)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        descriptor = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


@contextmanager
def locked(root):
    validate_root(root)
    descriptor = os.open(root / ".lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600)
    with os.fdopen(descriptor, "r+") as handle:
        info = os.fstat(handle.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_nlink != 1 or stat.S_IMODE(info.st_mode) != 0o600:
            raise NotifyError("unsafe_lock")
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise NotifyError("evaluation_busy") from None
        yield


def initial_state():
    return {"schema": 2, "lastSampleAt": None, "co2Since": None, "vocSince": None,
            "recoverySince": None, "episode": None, "notified": {}, "shadowed": [], "lastDecision": None,
            "recovery": None}


def validate_state(value):
    legacy = type(value.get("schema")) is int and value["schema"] == 1 and set(value) == set(initial_state()) - {"recovery"}
    if legacy:
        value = {**value, "schema": 2, "recovery": None}
    if set(value) != set(initial_state()) or type(value["schema"]) is not int or value["schema"] != 2:
        raise NotifyError("invalid_state")
    for key in ("lastSampleAt", "co2Since", "vocSince", "recoverySince", "episode"):
        if value[key] is not None:
            timestamp(value[key])
            if value["lastSampleAt"] is None or timestamp(value[key]) > timestamp(value["lastSampleAt"]):
                raise NotifyError("invalid_state")
    notified = value["notified"]
    shadowed = value["shadowed"]
    if not isinstance(notified, dict) or set(notified) - set(PEOPLE) or any(item not in ("reserved", "submitted", "unconfirmed") for item in notified.values()):
        raise NotifyError("invalid_state")
    if not isinstance(shadowed, list) or any(item not in PEOPLE for item in shadowed) or len(set(shadowed)) != len(shadowed):
        raise NotifyError("invalid_state")
    if value["episode"] is None and (notified or shadowed):
        raise NotifyError("invalid_state")
    if value["lastDecision"] is not None and not isinstance(value["lastDecision"], dict):
        raise NotifyError("invalid_state")
    recovery = value["recovery"]
    if recovery is not None:
        if not isinstance(recovery, dict) or set(recovery) != {"episode", "sampleAt", "recipients", "shadowRecipients", "notified", "shadowed"}:
            raise NotifyError("invalid_recovery")
        if value["lastSampleAt"] is None or not timestamp(recovery["episode"]) <= timestamp(recovery["sampleAt"]) <= timestamp(value["lastSampleAt"]):
            raise NotifyError("invalid_recovery")
        for key in ("recipients", "shadowRecipients", "shadowed"):
            people = recovery[key]
            if not isinstance(people, list) or any(person not in PEOPLE for person in people) or len(people) != len(set(people)):
                raise NotifyError("invalid_recovery")
        receipts = recovery["notified"]
        if not isinstance(receipts, dict) or set(receipts) - set(recovery["recipients"]) or any(item not in ("reserved", "submitted", "unconfirmed") for item in receipts.values()):
            raise NotifyError("invalid_recovery")
        if set(recovery["recipients"]) - set(recovery["shadowRecipients"]) or set(recovery["shadowed"]) - set(recovery["shadowRecipients"]):
            raise NotifyError("invalid_recovery")
    if legacy:
        value["recoverySince"] = None
    return value


def initialize(root):
    root.mkdir(mode=0o700)
    with locked(root):
        atomic_json(root / "state.json", initial_state())
        atomic_json(root / "config.json", {"schema": 1, "mode": "shadow"})


def mode_from(root):
    config = read_json(root / "config.json")
    if set(config) != {"schema", "mode"} or type(config["schema"]) is not int or config["schema"] != 1 or config["mode"] not in ("off", "shadow", "live"):
        raise NotifyError("invalid_policy")
    return config["mode"]


def sample_from(record, now):
    if record.get("history_origin") != ORIGIN or not fresh(record.get("timestamp"), now, 600):
        raise NotifyError("stale_or_wrong_sample")
    rooms = record.get("rooms")
    if not isinstance(rooms, list) or len(rooms) != 1 or not isinstance(rooms[0], dict):
        raise NotifyError("wrong_sample")
    room = rooms[0]
    if (room.get("source"), room.get("history_origin"), room.get("structure"), room.get("room"), room.get("connectivity")) != ("airthings", ORIGIN, "Philly", "Living Room", "ONLINE") or room.get("error") is not None:
        raise NotifyError("wrong_sample")
    result = {"timestamp": stamp(timestamp(record["timestamp"]))}
    for metric in THRESHOLDS:
        value = room.get("co2_ppm" if metric == "co2" else "voc_ppb")
        if type(value) not in (int, float) or not math.isfinite(value) or not 0 <= value <= 100000:
            raise NotifyError("invalid_reading")
        result[metric] = value
    return result


def occupants(now, directory=PRESENCE):
    canonical = read_json(directory / "state.json")
    cabin = read_json(directory / "cabin-scan.json")
    if "error" in canonical or "error" in cabin:
        raise NotifyError("presence_unavailable")
    if not fresh(canonical.get("timestamp"), now, 1800) or not fresh(cabin.get("timestamp"), now, 1800):
        raise NotifyError("presence_unavailable")
    if cabin.get("location") != "cabin" or canonical.get("cabin", {}).get("fresh") is not True or canonical.get("cabin", {}).get("occupancy") != "occupied":
        return []
    try:
        other = read_json(directory / "crosstown-scan.json")
        other_fresh = "error" not in other and other.get("location") == "crosstown" and fresh(other.get("timestamp"), now, 1800)
    except (OSError, NotifyError):
        other, other_fresh = {}, False
    result = []
    for alias, name in PEOPLE.items():
        person = canonical.get("people", {}).get(name, {})
        positive = cabin.get("presence", {}).get(name, {}).get("present") is True
        conflicting = other_fresh and other.get("presence", {}).get(name, {}).get("present") is True
        if positive and not conflicting and person.get("location") == "cabin" and person.get("cabin") is True and person.get("crosstown") is False:
            result.append(alias)
    return result


def advance(state, sample):
    state = copy.deepcopy(state)
    observed = timestamp(sample["timestamp"])
    previous = timestamp(state["lastSampleAt"]) if state["lastSampleAt"] else None
    if previous is not None and observed <= previous:
        return state, [], False
    if previous is None or (observed - previous).total_seconds() > GAP_SECONDS:
        for key in ("co2Since", "vocSince", "recoverySince"):
            state[key] = None
    state["lastSampleAt"] = sample["timestamp"]
    triggers = []
    for metric, (threshold, duration) in THRESHOLDS.items():
        key = metric + "Since"
        state[key] = (state[key] or sample["timestamp"]) if sample[metric] >= threshold else None
        if state[key] and (observed - timestamp(state[key])).total_seconds() >= duration:
            triggers.append(metric)
    if all(sample[metric] < limit for metric, limit in GOOD_LIMITS.items()):
        state["recoverySince"] = state["recoverySince"] or sample["timestamp"]
        if state["episode"] is not None and (observed - timestamp(state["recoverySince"])).total_seconds() >= RECOVERY_SECONDS:
            recipients = [owner for owner, status in state["notified"].items() if status == "submitted"]
            state["recovery"] = {
                "episode": state["episode"], "sampleAt": sample["timestamp"],
                "recipients": recipients, "shadowRecipients": sorted(set(recipients) | set(state["shadowed"])),
                "notified": {}, "shadowed": [],
            }
            state.update(episode=None, notified={}, shadowed=[])
    else:
        state["recoverySince"] = None
    if triggers and state["episode"] is None:
        state["episode"] = sample["timestamp"]
    return state, triggers, True


def render_message(sample, triggers):
    details = []
    if "co2" in triggers:
        details.append(f"CO₂ {round(sample['co2'])} ppm (elevated for at least 10 minutes)")
    if "voc" in triggers:
        details.append(f"VOCs {round(sample['voc'])} ppb (elevated for at least 15 minutes)")
    message = "Cabin living-room air: " + "; ".join(details) + ". "
    if "voc" in triggers:
        message += "Check for nearby sources such as cleaning products or cooking. "
    return message + "Consider opening a window briefly if weather and outdoor air quality permit."


def render_recovery_message(sample):
    return (
        "Cabin living-room CO₂ and VOC readings have stayed in their good ranges "
        f"for 15 minutes (CO₂ {round(sample['co2'])} ppm; VOCs {round(sample['voc'])} ppb). "
        "If you're done cooking and don't need more ventilation, you can probably close the windows."
    )


def credentials(cache=CACHE):
    values = {}
    wanted = {"DYLAN_CHAT_ID", "JULIA_CHAT_ID", "OPENCLAW_GATEWAY_TOKEN", "OPENCLAW_GATEWAY_PASSWORD"}
    for line in private_bytes(cache).decode().splitlines():
        key, separator, raw = line.strip().removeprefix("export ").partition("=")
        if separator and key in wanted:
            tokens = shlex.split(raw)
            if key in values or len(tokens) != 1:
                raise NotifyError("delivery_credentials_unavailable")
            values[key] = tokens[0]
    routes = {}
    for owner in PEOPLE:
        value = values.get(owner.upper() + "_CHAT_ID", "")
        if not TARGET.fullmatch(value):
            raise NotifyError("delivery_credentials_unavailable")
        routes[owner] = "chat_id:" + value
    if len(set(routes.values())) != len(routes) or not (values.get("OPENCLAW_GATEWAY_TOKEN") or values.get("OPENCLAW_GATEWAY_PASSWORD")):
        raise NotifyError("delivery_credentials_unavailable")
    return routes, {key: value for key, value in values.items() if key.startswith("OPENCLAW_GATEWAY_")}


def existing_sender(target, message, authentication):
    spec = importlib.util.spec_from_file_location("airthings_imessage_transport", Path(__file__).with_name("home-event-delivery.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    keys = ("OPENCLAW_GATEWAY_TOKEN", "OPENCLAW_GATEWAY_PASSWORD")
    previous = {key: os.environ.get(key) for key in keys}
    try:
        for key in keys:
            os.environ.pop(key, None)
        os.environ.update(authentication)
        module.send_message(target, message)
    finally:
        for key in keys:
            os.environ.pop(key, None)
            if previous[key] is not None:
                os.environ[key] = previous[key]


def evaluate(root, record, now, presence_reader=occupants, credential_reader=credentials, sender=existing_sender):
    started = time.monotonic()
    with locked(root):
        mode = mode_from(root)
        state = validate_state(read_json(root / "state.json"))
        decision = {"mode": mode, "checkedAt": stamp(now), "reason": "disabled", "wouldNotify": [], "submitted": [], "unconfirmed": []}
        if mode != "off":
            try:
                sample = sample_from(record, now)
                state, triggers, changed = advance(state, sample)
                recovery = state["recovery"]
                recovered = changed and recovery is not None and recovery["sampleAt"] == sample["timestamp"]
                decision["sampleAt"] = sample["timestamp"]
                decision["reason"] = "observing" if changed else "duplicate_or_old_sample"
                if changed and (triggers or recovered):
                    decision["triggers"] = triggers
                    decision["kind"] = "recovery" if recovered else "elevated"
                    if not 8 <= now.astimezone(ZONE).hour < 22:
                        decision["reason"] = "quiet_hours"
                    else:
                        recipients = presence_reader(now)
                        if not isinstance(recipients, list) or any(owner not in PEOPLE for owner in recipients) or len(set(recipients)) != len(recipients):
                            raise NotifyError("presence_unavailable")
                        ledger = recovery if recovered else state
                        if recovered:
                            eligible = recovery["shadowRecipients"] if mode == "shadow" else recovery["recipients"]
                            recipients = [owner for owner in recipients if owner in eligible]
                        decision["reason"] = "no_confirmed_occupants" if not recipients else "episode_already_notified"
                        pending = [owner for owner in recipients if owner not in (ledger["shadowed"] if mode == "shadow" else ledger["notified"])]
                        if pending and mode == "shadow":
                            ledger["shadowed"].extend(pending)
                            decision.update(reason="would_notify", wouldNotify=pending)
                        elif pending:
                            routes, authentication = credential_reader()
                            for owner in pending:
                                checked = now + timedelta(seconds=time.monotonic() - started)
                                if mode_from(root) != "live" or not 8 <= checked.astimezone(ZONE).hour < 22 or not fresh(sample["timestamp"], checked, 600) or owner not in presence_reader(checked):
                                    decision["reason"] = "delivery_recheck_suppressed"
                                    continue
                                ledger["notified"][owner] = "reserved"
                                state["lastDecision"] = {**decision, "reason": "delivery_reserved"}
                                atomic_json(root / "state.json", state)
                                try:
                                    message = render_recovery_message(sample) if recovered else render_message(sample, triggers)
                                    sender(routes[owner], message, authentication)
                                except Exception:
                                    ledger["notified"][owner] = "unconfirmed"
                                    decision["unconfirmed"].append(owner)
                                else:
                                    ledger["notified"][owner] = "submitted"
                                    decision["submitted"].append(owner)
                                decision["reason"] = "delivery_unconfirmed" if decision["unconfirmed"] else "submitted"
                                state["lastDecision"] = decision
                                atomic_json(root / "state.json", state)
            except (NotifyError, OSError, ValueError, TypeError, AttributeError, KeyError):
                decision["reason"] = "input_or_delivery_unavailable"
                state.update(co2Since=None, vocSince=None, recoverySince=None)
        state["lastDecision"] = decision
        atomic_json(root / "state.json", state)
        return decision


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("init", "evaluate", "status"))
    args = parser.parse_args()
    try:
        if args.command == "init":
            initialize(ROOT)
            result = {"mode": "shadow", "reason": "initialized"}
        elif args.command == "status":
            with locked(ROOT):
                result = {"mode": mode_from(ROOT), "state": validate_state(read_json(ROOT / "state.json"))}
        else:
            result = evaluate(ROOT, decode(sys.stdin.buffer.read(LIMIT + 1)), datetime.now(timezone.utc))
        print(json.dumps(result, separators=(",", ":"), sort_keys=True))
        return 0
    except (OSError, NotifyError, ValueError, TypeError):
        print('{"error":"notification_runtime_unavailable","ok":false}')
        return 12


if __name__ == "__main__":
    raise SystemExit(main())
