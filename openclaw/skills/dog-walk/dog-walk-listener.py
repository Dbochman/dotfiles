#!/usr/bin/env python3
"""Dog walk automation listener.

Detects dog walks via Fi GPS collar departure and manages Roomba automation.
Uses Ring doorbell motion + WiFi network presence + Fi GPS for return detection.

Runs as a persistent LaunchAgent (ai.openclaw.dog-walk-listener).
"""

import asyncio
import faulthandler
import json
import os
import signal
import subprocess
import sys
import threading
import time
import urllib.request
import urllib.error
import uuid
from datetime import datetime, timezone
from pathlib import Path

# Enable faulthandler for SIGUSR1 — dumps all thread tracebacks to stderr
faulthandler.enable()
faulthandler.register(signal.SIGUSR1, all_threads=True)

# Use venv packages (Ring doorbell library for FCM push events)
VENV_SITE = Path.home() / ".openclaw/ring/venv/lib"
for p in VENV_SITE.glob("python*/site-packages"):
    sys.path.insert(0, str(p))

import aiohttp
from ring_doorbell import Auth, Ring, RingEvent, RingEventListener
from ring_doorbell.listen.listenerconfig import RingEventListenerConfig

# Config
CONFIG_DIR = Path.home() / ".config" / "ring"
TOKEN_FILE = CONFIG_DIR / "token-cache.json"
FCM_CREDS_FILE = Path.home() / ".openclaw/dog-walk/fcm-credentials.json"

BB_URL = "http://localhost:1234"
DYLAN_CHAT = "any;-;dylanbochman@gmail.com"
USER_AGENT = "OpenClaw/1.0"

# Doorbell ID → location mapping
DOORBELL_LOCATIONS = {
    684794187: "crosstown",
    697442349: "cabin",
}

# Roomba commands per location
ROOMBA_COMMANDS = {
    "crosstown": {
        "start": ["crosstown-roomba", "start", "all"],
        "dock": ["crosstown-roomba", "dock", "all"],
    },
    "cabin": {
        "start_1": ["roomba", "start", "floomba"],
        "start_2": ["roomba", "start", "philly"],
        "dock_1": ["roomba", "dock", "floomba"],
        "dock_2": ["roomba", "dock", "philly"],
    },
}

OPENCLAW_BIN = str(Path.home() / ".openclaw/bin")

# Fi GPS geofence locations — coordinates loaded from env vars at startup
_FI_LOCATIONS = {}
if os.environ.get("CROSSTOWN_LAT") and os.environ.get("CROSSTOWN_LON"):
    _FI_LOCATIONS["crosstown"] = {
        "lat": float(os.environ["CROSSTOWN_LAT"]),
        "lon": float(os.environ["CROSSTOWN_LON"]),
        "radius_m": 150,
    }
if os.environ.get("CABIN_LAT") and os.environ.get("CABIN_LON"):
    _FI_LOCATIONS["cabin"] = {
        "lat": float(os.environ["CABIN_LAT"]),
        "lon": float(os.environ["CABIN_LON"]),
        "radius_m": 300,
    }

STATE_FILE = Path.home() / ".openclaw/dog-walk/state.json"
HISTORY_DIR = Path.home() / ".openclaw/dog-walk/history"
ROUTES_DIR = Path.home() / ".openclaw/dog-walk/routes"
SNOOZE_FILE = Path.home() / ".openclaw/dog-walk/snooze.json"

# State file serialization lock
_state_lock = threading.Lock()
_SKIP_KEYS = {"skip_reason", "skip_location", "skip_details"}
_CANDIDATE_KEYS = {
    "candidate_location",
    "candidate_started_at",
    "candidate_last_seen_at",
    "candidate_first_distance_m",
    "candidate_last_distance_m",
    "candidate_source",
    "candidate_reset_reason",
}
_CANDIDATE_EVENT_TYPES = {"departure_candidate", "departure_candidate_reset"}

# Main event loop reference — set in main(), used by on_event() for thread-safe bridging
_main_loop: asyncio.AbstractEventLoop | None = None

# Dedup: track recent Ring event IDs
_recent_events: dict[int, float] = {}
_DEDUP_WINDOW = 300  # 5 minutes

# Roomba cooldown: prevent re-triggering within 2 hours per location
_roomba_last_action: dict[str, float] = {}
_ROOMBA_COOLDOWN = 7200  # 2 hours

# Return monitoring state
_return_poll_task: asyncio.Task | None = None
_return_monitor_active: bool = False
_ring_motion_during_walk: bool = False

# Departure combo trigger: Ring motion timestamps per doorbell location
# Used by Fi departure loop to fast-trigger when both Ring motion + Fi disconnect occur
_ring_departure_motion: dict[str, float] = {}  # location → monotonic timestamp
_RING_DEPARTURE_WINDOW = 300  # 5 minutes — Ring motion must be within this window of Fi disconnect

# Inbox directory for external processes (dog-walk-start) to request return monitoring.
_INBOX_DIR = Path.home() / ".openclaw/dog-walk/inbox"
_INBOX_POLL_INTERVAL = 5  # seconds
_INBOX_MAX_AGE = 7200  # 2 hours


def log(msg: str) -> None:
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{ts}] {msg}\n"
    sys.stdout.write(line)
    sys.stdout.flush()


# ---------------------------------------------------------------------------
# State management
# ---------------------------------------------------------------------------

def _read_state() -> dict:
    try:
        if STATE_FILE.exists():
            return json.loads(STATE_FILE.read_text())
    except Exception as e:
        log(f"WARNING: Failed to read state file: {e}")
    return {}


def _write_state(state: dict, event_type: str = "state_update") -> None:
    if event_type != "departure_skip":
        for key in _SKIP_KEYS:
            state.pop(key, None)
    if event_type not in _CANDIDATE_EVENT_TYPES:
        for key in _CANDIDATE_KEYS:
            state.pop(key, None)
    state["event_type"] = event_type
    state["timestamp"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)

    tmp_path = STATE_FILE.with_suffix(".tmp")
    data = json.dumps(state, indent=2)
    with open(tmp_path, "w") as f:
        f.write(data)
        f.flush()
        os.fsync(f.fileno())
    os.replace(str(tmp_path), str(STATE_FILE))

    HISTORY_DIR.mkdir(parents=True, exist_ok=True)
    history_file = HISTORY_DIR / f"{datetime.now(timezone.utc).strftime('%Y-%m-%d')}.jsonl"
    with open(history_file, "a") as f:
        f.write(json.dumps(state) + "\n")
        f.flush()


def _update_state_dog_walk(
    location: str,
    event: str,
    people: int = 0,
    dogs: int = 0,
    return_signal: str | None = None,
    roomba_result: dict | None = None,
    walkers: list[str] | None = None,
    skip_reason: str | None = None,
    skip_details: dict | None = None,
    fi_result: dict | None = None,
) -> None:
    with _state_lock:
        state = _read_state()
        now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

        walk = state.get("dog_walk") or {}
        roombas = state.get("roombas") or {}
        loc_roombas = roombas.get(location) or {}

        if event == "departure":
            walk_id = _make_walk_id(location, now)
            walk = {
                "active": True,
                "walk_id": walk_id,
                "location": location,
                "origin_location": location,
                "departed_at": now,
                "returned_at": None,
                "people": people,
                "dogs": dogs,
                "walkers": None,
                "return_signal": None,
                "walk_duration_minutes": None,
                "distance_m": 0,
                "point_count": 0,
            }
            route_summary = _init_walk_route(
                walk_id,
                origin_location=location,
                started_at=now,
                fi_result=fi_result,
            )
            walk["distance_m"] = route_summary["distance_m"]
            walk["point_count"] = route_summary["point_count"]
            loc_roombas = {
                "status": "running",
                "started_at": now,
                "docked_at": None,
                "trigger": "dog_walk_departure",
            }
        elif event in ("dock", "dock_timeout"):
            walk["active"] = False
            walk.setdefault("origin_location", walk.get("location") or location)
            walk["returned_at"] = now
            walk["return_signal"] = return_signal
            departed_at = walk.get("departed_at")
            if departed_at:
                try:
                    departed = datetime.strptime(departed_at, "%Y-%m-%dT%H:%M:%SZ")
                    returned = datetime.strptime(now, "%Y-%m-%dT%H:%M:%SZ")
                    walk["walk_duration_minutes"] = round(
                        (returned - departed).total_seconds() / 60, 1
                    )
                except ValueError:
                    pass
            route_summary = _finalize_walk_route(
                walk_id=walk.get("walk_id"),
                origin_location=walk.get("origin_location") or location,
                started_at=walk.get("departed_at"),
                ended_at=now,
                return_signal=return_signal,
                fi_result=fi_result,
            )
            if route_summary:
                walk["distance_m"] = route_summary["distance_m"]
                walk["point_count"] = route_summary["point_count"]
            loc_roombas["status"] = "docked"
            loc_roombas["docked_at"] = now
            if event == "dock_timeout":
                loc_roombas["trigger"] = "timeout_fallback"
        elif event == "walkers_detected":
            walk["walkers"] = walkers
        elif event == "departure_skip":
            state["skip_reason"] = skip_reason
            state["skip_location"] = location
            if skip_details:
                state["skip_details"] = skip_details

        if roomba_result is not None:
            loc_roombas["last_command_result"] = roomba_result

        roombas[location] = loc_roombas
        state["dog_walk"] = walk
        state["roombas"] = roombas
        _write_state(state, event_type=event)
    log(f"STATE: dog_walk event={event} location={location}")


def _update_state_return_monitor(
    location: str, event: str, fi_result: dict | None = None, network_detail: dict | None = None
) -> None:
    with _state_lock:
        state = _read_state()
        now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        monitoring = state.get("return_monitoring") or {}

        if event == "start":
            monitoring = {
                "active": True,
                "location": location,
                "started_at": now,
                "polls": 0,
                "last_poll_at": None,
                "last_fi_gps": None,
                "last_network_check": None,
            }
        elif event == "poll":
            monitoring["polls"] = monitoring.get("polls", 0) + 1
            monitoring["last_poll_at"] = now
            if fi_result:
                monitoring["last_fi_gps"] = {
                    "distance_m": fi_result.get("distance_to_monitored"),
                    "at_location": fi_result.get("at_monitored_location", False),
                    "battery": fi_result.get("battery"),
                    "activity": fi_result.get("activity"),
                    "age_s": fi_result.get("age_s"),
                }
            if network_detail:
                monitoring["last_network_check"] = network_detail
        elif event == "stop":
            monitoring["active"] = False

        state["return_monitoring"] = monitoring
        _write_state(state, event_type=f"return_{event}")


def _update_state_departure_candidate(
    location: str,
    event: str,
    first_distance_m: int | None = None,
    last_distance_m: int | None = None,
    started_at: str | None = None,
    reset_reason: str | None = None,
) -> None:
    with _state_lock:
        state = _read_state()
        now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

        state["candidate_location"] = location
        state["candidate_started_at"] = started_at or state.get("candidate_started_at") or now
        state["candidate_last_seen_at"] = now
        state["candidate_source"] = "fi_gps"

        if first_distance_m is None:
            first_distance_m = state.get("candidate_first_distance_m")
        if last_distance_m is None:
            last_distance_m = state.get("candidate_last_distance_m", first_distance_m)

        if first_distance_m is not None:
            state["candidate_first_distance_m"] = first_distance_m
        if last_distance_m is not None:
            state["candidate_last_distance_m"] = last_distance_m

        if event == "start":
            state.pop("candidate_reset_reason", None)
            event_type = "departure_candidate"
        elif event == "reset":
            state["candidate_reset_reason"] = reset_reason or "unknown"
            event_type = "departure_candidate_reset"
        else:
            raise ValueError(f"Unknown departure candidate event: {event}")

        _write_state(state, event_type=event_type)


def _emit_skip_event(location: str, reason: str, details: dict | None = None) -> None:
    _update_state_dog_walk(location, "departure_skip", skip_reason=reason, skip_details=details)


# ---------------------------------------------------------------------------
# Route persistence
# ---------------------------------------------------------------------------

def _make_walk_id(location: str, started_at: str) -> str:
    stamp = started_at.replace("-", "").replace(":", "")
    return f"{stamp}-{location}-{uuid.uuid4().hex[:8]}"


def _route_path(walk_id: str | None, origin_location: str | None, started_at: str | None) -> Path | None:
    if not walk_id or not origin_location or not started_at:
        return None
    return ROUTES_DIR / origin_location / started_at[:10] / f"{walk_id}.json"


def _write_json_file(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(f"{path.suffix}.tmp")
    with open(tmp_path, "w") as f:
        f.write(json.dumps(data, indent=2))
        f.flush()
        os.fsync(f.fileno())
    os.replace(str(tmp_path), str(path))


def _read_json_file(path: Path) -> dict | None:
    try:
        return json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return None


def _route_point_from_fi(fi_result: dict | None) -> dict | None:
    if not fi_result:
        return None
    lat = fi_result.get("latitude")
    lon = fi_result.get("longitude")
    if lat is None or lon is None:
        return None
    return {
        "ts": fi_result.get("lastReport") or fi_result.get("connectionDate")
        or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "lat": lat,
        "lon": lon,
    }


def _route_point_location(point: dict | None) -> str | None:
    if not point:
        return None
    lat = point.get("lat")
    lon = point.get("lon")
    if lat is None or lon is None:
        return None
    for location, loc in _FI_LOCATIONS.items():
        if _haversine(lat, lon, loc["lat"], loc["lon"]) <= loc["radius_m"]:
            return location
    return None


def _route_end_location(route: dict) -> str | None:
    points = route.get("points") or []
    if not points:
        return None
    return _route_point_location(points[-1])


def _coerce_distance_m(value) -> int | None:
    if value is None:
        return None
    try:
        return round(float(value))
    except (TypeError, ValueError):
        return None


def _route_distance_m(points: list[dict]) -> int:
    if len(points) < 2:
        return 0
    distance_m = 0.0
    for prev, cur in zip(points, points[1:]):
        distance_m += _haversine(prev["lat"], prev["lon"], cur["lat"], cur["lon"])
    return round(distance_m)


def _summarize_route(route: dict, fi_result: dict | None = None) -> dict:
    points = route.get("points") or []
    fi_distance_m = _coerce_distance_m((fi_result or {}).get("walkDistance_m"))
    end_location = _route_end_location(route)
    origin_location = route.get("origin_location")
    return {
        "distance_m": fi_distance_m if fi_distance_m is not None else _route_distance_m(points),
        "point_count": len(points),
        "end_location": end_location,
        "is_interhome_transit": bool(
            origin_location and end_location and origin_location != end_location
        ),
    }


def _init_walk_route(
    walk_id: str,
    origin_location: str,
    started_at: str,
    fi_result: dict | None = None,
) -> dict:
    route = {
        "walk_id": walk_id,
        "origin_location": origin_location,
        "started_at": started_at,
        "ended_at": None,
        "return_signal": None,
        "distance_m": 0,
        "point_count": 0,
        "end_location": None,
        "is_interhome_transit": False,
        "points": [],
    }
    point = _route_point_from_fi(fi_result)
    if point:
        route["points"].append(point)
    route.update(_summarize_route(route, fi_result=fi_result))

    path = _route_path(walk_id, origin_location, started_at)
    if path:
        _write_json_file(path, route)

    return {"distance_m": route["distance_m"], "point_count": route["point_count"]}


def _append_walk_route_point(
    walk_id: str | None,
    origin_location: str | None,
    started_at: str | None,
    fi_result: dict | None,
) -> dict | None:
    path = _route_path(walk_id, origin_location, started_at)
    point = _route_point_from_fi(fi_result)
    if not path or not point:
        return None

    route = _read_json_file(path)
    if route is None:
        route = {
            "walk_id": walk_id,
            "origin_location": origin_location,
            "started_at": started_at,
            "ended_at": None,
            "return_signal": None,
            "distance_m": 0,
            "point_count": 0,
            "end_location": None,
            "is_interhome_transit": False,
            "points": [],
        }

    points = route.setdefault("points", [])
    last_point = points[-1] if points else None
    is_duplicate = bool(
        last_point
        and (
            last_point.get("ts") == point["ts"]
            or (
                last_point.get("lat") == point["lat"]
                and last_point.get("lon") == point["lon"]
            )
        )
    )
    if not is_duplicate:
        points.append(point)

    route.update(_summarize_route(route, fi_result=fi_result))
    _write_json_file(path, route)
    return {"distance_m": route["distance_m"], "point_count": route["point_count"]}


def _finalize_walk_route(
    walk_id: str | None,
    origin_location: str | None,
    started_at: str | None,
    ended_at: str,
    return_signal: str | None,
    fi_result: dict | None = None,
) -> dict | None:
    path = _route_path(walk_id, origin_location, started_at)
    if not path:
        return None

    route = _read_json_file(path)
    if route is None:
        route = {
            "walk_id": walk_id,
            "origin_location": origin_location,
            "started_at": started_at,
            "ended_at": None,
            "return_signal": None,
            "distance_m": 0,
            "point_count": 0,
            "end_location": None,
            "is_interhome_transit": False,
            "points": [],
        }

    point = _route_point_from_fi(fi_result)
    if point:
        points = route.setdefault("points", [])
        last_point = points[-1] if points else None
        if not last_point or last_point.get("ts") != point["ts"]:
            points.append(point)

    route["ended_at"] = ended_at
    route["return_signal"] = return_signal
    route.update(_summarize_route(route, fi_result=fi_result))
    _write_json_file(path, route)
    return {"distance_m": route["distance_m"], "point_count": route["point_count"]}


def _append_active_walk_route_point(fi_result: dict | None) -> dict | None:
    with _state_lock:
        state = _read_state()
        walk = state.get("dog_walk") or {}
        if not walk.get("active"):
            return None
        walk_id = walk.get("walk_id")
        origin_location = walk.get("origin_location") or walk.get("location")
        started_at = walk.get("departed_at")

    return _append_walk_route_point(
        walk_id=walk_id,
        origin_location=origin_location,
        started_at=started_at,
        fi_result=fi_result,
    )


def _fetch_fi_walk_path() -> list[dict] | None:
    """Fetch the full GPS path from Fi if Potato is on an OngoingWalk.

    Returns a list of {"ts", "lat", "lon"} points, or None if not walking
    or on error. The Fi API provides a dense polyline during active walks
    that is much more detailed than our 30s polling.
    """
    try:
        env = os.environ.copy()
        env["PATH"] = f"{OPENCLAW_BIN}:{env.get('PATH', '')}"
        r = subprocess.run(
            [f"{OPENCLAW_BIN}/fi-collar", "walk-path"],
            capture_output=True, timeout=15, env=env, text=True,
        )
        if r.returncode != 0:
            return None
        data = json.loads(r.stdout.strip())
        if not data.get("walking"):
            return None
        # Prefer timestamped positions over raw path polyline
        points = data.get("positions") or []
        if points:
            log(f"FI WALK PATH: got {len(points)} timestamped positions from Fi")
            return points
        # Fall back to path polyline (no timestamps)
        path = data.get("path") or []
        if path:
            now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
            log(f"FI WALK PATH: got {len(path)} path points from Fi (no timestamps)")
            return [{"ts": now_iso, "lat": p["lat"], "lon": p["lon"]} for p in path]
        return None
    except Exception as e:
        log(f"FI WALK PATH: error: {e}")
        return None


def _fetch_fi_walk_summary() -> dict | None:
    """Fetch the most recent completed walk from Fi's activityFeed.

    Returns {"fi_start", "fi_end", "fi_distance_m", "fi_walker"} or None.
    Called after return detection to get authoritative walk timestamps/distance.
    """
    try:
        env = os.environ.copy()
        env["PATH"] = f"{OPENCLAW_BIN}:{env.get('PATH', '')}"
        # Use fi-collar to get a fresh session, then query activityFeed directly
        r = subprocess.run(
            [f"{OPENCLAW_BIN}/fi-collar", "status"],
            capture_output=True, timeout=15, env=env, text=True,
        )
        # Read session for cookie
        config_dir = Path.home() / ".config/fi-collar"
        session_file = config_dir / "session.json"
        if not session_file.exists():
            log("FI WALK SUMMARY: no session file")
            return None
        session = json.loads(session_file.read_text())
        cookie = session.get("cookie", "")

        query = """query { currentUser { userHouseholds { household { pets {
            activityFeed(limit: 3) { activities {
                __typename start end
                ... on Walk { distance presentUser { firstName } }
            } }
        } } } } }"""

        body = json.dumps({"query": query}).encode()
        req = urllib.request.Request(
            "https://api.tryfi.com/graphql", data=body, method="POST",
            headers={"Content-Type": "application/json", "Cookie": cookie},
        )
        resp = urllib.request.urlopen(req, timeout=15)
        data = json.loads(resp.read().decode())
        if "errors" in data:
            log(f"FI WALK SUMMARY: GraphQL error: {data['errors'][0].get('message', '')}")
            return None

        for house in data.get("data", {}).get("currentUser", {}).get("userHouseholds", []):
            for pet in house.get("household", {}).get("pets", []):
                for activity in pet.get("activityFeed", {}).get("activities", []):
                    if activity.get("__typename") == "Walk":
                        walker = (activity.get("presentUser") or {}).get("firstName")
                        result = {
                            "fi_start": activity["start"],
                            "fi_end": activity["end"],
                            "fi_distance_m": round(activity.get("distance", 0)),
                            "fi_walker": walker,
                        }
                        log(f"FI WALK SUMMARY: {result['fi_start']} -> {result['fi_end']} "
                            f"({result['fi_distance_m']}m, walker: {walker})")
                        return result
        log("FI WALK SUMMARY: no walks found in activityFeed")
        return None
    except Exception as e:
        log(f"FI WALK SUMMARY: error: {e}")
        return None


def _enrich_route_with_fi_summary(fi_summary: dict, *,
                                   walk_id: str | None = None,
                                   origin: str | None = None,
                                   started_at: str | None = None) -> bool:
    """Enrich a walk's route file with Fi walk summary data.

    If walk_id/origin/started_at are provided, uses those directly (for delayed
    retries after the walk state has been cleared). Otherwise reads from current
    state (original behaviour).

    Returns True if enrichment succeeded, False otherwise.
    """
    if not fi_summary:
        return False

    if not walk_id:
        with _state_lock:
            state = _read_state()
            walk = state.get("dog_walk") or {}
            if not walk.get("walk_id"):
                return False
            walk_id = walk["walk_id"]
            origin = walk.get("origin_location") or walk.get("location")
            started_at = walk.get("departed_at")

    path = _route_path(walk_id, origin, started_at)
    if not path or not path.exists():
        return False

    try:
        route = json.loads(path.read_text())

        # Already enriched by a previous attempt — skip
        if route.get("fi_walk_start"):
            log(f"FI WALK SUMMARY: route {walk_id} already enriched — skipping")
            return True

        our_start = datetime.fromisoformat(route.get("started_at", "").replace("Z", "+00:00"))
        fi_start = datetime.fromisoformat(fi_summary["fi_start"].replace("Z", "+00:00"))
        fi_end = datetime.fromisoformat(fi_summary["fi_end"].replace("Z", "+00:00"))

        # Verify the Fi walk actually corresponds to this walk:
        # Fi start should be within 15 minutes of our detected start
        gap = abs((our_start - fi_start).total_seconds())
        if gap > 900:
            log(f"FI WALK SUMMARY: skipping — Fi walk start ({fi_summary['fi_start']}) "
                f"is {gap:.0f}s from our start ({route.get('started_at')}), threshold 900s")
            return False

        route["fi_walk_start"] = fi_summary["fi_start"]
        route["fi_walk_end"] = fi_summary["fi_end"]
        route["fi_distance_m"] = fi_summary["fi_distance_m"]
        route["fi_walker"] = fi_summary.get("fi_walker")
        route["detection_latency_s"] = round((our_start - fi_start).total_seconds())

        path.write_text(json.dumps(route, indent=2))
        log(f"FI WALK SUMMARY: enriched route {walk_id} "
            f"(latency: {route['detection_latency_s']}s, fi_dist: {fi_summary['fi_distance_m']}m)")
        return True
    except Exception as e:
        log(f"FI WALK SUMMARY: error enriching route: {e}")
        return False


_FI_ENRICHMENT_RETRY_DELAYS = [300, 600, 1200]  # 5min, 10min, 20min


def _delayed_fi_enrichment_retry(walk_id: str, origin: str, started_at: str) -> None:
    """Background thread: retry Fi walk enrichment at increasing delays.

    Fi's activityFeed often hasn't finalized the current walk by the time the
    listener detects return. This retries at 5, 10, and 20 minutes to catch it
    once Fi has processed the walk.
    """
    for delay in _FI_ENRICHMENT_RETRY_DELAYS:
        time.sleep(delay)
        try:
            # Check if already enriched (previous retry or immediate attempt)
            path = _route_path(walk_id, origin, started_at)
            if path and path.exists():
                route = json.loads(path.read_text())
                if route.get("fi_walk_start"):
                    log(f"FI WALK RETRY: route {walk_id} already enriched — stopping retries")
                    return

            log(f"FI WALK RETRY: attempting enrichment for {walk_id} (delay={delay}s)")
            fi_summary = _fetch_fi_walk_summary()
            if fi_summary:
                success = _enrich_route_with_fi_summary(
                    fi_summary, walk_id=walk_id, origin=origin, started_at=started_at)
                if success:
                    log(f"FI WALK RETRY: enriched {walk_id} on retry (delay={delay}s)")
                    return
            else:
                log(f"FI WALK RETRY: no Fi walk found for {walk_id} (delay={delay}s)")
        except Exception as e:
            log(f"FI WALK RETRY: error on retry for {walk_id} (delay={delay}s): {e}")

    log(f"FI WALK RETRY: exhausted retries for {walk_id} — walk not enriched")


def _mark_route_car_trip(location: str) -> None:
    """Mark the current walk's route file as a car trip."""
    with _state_lock:
        state = _read_state()
        walk = state.get("dog_walk") or {}
        if not walk.get("walk_id"):
            return
        walk_id = walk["walk_id"]
        origin = walk.get("origin_location") or walk.get("location")
        started_at = walk.get("departed_at")

    path = _route_path(walk_id, origin, started_at)
    if not path or not path.exists():
        return

    try:
        route = json.loads(path.read_text())
        route["is_car_trip"] = True
        path.write_text(json.dumps(route, indent=2))
        log(f"CAR TRIP: marked route {walk_id} as car trip")
    except Exception as e:
        log(f"CAR TRIP: error marking route: {e}")


def _merge_walk_path_into_route(walk_path: list[dict]) -> None:
    """Merge Fi's dense walk path into the active walk's route file.

    Deduplicates against existing points by checking lat/lon proximity (<5m).
    """
    with _state_lock:
        state = _read_state()
        walk = state.get("dog_walk") or {}
        if not walk.get("active"):
            return
        walk_id = walk.get("walk_id")
        origin_location = walk.get("origin_location") or walk.get("location")
        started_at = walk.get("departed_at")

    if not walk_id or not origin_location:
        return

    route_dir = ROUTES_DIR / origin_location / (started_at or "")[:10]
    route_file = route_dir / f"{walk_id}.json"
    if not route_file.exists():
        return

    try:
        route = json.loads(route_file.read_text())
    except (json.JSONDecodeError, OSError):
        return

    existing = route.get("points") or []

    def is_duplicate(new_pt: dict) -> bool:
        for ep in existing:
            if _haversine(new_pt["lat"], new_pt["lon"], ep["lat"], ep["lon"]) < 5:
                return True
        return False

    added = 0
    for pt in walk_path:
        if not is_duplicate(pt):
            existing.append(pt)
            added += 1

    if added == 0:
        log(f"FI WALK PATH: no new points to merge (all {len(walk_path)} were duplicates)")
        return

    # Sort by timestamp
    existing.sort(key=lambda p: p.get("ts", ""))
    route["points"] = existing
    route["point_count"] = len(existing)

    # Recompute distance
    total_dist = 0
    for i in range(1, len(existing)):
        total_dist += _haversine(
            existing[i - 1]["lat"], existing[i - 1]["lon"],
            existing[i]["lat"], existing[i]["lon"],
        )
    route["distance_m"] = round(total_dist)

    route_file.write_text(json.dumps(route, indent=2))
    log(f"FI WALK PATH: merged {added} new points into route (total: {len(existing)}, distance: {round(total_dist)}m)")


# ---------------------------------------------------------------------------
# Ring auth helpers
# ---------------------------------------------------------------------------

def load_ring_token() -> dict | None:
    if TOKEN_FILE.exists():
        try:
            return json.loads(TOKEN_FILE.read_text())
        except (json.JSONDecodeError, KeyError):
            pass
    return None


def save_ring_token(token_data: dict) -> None:
    TOKEN_FILE.write_text(json.dumps(token_data))
    TOKEN_FILE.chmod(0o600)


def load_fcm_credentials() -> dict | None:
    if FCM_CREDS_FILE.exists():
        try:
            return json.loads(FCM_CREDS_FILE.read_text())
        except (json.JSONDecodeError, KeyError):
            pass
    return None


def save_fcm_credentials(creds: dict) -> None:
    FCM_CREDS_FILE.parent.mkdir(parents=True, exist_ok=True)
    FCM_CREDS_FILE.write_text(json.dumps(creds))
    FCM_CREDS_FILE.chmod(0o600)
    log("FCM credentials updated")


# ---------------------------------------------------------------------------
# iMessage via BlueBubbles
# ---------------------------------------------------------------------------

def bb_password() -> str:
    return os.environ.get("BLUEBUBBLES_PASSWORD", "")


def send_imessage(text: str) -> bool:
    pw = bb_password()
    if not pw:
        log("ERROR: BLUEBUBBLES_PASSWORD not set")
        return False
    try:
        data = json.dumps({
            "chatGuid": DYLAN_CHAT,
            "tempGuid": str(uuid.uuid4()).upper(),
            "message": text,
            "method": "private-api",
        }).encode()
        req = urllib.request.Request(
            f"{BB_URL}/api/v1/message/text?password={pw}",
            data=data,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        resp = urllib.request.urlopen(req, timeout=10)
        result = json.loads(resp.read().decode())
        return result.get("status") == 200
    except Exception as e:
        log(f"ERROR sending iMessage: {e}")
        return False


# ---------------------------------------------------------------------------
# Roomba control
# ---------------------------------------------------------------------------

def _is_snoozed(location: str) -> bool:
    """Check if Roomba automation is snoozed for this location."""
    try:
        if SNOOZE_FILE.exists():
            data = json.loads(SNOOZE_FILE.read_text())
            expires = data.get(location)
            if expires:
                exp_dt = datetime.fromisoformat(expires.replace("Z", "+00:00"))
                if exp_dt > datetime.now(timezone.utc):
                    return True
    except Exception as e:
        log(f"WARNING: Failed to read snooze file: {e}")
    return False


def run_roomba_command(location: str, action: str) -> dict:
    now = time.time()
    # Snooze check — only skip start, dock should always work
    if action != "dock" and _is_snoozed(location):
        log(f"Roomba {action} for {location} SNOOZED — skipping")
        return {"success": False, "results": [], "skipped": "snoozed"}
    # Cooldown only applies to start — dock should always work so Roombas don't run indefinitely
    if action != "dock":
        last = _roomba_last_action.get(f"{location}_{action}", 0)
        if now - last < _ROOMBA_COOLDOWN:
            remaining = int((_ROOMBA_COOLDOWN - (now - last)) / 60)
            log(f"Roomba {action} for {location} on cooldown ({remaining}min remaining)")
            return {"success": False, "results": [], "skipped": "cooldown", "remaining_min": remaining}

    cmds = ROOMBA_COMMANDS.get(location, {})
    env = os.environ.copy()
    env["PATH"] = f"{OPENCLAW_BIN}:{env.get('PATH', '')}"
    results = []

    # dock all is sequential (stop+dock per robot via SSH+MQTT) — needs ~45s for 2 robots
    cmd_timeout = 90 if action == "dock" else 30

    if location == "crosstown":
        cmd = cmds.get(action)
        if cmd:
            log(f"ROOMBA: {' '.join(cmd)}")
            try:
                r = subprocess.run(cmd, capture_output=True, timeout=cmd_timeout, env=env)
                output = r.stdout.decode()[:500]
                error = r.stderr.decode()[:500] if r.returncode != 0 else None
                log(f"ROOMBA result: {output}")
                if error:
                    log(f"ROOMBA error: {error}")
                results.append({"name": "crosstown-roomba", "command": f"{action} all",
                                "returncode": r.returncode, "output": output, "error": error})
            except Exception as e:
                log(f"ROOMBA error: {e}")
                results.append({"name": "crosstown-roomba", "command": f"{action} all",
                                "returncode": -1, "output": "", "error": str(e)})
        else:
            return {"success": False, "results": [], "skipped": "no_command"}
    elif location == "cabin":
        roomba_names = {"start_1": "floomba", "start_2": "philly",
                        "dock_1": "floomba", "dock_2": "philly"}
        for key in (f"{action}_1", f"{action}_2"):
            cmd = cmds.get(key)
            name = roomba_names.get(key, key)
            if cmd:
                log(f"ROOMBA: {' '.join(cmd)}")
                try:
                    r = subprocess.run(cmd, capture_output=True, timeout=cmd_timeout, env=env)
                    output = r.stdout.decode()[:500]
                    error = r.stderr.decode()[:500] if r.returncode != 0 else None
                    log(f"ROOMBA result: {output}")
                    if error:
                        log(f"ROOMBA error: {error}")
                    results.append({"name": name, "command": action,
                                    "returncode": r.returncode, "output": output, "error": error})
                except Exception as e:
                    log(f"ROOMBA error: {e}")
                    results.append({"name": name, "command": action,
                                    "returncode": -1, "output": "", "error": str(e)})
    else:
        return {"success": False, "results": [], "skipped": "no_command"}

    if action != "dock":
        _roomba_last_action[f"{location}_{action}"] = now
    success = all(r["returncode"] == 0 for r in results) if results else False
    return {"success": success, "results": results}


def _check_roomba_dock_status(location: str) -> dict:
    """Check if roombas at a location are actually on the dock.

    Returns {"all_docked": bool, "robots": {name: {"docked": bool, "status": str}}}
    """
    env = os.environ.copy()
    env["PATH"] = f"{OPENCLAW_BIN}:{env.get('PATH', '')}"
    robots = {}

    if location == "crosstown":
        try:
            r = subprocess.run(
                ["crosstown-roomba", "status"],
                capture_output=True, timeout=30, env=env,
            )
            output = r.stdout.decode()
            # Parse multi-robot output — each robot separated by blank line
            for block in output.split("\n\n"):
                block = block.strip()
                if not block:
                    continue
                name = block.split(":")[0].strip() if ":" in block else "unknown"
                status_line = ""
                for line in block.split("\n"):
                    if "Status:" in line:
                        status_line = line.split("Status:")[1].strip()
                        break
                docked = "on dock" in status_line.lower() or "charging" in status_line.lower()
                robots[name] = {"docked": docked, "status": status_line}
        except Exception as e:
            log(f"DOCK VERIFY: Status check failed: {e}")
            return {"all_docked": False, "robots": {}, "error": str(e)}
    elif location == "cabin":
        for name in ("floomba", "philly"):
            try:
                r = subprocess.run(
                    ["roomba", "status", name],
                    capture_output=True, timeout=30, env=env,
                )
                output = r.stdout.decode()
                docked = "docking" in output.lower() or "dock" in output.lower()
                robots[name] = {"docked": docked, "status": output.strip()[:200]}
            except Exception as e:
                robots[name] = {"docked": False, "status": f"error: {e}"}

    all_docked = bool(robots) and all(r["docked"] for r in robots.values())
    return {"all_docked": all_docked, "robots": robots}


def _verify_dock_and_retry(location: str) -> None:
    """Background task: wait 3 minutes, check if roombas actually docked, retry if not.

    Runs in a daemon thread so it doesn't block the return monitor exit.
    """
    VERIFY_DELAY = 180  # 3 minutes
    MAX_RETRIES = 2

    time.sleep(VERIFY_DELAY)

    for attempt in range(MAX_RETRIES):
        status = _check_roomba_dock_status(location)
        if status.get("all_docked"):
            log(f"DOCK VERIFY: All roombas at {location} confirmed on dock")
            with _state_lock:
                state = _read_state()
                roombas = state.get("roombas", {})
                loc_roombas = roombas.get(location, {})
                loc_roombas["dock_verified"] = True
                roombas[location] = loc_roombas
                state["roombas"] = roombas
                _write_state(state, event_type="dock_verified")
            return

        not_docked = [
            f"{name} ({info['status']})"
            for name, info in status.get("robots", {}).items()
            if not info["docked"]
        ]
        log(f"DOCK VERIFY: Attempt {attempt + 1} — not docked: {', '.join(not_docked)}")

        # Retry dock
        retry_result = run_roomba_command(location, "dock")
        log(f"DOCK VERIFY: Retry dock result: {retry_result}")

        with _state_lock:
            state = _read_state()
            roombas = state.get("roombas", {})
            loc_roombas = roombas.get(location, {})
            loc_roombas["dock_verified"] = False
            loc_roombas["dock_retry_count"] = attempt + 1
            loc_roombas["last_command_result"] = retry_result
            roombas[location] = loc_roombas
            state["roombas"] = roombas
            _write_state(state, event_type="dock_retry")

        if attempt < MAX_RETRIES - 1:
            time.sleep(VERIFY_DELAY)

    # Final check after last retry
    time.sleep(VERIFY_DELAY)
    final_status = _check_roomba_dock_status(location)
    if final_status.get("all_docked"):
        log(f"DOCK VERIFY: All roombas at {location} confirmed on dock after retries")
        with _state_lock:
            state = _read_state()
            roombas = state.get("roombas", {})
            loc_roombas = roombas.get(location, {})
            loc_roombas["dock_verified"] = True
            roombas[location] = loc_roombas
            state["roombas"] = roombas
            _write_state(state, event_type="dock_verified")
    else:
        not_docked = [
            f"{name} ({info['status']})"
            for name, info in final_status.get("robots", {}).items()
            if not info["docked"]
        ]
        log(f"DOCK VERIFY: FAILED after {MAX_RETRIES} retries — still not docked: {', '.join(not_docked)}")
        send_imessage(f"\u26a0\ufe0f Roombas at {location} didn't dock after {MAX_RETRIES} retries: {', '.join(not_docked)}")


# ---------------------------------------------------------------------------
# Network presence (return monitoring only)
# ---------------------------------------------------------------------------

def _check_network_presence(location: str) -> dict:
    """Check network presence. Returns {"any_present": bool, "people": {...}}."""
    try:
        if location == "crosstown":
            result = subprocess.run(
                ["ssh", "-o", "ConnectTimeout=5", "dylans-macbook-pro",
                 "~/.openclaw/workspace/scripts/presence-detect.sh", "crosstown"],
                capture_output=True, timeout=90, text=True,
            )
        elif location == "cabin":
            script = str(Path.home() / ".openclaw/workspace/scripts/presence-detect.sh")
            result = subprocess.run(
                [script, "cabin"],
                capture_output=True, timeout=60, text=True,
            )
        else:
            return {"any_present": False, "people": {}}

        if result.returncode != 0:
            log(f"NETWORK CHECK: {location} scan failed: {result.stderr[:200]}")
            return {"any_present": False, "people": {}}
        scan = json.loads(result.stdout)
        presence = scan.get("presence", {})
        people_detail = {}
        any_present = False
        for person, info in presence.items():
            present = info.get("present", False)
            people_detail[person] = {"present": present}
            if present:
                any_present = True
                log(f"NETWORK CHECK: {person} detected on {location} network")
        if not any_present:
            log(f"NETWORK CHECK: no one on {location} network")
        return {"any_present": any_present, "people": people_detail}
    except Exception as e:
        log(f"NETWORK CHECK: error: {e}")
        return {"any_present": False, "people": {}}


def _people_at_location(location: str) -> set[str]:
    """Read presence state to determine who was recently at this location.

    Uses the sticky presence model — once detected at a location, a person stays
    there until detected elsewhere. Returns lowercase names.
    """
    try:
        presence_file = Path.home() / ".openclaw/presence/state.json"
        if not presence_file.exists():
            return {"dylan", "julia"}
        state = json.loads(presence_file.read_text())
        people = state.get("people", {})
        at_location = set()
        for name, info in people.items():
            if info.get("location", "").lower() == location.lower():
                at_location.add(name.lower())
        return at_location if at_location else {"dylan", "julia"}
    except Exception as e:
        log(f"WHO LEFT: error reading presence state: {e}")
        return {"dylan", "julia"}


def _recently_present_on_network(location: str) -> set[str]:
    """Check the last saved scan to see who was recently on the local network.

    Reads the periodic scan file written by presence-detect.sh.  Returns
    lowercase names of people whose phone was present AND the scan is less
    than 1 hour old.  This prevents flagging someone as a "walker" when
    they left for work hours ago and simply aren't on WiFi.
    """
    RECENCY_S = 3600  # 1 hour
    scan_file = Path.home() / f".openclaw/presence/{location}-scan.json"
    try:
        if not scan_file.exists():
            return set()
        data = json.loads(scan_file.read_text())
        ts_str = data.get("timestamp", "")
        if not ts_str:
            return set()
        scan_ts = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
        age_s = (datetime.now(timezone.utc) - scan_ts).total_seconds()
        if age_s > RECENCY_S:
            log(f"WHO LEFT: last {location} scan is {int(age_s)}s old (>{RECENCY_S}s), skipping recency check")
            return set()
        presence = data.get("presence", {})
        return {name.lower() for name, info in presence.items() if info.get("present")}
    except Exception as e:
        log(f"WHO LEFT: error reading last scan for {location}: {e}")
        return set()


def _detect_who_left(location: str) -> list[str]:
    """Determine who left by checking who's absent from the network.

    Cross-references three sources:
    1. Sticky presence state → who was at this location (candidates)
    2. Fresh ARP/WiFi scan → who is currently absent from the network
    3. Last saved periodic scan → who was recently present (within 1 hour)

    A person is only counted as a walker if they are:
    - A candidate (presence state says they were at this location), AND
    - Absent from the fresh scan, AND
    - Recently present on the network (seen within the last hour)

    This prevents false walker detection when someone left for work hours
    ago — their phone is absent but they weren't recently on the network.
    """
    # Who was at this location before the walk started?
    candidates = _people_at_location(location)
    log(f"WHO LEFT: candidates at {location} (from presence state): {sorted(candidates)}")

    # Who was recently on the network? (from last periodic scan, within 1 hour)
    recently_present = _recently_present_on_network(location)
    log(f"WHO LEFT: recently present on {location} network: {sorted(recently_present)}")

    try:
        if location == "crosstown":
            result = subprocess.run(
                ["ssh", "-o", "ConnectTimeout=5", "dylans-macbook-pro",
                 "~/.openclaw/workspace/scripts/presence-detect.sh", "crosstown"],
                capture_output=True, timeout=90, text=True,
            )
        elif location == "cabin":
            script = str(Path.home() / ".openclaw/workspace/scripts/presence-detect.sh")
            result = subprocess.run([script, "cabin"], capture_output=True, timeout=60, text=True)
        else:
            return sorted(candidates)

        if result.returncode != 0:
            log(f"WHO LEFT: network scan failed (rc={result.returncode})")
            return sorted(candidates)

        scan = json.loads(result.stdout)
        presence = scan.get("presence", {})

        # People absent from the fresh scan
        absent_from_network = set()
        for person_key, info in presence.items():
            if not info.get("present"):
                absent_from_network.add(person_key.lower())

        # Walkers = absent now AND were at this location AND recently on network
        walkers = absent_from_network & candidates & recently_present
        still_home = candidates - absent_from_network
        absent_but_not_recent = (absent_from_network & candidates) - recently_present

        if still_home:
            log(f"WHO LEFT: still on network at {location}: {sorted(still_home)}")
        if absent_but_not_recent:
            log(f"WHO LEFT: absent but not recently on network (excluded): {sorted(absent_but_not_recent)}")

        # If nobody qualifies, fall back to all candidates
        return sorted(walkers) if walkers else sorted(candidates)
    except Exception as e:
        log(f"WHO LEFT: error detecting: {e}")
        return sorted(candidates)


# ---------------------------------------------------------------------------
# Fi GPS
# ---------------------------------------------------------------------------

def _haversine(lat1, lon1, lat2, lon2):
    import math
    R = 6371000
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def _set_fi_collar_mode(mode: str) -> bool:
    """Set Fi collar mode (NORMAL or LOST_DOG).

    LOST_DOG mode enables high-frequency GPS polling (~15-30s) for better
    route tracking during walks. Must be reset to NORMAL after the walk.
    """
    try:
        env = os.environ.copy()
        env["PATH"] = f"{OPENCLAW_BIN}:{env.get('PATH', '')}"
        r = subprocess.run(
            [f"{OPENCLAW_BIN}/fi-collar", "set-mode", mode],
            capture_output=True, timeout=15, env=env, text=True,
        )
        if r.returncode == 0:
            data = json.loads(r.stdout.strip())
            if data.get("success"):
                log(f"FI COLLAR: mode set to {data.get('mode', mode)}")
                return True
        log(f"FI COLLAR: failed to set mode {mode}: {r.stderr.strip() or r.stdout.strip()}")
        return False
    except Exception as e:
        log(f"FI COLLAR: error setting mode {mode}: {e}")
        return False


# ---------------------------------------------------------------------------
# Async wrappers for blocking helpers (used in async code paths)
# ---------------------------------------------------------------------------

async def _send_imessage_async(text: str) -> bool:
    return await asyncio.to_thread(send_imessage, text)


async def _run_roomba_command_async(location: str, action: str) -> dict:
    return await asyncio.to_thread(run_roomba_command, location, action)


async def _set_fi_collar_mode_async(mode: str) -> bool:
    return await asyncio.to_thread(_set_fi_collar_mode, mode)


async def _append_route_point_async(fi_result: dict | None) -> dict | None:
    return await asyncio.to_thread(_append_active_walk_route_point, fi_result)


async def _update_state_return_monitor_async(
    location: str,
    event: str,
    fi_result: dict | None = None,
    network_detail: dict | None = None,
) -> None:
    await asyncio.to_thread(
        _update_state_return_monitor, location, event, fi_result, network_detail,
    )


async def _update_state_dog_walk_async(location: str, event: str, **kwargs) -> None:
    await asyncio.to_thread(_update_state_dog_walk, location, event, **kwargs)


def _check_fi_gps(location: str | None = None) -> dict | None:
    """Check Potato's Fi GPS location.

    If location is provided, also compute distance/at-home status for that monitored
    location. The Fi CLI's own nearest-home fields are always preserved.
    """
    try:
        env = os.environ.copy()
        env["PATH"] = f"{OPENCLAW_BIN}:{env.get('PATH', '')}"
        r = subprocess.run(
            [f"{OPENCLAW_BIN}/fi-collar", "status"],
            capture_output=True, timeout=15, env=env, text=True,
        )
        lines = r.stdout.strip().split("\n")
        result = None
        for line in lines:
            try:
                parsed = json.loads(line)
                if parsed.get("name") == "Potato" or "latitude" in parsed:
                    result = parsed
                    break
            except json.JSONDecodeError:
                continue
        if not result or "latitude" not in result:
            log("FI GPS: no valid location in output")
            return None
        if result.get("error"):
            log(f"FI GPS: API error: {result.get('message', result['error'])}")
            return None
        # Detect base-station echo: when activity is Rest/OngoingRest, Fi returns
        # base station coords as pet position. If pet coords match any home location
        # within 5m and connection is not "Base", the GPS is unreliable.
        connection = result.get("connection", "")
        if connection != "Base":
            pet_lat, pet_lon = result["latitude"], result["longitude"]
            for loc_name, loc in _FI_LOCATIONS.items():
                dist_to_home = _haversine(pet_lat, pet_lon, loc["lat"], loc["lon"])
                if dist_to_home < 5:  # within 5m of base station = echo
                    log(f"FI GPS: base-station echo detected ({dist_to_home:.0f}m from {loc_name} base, connection={connection}), treating as stale")
                    return None
        # Check staleness
        last_report = result.get("lastReport") or result.get("connectionDate")
        if last_report:
            report_time = datetime.fromisoformat(last_report.replace("Z", "+00:00"))
            age_s = (datetime.now(timezone.utc) - report_time).total_seconds()
            if age_s > 600:  # > 10 minutes
                log(f"FI GPS: stale data ({int(age_s)}s old), ignoring")
                return None
            result["age_s"] = int(age_s)
        if location:
            loc = _FI_LOCATIONS.get(location)
            if loc:
                dist = _haversine(result["latitude"], result["longitude"], loc["lat"], loc["lon"])
                result["distance_to_monitored"] = round(dist)
                result["at_monitored_location"] = dist <= loc["radius_m"]
            else:
                log(f"FI GPS: no geofence configured for {location}")
                return None
        battery = result.get("battery")
        if battery is not None and battery < 10:
            log(f"FI GPS: low battery warning ({battery}%)")
        return result
    except Exception as e:
        log(f"FI GPS: error: {e}")
        return None


# ---------------------------------------------------------------------------
# Walk hours & presence
# ---------------------------------------------------------------------------

_WALK_HOURS = [(7, 12), (12, 17), (17, 21)]  # 7 AM-12 PM, 12-5 PM, 5-9 PM


def _is_walk_hour() -> bool:
    hour = datetime.now().hour
    return any(start <= hour < end for start, end in _WALK_HOURS)


def _fi_reported_at(fi_result: dict) -> datetime | None:
    """Extract a timezone-aware report timestamp from a Fi GPS result."""
    raw = fi_result.get("lastReport") or fi_result.get("connectionDate")
    if not raw:
        return None
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None


def _distance_to_location(fi_result: dict, location: str) -> int | None:
    if location not in _FI_LOCATIONS:
        return None
    lat = fi_result.get("latitude")
    lon = fi_result.get("longitude")
    if lat is None or lon is None:
        return None
    loc = _FI_LOCATIONS[location]
    return round(_haversine(lat, lon, loc["lat"], loc["lon"]))


def _get_home_anchor() -> str | None:
    try:
        state = _read_state()
        location = state.get("home_location")
        if location in _FI_LOCATIONS:
            return location
    except Exception:
        pass
    return None


def _update_state_home_anchor(location: str, distance_m: int | None = None) -> None:
    with _state_lock:
        state = _read_state()
        if state.get("home_location") == location:
            return

        state["home_location"] = location
        state["home_location_seen_at"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        state["home_location_source"] = "fi_gps"
        if distance_m is not None:
            state["home_location_distance_m"] = distance_m
        _write_state(state, event_type="state_update")

    log(f"FI HOME: Home anchor set to {location}")


# ---------------------------------------------------------------------------
# Return monitoring (Ring motion + WiFi + Fi GPS)
# ---------------------------------------------------------------------------

async def _return_poll_loop(location: str) -> None:
    """Poll Ring motion + network presence + Fi GPS to detect return home.

    Checks every 60 seconds. Safety timeout: 2 hours.
    """
    global _return_monitor_active, _ring_motion_during_walk
    POLL_INTERVAL = 30  # faster polling for better route tracking
    MAX_DURATION = 7200
    MIN_WALK_FOR_WIFI = 600  # ignore WiFi returns for first 10 min (phones linger at door)
    CAR_SPEED_MPS = 13.4  # ~30 mph threshold for car detection
    CAR_DURATION_S = 360  # 6 minutes at car speed → switch to NORMAL
    start_time = time.time()
    car_speed_since: float | None = None  # timestamp when car speed first detected
    lost_dog_active = True  # track whether we're in LOST_DOG mode
    is_car_trip = False  # set True when sustained car speed detected
    consecutive_at_home = 0  # consecutive Fi readings at home (used after car trip)
    CAR_RETURN_READINGS = 3  # require 3 consecutive at-home readings after car trip

    try:
        log(f"RETURN MONITOR: Starting for {location}")
        await _send_imessage_async(f"\U0001f4cd Tracking your walk at {location} — will dock Roombas when you're back")

        # Wait 2 minutes then detect who left
        await asyncio.sleep(120)
        walkers = await asyncio.to_thread(_detect_who_left, location)
        log(f"RETURN MONITOR: Walkers detected: {walkers}")
        await _update_state_dog_walk_async(location, "walkers_detected", walkers=walkers)

        _ring_motion_during_walk = False
        prev_gps: dict | None = None  # for speed calculation

        while time.time() - start_time < MAX_DURATION:
            elapsed = time.time() - start_time

            try:
                return_signal = None
                elapsed_min = int(elapsed / 60)

                # Gather poll data, then write state once at the end
                poll_wifi_detail = None
                poll_fi_result = None

                # 1. Ring motion (event-driven flag set by _handle_motion)
                if _ring_motion_during_walk:
                    _ring_motion_during_walk = False
                    return_signal = "ring_motion"
                    log(f"RETURN MONITOR: Ring motion after {elapsed_min}min — docking at {location}")

                # 2. Network WiFi presence (skip early — phones linger at front door)
                elif elapsed >= MIN_WALK_FOR_WIFI:
                    poll_wifi_detail = await asyncio.to_thread(_check_network_presence, location)
                    if poll_wifi_detail["any_present"]:
                        return_signal = "network_wifi"
                        log(f"RETURN MONITOR: Network return after {elapsed_min}min — docking at {location}")

                # 3. Fi GPS geofence + speed check
                if not return_signal:
                    fi_result = await asyncio.to_thread(_check_fi_gps, location)
                    if fi_result:
                        await _append_route_point_async(fi_result)
                        poll_fi_result = fi_result

                        # Car speed detection — switch to NORMAL to save battery
                        if prev_gps and lost_dog_active:
                            lat1, lon1 = prev_gps["latitude"], prev_gps["longitude"]
                            lat2, lon2 = fi_result["latitude"], fi_result["longitude"]
                            dist_between = _haversine(lat1, lon1, lat2, lon2)
                            prev_ts = _fi_reported_at(prev_gps)
                            cur_ts = _fi_reported_at(fi_result)
                            time_gap = (cur_ts - prev_ts).total_seconds() if prev_ts and cur_ts else 0
                            speed_mps = dist_between / time_gap if 0 < time_gap <= 900 else 0
                            if speed_mps >= CAR_SPEED_MPS:
                                if car_speed_since is None:
                                    car_speed_since = time.time()
                                    log(f"RETURN MONITOR: Car speed detected ({speed_mps:.1f} m/s = {speed_mps * 2.237:.0f} mph)")
                                elif time.time() - car_speed_since >= CAR_DURATION_S:
                                    log(f"RETURN MONITOR: Car travel >6min — switching collar to NORMAL to save battery")
                                    await _set_fi_collar_mode_async("NORMAL")
                                    lost_dog_active = False
                                    is_car_trip = True
                            else:
                                car_speed_since = None

                        prev_gps = fi_result

                        if fi_result.get("at_monitored_location"):
                            consecutive_at_home += 1
                            dist = fi_result.get("distance_to_monitored", "?")
                            if is_car_trip and consecutive_at_home < CAR_RETURN_READINGS:
                                log(f"RETURN MONITOR: Fi GPS — Potato {dist}m from {location} "
                                    f"(at home {consecutive_at_home}/{CAR_RETURN_READINGS}, car trip — waiting for confirmation)")
                            else:
                                return_signal = "fi_gps"
                                log(f"RETURN MONITOR: Fi GPS shows Potato {dist}m from {location} after {elapsed_min}min — docking")
                        else:
                            consecutive_at_home = 0
                            dist = fi_result.get("distance_to_monitored", "?")
                            log(f"RETURN MONITOR: Fi GPS — Potato {dist}m from {location} (outside geofence)")

                # Consolidated state write — one per iteration
                if poll_wifi_detail or poll_fi_result:
                    await _update_state_return_monitor_async(
                        location, "poll",
                        fi_result=poll_fi_result,
                        network_detail=poll_wifi_detail,
                    )

                # --- Dock and finalize if any signal triggered ---
                if return_signal:
                    # Walk path + final GPS — best effort, don't block finalization
                    try:
                        walk_path = await asyncio.to_thread(_fetch_fi_walk_path)
                        if walk_path:
                            await asyncio.to_thread(_merge_walk_path_into_route, walk_path)
                        return_fi = await asyncio.to_thread(_check_fi_gps, location)
                        if return_fi:
                            await _append_route_point_async(return_fi)
                    except Exception as e:
                        log(f"RETURN MONITOR: Walk path capture failed (non-fatal): {e}")

                    # Dock Roombas — best effort
                    roomba_result = None
                    try:
                        roomba_result = await _run_roomba_command_async(location, "dock")
                    except Exception as e:
                        log(f"RETURN MONITOR: Dock command failed: {e}")
                        roomba_result = {"success": False, "results": [], "error": str(e)}

                    # Launch background verification (3min delay, retries if not docked)
                    threading.Thread(
                        target=_verify_dock_and_retry, args=(location,),
                        daemon=True, name=f"dock-verify-{location}",
                    ).start()

                    # Enrich route with Fi walk summary (authoritative start/end/distance)
                    # Capture walk details now — state will be cleared after finalization
                    with _state_lock:
                        _enrich_state = _read_state()
                    _enrich_dw = _enrich_state.get("dog_walk") or {}
                    _enrich_walk_id = _enrich_dw.get("walk_id")
                    _enrich_origin = _enrich_dw.get("origin_location") or _enrich_dw.get("location")
                    _enrich_started_at = _enrich_dw.get("departed_at")
                    fi_enriched = False
                    try:
                        fi_summary = await asyncio.to_thread(_fetch_fi_walk_summary)
                        if fi_summary:
                            fi_enriched = await asyncio.to_thread(
                                _enrich_route_with_fi_summary, fi_summary)
                    except Exception as e:
                        log(f"RETURN MONITOR: Fi walk summary failed (non-fatal): {e}")

                    # If immediate enrichment failed, retry in background at 5/10/20 min
                    if not fi_enriched and _enrich_walk_id:
                        threading.Thread(
                            target=_delayed_fi_enrichment_retry,
                            args=(_enrich_walk_id, _enrich_origin, _enrich_started_at),
                            daemon=True, name=f"fi-enrich-retry-{_enrich_walk_id}",
                        ).start()

                    # Mark car trips on the route file
                    if is_car_trip:
                        try:
                            await asyncio.to_thread(_mark_route_car_trip, location)
                        except Exception as e:
                            log(f"RETURN MONITOR: Car trip marking failed (non-fatal): {e}")

                    # Notify + finalize state — best effort
                    signal_labels = {"ring_motion": "Ring doorbell motion", "network_wifi": "WiFi reconnect", "fi_gps": f"Fi GPS"}
                    try:
                        await _send_imessage_async(f"\U0001f3e0 Welcome back! Docking Roombas at {location} ({elapsed_min}min walk, signal: {signal_labels.get(return_signal, return_signal)})")
                    except Exception as e:
                        log(f"RETURN MONITOR: iMessage failed (non-fatal): {e}")

                    try:
                        await _update_state_dog_walk_async(location, "dock", return_signal=return_signal, roomba_result=roomba_result)
                        await _update_state_return_monitor_async(location, "stop")
                    except Exception as e:
                        log(f"RETURN MONITOR: State update failed (non-fatal): {e}")

                    return  # Always exit after return detected — never loop back

            except asyncio.CancelledError:
                log("RETURN MONITOR: Cancelled")
                return
            except Exception as e:
                log(f"RETURN MONITOR: Error in poll loop: {e}")
                # If we had a return signal but finalization threw, still exit —
                # don't loop back and re-dock repeatedly
                if return_signal:
                    log(f"RETURN MONITOR: Exiting despite error (return_signal={return_signal} was set)")
                    return

            await asyncio.sleep(POLL_INTERVAL)

        log(f"RETURN MONITOR: Timeout after {MAX_DURATION // 60}min — docking as safety fallback")
        await _send_imessage_async(f"\u23f0 Walk tracking timed out after 2 hours — docking Roombas at {location}.")
        roomba_result = await _run_roomba_command_async(location, "dock")
        # Launch background verification (3min delay, retries if not docked)
        threading.Thread(
            target=_verify_dock_and_retry, args=(location,),
            daemon=True, name=f"dock-verify-{location}",
        ).start()
        await _update_state_dog_walk_async(location, "dock_timeout", return_signal="timeout", roomba_result=roomba_result)
        await _update_state_return_monitor_async(location, "stop")
    finally:
        _return_monitor_active = False
        _ring_motion_during_walk = False
        # Always restore normal GPS mode when walk ends
        await _set_fi_collar_mode_async("NORMAL")
        log("RETURN MONITOR: Ended — cleared _return_monitor_active flag, collar reset to NORMAL")


def start_return_monitor(location: str) -> None:
    global _return_poll_task, _return_monitor_active
    if _return_poll_task and not _return_poll_task.done():
        _return_poll_task.cancel()
    _return_monitor_active = True
    _update_state_return_monitor(location, "start")
    _return_poll_task = asyncio.create_task(_return_poll_loop(location))


def stop_return_monitor() -> None:
    global _return_poll_task, _return_monitor_active, _ring_motion_during_walk
    _return_monitor_active = False
    _ring_motion_during_walk = False
    if _return_poll_task and not _return_poll_task.done():
        _return_poll_task.cancel()
        _return_poll_task = None
    _update_state_return_monitor("", "stop")


# ---------------------------------------------------------------------------
# Fi GPS departure detection (primary departure mechanism)
# ---------------------------------------------------------------------------

async def _fi_departure_poll_loop() -> None:
    """Poll Fi GPS every 3 minutes to detect dog walk departures.

    Triggers departure when Potato leaves the geofence:
    - 2 consecutive readings outside geofence, >=3 min apart
    - Both readings must be < 10 min old (not stale)
    - Only during walk hours
    - Only when no walk is already active
    """
    FI_POLL_INTERVAL = 180  # 3 minutes
    FI_FAST_POLL_INTERVAL = 30  # faster polling after base disconnect
    CONFIRM_NORMAL = 180  # normal: 2 readings 3 min apart
    CONFIRM_BASE_DISCONNECT = 60  # faster: base station disconnected
    last_outside_reading = None
    last_connection = "Base"  # track base station connection transitions
    home_anchor = _get_home_anchor()

    log("FI DEPARTURE: Polling loop started (every 3 min during walk hours)")
    if home_anchor:
        log(f"FI DEPARTURE: Bootstrapped home anchor from state: {home_anchor}")

    def reset_candidate(reason: str, last_distance_m: int | None = None) -> None:
        nonlocal last_outside_reading
        if not last_outside_reading:
            return

        _update_state_departure_candidate(
            last_outside_reading["location"],
            "reset",
            first_distance_m=last_outside_reading["first_distance_m"],
            last_distance_m=(
                last_distance_m
                if last_distance_m is not None
                else last_outside_reading["first_distance_m"]
            ),
            started_at=last_outside_reading["started_at"],
            reset_reason=reason,
        )
        log(f"FI DEPARTURE: Candidate reset for {last_outside_reading['location']} ({reason})")
        last_outside_reading = None

    _poll_count = 0
    while True:
        try:
            # Use faster polling after base station disconnect OR when Ring motion was recently detected
            has_recent_ring = any(
                time.monotonic() - ts <= _RING_DEPARTURE_WINDOW
                for ts in _ring_departure_motion.values()
            )
            fast_poll = (last_connection != "Base" and last_outside_reading) or has_recent_ring
            poll_interval = FI_FAST_POLL_INTERVAL if fast_poll else FI_POLL_INTERVAL
            await asyncio.sleep(poll_interval)
            _poll_count += 1

            if not _is_walk_hour():
                reset_candidate("outside_walk_hours")
                last_connection = "Base"  # reset on walk-hour boundary
                continue

            if _return_monitor_active:
                reset_candidate("return_monitor_active")
                continue

            fi_result = await asyncio.to_thread(_check_fi_gps, None)
            if not fi_result:
                continue

            # Track base station connection transitions
            connection = fi_result.get("connection", "")
            base_just_disconnected = False
            if last_connection == "Base" and connection != "Base" and connection:
                log(f"FI DEPARTURE: Base station disconnect detected (connection: {last_connection} → {connection})")
                base_just_disconnected = True
            last_connection = connection or last_connection

            # --- Ring + Fi combo trigger ---
            # If Fi just disconnected from base AND we saw Ring motion recently,
            # skip GPS geofence confirmation and trigger departure immediately.
            if base_just_disconnected:
                combo_location = home_anchor
                if combo_location and combo_location in _ring_departure_motion:
                    ring_age = time.monotonic() - _ring_departure_motion[combo_location]
                    if ring_age <= _RING_DEPARTURE_WINDOW:
                        log(f"FI DEPARTURE: COMBO TRIGGER — Ring motion {int(ring_age)}s ago + Fi base disconnect at {combo_location}")
                        del _ring_departure_motion[combo_location]
                        reset_candidate("combo_trigger")

                        dist = _distance_to_location(fi_result, combo_location) or 0
                        now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

                        await _set_fi_collar_mode_async("LOST_DOG")
                        await _send_imessage_async(
                            f"\U0001f9f9 Potato left {combo_location} (Ring + Fi combo: base disconnected) — starting Roombas!"
                        )
                        roomba_result = await _run_roomba_command_async(combo_location, "start")
                        await _update_state_dog_walk_async(
                            combo_location,
                            "departure",
                            people=0,
                            dogs=1,
                            roomba_result=roomba_result,
                            fi_result=fi_result,
                        )
                        await _append_route_point_async(fi_result)
                        start_return_monitor(combo_location)
                        continue

            fi_location = fi_result.get("location")
            fi_at_location = bool(fi_result.get("at_location")) and fi_location in _FI_LOCATIONS

            if fi_at_location:
                if fi_location != home_anchor:
                    home_anchor = fi_location
                    _update_state_home_anchor(fi_location, distance_m=fi_result.get("distance_m"))
                reset_candidate("inside_geofence", fi_result.get("distance_m"))
                # Clear stale Ring departure motion when confirmed home
                _ring_departure_motion.pop(fi_location, None)
                if connection == "Base":
                    last_connection = "Base"  # reset on confirmed at-home
                # Log periodically so silence doesn't look like a freeze
                if _poll_count == 1 or _poll_count % 5 == 0:
                    dist = fi_result.get("distance_m", "?")
                    log(f"FI DEPARTURE: Potato home at {fi_location} ({dist}m, conn={connection}, polls={_poll_count})")
                continue

            candidate_location = home_anchor or fi_location
            if not candidate_location:
                reset_candidate("no_occupied_location")
                continue

            dist = _distance_to_location(fi_result, candidate_location)
            if dist is None:
                continue
            now = time.time()
            now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

            if last_outside_reading is None:
                last_outside_reading = {
                    "monotonic_started_at": now,
                    "started_at": now_iso,
                    "location": candidate_location,
                    "first_distance_m": dist,
                }
                _update_state_departure_candidate(
                    candidate_location,
                    "start",
                    first_distance_m=dist,
                    last_distance_m=dist,
                    started_at=now_iso,
                )
                log(f"FI DEPARTURE: Potato {dist}m from {candidate_location} (first reading, need confirmation)")
                continue

            if last_outside_reading["location"] != candidate_location:
                reset_candidate("location_changed")
                last_outside_reading = {
                    "monotonic_started_at": now,
                    "started_at": now_iso,
                    "location": candidate_location,
                    "first_distance_m": dist,
                }
                _update_state_departure_candidate(
                    candidate_location,
                    "start",
                    first_distance_m=dist,
                    last_distance_m=dist,
                    started_at=now_iso,
                )
                log(f"FI DEPARTURE: Potato {dist}m from {candidate_location} (new location, need confirmation)")
                continue

            time_since_first = now - last_outside_reading["monotonic_started_at"]
            confirm_threshold = CONFIRM_BASE_DISCONNECT if last_connection != "Base" else CONFIRM_NORMAL
            if time_since_first < confirm_threshold:
                extra = f", base disconnected — fast confirm" if confirm_threshold == CONFIRM_BASE_DISCONNECT else ""
                log(f"FI DEPARTURE: Potato {dist}m from {candidate_location} (confirming, {int(time_since_first)}s since first{extra})")
                continue

            # Confirmed departure
            log(f"FI DEPARTURE: Confirmed! Potato {dist}m from {candidate_location} "
                f"(first reading {int(time_since_first)}s ago at {last_outside_reading['first_distance_m']}m)")
            last_outside_reading = None

            # Enable high-frequency GPS for route tracking
            await _set_fi_collar_mode_async("LOST_DOG")

            await _send_imessage_async(
                f"\U0001f9f9 Potato left {candidate_location} (GPS: {dist}m away) — starting Roombas!"
            )
            roomba_result = await _run_roomba_command_async(candidate_location, "start")
            await _update_state_dog_walk_async(
                candidate_location,
                "departure",
                people=0,
                dogs=1,
                roomba_result=roomba_result,
                fi_result=fi_result,
            )
            # Seed the route with departure GPS point
            await _append_route_point_async(fi_result)
            start_return_monitor(candidate_location)

        except asyncio.CancelledError:
            log("FI DEPARTURE: Polling loop cancelled")
            return
        except Exception as e:
            log(f"FI DEPARTURE: Error: {e}")
            await asyncio.sleep(30)


# ---------------------------------------------------------------------------
# Inbox polling (dog-walk-start IPC)
# ---------------------------------------------------------------------------

async def _inbox_poll_loop() -> None:
    """Watch inbox directory for return-monitor requests from external processes."""
    _INBOX_DIR.mkdir(parents=True, exist_ok=True)
    while True:
        try:
            for fpath in _INBOX_DIR.iterdir():
                if not fpath.name.endswith(".json"):
                    continue
                try:
                    data = json.loads(fpath.read_text())
                    fpath.unlink()
                except (json.JSONDecodeError, OSError) as e:
                    log(f"INBOX: bad file {fpath.name}: {e}")
                    fpath.unlink(missing_ok=True)
                    continue

                location = data.get("location")
                requested_at = data.get("requested_at", "")
                if not location:
                    log(f"INBOX: missing location in {fpath.name}")
                    continue

                try:
                    req_dt = datetime.fromisoformat(requested_at.replace("Z", "+00:00"))
                    age = (datetime.now(req_dt.tzinfo) - req_dt).total_seconds()
                    if age > _INBOX_MAX_AGE:
                        log(f"INBOX: ignoring stale request for {location} (age={int(age)}s)")
                        continue
                except (ValueError, AttributeError):
                    pass

                if _return_monitor_active:
                    log(f"INBOX: return monitor already active, ignoring request for {location}")
                    continue

                log(f"INBOX: starting return monitor for {location}")
                await _update_state_dog_walk_async(
                    location, "departure",
                    people=0, dogs=0,
                    roomba_result={"success": True, "results": [], "source": "dog-walk-start"},
                )
                start_return_monitor(location)
        except Exception as e:
            log(f"INBOX: error: {e}")

        await asyncio.sleep(_INBOX_POLL_INTERVAL)


# ---------------------------------------------------------------------------
# Ring event handling (doorbell dings + return motion signal only)
# ---------------------------------------------------------------------------

def on_event(event: RingEvent) -> None:
    """Thin bridge from FCM callback thread to the main asyncio loop.

    Reads minimal event fields then hands off to the loop thread via
    call_soon_threadsafe. All mutable state is mutated on the loop thread only.
    """
    if event.is_update:
        return
    if _main_loop is None:
        log("WARNING: Ring event received before main loop is set — dropping")
        return
    _main_loop.call_soon_threadsafe(
        _process_ring_event_on_loop,
        event.id,
        event.kind,
        event.device_name,
        event.doorbot_id,
        event.state or "",
    )


def _process_ring_event_on_loop(
    event_id: int,
    kind: str,
    device: str,
    doorbot_id: int,
    state: str,
) -> None:
    """Process a Ring event on the asyncio loop thread.

    Owns all mutable state: _recent_events, _ring_departure_motion,
    _ring_motion_during_walk. Called via call_soon_threadsafe from on_event().
    """
    now = time.time()

    # Dedup cleanup
    expired = [eid for eid, ts in _recent_events.items() if now - ts > _DEDUP_WINDOW]
    for eid in expired:
        del _recent_events[eid]

    if event_id in _recent_events:
        return
    _recent_events[event_id] = now

    log(f"Event: kind={kind} device={device} doorbot_id={doorbot_id} state={state}")

    if kind == "ding":
        asyncio.create_task(_handle_ding(device, doorbot_id, event_id))
    elif kind == "motion":
        _handle_motion(doorbot_id, state)


async def _handle_ding(device: str, doorbot_id: int, event_id: int) -> None:
    msg = f"\U0001f514 {device}: Doorbell rang!"
    log(f"NOTIFY: {msg}")
    await _send_imessage_async(msg)


def _handle_motion(doorbot_id: int, state: str) -> None:
    """Handle motion — used for return detection AND departure combo trigger.

    Runs on the asyncio loop thread (called from _process_ring_event_on_loop),
    so access to _ring_departure_motion and _ring_motion_during_walk is safe.
    """
    global _ring_motion_during_walk
    try:
        person_detected = state.lower() == "human"

        if person_detected and _return_monitor_active:
            _ring_motion_during_walk = True
            log("RING MOTION during walk monitoring — signaling return")

        elif person_detected and not _return_monitor_active:
            # Track motion for departure combo trigger (Ring + Fi disconnect)
            location = DOORBELL_LOCATIONS.get(doorbot_id)
            if location:
                _ring_departure_motion[location] = time.monotonic()

    except Exception as e:
        log(f"ERROR handling motion: {e}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

_ring: Ring | None = None


async def main() -> None:
    global _ring, _main_loop
    _main_loop = asyncio.get_running_loop()

    log("Dog walk listener starting...")

    # Safety: reset collar to NORMAL if stuck in LOST_DOG (e.g., after crash/power outage)
    try:
        fi_result = await asyncio.to_thread(_check_fi_gps)
        if fi_result and fi_result.get("mode") == "LOST_DOG":
            log("STARTUP: Collar stuck in LOST_DOG mode — resetting to NORMAL")
            await _set_fi_collar_mode_async("NORMAL")
        elif fi_result:
            log(f"STARTUP: Collar mode OK ({fi_result.get('mode', 'unknown')})")
    except Exception as e:
        log(f"STARTUP: Could not check collar mode: {e}")

    # Auth
    token = load_ring_token()
    if not token:
        log("ERROR: No Ring token cache. Run 'ring status' first to authenticate.")
        sys.exit(1)

    auth = Auth(USER_AGENT, token=token, token_updater=save_ring_token)
    ring = Ring(auth)

    try:
        await ring.async_create_session()
    except Exception as e:
        log(f"ERROR: Ring session creation failed: {e}")
        sys.exit(1)

    await ring.async_update_data()
    _ring = ring

    devices = ring.devices()
    doorbells = list(devices.doorbots) + list(devices.authorized_doorbots)
    log(f"Monitoring {len(doorbells)} doorbell(s): {', '.join(db.name + ' (id=' + str(db.id) + ')' for db in doorbells)}")

    # FCM credentials
    fcm_creds = load_fcm_credentials()

    listener_config = RingEventListenerConfig.default_config()
    listener_config.abort_on_sequential_error_count = None

    listener = RingEventListener(ring, credentials=fcm_creds,
                                 credentials_updated_callback=save_fcm_credentials,
                                 config=listener_config)
    listener.add_notification_callback(on_event)

    started = await listener.start(timeout=30)
    if not started:
        log("ERROR: Failed to start event listener (FCM registration failed)")
        sys.exit(1)

    log("Ring event listener started (doorbell dings + return motion signal)")

    # Start background loops
    asyncio.create_task(_inbox_poll_loop())
    asyncio.create_task(_fi_departure_poll_loop())

    # Watchdog — restart listener if FCM push receiver dies
    try:
        while True:
            await asyncio.sleep(300)
            if not listener.started:
                log("WARNING: Event listener died — attempting restart...")
                try:
                    fcm_creds = load_fcm_credentials()
                    listener = RingEventListener(ring, credentials=fcm_creds,
                                                 credentials_updated_callback=save_fcm_credentials,
                                                 config=listener_config)
                    listener.add_notification_callback(on_event)
                    restarted = await listener.start(timeout=30)
                    if restarted:
                        log("Event listener restarted successfully")
                    else:
                        log("ERROR: Event listener restart failed — will retry in 5 min")
                except Exception as e:
                    log(f"ERROR: Event listener restart exception: {e} — will retry in 5 min")
            elif int(time.time()) % 3600 < 300:
                log("Heartbeat — listener still running")
    except asyncio.CancelledError:
        pass
    finally:
        await listener.stop()
        log("Dog walk listener stopped")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        log("Interrupted — shutting down")
    except Exception as e:
        log(f"FATAL: {e}")
        sys.exit(1)
