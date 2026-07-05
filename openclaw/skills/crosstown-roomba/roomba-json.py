#!/usr/bin/env python3
"""Small JSON boundary helper for the Crosstown Roomba shell wrapper."""

from __future__ import annotations

import json
import sys


def compact(value: object) -> str:
    return json.dumps(value, separators=(",", ":"))


def read_object() -> dict:
    value = json.load(sys.stdin)
    if not isinstance(value, dict):
        raise TypeError("response JSON must be an object")
    return value


def is_error(value: dict) -> bool:
    error = value.get("error")
    return error not in (None, False, 0, "") or value.get("ok") is False


def validate() -> None:
    try:
        value = read_object()
    except (json.JSONDecodeError, TypeError):
        print("Response was not valid object JSON")
        raise SystemExit(2)
    print(compact(value))
    if is_error(value):
        raise SystemExit(3)


def phase() -> None:
    try:
        value = read_object()
        current = value.get("cleanMissionStatus", {}).get("phase")
    except (json.JSONDecodeError, TypeError, AttributeError):
        raise SystemExit(2)
    if not isinstance(current, str) or not current:
        raise SystemExit(2)
    print(current)


def success(args: list[str]) -> None:
    robot, label, action, verification, skipped, current, message = args
    print(compact({
        "robot": robot,
        "label": label,
        "action": action,
        "ok": True,
        "verification": verification,
        "skipped": skipped == "true",
        "phase": current or None,
        "message": message,
    }))


def failure(args: list[str]) -> None:
    robot, label, action, code, message, detail, current = args
    error = {"code": code, "message": message}
    if detail:
        error["detail"] = detail[:1000]
    print(compact({
        "robot": robot,
        "label": label,
        "action": action,
        "ok": False,
        "verification": "failed",
        "skipped": False,
        "phase": current or None,
        "error": error,
    }))


def summary(args: list[str]) -> None:
    action, target = args
    results = [json.loads(line) for line in sys.stdin if line.strip()]
    print(compact({
        "action": action,
        "target": target,
        "ok": bool(results) and all(item.get("ok") is True for item in results),
        "results": results,
    }))


def status(args: list[str]) -> None:
    label, = args
    value = read_object()
    mission = value.get("cleanMissionStatus", {})
    phases = {
        "charge": "Charging (on dock)",
        "new": "Starting",
        "run": "Cleaning",
        "pause": "Paused",
        "stop": "Stopped",
        "stuck": "Stuck!",
        "hmMidMsn": "Returning to dock (recharging)",
        "hmUsrDock": "Returning to dock",
        "hmPostMsn": "Returning to dock (done)",
        "evac": "Emptying bin",
    }
    current = mission.get("phase", "?")
    bin_state = value.get("bin", {})
    print(f"{label}:")
    print(f"  Status:   {phases.get(current, current)}")
    print(f"  Battery:  {value.get('batPct', '?')}%")
    print(
        f"  Bin:      {'FULL' if bin_state.get('full') else 'OK'} "
        f"({'present' if bin_state.get('present') else 'MISSING'})"
    )
    if value.get("tankLvl") is not None:
        print(f"  Tank:     {value['tankLvl']}%")
    if mission.get("error", 0):
        print(f"  Error:    code {mission['error']}")
    print(f"  Missions: {mission.get('nMssn', '?')}")


def wifi(args: list[str]) -> None:
    label, = args
    value = read_object()
    signal = value.get("signal", {})
    netinfo = value.get("netinfo", {})
    ssid_hex = value.get("wlcfg", {}).get("ssid", "")
    try:
        ssid = bytes.fromhex(ssid_hex).decode("utf-8") if ssid_hex else "?"
    except (ValueError, UnicodeDecodeError):
        ssid = ssid_hex
    print(f"{label}:")
    print(f"  SSID:   {ssid}")
    print(f"  Signal: {signal.get('rssi', '?')} dBm (SNR {signal.get('snr', '?')})")
    print(f"  IP:     {netinfo.get('addr', '?')}")


COMMANDS = {
    "validate": (validate, 0),
    "phase": (phase, 0),
    "success": (success, 7),
    "failure": (failure, 7),
    "summary": (summary, 2),
    "status": (status, 1),
    "wifi": (wifi, 1),
}


def main() -> None:
    if len(sys.argv) < 2 or sys.argv[1] not in COMMANDS:
        raise SystemExit(2)
    function, arg_count = COMMANDS[sys.argv[1]]
    args = sys.argv[2:]
    if len(args) != arg_count:
        raise SystemExit(2)
    function() if arg_count == 0 else function(args)


if __name__ == "__main__":
    main()
