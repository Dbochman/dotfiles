#!/usr/bin/env python3
"""Dog walk automation listener.

Detects dog walks via Fi GPS collar departure and manages Roomba automation.
Uses Ring doorbell motion + WiFi network presence + Fi GPS for return detection.

Runs as a persistent LaunchAgent (ai.openclaw.dog-walk-listener).
"""

import asyncio
import json
import os
import subprocess
import sys
import threading
import time
import urllib.request
import urllib.error
import uuid
from datetime import datetime, timezone
from pathlib import Path

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

def run_roomba_command(location: str, action: str) -> dict:
    now = time.time()
    cooldown_key = f"{location}_{action}"
    last = _roomba_last_action.get(cooldown_key, 0)
    if now - last < _ROOMBA_COOLDOWN:
        remaining = int((_ROOMBA_COOLDOWN - (now - last)) / 60)
        log(f"Roomba {action} for {location} on cooldown ({remaining}min remaining)")
        return {"success": False, "results": [], "skipped": "cooldown", "remaining_min": remaining}

    cmds = ROOMBA_COMMANDS.get(location, {})
    env = os.environ.copy()
    env["PATH"] = f"{OPENCLAW_BIN}:{env.get('PATH', '')}"
    results = []

    if location == "crosstown":
        cmd = cmds.get(action)
        if cmd:
            log(f"ROOMBA: {' '.join(cmd)}")
            try:
                r = subprocess.run(cmd, capture_output=True, timeout=30, env=env)
                output = r.stdout.decode()[:200]
                error = r.stderr.decode()[:200] if r.returncode != 0 else None
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
                    r = subprocess.run(cmd, capture_output=True, timeout=30, env=env)
                    output = r.stdout.decode()[:200]
                    error = r.stderr.decode()[:200] if r.returncode != 0 else None
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

    _roomba_last_action[cooldown_key] = now
    success = all(r["returncode"] == 0 for r in results) if results else False
    return {"success": success, "results": results}


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


def _detect_who_left(location: str) -> list[str]:
    """Determine who left by checking who's absent from the network."""
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
            return ["dylan", "julia"]

        if result.returncode != 0:
            return ["dylan", "julia"]

        scan = json.loads(result.stdout)
        presence = scan.get("presence", {})
        absent = []
        for person_key, info in presence.items():
            if not info.get("present"):
                absent.append(person_key.lower())
        return absent if absent else ["dylan", "julia"]
    except Exception as e:
        log(f"WHO LEFT: error detecting: {e}")
        return ["dylan", "julia"]


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
    WALK_PATH_INTERVAL = 120  # fetch Fi walk path every 2 min for dense route data
    MAX_DURATION = 7200
    MIN_WALK_FOR_WIFI = 600  # ignore WiFi returns for first 10 min (phones linger at door)
    start_time = time.time()
    last_walk_path_fetch = 0.0

    try:
        log(f"RETURN MONITOR: Starting for {location}")
        send_imessage(f"\U0001f4cd Tracking your walk at {location} — will dock Roombas when you're back")

        # Wait 2 minutes then detect who left
        await asyncio.sleep(120)
        walkers = await asyncio.to_thread(_detect_who_left, location)
        log(f"RETURN MONITOR: Walkers detected: {walkers}")
        _update_state_dog_walk(location, "walkers_detected", walkers=walkers)

        _ring_motion_during_walk = False

        while time.time() - start_time < MAX_DURATION:
            elapsed = time.time() - start_time

            # Periodically fetch Fi's dense walk path (every 2 min)
            if time.time() - last_walk_path_fetch >= WALK_PATH_INTERVAL:
                walk_path = await asyncio.to_thread(_fetch_fi_walk_path)
                if walk_path:
                    await asyncio.to_thread(_merge_walk_path_into_route, walk_path)
                last_walk_path_fetch = time.time()

            try:
                # 1. Ring motion (event-driven flag set by _handle_motion)
                if _ring_motion_during_walk:
                    _ring_motion_during_walk = False
                    elapsed_min = int(elapsed / 60)
                    log(f"RETURN MONITOR: Ring motion after {elapsed_min}min — docking at {location}")
                    # Final walk path + GPS capture before docking
                    walk_path = await asyncio.to_thread(_fetch_fi_walk_path)
                    if walk_path:
                        await asyncio.to_thread(_merge_walk_path_into_route, walk_path)
                    return_fi = await asyncio.to_thread(_check_fi_gps, location)
                    if return_fi:
                        _append_active_walk_route_point(return_fi)
                    roomba_result = run_roomba_command(location, "dock")
                    send_imessage(f"\U0001f3e0 Welcome back! Docking Roombas at {location} ({elapsed_min}min walk, signal: Ring doorbell motion)")
                    _update_state_dog_walk(location, "dock", return_signal="ring_motion", roomba_result=roomba_result)
                    _update_state_return_monitor(location, "stop")
                    return

                # 2. Network WiFi presence (skip early — phones linger at front door)
                if elapsed >= MIN_WALK_FOR_WIFI:
                    wifi_detail = await asyncio.to_thread(_check_network_presence, location)
                    _update_state_return_monitor(location, "poll", network_detail=wifi_detail)
                    if wifi_detail["any_present"]:
                        elapsed_min = int(elapsed / 60)
                        log(f"RETURN MONITOR: Network return after {elapsed_min}min — docking at {location}")
                        # Final walk path + GPS capture before docking
                        walk_path = await asyncio.to_thread(_fetch_fi_walk_path)
                        if walk_path:
                            await asyncio.to_thread(_merge_walk_path_into_route, walk_path)
                        return_fi = await asyncio.to_thread(_check_fi_gps, location)
                        if return_fi:
                            _append_active_walk_route_point(return_fi)
                        roomba_result = run_roomba_command(location, "dock")
                        send_imessage(f"\U0001f3e0 Welcome back! Docking Roombas at {location} ({elapsed_min}min walk, signal: WiFi reconnect)")
                        _update_state_dog_walk(location, "dock", return_signal="network_wifi", roomba_result=roomba_result)
                        _update_state_return_monitor(location, "stop")
                        return

                # 3. Fi GPS geofence
                fi_result = await asyncio.to_thread(_check_fi_gps, location)
                if fi_result:
                    _append_active_walk_route_point(fi_result)
                    _update_state_return_monitor(location, "poll", fi_result=fi_result)
                    if fi_result.get("at_monitored_location"):
                        elapsed_min = int(elapsed / 60)
                        dist = fi_result.get("distance_to_monitored", "?")
                        log(f"RETURN MONITOR: Fi GPS shows Potato {dist}m from {location} after {elapsed_min}min — docking")
                        roomba_result = run_roomba_command(location, "dock")
                        send_imessage(f"\U0001f3e0 Welcome back! Docking Roombas at {location} ({elapsed_min}min walk, signal: Fi GPS — Potato {dist}m from home)")
                        _update_state_dog_walk(
                            location,
                            "dock",
                            return_signal="fi_gps",
                            roomba_result=roomba_result,
                            fi_result=fi_result,
                        )
                        _update_state_return_monitor(location, "stop")
                        return
                    else:
                        dist = fi_result.get("distance_to_monitored", "?")
                        log(f"RETURN MONITOR: Fi GPS — Potato {dist}m from {location} (outside geofence)")

            except asyncio.CancelledError:
                log("RETURN MONITOR: Cancelled")
                return
            except Exception as e:
                log(f"RETURN MONITOR: Error: {e}")

            await asyncio.sleep(POLL_INTERVAL)

        log(f"RETURN MONITOR: Timeout after {MAX_DURATION // 60}min — docking as safety fallback")
        send_imessage(f"\u23f0 Walk tracking timed out after 2 hours — docking Roombas at {location}.")
        roomba_result = run_roomba_command(location, "dock")
        _update_state_dog_walk(location, "dock_timeout", return_signal="timeout", roomba_result=roomba_result)
        _update_state_return_monitor(location, "stop")
    finally:
        _return_monitor_active = False
        _ring_motion_during_walk = False
        log("RETURN MONITOR: Ended — cleared _return_monitor_active flag")


def start_return_monitor(location: str) -> None:
    global _return_poll_task, _return_monitor_active
    if _return_poll_task and not _return_poll_task.done():
        _return_poll_task.cancel()
    _return_monitor_active = True
    _update_state_return_monitor(location, "start")
    _return_poll_task = asyncio.get_event_loop().create_task(_return_poll_loop(location))


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

    while True:
        try:
            # Use faster polling after base station disconnect
            poll_interval = FI_FAST_POLL_INTERVAL if last_connection != "Base" and last_outside_reading else FI_POLL_INTERVAL
            await asyncio.sleep(poll_interval)

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
            if last_connection == "Base" and connection != "Base" and connection:
                log(f"FI DEPARTURE: Base station disconnect detected (connection: {last_connection} → {connection})")
            last_connection = connection or last_connection

            fi_location = fi_result.get("location")
            fi_at_location = bool(fi_result.get("at_location")) and fi_location in _FI_LOCATIONS

            if fi_at_location:
                if fi_location != home_anchor:
                    home_anchor = fi_location
                    _update_state_home_anchor(fi_location, distance_m=fi_result.get("distance_m"))
                reset_candidate("inside_geofence", fi_result.get("distance_m"))
                if connection == "Base":
                    last_connection = "Base"  # reset on confirmed at-home
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

            send_imessage(
                f"\U0001f9f9 Potato left {candidate_location} (GPS: {dist}m away) — starting Roombas!"
            )
            roomba_result = run_roomba_command(candidate_location, "start")
            _update_state_dog_walk(
                candidate_location,
                "departure",
                people=0,
                dogs=1,
                roomba_result=roomba_result,
                fi_result=fi_result,
            )
            # Seed the route with departure GPS point
            _append_active_walk_route_point(fi_result)
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
                _update_state_dog_walk(
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
    now = time.time()

    expired = [eid for eid, ts in _recent_events.items() if now - ts > _DEDUP_WINDOW]
    for eid in expired:
        del _recent_events[eid]

    if event.is_update:
        return
    if event.id in _recent_events:
        return
    _recent_events[event.id] = now

    kind = event.kind
    device = event.device_name
    doorbot_id = event.doorbot_id

    log(f"Event: kind={kind} device={device} doorbot_id={doorbot_id} state={event.state}")

    if kind == "ding":
        loop = asyncio.get_event_loop()
        loop.create_task(_handle_ding(device, doorbot_id, event.id))

    elif kind == "motion":
        loop = asyncio.get_event_loop()
        loop.create_task(_handle_motion(device, doorbot_id, event.id, state=event.state or ""))


async def _handle_ding(device: str, doorbot_id: int, event_id: int) -> None:
    msg = f"\U0001f514 {device}: Doorbell rang!"
    log(f"NOTIFY: {msg}")
    send_imessage(msg)


async def _handle_motion(device: str, doorbot_id: int, event_id: int, state: str = "") -> None:
    """Handle motion — only used as a return signal during active walk monitoring."""
    try:
        person_detected = state.lower() == "human"

        if person_detected and _return_monitor_active:
            global _ring_motion_during_walk
            _ring_motion_during_walk = True
            log("RING MOTION during walk monitoring — signaling return")

    except Exception as e:
        log(f"ERROR handling motion: {e}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

_ring: Ring | None = None


async def main() -> None:
    global _ring

    log("Dog walk listener starting...")

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
    asyncio.get_event_loop().create_task(_inbox_poll_loop())
    asyncio.get_event_loop().create_task(_fi_departure_poll_loop())

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
