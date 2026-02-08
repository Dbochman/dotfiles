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
from datetime import datetime, timedelta, timezone
from http.server import HTTPServer, BaseHTTPRequestHandler
from socketserver import ThreadingMixIn
from urllib.parse import urlparse, parse_qs

HISTORY_DIR = os.path.expanduser("~/.openclaw/nest-history")
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
        self._respond(200, {
            "meta": {
                "hours": clamped_hours,
                "count": len(records),
                "downsampled": clamped_hours > DOWNSAMPLE_THRESHOLD_HOURS,
            },
            "snapshots": records,
        })

    def _serve_current(self):
        # Load just today + yesterday to find the latest snapshot
        records, _ = load_snapshots(24)
        if records:
            self._respond(200, records[-1])
        else:
            self._respond(200, {"error": "no data", "timestamp": None, "rooms": [], "weather": {}})

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
<title>Nest Climate Dashboard</title>
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
  --solarium: #FF8C00;
  --living: #4A90D9;
  --bedroom: #8B5CF6;
  --outside: #6B7280;
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
.cards { display: grid; grid-template-columns: repeat(auto-fit, minmax(220px, 1fr)); gap: 0.75rem; margin-bottom: 1.25rem; }
.card { background: var(--surface); border: 1px solid var(--border); border-radius: 8px; padding: 1rem; }
.card-label { font-size: 0.7rem; text-transform: uppercase; letter-spacing: 0.05em; color: var(--text-muted); margin-bottom: 0.25rem; }
.card-value { font-size: 1.75rem; font-weight: 700; }
.card-sub { font-size: 0.8rem; color: var(--text-muted); margin-top: 0.25rem; }
.card[data-room="Solarium"] .card-value { color: var(--solarium); }
.card[data-room="Living Room"] .card-value { color: var(--living); }
.card[data-room="Bedroom"] .card-value { color: var(--bedroom); }
.card[data-room="Outside"] .card-value { color: var(--outside); }
.controls { display: flex; gap: 0.5rem; margin-bottom: 1rem; flex-wrap: wrap; }
.controls button { background: var(--surface); border: 1px solid var(--border); color: var(--text); padding: 0.4rem 1rem; border-radius: 6px; cursor: pointer; font-size: 0.8rem; }
.controls button.active { background: #3b82f6; border-color: #3b82f6; color: #fff; }
.chart-container { background: var(--surface); border: 1px solid var(--border); border-radius: 8px; padding: 1rem; margin-bottom: 1rem; }
.chart-container h2 { font-size: 0.85rem; font-weight: 600; margin-bottom: 0.75rem; }
canvas { width: 100% !important; }
.loading { text-align: center; color: var(--text-muted); padding: 2rem; }
</style>
</head>
<body>
<h1>Nest Climate Dashboard <span class="updated" id="lastUpdate"></span></h1>
<div class="cards" id="cards"><div class="loading">Loading...</div></div>
<div class="controls">
  <button data-hours="24" class="active">24h</button>
  <button data-hours="168">7d</button>
  <button data-hours="720">30d</button>
  <button data-hours="8760">1Y</button>
</div>
<div class="chart-container"><h2>Temperature</h2><canvas id="tempChart" height="80"></canvas></div>
<div class="chart-container"><h2>Humidity</h2><canvas id="humidChart" height="80"></canvas></div>
<div class="chart-container"><h2>HVAC Heating Duty Cycle</h2><canvas id="hvacChart" height="60"></canvas></div>

<script>
const COLORS = {
  'Solarium': '#FF8C00',
  'Living Room': '#4A90D9',
  'Bedroom': '#8B5CF6',
  'Outside': '#6B7280',
};

let tempChart, humidChart, hvacChart;
let currentHours = 24;

function roomColor(name) {
  return COLORS[name] || '#' + (Math.random().toString(16) + '000000').slice(2, 8);
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

function renderCards(snapshot) {
  const el = document.getElementById('cards');
  if (!snapshot || !snapshot.rooms) {
    el.innerHTML = '<div class="loading">No data available</div>';
    return;
  }

  let html = '';
  // Room cards
  for (const r of snapshot.rooms) {
    const hvacLabel = r.eco && r.eco !== 'OFF' ? 'ECO' : (r.hvac || '—');
    html += `<div class="card" data-room="${r.room}">
      <div class="card-label">${r.room}</div>
      <div class="card-value">${(r.temp_f ?? 0).toFixed(1)}°F</div>
      <div class="card-sub">Set: ${(r.setpoint_f ?? 0).toFixed(0)}°F · ${hvacLabel} · ${r.humidity ?? 0}% RH</div>
    </div>`;
  }
  // Weather card
  const w = snapshot.weather;
  if (w && w.temp_f != null) {
    html += `<div class="card" data-room="Outside">
      <div class="card-label">Outside</div>
      <div class="card-value">${w.temp_f.toFixed(1)}°F</div>
      <div class="card-sub">${w.description || '—'} · ${w.humidity ?? 0}% RH · ${(w.wind_mph ?? 0).toFixed(0)} mph</div>
    </div>`;
  }
  el.innerHTML = html;

  // Update timestamp
  const ts = snapshot.timestamp;
  if (ts) {
    const d = new Date(ts);
    document.getElementById('lastUpdate').textContent = 'Updated ' + d.toLocaleTimeString();
  }
}

function buildTimeSeries(snapshots) {
  // Collect room names
  const roomNames = new Set();
  for (const s of snapshots) {
    for (const r of (s.rooms || [])) roomNames.add(r.room);
  }

  const series = {};
  for (const name of roomNames) {
    series[name] = { temps: [], humids: [], setpoints: [] };
  }
  series['Outside'] = { temps: [], humids: [] };

  for (const s of snapshots) {
    const ts = s.timestamp;
    for (const r of (s.rooms || [])) {
      if (!series[r.room]) continue;
      series[r.room].temps.push({ x: ts, y: r.temp_f });
      series[r.room].humids.push({ x: ts, y: r.humidity });
      series[r.room].setpoints.push({ x: ts, y: r.setpoint_f });
    }
    const w = s.weather;
    if (w && w.temp_f != null) {
      series['Outside'].temps.push({ x: ts, y: w.temp_f });
      series['Outside'].humids.push({ x: ts, y: w.humidity });
    }
  }
  return series;
}

function computeHvacDuty(snapshots) {
  // For each room, bucket snapshots by hour.
  // Duty cycle = count(hvac=="HEATING") / total snapshots in that hour bucket.
  const roomNames = new Set();
  for (const s of snapshots) {
    for (const r of (s.rooms || [])) roomNames.add(r.room);
  }

  const buckets = {}; // room -> hourKey -> {heating: n, total: n}
  for (const name of roomNames) buckets[name] = {};

  for (const s of snapshots) {
    const d = new Date(s.timestamp);
    const hourKey = d.toISOString().slice(0, 13) + ':00:00Z'; // YYYY-MM-DDTHH:00:00Z
    for (const r of (s.rooms || [])) {
      if (!buckets[r.room]) continue;
      if (!buckets[r.room][hourKey]) buckets[r.room][hourKey] = { heating: 0, total: 0 };
      buckets[r.room][hourKey].total++;
      if (r.hvac === 'HEATING') buckets[r.room][hourKey].heating++;
    }
  }

  const result = {};
  for (const name of roomNames) {
    result[name] = [];
    for (const [hourKey, b] of Object.entries(buckets[name]).sort()) {
      result[name].push({ x: hourKey, y: Math.round(100 * b.heating / b.total) });
    }
  }
  return result;
}

const chartDefaults = {
  responsive: true,
  maintainAspectRatio: true,
  animation: { duration: 300 },
  plugins: { legend: { labels: { color: getComputedStyle(document.documentElement).getPropertyValue('--text').trim() || '#e4e4e7', boxWidth: 12, padding: 10, font: { size: 11 } } } },
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
        y: { ...chartDefaults.scales.y, title: { display: true, text: 'Duty %', color: getComputedStyle(document.documentElement).getPropertyValue('--text-muted').trim() || '#9ca3af' }, min: 0, max: 100 },
      },
    },
  });
}

async function refresh() {
  const data = await fetchData(currentHours);
  const snaps = data.snapshots || [];

  // Cards: latest snapshot
  if (snaps.length > 0) renderCards(snaps[snaps.length - 1]);

  if (typeof Chart === 'undefined') return; // CDN unreachable

  const series = buildTimeSeries(snaps);
  const hvacDuty = computeHvacDuty(snaps);

  // Temperature datasets
  const tempDS = [];
  for (const [name, s] of Object.entries(series)) {
    if (s.temps.length === 0) continue;
    tempDS.push({
      label: name,
      data: s.temps,
      borderColor: roomColor(name),
      backgroundColor: roomColor(name) + '22',
      fill: false,
    });
    // Setpoint lines (dotted) for rooms (not outside)
    if (name !== 'Outside' && s.setpoints && s.setpoints.length > 0) {
      tempDS.push({
        label: name + ' setpoint',
        data: s.setpoints,
        borderColor: roomColor(name),
        borderDash: [4, 4],
        borderWidth: 1,
        fill: false,
        pointRadius: 0,
      });
    }
  }

  // Humidity datasets
  const humidDS = [];
  for (const [name, s] of Object.entries(series)) {
    if (s.humids.length === 0) continue;
    humidDS.push({
      label: name,
      data: s.humids,
      borderColor: roomColor(name),
      backgroundColor: roomColor(name) + '22',
      fill: false,
    });
  }

  // HVAC duty datasets
  const hvacDS = [];
  for (const [name, buckets] of Object.entries(hvacDuty)) {
    if (buckets.length === 0) continue;
    hvacDS.push({
      label: name,
      data: buckets,
      backgroundColor: roomColor(name) + '99',
      borderColor: roomColor(name),
      borderWidth: 1,
    });
  }

  // Destroy and recreate charts (simpler than updating)
  if (tempChart) tempChart.destroy();
  if (humidChart) humidChart.destroy();
  if (hvacChart) hvacChart.destroy();

  tempChart = createLineChart(document.getElementById('tempChart'), tempDS, '°F');
  humidChart = createLineChart(document.getElementById('humidChart'), humidDS, '% RH');
  hvacChart = createBarChart(document.getElementById('hvacChart'), hvacDS);
}

// Time range buttons
document.querySelector('.controls').addEventListener('click', e => {
  if (e.target.tagName !== 'BUTTON') return;
  document.querySelectorAll('.controls button').forEach(b => b.classList.remove('active'));
  e.target.classList.add('active');
  currentHours = parseInt(e.target.dataset.hours);
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
