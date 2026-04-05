#!/usr/bin/env python3
"""Roomba Dashboard — single-file HTTP server with embedded UI.

Serves a JSON API and dashboard for Roomba status, snooze controls,
and a calendar heatmap of Roomba runs per location per day.

Reads JSONL events from ~/.openclaw/dog-walk/history/YYYY-MM-DD.jsonl
for calendar data, Roomba status via SSH (Crosstown) and iRobot Cloud (Cabin).

Same architecture as nest-dashboard.py. Intended for Tailscale-only access.
"""

import calendar
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
SNOOZE_FILE = os.path.expanduser("~/.openclaw/dog-walk/snooze.json")
SECRETS_FILE = os.path.expanduser("~/.openclaw/.secrets-cache")
IROBOT_CLOUD_SCRIPT = os.path.expanduser("~/.openclaw/skills/cabin-roomba/irobot-cloud.py")
PORT = 8553

ROOMBA_CACHE_TTL = 300  # 5 minutes
ROOMBA_SSH_TIMEOUT = 25
CABIN_ROOMBA_CACHE_TTL = 600  # 10 minutes
MACBOOK_HOST = "dylans-macbook-pro"
ROOMBA_CMD_SCRIPT = "$HOME/.openclaw/rest980/roomba-cmd.js"
ROOMBA_NODE = "/opt/homebrew/bin/node"
ROOMBA_ENVS = {
    "10max": {"env": "$HOME/.openclaw/rest980/env-10max", "label": "Roomba Combo 10 Max"},
    "j5": {"env": "$HOME/.openclaw/rest980/env-j5", "label": "Roomba J5 (Scoomba)"},
}
CABIN_ROBOT_BLIDS = {
    "3D3ACA3E5298BA11AB7E84129F29D2DD": "Floomba",
    "1D867094BA92F76D455065BCDBC68CCA": "Philly",
}

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

_roomba_cache = {"data": None, "ts": 0, "lock": threading.Lock()}
_cabin_roomba_cache = {"data": None, "ts": 0, "lock": threading.Lock()}


def _load_secrets_env():
    """Load secrets into env if not already set."""
    if os.environ.get("IROBOT_EMAIL"):
        return
    if not os.path.exists(SECRETS_FILE):
        return
    with open(SECRETS_FILE) as f:
        for line in f:
            line = line.strip()
            if "=" in line and not line.startswith("#"):
                k, v = line.split("=", 1)
                os.environ.setdefault(k, v)


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


# ---------------------------------------------------------------------------
# Snooze
# ---------------------------------------------------------------------------

def load_snooze():
    """Load snooze state. Returns dict like {"crosstown": "...", "cabin": null}."""
    try:
        if os.path.exists(SNOOZE_FILE):
            with open(SNOOZE_FILE) as f:
                data = json.load(f)
            now = datetime.now(timezone.utc)
            changed = False
            for loc in list(data):
                if data[loc] and _parse_iso8601(data[loc]) and _parse_iso8601(data[loc]) < now:
                    data[loc] = None
                    changed = True
            if changed:
                save_snooze(data)
            return data
    except (OSError, json.JSONDecodeError):
        pass
    return {"crosstown": None, "cabin": None}


def save_snooze(data):
    """Persist snooze state to disk."""
    os.makedirs(os.path.dirname(SNOOZE_FILE), exist_ok=True)
    tmp = SNOOZE_FILE + ".tmp"
    with open(tmp, "w") as f:
        json.dump(data, f, indent=2)
    os.replace(tmp, SNOOZE_FILE)


# ---------------------------------------------------------------------------
# Calendar heatmap data
# ---------------------------------------------------------------------------

def load_calendar_data(year, month):
    """Load Roomba run data for a calendar month from JSONL history.

    Returns: {
        "year": int, "month": int,
        "crosstown": {day: [run_details...], ...},
        "cabin": {day: [run_details...], ...},
        "max_runs": int
    }
    """
    num_days = calendar.monthrange(year, month)[1]
    crosstown = {}
    cabin = {}

    for day in range(1, num_days + 1):
        date_str = f"{year}-{month:02d}-{day:02d}"
        path = os.path.join(HISTORY_DIR, f"{date_str}.jsonl")
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

                    event_type = rec.get("event_type")
                    if event_type not in ("departure", "dock", "dock_timeout"):
                        continue

                    roombas = rec.get("roombas", {})
                    walk = rec.get("dog_walk", {})
                    ts = rec.get("timestamp", "")

                    for loc in ("crosstown", "cabin"):
                        loc_data = roombas.get(loc, {})
                        cmd_result = loc_data.get("last_command_result")
                        if not cmd_result:
                            continue

                        target = crosstown if loc == "crosstown" else cabin
                        if day not in target:
                            target[day] = []

                        # Determine trigger source
                        source = cmd_result.get("source", "automatic")
                        if source == "dog-walk-start":
                            trigger = "manual"
                        else:
                            trigger = "dog_walk"

                        run_info = {
                            "time": ts,
                            "event": event_type,
                            "trigger": trigger,
                            "success": cmd_result.get("success", False),
                            "skipped": cmd_result.get("skipped"),
                        }

                        # Extract Roomba names from results
                        results = cmd_result.get("results", [])
                        if results:
                            run_info["roombas"] = [r.get("name", "?") for r in results]

                        # For dock events, add return signal
                        if event_type in ("dock", "dock_timeout"):
                            run_info["return_signal"] = walk.get("return_signal")
                            run_info["duration_min"] = walk.get("walk_duration_minutes")

                        target[day].append(run_info)
        except OSError:
            continue

    # Deduplicate: keep only departure events for run count, dock for details
    for loc_data in (crosstown, cabin):
        for day in loc_data:
            runs = loc_data[day]
            # Group by walk: departure = start, dock = end
            departures = [r for r in runs if r["event"] == "departure"]
            docks = [r for r in runs if r["event"] in ("dock", "dock_timeout")]
            # Merge dock info into departures where possible
            merged = []
            for dep in departures:
                entry = {
                    "time": dep["time"],
                    "trigger": dep["trigger"],
                    "success": dep["success"],
                    "skipped": dep.get("skipped"),
                    "roombas": dep.get("roombas", []),
                }
                # Find matching dock (closest dock after this departure)
                for dock in docks:
                    if dock["time"] > dep["time"]:
                        entry["return_signal"] = dock.get("return_signal")
                        entry["duration_min"] = dock.get("duration_min")
                        docks.remove(dock)
                        break
                merged.append(entry)
            # Include snoozed/skipped departures
            snoozed = [r for r in runs if r.get("skipped")]
            for s in snoozed:
                if s not in departures:
                    merged.append({
                        "time": s["time"],
                        "trigger": s["trigger"],
                        "success": False,
                        "skipped": s["skipped"],
                    })
            loc_data[day] = merged

    max_runs = 0
    for loc_data in (crosstown, cabin):
        for day_runs in loc_data.values():
            max_runs = max(max_runs, len(day_runs))

    # Convert day keys to strings for JSON
    return {
        "year": year,
        "month": month,
        "crosstown": {str(k): v for k, v in crosstown.items()},
        "cabin": {str(k): v for k, v in cabin.items()},
        "max_runs": max_runs,
        "first_weekday": calendar.monthrange(year, month)[0],  # 0=Monday
        "num_days": num_days,
    }


# ---------------------------------------------------------------------------
# HTTP Handler
# ---------------------------------------------------------------------------

class DashboardHandler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        sys.stderr.write(f"{self.address_string()} {args[0]}\n")

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/") or "/"
        qs = parse_qs(parsed.query)

        if path == "/":
            self._serve_html()
        elif path == "/api/roombas":
            self._serve_roombas()
        elif path == "/api/cabin-roombas":
            self._serve_cabin_roombas()
        elif path == "/api/snooze":
            self._serve_snooze_status()
        elif path == "/api/calendar":
            now = datetime.now()
            try:
                year = int(qs.get("year", [str(now.year)])[0])
            except (ValueError, IndexError):
                year = now.year
            try:
                month = int(qs.get("month", [str(now.month)])[0])
            except (ValueError, IndexError):
                month = now.month
            month = max(1, min(12, month))
            self._serve_calendar(year, month)
        else:
            self._respond(404, {"error": "not found"})

    def do_POST(self):
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/") or "/"
        if path == "/api/snooze":
            try:
                length = int(self.headers.get("Content-Length", 0))
                body = json.loads(self.rfile.read(length)) if length else {}
            except (json.JSONDecodeError, ValueError):
                self._respond(400, {"error": "invalid json"})
                return
            location = body.get("location", "all")
            minutes = body.get("minutes", 0)
            self._set_snooze(location, minutes)
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

    def _serve_roombas(self):
        self._respond(200, fetch_roomba_status())

    def _serve_cabin_roombas(self):
        self._respond(200, fetch_cabin_roomba_status())

    def _serve_snooze_status(self):
        self._respond(200, load_snooze())

    def _set_snooze(self, location, minutes):
        snooze = load_snooze()
        now = datetime.now(timezone.utc)
        locations = ["crosstown", "cabin"] if location == "all" else [location]
        for loc in locations:
            if minutes > 0:
                expires = (now + timedelta(minutes=minutes)).strftime("%Y-%m-%dT%H:%M:%SZ")
                snooze[loc] = expires
            else:
                snooze[loc] = None
        save_snooze(snooze)
        self._respond(200, {"ok": True, "snooze": snooze})

    def _serve_calendar(self, year, month):
        data = load_calendar_data(year, month)
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
    print(f"Roomba Dashboard running on http://0.0.0.0:{PORT}", flush=True)
    print(f"  History dir: {HISTORY_DIR}", flush=True)
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
<title>Roomba Dashboard</title>
<noscript><p style="color:#f87171;text-align:center;margin:2rem">JavaScript required.</p></noscript>
<style>
:root{--bg:#0f1117;--surface:#1a1d27;--border:#2a2d3a;--text:#e4e4e7;--muted:#9ca3af}
*{margin:0;padding:0;box-sizing:border-box}
body{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',system-ui,sans-serif;background:var(--bg);color:var(--text);padding:1rem;max-width:1200px;margin:0 auto}

/* Header */
.header{display:flex;align-items:center;justify-content:space-between;margin-bottom:1rem;flex-wrap:wrap;gap:0.5rem}
.header h1{font-size:1.15rem;font-weight:600}
.header-right{display:flex;align-items:center;gap:1rem}
.last-update{font-size:0.7rem;color:var(--muted)}

/* Stat cards */
.stats{display:grid;grid-template-columns:repeat(auto-fit,minmax(160px,1fr));gap:0.75rem;margin-bottom:1rem}
.stat{background:var(--surface);border:1px solid var(--border);border-radius:10px;padding:0.85rem}
.stat-label{font-size:0.65rem;text-transform:uppercase;letter-spacing:0.05em;color:var(--muted);margin-bottom:0.2rem}
.stat-value{font-size:1.5rem;font-weight:700}
.stat-sub{font-size:0.75rem;color:var(--muted);margin-top:0.15rem}

/* Section */
.section{background:var(--surface);border:1px solid var(--border);border-radius:10px;padding:0.85rem;margin-bottom:1rem}
.section-head{display:flex;align-items:center;justify-content:space-between;gap:0.75rem;margin-bottom:0.75rem;flex-wrap:wrap}
.section-head h2{font-size:0.82rem;font-weight:600;color:var(--muted)}

/* Snooze bar */
.snooze-bar{background:var(--surface);border:1px solid var(--border);border-radius:10px;padding:0.75rem 1rem;margin-bottom:1rem;display:flex;align-items:center;gap:1rem;flex-wrap:wrap}
.snooze-bar h3{font-size:0.78rem;font-weight:600;color:var(--muted);white-space:nowrap}
.snooze-group{display:flex;align-items:center;gap:0.5rem}
.snooze-group .label{font-size:0.75rem;color:var(--text);min-width:70px}
.snooze-group .status{font-size:0.72rem;min-width:80px}
.snooze-btn{background:rgba(255,255,255,0.06);border:1px solid var(--border);color:var(--text);padding:0.25rem 0.6rem;border-radius:5px;cursor:pointer;font-size:0.7rem;transition:all 0.15s}
.snooze-btn:hover{border-color:#f59e0b;color:#f59e0b}
.snooze-btn.active{background:rgba(245,158,11,0.15);border-color:#f59e0b;color:#f59e0b}
.snooze-btn.clear{border-color:rgba(34,197,94,0.4);color:#22c55e}
.snooze-btn.clear:hover{border-color:#22c55e}

/* Calendar grid */
.cal-grid-wrap{display:grid;grid-template-columns:1fr 1fr;gap:0.75rem}
@media(max-width:840px){.cal-grid-wrap{grid-template-columns:1fr}}
.cal-card{background:rgba(255,255,255,0.02);border:1px solid rgba(255,255,255,0.04);border-radius:10px;padding:0.65rem}
.cal-card h3{font-size:0.85rem;font-weight:600;margin-bottom:0.5rem}
.cal-nav{display:flex;align-items:center;gap:0.75rem;justify-content:center;margin-bottom:0.75rem}
.cal-nav button{background:var(--surface);border:1px solid var(--border);color:var(--text);padding:0.3rem 0.7rem;border-radius:5px;cursor:pointer;font-size:0.75rem}
.cal-nav button:hover{border-color:#3b82f6}
.cal-nav .month-label{font-size:0.85rem;font-weight:600;min-width:140px;text-align:center}
.cal-grid{display:grid;grid-template-columns:repeat(7,1fr);gap:2px}
.cal-hdr{font-size:0.62rem;text-align:center;color:var(--muted);padding:0.25rem 0;font-weight:500;text-transform:uppercase}
.cal-cell{position:relative;aspect-ratio:1;border-radius:4px;display:flex;align-items:center;justify-content:center;font-size:0.72rem;cursor:default;transition:all 0.12s;border:1px solid transparent}
.cal-cell.empty{background:transparent}
.cal-cell.has-runs{cursor:pointer}
.cal-cell.has-runs:hover{border-color:rgba(255,255,255,0.2);transform:scale(1.08)}
.cal-cell .day-num{position:relative;z-index:1}
.cal-cell.today{border-color:rgba(255,255,255,0.3)}

/* Tooltip */
.cal-tooltip{position:fixed;background:#1e2130;border:1px solid var(--border);border-radius:8px;padding:0.6rem 0.8rem;font-size:0.72rem;color:var(--text);z-index:1000;pointer-events:none;max-width:280px;box-shadow:0 8px 24px rgba(0,0,0,0.4);display:none}
.cal-tooltip .tt-title{font-weight:600;margin-bottom:0.3rem}
.cal-tooltip .tt-run{padding:0.2rem 0;border-top:1px solid rgba(255,255,255,0.05)}
.cal-tooltip .tt-run:first-of-type{border-top:none}
.cal-tooltip .tt-detail{color:var(--muted);font-size:0.68rem}

/* Badges */
.badge{display:inline-block;padding:0.1rem 0.45rem;border-radius:4px;font-size:0.7rem;font-weight:500}
.badge-ok{background:rgba(34,197,94,0.15);color:#22c55e}
.badge-err{background:rgba(239,68,68,0.15);color:#ef4444}
.badge-green{background:rgba(34,197,94,0.15);color:#22c55e}
.badge-amber{background:rgba(245,158,11,0.15);color:#f59e0b}
.badge-red{background:rgba(239,68,68,0.15);color:#ef4444}
.badge-blue{background:rgba(59,130,246,0.15);color:#3b82f6}
.badge-muted{background:rgba(107,114,128,0.15);color:#6b7280}

.loading{text-align:center;color:var(--muted);padding:2rem}
.error-banner{background:rgba(239,68,68,0.1);border:1px solid rgba(239,68,68,0.3);border-radius:8px;padding:0.75rem;color:#ef4444;font-size:0.8rem;margin-bottom:1rem;display:none}
</style>
</head>
<body>

<div class="header">
  <h1>Roomba Dashboard</h1>
  <div class="header-right">
    <span class="last-update" id="lastUpdate"></span>
  </div>
</div>

<div class="error-banner" id="errorBanner"></div>

<div class="section">
  <div class="section-head"><h2>Crosstown</h2></div>
  <div class="stats" id="crosstownCards"><div class="loading">Loading...</div></div>
</div>

<div class="section">
  <div class="section-head"><h2>Cabin</h2></div>
  <div class="stats" id="cabinCards"><div class="loading">Loading...</div></div>
</div>

<div class="snooze-bar" id="snoozeBar">
  <h3>Roomba Snooze</h3>
  <div class="snooze-group" data-loc="crosstown">
    <span class="label">Crosstown</span>
    <span class="status" id="snoozeStatus-crosstown" style="color:var(--muted)">—</span>
    <button class="snooze-btn" data-loc="crosstown" data-mins="60">1h</button>
    <button class="snooze-btn" data-loc="crosstown" data-mins="180">3h</button>
    <button class="snooze-btn" data-loc="crosstown" data-mins="480">8h</button>
    <button class="snooze-btn" data-loc="crosstown" data-mins="525600">Indef</button>
    <button class="snooze-btn clear" data-loc="crosstown" data-mins="0">Clear</button>
  </div>
  <div class="snooze-group" data-loc="cabin">
    <span class="label">Cabin</span>
    <span class="status" id="snoozeStatus-cabin" style="color:var(--muted)">—</span>
    <button class="snooze-btn" data-loc="cabin" data-mins="60">1h</button>
    <button class="snooze-btn" data-loc="cabin" data-mins="180">3h</button>
    <button class="snooze-btn" data-loc="cabin" data-mins="480">8h</button>
    <button class="snooze-btn" data-loc="cabin" data-mins="525600">Indef</button>
    <button class="snooze-btn clear" data-loc="cabin" data-mins="0">Clear</button>
  </div>
</div>

<div class="section">
  <div class="section-head">
    <h2>Run History</h2>
  </div>
  <div class="cal-nav">
    <button id="calPrev">&larr;</button>
    <span class="month-label" id="calMonthLabel"></span>
    <button id="calNext">&rarr;</button>
  </div>
  <div class="cal-grid-wrap" id="calGrid"><div class="loading">Loading calendar...</div></div>
</div>

<div class="cal-tooltip" id="tooltip"></div>

<script>
const C = { green:'#22c55e', amber:'#f59e0b', red:'#ef4444', blue:'#3b82f6', purple:'#8b5cf6', teal:'#14b8a6', orange:'#f97316', muted:'#9ca3af' };
const LOCATION_COLORS = { crosstown: '#4A90D9', cabin: '#FF8C00' };
const SIGNAL_LABELS = { 'network_wifi':'WiFi', 'ring_motion':'Ring', 'fi_gps':'Fi GPS', 'timeout':'Timeout' };
const MONTH_NAMES = ['January','February','March','April','May','June','July','August','September','October','November','December'];
const DAY_HDRS = ['Mo','Tu','We','Th','Fr','Sa','Su'];

let calYear, calMonth;
const now = new Date();
calYear = now.getFullYear();
calMonth = now.getMonth() + 1;

// ── Helpers ──

function fmtTime(iso) {
  if (!iso) return '-';
  const d = new Date(iso);
  return d.toLocaleTimeString('en-US', { hour:'numeric', minute:'2-digit' });
}

// ── Roomba Cards ──

function renderCrosstownCards(roombas) {
  const el = document.getElementById('crosstownCards');
  if (!roombas || roombas.error || !roombas.robots) {
    el.innerHTML = '<div class="stat"><div class="stat-label">Status</div><div class="stat-value" style="color:' + C.muted + '">Offline</div></div>';
    return;
  }

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
    if (r.missions != null) html += '<div class="stat-sub">' + r.missions + ' total missions</div>';
    html += '</div>';
  }
  el.innerHTML = html || '<div class="stat"><div class="stat-label">Status</div><div class="stat-value" style="color:' + C.muted + '">No data</div></div>';
}

function renderCabinCards(data) {
  const el = document.getElementById('cabinCards');
  if (!data || data.error || !data.robots || !Object.keys(data.robots).length) {
    el.innerHTML = '<div class="stat"><div class="stat-label">Status</div><div class="stat-value" style="color:' + C.muted + '">Offline</div></div>';
    return;
  }

  const MISSION_LABELS = { 'ok':'Completed', 'stuck':'Stuck', 'cancelled':'Cancelled' };
  const MISSION_COLORS = { 'ok':C.green, 'stuck':C.red, 'cancelled':C.muted };

  let html = '';
  for (const [id, r] of Object.entries(data.robots)) {
    if (r.error) {
      html += '<div class="stat"><div class="stat-label">' + (r.name || id) + '</div>';
      html += '<div class="stat-value" style="color:' + C.muted + '">Offline</div></div>';
      continue;
    }

    const mission = r.lastMission || 'unknown';
    const label = MISSION_LABELS[mission] || mission;
    const color = MISSION_COLORS[mission] || C.muted;

    html += '<div class="stat"><div class="stat-label">' + (r.name || id) + '</div>';
    html += '<div class="stat-value" style="color:' + color + '">' + label + '</div>';

    let sub = '';
    if (r.durationMin != null) sub += r.durationMin + 'min';
    if (r.sqft != null) sub += (sub ? ', ' : '') + r.sqft + ' sqft';
    if (sub) html += '<div class="stat-sub">' + sub + '</div>';

    if (r.startTime) {
      const ago = Math.round((Date.now()/1000 - r.startTime) / 3600);
      html += '<div class="stat-sub">' + (ago < 24 ? ago + 'h ago' : Math.round(ago/24) + 'd ago') + '</div>';
    }
    if (r.missions != null) html += '<div class="stat-sub">' + r.missions + ' total missions</div>';
    html += '</div>';
  }
  el.innerHTML = html;
}

// ── Snooze ──

function renderSnooze(data) {
  for (const loc of ['crosstown', 'cabin']) {
    const el = document.getElementById('snoozeStatus-' + loc);
    if (!el) continue;
    const expires = data[loc];
    if (expires) {
      const expDate = new Date(expires);
      const remaining = Math.max(0, Math.round((expDate.getTime() - Date.now()) / 60000));
      if (remaining > 0) {
        el.textContent = remaining > 10000 ? 'Indefinite' : remaining < 60 ? remaining + 'm left' : Math.floor(remaining/60) + 'h ' + (remaining%60) + 'm left';
        el.style.color = C.amber;
      } else {
        el.textContent = 'Active';
        el.style.color = C.muted;
      }
    } else {
      el.textContent = 'Active';
      el.style.color = C.green;
    }
  }
}

async function setSnooze(location, minutes) {
  try {
    const resp = await fetch('/api/snooze', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ location, minutes }),
    });
    if (resp.ok) {
      const result = await resp.json();
      renderSnooze(result.snooze);
    }
  } catch (err) {
    console.error('Snooze failed:', err);
  }
}

document.getElementById('snoozeBar').addEventListener('click', e => {
  const btn = e.target.closest('.snooze-btn');
  if (!btn) return;
  setSnooze(btn.dataset.loc, parseInt(btn.dataset.mins));
});

// ── Calendar Heatmap ──

let calData = null;

function colorForRuns(count, maxRuns, location) {
  if (count === 0) return 'rgba(255,255,255,0.03)';
  const base = LOCATION_COLORS[location] || '#9ca3af';
  // Parse hex to RGB
  const r = parseInt(base.slice(1,3), 16);
  const g = parseInt(base.slice(3,5), 16);
  const b = parseInt(base.slice(5,7), 16);
  const intensity = Math.min(1, count / Math.max(maxRuns, 2));
  const alpha = 0.15 + intensity * 0.65;
  return 'rgba(' + r + ',' + g + ',' + b + ',' + alpha.toFixed(2) + ')';
}

function renderCalendar(data) {
  calData = data;
  const label = document.getElementById('calMonthLabel');
  label.textContent = MONTH_NAMES[data.month - 1] + ' ' + data.year;

  const grid = document.getElementById('calGrid');
  const today = new Date();
  const isCurrentMonth = today.getFullYear() === data.year && (today.getMonth() + 1) === data.month;
  const todayDay = isCurrentMonth ? today.getDate() : -1;

  let html = '';
  for (const loc of ['crosstown', 'cabin']) {
    const locLabel = loc.charAt(0).toUpperCase() + loc.slice(1);
    const locRuns = data[loc] || {};
    html += '<div class="cal-card"><h3 style="color:' + LOCATION_COLORS[loc] + '">' + locLabel + '</h3>';
    html += '<div class="cal-grid">';

    // Day headers
    for (const d of DAY_HDRS) {
      html += '<div class="cal-hdr">' + d + '</div>';
    }

    // Empty cells for padding before first day (firstWeekday: 0=Mon)
    for (let i = 0; i < data.first_weekday; i++) {
      html += '<div class="cal-cell empty"></div>';
    }

    // Day cells
    for (let day = 1; day <= data.num_days; day++) {
      const runs = locRuns[String(day)] || [];
      const count = runs.length;
      const bg = colorForRuns(count, data.max_runs, loc);
      const classes = ['cal-cell'];
      if (count > 0) classes.push('has-runs');
      if (day === todayDay) classes.push('today');

      html += '<div class="' + classes.join(' ') + '" style="background:' + bg + '" data-loc="' + loc + '" data-day="' + day + '">';
      html += '<span class="day-num">' + day + '</span>';
      html += '</div>';
    }

    html += '</div></div>';
  }
  grid.innerHTML = html;
}

// Tooltip
const tooltip = document.getElementById('tooltip');

document.getElementById('calGrid').addEventListener('mouseover', e => {
  const cell = e.target.closest('.cal-cell.has-runs');
  if (!cell || !calData) { tooltip.style.display = 'none'; return; }
  const loc = cell.dataset.loc;
  const day = cell.dataset.day;
  const runs = (calData[loc] || {})[day] || [];
  if (!runs.length) { tooltip.style.display = 'none'; return; }

  const locLabel = loc.charAt(0).toUpperCase() + loc.slice(1);
  let html = '<div class="tt-title">' + locLabel + ' — ' + MONTH_NAMES[calData.month - 1] + ' ' + day + '</div>';
  html += '<div style="color:var(--muted);font-size:0.68rem;margin-bottom:0.3rem">' + runs.length + ' run' + (runs.length > 1 ? 's' : '') + '</div>';

  for (const run of runs) {
    html += '<div class="tt-run">';
    html += '<div>' + fmtTime(run.time);
    if (run.trigger === 'manual') html += ' <span class="badge badge-blue">Manual</span>';
    else html += ' <span class="badge badge-muted">Dog Walk</span>';
    if (run.success) html += ' <span class="badge badge-green">OK</span>';
    else if (run.skipped) html += ' <span class="badge badge-amber">' + run.skipped + '</span>';
    else html += ' <span class="badge badge-red">Failed</span>';
    html += '</div>';

    let detail = '';
    if (run.roombas && run.roombas.length) detail += run.roombas.join(', ');
    if (run.duration_min != null) detail += (detail ? ' · ' : '') + Math.round(run.duration_min) + 'min walk';
    if (run.return_signal) detail += (detail ? ' · ' : '') + (SIGNAL_LABELS[run.return_signal] || run.return_signal);
    if (detail) html += '<div class="tt-detail">' + detail + '</div>';
    html += '</div>';
  }
  tooltip.innerHTML = html;
  tooltip.style.display = 'block';

  const rect = cell.getBoundingClientRect();
  let left = rect.right + 8;
  let top = rect.top;
  // Keep tooltip on screen
  if (left + 280 > window.innerWidth) left = rect.left - 288;
  if (top + tooltip.offsetHeight > window.innerHeight) top = window.innerHeight - tooltip.offsetHeight - 8;
  tooltip.style.left = left + 'px';
  tooltip.style.top = Math.max(8, top) + 'px';
});

document.getElementById('calGrid').addEventListener('mouseout', e => {
  if (!e.target.closest('.cal-cell.has-runs')) tooltip.style.display = 'none';
});

// Month navigation
document.getElementById('calPrev').addEventListener('click', () => {
  calMonth--;
  if (calMonth < 1) { calMonth = 12; calYear--; }
  loadCalendar();
});
document.getElementById('calNext').addEventListener('click', () => {
  calMonth++;
  if (calMonth > 12) { calMonth = 1; calYear++; }
  loadCalendar();
});

async function loadCalendar() {
  try {
    const resp = await fetch('/api/calendar?year=' + calYear + '&month=' + calMonth);
    if (resp.ok) renderCalendar(await resp.json());
  } catch (err) {
    console.error('Calendar load failed:', err);
  }
}

// ── Refresh ──

async function refresh() {
  try {
    const [snoozeResp] = await Promise.all([
      fetch('/api/snooze'),
    ]);
    if (snoozeResp.ok) renderSnooze(await snoozeResp.json());
    document.getElementById('lastUpdate').textContent = 'Updated ' + new Date().toLocaleTimeString();
    document.getElementById('errorBanner').style.display = 'none';
  } catch (err) {
    console.error('Refresh failed:', err);
    document.getElementById('errorBanner').textContent = 'Failed to load data: ' + err.message;
    document.getElementById('errorBanner').style.display = 'block';
  }
  // Roombas fetched separately — SSH/cloud calls are slow
  try {
    const [crosstownResp, cabinResp] = await Promise.all([
      fetch('/api/roombas'),
      fetch('/api/cabin-roombas'),
    ]);
    renderCrosstownCards(await crosstownResp.json());
    renderCabinCards(await cabinResp.json());
  } catch (err) {
    console.error('Roomba fetch failed:', err);
  }
}

refresh();
loadCalendar();
setInterval(refresh, 5 * 60 * 1000);
setInterval(loadCalendar, 5 * 60 * 1000);
</script>
</body>
</html>
"""

if __name__ == "__main__":
    run()
