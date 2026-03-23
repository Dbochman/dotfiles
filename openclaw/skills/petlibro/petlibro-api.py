#!/usr/bin/env python3
"""Petlibro smart pet device API wrapper for OpenClaw.

Usage:
    petlibro-api.py status                All devices summary
    petlibro-api.py feed <name> [N]       Manual feed N portions (default 1)
    petlibro-api.py water <name>          Today's water intake
    petlibro-api.py schedule <name>       Today's feeding schedule
    petlibro-api.py devices               List all devices with IDs
    petlibro-api.py raw <endpoint> [json] Raw API POST
"""

import hashlib
import json
import os
import sys
import time
import urllib.request
import urllib.error
from pathlib import Path

CONFIG_DIR = Path.home() / ".config" / "petlibro"
CONFIG_FILE = CONFIG_DIR / "config.yaml"
TOKEN_FILE = CONFIG_DIR / "token-cache.json"

BASE_URL = "https://api.us.petlibro.com"
APPID = 1
APPSN = "c35772530d1041699c87fe62348507a8"
TOKEN_EXPIRY_BUFFER = 300  # refresh 5 min before assumed 1h expiry


def load_config():
    if not CONFIG_FILE.exists():
        print(json.dumps({"error": "config_missing", "message": f"Config not found at {CONFIG_FILE}"}))
        sys.exit(1)
    config = {}
    for line in CONFIG_FILE.read_text().splitlines():
        if ":" in line:
            k, _, v = line.partition(":")
            config[k.strip()] = v.strip()
    return config


def api_post(endpoint, body=None, token=None):
    headers = {
        "Content-Type": "application/json",
        "source": "ANDROID",
        "language": "EN",
        "version": "1.3.45",
    }
    if token:
        headers["token"] = token
    data = json.dumps(body or {}).encode()
    req = urllib.request.Request(f"{BASE_URL}{endpoint}", data=data, headers=headers)
    try:
        resp = urllib.request.urlopen(req, timeout=15)
        result = json.loads(resp.read().decode())
        if result.get("code") == 1009:  # NOT_YET_LOGIN — token expired
            return {"error": "token_expired", "code": 1009}
        return result
    except urllib.error.HTTPError as e:
        return {"error": "http", "status": e.code, "message": e.read().decode()[:300]}


def authenticate(email, password):
    md5_pass = hashlib.md5(password.encode("UTF-8")).hexdigest()
    result = api_post("/member/auth/login", {
        "appId": APPID,
        "appSn": APPSN,
        "country": "US",
        "email": email,
        "password": md5_pass,
        "timezone": "America/New_York",
    })
    if result.get("code") == 0:
        token = result["data"]["token"]
        cache = {"token": token, "cached_at": time.time()}
        TOKEN_FILE.write_text(json.dumps(cache))
        TOKEN_FILE.chmod(0o600)
        return token
    else:
        print(json.dumps({"error": "auth_failed", "detail": result}))
        sys.exit(1)


def get_token():
    config = load_config()
    if TOKEN_FILE.exists():
        try:
            cached = json.loads(TOKEN_FILE.read_text())
            if time.time() - cached.get("cached_at", 0) < 3600 - TOKEN_EXPIRY_BUFFER:
                return cached["token"]
        except (json.JSONDecodeError, KeyError):
            pass
    return authenticate(config["email"], config["password"])


def get_token_with_retry():
    token = get_token()
    # Test token validity
    result = api_post("/device/device/list", {}, token)
    if result.get("error") == "token_expired":
        config = load_config()
        token = authenticate(config["email"], config["password"])
    return token


def resolve_device(devices, name):
    name_lower = name.lower()
    for d in devices:
        dname = d.get("name", "").lower()
        dprod = d.get("productName", "").lower()
        if name_lower in dname or name_lower in dprod:
            return d
        # Short aliases
        if name_lower in ("feeder", "feed", "food") and "feeder" in dprod:
            return d
        if name_lower in ("fountain", "water", "drink") and "fountain" in dprod:
            return d
    return None


def cmd_status():
    token = get_token_with_retry()
    result = api_post("/device/device/list", {}, token)
    devices = result.get("data", [])

    output = []
    for d in devices:
        dev = {
            "name": d.get("name", "?"),
            "model": d.get("productIdentifier", "?"),
            "online": d.get("online", False),
            "wifi": d.get("wifiRssiLevel", "?"),
        }

        if "feeder" in d.get("productName", "").lower():
            dev["type"] = "feeder"
            dev["foodLevel"] = d.get("warehouseSurplusGrain", "?")
            dev["nextFeedTime"] = d.get("nextFeedingTime", "?")
            dev["nextFeedPortions"] = d.get("nextFeedingQuantity", "?")
            dev["soundEnabled"] = d.get("enableSound", None)
            dev["lightEnabled"] = d.get("enableLight", None)
            dev["bowlMode"] = d.get("bowlMode", None)

        elif "fountain" in d.get("productName", "").lower():
            dev["type"] = "fountain"
            dev["waterWeight"] = d.get("weight", "?")
            dev["waterPercent"] = d.get("weightPercent", "?")
            dev["todayDrinkMl"] = d.get("todayTotalMl", "?")
            dev["battery"] = d.get("electricQuantity", "?")
            dev["batteryState"] = d.get("batteryState", "?")
            dev["filterDaysRemaining"] = d.get("remainingReplacementDays", "?")
            dev["cleaningDaysRemaining"] = d.get("remainingCleaningDays", "?")

        output.append(dev)

    print(json.dumps(output, indent=2))


def cmd_feed(name, portions=1):
    token = get_token_with_retry()
    result = api_post("/device/device/list", {}, token)
    devices = result.get("data", [])
    device = resolve_device(devices, name)
    if not device:
        print(json.dumps({"error": "device_not_found", "message": f"No device matching '{name}'"}))
        sys.exit(1)

    sn = device.get("deviceSn", "")
    feed_result = api_post("/device/device/manualFeeding", {
        "deviceSn": sn,
        "grainNum": int(portions),
    }, token)

    if feed_result.get("code") == 0:
        print(json.dumps({"success": True, "device": device.get("name"), "portions": int(portions)}))
    else:
        print(json.dumps({"error": "feed_failed", "detail": feed_result}))


def cmd_water(name):
    token = get_token_with_retry()
    result = api_post("/device/device/list", {}, token)
    devices = result.get("data", [])
    device = resolve_device(devices, name)
    if not device:
        print(json.dumps({"error": "device_not_found", "message": f"No device matching '{name}'"}))
        sys.exit(1)

    sn = device.get("deviceSn", "")
    water_result = api_post("/data/deviceDrinkWater/todayDrinkData", {
        "deviceSn": sn,
    }, token)

    if water_result.get("code") == 0:
        print(json.dumps(water_result.get("data", {}), indent=2))
    else:
        print(json.dumps({"error": "water_failed", "detail": water_result}))


def cmd_schedule(name):
    token = get_token_with_retry()
    result = api_post("/device/device/list", {}, token)
    devices = result.get("data", [])
    device = resolve_device(devices, name)
    if not device:
        print(json.dumps({"error": "device_not_found", "message": f"No device matching '{name}'"}))
        sys.exit(1)

    sn = device.get("deviceSn", "")
    sched_result = api_post("/device/feedingPlan/todayNew", {
        "deviceSn": sn,
    }, token)

    if sched_result.get("code") == 0:
        print(json.dumps(sched_result.get("data", {}), indent=2))
    else:
        print(json.dumps({"error": "schedule_failed", "detail": sched_result}))


def cmd_devices():
    token = get_token_with_retry()
    result = api_post("/device/device/list", {}, token)
    devices = result.get("data", [])
    output = []
    for d in devices:
        output.append({
            "name": d.get("name", "?"),
            "model": d.get("productIdentifier", "?"),
            "productName": d.get("productName", "?"),
            "serial": d.get("deviceSn", "?"),
            "mac": d.get("mac", "?"),
            "online": d.get("online", False),
            "firmware": d.get("softwareVersion", "?"),
        })
    print(json.dumps(output, indent=2))


def cmd_raw(endpoint, body_str=None):
    token = get_token_with_retry()
    body = json.loads(body_str) if body_str else {}
    result = api_post(endpoint, body, token)
    print(json.dumps(result, indent=2, default=str))


def main():
    if len(sys.argv) < 2:
        print(__doc__.strip())
        sys.exit(1)

    cmd = sys.argv[1]
    if cmd == "status":
        cmd_status()
    elif cmd == "feed":
        if len(sys.argv) < 3:
            print(json.dumps({"error": "missing_arg", "message": "Usage: petlibro-api.py feed <name> [portions]"}))
            sys.exit(1)
        portions = int(sys.argv[3]) if len(sys.argv) > 3 else 1
        cmd_feed(sys.argv[2], portions)
    elif cmd == "water":
        if len(sys.argv) < 3:
            print(json.dumps({"error": "missing_arg", "message": "Usage: petlibro-api.py water <name>"}))
            sys.exit(1)
        cmd_water(sys.argv[2])
    elif cmd == "schedule":
        if len(sys.argv) < 3:
            print(json.dumps({"error": "missing_arg", "message": "Usage: petlibro-api.py schedule <name>"}))
            sys.exit(1)
        cmd_schedule(sys.argv[2])
    elif cmd == "devices":
        cmd_devices()
    elif cmd == "raw":
        if len(sys.argv) < 3:
            print(json.dumps({"error": "missing_arg", "message": "Usage: petlibro-api.py raw <endpoint> [json]"}))
            sys.exit(1)
        cmd_raw(sys.argv[2], sys.argv[3] if len(sys.argv) > 3 else None)
    else:
        print(json.dumps({"error": "unknown_command", "message": f"Unknown: {cmd}"}))
        sys.exit(1)


if __name__ == "__main__":
    main()
