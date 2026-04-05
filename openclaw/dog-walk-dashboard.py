#!/usr/bin/env python3
"""Dog Walk & Roomba Dashboard — single-file HTTP server with embedded UI.

Serves a JSON API and Chart.js dashboard for dog walk history and Roomba operations.
Reads JSONL events from ~/.openclaw/dog-walk/history/YYYY-MM-DD.jsonl
current state from ~/.openclaw/dog-walk/state.json, and route summaries from
~/.openclaw/dog-walk/routes/<location>/<YYYY-MM-DD>/<walk_id>.json

Same architecture as nest-dashboard.py. Intended for Tailscale-only access.
"""

import json
import os
import signal
import subprocess
import sys
import threading
import time
from datetime import datetime, timedelta, timezone
from http.server import HTTPServer, BaseHTTPRequestHandler
from socketserver import ThreadingMixIn
from urllib.parse import urlparse, parse_qs

HISTORY_DIR = os.path.expanduser("~/.openclaw/dog-walk/history")
STATE_FILE = os.path.expanduser("~/.openclaw/dog-walk/state.json")
ROUTES_DIR = os.path.expanduser("~/.openclaw/dog-walk/routes")
FI_COLLAR_CMD = os.path.expanduser("~/.openclaw/bin/fi-collar")
SECRETS_FILE = os.path.expanduser("~/.openclaw/.secrets-cache")
PORT = 8552
MAX_DAYS = 365
FI_CACHE_TTL = 120  # seconds — Fi GPS updates ~every 7min at rest, cache for 2min
ROOMBA_CACHE_TTL = 300  # seconds — Roomba status changes slowly, cache for 5min
ROOMBA_SSH_TIMEOUT = 25  # seconds per robot — dorita980 has 20s internal timeout
CABIN_ROOMBA_CACHE_TTL = 600  # seconds — cloud API, cache for 10min
IROBOT_CLOUD_SCRIPT = os.path.expanduser("~/.openclaw/skills/cabin-roomba/irobot-cloud.py")
MACBOOK_HOST = "dylans-macbook-pro"
ROOMBA_CMD_SCRIPT = "$HOME/.openclaw/rest980/roomba-cmd.js"
ROOMBA_NODE = "/opt/homebrew/bin/node"
ROOMBA_ENVS = {
    "10max": {"env": "$HOME/.openclaw/rest980/env-10max", "label": "Roomba Combo 10 Max"},
    "j5": {"env": "$HOME/.openclaw/rest980/env-j5", "label": "Roomba J5 (Scoomba)"},
}

_fi_cache = {"data": None, "ts": 0, "lock": threading.Lock()}
_roomba_cache = {"data": None, "ts": 0, "lock": threading.Lock()}
_cabin_roomba_cache = {"data": None, "ts": 0, "lock": threading.Lock()}

CABIN_ROBOT_BLIDS = {
    "3D3ACA3E5298BA11AB7E84129F29D2DD": "Floomba",
    "1D867094BA92F76D455065BCDBC68CCA": "Philly",
}

HOME_META = {
    "cabin": {
        "label": "Cabin",
        "env_lat": "CABIN_LAT",
        "env_lon": "CABIN_LON",
        "radius_m": 300,
        "color": "#FF8C00",
    },
    "crosstown": {
        "label": "Crosstown",
        "env_lat": "CROSSTOWN_LAT",
        "env_lon": "CROSSTOWN_LON",
        "radius_m": 150,
        "color": "#4A90D9",
    },
}


def _load_secrets_env():
    """Load secrets into env if not already set (for Fi API auth)."""
    if os.environ.get("TRYFI_EMAIL"):
        return
    if not os.path.exists(SECRETS_FILE):
        return
    with open(SECRETS_FILE) as f:
        for line in f:
            line = line.strip()
            if "=" in line and not line.startswith("#"):
                k, v = line.split("=", 1)
                os.environ.setdefault(k, v)


def fetch_fi_status():
    """Fetch Fi collar status, with caching."""
    with _fi_cache["lock"]:
        if time.time() - _fi_cache["ts"] < FI_CACHE_TTL and _fi_cache["data"] is not None:
            return _fi_cache["data"]

    _load_secrets_env()
    try:
        result = subprocess.run(
            [FI_COLLAR_CMD, "status"],
            capture_output=True, text=True, timeout=15,
        )
        if result.returncode != 0:
            return {"error": "fi-collar failed", "stderr": result.stderr[:200]}

        # Parse multi-line JSON output (pet + base station)
        lines = [l.strip() for l in result.stdout.strip().split("\n") if l.strip()]
        pet = None
        base = None
        for line in lines:
            obj = json.loads(line)
            if obj.get("type") == "base":
                base = obj
            else:
                pet = obj

        data = {"pet": pet, "base": base}
        with _fi_cache["lock"]:
            _fi_cache["data"] = data
            _fi_cache["ts"] = time.time()
        return data
    except (subprocess.TimeoutExpired, OSError, json.JSONDecodeError) as e:
        return {"error": str(e)}


ROOMBA_PHASES = {
    "charge": "Charging",
    "new": "Starting",
    "run": "Cleaning",
    "pause": "Paused",
    "stop": "Stopped",
    "stuck": "Stuck!",
    "hmMidMsn": "Recharging",
    "hmUsrDock": "Returning",
    "hmPostMsn": "Docking",
    "evac": "Emptying bin",
}


def fetch_roomba_status():
    """Fetch Crosstown Roomba statuses via SSH to MBP, with caching."""
    with _roomba_cache["lock"]:
        if time.time() - _roomba_cache["ts"] < ROOMBA_CACHE_TTL and _roomba_cache["data"] is not None:
            return _roomba_cache["data"]

    robots = {}
    for name, cfg in ROOMBA_ENVS.items():
        try:
            cmd = f"PATH=/opt/homebrew/bin:$PATH {ROOMBA_NODE} {ROOMBA_CMD_SCRIPT} {cfg['env']} status"
            result = subprocess.run(
                ["ssh", "-o", "ConnectTimeout=5", MACBOOK_HOST, cmd],
                capture_output=True, text=True, timeout=ROOMBA_SSH_TIMEOUT,
            )
            if result.returncode != 0:
                robots[name] = {"label": cfg["label"], "error": result.stderr[:200]}
                continue

            raw = json.loads(result.stdout.strip())
            if "error" in raw:
                robots[name] = {"label": cfg["label"], "error": raw.get("message", "unknown")}
                continue

            ms = raw.get("cleanMissionStatus", {})
            phase = ms.get("phase", "unknown")
            entry = {
                "label": cfg["label"],
                "phase": phase,
                "status": ROOMBA_PHASES.get(phase, phase),
                "battery": raw.get("batPct"),
                "binFull": raw.get("bin", {}).get("full", False),
                "binPresent": raw.get("bin", {}).get("present", True),
                "error": ms.get("error", 0),
                "missions": ms.get("nMssn"),
            }
            tank = raw.get("tankLvl")
            if tank is not None:
                entry["tank"] = tank
            robots[name] = entry
        except (subprocess.TimeoutExpired, OSError, json.JSONDecodeError) as e:
            robots[name] = {"label": cfg["label"], "error": str(e)}

    data = {"location": "crosstown", "robots": robots}
    with _roomba_cache["lock"]:
        _roomba_cache["data"] = data
        _roomba_cache["ts"] = time.time()
    return data


def fetch_cabin_roomba_status():
    """Fetch cabin Roomba last mission via iRobot cloud API, with caching."""
    with _cabin_roomba_cache["lock"]:
        if (time.time() - _cabin_roomba_cache["ts"] < CABIN_ROOMBA_CACHE_TTL
                and _cabin_roomba_cache["data"] is not None):
            return _cabin_roomba_cache["data"]

    _load_secrets_env()
    try:
        result = subprocess.run(
            [sys.executable, IROBOT_CLOUD_SCRIPT, "status"],
            capture_output=True, text=True, timeout=30,
        )
        if result.returncode != 0:
            return {"error": "irobot-cloud failed", "stderr": result.stderr[:200]}

        robots = {}
        for line in result.stdout.strip().split("\n"):
            if not line.strip():
                continue
            obj = json.loads(line)
            name = obj.get("name", "unknown").lower()
            if obj.get("blid") in CABIN_ROBOT_BLIDS:
                robots[name] = obj
        data = {"location": "cabin", "robots": robots}
    except (subprocess.TimeoutExpired, OSError, json.JSONDecodeError) as e:
        data = {"location": "cabin", "error": str(e)}

    with _cabin_roomba_cache["lock"]:
        _cabin_roomba_cache["data"] = data
        _cabin_roomba_cache["ts"] = time.time()
    return data


def _parse_iso8601(ts_str):
    if not ts_str:
        return None
    try:
        return datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return None


def _normalize_location(location, allow_all=True):
    location = (location or "all").lower()
    if location == "both":
        location = "all"
    if allow_all and location == "all":
        return location
    if location in HOME_META:
        return location
    return "all" if allow_all else None


def _load_home_config():
    _load_secrets_env()
    homes = {}
    for location, meta in HOME_META.items():
        lat = os.environ.get(meta["env_lat"])
        lon = os.environ.get(meta["env_lon"])
        homes[location] = {
            "location": location,
            "label": meta["label"],
            "radius_m": meta["radius_m"],
            "color": meta["color"],
            "lat": float(lat) if lat else None,
            "lon": float(lon) if lon else None,
            "configured": bool(lat and lon),
        }
    return homes


def _iter_route_files():
    if not os.path.isdir(ROUTES_DIR):
        return
    for root, _, files in os.walk(ROUTES_DIR):
        for name in files:
            if name.endswith(".json"):
                yield os.path.join(root, name)


def _load_route_file(path):
    try:
        with open(path) as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return None


def _route_summary(route):
    return {
        "walk_id": route.get("walk_id"),
        "origin_location": route.get("origin_location"),
        "started_at": route.get("started_at"),
        "ended_at": route.get("ended_at"),
        "end_location": route.get("end_location"),
        "return_signal": route.get("return_signal"),
        "distance_m": route.get("distance_m", 0),
        "point_count": route.get("point_count", len(route.get("points") or [])),
        "active": route.get("ended_at") is None,
    }


def _route_matches(route, cutoff=None, allowed_locations=None):
    origin_location = route.get("origin_location")
    if allowed_locations is not None and origin_location not in allowed_locations:
        return False
    if route.get("is_interhome_transit"):
        return False
    started_dt = _parse_iso8601(route.get("started_at"))
    if started_dt is None:
        return False
    if cutoff is not None and started_dt < cutoff:
        return False
    return True


def load_events(days):
    """Load events from JSONL files covering the requested time range."""
    days = min(max(1, days), MAX_DAYS)
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(days=days)
    records = []

    for i in range(days + 1):
        day = (now - timedelta(days=i)).strftime("%Y-%m-%d")
        path = os.path.join(HISTORY_DIR, f"{day}.jsonl")
        if not os.path.exists(path):
            continue
        try:
            with open(path) as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        rec = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    ts_str = rec.get("timestamp", "")
                    try:
                        ts = _parse_iso8601(ts_str)
                    except (ValueError, TypeError):
                        ts = None
                    if ts is None:
                        continue
                    if ts >= cutoff:
                        records.append(rec)
        except OSError:
            continue

    records.sort(key=lambda r: r.get("timestamp", ""))
    return records, days


def load_current_state():
    """Load current state from state.json."""
    try:
        with open(STATE_FILE) as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return None


def load_route_summaries(days, location="all"):
    """Load per-walk route summaries from route files."""
    days = min(max(1, days), MAX_DAYS)
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(days=days)
    location = _normalize_location(location, allow_all=True)
    allowed_locations = None if location == "all" else {location}
    summaries = []

    for path in _iter_route_files() or []:
        route = _load_route_file(path)
        if route is None or not _route_matches(route, cutoff=cutoff, allowed_locations=allowed_locations):
            continue
        summaries.append(_route_summary(route))

    summaries.sort(key=lambda r: r.get("started_at") or "", reverse=True)
    return summaries, days, location


def load_route_detail(walk_id):
    if not walk_id:
        return None
    for path in _iter_route_files() or []:
        if os.path.basename(path) != f"{walk_id}.json":
            continue
        route = _load_route_file(path)
        if route is None or route.get("is_interhome_transit"):
            return None
        return route
    return None


def load_heatmap_points(days, location):
    days = min(max(1, days), MAX_DAYS)
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(days=days)
    location = _normalize_location(location, allow_all=False)
    if location is None:
        return [], days, None

    points = []
    walk_count = 0
    for path in _iter_route_files() or []:
        route = _load_route_file(path)
        if route is None or not _route_matches(route, cutoff=cutoff, allowed_locations={location}):
            continue
        route_points = route.get("points") or []
        if not route_points:
            continue
        walk_count += 1
        for point in route_points:
            lat = point.get("lat")
            lon = point.get("lon")
            if lat is None or lon is None:
                continue
            points.append([lat, lon, 0.35])

    return points, days, {
        "location": location,
        "walk_count": walk_count,
        "point_count": len(points),
    }


class DashboardHandler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        sys.stderr.write(f"{self.address_string()} {args[0]}\n")

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/") or "/"
        qs = parse_qs(parsed.query)

        if path == "/":
            self._serve_html()
        elif path == "/api/homes":
            self._serve_homes()
        elif path == "/api/events":
            days = 30
            try:
                days = int(qs.get("days", ["30"])[0])
            except (ValueError, IndexError):
                pass
            self._serve_events(days)
        elif path == "/api/current":
            self._serve_current()
        elif path == "/api/routes":
            days = 30
            try:
                days = int(qs.get("days", ["30"])[0])
            except (ValueError, IndexError):
                pass
            location = qs.get("location", ["all"])[0]
            self._serve_routes(days, location)
        elif path == "/api/route":
            walk_id = qs.get("id", [""])[0]
            self._serve_route(walk_id)
        elif path == "/api/heatmap":
            days = 30
            try:
                days = int(qs.get("days", ["30"])[0])
            except (ValueError, IndexError):
                pass
            location = qs.get("location", ["all"])[0]
            self._serve_heatmap(days, location)
        elif path == "/api/fi":
            self._serve_fi()
        elif path == "/api/roombas":
            self._serve_roombas()
        elif path == "/api/cabin-roombas":
            self._serve_cabin_roombas()
        else:
            self._respond(404, {"error": "not found"})

    def _respond(self, code, data):
        body = json.dumps(data).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def _serve_events(self, days):
        records, clamped_days = load_events(days)
        self._respond(200, {
            "meta": {"days": clamped_days, "count": len(records)},
            "events": records,
        })

    def _serve_current(self):
        state = load_current_state()
        if state:
            self._respond(200, state)
        else:
            self._respond(200, {"error": "no state data"})

    def _serve_homes(self):
        homes = _load_home_config()
        self._respond(200, {"homes": homes})

    def _serve_routes(self, days, location):
        routes, clamped_days, normalized_location = load_route_summaries(days, location)
        total_distance_m = sum(route.get("distance_m", 0) or 0 for route in routes)
        self._respond(200, {
            "meta": {
                "days": clamped_days,
                "location": normalized_location,
                "count": len(routes),
                "total_distance_m": total_distance_m,
            },
            "routes": routes,
        })

    def _serve_route(self, walk_id):
        if not walk_id:
            self._respond(400, {"error": "missing route id"})
            return
        route = load_route_detail(walk_id)
        if route is None:
            self._respond(404, {"error": "route not found"})
            return
        self._respond(200, route)

    def _serve_heatmap(self, days, location):
        points, clamped_days, meta = load_heatmap_points(days, location)
        if meta is None:
            self._respond(400, {"error": "location must be cabin or crosstown"})
            return
        self._respond(200, {
            "meta": {
                "days": clamped_days,
                "location": meta["location"],
                "walk_count": meta["walk_count"],
                "point_count": meta["point_count"],
            },
            "points": points,
        })

    def _serve_fi(self):
        data = fetch_fi_status()
        self._respond(200, data)

    def _serve_roombas(self):
        data = fetch_roomba_status()
        self._respond(200, data)

    def _serve_cabin_roombas(self):
        data = fetch_cabin_roomba_status()
        self._respond(200, data)

    def _serve_html(self):
        body = DASHBOARD_HTML.encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


class ThreadedHTTPServer(ThreadingMixIn, HTTPServer):
    daemon_threads = True


def run():
    server = ThreadedHTTPServer(("0.0.0.0", PORT), DashboardHandler)
    print(f"Dog Walk Dashboard running on http://0.0.0.0:{PORT}", flush=True)
    print(f"  Data dir: {HISTORY_DIR}", flush=True)
    print(f"  Access via Tailscale IP or localhost", flush=True)

    def shutdown(signum, frame):
        print(f"\nShutting down (signal {signum})...")
        server.shutdown()

    signal.signal(signal.SIGTERM, shutdown)
    signal.signal(signal.SIGINT, shutdown)

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        print("Server stopped.")


# ---------------------------------------------------------------------------
# Embedded HTML Dashboard
# ---------------------------------------------------------------------------

DASHBOARD_HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Dog Walk Dashboard</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4"></script>
<script src="https://cdn.jsdelivr.net/npm/luxon@3"></script>
<script src="https://cdn.jsdelivr.net/npm/chartjs-adapter-luxon@1"></script>
<link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css">
<script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
<script src="https://unpkg.com/leaflet.heat/dist/leaflet-heat.js"></script>
<noscript><p style="color:#f87171;text-align:center;margin:2rem">JavaScript required.</p></noscript>
<style>
:root{--bg:#0f1117;--surface:#1a1d27;--border:#2a2d3a;--text:#e4e4e7;--muted:#9ca3af}
*{margin:0;padding:0;box-sizing:border-box}
body{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',system-ui,sans-serif;background:var(--bg);color:var(--text);padding:1rem;max-width:1400px;margin:0 auto}

/* Header */
.header{display:flex;align-items:center;justify-content:space-between;margin-bottom:1rem;flex-wrap:wrap;gap:0.5rem}
.header h1{font-size:1.15rem;font-weight:600}
.header-right{display:flex;align-items:center;gap:1rem}
.stale-warn{font-size:0.75rem;color:#ef4444;font-weight:500;display:none}
.last-update{font-size:0.7rem;color:var(--muted)}

/* Controls */
.controls{display:flex;gap:0.5rem;margin-bottom:1rem;flex-wrap:wrap}
.controls button{background:var(--surface);border:1px solid var(--border);color:var(--text);padding:0.35rem 0.9rem;border-radius:6px;cursor:pointer;font-size:0.78rem;transition:all 0.15s}
.controls button:hover{border-color:#3b82f6}
.controls button.active{background:#3b82f6;border-color:#3b82f6;color:#fff}

/* Stat cards */
.stats{display:grid;grid-template-columns:repeat(auto-fit,minmax(160px,1fr));gap:0.75rem;margin-bottom:1rem}
.stat{background:var(--surface);border:1px solid var(--border);border-radius:10px;padding:0.85rem}
.stat-label{font-size:0.65rem;text-transform:uppercase;letter-spacing:0.05em;color:var(--muted);margin-bottom:0.2rem}
.stat-value{font-size:1.5rem;font-weight:700}
.stat-sub{font-size:0.75rem;color:var(--muted);margin-top:0.15rem}
.stat-tag{display:inline-block;font-size:0.6rem;padding:0.1rem 0.4rem;border-radius:3px;background:rgba(255,255,255,0.08);color:var(--muted);margin-left:0.4rem;vertical-align:middle;letter-spacing:0.03em}

/* Map section */
.map-section{background:var(--surface);border:1px solid var(--border);border-radius:10px;padding:0.85rem;margin-bottom:1rem}
.section-head{display:flex;align-items:center;justify-content:space-between;gap:0.75rem;margin-bottom:0.75rem;flex-wrap:wrap}
.section-head h2{font-size:0.82rem;font-weight:600;color:var(--muted)}
.section-meta{font-size:0.72rem;color:var(--muted)}
.map-grid{display:grid;grid-template-columns:1fr;gap:0.75rem}
.map-grid.two-up{grid-template-columns:1fr 1fr}
@media(max-width:840px){.map-grid.two-up{grid-template-columns:1fr}}
.map-card{background:rgba(255,255,255,0.02);border:1px solid rgba(255,255,255,0.04);border-radius:10px;padding:0.65rem}
.map-head{display:flex;align-items:center;justify-content:space-between;gap:0.75rem;margin-bottom:0.55rem;flex-wrap:wrap}
.map-head h3{font-size:0.85rem;font-weight:600}
.map-sub{font-size:0.72rem;color:var(--muted)}
.map-canvas{height:360px;border-radius:8px;overflow:hidden;border:1px solid rgba(255,255,255,0.05)}
.map-empty{padding:1rem 0.25rem;font-size:0.78rem;color:var(--muted)}
.leaflet-container{background:#0b1220;color:#111827}
.leaflet-control-attribution{font-size:0.62rem}

/* Charts */
.charts-grid{display:grid;grid-template-columns:1fr 1fr;gap:0.75rem;margin-bottom:1rem}
@media(max-width:500px){.charts-grid{grid-template-columns:1fr}}
.chart-box{background:var(--surface);border:1px solid var(--border);border-radius:8px;padding:0.85rem;overflow:hidden}
.chart-box.full{grid-column:1/-1}
.chart-box h2{font-size:0.8rem;font-weight:600;margin-bottom:0.5rem;color:var(--muted)}
.chart-wrap{position:relative;width:100%;height:180px;max-height:180px}

/* Walk log table */
.table-section{background:var(--surface);border:1px solid var(--border);border-radius:8px;padding:0.85rem;margin-bottom:1rem}
.table-section h2{font-size:0.8rem;font-weight:600;margin-bottom:0.5rem;color:var(--muted)}
.walk-table{width:100%;border-collapse:collapse;font-size:0.78rem}
.walk-table th{text-align:left;padding:0.4rem 0.6rem;border-bottom:1px solid var(--border);color:var(--muted);font-weight:500;font-size:0.7rem;text-transform:uppercase;letter-spacing:0.04em}
.walk-table td{padding:0.4rem 0.6rem;border-bottom:1px solid rgba(255,255,255,0.03)}
.walk-table tr:last-child td{border-bottom:none}
.walk-table tbody tr{cursor:pointer;transition:background 0.12s}
.walk-table tbody tr:hover{background:rgba(59,130,246,0.08)}
.walk-table tbody tr.selected{background:rgba(59,130,246,0.16)}

/* Badges */
.badge{display:inline-block;padding:0.1rem 0.45rem;border-radius:4px;font-size:0.7rem;font-weight:500}
.badge-ok{background:rgba(34,197,94,0.15);color:#22c55e}
.badge-err{background:rgba(239,68,68,0.15);color:#ef4444}
.badge-green{background:rgba(34,197,94,0.15);color:#22c55e}
.badge-amber{background:rgba(245,158,11,0.15);color:#f59e0b}
.badge-red{background:rgba(239,68,68,0.15);color:#ef4444}
.badge-blue{background:rgba(59,130,246,0.15);color:#3b82f6}
.badge-purple{background:rgba(139,92,246,0.15);color:#8b5cf6}
.badge-teal{background:rgba(20,184,166,0.15);color:#14b8a6}
.badge-gray{background:rgba(107,114,128,0.15);color:#6b7280}

.loading{text-align:center;color:var(--muted);padding:2rem}
.error-banner{background:rgba(239,68,68,0.1);border:1px solid rgba(239,68,68,0.3);border-radius:8px;padding:0.75rem;color:#ef4444;font-size:0.8rem;margin-bottom:1rem;display:none}
</style>
</head>
<body>

<div class="header">
  <h1>Dog Walk Dashboard</h1>
  <div class="header-right">
    <span class="stale-warn" id="staleWarn">Data stale</span>
    <span class="last-update" id="lastUpdate"></span>
  </div>
</div>

<div class="error-banner" id="errorBanner"></div>

<div class="stats" id="statusCards"><div class="loading">Loading...</div></div>
<div class="stats" id="potatoCard"></div>
<div class="stats" id="roombaCards"></div>
<div class="stats" id="cabinRoombaCards"></div>

<div class="controls" id="locationControls">
  <button data-location="all" class="active">Both</button>
  <button data-location="cabin">Cabin</button>
  <button data-location="crosstown">Crosstown</button>
</div>
<div class="controls" id="timeControls">
  <button data-days="7" class="active">7d</button>
  <button data-days="30">30d</button>
  <button data-days="90">90d</button>
  <button data-days="365">1Y</button>
</div>
<div class="controls" id="layerControls">
  <button data-layer="routes" class="active">Routes</button>
  <button data-layer="heatmap">Heatmap</button>
</div>

<div class="stats" id="routeCards"></div>

<div class="map-section">
  <div class="section-head">
    <h2>Walk Map</h2>
    <span class="section-meta" id="mapMeta"></span>
  </div>
  <div class="map-grid" id="mapGrid"><div class="loading">Loading route data...</div></div>
</div>

<div class="table-section"><h2>Recent Walks</h2><table class="walk-table"><thead><tr><th>Date</th><th>Location</th><th>Distance</th><th>Duration</th><th>Return</th><th>Walkers</th></tr></thead><tbody id="walkBody"></tbody></table></div>

<div class="charts-grid">
  <div class="chart-box" id="durationBox"><h2>Walk Duration (minutes)</h2><div class="chart-wrap"><canvas id="durationChart"></canvas></div></div>
  <div class="chart-box" id="walksPerDayBox"><h2>Walks per Day</h2><div class="chart-wrap"><canvas id="walksPerDayChart"></canvas></div></div>
  <div class="chart-box" id="signalBox"><h2>Return Signal Distribution</h2><div class="chart-wrap"><canvas id="signalChart"></canvas></div></div>
  <div class="chart-box" id="funnelBox"><h2>Departure Pipeline</h2><div class="chart-wrap"><canvas id="funnelChart"></canvas></div></div>
</div>

<script>
const C = { green:'#22c55e', amber:'#f59e0b', red:'#ef4444', blue:'#3b82f6', purple:'#8b5cf6', teal:'#14b8a6', cyan:'#06b6d4', orange:'#f97316', pink:'#ec4899', muted:'#9ca3af', grid:'rgba(255,255,255,0.05)' };
const SIGNAL_COLORS = { 'network_wifi':C.green, 'ring_motion':C.blue, 'findmy':C.purple, 'fi_gps':C.teal, 'timeout':C.red };
const SIGNAL_LABELS = { 'network_wifi':'WiFi', 'ring_motion':'Ring Motion', 'findmy':'FindMy', 'fi_gps':'Fi GPS', 'timeout':'Timeout' };
const LOCATION_COLORS = { 'cabin':'#FF8C00', 'crosstown':'#4A90D9' };

let charts = {};
let maps = {};
let currentDays = 7;
let currentLocation = 'all';
let currentLayer = 'routes';
let currentEvents = [];
let currentRoutes = [];
let currentHomes = {};
let currentSelectedWalkId = null;
let mapRenderToken = 0;
const routeDetailCache = new Map();

// ── Helpers ──

function fmtTime(iso) {
  if (!iso) return '-';
  const d = new Date(iso);
  return d.toLocaleDateString('en-US', { month:'short', day:'numeric' }) + ' ' +
         d.toLocaleTimeString('en-US', { hour:'numeric', minute:'2-digit' });
}

function fmtDuration(mins) {
  if (mins == null) return '-';
  if (mins < 60) return Math.round(mins) + 'm';
  const h = Math.floor(mins / 60);
  const m = Math.round(mins % 60);
  return h + 'h ' + m + 'm';
}

function fmtDistance(meters) {
  if (meters == null) return '-';
  if (meters < 160) return Math.round(meters) + 'm';
  return (meters / 1609.344).toFixed(1) + ' mi';
}

function homeLabel(location) {
  return (currentHomes[location] && currentHomes[location].label) || (location ? location.charAt(0).toUpperCase() + location.slice(1) : '?');
}

function getVisibleLocations() {
  return currentLocation === 'all' ? ['cabin', 'crosstown'] : [currentLocation];
}

function filterByLocation(events) {
  if (currentLocation === 'all') return events;
  return events.filter(e => {
    const loc = (e.dog_walk && e.dog_walk.location) || e.candidate_location || e.skip_location || e.location || '';
    return loc === currentLocation;
  });
}

function isManualDeparture(event) {
  if (!event || event.event_type !== 'departure') return false;
  const loc = event.dog_walk && event.dog_walk.location;
  const result = loc && event.roombas && event.roombas[loc] && event.roombas[loc].last_command_result;
  return !!(result && result.source === 'dog-walk-start');
}

// ── Staleness ──

function renderStaleness(state) {
  const el = document.getElementById('lastUpdate');
  const warn = document.getElementById('staleWarn');
  el.textContent = 'Updated ' + new Date().toLocaleTimeString();
  // Check if state data is stale
  if (state && state.last_updated) {
    const ago = (Date.now() - new Date(state.last_updated).getTime()) / 60000;
    if (ago > 30) { warn.textContent = Math.round(ago) + 'm stale'; warn.style.display = 'inline'; return; }
  }
  warn.style.display = 'none';
}

// ── Status cards ──

function renderStatusCards(state) {
  const el = document.getElementById('statusCards');
  if (!state || state.error) { el.innerHTML = '<div class="stat"><div class="stat-label">Status</div><div class="stat-value" style="color:' + C.muted + '">No data</div></div>'; return; }

  let html = '';
  const walk = state.dog_walk || {};
  const isActive = walk.active;

  html += '<div class="stat"><div class="stat-label">Current Walk</div>';
  if (isActive) {
    const elapsed = walk.departed_at ? Math.round((Date.now() - new Date(walk.departed_at).getTime()) / 60000) : 0;
    html += '<div class="stat-value" style="color:' + C.green + '">' + elapsed + 'm</div>';
    html += '<div class="stat-sub">Active at ' + (walk.location || '?') + '</div>';
    if (walk.distance_m != null) html += '<div class="stat-sub">Distance: ' + fmtDistance(walk.distance_m) + '</div>';
    if (walk.walkers && walk.walkers.length) html += '<div class="stat-sub">Walkers: ' + walk.walkers.join(', ') + '</div>';
  } else {
    html += '<div class="stat-value" style="color:' + C.muted + '">None</div>';
    if (walk.returned_at) html += '<div class="stat-sub">Last: ' + fmtTime(walk.returned_at) + '</div>';
  }
  html += '</div>';

  if (walk.walk_duration_minutes != null && !isActive) {
    html += '<div class="stat"><div class="stat-label">Last Walk</div>';
    html += '<div class="stat-value" style="color:' + C.blue + '">' + fmtDuration(walk.walk_duration_minutes) + '</div>';
    html += '<div class="stat-sub">' + (walk.location || '') + '<span class="stat-tag">' + (SIGNAL_LABELS[walk.return_signal] || walk.return_signal || '?') + '</span></div>';
    if (walk.distance_m != null) html += '<div class="stat-sub">' + fmtDistance(walk.distance_m) + ' tracked</div>';
    html += '</div>';
  }

  if (!isActive && state.event_type === 'departure_candidate' && state.candidate_location) {
    const ageMin = state.candidate_started_at ? Math.max(0, Math.round((Date.now() - new Date(state.candidate_started_at).getTime()) / 60000)) : null;
    const dist = state.candidate_last_distance_m != null ? state.candidate_last_distance_m + 'm away' : 'outside geofence';
    html += '<div class="stat"><div class="stat-label">Departure Candidate</div>';
    html += '<div class="stat-value" style="color:' + C.amber + '">' + (ageMin != null ? ageMin + 'm' : 'Pending') + '</div>';
    html += '<div class="stat-sub">' + state.candidate_location + ' · ' + dist + '</div>';
    html += '<div class="stat-sub">Waiting for second Fi reading</div>';
    html += '</div>';
  }

  const rm = state.return_monitoring || state.findmy_polling || {};
  if (rm.active) {
    html += '<div class="stat"><div class="stat-label">Return Monitor</div>';
    html += '<div class="stat-value" style="color:' + C.teal + '">Active</div>';
    html += '<div class="stat-sub">Polls: ' + (rm.polls || 0) + '</div>';
    if (rm.last_fi_gps) {
      const dist = rm.last_fi_gps.distance_m;
      const atHome = rm.last_fi_gps.at_location;
      html += '<div class="stat-sub">Potato: ' + (atHome ? 'Home' : dist + 'm away') + '</div>';
    }
    html += '</div>';
  }

  el.innerHTML = html;
}

function renderPotatoCard(fi) {
  const el = document.getElementById('potatoCard');
  if (!fi || fi.error || !fi.pet) {
    el.innerHTML = '<div class="stat"><div class="stat-label">Potato (Fi)</div><div class="stat-value" style="color:' + C.muted + '">Offline</div></div>';
    return;
  }
  const p = fi.pet;
  const base = fi.base;

  const bat = p.battery;
  const batColor = bat == null ? C.muted : bat > 50 ? C.green : bat > 20 ? C.amber : C.red;
  const batText = bat != null ? bat + '%' : '?';

  const activity = p.activity || 'Unknown';
  const actColor = activity === 'Walk' ? C.green : C.muted;

  const conn = p.connection || 'Unknown';
  const connIcon = conn === 'Base' ? 'Charging' : conn === 'User' ? 'Bluetooth' : conn === 'Cellular' ? 'LTE' : conn;
  const connDetail = p.connectionDetail ? ' (' + p.connectionDetail + ')' : '';

  const dist = p.distance_m != null ? p.distance_m + 'm' : '';
  const atHome = p.at_location;
  const locColor = atHome ? C.green : C.amber;
  const loc = p.location || '?';
  const place = p.place || '';

  let ageText = '';
  if (p.connectionDate) {
    const ageMin = Math.round((Date.now() - new Date(p.connectionDate).getTime()) / 60000);
    ageText = ageMin < 1 ? 'just now' : ageMin < 60 ? ageMin + 'm ago' : Math.floor(ageMin/60) + 'h ' + (ageMin%60) + 'm ago';
  }

  let html = '';
  html += '<div class="stat"><div class="stat-label">Potato Battery</div>';
  html += '<div class="stat-value" style="color:' + batColor + '">' + batText + '</div>';
  html += '<div class="stat-sub">' + connIcon + connDetail + '</div>';
  if (ageText) html += '<div class="stat-sub">' + ageText + '</div>';
  html += '</div>';

  html += '<div class="stat"><div class="stat-label">Potato Location</div>';
  html += '<div class="stat-value" style="color:' + actColor + '">' + activity + '</div>';
  html += '<div class="stat-sub" style="color:' + locColor + '">' + (atHome ? 'Home' : dist + ' from home') + '<span class="stat-tag">' + loc + '</span></div>';
  if (place) html += '<div class="stat-sub">' + place + '</div>';
  html += '</div>';

  if (base) {
    html += '<div class="stat"><div class="stat-label">Fi Base (' + (base.name || '?') + ')</div>';
    html += '<div class="stat-value" style="color:' + (base.online ? C.green : C.red) + '">' + (base.online ? 'Online' : 'Offline') + '</div>';
    html += '</div>';
  }

  el.innerHTML = html;
}

function renderRoombaCards(roombas) {
  const el = document.getElementById('roombaCards');
  if (!roombas || roombas.error || !roombas.robots) { el.innerHTML = ''; return; }

  let html = '';
  for (const [id, r] of Object.entries(roombas.robots)) {
    if (r.error) {
      html += '<div class="stat"><div class="stat-label">' + (r.label || id) + '</div>';
      html += '<div class="stat-value" style="color:' + C.muted + '">Offline</div>';
      html += '<div class="stat-sub" style="color:' + C.red + '">' + r.error + '</div></div>';
      continue;
    }

    const status = r.status || 'Unknown';
    const phase = r.phase || '';
    const isActive = ['run', 'hmMidMsn', 'hmUsrDock', 'hmPostMsn', 'evac', 'new'].includes(phase);
    const isError = phase === 'stuck' || r.error > 0;
    const statusColor = isError ? C.red : isActive ? C.green : C.muted;

    const bat = r.battery;
    const batColor = bat == null ? C.muted : bat > 50 ? C.green : bat > 20 ? C.amber : C.red;

    html += '<div class="stat"><div class="stat-label">' + (r.label || id) + '</div>';
    html += '<div class="stat-value" style="color:' + statusColor + '">' + status + '</div>';

    let sub = '';
    if (bat != null) sub += '<span style="color:' + batColor + '">' + bat + '%</span>';
    if (r.binFull) sub += ' <span class="badge badge-amber">Bin Full</span>';
    if (!r.binPresent) sub += ' <span class="badge badge-red">No Bin</span>';
    if (sub) html += '<div class="stat-sub">' + sub + '</div>';

    if (r.tank != null) html += '<div class="stat-sub">Tank: ' + r.tank + '%</div>';
    if (r.error > 0) html += '<div class="stat-sub" style="color:' + C.red + '">Error code ' + r.error + '</div>';
    html += '</div>';
  }
  el.innerHTML = html;
}

function renderCabinRoombaCards(data) {
  const el = document.getElementById('cabinRoombaCards');
  if (!data || data.error || !data.robots || !Object.keys(data.robots).length) { el.innerHTML = ''; return; }

  const MISSION_LABELS = { 'ok':'Completed', 'stuck':'Stuck', 'cancelled':'Cancelled' };
  const MISSION_COLORS = { 'ok':C.green, 'stuck':C.red, 'cancelled':C.muted };

  let html = '';
  for (const [id, r] of Object.entries(data.robots)) {
    if (r.error) {
      html += '<div class="stat"><div class="stat-label">' + (r.name || id) + ' (Cabin)</div>';
      html += '<div class="stat-value" style="color:' + C.muted + '">Offline</div></div>';
      continue;
    }

    const mission = r.lastMission || 'unknown';
    const label = MISSION_LABELS[mission] || mission;
    const color = MISSION_COLORS[mission] || C.muted;

    html += '<div class="stat"><div class="stat-label">' + (r.name || id) + ' (Cabin)</div>';
    html += '<div class="stat-value" style="color:' + color + '">' + label + '</div>';

    let sub = '';
    if (r.durationMin != null) sub += r.durationMin + 'min';
    if (r.sqft != null) sub += (sub ? ', ' : '') + r.sqft + ' sqft';
    if (sub) html += '<div class="stat-sub">' + sub + '</div>';

    if (r.startTime) {
      const ago = Math.round((Date.now()/1000 - r.startTime) / 3600);
      html += '<div class="stat-sub">' + (ago < 24 ? ago + 'h ago' : Math.round(ago/24) + 'd ago') + '</div>';
    }
    if (r.missions != null) html += '<div class="stat-sub">' + r.missions + ' missions</div>';
    html += '</div>';
  }
  el.innerHTML = html;
}

function buildWalkRows(routes, events) {
  const byWalkId = new Map();

  for (const event of events) {
    const walk = event.dog_walk || {};
    const walkId = walk.walk_id;
    if (!walkId) continue;
    if (!byWalkId.has(walkId)) byWalkId.set(walkId, {});
    const row = byWalkId.get(walkId);
    if (event.event_type === 'departure') row.departure = event;
    if (event.event_type === 'walkers_detected') row.walkers = event;
    if (event.event_type === 'dock' || event.event_type === 'dock_timeout') row.dock = event;
  }

  return routes.map(route => {
    const joined = byWalkId.get(route.walk_id) || {};
    const departure = joined.departure;
    const dock = joined.dock;
    const duration = dock && dock.dog_walk && dock.dog_walk.walk_duration_minutes != null
      ? dock.dog_walk.walk_duration_minutes
      : (route.started_at && route.ended_at)
        ? (new Date(route.ended_at).getTime() - new Date(route.started_at).getTime()) / 60000
        : null;
    return {
      walk_id: route.walk_id,
      started_at: route.started_at,
      location: route.origin_location,
      distance_m: route.distance_m,
      point_count: route.point_count,
      active: route.active,
      duration,
      return_signal: route.return_signal,
      walkers: (dock && dock.dog_walk && dock.dog_walk.walkers) || (joined.walkers && joined.walkers.dog_walk && joined.walkers.dog_walk.walkers) || (departure && departure.dog_walk && departure.dog_walk.walkers) || [],
      manual: departure ? isManualDeparture(departure) : false,
    };
  }).sort((a, b) => (b.started_at || '').localeCompare(a.started_at || ''));
}

function normalizeSelectedWalk() {
  if (!currentRoutes.length) {
    currentSelectedWalkId = null;
    return;
  }
  if (currentSelectedWalkId && currentRoutes.some(route => route.walk_id === currentSelectedWalkId)) {
    return;
  }
  const withPoints = currentRoutes.find(route => (route.point_count || 0) > 0);
  currentSelectedWalkId = (withPoints || currentRoutes[0]).walk_id;
}

function renderRouteCards() {
  const el = document.getElementById('routeCards');
  if (!currentRoutes.length) {
    el.innerHTML = '<div class="stat"><div class="stat-label">Route Data</div><div class="stat-value" style="color:' + C.muted + '">None</div><div class="stat-sub">No tracked walks in this period</div></div>';
    return;
  }

  const totalDistance = currentRoutes.reduce((sum, route) => sum + (route.distance_m || 0), 0);
  const avgDistance = totalDistance / currentRoutes.length;
  const activeCount = currentRoutes.filter(route => route.active).length;
  const selected = currentRoutes.find(route => route.walk_id === currentSelectedWalkId);

  let html = '';
  html += '<div class="stat"><div class="stat-label">Tracked Distance</div><div class="stat-value" style="color:' + C.blue + '">' + fmtDistance(totalDistance) + '</div><div class="stat-sub">' + currentRoutes.length + ' walks</div></div>';
  html += '<div class="stat"><div class="stat-label">Average Walk</div><div class="stat-value" style="color:' + C.teal + '">' + fmtDistance(avgDistance) + '</div><div class="stat-sub">' + getVisibleLocations().map(homeLabel).join(' + ') + '</div></div>';
  html += '<div class="stat"><div class="stat-label">Maps</div><div class="stat-value" style="color:' + (currentLayer === 'heatmap' ? C.orange : C.green) + '">' + (currentLayer === 'heatmap' ? 'Heatmap' : 'Routes') + '</div><div class="stat-sub">' + (activeCount ? activeCount + ' active walk' + (activeCount === 1 ? '' : 's') : 'Historical view') + '</div></div>';
  if (selected) {
    const signal = selected.active ? 'Active' : (SIGNAL_LABELS[selected.return_signal] || selected.return_signal || 'Pending');
    html += '<div class="stat"><div class="stat-label">Selected Walk</div><div class="stat-value" style="color:' + (LOCATION_COLORS[selected.origin_location] || C.muted) + '">' + fmtDistance(selected.distance_m) + '</div><div class="stat-sub">' + homeLabel(selected.origin_location) + ' · ' + fmtTime(selected.started_at) + '</div><div class="stat-sub">' + signal + ' · ' + (selected.point_count || 0) + ' pts</div></div>';
  }
  el.innerHTML = html;
}

function renderWalkTable() {
  const walks = buildWalkRows(currentRoutes, currentEvents);
  const tbody = document.getElementById('walkBody');
  if (!walks.length) {
    tbody.innerHTML = '<tr><td colspan="6" style="color:' + C.muted + ';text-align:center;padding:1rem">No tracked walks in this period</td></tr>';
    return;
  }

  tbody.innerHTML = walks.slice(0, 50).map(w => {
    const sig = w.active ? 'active' : w.return_signal;
    const sigBadgeColor = sig === 'timeout' ? 'red' : sig === 'findmy' ? 'purple' : sig === 'ring_motion' ? 'blue' : sig === 'fi_gps' ? 'teal' : sig === 'active' ? 'amber' : 'green';
    const sigLabel = w.active ? 'Active' : (SIGNAL_LABELS[sig] || sig || '-');
    const sigBadge = sig ? '<span class="badge badge-' + sigBadgeColor + '">' + sigLabel + '</span>' : '-';
    const manual = w.manual ? ' <span class="stat-tag">manual</span>' : '';
    const duration = w.active && w.started_at ? Math.max(0, (Date.now() - new Date(w.started_at).getTime()) / 60000) : w.duration;
    return '<tr data-walk-id="' + (w.walk_id || '') + '" class="' + (w.walk_id === currentSelectedWalkId ? 'selected' : '') + '"><td>' + fmtTime(w.started_at) + '</td><td>' + homeLabel(w.location) + manual + '</td><td>' + fmtDistance(w.distance_m) + '</td><td>' + fmtDuration(duration) + '</td><td>' + sigBadge + '</td><td>' + (w.walkers.length ? w.walkers.join(', ') : '-') + '</td></tr>';
  }).join('');
}

// ── Maps ──

function destroyMaps() {
  for (const state of Object.values(maps)) {
    if (state && state.map) state.map.remove();
  }
  maps = {};
}

function setMapMeta() {
  const meta = document.getElementById('mapMeta');
  if (!currentRoutes.length) {
    meta.textContent = 'No tracked route files in the selected window';
    return;
  }
  const label = currentLayer === 'heatmap' ? 'Heatmap density' : 'Approximate Fi route overlays';
  meta.textContent = label + ' · ' + currentDays + 'd · ' + currentRoutes.length + ' walks';
}

function renderMapShell() {
  const grid = document.getElementById('mapGrid');
  const visible = getVisibleLocations();
  grid.className = 'map-grid' + (visible.length === 2 ? ' two-up' : '');
  grid.innerHTML = visible.map(location => {
    return '<div class="map-card"><div class="map-head"><h3>' + homeLabel(location) + '</h3><span class="map-sub" id="mapSub-' + location + '"></span></div><div class="map-canvas" id="map-' + location + '"></div><div class="map-empty" id="mapEmpty-' + location + '" style="display:none"></div></div>';
  }).join('');
}

function ensureMap(location) {
  if (!window.L) return null;
  if (maps[location]) return maps[location];
  const map = L.map('map-' + location, { scrollWheelZoom: false, zoomControl: true });
  L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
    maxZoom: 19,
    attribution: '&copy; OpenStreetMap'
  }).addTo(map);
  maps[location] = { map };
  return maps[location];
}

function setMapMessage(location, message) {
  const empty = document.getElementById('mapEmpty-' + location);
  if (!empty) return;
  empty.style.display = '';
  empty.textContent = message;
}

function setMapSub(location, message) {
  const sub = document.getElementById('mapSub-' + location);
  if (sub) sub.textContent = message;
}

function addHomeCircle(map, location) {
  const home = currentHomes[location];
  if (!home || !home.configured || home.lat == null || home.lon == null) return null;
  return L.circle([home.lat, home.lon], {
    radius: home.radius_m || 150,
    color: LOCATION_COLORS[location] || '#9ca3af',
    weight: 1,
    fillOpacity: 0.03,
    dashArray: '6 6'
  }).addTo(map);
}

function fitMap(map, location, points) {
  if (points && points.length > 1) {
    map.fitBounds(L.latLngBounds(points), { padding: [24, 24] });
    return;
  }
  if (points && points.length === 1) {
    map.setView(points[0], 16);
    return;
  }
  const home = currentHomes[location];
  if (home && home.configured && home.lat != null && home.lon != null) {
    map.setView([home.lat, home.lon], 15);
    return;
  }
  map.setView([42.43, -71.66], 8);
}

function fetchRouteDetail(walkId) {
  if (!walkId) return Promise.resolve(null);
  if (routeDetailCache.has(walkId)) return routeDetailCache.get(walkId);
  const promise = fetch('/api/route?id=' + encodeURIComponent(walkId))
    .then(resp => resp.ok ? resp.json() : null)
    .catch(() => null);
  routeDetailCache.set(walkId, promise);
  return promise;
}

async function renderRouteMaps(token) {
  const visible = getVisibleLocations();
  const details = await Promise.all(currentRoutes.filter(route => (route.point_count || 0) > 0).map(route => fetchRouteDetail(route.walk_id)));
  if (token !== mapRenderToken) return;

  const detailById = new Map();
  for (const detail of details) {
    if (detail && detail.walk_id) detailById.set(detail.walk_id, detail);
  }

  for (const location of visible) {
    const state = ensureMap(location);
    if (!state) continue;
    const map = state.map;
    const locationRoutes = currentRoutes.filter(route => route.origin_location === location);
    const routeDetails = locationRoutes.map(route => detailById.get(route.walk_id)).filter(Boolean);
    const selected = detailById.get(currentSelectedWalkId);
    const selectedHere = selected && selected.origin_location === location ? selected : null;
    const allBounds = [];

    addHomeCircle(map, location);

    for (const detail of routeDetails) {
      const points = (detail.points || []).filter(point => point.lat != null && point.lon != null).map(point => [point.lat, point.lon]);
      if (!points.length) continue;
      allBounds.push(...points);
      const isSelected = detail.walk_id === currentSelectedWalkId;
      const color = LOCATION_COLORS[location] || '#9ca3af';
      const layer = points.length === 1
        ? L.circleMarker(points[0], {
            radius: isSelected ? 7 : 4,
            color,
            weight: isSelected ? 3 : 2,
            opacity: isSelected ? 1 : 0.4,
            fillOpacity: isSelected ? 0.75 : 0.25,
          }).addTo(map)
        : L.polyline(points, {
            color,
            weight: isSelected ? 5 : 2,
            opacity: isSelected ? 0.96 : 0.24,
            lineCap: 'round',
            lineJoin: 'round',
          }).addTo(map);
      layer.on('click', () => {
        currentSelectedWalkId = detail.walk_id;
        renderRouteCards();
        renderWalkTable();
        renderMaps();
      });
      if (isSelected && layer.bringToFront) layer.bringToFront();
    }

    const totalDistance = locationRoutes.reduce((sum, route) => sum + (route.distance_m || 0), 0);
    setMapSub(location, locationRoutes.length + ' walks · ' + fmtDistance(totalDistance));
    if (selectedHere && selectedHere.points && selectedHere.points.length) {
      const selectedPoints = selectedHere.points.filter(point => point.lat != null && point.lon != null).map(point => [point.lat, point.lon]);
      fitMap(map, location, selectedPoints);
    } else {
      fitMap(map, location, allBounds);
    }
    if (!routeDetails.length) {
      setMapMessage(location, 'No route points captured for this home in the selected window.');
    }
  }
}

async function renderHeatmaps(token) {
  const visible = getVisibleLocations();
  const results = await Promise.all(visible.map(location => {
    return fetch('/api/heatmap?days=' + currentDays + '&location=' + location)
      .then(resp => resp.ok ? resp.json() : null)
      .catch(() => null);
  }));
  if (token !== mapRenderToken) return;

  for (let i = 0; i < visible.length; i++) {
    const location = visible[i];
    const state = ensureMap(location);
    if (!state) continue;
    const map = state.map;
    const data = results[i];
    const points = data && data.points ? data.points : [];
    addHomeCircle(map, location);

    if (points.length && window.L && L.heatLayer) {
      L.heatLayer(points, {
        radius: 24,
        blur: 18,
        minOpacity: 0.25,
        maxZoom: 17,
        gradient: {
          0.2: '#60a5fa',
          0.45: '#22c55e',
          0.7: '#f59e0b',
          1.0: '#ef4444'
        }
      }).addTo(map);
      fitMap(map, location, points.map(point => [point[0], point[1]]));
      const meta = data.meta || {};
      setMapSub(location, (meta.walk_count || 0) + ' walks · ' + (meta.point_count || 0) + ' points');
    } else {
      fitMap(map, location, []);
      setMapSub(location, 'No heatmap points');
      setMapMessage(location, 'No Fi route points are available to build a heatmap here yet.');
    }
  }
}

async function renderMaps() {
  destroyMaps();
  setMapMeta();

  const grid = document.getElementById('mapGrid');
  if (!window.L) {
    grid.innerHTML = '<div class="map-card"><div class="map-empty" style="display:block">Leaflet failed to load. Route maps are unavailable.</div></div>';
    return;
  }

  renderMapShell();
  const token = ++mapRenderToken;
  if (currentLayer === 'heatmap') {
    await renderHeatmaps(token);
  } else {
    await renderRouteMaps(token);
  }
}

// ── Charts (shared infrastructure matching usage dashboard) ──

const baseOpts = {
  responsive: true, maintainAspectRatio: false,
  animation: { duration: 300 },
  plugins: { legend: { labels: { color: C.muted, boxWidth: 10, padding: 8, font: { size: 10 } } } },
  scales: {
    x: { type:'time', grid:{ color: C.grid }, ticks:{ color: C.muted, font:{ size: 9 }, maxRotation: 0, autoSkipPadding: 20 } },
    y: { grid:{ color: C.grid }, ticks:{ color: C.muted, font:{ size: 9 } } },
  },
};

function updateOrCreate(id, type, datasets, yTitle, extra) {
  const ctx = document.getElementById(id);
  if (charts[id]) {
    charts[id].data.datasets = datasets;
    if (charts[id].data.labels && extra && extra._labels) charts[id].data.labels = extra._labels;
    charts[id].update('none');
    return;
  }
  const opts = JSON.parse(JSON.stringify(baseOpts));
  if (yTitle) opts.scales.y.title = { display:true, text:yTitle, color:C.muted, font:{size:10} };
  if (extra) {
    if (extra.indexAxis) opts.indexAxis = extra.indexAxis;
    if (extra.noLegend) opts.plugins.legend = { display: false };
    if (extra.noTimeAxis) { opts.scales.x = { grid:{ color: C.grid }, ticks:{ color: C.muted, font:{ size: 9 } } }; }
    if (extra.beginAtZero) opts.scales.y.beginAtZero = true;
    if (extra.stepSize) opts.scales.y.ticks.stepSize = extra.stepSize;
    if (extra.timeUnit) opts.scales.x.time = { unit: extra.timeUnit };
    if (extra.stacked) { opts.scales.x.stacked = true; opts.scales.y.stacked = true; }
  }
  const config = { type, data:{ datasets }, options: opts };
  if (extra && extra._labels) config.data.labels = extra._labels;
  charts[id] = new Chart(ctx, config);
}

function buildDurationData(events) {
  return filterByLocation(events)
    .filter(e => (e.event_type === 'dock' || e.event_type === 'dock_timeout') && e.dog_walk && e.dog_walk.walk_duration_minutes != null)
    .map(d => ({ x: d.timestamp, y: d.dog_walk.walk_duration_minutes, location: d.dog_walk.location }));
}

function buildSignalData(events) {
  const docks = filterByLocation(events).filter(e => (e.event_type === 'dock' || e.event_type === 'dock_timeout') && e.dog_walk && e.dog_walk.return_signal);
  const counts = {};
  for (const d of docks) { const sig = d.dog_walk.return_signal; counts[sig] = (counts[sig] || 0) + 1; }
  return counts;
}

function buildFunnelData(events) {
  const filtered = filterByLocation(events);
  const candidates = filtered.filter(e => e.event_type === 'departure_candidate').length;
  const resets = filtered.filter(e => e.event_type === 'departure_candidate_reset');
  const resetReasons = {};
  for (const r of resets) {
    const reason = r.candidate_reset_reason || 'unknown';
    resetReasons[reason] = (resetReasons[reason] || 0) + 1;
  }
  const autoDepartures = filtered.filter(e => e.event_type === 'departure' && !isManualDeparture(e)).length;
  const manualStarts = filtered.filter(e => e.event_type === 'departure' && isManualDeparture(e)).length;
  const completions = filtered.filter(e => e.event_type === 'dock' || e.event_type === 'dock_timeout').length;
  return { candidates, resetReasons, autoDepartures, manualStarts, completions };
}

function buildWalksPerDay(events) {
  const departures = filterByLocation(events).filter(e => e.event_type === 'departure');
  const byDay = {};
  for (const d of departures) { const day = d.timestamp ? d.timestamp.slice(0, 10) : null; if (day) byDay[day] = (byDay[day] || 0) + 1; }
  return Object.entries(byDay).sort().map(([day, count]) => ({ x: day + 'T12:00:00Z', y: count }));
}

function renderCharts(events) {
  const durationData = buildDurationData(events);
  const signalCounts = buildSignalData(events);
  const funnel = buildFunnelData(events);
  const walksPerDay = buildWalksPerDay(events);

  // Duration scatter — full width
  const durationBox = document.getElementById('durationBox');
  if (durationData.length > 0) {
    durationBox.style.display = '';
    const byLoc = {};
    for (const d of durationData) { const loc = d.location || 'unknown'; if (!byLoc[loc]) byLoc[loc] = []; byLoc[loc].push({ x: d.x, y: d.y }); }
    const datasets = Object.entries(byLoc).map(([loc, pts]) => ({
      label: loc.charAt(0).toUpperCase() + loc.slice(1), data: pts,
      backgroundColor: LOCATION_COLORS[loc] || '#6b7280', borderColor: LOCATION_COLORS[loc] || '#6b7280',
      pointRadius: 5, pointHoverRadius: 7, showLine: false,
    }));
    updateOrCreate('durationChart', 'scatter', datasets, 'Minutes');
  } else {
    durationBox.style.display = 'none';
    if (charts['durationChart']) { charts['durationChart'].destroy(); delete charts['durationChart']; }
  }

  // Signal doughnut
  const signalBox = document.getElementById('signalBox');
  if (charts['signalChart']) { charts['signalChart'].destroy(); delete charts['signalChart']; }
  const sigKeys = Object.keys(signalCounts).filter(k => signalCounts[k] > 0);
  if (sigKeys.length > 0) {
    signalBox.style.display = '';
    charts['signalChart'] = new Chart(document.getElementById('signalChart'), {
      type: 'doughnut',
      data: {
        labels: sigKeys.map(k => SIGNAL_LABELS[k] || k),
        datasets: [{ data: sigKeys.map(k => signalCounts[k]), backgroundColor: sigKeys.map(k => SIGNAL_COLORS[k] || '#6b7280'), borderWidth: 0 }],
      },
      options: {
        responsive: true, maintainAspectRatio: false,
        plugins: { legend: { position:'bottom', labels:{ color: C.muted, boxWidth:10, padding:8, font:{size:10} } } },
        cutout: '60%',
      },
    });
  } else {
    signalBox.style.display = 'none';
  }

  // Funnel horizontal bar
  const funnelBox = document.getElementById('funnelBox');
  const SKIP_LABELS = {
    'inside_geofence':'Reset: Back Inside',
    'outside_walk_hours':'Reset: Outside Hours',
    'return_monitor_active':'Reset: Monitor Active',
    'no_occupied_location':'Reset: No Location',
    'location_changed':'Reset: Location Changed',
    'unknown':'Reset: Unknown',
  };
  const SKIP_COLORS = {
    'inside_geofence':C.blue,
    'outside_walk_hours':'#6b7280',
    'return_monitor_active':C.purple,
    'no_occupied_location':C.muted,
    'location_changed':C.orange,
    'unknown':'#6b7280',
  };
  const fLabels = [], fValues = [], fColors = [];
  fLabels.push('1st Outside Reading');
  fValues.push(funnel.candidates);
  fColors.push(C.amber);
  for (const [reason, count] of Object.entries(funnel.resetReasons).sort((a, b) => b[1] - a[1])) {
    fLabels.push(SKIP_LABELS[reason] || reason); fValues.push(count); fColors.push(SKIP_COLORS[reason] || '#6b7280');
  }
  fLabels.push('Fi Departures', 'Manual Starts', 'Completed Walks');
  fValues.push(funnel.autoDepartures, funnel.manualStarts, funnel.completions);
  fColors.push(C.green, C.orange, C.teal);

  if (fValues.some(v => v > 0)) {
    funnelBox.style.display = '';
    if (charts['funnelChart']) { charts['funnelChart'].destroy(); delete charts['funnelChart']; }
    charts['funnelChart'] = new Chart(document.getElementById('funnelChart'), {
      type: 'bar',
      data: { labels: fLabels, datasets: [{ data: fValues, backgroundColor: fColors, borderWidth: 0, borderRadius: 3 }] },
      options: {
        responsive: true, maintainAspectRatio: false, indexAxis: 'y',
        plugins: { legend: { display: false } },
        scales: {
          x: { grid:{ color: C.grid }, ticks:{ color: C.muted, font:{ size: 9 } }, title:{ display:true, text:'Count', color: C.muted, font:{size:10} } },
          y: { grid:{ display: false }, ticks:{ color: C.muted, font:{ size: 10 } } },
        },
      },
    });
  } else {
    funnelBox.style.display = 'none';
    if (charts['funnelChart']) { charts['funnelChart'].destroy(); delete charts['funnelChart']; }
  }

  // Walks per day — full width
  const walksBox = document.getElementById('walksPerDayBox');
  if (walksPerDay.length > 0) {
    walksBox.style.display = '';
    updateOrCreate('walksPerDayChart', 'bar', [
      { data: walksPerDay, backgroundColor: 'rgba(59,130,246,0.7)', borderColor: C.blue, borderWidth: 1, borderRadius: 3 },
    ], 'Walks', { noLegend:true, beginAtZero:true, stepSize:1, timeUnit:'day' });
  } else {
    walksBox.style.display = 'none';
    if (charts['walksPerDayChart']) { charts['walksPerDayChart'].destroy(); delete charts['walksPerDayChart']; }
  }
}

// ── Refresh ──

async function refresh() {
  try {
    const [eventsResp, stateResp, fiResp, routesResp, homesResp] = await Promise.all([
      fetch('/api/events?days=' + currentDays),
      fetch('/api/current'),
      fetch('/api/fi'),
      fetch('/api/routes?days=' + currentDays + '&location=' + currentLocation),
      fetch('/api/homes'),
    ]);
    if (!eventsResp.ok || !stateResp.ok || !routesResp.ok || !homesResp.ok) throw new Error('HTTP ' + eventsResp.status);
    const eventsData = await eventsResp.json();
    const state = await stateResp.json();
    const fi = await fiResp.json();
    currentEvents = eventsData.events || [];
    currentRoutes = (await routesResp.json()).routes || [];
    currentHomes = (await homesResp.json()).homes || {};
    normalizeSelectedWalk();

    document.getElementById('errorBanner').style.display = 'none';
    renderStaleness(state);
    renderStatusCards(state);
    renderPotatoCard(fi);
    renderRouteCards();
    renderWalkTable();
    await renderMaps();
    if (typeof Chart !== 'undefined') renderCharts(currentEvents);
  } catch (err) {
    console.error('Refresh failed:', err);
    document.getElementById('errorBanner').textContent = 'Failed to load data: ' + err.message;
    document.getElementById('errorBanner').style.display = 'block';
  }
  // Roombas fetched separately — SSH/cloud calls are slow, don't block page
  try {
    const [roombaResp, cabinResp] = await Promise.all([
      fetch('/api/roombas'),
      fetch('/api/cabin-roombas'),
    ]);
    renderRoombaCards(await roombaResp.json());
    renderCabinRoombaCards(await cabinResp.json());
  } catch (err) {
    console.error('Roomba fetch failed:', err);
  }
}

// ── Events ──

document.getElementById('locationControls').addEventListener('click', e => {
  if (e.target.tagName !== 'BUTTON') return;
  currentLocation = e.target.dataset.location;
  currentSelectedWalkId = null;
  document.querySelectorAll('#locationControls button').forEach(b => b.classList.toggle('active', b.dataset.location === currentLocation));
  Object.values(charts).forEach(c => { try { c.destroy(); } catch(e) {} });
  charts = {};
  refresh();
});
document.getElementById('timeControls').addEventListener('click', e => {
  if (e.target.tagName !== 'BUTTON') return;
  currentDays = parseInt(e.target.dataset.days);
  document.querySelectorAll('#timeControls button').forEach(b => b.classList.toggle('active', parseInt(b.dataset.days) === currentDays));
  Object.values(charts).forEach(c => { try { c.destroy(); } catch(e) {} });
  charts = {};
  refresh();
});
document.getElementById('layerControls').addEventListener('click', e => {
  if (e.target.tagName !== 'BUTTON') return;
  currentLayer = e.target.dataset.layer;
  document.querySelectorAll('#layerControls button').forEach(b => b.classList.toggle('active', b.dataset.layer === currentLayer));
  renderRouteCards();
  renderMaps();
});
document.getElementById('walkBody').addEventListener('click', e => {
  const row = e.target.closest('tr[data-walk-id]');
  if (!row) return;
  currentSelectedWalkId = row.dataset.walkId || null;
  renderRouteCards();
  renderWalkTable();
  if (currentLayer === 'routes') renderMaps();
});

refresh();
setInterval(refresh, 5 * 60 * 1000);
</script>
</body>
</html>
"""

if __name__ == "__main__":
    run()
