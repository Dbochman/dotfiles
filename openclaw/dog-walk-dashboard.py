#!/usr/bin/env python3
"""Dog Walk & Roomba Dashboard — single-file HTTP server with embedded UI.

Serves a JSON API and Chart.js dashboard for dog walk history and Roomba operations.
Reads JSONL events from ~/.openclaw/dog-walk/history/YYYY-MM-DD.jsonl
and current state from ~/.openclaw/dog-walk/state.json

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
                        ts = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
                    except (ValueError, AttributeError):
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


class DashboardHandler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        sys.stderr.write(f"{self.address_string()} {args[0]}\n")

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/") or "/"
        qs = parse_qs(parsed.query)

        if path == "/":
            self._serve_html()
        elif path == "/api/events":
            days = 30
            try:
                days = int(qs.get("days", ["30"])[0])
            except (ValueError, IndexError):
                pass
            self._serve_events(days)
        elif path == "/api/current":
            self._serve_current()
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

<div class="table-section"><h2>Recent Walks</h2><table class="walk-table"><thead><tr><th>Date</th><th>Location</th><th>Duration</th><th>Return Signal</th><th>Walkers</th><th>Roombas</th></tr></thead><tbody id="walkBody"></tbody></table></div>

<div class="charts-grid">
  <div class="chart-box" id="durationBox"><h2>Walk Duration (minutes)</h2><div class="chart-wrap"><canvas id="durationChart"></canvas></div></div>
  <div class="chart-box" id="walksPerDayBox"><h2>Walks per Day</h2><div class="chart-wrap"><canvas id="walksPerDayChart"></canvas></div></div>
  <div class="chart-box" id="signalBox"><h2>Return Signal Distribution</h2><div class="chart-wrap"><canvas id="signalChart"></canvas></div></div>
  <div class="chart-box" id="funnelBox"><h2>Detection Funnel</h2><div class="chart-wrap"><canvas id="funnelChart"></canvas></div></div>
</div>

<script>
const C = { green:'#22c55e', amber:'#f59e0b', red:'#ef4444', blue:'#3b82f6', purple:'#8b5cf6', teal:'#14b8a6', cyan:'#06b6d4', orange:'#f97316', pink:'#ec4899', muted:'#9ca3af', grid:'rgba(255,255,255,0.05)' };
const SIGNAL_COLORS = { 'network_wifi':C.green, 'ring_motion':C.blue, 'findmy':C.purple, 'fi_gps':C.teal, 'timeout':C.red };
const SIGNAL_LABELS = { 'network_wifi':'WiFi', 'ring_motion':'Ring Motion', 'findmy':'FindMy', 'fi_gps':'Fi GPS', 'timeout':'Timeout' };
const LOCATION_COLORS = { 'cabin':'#FF8C00', 'crosstown':'#4A90D9' };

let charts = {};
let currentDays = 7;
let currentLocation = 'all';

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

function filterByLocation(events) {
  if (currentLocation === 'all') return events;
  return events.filter(e => {
    const loc = (e.dog_walk && e.dog_walk.location) || e.skip_location || e.location || '';
    return loc === currentLocation;
  });
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

// ── Walk table ──

function renderWalkTable(events) {
  const walks = [];
  const departures = events.filter(e => e.event_type === 'departure');
  const docks = events.filter(e => e.event_type === 'dock' || e.event_type === 'dock_timeout');

  for (const dep of departures) {
    const loc = dep.dog_walk && dep.dog_walk.location;
    const depTime = dep.dog_walk && dep.dog_walk.departed_at;
    const dock = docks.find(d => d.dog_walk && d.dog_walk.location === loc && d.dog_walk.departed_at === depTime);
    walks.push({
      departed_at: depTime,
      location: loc,
      duration: dock && dock.dog_walk ? dock.dog_walk.walk_duration_minutes : null,
      return_signal: dock && dock.dog_walk ? dock.dog_walk.return_signal : null,
      walkers: (dock && dock.dog_walk && dock.dog_walk.walkers) || (dep.dog_walk && dep.dog_walk.walkers) || [],
      roomba_ok: dock && dock.roombas && dock.roombas[loc] && dock.roombas[loc].last_command_result ? dock.roombas[loc].last_command_result.success : null,
      people: dep.dog_walk ? dep.dog_walk.people : 0,
    });
  }

  walks.reverse();
  const tbody = document.getElementById('walkBody');
  if (!walks.length) { tbody.innerHTML = '<tr><td colspan="6" style="color:' + C.muted + ';text-align:center;padding:1rem">No walks in this period</td></tr>'; return; }

  tbody.innerHTML = walks.slice(0, 50).map(w => {
    const sig = w.return_signal;
    const sigBadgeColor = sig === 'timeout' ? 'red' : sig === 'findmy' ? 'purple' : sig === 'ring_motion' ? 'blue' : sig === 'fi_gps' ? 'teal' : 'green';
    const sigBadge = sig ? '<span class="badge badge-' + sigBadgeColor + '">' + (SIGNAL_LABELS[sig] || sig) + '</span>' : '-';
    const roombaBadge = w.roomba_ok === true ? '<span class="badge badge-ok">OK</span>' : w.roomba_ok === false ? '<span class="badge badge-err">Failed</span>' : '-';
    const manual = w.people === 0 ? ' <span class="stat-tag">manual</span>' : '';
    return '<tr><td>' + fmtTime(w.departed_at) + '</td><td>' + (w.location || '?') + manual + '</td><td>' + fmtDuration(w.duration) + '</td><td>' + sigBadge + '</td><td>' + (w.walkers.length ? w.walkers.join(', ') : '-') + '</td><td>' + roombaBadge + '</td></tr>';
  }).join('');
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
  const skips = filtered.filter(e => e.event_type === 'departure_skip');
  const departures = filtered.filter(e => e.event_type === 'departure').length;
  const docks = filtered.filter(e => e.event_type === 'dock' || e.event_type === 'dock_timeout').length;
  const skipReasons = {};
  for (const s of skips) { const reason = s.skip_reason || 'unknown'; skipReasons[reason] = (skipReasons[reason] || 0) + 1; }
  return { skipReasons, departures, docks };
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
  const SKIP_LABELS = { 'outside_walk_hours':'Outside Hours', 'confirmed_vacant':'Vacant', 'wifi_present':'WiFi Present', 'cabin_prompt_suppressed':'Prompt Suppressed' };
  const SKIP_COLORS = { 'outside_walk_hours':'#6b7280', 'confirmed_vacant':C.muted, 'wifi_present':C.blue, 'cabin_prompt_suppressed':C.amber };
  const fLabels = [], fValues = [], fColors = [];
  for (const [reason, count] of Object.entries(funnel.skipReasons).sort((a, b) => b[1] - a[1])) {
    fLabels.push(SKIP_LABELS[reason] || reason); fValues.push(count); fColors.push(SKIP_COLORS[reason] || '#6b7280');
  }
  fLabels.push('Departures', 'Docks'); fValues.push(funnel.departures, funnel.docks); fColors.push(C.green, C.teal);

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
    const [eventsResp, stateResp, fiResp] = await Promise.all([
      fetch('/api/events?days=' + currentDays),
      fetch('/api/current'),
      fetch('/api/fi'),
    ]);
    if (!eventsResp.ok || !stateResp.ok) throw new Error('HTTP ' + eventsResp.status);
    const eventsData = await eventsResp.json();
    const state = await stateResp.json();
    const fi = await fiResp.json();
    const events = eventsData.events || [];

    document.getElementById('errorBanner').style.display = 'none';
    renderStaleness(state);
    renderStatusCards(state);
    renderPotatoCard(fi);
    renderWalkTable(filterByLocation(events));
    if (typeof Chart !== 'undefined') renderCharts(events);
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

refresh();
setInterval(refresh, 5 * 60 * 1000);
</script>
</body>
</html>
"""

if __name__ == "__main__":
    run()
