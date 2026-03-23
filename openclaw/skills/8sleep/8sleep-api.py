#!/usr/bin/env python3
"""Eight Sleep Pod API wrapper for OpenClaw.

Usage:
    8sleep-api.py status                  Current temperature, power for both sides
    8sleep-api.py temp <side> <level>     Set temperature (-100 to +100) for dylan|julia
    8sleep-api.py device                  Device info (model, firmware, water, connectivity)
    8sleep-api.py sleep <side> [date]     Sleep data for dylan|julia (default: last night)
    8sleep-api.py raw <path>              Raw API GET (e.g., "users/me")
"""

import json
import os
import sys
import time
import urllib.request
import urllib.error
from pathlib import Path

CONFIG_DIR = Path.home() / ".config" / "eightctl"
CONFIG_FILE = CONFIG_DIR / "config.yaml"
TOKEN_FILE = CONFIG_DIR / "token-cache.json"

AUTH_URL = "https://auth-api.8slp.net/v1/tokens"
API_URL = "https://client-api.8slp.net/v1"
CLIENT_ID = "0894c7f33bb94800a03f1f4df13a4f38"
CLIENT_SECRET = "f0954a3ed5763ba3d06834c73731a32f15f168f47d4f164751275def86db0c76"
USER_AGENT = "okhttp/4.9.3"

TOKEN_EXPIRY_BUFFER = 300  # refresh 5 min before expiry

# User IDs (from device data — Dylan=left, Julia=right)
USERS = {
    "dylan": {"id": "9ce2e82f950545969b18164ed79feeea", "side": "left"},
    "julia": {"id": "65012cc935d6472291c0b7324c8b12b6", "side": "right"},
}


def resolve_side(name):
    """Resolve a side name to user info."""
    name = name.lower().strip()
    if name in USERS:
        return USERS[name]
    for key, info in USERS.items():
        if name in key or name == info["side"]:
            return info
    return None


def load_config():
    """Load email/password from config.yaml."""
    if not CONFIG_FILE.exists():
        print(json.dumps({"error": "config_missing",
                          "message": f"Config not found at {CONFIG_FILE}"}))
        sys.exit(1)
    config = {}
    for line in CONFIG_FILE.read_text().splitlines():
        if ":" in line:
            key, _, value = line.partition(":")
            config[key.strip()] = value.strip()
    return config


def authenticate(email, password):
    """Get access token from Eight Sleep API."""
    data = json.dumps({
        "client_id": CLIENT_ID,
        "client_secret": CLIENT_SECRET,
        "grant_type": "password",
        "username": email,
        "password": password,
    }).encode()
    req = urllib.request.Request(
        AUTH_URL, data=data,
        headers={"Content-Type": "application/json", "user-agent": USER_AGENT}
    )
    try:
        resp = urllib.request.urlopen(req, timeout=15)
        result = json.loads(resp.read().decode())
        # Cache token with timestamp
        result["cached_at"] = time.time()
        TOKEN_FILE.write_text(json.dumps(result))
        TOKEN_FILE.chmod(0o600)
        return result
    except urllib.error.HTTPError as e:
        body = e.read().decode()
        if e.code == 429:
            print(json.dumps({"error": "rate_limited",
                              "message": "Rate limited by Eight Sleep API. Wait a few minutes."}))
        elif e.code == 400 and "invalid_grant" in body:
            print(json.dumps({"error": "auth_failed",
                              "message": "Invalid credentials. Check ~/.config/eightctl/config.yaml"}))
        else:
            print(json.dumps({"error": "auth_error", "status": e.code, "message": body[:300]}))
        sys.exit(1)


def get_token():
    """Get a valid token, using cache if available."""
    config = load_config()
    # Check cache
    if TOKEN_FILE.exists():
        try:
            cached = json.loads(TOKEN_FILE.read_text())
            cached_at = cached.get("cached_at", 0)
            expires_in = cached.get("expiresIn", cached.get("expires_in", 3600))
            if expires_in and time.time() - cached_at < (expires_in - TOKEN_EXPIRY_BUFFER):
                return cached
        except (json.JSONDecodeError, KeyError):
            pass
    return authenticate(config["email"], config["password"])


def api_get(path, token_data=None):
    """GET from the Eight Sleep API."""
    if token_data is None:
        token_data = get_token()
    req = urllib.request.Request(
        f"{API_URL}/{path}",
        headers={
            "Authorization": f"Bearer {token_data['access_token']}",
            "user-agent": USER_AGENT,
        }
    )
    try:
        resp = urllib.request.urlopen(req, timeout=15)
        return json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        return {"error": e.code, "message": e.read().decode()[:300]}


def api_put(path, body, token_data=None):
    """PUT to the Eight Sleep API."""
    if token_data is None:
        token_data = get_token()
    data = json.dumps(body).encode()
    req = urllib.request.Request(
        f"{API_URL}/{path}",
        data=data, method="PUT",
        headers={
            "Authorization": f"Bearer {token_data['access_token']}",
            "Content-Type": "application/json",
            "user-agent": USER_AGENT,
        }
    )
    try:
        resp = urllib.request.urlopen(req, timeout=15)
        return json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        return {"error": e.code, "message": e.read().decode()[:300]}


def cmd_status():
    """Show current status for both sides."""
    token_data = get_token()
    uid = token_data["userId"]

    # Get device info
    current = api_get(f"users/{uid}/current-device", token_data)
    dev_id = current.get("id", "")
    my_side = current.get("side", "unknown")

    device = api_get(f"devices/{dev_id}", token_data)
    d = device.get("result", device)

    sensor = d.get("sensorInfo", {})

    # Get temperature data
    temp = api_get(f"users/{uid}/temperature", token_data)

    output = {
        "device": {
            "model": sensor.get("skuName", "?") + " " + sensor.get("model", "?"),
            "connected": sensor.get("connected", False),
            "hasWater": d.get("hasWater", None),
            "needsPriming": d.get("needsPriming", None),
        },
        "dylan": {
            "side": "left",
            "currentLevel": d.get("leftHeatingLevel", "?"),
            "targetLevel": d.get("leftTargetHeatingLevel", "?"),
            "heating": d.get("leftNowHeating", False),
        },
        "julia": {
            "side": "right",
            "currentLevel": d.get("rightHeatingLevel", "?"),
            "targetLevel": d.get("rightTargetHeatingLevel", "?"),
            "heating": d.get("rightNowHeating", False),
        },
        "schedule": {
            "type": temp.get("settings", {}).get("scheduleType", "?"),
            "smart": temp.get("settings", {}).get("smart", {}),
        },
    }
    print(json.dumps(output, indent=2))


def cmd_temp(side_name, level):
    """Set temperature for a specific side."""
    user = resolve_side(side_name)
    if not user:
        print(json.dumps({"error": "invalid_side",
                          "message": f"Unknown side: {side_name}. Use 'dylan' or 'julia'"}))
        sys.exit(1)

    level = int(level)
    if level < -100 or level > 100:
        print(json.dumps({"error": "invalid_level",
                          "message": "Level must be between -100 and +100"}))
        sys.exit(1)

    token_data = get_token()
    result = api_put(f"users/{user['id']}/temperature", {
        "currentLevel": level,
    }, token_data)
    print(json.dumps({"success": True, "side": side_name, "level": level, "response": result}))


def cmd_device():
    """Show device info."""
    token_data = get_token()
    uid = token_data["userId"]
    current = api_get(f"users/{uid}/current-device", token_data)
    dev_id = current.get("id", "")
    device = api_get(f"devices/{dev_id}", token_data)
    d = device.get("result", device)

    sensor = d.get("sensorInfo", {})
    output = {
        "model": sensor.get("model", "?"),
        "size": sensor.get("skuName", "?"),
        "serial": sensor.get("serialNumber", "?"),
        "hwRevision": sensor.get("hwRevision", "?"),
        "connected": sensor.get("connected", False),
        "lastConnected": sensor.get("lastConnected", "?"),
        "hasWater": d.get("hasWater", None),
        "needsPriming": d.get("needsPriming", None),
        "lastLowWater": d.get("lastLowWater", None),
        "ledBrightness": d.get("ledBrightnessLevel", None),
        "leftUser": d.get("leftUserId", "?"),
        "rightUser": d.get("rightUserId", "?"),
    }
    print(json.dumps(output, indent=2))


def cmd_sleep(side_name, date=None):
    """Get sleep data for a specific side."""
    user = resolve_side(side_name)
    if not user:
        print(json.dumps({"error": "invalid_side",
                          "message": f"Unknown side: {side_name}. Use 'dylan' or 'julia'"}))
        sys.exit(1)

    token_data = get_token()
    uid = user["id"]

    if date:
        path = f"users/{uid}/trends?tz=America/New_York&from={date}&to={date}"
    else:
        from datetime import datetime, timedelta
        yesterday = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
        path = f"users/{uid}/trends?tz=America/New_York&from={yesterday}&to={yesterday}"

    result = api_get(path, token_data)
    result["side"] = side_name
    print(json.dumps(result, indent=2, default=str))


def cmd_raw(path):
    """Raw API GET."""
    result = api_get(path)
    print(json.dumps(result, indent=2, default=str))


def main():
    if len(sys.argv) < 2:
        print(__doc__.strip())
        sys.exit(1)

    cmd = sys.argv[1]
    if cmd == "status":
        cmd_status()
    elif cmd == "temp":
        if len(sys.argv) < 4:
            print(json.dumps({"error": "missing_arg",
                              "message": "Usage: 8sleep-api.py temp <dylan|julia> <level>"}))
            sys.exit(1)
        cmd_temp(sys.argv[2], sys.argv[3])
    elif cmd == "device":
        cmd_device()
    elif cmd == "sleep":
        if len(sys.argv) < 3:
            print(json.dumps({"error": "missing_arg",
                              "message": "Usage: 8sleep-api.py sleep <dylan|julia> [date]"}))
            sys.exit(1)
        cmd_sleep(sys.argv[2], sys.argv[3] if len(sys.argv) > 3 else None)
    elif cmd == "raw":
        if len(sys.argv) < 3:
            print(json.dumps({"error": "missing_arg", "message": "Usage: 8sleep-api.py raw <path>"}))
            sys.exit(1)
        cmd_raw(sys.argv[2])
    else:
        print(json.dumps({"error": "unknown_command", "message": f"Unknown: {cmd}"}))
        sys.exit(1)


if __name__ == "__main__":
    main()
