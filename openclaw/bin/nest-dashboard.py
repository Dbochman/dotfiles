#!/usr/bin/env python3
"""Nest Climate Dashboard — single-file HTTP server with embedded UI.

Serves a JSON API and Chart.js dashboard for Nest thermostat history.
Reads JSONL snapshots from ~/.openclaw/nest-history/YYYY-MM-DD.jsonl

Intended for Tailscale-only access (Mac Mini firewall blocks external).
"""

import json
import os
import signal
import sys
import threading
from datetime import datetime, timedelta, timezone
from http.server import HTTPServer, BaseHTTPRequestHandler
from socketserver import ThreadingMixIn
from urllib.parse import urlparse, parse_qs

HISTORY_DIR = os.path.expanduser("~/.openclaw/nest-history")
PRESENCE_DIR = os.path.expanduser("~/.openclaw/presence")
PRESENCE_HISTORY_DIR = os.path.join(PRESENCE_DIR, "history")
PORT = 8550
MAX_HOURS = 8760  # 1 year
DOWNSAMPLE_THRESHOLD_HOURS = 168  # 7 days — beyond this, keep ~1 per hour


def load_snapshots(hours):
    """Load snapshots from JSONL files covering the requested time range."""
    hours = min(max(1, hours), MAX_HOURS)
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(hours=hours)
    records = []

    # Only open files that could contain data in the range
    num_days = hours // 24 + 2
    for i in range(num_days):
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

    # Downsample for large ranges: keep closest snapshot to each hour boundary
    if hours > DOWNSAMPLE_THRESHOLD_HOURS and len(records) > 1:
        records = _downsample_hourly(records)

    return records, hours


def _downsample_hourly(records):
    """Keep approximately one snapshot per hour."""
    buckets = {}
    for rec in records:
        ts_str = rec.get("timestamp", "")
        try:
            ts = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
        except (ValueError, AttributeError):
            continue
        # Bucket key: date + hour
        key = ts.strftime("%Y-%m-%d-%H")
        # Keep the one closest to the hour boundary (minute closest to 0)
        if key not in buckets or ts.minute < _ts_minute(buckets[key]):
            buckets[key] = rec
    # Return in chronological order
    return [buckets[k] for k in sorted(buckets.keys())]


def _ts_minute(rec):
    ts_str = rec.get("timestamp", "")
    try:
        return datetime.fromisoformat(ts_str.replace("Z", "+00:00")).minute
    except (ValueError, AttributeError):
        return 60


def load_current_presence():
    """Load current presence state from state.json."""
    path = os.path.join(PRESENCE_DIR, "state.json")
    try:
        with open(path) as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return None


def load_presence_history(hours):
    """Load presence history from JSONL files covering the requested time range."""
    hours = min(max(1, hours), MAX_HOURS)
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(hours=hours)
    records = []

    num_days = hours // 24 + 2
    for i in range(num_days):
        day = (now - timedelta(days=i)).strftime("%Y-%m-%d")
        path = os.path.join(PRESENCE_HISTORY_DIR, f"{day}.jsonl")
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
    return records


class DashboardHandler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        # Quieter logging — just method + path + status
        sys.stderr.write(f"{self.address_string()} {args[0]}\n")

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/") or "/"
        qs = parse_qs(parsed.query)

        if path == "/":
            self._serve_html()
        elif path == "/api/data":
            hours = 24
            try:
                hours = int(qs.get("hours", ["24"])[0])
            except (ValueError, IndexError):
                pass
            self._serve_data(hours)
        elif path == "/api/current":
            self._serve_current()
        elif path == "/api/presence":
            self._serve_presence()
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

    def _serve_data(self, hours):
        records, clamped_hours = load_snapshots(hours)
        presence = load_presence_history(hours)
        self._respond(200, {
            "meta": {
                "hours": clamped_hours,
                "count": len(records),
                "downsampled": clamped_hours > DOWNSAMPLE_THRESHOLD_HOURS,
            },
            "snapshots": records,
            "presence": presence,
        })

    def _serve_current(self):
        # Load just today + yesterday to find the latest snapshot
        records, _ = load_snapshots(24)
        if records:
            self._respond(200, records[-1])
        else:
            self._respond(200, {"error": "no data", "timestamp": None, "rooms": [], "weather": {}})

    def _serve_presence(self):
        state = load_current_presence()
        if state:
            self._respond(200, state)
        else:
            self._respond(200, {"error": "no presence data"})

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
    print(f"Nest Dashboard running on http://0.0.0.0:{PORT}", flush=True)
    print(f"  Data dir: {HISTORY_DIR}", flush=True)
    print(f"  Access via Tailscale IP or localhost", flush=True)

    def shutdown(signum, frame):
        print(f"\nShutting down (signal {signum})...", flush=True)
        # Run shutdown in a thread to avoid deadlock with serve_forever's lock
        threading.Thread(target=server.shutdown, daemon=True).start()

    signal.signal(signal.SIGTERM, shutdown)
    signal.signal(signal.SIGINT, shutdown)

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        print("Server stopped.", flush=True)
        sys.exit(0)


# ---------------------------------------------------------------------------
# Embedded HTML Dashboard
# ---------------------------------------------------------------------------

DASHBOARD_HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Nest Climate Dashboard</title>
<link rel="icon" href="data:image/svg+xml,<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 100 100'><text y='.9em' font-size='90'>🌡️</text></svg>">
<script src="https://cdn.jsdelivr.net/npm/chart.js@4"></script>
<script src="https://cdn.jsdelivr.net/npm/luxon@3"></script>
<script src="https://cdn.jsdelivr.net/npm/chartjs-adapter-luxon@1"></script>
<noscript><p style="color:#f87171;text-align:center;margin:2rem">JavaScript is required for charts. Status cards still work via the API.</p></noscript>
<style>
:root {
  --bg: #0f1117;
  --surface: #1a1d27;
  --border: #2a2d3a;
  --text: #e4e4e7;
  --text-muted: #9ca3af;
  --solarium: #3B82F6;
  --living: #0EA5E9;
  --bedroom: #14B8A6;
  --outside: #86EFAC;
}
@media (prefers-color-scheme: light) {
  :root {
    --bg: #f8fafc;
    --surface: #ffffff;
    --border: #e2e8f0;
    --text: #1e293b;
    --text-muted: #64748b;
  }
}
* { margin: 0; padding: 0; box-sizing: border-box; }
body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', system-ui, sans-serif; background: var(--bg); color: var(--text); padding: 1rem; max-width: 1200px; margin: 0 auto; }
h1 { font-size: 1.25rem; font-weight: 600; margin-bottom: 1rem; }
.updated { font-size: 0.75rem; color: var(--text-muted); font-weight: 400; margin-left: 0.5rem; }
.cards { display: grid; grid-template-columns: repeat(auto-fit, minmax(160px, 1fr)); gap: 0.5rem; }
.card { background: var(--surface); border: 1px solid var(--border); border-radius: 8px; padding: 1rem; }
.card-label { font-size: 0.7rem; text-transform: uppercase; letter-spacing: 0.05em; color: var(--text-muted); margin-bottom: 0.25rem; }
.card-value { font-size: 1.75rem; font-weight: 700; }
.card-sub { font-size: 0.8rem; color: var(--text-muted); margin-top: 0.25rem; }
.card-tag { display: inline-block; font-size: 0.6rem; padding: 0.1rem 0.4rem; border-radius: 3px; background: rgba(255,255,255,0.08); color: var(--text-muted); margin-left: 0.4rem; vertical-align: middle; letter-spacing: 0.03em; }
@media (prefers-color-scheme: light) { .card-tag { background: rgba(0,0,0,0.06); } }
.card[data-room="Solarium"] .card-value { color: var(--solarium); }
.card[data-room="Living Room"] .card-value { color: var(--living); }
.card[data-room="Bedroom"] .card-value { color: var(--bedroom); }
.card[data-room="Outside"] .card-value { color: var(--outside); }
.card[data-room="Dylan's Office"] .card-value { color: #7C3AED; }
.card[data-room="Cat Room"] .card-value { color: #F97316; }
.card[data-room="Movie room"] .card-value { color: #FACC15; }
.card[data-room="Basement door"] .card-value { color: #A16207; }
.card[data-room="Basement"] .card-value { color: #92400E; }
.controls-row { display: flex; gap: 1rem; margin-bottom: 1rem; flex-wrap: wrap; align-items: center; }
.controls { display: flex; gap: 0.5rem; flex-wrap: wrap; }
.controls button { background: var(--surface); border: 1px solid var(--border); color: var(--text); padding: 0.4rem 1rem; border-radius: 6px; cursor: pointer; font-size: 0.8rem; }
.controls button.active { background: #3b82f6; border-color: #3b82f6; color: #fff; }
.chart-container { background: var(--surface); border: 1px solid var(--border); border-radius: 8px; padding: 1rem; margin-bottom: 1rem; }
.chart-container h2 { font-size: 0.85rem; font-weight: 600; margin-bottom: 0.75rem; }
.chart-wrap { position: relative; width: 100%; min-height: 300px; }
.chart-wrap.short { min-height: 220px; }
canvas { width: 100% !important; }
.loading { text-align: center; color: var(--text-muted); padding: 2rem; }
.presence-card .card-label { font-size: 0.7rem; text-transform: uppercase; letter-spacing: 0.05em; color: var(--text-muted); margin-bottom: 0.25rem; }
.presence-badge { display: inline-block; padding: 0.15rem 0.5rem; border-radius: 4px; font-size: 0.8rem; font-weight: 600; }
.presence-badge.occupied { background: #22c55e22; color: #22c55e; }
.presence-badge.partial { background: #3b82f622; color: #3b82f6; }
.presence-badge.vacant { background: #6b728022; color: #6b7280; }
.presence-badge.possibly { background: #f59e0b22; color: #f59e0b; }
.presence-people { font-size: 0.8rem; color: var(--text-muted); margin-top: 0.35rem; }
.presence-fresh { font-size: 0.7rem; color: var(--text-muted); margin-top: 0.2rem; }
.presence-legend { font-size: 0.75rem; color: var(--text-muted); margin-bottom: 0.75rem; display: none; gap: 1rem; }
.presence-legend.visible { display: flex; }
.presence-legend span { display: inline-flex; align-items: center; gap: 0.3rem; }
.presence-legend .swatch { display: inline-block; width: 12px; height: 12px; border-radius: 2px; }
.chart-legend { display: flex; flex-wrap: wrap; gap: 0.5rem 1rem; font-size: 0.75rem; padding: 0.5rem 0.25rem 0; align-items: center; }
.legend-group { display: flex; flex-wrap: wrap; gap: 0.25rem 0.75rem; align-items: center; }
.legend-group-label { font-size: 0.65rem; text-transform: uppercase; letter-spacing: 0.06em; color: var(--text-muted); font-weight: 600; margin-right: 0.25rem; padding: 0.1rem 0.4rem; background: rgba(255,255,255,0.05); border-radius: 3px; }
@media (prefers-color-scheme: light) { .legend-group-label { background: rgba(0,0,0,0.05); } }
.legend-item { display: inline-flex; align-items: center; gap: 0.3rem; cursor: pointer; color: var(--text); user-select: none; padding: 0.1rem 0; }
.legend-item.hidden { opacity: 0.35; text-decoration: line-through; }
.legend-swatch { display: inline-block; width: 14px; height: 3px; border-radius: 1px; flex-shrink: 0; }
.location-groups { display: grid; grid-template-columns: 1fr 2fr; gap: 1rem; margin-bottom: 1.25rem; }
.location-groups.single { grid-template-columns: 1fr; }
.location-group { background: var(--surface); border: 1px solid var(--border); border-radius: 10px; padding: 0.75rem 1rem 1rem; }
.location-group-header { display: flex; align-items: center; gap: 0.5rem; margin-bottom: 0.75rem; }
.location-group-title { font-size: 0.8rem; font-weight: 700; text-transform: uppercase; letter-spacing: 0.06em; color: var(--text-muted); }
.location-group .presence-card { background: none; border: none; padding: 0; margin-bottom: 0.75rem; }
.location-group .cards { display: grid; grid-template-columns: repeat(2, 1fr); gap: 0.5rem; margin: 0; }
.location-group.crosstown .cards { grid-template-columns: repeat(auto-fit, minmax(140px, 1fr)); }
.location-group .card { border: 1px solid var(--border); }
</style>
</head>
<body>
<h1>Nest Climate Dashboard <span class="updated" id="lastUpdate"></span></h1>
<div id="locationGroups"><div class="loading">Loading...</div></div>
<div class="controls-row">
  <div class="controls" id="structureControls">
    <button data-structure="all" class="active">Both</button>
    <button data-structure="Philly">Cabin</button>
    <button data-structure="19Crosstown">Crosstown</button>
  </div>
  <div class="controls" id="timeControls">
    <button data-hours="24" class="active">24h</button>
    <button data-hours="168">7d</button>
    <button data-hours="720">30d</button>
    <button data-hours="8760">1Y</button>
  </div>
  <div class="controls" id="toggleControls">
    <button id="setpointToggle">Setpoints</button>
  </div>
</div>
<div class="presence-legend" id="presenceLegend">
  <span><span class="swatch" style="background:rgba(34,197,94,0.15)"></span> Occupied</span>
  <span><span class="swatch" style="background:rgba(59,130,246,0.15)"></span> Partial</span>
  <span><span class="swatch" style="background:rgba(107,114,128,0.12)"></span> Vacant</span>
</div>
<div class="chart-container"><h2>Temperature</h2><div class="chart-wrap"><canvas id="tempChart"></canvas></div><div class="chart-legend" id="tempLegend"></div></div>
<div class="chart-container"><h2>Humidity</h2><div class="chart-wrap"><canvas id="humidChart"></canvas></div><div class="chart-legend" id="humidLegend"></div></div>
<div class="chart-container"><h2>HVAC Duty Cycle</h2><div class="chart-wrap short"><canvas id="hvacChart"></canvas></div><div class="chart-legend" id="hvacLegend"></div></div>

<script>
// Cabin = blues & greens, Crosstown = purples & warm
// Disambiguated names (XTown/Cabin suffix) used in "Both" view
const COLORS = {
  // Cabin (blue → teal → green)
  'Solarium': '#3B82F6',
  'Living Room': '#0EA5E9',
  'Living Room (Cabin)': '#0EA5E9',
  'Bedroom': '#14B8A6',
  'Bedroom (Cabin)': '#14B8A6',
  'Outside': '#86EFAC',
  'Outside (Cabin)': '#86EFAC',
  // Crosstown (violet → rose → vermilion → tangerine → gold → sand → linen → white)
  "Dylan\u2019s Office": '#7C3AED',
  "Dylan\u2019s Office (XTown)": '#7C3AED',
  "Dylan's Office": '#7C3AED',
  "Dylan's Office (XTown)": '#7C3AED',
  'Bedroom (XTown)': '#E11D48',
  'Living Room (XTown)': '#EF4444',
  'Cat Room': '#F97316',
  'Cat Room (XTown)': '#F97316',
  'Movie room': '#FACC15',
  'Movie room (XTown)': '#FACC15',
  'Basement door': '#A16207',
  'Basement door (XTown)': '#A16207',
  'Basement': '#92400E',
  'Basement (XTown)': '#92400E',
  'Outside (Crosstown)': '#FECACA',
};

const STRUCTURES = ['Philly', '19Crosstown'];

let tempChart, humidChart, hvacChart;
let currentHours = 24;
let currentStructure = 'all';
let currentPresence = []; // presence history for overlay
let showSetpoints = false;

// Consistent color cache so random colors don't change on re-render
const colorCache = {};
// For disambiguated names like "Bedroom (XTown)", derive a variant of the base color
const STRUCTURE_COLOR_SHIFT = { 'Cabin': 0, 'XTown': 40 };
function roomColor(name) {
  // In filtered view, bare names like "Living Room" need structure-aware lookup
  const cacheKey = currentStructure + ':' + name;
  if (colorCache[cacheKey]) return colorCache[cacheKey];
  let c;
  // Check structure-specific color first (for colliding names like Living Room, Bedroom)
  if (currentStructure === '19Crosstown') {
    c = COLORS[name + ' (XTown)'] || COLORS[name];
  } else if (currentStructure === 'Philly') {
    c = COLORS[name + ' (Cabin)'] || COLORS[name];
  } else {
    c = COLORS[name];
  }
  c = c || '#' + (Math.random().toString(16) + '000000').slice(2, 8);
  colorCache[cacheKey] = c;
  return c;
}

// Structure-aware color: for filtered views where "Living Room" could be either structure
function roomColorForCard(rawRoomName) {
  const struct = roomStructure(rawRoomName);
  const short = stripPrefix(rawRoomName);
  // Try disambiguated name first, then bare name
  const locSuffix = struct === '19Crosstown' ? 'XTown' : 'Cabin';
  return COLORS[short + ' (' + locSuffix + ')'] || COLORS[short] || roomColor(short);
}

function shiftHue(hex, degrees) {
  // Parse hex to RGB, convert to HSL, shift hue, convert back
  const r = parseInt(hex.slice(1,3), 16) / 255;
  const g = parseInt(hex.slice(3,5), 16) / 255;
  const b = parseInt(hex.slice(5,7), 16) / 255;
  const max = Math.max(r, g, b), min = Math.min(r, g, b);
  let h, s, l = (max + min) / 2;
  if (max === min) { h = s = 0; }
  else {
    const d = max - min;
    s = l > 0.5 ? d / (2 - max - min) : d / (max + min);
    if (max === r) h = ((g - b) / d + (g < b ? 6 : 0)) / 6;
    else if (max === g) h = ((b - r) / d + 2) / 6;
    else h = ((r - g) / d + 4) / 6;
  }
  h = ((h * 360 + degrees) % 360) / 360;
  // HSL to RGB
  function hue2rgb(p, q, t) { if (t < 0) t += 1; if (t > 1) t -= 1; if (t < 1/6) return p + (q - p) * 6 * t; if (t < 1/2) return q; if (t < 2/3) return p + (q - p) * (2/3 - t) * 6; return p; }
  let rr, gg, bb;
  if (s === 0) { rr = gg = bb = l; }
  else { const q = l < 0.5 ? l * (1 + s) : l + s - l * s; const p = 2 * l - q; rr = hue2rgb(p, q, h + 1/3); gg = hue2rgb(p, q, h); bb = hue2rgb(p, q, h - 1/3); }
  return '#' + [rr, gg, bb].map(v => Math.round(v * 255).toString(16).padStart(2, '0')).join('');
}

function stripPrefix(roomName) {
  for (const s of STRUCTURES) {
    if (roomName.startsWith(s + ' ')) return roomName.slice(s.length + 1);
  }
  return roomName;
}

function roomStructure(roomName) {
  for (const s of STRUCTURES) {
    if (roomName.startsWith(s + ' ')) return s;
  }
  // Rooms with no prefix are Nest/Philly (Cabin)
  return 'Philly';
}

function roomLocationLabel(roomName) {
  for (const s of STRUCTURES) {
    if (roomName.startsWith(s + ' ')) return s === 'Philly' ? 'Cabin' : 'Crosstown';
  }
  return 'Cabin';
}

function filterRooms(rooms) {
  if (!rooms) return [];
  if (currentStructure === 'all') return rooms;
  return rooms.filter(r => roomStructure(r.room) === currentStructure);
}

function displayName(roomName) {
  return stripPrefix(roomName);
}

// Pre-compute which short names collide across structures so displayName
// can disambiguate only when needed (called once per refresh cycle).
function computeDisplayCollisions(snapshots) {
  const byStructure = {};
  for (const s of snapshots) {
    for (const r of (s.rooms || [])) {
      const struct = roomStructure(r.room);
      const short = stripPrefix(r.room);
      if (!byStructure[short]) byStructure[short] = new Set();
      byStructure[short].add(struct);
    }
  }
  // A short name collides if it appears in more than one structure
  const collisions = new Set();
  for (const [short, structs] of Object.entries(byStructure)) {
    if (structs.size > 1) collisions.add(short);
  }
  displayName._collisions = collisions;
}

const LOCATION_SHORT = { 'Philly': 'Cabin', '19Crosstown': 'XTown' };

function displayNameFull(roomName) {
  const short = stripPrefix(roomName);
  if (currentStructure !== 'all') return short;
  if (displayName._collisions && displayName._collisions.has(short)) {
    const struct = roomStructure(roomName);
    return short + ' (' + (LOCATION_SHORT[struct] || struct) + ')';
  }
  return short;
}

// Short legend name: strip location identifier, used for chart legends
function displayNameLegend(fullName) {
  return fullName.replace(/ \(Cabin\)$/, '').replace(/ \(XTown\)$/, '').replace(/ \(Crosstown\)$/, '');
}

// Tooltip name: add location identifier for disambiguation
function displayNameTooltip(fullName) {
  if (currentStructure !== 'all') {
    const loc = currentStructure === '19Crosstown' ? 'Crosstown' : 'Cabin';
    return fullName + ' (' + loc + ')';
  }
  // In "Both" view, non-colliding names need location added too
  if (!fullName.includes('(Cabin)') && !fullName.includes('(XTown)') && !fullName.startsWith('Outside')) {
    const struct = _seriesStructureMap[fullName];
    if (struct) {
      const loc = struct === '19Crosstown' ? 'Crosstown' : 'Cabin';
      return fullName + ' (' + loc + ')';
    }
  }
  return fullName;
}

async function fetchData(hours) {
  try {
    const resp = await fetch('/api/data?hours=' + hours);
    return await resp.json();
  } catch (e) {
    console.error('Fetch failed:', e);
    return { snapshots: [], meta: {} };
  }
}

function getWeatherEntries(snapshot) {
  // Returns array of {label, data} for weather card(s).
  // Handles old format (flat dict with temp_f) and new format (dict of dicts).
  const w = snapshot.weather;
  if (!w) return [];
  if (w.temp_f != null) {
    // Old single-location format
    return [{label: 'Outside', data: w}];
  }
  // New per-structure format: {"Philly": {...}, "19Crosstown": {...}}
  const WEATHER_LABELS = { 'Philly': 'Cabin', '19Crosstown': 'Crosstown' };
  const entries = [];
  if (currentStructure === 'all') {
    for (const [name, wd] of Object.entries(w)) {
      const wl = WEATHER_LABELS[name] || name;
      if (wd && wd.temp_f != null) entries.push({label: 'Outside (' + wl + ')', data: wd});
    }
  } else {
    const wd = w[currentStructure];
    const wl = WEATHER_LABELS[currentStructure] || currentStructure;
    if (wd && wd.temp_f != null) entries.push({label: 'Outside (' + wl + ')', data: wd});
  }
  return entries;
}

function renderLocationGroups(snapshot, presenceState) {
  const el = document.getElementById('locationGroups');
  if (!snapshot || !snapshot.rooms) {
    el.innerHTML = '<div class="loading">No data available</div>';
    return;
  }

  const structures = currentStructure === 'all'
    ? [{ key: 'Philly', label: 'Cabin', loc: 'cabin' }, { key: '19Crosstown', label: 'Crosstown', loc: 'crosstown' }]
    : currentStructure === 'Philly'
      ? [{ key: 'Philly', label: 'Cabin', loc: 'cabin' }]
      : [{ key: '19Crosstown', label: 'Crosstown', loc: 'crosstown' }];

  // Weather entries keyed by structure
  const weatherByStruct = {};
  const w = snapshot.weather;
  if (w) {
    if (w.temp_f != null) {
      // Old single-location format
      weatherByStruct['Philly'] = [{ label: 'Outside', data: w }];
    } else {
      for (const [name, wd] of Object.entries(w)) {
        if (wd && wd.temp_f != null) {
          if (!weatherByStruct[name]) weatherByStruct[name] = [];
          weatherByStruct[name].push({ label: 'Outside', data: wd });
        }
      }
    }
  }

  // Crosstown card sort order
  const CROSSTOWN_ORDER = ["Dylan\u2019s Office", "Dylan's Office", "Bedroom", "Living Room", "Cat Room", "Movie room", "Basement door", "Basement"];

  const singleClass = structures.length === 1 ? ' single' : '';
  let html = '<div class="location-groups' + singleClass + '">';
  for (const struct of structures) {
    const groupClass = struct.key === '19Crosstown' ? ' crosstown' : '';
    html += '<div class="location-group' + groupClass + '">';
    html += '<div class="location-group-header"><span class="location-group-title">' + struct.label + '</span>';

    // Presence badge inline in header
    if (presenceState && !presenceState.error) {
      const info = presenceState[struct.loc];
      if (info) {
        const occ = info.occupancy || 'unknown';
        const people = presenceState.people
          ? Object.entries(presenceState.people).filter(([,v]) => v[struct.loc]).map(([k]) => k)
          : [];
        const totalTracked = presenceState.people ? Object.keys(presenceState.people).length : 0;
        const isPartial = occ === 'occupied' && people.length > 0 && people.length < totalTracked;
        const badgeClass = isPartial ? 'partial' : occ === 'occupied' ? 'occupied' : occ === 'confirmed_vacant' ? 'vacant' : 'possibly';
        const badgeText = isPartial ? 'Partial' : occ === 'occupied' ? 'Occupied' : occ === 'confirmed_vacant' ? 'Vacant' : 'Possibly Vacant';
        const fresh = info.fresh !== false ? '' : ' (stale)';
        const sinceChange = info.stateChangedAt ? humanDuration(info.stateChangedAt) : (info.scanAge || '');
        const sinceLabel = info.stateChangedAt ? sinceChange : sinceChange;
        html += '<span class="presence-badge ' + badgeClass + '">' + badgeText + '</span>';
        if (people.length) html += '<span class="presence-people" style="margin:0;font-size:0.7rem">' + people.join(', ') + '</span>';
        if (sinceLabel) html += '<span class="presence-fresh" style="margin:0">' + sinceLabel + fresh + '</span>';
      }
    }
    html += '</div>';

    // Device cards — sorted for Crosstown
    let rooms = (snapshot.rooms || []).filter(r => roomStructure(r.room) === struct.key);
    if (struct.key === '19Crosstown') {
      rooms = rooms.slice().sort((a, b) => {
        const an = displayName(a.room), bn = displayName(b.room);
        const ai = CROSSTOWN_ORDER.indexOf(an), bi = CROSSTOWN_ORDER.indexOf(bn);
        return (ai === -1 ? 999 : ai) - (bi === -1 ? 999 : bi);
      });
    }
    html += '<div class="cards">';
    for (const r of rooms) {
      const label = displayName(r.room);
      const cardColor = roomColorForCard(r.room);
      let hvacLabel = r.eco && r.eco !== 'OFF' ? 'ECO' : (r.hvac || '—');
      if (r.source === 'mysa' && r.duty_pct != null) hvacLabel = r.duty_pct > 0 ? `${r.duty_pct}% duty` : 'OFF';
      const sourceTag = r.source && r.source !== 'nest' ? ` · <span class="card-tag">${r.source}</span>` : '';
      html += `<div class="card">
        <div class="card-label">${label}</div>
        <div class="card-value" style="color:${cardColor}">${(r.temp_f ?? 0).toFixed(1)}°F</div>
        <div class="card-sub">Set: ${(r.setpoint_f ?? 0).toFixed(0)}°F · ${hvacLabel} · ${r.humidity ?? 0}% RH${sourceTag}</div>
      </div>`;
    }
    // Weather card for this structure
    const weatherEntries = weatherByStruct[struct.key] || [];
    for (const { label, data: wd } of weatherEntries) {
      const outsideColor = struct.key === '19Crosstown' ? '#FEDDBA' : '#C9E2FE';
      html += `<div class="card">
        <div class="card-label">${label}</div>
        <div class="card-value" style="color:${outsideColor}">${wd.temp_f.toFixed(1)}°F</div>
        <div class="card-sub">${wd.description || '—'} · ${wd.humidity ?? 0}% RH · ${(wd.wind_mph ?? 0).toFixed(0)} mph</div>
      </div>`;
    }
    html += '</div></div>';
  }
  html += '</div>';
  el.innerHTML = html;

  // Update timestamp
  const ts = snapshot.timestamp;
  if (ts) {
    const d = new Date(ts);
    document.getElementById('lastUpdate').textContent = 'Updated ' + d.toLocaleTimeString();
  }
}

// Maps display name → structure key for unambiguous rooms (used by seriesGroup)
let _seriesStructureMap = {};

function buildTimeSeries(snapshots) {
  // Collect room names (filtered + disambiguated display names)
  const roomNames = new Set();
  _seriesStructureMap = {};
  for (const s of snapshots) {
    for (const r of filterRooms(s.rooms || [])) {
      const dname = displayNameFull(r.room);
      roomNames.add(dname);
      _seriesStructureMap[dname] = roomStructure(r.room);
    }
  }

  // Collect weather series names from all snapshots
  const weatherNames = new Set();
  for (const s of snapshots) {
    for (const {label} of getWeatherEntries(s)) {
      weatherNames.add(label);
      _seriesStructureMap[label] = label.includes('Crosstown') ? '19Crosstown' : 'Philly';
    }
  }

  const series = {};
  for (const name of roomNames) {
    series[name] = { temps: [], humids: [], setpoints: [] };
  }
  for (const name of weatherNames) {
    series[name] = { temps: [], humids: [] };
  }

  for (const s of snapshots) {
    const ts = s.timestamp;
    for (const r of filterRooms(s.rooms || [])) {
      const name = displayNameFull(r.room);
      if (!series[name]) continue;
      series[name].temps.push({ x: ts, y: r.temp_f });
      series[name].humids.push({ x: ts, y: r.humidity });
      series[name].setpoints.push({ x: ts, y: r.setpoint_f });
    }
    for (const {label, data: w} of getWeatherEntries(s)) {
      if (!series[label]) series[label] = { temps: [], humids: [] };
      series[label].temps.push({ x: ts, y: w.temp_f });
      series[label].humids.push({ x: ts, y: w.humidity });
    }
  }
  return series;
}

function computeHvacDuty(snapshots) {
  // For each room, bucket snapshots by hour.
  // Duty cycle = count(hvac is active) / total snapshots in that hour bucket.
  // Active states: HEATING, COOLING, AUTO, FAN, DRY (anything not OFF/?)
  const roomNames = new Set();
  for (const s of snapshots) {
    for (const r of filterRooms(s.rooms || [])) roomNames.add(displayNameFull(r.room));
  }

  const buckets = {}; // room -> hourKey -> {dutySum: n, total: n}
  for (const name of roomNames) buckets[name] = {};

  for (const s of snapshots) {
    const d = new Date(s.timestamp);
    const hourKey = d.toISOString().slice(0, 13) + ':00:00Z'; // YYYY-MM-DDTHH:00:00Z
    for (const r of filterRooms(s.rooms || [])) {
      const name = displayNameFull(r.room);
      if (!buckets[name]) continue;
      if (!buckets[name][hourKey]) buckets[name][hourKey] = { dutySum: 0, total: 0 };
      buckets[name][hourKey].total++;
      // Use real duty_pct if available (Mysa), otherwise binary 100/0 from HVAC status
      if (r.duty_pct != null) {
        buckets[name][hourKey].dutySum += r.duty_pct;
      } else if (r.hvac && r.hvac !== 'OFF' && r.hvac !== '?') {
        buckets[name][hourKey].dutySum += 100;
      }
    }
  }

  const result = {};
  for (const name of roomNames) {
    result[name] = [];
    for (const [hourKey, b] of Object.entries(buckets[name]).sort()) {
      result[name].push({ x: hourKey, y: Math.round(b.dutySum / b.total) });
    }
  }
  return result;
}

// ── Presence rendering ───────────────────────────────────────────────────
const LOCATION_MAP = { 'Philly': 'cabin', '19Crosstown': 'crosstown' };
const LOCATION_LABELS = { cabin: 'Cabin', crosstown: 'Crosstown' };

function humanDuration(isoTimestamp) {
  if (!isoTimestamp) return '';
  const ms = Date.now() - new Date(isoTimestamp).getTime();
  if (ms < 0) return 'just now';
  const mins = Math.floor(ms / 60000);
  if (mins < 1) return 'just now';
  if (mins < 60) return mins + 'min';
  const hrs = Math.floor(mins / 60);
  if (hrs < 24) return hrs + 'h ' + (mins % 60) + 'min';
  const days = Math.floor(hrs / 24);
  return days + 'd ' + (hrs % 24) + 'h';
}

// Presence rendering is now integrated into renderLocationGroups

// Chart.js plugin: occupancy background bands
const presenceOverlayPlugin = {
  id: 'presenceOverlay',
  beforeDraw(chart) {
    if (currentStructure === 'all' || !currentPresence.length) return;
    const loc = LOCATION_MAP[currentStructure];
    if (!loc) return;

    const { ctx, chartArea: { left, right, top, bottom }, scales: { x: xScale } } = chart;
    const sorted = currentPresence.filter(p => p[loc]).sort((a, b) => a.timestamp.localeCompare(b.timestamp));
    if (!sorted.length) return;

    ctx.save();
    for (let i = 0; i < sorted.length; i++) {
      const rec = sorted[i];
      const locData = rec[loc];
      const occ = locData.occupancy;
      const peopleCt = (locData.people || []).length;
      const isPartial = occ === 'occupied' && peopleCt === 1;
      const x1 = xScale.getPixelForValue(new Date(rec.timestamp).getTime());
      const nextTs = sorted[i + 1] ? new Date(sorted[i + 1].timestamp).getTime() : Date.now();
      const x2 = xScale.getPixelForValue(nextTs);
      const clampX1 = Math.max(x1, left);
      const clampX2 = Math.min(x2, right);
      if (clampX2 <= clampX1) continue;

      ctx.fillStyle = isPartial ? 'rgba(59,130,246,0.07)' : occ === 'occupied' ? 'rgba(34,197,94,0.07)' : occ === 'confirmed_vacant' ? 'rgba(107,114,128,0.06)' : 'rgba(245,158,11,0.05)';
      ctx.fillRect(clampX1, top, clampX2 - clampX1, bottom - top);
    }
    ctx.restore();
  }
};

const chartDefaults = {
  responsive: true,
  maintainAspectRatio: false,
  animation: { duration: 300 },
  plugins: {
    legend: {
      display: false, // We use custom HTML legends
    },
    tooltip: {
      callbacks: {
        label: function(context) {
          const ds = context.dataset;
          const name = ds._fullName || ds.label;
          const tooltipName = displayNameTooltip(name);
          return tooltipName + ': ' + context.formattedValue;
        },
      },
    },
  },
  scales: {
    x: {
      type: 'time',
      grid: { color: 'rgba(255,255,255,0.05)' },
      ticks: { color: getComputedStyle(document.documentElement).getPropertyValue('--text-muted').trim() || '#9ca3af', font: { size: 10 } },
    },
    y: {
      grid: { color: 'rgba(255,255,255,0.05)' },
      ticks: { color: getComputedStyle(document.documentElement).getPropertyValue('--text-muted').trim() || '#9ca3af', font: { size: 10 } },
    },
  },
};

// Render grouped HTML legend for a chart
function renderGroupedLegend(chart, containerId) {
  const el = document.getElementById(containerId);
  if (!el) return;
  const datasets = chart.data.datasets;
  const showGroups = currentStructure === 'all' && datasets.some(d => d._group);

  let html = '';
  let lastGroup = null;
  for (let i = 0; i < datasets.length; i++) {
    const ds = datasets[i];
    const meta = chart.getDatasetMeta(i);
    const hidden = meta.hidden || ds.hidden;
    const group = ds._group || '';

    // Group header
    if (showGroups && group !== lastGroup) {
      if (lastGroup !== null) html += '</div>';
      html += '<div class="legend-group"><span class="legend-group-label">' + group + '</span>';
      lastGroup = group;
    }

    const color = ds.borderColor || ds.backgroundColor;
    const isDashed = !!ds.borderDash;
    const swatchStyle = isDashed
      ? 'background:none;border-top:2px dashed ' + color
      : 'background:' + color;
    html += '<span class="legend-item' + (hidden ? ' hidden' : '') + '" data-index="' + i + '">'
      + '<span class="legend-swatch" style="' + swatchStyle + '"></span>'
      + ds.label + '</span>';
  }
  if (showGroups && lastGroup !== null) html += '</div>';
  el.innerHTML = html;

  // Click to toggle dataset visibility
  el.querySelectorAll('.legend-item').forEach(item => {
    item.addEventListener('click', () => {
      const idx = parseInt(item.dataset.index);
      const meta = chart.getDatasetMeta(idx);
      meta.hidden = !meta.hidden;
      item.classList.toggle('hidden', meta.hidden);
      chart.update();
    });
  });
}

function createLineChart(ctx, datasets, yLabel) {
  return new Chart(ctx, {
    type: 'line',
    data: { datasets },
    options: {
      ...chartDefaults,
      scales: {
        ...chartDefaults.scales,
        y: { ...chartDefaults.scales.y, title: { display: true, text: yLabel, color: getComputedStyle(document.documentElement).getPropertyValue('--text-muted').trim() || '#9ca3af' } },
      },
      elements: { point: { radius: 0, hitRadius: 6 }, line: { tension: 0.3, borderWidth: 2 } },
    },
    plugins: [presenceOverlayPlugin],
  });
}

function createBarChart(ctx, datasets) {
  return new Chart(ctx, {
    type: 'bar',
    data: { datasets },
    options: {
      ...chartDefaults,
      scales: {
        ...chartDefaults.scales,
        y: { ...chartDefaults.scales.y, title: { display: true, text: 'Active %', color: getComputedStyle(document.documentElement).getPropertyValue('--text-muted').trim() || '#9ca3af' }, min: 0, max: 100 },
      },
    },
    plugins: [presenceOverlayPlugin],
  });
}

async function refresh() {
  const data = await fetchData(currentHours);
  const snaps = data.snapshots || [];
  currentPresence = data.presence || [];

  // Pre-compute which room short names collide across structures
  computeDisplayCollisions(snaps);

  // Fetch presence state and render combined location groups
  let presState = null;
  try {
    const presResp = await fetch('/api/presence');
    presState = await presResp.json();
  } catch {}

  // Presence legend: show when a specific structure is selected and we have presence data
  const legend = document.getElementById('presenceLegend');
  legend.classList.toggle('visible', currentStructure !== 'all' && currentPresence.length > 0);

  // Render location groups (presence + device cards)
  if (snaps.length > 0) renderLocationGroups(snaps[snaps.length - 1], presState);

  if (typeof Chart === 'undefined') return; // CDN unreachable

  const series = buildTimeSeries(snaps);
  const hvacDuty = computeHvacDuty(snaps);

  // Helper: determine structure group for a series name
  function seriesGroup(name) {
    if (name.includes('(XTown)') || name.includes('(Crosstown)')) return 'Crosstown';
    if (name.includes('(Cabin)')) return 'Cabin';
    // In filtered view, check structure filter
    if (currentStructure === '19Crosstown') return 'Crosstown';
    if (currentStructure === 'Philly') return 'Cabin';
    // Unambiguous names in "Both" — look up from snapshot data
    const struct = _seriesStructureMap[name];
    return struct === '19Crosstown' ? 'Crosstown' : 'Cabin';
  }

  // Sort datasets: Cabin first, then Crosstown, alphabetical within group
  function sortByGroup(entries) {
    return entries.sort((a, b) => {
      const ga = seriesGroup(a[0]), gb = seriesGroup(b[0]);
      if (ga !== gb) return ga === 'Cabin' ? -1 : 1;
      return a[0].localeCompare(b[0]);
    });
  }

  // Temperature datasets
  const tempDS = [];
  for (const [name, s] of sortByGroup(Object.entries(series))) {
    if (s.temps.length === 0) continue;
    const color = roomColor(name);
    tempDS.push({
      label: displayNameLegend(name),
      data: s.temps,
      borderColor: color,
      backgroundColor: color + '22',
      fill: false,
      _group: seriesGroup(name),
      _fullName: name,
    });
    // Setpoint lines (dotted) for rooms (not outside)
    if (!name.startsWith('Outside') && s.setpoints && s.setpoints.length > 0) {
      tempDS.push({
        label: displayNameLegend(name) + ' setpoint',
        data: s.setpoints,
        borderColor: color,
        borderDash: [4, 4],
        borderWidth: 1,
        fill: false,
        pointRadius: 0,
        hidden: !showSetpoints,
        _group: seriesGroup(name),
        _fullName: name + ' setpoint',
      });
    }
  }

  // Humidity datasets
  const humidDS = [];
  for (const [name, s] of sortByGroup(Object.entries(series))) {
    if (s.humids.length === 0) continue;
    const color = roomColor(name);
    humidDS.push({
      label: displayNameLegend(name),
      data: s.humids,
      borderColor: color,
      backgroundColor: color + '22',
      fill: false,
      _group: seriesGroup(name),
      _fullName: name,
    });
  }

  // HVAC duty datasets
  const hvacDS = [];
  for (const [name, buckets] of sortByGroup(Object.entries(hvacDuty))) {
    if (buckets.length === 0) continue;
    const color = roomColor(name);
    hvacDS.push({
      label: displayNameLegend(name),
      data: buckets,
      backgroundColor: color + '99',
      borderColor: color,
      borderWidth: 1,
      _group: seriesGroup(name),
      _fullName: name,
    });
  }

  // Destroy and recreate charts (simpler than updating)
  if (tempChart) tempChart.destroy();
  if (humidChart) humidChart.destroy();
  if (hvacChart) hvacChart.destroy();

  tempChart = createLineChart(document.getElementById('tempChart'), tempDS, '°F');
  renderGroupedLegend(tempChart, 'tempLegend');
  humidChart = createLineChart(document.getElementById('humidChart'), humidDS, '% RH');
  renderGroupedLegend(humidChart, 'humidLegend');
  hvacChart = createBarChart(document.getElementById('hvacChart'), hvacDS);
  renderGroupedLegend(hvacChart, 'hvacLegend');
}

// Structure filter buttons
document.getElementById('structureControls').addEventListener('click', e => {
  if (e.target.tagName !== 'BUTTON') return;
  document.querySelectorAll('#structureControls button').forEach(b => b.classList.remove('active'));
  e.target.classList.add('active');
  currentStructure = e.target.dataset.structure;
  refresh();
});

// Time range buttons
document.getElementById('timeControls').addEventListener('click', e => {
  if (e.target.tagName !== 'BUTTON') return;
  document.querySelectorAll('#timeControls button').forEach(b => b.classList.remove('active'));
  e.target.classList.add('active');
  currentHours = parseInt(e.target.dataset.hours);
  refresh();
});

// Setpoint toggle
document.getElementById('setpointToggle').addEventListener('click', e => {
  showSetpoints = !showSetpoints;
  e.target.classList.toggle('active', showSetpoints);
  refresh();
});

// Initial load + auto-refresh every 5 minutes
refresh();
setInterval(refresh, 5 * 60 * 1000);
</script>
</body>
</html>
"""

if __name__ == "__main__":
    run()
