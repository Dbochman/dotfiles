#!/usr/bin/env python3
"""Fi collar API client — GPS location, battery, and activity for Potato."""

import json
import os
import sys
import time
import urllib.request
import urllib.parse
import urllib.error
import math

API_BASE = "https://api.tryfi.com"
CONFIG_DIR = os.path.expanduser("~/.config/fi-collar")
TOKEN_FILE = os.path.join(CONFIG_DIR, "session.json")
TOKEN_TTL = 3600 * 12  # 12 hours

# Home locations for proximity detection — from env vars to avoid committing coordinates
LOCATIONS = {}
if os.environ.get("CROSSTOWN_LAT") and os.environ.get("CROSSTOWN_LON"):
    LOCATIONS["crosstown"] = {
        "lat": float(os.environ["CROSSTOWN_LAT"]),
        "lon": float(os.environ["CROSSTOWN_LON"]),
        "radius_m": 150,
        "label": "Crosstown (19 Crosstown Ave)",
    }
if os.environ.get("CABIN_LAT") and os.environ.get("CABIN_LON"):
    LOCATIONS["cabin"] = {
        "lat": float(os.environ["CABIN_LAT"]),
        "lon": float(os.environ["CABIN_LON"]),
        "radius_m": 300,
        "label": "Cabin (95 School House Rd)",
    }


def haversine(lat1, lon1, lat2, lon2):
    """Distance in meters between two coordinates."""
    R = 6371000
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def nearest_location(lat, lon):
    """Return the nearest known location and distance."""
    best = None
    best_dist = float("inf")
    for name, loc in LOCATIONS.items():
        dist = haversine(lat, lon, loc["lat"], loc["lon"])
        if dist < best_dist:
            best_dist = dist
            best = name
    at_location = best_dist <= LOCATIONS[best]["radius_m"]
    return {"location": best, "label": LOCATIONS[best]["label"],
            "distance_m": round(best_dist), "at_location": at_location}


def login():
    """Login and cache session."""
    email = os.environ.get("TRYFI_EMAIL", "")
    password = os.environ.get("TRYFI_PASSWORD", "")
    if not email or not password:
        print(json.dumps({"error": "missing_credentials",
                          "message": "Set TRYFI_EMAIL and TRYFI_PASSWORD"}))
        sys.exit(1)

    data = urllib.parse.urlencode({"email": email, "password": password}).encode()
    req = urllib.request.Request(f"{API_BASE}/auth/login", data=data)
    try:
        resp = urllib.request.urlopen(req, timeout=15)
        result = json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        print(json.dumps({"error": e.code, "message": e.read().decode()[:300]}))
        sys.exit(1)
    except (urllib.error.URLError, OSError) as e:
        print(json.dumps({"error": "network", "message": str(e)}))
        sys.exit(1)

    if "error" in result:
        print(json.dumps(result))
        sys.exit(1)

    # Extract session cookie
    cookies = resp.headers.get_all("Set-Cookie") or []
    cookie_str = "; ".join(c.split(";")[0] for c in cookies)

    session = {
        "userId": result["userId"],
        "sessionId": result.get("sessionId", ""),
        "cookie": cookie_str,
        "cached_at": time.time(),
    }
    os.makedirs(CONFIG_DIR, exist_ok=True)
    with open(TOKEN_FILE, "w") as f:
        json.dump(session, f)
    os.chmod(TOKEN_FILE, 0o600)
    return session


def get_session():
    """Get cached session or login."""
    if os.path.exists(TOKEN_FILE):
        try:
            with open(TOKEN_FILE) as f:
                session = json.load(f)
            if time.time() - session.get("cached_at", 0) < TOKEN_TTL:
                return session
        except (json.JSONDecodeError, KeyError):
            pass
    return login()


def graphql(session, query, variables=None):
    """Execute a GraphQL query."""
    body = json.dumps({"query": query, "variables": variables or {}}).encode()
    req = urllib.request.Request(
        f"{API_BASE}/graphql", data=body, method="POST",
        headers={
            "Content-Type": "application/json",
            "Cookie": session.get("cookie", ""),
        }
    )
    try:
        resp = urllib.request.urlopen(req, timeout=15)
        result = json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        if e.code in (401, 403):
            # Session expired, re-login and retry once
            session = login()
            req.add_header("Cookie", session.get("cookie", ""))
            resp = urllib.request.urlopen(req, timeout=15)
            result = json.loads(resp.read().decode())
        else:
            return {"error": e.code, "message": e.read().decode()[:300]}
    except (urllib.error.URLError, OSError) as e:
        return {"error": "network", "message": str(e)}

    if "errors" in result:
        return {"error": "graphql", "message": result["errors"][0].get("message", str(result["errors"]))}
    return result.get("data", result)


def cmd_location():
    """Get Potato's current GPS location."""
    session = get_session()
    query = """query { currentUser { userHouseholds { household { pets {
        id name
        ongoingActivity { __typename start lastReportTimestamp areaName
            ... on OngoingRest { position { latitude longitude } place { name address } }
            ... on OngoingWalk { distance positions { date position { latitude longitude } } path { latitude longitude } }
        }
    } } } } }"""
    data = graphql(session, query)
    if "error" in data:
        print(json.dumps(data))
        sys.exit(1)

    for house in data["currentUser"]["userHouseholds"]:
        for pet in house["household"]["pets"]:
            activity = pet.get("ongoingActivity") or {}
            lat = lon = None
            if activity.get("position"):
                lat = activity["position"]["latitude"]
                lon = activity["position"]["longitude"]
            elif activity.get("positions"):
                last = activity["positions"][-1]["position"]
                lat, lon = last["latitude"], last["longitude"]

            result = {
                "name": pet["name"],
                "petId": pet["id"],
                "activity": activity.get("__typename", "Unknown").replace("Ongoing", ""),
                "areaName": activity.get("areaName"),
                "lastReport": activity.get("lastReportTimestamp") or activity.get("start"),
            }
            if lat is not None:
                result["latitude"] = lat
                result["longitude"] = lon
                result.update(nearest_location(lat, lon))
            if activity.get("place"):
                result["place"] = activity["place"].get("name")
                result["address"] = activity["place"].get("address")
            if activity.get("distance"):
                result["walkDistance_m"] = activity["distance"]

            print(json.dumps(result))


def cmd_status():
    """Get full status: location, battery, connection."""
    session = get_session()
    query = """query { currentUser { userHouseholds { household {
        pets {
            id name
            ongoingActivity { __typename start lastReportTimestamp areaName
                ... on OngoingRest { position { latitude longitude } place { name address } }
                ... on OngoingWalk { distance positions { date position { latitude longitude } } }
            }
            device { moduleId info
                operationParams { mode ledEnabled }
                lastConnectionState { __typename date
                    ... on ConnectedToUser { user { firstName } }
                    ... on ConnectedToBase { chargingBase { id } }
                    ... on ConnectedToCellular { signalStrengthPercent }
                }
            }
        }
        bases { baseId name online position { latitude longitude } }
    } } } }"""
    data = graphql(session, query)
    if "error" in data:
        print(json.dumps(data))
        sys.exit(1)

    for house in data["currentUser"]["userHouseholds"]:
        h = house["household"]
        for pet in h["pets"]:
            activity = pet.get("ongoingActivity") or {}
            device = pet.get("device") or {}
            conn = device.get("lastConnectionState") or {}

            lat = lon = None
            if activity.get("position"):
                lat = activity["position"]["latitude"]
                lon = activity["position"]["longitude"]
            elif activity.get("positions"):
                last = activity["positions"][-1]["position"]
                lat, lon = last["latitude"], last["longitude"]

            # Parse device info for battery
            battery = None
            if device.get("info"):
                try:
                    info = json.loads(device["info"]) if isinstance(device["info"], str) else device["info"]
                    battery = info.get("batteryPercent") or info.get("battery")
                except (json.JSONDecodeError, TypeError):
                    pass

            conn_type = conn.get("__typename", "Unknown").replace("ConnectedTo", "").replace("UnknownConnectivity", "Unknown")
            conn_detail = ""
            if conn.get("user"):
                conn_detail = conn["user"].get("firstName", "")
            elif conn.get("signalStrengthPercent") is not None:
                conn_detail = f"{conn['signalStrengthPercent']}%"

            result = {
                "name": pet["name"],
                "activity": activity.get("__typename", "Unknown").replace("Ongoing", ""),
                "areaName": activity.get("areaName"),
                "connection": conn_type,
                "connectionDetail": conn_detail,
                "connectionDate": conn.get("date"),
                "mode": device.get("operationParams", {}).get("mode"),
                "moduleId": device.get("moduleId"),
            }
            if battery is not None:
                result["battery"] = battery
            if lat is not None:
                result["latitude"] = lat
                result["longitude"] = lon
                result.update(nearest_location(lat, lon))
            if activity.get("place"):
                result["place"] = activity["place"].get("name")
                result["address"] = activity["place"].get("address")

            print(json.dumps(result))

        for base in h.get("bases", []):
            print(json.dumps({
                "type": "base",
                "name": base["name"],
                "online": base["online"],
                "latitude": base.get("position", {}).get("latitude"),
                "longitude": base.get("position", {}).get("longitude"),
            }))


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "location"
    if cmd == "location":
        cmd_location()
    elif cmd == "status":
        cmd_status()
    elif cmd == "login":
        s = login()
        print(json.dumps({"success": True, "userId": s["userId"]}))
    else:
        print(f"Usage: fi-api.py [location|status|login]", file=sys.stderr)
        sys.exit(1)
