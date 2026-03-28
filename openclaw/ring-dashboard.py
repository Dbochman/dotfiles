#!/usr/bin/env python3
"""Dog Walk & Roomba Dashboard — single-file HTTP server with embedded UI.

Serves a JSON API and Chart.js dashboard for dog walk history and Roomba operations.
Reads JSONL events from ~/.openclaw/ring-listener/history/YYYY-MM-DD.jsonl
and current state from ~/.openclaw/ring-listener/state.json

Same architecture as nest-dashboard.py. Intended for Tailscale-only access.
"""

import json
import os
import signal
import sys
from datetime import datetime, timedelta, timezone
from http.server import HTTPServer, BaseHTTPRequestHandler
from socketserver import ThreadingMixIn
from urllib.parse import urlparse, parse_qs

HISTORY_DIR = os.path.expanduser("~/.openclaw/ring-listener/history")
STATE_FILE = os.path.expanduser("~/.openclaw/ring-listener/state.json")
PORT = 8552
MAX_DAYS = 365


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
<style>
:root {
  --bg: #0f1117;
  --surface: #1a1d27;
  --border: #2a2d3a;
  --text: #e4e4e7;
  --text-muted: #9ca3af;
  --green: #22c55e;
  --amber: #f59e0b;
  --red: #ef4444;
  --blue: #3b82f6;
  --purple: #8b5cf6;
  --teal: #14b8a6;
  --pink: #ec4899;
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
h2 { font-size: 0.85rem; font-weight: 600; margin-bottom: 0.75rem; }
.updated { font-size: 0.75rem; color: var(--text-muted); font-weight: 400; margin-left: 0.5rem; }

/* Status cards */
.cards { display: grid; grid-template-columns: repeat(auto-fit, minmax(220px, 1fr)); gap: 0.75rem; margin-bottom: 1.25rem; }
.card { background: var(--surface); border: 1px solid var(--border); border-radius: 8px; padding: 1rem; }
.card-label { font-size: 0.7rem; text-transform: uppercase; letter-spacing: 0.05em; color: var(--text-muted); margin-bottom: 0.25rem; }
.card-value { font-size: 1.75rem; font-weight: 700; }
.card-sub { font-size: 0.8rem; color: var(--text-muted); margin-top: 0.25rem; }
.card-tag { display: inline-block; font-size: 0.6rem; padding: 0.1rem 0.4rem; border-radius: 3px; background: rgba(255,255,255,0.08); color: var(--text-muted); margin-left: 0.4rem; vertical-align: middle; letter-spacing: 0.03em; }
@media (prefers-color-scheme: light) { .card-tag { background: rgba(0,0,0,0.06); } }

/* Controls */
.controls { display: flex; gap: 0.5rem; margin-bottom: 1rem; flex-wrap: wrap; }
.controls button { background: var(--surface); border: 1px solid var(--border); color: var(--text); padding: 0.4rem 1rem; border-radius: 6px; cursor: pointer; font-size: 0.8rem; }
.controls button.active { background: #3b82f6; border-color: #3b82f6; color: #fff; }

/* Charts */
.chart-container { background: var(--surface); border: 1px solid var(--border); border-radius: 8px; padding: 1rem; margin-bottom: 1rem; }
.chart-wrap { position: relative; width: 100%; min-height: 300px; }
.chart-wrap.short { min-height: 220px; }
canvas { width: 100% !important; }
.loading { text-align: center; color: var(--text-muted); padding: 2rem; }

/* Walk log table */
.walk-log { background: var(--surface); border: 1px solid var(--border); border-radius: 8px; padding: 1rem; margin-bottom: 1rem; overflow-x: auto; }
.walk-log table { width: 100%; border-collapse: collapse; font-size: 0.8rem; }
.walk-log th { text-align: left; padding: 0.5rem 0.75rem; color: var(--text-muted); font-weight: 600; font-size: 0.7rem; text-transform: uppercase; letter-spacing: 0.05em; border-bottom: 1px solid var(--border); }
.walk-log td { padding: 0.5rem 0.75rem; border-bottom: 1px solid var(--border); }
.walk-log tr:last-child td { border-bottom: none; }

/* Badges */
.badge { display: inline-block; padding: 0.1rem 0.5rem; border-radius: 4px; font-size: 0.7rem; font-weight: 600; }
.badge-green { background: #22c55e22; color: #22c55e; }
.badge-amber { background: #f59e0b22; color: #f59e0b; }
.badge-red { background: #ef444422; color: #ef4444; }
.badge-blue { background: #3b82f622; color: #3b82f6; }
.badge-purple { background: #8b5cf622; color: #8b5cf6; }
.badge-gray { background: #6b728022; color: #6b7280; }
</style>
</head>
<body>
<h1>Dog Walk Dashboard <span class="updated" id="lastUpdate"></span></h1>

<div class="cards" id="statusCards"><div class="loading">Loading...</div></div>

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

<div class="walk-log"><h2>Recent Walks</h2><table id="walkTable"><thead><tr><th>Date</th><th>Location</th><th>Duration</th><th>Return Signal</th><th>Walkers</th><th>Roombas</th></tr></thead><tbody id="walkBody"></tbody></table></div>

<div class="chart-container"><h2>Walk Duration (minutes)</h2><div class="chart-wrap short"><canvas id="durationChart"></canvas></div></div>
<div class="chart-container"><h2>Return Signal Distribution</h2><div class="chart-wrap short"><canvas id="signalChart"></canvas></div></div>
<div class="chart-container"><h2>Detection Funnel</h2><div class="chart-wrap short"><canvas id="funnelChart"></canvas></div></div>
<div class="chart-container"><h2>Walks per Day</h2><div class="chart-wrap short"><canvas id="walksPerDayChart"></canvas></div></div>

<script>
const SIGNAL_COLORS = {
  'network_wifi': '#22c55e',
  'ring_motion': '#3b82f6',
  'findmy': '#8b5cf6',
  'timeout': '#ef4444',
};
const SIGNAL_LABELS = {
  'network_wifi': 'WiFi',
  'ring_motion': 'Ring Motion',
  'findmy': 'FindMy',
  'timeout': 'Timeout',
};
const LOCATION_COLORS = { 'cabin': '#FF8C00', 'crosstown': '#4A90D9' };

let durationChart, signalChart, funnelChart, walksPerDayChart;
let currentDays = 7;
let currentLocation = 'all';

function fmtTime(iso) {
  if (!iso) return '—';
  const d = new Date(iso);
  return d.toLocaleDateString('en-US', { month: 'short', day: 'numeric' }) + ' ' +
         d.toLocaleTimeString('en-US', { hour: 'numeric', minute: '2-digit' });
}

function fmtDuration(mins) {
  if (mins == null) return '—';
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

function renderStatusCards(state) {
  const el = document.getElementById('statusCards');
  if (!state || state.error) { el.innerHTML = '<div class="card"><div class="card-label">Status</div><div class="card-value" style="color:var(--text-muted)">No data</div></div>'; return; }

  let html = '';
  const walk = state.dog_walk || {};
  const isActive = walk.active;

  // Walk status card
  html += '<div class="card"><div class="card-label">Current Walk</div>';
  if (isActive) {
    const elapsed = walk.departed_at ? Math.round((Date.now() - new Date(walk.departed_at).getTime()) / 60000) : 0;
    html += '<div class="card-value" style="color:var(--green)">' + elapsed + 'm</div>';
    html += '<div class="card-sub">Active at ' + (walk.location || '?') + '</div>';
    if (walk.walkers && walk.walkers.length) html += '<div class="card-sub">Walkers: ' + walk.walkers.join(', ') + '</div>';
  } else {
    html += '<div class="card-value" style="color:var(--text-muted)">None</div>';
    if (walk.returned_at) html += '<div class="card-sub">Last: ' + fmtTime(walk.returned_at) + '</div>';
  }
  html += '</div>';

  // Last walk duration
  if (walk.walk_duration_minutes != null && !isActive) {
    html += '<div class="card"><div class="card-label">Last Walk</div>';
    html += '<div class="card-value" style="color:var(--blue)">' + fmtDuration(walk.walk_duration_minutes) + '</div>';
    html += '<div class="card-sub">' + (walk.location || '') + '<span class="card-tag">' + (SIGNAL_LABELS[walk.return_signal] || walk.return_signal || '?') + '</span></div>';
    html += '</div>';
  }

  // Roomba status per location
  const roombas = state.roombas || {};
  for (const [loc, rb] of Object.entries(roombas)) {
    const status = rb.status || 'unknown';
    const color = status === 'running' ? 'var(--green)' : 'var(--text-muted)';
    html += '<div class="card"><div class="card-label">Roombas ' + loc + '</div>';
    html += '<div class="card-value" style="color:' + color + '">' + status.charAt(0).toUpperCase() + status.slice(1) + '</div>';
    if (rb.last_command_result) {
      const ok = rb.last_command_result.success;
      html += '<div class="card-sub">Last cmd: <span class="badge ' + (ok ? 'badge-green' : 'badge-red') + '">' + (ok ? 'OK' : 'Failed') + '</span></div>';
    }
    html += '</div>';
  }

  // FindMy polling
  const fm = state.findmy_polling || {};
  if (fm.active) {
    html += '<div class="card"><div class="card-label">Return Monitor</div>';
    html += '<div class="card-value" style="color:var(--purple)">Active</div>';
    html += '<div class="card-sub">Polls: ' + (fm.polls || 0) + '</div>';
    if (fm.last_result && fm.last_result.description) html += '<div class="card-sub">' + fm.last_result.description + '</div>';
    html += '</div>';
  }

  el.innerHTML = html;
}

function renderWalkTable(events) {
  const walks = [];
  // Pair departures with docks
  const departures = events.filter(e => e.event_type === 'departure');
  const docks = events.filter(e => e.event_type === 'dock' || e.event_type === 'dock_timeout');

  for (const dep of departures) {
    const loc = dep.dog_walk && dep.dog_walk.location;
    const depTime = dep.dog_walk && dep.dog_walk.departed_at;
    // Find matching dock
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

  walks.reverse(); // most recent first
  const tbody = document.getElementById('walkBody');
  if (!walks.length) { tbody.innerHTML = '<tr><td colspan="6" style="color:var(--text-muted);text-align:center">No walks in this period</td></tr>'; return; }

  tbody.innerHTML = walks.slice(0, 50).map(w => {
    const sig = w.return_signal;
    const sigBadge = sig ? '<span class="badge badge-' + (sig === 'timeout' ? 'red' : sig === 'findmy' ? 'purple' : sig === 'ring_motion' ? 'blue' : 'green') + '">' + (SIGNAL_LABELS[sig] || sig) + '</span>' : '—';
    const roombaBadge = w.roomba_ok === true ? '<span class="badge badge-green">OK</span>' : w.roomba_ok === false ? '<span class="badge badge-red">Failed</span>' : '—';
    const manual = w.people === 0 ? ' <span class="card-tag">manual</span>' : '';
    return '<tr><td>' + fmtTime(w.departed_at) + '</td><td>' + (w.location || '?') + manual + '</td><td>' + fmtDuration(w.duration) + '</td><td>' + sigBadge + '</td><td>' + (w.walkers.length ? w.walkers.join(', ') : '—') + '</td><td>' + roombaBadge + '</td></tr>';
  }).join('');
}

function buildDurationData(events) {
  const docks = filterByLocation(events).filter(e => (e.event_type === 'dock' || e.event_type === 'dock_timeout') && e.dog_walk && e.dog_walk.walk_duration_minutes != null);
  return docks.map(d => ({
    x: d.timestamp,
    y: d.dog_walk.walk_duration_minutes,
    location: d.dog_walk.location,
  }));
}

function buildSignalData(events) {
  const docks = filterByLocation(events).filter(e => (e.event_type === 'dock' || e.event_type === 'dock_timeout') && e.dog_walk && e.dog_walk.return_signal);
  const counts = {};
  for (const d of docks) {
    const sig = d.dog_walk.return_signal;
    counts[sig] = (counts[sig] || 0) + 1;
  }
  return counts;
}

function buildFunnelData(events) {
  const filtered = filterByLocation(events);
  const skips = filtered.filter(e => e.event_type === 'departure_skip');
  const departures = filtered.filter(e => e.event_type === 'departure').length;
  const docks = filtered.filter(e => e.event_type === 'dock' || e.event_type === 'dock_timeout').length;

  const skipReasons = {};
  for (const s of skips) {
    const reason = s.skip_reason || 'unknown';
    skipReasons[reason] = (skipReasons[reason] || 0) + 1;
  }
  return { skipReasons, departures, docks };
}

function buildWalksPerDay(events) {
  const departures = filterByLocation(events).filter(e => e.event_type === 'departure');
  const byDay = {};
  for (const d of departures) {
    const day = d.timestamp ? d.timestamp.slice(0, 10) : null;
    if (day) byDay[day] = (byDay[day] || 0) + 1;
  }
  return Object.entries(byDay).sort().map(([day, count]) => ({ x: day + 'T12:00:00Z', y: count }));
}

function destroyCharts() {
  if (durationChart) { durationChart.destroy(); durationChart = null; }
  if (signalChart) { signalChart.destroy(); signalChart = null; }
  if (funnelChart) { funnelChart.destroy(); funnelChart = null; }
  if (walksPerDayChart) { walksPerDayChart.destroy(); walksPerDayChart = null; }
}

function getStyle(prop) { return getComputedStyle(document.documentElement).getPropertyValue(prop).trim(); }

function chartDefaults() {
  return {
    responsive: true, maintainAspectRatio: false,
    animation: { duration: 300 },
    plugins: { legend: { labels: { color: getStyle('--text'), boxWidth: 12, padding: 10, font: { size: 11 } } } },
    scales: {
      x: { grid: { color: 'rgba(255,255,255,0.05)' }, ticks: { color: getStyle('--text-muted'), font: { size: 10 } } },
      y: { grid: { color: 'rgba(255,255,255,0.05)' }, ticks: { color: getStyle('--text-muted'), font: { size: 10 } } },
    }
  };
}

function renderCharts(events) {
  destroyCharts();
  const durationData = buildDurationData(events);
  const signalCounts = buildSignalData(events);
  const funnel = buildFunnelData(events);
  const walksPerDay = buildWalksPerDay(events);

  // Duration scatter chart
  const durationByLoc = {};
  for (const d of durationData) {
    const loc = d.location || 'unknown';
    if (!durationByLoc[loc]) durationByLoc[loc] = [];
    durationByLoc[loc].push({ x: d.x, y: d.y });
  }
  const durationDatasets = Object.entries(durationByLoc).map(([loc, pts]) => ({
    label: loc.charAt(0).toUpperCase() + loc.slice(1),
    data: pts,
    backgroundColor: LOCATION_COLORS[loc] || '#6b7280',
    borderColor: LOCATION_COLORS[loc] || '#6b7280',
    pointRadius: 5, pointHoverRadius: 7, showLine: false,
  }));
  const defDur = chartDefaults();
  defDur.scales.x.type = 'time';
  defDur.scales.y.title = { display: true, text: 'Minutes', color: getStyle('--text-muted'), font: { size: 10 } };
  durationChart = new Chart(document.getElementById('durationChart'), {
    type: 'scatter', data: { datasets: durationDatasets }, options: defDur,
  });

  // Return signal doughnut
  const sigLabels = Object.keys(signalCounts).map(k => SIGNAL_LABELS[k] || k);
  const sigValues = Object.values(signalCounts);
  const sigColors = Object.keys(signalCounts).map(k => SIGNAL_COLORS[k] || '#6b7280');
  signalChart = new Chart(document.getElementById('signalChart'), {
    type: 'doughnut',
    data: { labels: sigLabels, datasets: [{ data: sigValues, backgroundColor: sigColors, borderWidth: 0 }] },
    options: { responsive: true, maintainAspectRatio: false, animation: { duration: 300 },
      plugins: { legend: { position: 'right', labels: { color: getStyle('--text'), padding: 12, font: { size: 11 } } } } },
  });

  // Detection funnel bar chart
  const funnelLabels = [];
  const funnelValues = [];
  const funnelColors = [];
  const SKIP_LABELS = { 'outside_walk_hours': 'Outside Hours', 'confirmed_vacant': 'Vacant', 'wifi_present': 'WiFi Present', 'cabin_prompt_suppressed': 'Prompt Suppressed' };
  const SKIP_COLORS = { 'outside_walk_hours': '#6b7280', 'confirmed_vacant': '#9ca3af', 'wifi_present': '#3b82f6', 'cabin_prompt_suppressed': '#f59e0b' };
  for (const [reason, count] of Object.entries(funnel.skipReasons).sort((a, b) => b[1] - a[1])) {
    funnelLabels.push(SKIP_LABELS[reason] || reason);
    funnelValues.push(count);
    funnelColors.push(SKIP_COLORS[reason] || '#6b7280');
  }
  funnelLabels.push('Departures', 'Docks');
  funnelValues.push(funnel.departures, funnel.docks);
  funnelColors.push('#22c55e', '#14b8a6');

  const defFunnel = chartDefaults();
  delete defFunnel.scales.x.type;
  defFunnel.indexAxis = 'y';
  defFunnel.scales.x.title = { display: true, text: 'Count', color: getStyle('--text-muted'), font: { size: 10 } };
  defFunnel.plugins.legend = { display: false };
  funnelChart = new Chart(document.getElementById('funnelChart'), {
    type: 'bar',
    data: { labels: funnelLabels, datasets: [{ data: funnelValues, backgroundColor: funnelColors, borderWidth: 0, borderRadius: 3 }] },
    options: defFunnel,
  });

  // Walks per day bar chart
  const defWpd = chartDefaults();
  defWpd.scales.x.type = 'time';
  defWpd.scales.x.time = { unit: 'day' };
  defWpd.scales.y.beginAtZero = true;
  defWpd.scales.y.ticks.stepSize = 1;
  defWpd.plugins.legend = { display: false };
  walksPerDayChart = new Chart(document.getElementById('walksPerDayChart'), {
    type: 'bar',
    data: { datasets: [{ data: walksPerDay, backgroundColor: '#3b82f699', borderRadius: 3 }] },
    options: defWpd,
  });
}

async function refresh() {
  try {
    const [eventsResp, stateResp] = await Promise.all([
      fetch('/api/events?days=' + currentDays),
      fetch('/api/current'),
    ]);
    const eventsData = await eventsResp.json();
    const state = await stateResp.json();
    const events = eventsData.events || [];

    document.getElementById('lastUpdate').textContent = 'Updated ' + new Date().toLocaleTimeString();

    renderStatusCards(state);
    renderWalkTable(filterByLocation(events));
    renderCharts(events);
  } catch (err) {
    console.error('Refresh failed:', err);
  }
}

document.getElementById('locationControls').addEventListener('click', e => {
  if (!e.target.dataset.location) return;
  currentLocation = e.target.dataset.location;
  document.querySelectorAll('#locationControls button').forEach(b => b.classList.toggle('active', b.dataset.location === currentLocation));
  refresh();
});
document.getElementById('timeControls').addEventListener('click', e => {
  if (!e.target.dataset.days) return;
  currentDays = parseInt(e.target.dataset.days);
  document.querySelectorAll('#timeControls button').forEach(b => b.classList.toggle('active', parseInt(b.dataset.days) === currentDays));
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
