#!/usr/bin/env python3
"""Eight Sleep Pod API wrapper for OpenClaw.

Usage:
    8sleep-api.py [--location <name>] status                  Current temperature, power for both sides
    8sleep-api.py [--location <name>] temp <side> <level>     Set temperature (-100 to +100) for dylan|julia
    8sleep-api.py [--location <name>] off <side>              Turn off side (stop thermal unit)
    8sleep-api.py [--location <name>] on <side>               Turn on side (resume smart schedule)
    8sleep-api.py [--location <name>] away <side> start|end   Start/end away mode for a side
    8sleep-api.py [--location <name>] home <side>             Make this Pod current and end away mode
    8sleep-api.py [--location <name>] device                  Device info (model, firmware, water, connectivity)
    8sleep-api.py [--location <name>] sleep <side> [date]     Sleep data for dylan|julia (default: last night)
    8sleep-api.py raw <path>                                  Raw API GET (e.g., "users/me")

Location selects which Pod to target on multi-Pod accounts. Default: crosstown.
Resolved via EIGHTSLEEP_<LOC>_DEVICE_ID env (optional — falls back to
the account's "current device" if not set, which is the only Pod for
single-Pod accounts).
"""

import json
import os
import sys
import time
import fcntl
import urllib.request
import urllib.error
from pathlib import Path

CONFIG_DIR = Path.home() / ".config" / "eightctl"
CONFIG_FILE = CONFIG_DIR / "config.yaml"
TOKEN_FILE = CONFIG_DIR / "token-cache.json"
ROUTING_LOCK_FILE = CONFIG_DIR / "routing.lock"

AUTH_URL = "https://auth-api.8slp.net/v1/tokens"
API_URL = "https://client-api.8slp.net/v1"
APP_API_URL = "https://app-api.8slp.net/v1"  # write operations (pyEight convention)
CLIENT_ID = os.environ["EIGHTSLEEP_CLIENT_ID"]
CLIENT_SECRET = os.environ["EIGHTSLEEP_CLIENT_SECRET"]
USER_AGENT = "okhttp/4.9.3"

TOKEN_EXPIRY_BUFFER = 300  # refresh 5 min before expiry

# User IDs (from device data — Dylan=left, Julia=right)
USERS = {
    "dylan": {"id": os.environ["EIGHTSLEEP_DYLAN_USER_ID"], "side": "left"},
    "julia": {"id": os.environ["EIGHTSLEEP_JULIA_USER_ID"], "side": "right"},
}

# Per-location Pod device IDs. These are required for deterministic routing on
# multi-Pod accounts. A single-Pod account can still fall back to current-device.
LOCATIONS = {
    "crosstown": os.environ.get("EIGHTSLEEP_CROSSTOWN_DEVICE_ID"),
    "cabin": os.environ.get("EIGHTSLEEP_CABIN_DEVICE_ID"),
}
DEFAULT_LOCATION = "crosstown"


def resolve_side(name):
    """Resolve a side name to user info."""
    name = name.lower().strip()
    if name in USERS:
        return USERS[name]
    for key, info in USERS.items():
        if name in key or name == info["side"]:
            return info
    return None


def resolve_device_id(token_data, location):
    """Get the Eight Sleep device ID for a location.

    If EIGHTSLEEP_<LOC>_DEVICE_ID is set, use it directly (multi-Pod accounts).
    Otherwise fall back to users/<uid>/current-device (correct for single-Pod
    accounts and for whichever Pod the user most recently used).
    """
    configured = LOCATIONS.get(location)
    if configured:
        return configured
    uid = token_data["userId"]
    current = api_get(f"users/{uid}/current-device", token_data)
    return current.get("id", "")


def pop_location_arg(args):
    """Pop --location/-l from a list of args, return (location, remaining).

    Accepts: --location <name>, --location=<name>, -l <name>.
    Returns DEFAULT_LOCATION if none specified.
    """
    location = DEFAULT_LOCATION
    out = []
    i = 0
    while i < len(args):
        a = args[i]
        if a in ("-l", "--location"):
            if i + 1 >= len(args):
                print(json.dumps({"error": "missing_arg",
                                  "message": "--location requires a value"}))
                sys.exit(1)
            location = args[i + 1].lower()
            i += 2
        elif a.startswith("--location="):
            location = a.split("=", 1)[1].lower()
            i += 1
        else:
            out.append(a)
            i += 1
    if location not in LOCATIONS:
        print(json.dumps({"error": "unknown_location",
                          "message": f"Unknown location: {location}. Known: {sorted(LOCATIONS.keys())}"}))
        sys.exit(1)
    return location, out


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


def _auth_request(payload):
    """Send an auth request and cache the result."""
    data = json.dumps(payload).encode()
    req = urllib.request.Request(
        AUTH_URL, data=data,
        headers={"Content-Type": "application/json", "user-agent": USER_AGENT}
    )
    resp = urllib.request.urlopen(req, timeout=15)
    result = json.loads(resp.read().decode())
    result["cached_at"] = time.time()
    TOKEN_FILE.write_text(json.dumps(result))
    TOKEN_FILE.chmod(0o600)
    return result


def refresh_token(token_data):
    """Refresh access token using a refresh token.

    The refresh grant doesn't return userId, so we preserve it from the
    previous cached token to avoid breaking downstream code.
    """
    rt = token_data.get("refresh_token") or token_data.get("refreshToken")
    if not rt:
        return None
    try:
        result = _auth_request({
            "client_id": CLIENT_ID,
            "client_secret": CLIENT_SECRET,
            "grant_type": "refresh_token",
            "refresh_token": rt,
        })
        # Preserve fields the refresh response doesn't include
        for key in ("userId",):
            if key not in result and key in token_data:
                result[key] = token_data[key]
                TOKEN_FILE.write_text(json.dumps(result))
                TOKEN_FILE.chmod(0o600)
        return result
    except urllib.error.HTTPError:
        # Refresh failed (expired/revoked) — fall through to password auth
        return None


def authenticate(email, password):
    """Get access token from Eight Sleep API via password grant."""
    try:
        return _auth_request({
            "client_id": CLIENT_ID,
            "client_secret": CLIENT_SECRET,
            "grant_type": "password",
            "username": email,
            "password": password,
        })
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
    """Get a valid token: cached → refresh → password auth."""
    config = load_config()
    cached = None
    if TOKEN_FILE.exists():
        try:
            cached = json.loads(TOKEN_FILE.read_text())
            cached_at = cached.get("cached_at", 0)
            expires_in = cached.get("expiresIn", cached.get("expires_in", 3600))
            if expires_in and time.time() - cached_at < (expires_in - TOKEN_EXPIRY_BUFFER):
                return cached
        except (json.JSONDecodeError, KeyError):
            cached = None
    # Token expired — try refresh first (avoids rate limits on password grant)
    if cached:
        refreshed = refresh_token(cached)
        if refreshed:
            return refreshed
    # No cached token or refresh failed — full password auth
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
        payload = resp.read().decode()
        return json.loads(payload) if payload else {}
    except urllib.error.HTTPError as e:
        return {"error": e.code, "message": e.read().decode()[:300]}
    except (urllib.error.URLError, OSError, json.JSONDecodeError) as e:
        return {"error": "network", "message": str(e)}


def api_get_app(path, token_data=None):
    """GET from the Eight Sleep app API."""
    if token_data is None:
        token_data = get_token()
    req = urllib.request.Request(
        f"{APP_API_URL}/{path}",
        headers={
            "Authorization": f"Bearer {token_data['access_token']}",
            "user-agent": USER_AGENT,
        }
    )
    try:
        resp = urllib.request.urlopen(req, timeout=15)
        payload = resp.read().decode()
        return json.loads(payload) if payload else {}
    except urllib.error.HTTPError as e:
        return {"error": e.code, "message": e.read().decode()[:300]}
    except (urllib.error.URLError, OSError, json.JSONDecodeError) as e:
        return {"error": "network", "message": str(e)}


def api_put(path, body, token_data=None, use_app_api=False):
    """PUT to the Eight Sleep API."""
    if token_data is None:
        token_data = get_token()
    base = APP_API_URL if use_app_api else API_URL
    data = json.dumps(body).encode()
    req = urllib.request.Request(
        f"{base}/{path}",
        data=data, method="PUT",
        headers={
            "Authorization": f"Bearer {token_data['access_token']}",
            "Content-Type": "application/json",
            "user-agent": USER_AGENT,
        }
    )
    try:
        resp = urllib.request.urlopen(req, timeout=15)
        payload = resp.read().decode()
        return json.loads(payload) if payload else {}
    except urllib.error.HTTPError as e:
        return {"error": e.code, "message": e.read().decode()[:300]}
    except (urllib.error.URLError, OSError, json.JSONDecodeError) as e:
        return {"error": "network", "message": str(e)}


class APICommandError(Exception):
    """A user-facing Eight Sleep API or routing failure."""


def require_api_success(result, operation):
    """Return an API result or raise a concise error for a failed response."""
    if isinstance(result, dict):
        if result.get("error") or result.get("success") is False:
            message = result.get(
                "message", result.get("error", "API reported an unsuccessful result")
            )
            raise APICommandError(f"{operation} failed: {message}")
    return result


def resolve_location_set(user_id, location, token_data):
    """Resolve a location's device to its household set for one user."""
    summary = require_api_success(
        api_get_app(f"household/users/{user_id}/summary", token_data),
        f"loading {location} household routing",
    )
    sets = [
        item
        for household in summary.get("households", [])
        for item in household.get("sets", [])
    ]
    configured_device = LOCATIONS.get(location)
    if not configured_device:
        device_ids = {
            device.get("deviceId")
            for item in sets
            for device in item.get("devices", [])
            if device.get("deviceId")
        }
        if len(device_ids) > 1:
            variable = f"EIGHTSLEEP_{location.upper()}_DEVICE_ID"
            raise APICommandError(
                f"{variable} is required for a multi-Pod account"
            )
        configured_device = next(iter(device_ids), None)

    for item in sets:
        if any(
            device.get("deviceId") == configured_device
            for device in item.get("devices", [])
        ):
            set_id = item.get("setId")
            if not set_id:
                raise APICommandError(
                    f"{location} household routing did not include a setId"
                )
            return set_id
    raise APICommandError(
        f"configured {location} Pod is not present in the user's household"
    )


def get_current_set(user_id, token_data, allow_missing=False):
    """Return the current household set selected for a user."""
    result = require_api_success(
        api_get_app(f"household/users/{user_id}/current-set", token_data),
        "reading current Pod selection",
    )
    set_id = result.get("setId")
    if not set_id:
        if allow_missing:
            return None
        raise APICommandError("current Pod selection did not include a setId")
    return set_id


def select_current_set(user_id, set_id, token_data):
    """Select and verify a household set for a user."""
    require_api_success(
        api_put(
            f"household/users/{user_id}/current-set",
            {"setId": set_id},
            token_data,
            use_app_api=True,
        ),
        "selecting target Pod",
    )
    if get_current_set(user_id, token_data) != set_id:
        raise APICommandError("Eight Sleep did not select the requested Pod")


def select_current_set_with_retry(user_id, set_id, token_data, attempts=3):
    """Select a set with a short retry window for transient cloud failures."""
    last_error = None
    for attempt in range(1, attempts + 1):
        try:
            select_current_set(user_id, set_id, token_data)
            return
        except APICommandError as exc:
            last_error = exc
            if attempt < attempts:
                time.sleep(0.5 * attempt)
    raise last_error


def acquire_routing_lock():
    """Serialize local commands that inspect or change a user's current set."""
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    lock_file = ROUTING_LOCK_FILE.open("a", encoding="utf-8")
    ROUTING_LOCK_FILE.chmod(0o600)
    fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
    return lock_file


def run_on_current_set(user_id, location, token_data, operation, require_home=False):
    """Run a user-scoped operation only when the requested Pod is current.

    Selecting a household set is a semantic relocation: Eight Sleep makes that
    Pod current and marks the user's other Pod away. It is not a neutral routing
    mechanism, so ordinary controls must never switch sets temporarily.
    """
    with acquire_routing_lock():
        target_set = resolve_location_set(user_id, location, token_data)
        if get_current_set(user_id, token_data, allow_missing=True) != target_set:
            raise APICommandError(
                f"{location} is not this user's current Pod; run the home command first"
            )
        if require_home:
            away = require_api_success(
                api_get_app(f"users/{user_id}/away-mode", token_data),
                f"reading {location} away mode",
            )
            if away.get("isAway") is not False:
                raise APICommandError(
                    f"{location} is still away for this user; run the home command first"
                )
        result = require_api_success(operation(), f"{location} Pod operation")
        if get_current_set(user_id, token_data, allow_missing=True) != target_set:
            raise APICommandError("current Pod changed during the operation")
        return result


def command_error(exc):
    """Emit the CLI's JSON error contract and terminate nonzero."""
    print(json.dumps({"error": "api_error", "message": str(exc)}))
    raise SystemExit(1)


def cmd_status(location=DEFAULT_LOCATION):
    """Show current status for both sides."""
    token_data = get_token()
    uid = token_data["userId"]

    # Get device info — location selects which Pod on multi-Pod accounts
    dev_id = resolve_device_id(token_data, location)

    try:
        device = require_api_success(
            api_get(f"devices/{dev_id}", token_data),
            f"loading {location} Pod status",
        )
    except APICommandError as exc:
        command_error(exc)
    d = device.get("result", device)

    sensor = d.get("sensorInfo", {})

    # The schedule endpoint is user/current-set scoped. Avoid changing the
    # global current set for a read-only status call (the dashboard polls this),
    # and include schedule details only when the selected Pod is already current.
    current = api_get(f"users/{uid}/current-device", token_data)
    temp = {}
    if not current.get("error") and current.get("id") == dev_id:
        temp = api_get(f"users/{uid}/temperature", token_data)

    output = {
        "device": {
            "location": location,
            "deviceId": dev_id,
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


def cmd_temp(side_name, level, location=DEFAULT_LOCATION):
    """Set temperature for a specific side on one Pod."""
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
    try:
        result = run_on_current_set(
            user["id"],
            location,
            token_data,
            lambda: api_put(
                f"users/{user['id']}/temperature",
                {"currentLevel": level},
                token_data,
                use_app_api=True,
            ),
            require_home=True,
        )
    except APICommandError as exc:
        command_error(exc)
    print(json.dumps({"success": True, "location": location, "side": side_name, "level": level, "response": result}))


def cmd_device(location=DEFAULT_LOCATION):
    """Show device info."""
    token_data = get_token()
    dev_id = resolve_device_id(token_data, location)
    try:
        device = require_api_success(
            api_get(f"devices/{dev_id}", token_data),
            f"loading {location} Pod",
        )
    except APICommandError as exc:
        command_error(exc)
    d = device.get("result", device)

    sensor = d.get("sensorInfo", {})
    output = {
        "location": location,
        "deviceId": dev_id,
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


def cmd_sleep(side_name, date=None, location=DEFAULT_LOCATION):
    """Get sleep data for a specific side.

    User-scoped endpoint — see note in cmd_temp. Location is informational.
    """
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
        # Eight Sleep keys sleep by wake-up date, so "last night" = today
        from datetime import datetime
        today = datetime.now().strftime("%Y-%m-%d")
        path = f"users/{uid}/trends?tz=America/New_York&from={today}&to={today}"

    result = api_get(path, token_data)
    result["side"] = side_name
    result["location"] = location
    print(json.dumps(result, indent=2, default=str))


def cmd_off(side_name, location=DEFAULT_LOCATION):
    """Turn off a side (stop thermal unit) on one Pod."""
    user = resolve_side(side_name)
    if not user:
        print(json.dumps({"error": "invalid_side",
                          "message": f"Unknown side: {side_name}. Use 'dylan' or 'julia'"}))
        sys.exit(1)

    token_data = get_token()
    try:
        result = run_on_current_set(
            user["id"],
            location,
            token_data,
            lambda: api_put(
                f"users/{user['id']}/temperature",
                {"currentState": {"type": "off"}},
                token_data,
                use_app_api=True,
            ),
            require_home=True,
        )
    except APICommandError as exc:
        command_error(exc)
    print(json.dumps({"success": True, "location": location, "side": side_name, "state": "off", "response": result}))


def cmd_on(side_name, location=DEFAULT_LOCATION):
    """Turn on a side (resume smart schedule) on one Pod."""
    user = resolve_side(side_name)
    if not user:
        print(json.dumps({"error": "invalid_side",
                          "message": f"Unknown side: {side_name}. Use 'dylan' or 'julia'"}))
        sys.exit(1)

    token_data = get_token()
    try:
        result = run_on_current_set(
            user["id"],
            location,
            token_data,
            lambda: api_put(
                f"users/{user['id']}/temperature",
                {"currentState": {"type": "smart"}},
                token_data,
                use_app_api=True,
            ),
            require_home=True,
        )
    except APICommandError as exc:
        command_error(exc)
    print(json.dumps({"success": True, "location": location, "side": side_name, "state": "on", "response": result}))


def verify_home_assignment(user, location, token_data, attempts=5):
    """Verify the target side is assigned here and absent from the other Pod."""
    target_device = LOCATIONS.get(location)
    other_location = "cabin" if location == "crosstown" else "crosstown"
    other_device = LOCATIONS.get(other_location)
    if not target_device or not other_device or target_device == other_device:
        raise APICommandError(
            "both distinct Eight Sleep location device IDs are required for home"
        )

    field = f"{user['side']}UserId"
    query = "?filter=leftUserId,rightUserId,awaySides"
    last_error = None
    for attempt in range(1, attempts + 1):
        try:
            target_payload = require_api_success(
                api_get(f"devices/{target_device}{query}", token_data),
                f"verifying {location} side assignment",
            )
            other_payload = require_api_success(
                api_get(f"devices/{other_device}{query}", token_data),
                f"verifying {other_location} side assignment",
            )
            target = target_payload.get("result") or {}
            other = other_payload.get("result") or {}
            if not isinstance(target, dict) or not isinstance(other, dict):
                raise APICommandError("device assignment response was malformed")
            other_away = other.get("awaySides") or {}
            if not isinstance(other_away, dict):
                raise APICommandError("away-side assignment response was malformed")
            if (
                target.get(field) == user["id"]
                and not other.get(field)
                and other_away.get(field) == user["id"]
            ):
                return
        except APICommandError as exc:
            last_error = exc
        if attempt < attempts:
            time.sleep(0.5 * attempt)
    if last_error:
        raise last_error
    raise APICommandError(
        f"Eight Sleep did not move {user['side']} assignment to {location}"
    )


def cmd_home(side_name, location=DEFAULT_LOCATION):
    """Make one Pod current for a user and ensure that side is not away."""
    user = resolve_side(side_name)
    if not user:
        print(json.dumps({"error": "invalid_side",
                          "message": f"Unknown side: {side_name}. Use 'dylan' or 'julia'"}))
        sys.exit(1)

    from datetime import datetime, timezone

    token_data = get_token()
    changed = False
    try:
        with acquire_routing_lock():
            target_set = resolve_location_set(user["id"], location, token_data)
            if get_current_set(user["id"], token_data, allow_missing=True) != target_set:
                select_current_set_with_retry(user["id"], target_set, token_data)
                changed = True
            if get_current_set(user["id"], token_data, allow_missing=True) != target_set:
                raise APICommandError("Eight Sleep did not keep the requested Pod current")

            status = require_api_success(
                api_get_app(f"users/{user['id']}/away-mode", token_data),
                f"reading {location} home state",
            )
            result = {}
            if status.get("isAway") is not False:
                timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")
                result = require_api_success(
                    api_put(
                        f"users/{user['id']}/away-mode",
                        {"awayPeriod": {"end": timestamp}},
                        token_data,
                        use_app_api=True,
                    ),
                    f"ending {location} away mode",
                )
                changed = True
                status = require_api_success(
                    api_get_app(f"users/{user['id']}/away-mode", token_data),
                    f"verifying {location} home state",
                )
            if status.get("isAway") is not False:
                raise APICommandError(
                    f"{location} away-mode readback did not clear"
                )
            try:
                verify_home_assignment(user, location, token_data)
            except APICommandError:
                # A stale same-set assignment can survive while current-set
                # already names the target. Reassert the semantic relocation
                # once, then recheck both the active and away Pods.
                select_current_set_with_retry(user["id"], target_set, token_data)
                changed = True
                status = require_api_success(
                    api_get_app(f"users/{user['id']}/away-mode", token_data),
                    f"rechecking {location} home state",
                )
                if status.get("isAway") is not False:
                    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")
                    result = require_api_success(
                        api_put(
                            f"users/{user['id']}/away-mode",
                            {"awayPeriod": {"end": timestamp}},
                            token_data,
                            use_app_api=True,
                        ),
                        f"ending {location} away mode after reselection",
                    )
                    status = require_api_success(
                        api_get_app(f"users/{user['id']}/away-mode", token_data),
                        f"verifying {location} home state after reselection",
                    )
                    if status.get("isAway") is not False:
                        raise APICommandError(
                            f"{location} away-mode readback did not clear after reselection"
                        )
                verify_home_assignment(user, location, token_data)
            if get_current_set(user["id"], token_data, allow_missing=True) != target_set:
                raise APICommandError("current Pod changed during the home operation")
    except APICommandError as exc:
        command_error(exc)

    print(json.dumps({
        "success": True,
        "location": location,
        "side": side_name,
        "state": "home",
        "changed": changed,
        "response": result,
    }))


def cmd_away(side_name, action, location=DEFAULT_LOCATION):
    """Start or end away mode for a side on one Pod."""
    user = resolve_side(side_name)
    if not user:
        print(json.dumps({"error": "invalid_side",
                          "message": f"Unknown side: {side_name}. Use 'dylan' or 'julia'"}))
        sys.exit(1)

    from datetime import datetime, timezone, timedelta

    token_data = get_token()

    if action == "start":
        # Set start time to 24h ago (triggers immediate activation per pyEight convention)
        ts = (datetime.now(timezone.utc) - timedelta(hours=24)).strftime("%Y-%m-%dT%H:%M:%S.000Z")
        body = {"awayPeriod": {"start": ts}}
    elif action == "end":
        ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")
        body = {"awayPeriod": {"end": ts}}
    else:
        print(json.dumps({"error": "invalid_action",
                          "message": f"Unknown action: {action}. Use 'start' or 'end'"}))
        sys.exit(1)

    expected_away = action == "start"

    def apply_and_verify_away():
        result = require_api_success(
            api_put(
                f"users/{user['id']}/away-mode",
                body,
                token_data,
                use_app_api=True,
            ),
            f"setting {location} away mode",
        )
        status = require_api_success(
            api_get_app(f"users/{user['id']}/away-mode", token_data),
            f"verifying {location} away mode",
        )
        if status.get("isAway") is not expected_away:
            raise APICommandError(
                f"{location} away-mode readback did not match {action}"
            )
        return result

    try:
        result = run_on_current_set(
            user["id"],
            location,
            token_data,
            apply_and_verify_away,
        )
    except APICommandError as exc:
        command_error(exc)
    print(json.dumps({"success": True, "location": location, "side": side_name, "away": action, "response": result}))


def cmd_raw(path):
    """Raw API GET."""
    result = api_get(path)
    print(json.dumps(result, indent=2, default=str))


def main():
    if len(sys.argv) < 2:
        print(__doc__.strip())
        sys.exit(1)

    raw_args = sys.argv[1:]
    location_explicit = any(
        argument in ("-l", "--location") or argument.startswith("--location=")
        for argument in raw_args
    )
    location, rest = pop_location_arg(raw_args)
    if not rest:
        print(__doc__.strip())
        sys.exit(1)

    cmd = rest[0]
    if cmd == "status":
        cmd_status(location)
    elif cmd == "temp":
        if len(rest) < 3:
            print(json.dumps({"error": "missing_arg",
                              "message": "Usage: 8sleep-api.py temp <dylan|julia> <level>"}))
            sys.exit(1)
        cmd_temp(rest[1], rest[2], location)
    elif cmd == "off":
        if len(rest) < 2:
            print(json.dumps({"error": "missing_arg",
                              "message": "Usage: 8sleep-api.py off <dylan|julia>"}))
            sys.exit(1)
        cmd_off(rest[1], location)
    elif cmd == "on":
        if len(rest) < 2:
            print(json.dumps({"error": "missing_arg",
                              "message": "Usage: 8sleep-api.py on <dylan|julia>"}))
            sys.exit(1)
        cmd_on(rest[1], location)
    elif cmd == "away":
        if len(rest) < 3:
            print(json.dumps({"error": "missing_arg",
                              "message": "Usage: 8sleep-api.py away <dylan|julia> start|end"}))
            sys.exit(1)
        cmd_away(rest[1], rest[2], location)
    elif cmd == "home":
        if len(rest) < 2:
            print(json.dumps({"error": "missing_arg",
                              "message": "Usage: 8sleep-api.py home <dylan|julia>"}))
            sys.exit(1)
        if not location_explicit:
            print(json.dumps({"error": "missing_location",
                              "message": "home requires --location crosstown|cabin"}))
            sys.exit(1)
        cmd_home(rest[1], location)
    elif cmd == "device":
        cmd_device(location)
    elif cmd == "sleep":
        if len(rest) < 2:
            print(json.dumps({"error": "missing_arg",
                              "message": "Usage: 8sleep-api.py sleep <dylan|julia> [date]"}))
            sys.exit(1)
        cmd_sleep(rest[1], rest[2] if len(rest) > 2 else None, location)
    elif cmd == "raw":
        if len(rest) < 2:
            print(json.dumps({"error": "missing_arg", "message": "Usage: 8sleep-api.py raw <path>"}))
            sys.exit(1)
        cmd_raw(rest[1])
    else:
        print(json.dumps({"error": "unknown_command", "message": f"Unknown: {cmd}"}))
        sys.exit(1)


if __name__ == "__main__":
    main()
