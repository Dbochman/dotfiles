#!/usr/bin/env python3
"""OpenClaw Usage Dashboard — single-file HTTP server with embedded UI.

Serves a JSON API and Chart.js dashboard for OpenClaw token consumption,
API utilization, and agent activity metrics.
Reads JSONL snapshots from ~/.openclaw/usage-history/YYYY-MM-DD.jsonl

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

HISTORY_DIR = os.path.expanduser("~/.openclaw/usage-history")
PORT = 8551
MAX_HOURS = 8760  # 1 year
DOWNSAMPLE_THRESHOLD_HOURS = 168  # 7 days — beyond this, keep ~1 per hour


def load_snapshots(hours):
    """Load snapshots from JSONL files covering the requested time range."""
    hours = min(max(1, hours), MAX_HOURS)
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(hours=hours)
    records = []

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
        key = ts.strftime("%Y-%m-%d-%H")
        if key not in buckets or ts.minute < _ts_minute(buckets[key]):
            buckets[key] = rec
    return [buckets[k] for k in sorted(buckets.keys())]


def _ts_minute(rec):
    ts_str = rec.get("timestamp", "")
    try:
        return datetime.fromisoformat(ts_str.replace("Z", "+00:00")).minute
    except (ValueError, AttributeError):
        return 60


class DashboardHandler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
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
        records, _ = load_snapshots(24)
        if records:
            self._respond(200, records[-1])
        else:
            self._respond(200, {"error": "no data", "timestamp": None})

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
    print(f"Usage Dashboard running on http://0.0.0.0:{PORT}", flush=True)
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
<title>OpenClaw Usage Dashboard</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4"></script>
<script src="https://cdn.jsdelivr.net/npm/luxon@3"></script>
<script src="https://cdn.jsdelivr.net/npm/chartjs-adapter-luxon@1"></script>
<noscript><p style="color:#f87171;text-align:center;margin:2rem">JavaScript is required for charts.</p></noscript>
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
  --cyan: #06b6d4;
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
.updated { font-size: 0.75rem; color: var(--text-muted); font-weight: 400; margin-left: 0.5rem; }
.cards { display: grid; grid-template-columns: repeat(auto-fit, minmax(180px, 1fr)); gap: 0.75rem; margin-bottom: 1.25rem; }
.card { background: var(--surface); border: 1px solid var(--border); border-radius: 8px; padding: 1rem; }
.card-label { font-size: 0.7rem; text-transform: uppercase; letter-spacing: 0.05em; color: var(--text-muted); margin-bottom: 0.25rem; }
.card-value { font-size: 1.75rem; font-weight: 700; }
.card-sub { font-size: 0.8rem; color: var(--text-muted); margin-top: 0.25rem; }
.card-bar { height: 4px; border-radius: 2px; margin-top: 0.5rem; background: var(--border); overflow: hidden; }
.card-bar-fill { height: 100%; border-radius: 2px; transition: width 0.3s; }
.controls { display: flex; gap: 0.5rem; margin-bottom: 1rem; flex-wrap: wrap; }
.controls button { background: var(--surface); border: 1px solid var(--border); color: var(--text); padding: 0.4rem 1rem; border-radius: 6px; cursor: pointer; font-size: 0.8rem; }
.controls button.active { background: #3b82f6; border-color: #3b82f6; color: #fff; }
.chart-container { background: var(--surface); border: 1px solid var(--border); border-radius: 8px; padding: 1rem; margin-bottom: 1rem; }
.chart-container h2 { font-size: 0.85rem; font-weight: 600; margin-bottom: 0.75rem; }
.chart-wrap { position: relative; width: 100%; min-height: 280px; }
.chart-wrap.short { min-height: 200px; }
canvas { width: 100% !important; }
.loading { text-align: center; color: var(--text-muted); padding: 2rem; }
</style>
</head>
<body>
<h1>OpenClaw Usage <span class="updated" id="lastUpdate"></span></h1>
<div class="cards" id="cards"><div class="loading">Loading...</div></div>
<div class="controls" id="timeControls">
  <button data-hours="24" class="active">24h</button>
  <button data-hours="168">7d</button>
  <button data-hours="720">30d</button>
</div>
<div class="chart-container"><h2>Utilization Over Time</h2><div class="chart-wrap"><canvas id="utilChart"></canvas></div></div>
<div class="chart-container"><h2>Token Usage</h2><div class="chart-wrap"><canvas id="tokenChart"></canvas></div></div>
<div class="chart-container"><h2>Activity</h2><div class="chart-wrap"><canvas id="activityChart"></canvas></div></div>
<div class="chart-container"><h2>Response Times</h2><div class="chart-wrap"><canvas id="rtChart"></canvas></div></div>
<div class="chart-container"><h2>Errors</h2><div class="chart-wrap short"><canvas id="errorChart"></canvas></div></div>

<script>
let utilChart, tokenChart, activityChart, rtChart, errorChart;
let currentHours = 24;

function utilizationColor(pct) {
  if (pct == null) return 'var(--text-muted)';
  if (pct < 50) return 'var(--green)';
  if (pct < 80) return 'var(--amber)';
  return 'var(--red)';
}

function formatCountdown(resetsAt) {
  if (!resetsAt) return '';
  const diff = new Date(resetsAt) - Date.now();
  if (diff <= 0) return 'resetting...';
  const h = Math.floor(diff / 3600000);
  const m = Math.floor((diff % 3600000) / 60000);
  return h > 0 ? h + 'h ' + m + 'm' : m + 'm';
}

function formatTokens(n) {
  if (n == null || n === 0) return '0';
  if (n >= 1000000) return (n / 1000000).toFixed(1) + 'M';
  if (n >= 1000) return (n / 1000).toFixed(1) + 'K';
  return String(n);
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
  if (!snapshot || snapshot.error) {
    el.innerHTML = '<div class="loading">No data available</div>';
    return;
  }
  const u = snapshot.utilization || {};
  const t = snapshot.tokens || {};
  const a = snapshot.activity || {};

  let html = '';

  // 5-hour utilization
  const fh = u.five_hour;
  if (fh) {
    const pct = fh.utilization != null ? fh.utilization.toFixed(1) : '?';
    const color = utilizationColor(fh.utilization);
    html += `<div class="card">
      <div class="card-label">5-Hour Utilization</div>
      <div class="card-value" style="color:${color}">${pct}%</div>
      <div class="card-sub">Resets in ${formatCountdown(fh.resets_at)}</div>
      <div class="card-bar"><div class="card-bar-fill" style="width:${Math.min(fh.utilization||0,100)}%;background:${color}"></div></div>
    </div>`;
  }

  // 7-day utilization
  const sd = u.seven_day;
  if (sd) {
    const pct = sd.utilization != null ? sd.utilization.toFixed(1) : '?';
    const color = utilizationColor(sd.utilization);
    html += `<div class="card">
      <div class="card-label">7-Day Utilization</div>
      <div class="card-value" style="color:${color}">${pct}%</div>
      <div class="card-sub">Resets in ${formatCountdown(sd.resets_at)}</div>
      <div class="card-bar"><div class="card-bar-fill" style="width:${Math.min(sd.utilization||0,100)}%;background:${color}"></div></div>
    </div>`;
  }

  // Per-model breakdown
  for (const [key, label] of [['seven_day_opus', 'Opus 7d'], ['seven_day_sonnet', 'Sonnet 7d']]) {
    const m = u[key];
    if (m) {
      const pct = m.utilization != null ? m.utilization.toFixed(1) : '?';
      html += `<div class="card">
        <div class="card-label">${label}</div>
        <div class="card-value">${pct}%</div>
        <div class="card-sub">Resets in ${formatCountdown(m.resets_at)}</div>
      </div>`;
    }
  }

  // Extra credits
  const ex = u.extra_usage;
  if (ex && ex.is_enabled) {
    html += `<div class="card">
      <div class="card-label">Extra Credits</div>
      <div class="card-value">$${(ex.used_credits || 0).toFixed(2)}</div>
      <div class="card-sub">of $${ex.monthly_limit || '?'} limit</div>
    </div>`;
  }

  // Tokens today
  html += `<div class="card">
    <div class="card-label">Tokens (Period)</div>
    <div class="card-value">${formatTokens(t.total)}</div>
    <div class="card-sub">In: ${formatTokens(t.input)} / Out: ${formatTokens(t.output)}</div>
  </div>`;

  // Activity
  const totalActivity = (a.agent_runs||0) + (a.messages_sent||0) + (a.cron_runs||0);
  html += `<div class="card">
    <div class="card-label">Activity (Period)</div>
    <div class="card-value">${totalActivity}</div>
    <div class="card-sub">Agents: ${a.agent_runs||0} / Msgs: ${a.messages_sent||0} / Cron: ${a.cron_runs||0}</div>
  </div>`;

  el.innerHTML = html;

  // Timestamp
  if (snapshot.timestamp) {
    const d = new Date(snapshot.timestamp);
    document.getElementById('lastUpdate').textContent = 'Updated ' + d.toLocaleTimeString();
  }
}

function aggregateForCards(snapshots) {
  // Sum tokens and activity across all snapshots in range
  const result = { tokens: { input: 0, output: 0, total: 0 }, activity: { agent_runs: 0, messages_sent: 0, cron_runs: 0, errors: 0, gateway_restarts: 0 } };
  for (const s of snapshots) {
    const t = s.tokens || {};
    result.tokens.input += t.input || 0;
    result.tokens.output += t.output || 0;
    result.tokens.total += t.total || 0;
    const a = s.activity || {};
    result.activity.agent_runs += a.agent_runs || 0;
    result.activity.messages_sent += a.messages_sent || 0;
    result.activity.cron_runs += a.cron_runs || 0;
    result.activity.errors += a.errors || 0;
    result.activity.gateway_restarts += a.gateway_restarts || 0;
  }
  // Use latest utilization
  if (snapshots.length > 0) {
    result.utilization = snapshots[snapshots.length - 1].utilization;
    result.timestamp = snapshots[snapshots.length - 1].timestamp;
  }
  return result;
}

const chartTextColor = () => getComputedStyle(document.documentElement).getPropertyValue('--text-muted').trim() || '#9ca3af';

const chartDefaults = {
  responsive: true,
  maintainAspectRatio: false,
  animation: { duration: 300 },
  plugins: { legend: { labels: { color: chartTextColor(), boxWidth: 12, padding: 10, font: { size: 11 } } } },
  scales: {
    x: {
      type: 'time',
      grid: { color: 'rgba(255,255,255,0.05)' },
      ticks: { color: chartTextColor(), font: { size: 10 } },
    },
    y: {
      grid: { color: 'rgba(255,255,255,0.05)' },
      ticks: { color: chartTextColor(), font: { size: 10 } },
    },
  },
};

// ── Chart: Utilization Over Time ──
function buildUtilChart(snapshots) {
  const fiveH = [], sevenD = [];
  for (const s of snapshots) {
    const u = s.utilization;
    if (!u) continue;
    const ts = s.timestamp;
    if (u.five_hour) fiveH.push({ x: ts, y: u.five_hour.utilization });
    if (u.seven_day) sevenD.push({ x: ts, y: u.seven_day.utilization });
  }
  return [
    { label: '5-Hour %', data: fiveH, borderColor: 'var(--blue)', backgroundColor: 'rgba(59,130,246,0.1)', fill: false },
    { label: '7-Day %', data: sevenD, borderColor: 'var(--purple)', backgroundColor: 'rgba(139,92,246,0.1)', fill: false },
  ];
}

// ── Chart: Token Usage (stacked bar by hour/day) ──
function buildTokenChart(snapshots) {
  // Bucket tokens by hour
  const inputBuckets = {}, outputBuckets = {};
  for (const s of snapshots) {
    const ts = new Date(s.timestamp);
    const key = ts.toISOString().slice(0, 13) + ':00:00Z';
    const t = s.tokens || {};
    inputBuckets[key] = (inputBuckets[key] || 0) + (t.input || 0);
    outputBuckets[key] = (outputBuckets[key] || 0) + (t.output || 0);
  }
  const keys = Object.keys(inputBuckets).sort();
  return [
    { label: 'Input', data: keys.map(k => ({ x: k, y: inputBuckets[k] })), backgroundColor: 'rgba(59,130,246,0.7)', borderColor: '#3b82f6', borderWidth: 1 },
    { label: 'Output', data: keys.map(k => ({ x: k, y: outputBuckets[k] || 0 })), backgroundColor: 'rgba(139,92,246,0.7)', borderColor: '#8b5cf6', borderWidth: 1 },
  ];
}

// ── Chart: Activity ──
function buildActivityChart(snapshots) {
  const agents = [], msgs = [], crons = [];
  for (const s of snapshots) {
    const a = s.activity || {};
    const ts = s.timestamp;
    agents.push({ x: ts, y: a.agent_runs || 0 });
    msgs.push({ x: ts, y: a.messages_sent || 0 });
    crons.push({ x: ts, y: a.cron_runs || 0 });
  }
  return [
    { label: 'Agent Runs', data: agents, borderColor: '#06b6d4', fill: false },
    { label: 'Messages', data: msgs, borderColor: '#22c55e', fill: false },
    { label: 'Cron Jobs', data: crons, borderColor: '#f59e0b', fill: false },
  ];
}

// ── Chart: Response Times (p50/p95/max) ──
function buildRTChart(snapshots) {
  const p50 = [], p95 = [], maxRT = [];
  for (const s of snapshots) {
    const rts = (s.response_times_ms || []).sort((a, b) => a - b);
    if (rts.length === 0) continue;
    const ts = s.timestamp;
    p50.push({ x: ts, y: rts[Math.floor(rts.length * 0.5)] });
    p95.push({ x: ts, y: rts[Math.floor(rts.length * 0.95)] });
    maxRT.push({ x: ts, y: rts[rts.length - 1] });
  }
  return [
    { label: 'p50', data: p50, borderColor: '#22c55e', fill: false },
    { label: 'p95', data: p95, borderColor: '#f59e0b', fill: false },
    { label: 'max', data: maxRT, borderColor: '#ef4444', fill: false },
  ];
}

// ── Chart: Errors ──
function buildErrorChart(snapshots) {
  const buckets = {};
  for (const s of snapshots) {
    const ts = new Date(s.timestamp);
    const key = ts.toISOString().slice(0, 13) + ':00:00Z';
    const a = s.activity || {};
    buckets[key] = (buckets[key] || 0) + (a.errors || 0);
  }
  const keys = Object.keys(buckets).sort();
  return [
    { label: 'Errors', data: keys.map(k => ({ x: k, y: buckets[k] })), backgroundColor: 'rgba(239,68,68,0.7)', borderColor: '#ef4444', borderWidth: 1 },
  ];
}

// 100% threshold line plugin
const thresholdPlugin = {
  id: 'threshold',
  afterDraw(chart) {
    if (chart.canvas.id !== 'utilChart') return;
    const yScale = chart.scales.y;
    if (!yScale) return;
    const y = yScale.getPixelForValue(100);
    if (y < chart.chartArea.top || y > chart.chartArea.bottom) return;
    const ctx = chart.ctx;
    ctx.save();
    ctx.beginPath();
    ctx.setLineDash([6, 4]);
    ctx.strokeStyle = 'rgba(239,68,68,0.5)';
    ctx.lineWidth = 1;
    ctx.moveTo(chart.chartArea.left, y);
    ctx.lineTo(chart.chartArea.right, y);
    ctx.stroke();
    ctx.restore();
  }
};

function createLineChart(ctx, datasets, yLabel) {
  return new Chart(ctx, {
    type: 'line',
    data: { datasets },
    options: {
      ...chartDefaults,
      scales: {
        ...chartDefaults.scales,
        y: { ...chartDefaults.scales.y, title: { display: true, text: yLabel, color: chartTextColor() } },
      },
      elements: { point: { radius: 0, hitRadius: 6 }, line: { tension: 0.3, borderWidth: 2 } },
    },
    plugins: [thresholdPlugin],
  });
}

function createStackedBarChart(ctx, datasets, yLabel) {
  return new Chart(ctx, {
    type: 'bar',
    data: { datasets },
    options: {
      ...chartDefaults,
      scales: {
        ...chartDefaults.scales,
        x: { ...chartDefaults.scales.x, stacked: true },
        y: { ...chartDefaults.scales.y, stacked: true, title: { display: true, text: yLabel, color: chartTextColor() } },
      },
    },
  });
}

function createBarChart(ctx, datasets, yLabel) {
  return new Chart(ctx, {
    type: 'bar',
    data: { datasets },
    options: {
      ...chartDefaults,
      scales: {
        ...chartDefaults.scales,
        y: { ...chartDefaults.scales.y, title: { display: true, text: yLabel, color: chartTextColor() }, min: 0 },
      },
    },
  });
}

async function refresh() {
  const data = await fetchData(currentHours);
  const snaps = data.snapshots || [];

  // Cards: aggregate across all snapshots
  if (snaps.length > 0) {
    renderCards(aggregateForCards(snaps));
  } else {
    document.getElementById('cards').innerHTML = '<div class="loading">No data available</div>';
  }

  if (typeof Chart === 'undefined') return;

  // Destroy existing charts
  [utilChart, tokenChart, activityChart, rtChart, errorChart].forEach(c => { if (c) c.destroy(); });

  utilChart = createLineChart(document.getElementById('utilChart'), buildUtilChart(snaps), 'Utilization %');
  tokenChart = createStackedBarChart(document.getElementById('tokenChart'), buildTokenChart(snaps), 'Tokens');
  activityChart = createLineChart(document.getElementById('activityChart'), buildActivityChart(snaps), 'Count');
  rtChart = createLineChart(document.getElementById('rtChart'), buildRTChart(snaps), 'ms');
  errorChart = createBarChart(document.getElementById('errorChart'), buildErrorChart(snaps), 'Errors');
}

// Time range buttons
document.getElementById('timeControls').addEventListener('click', e => {
  if (e.target.tagName !== 'BUTTON') return;
  document.querySelectorAll('#timeControls button').forEach(b => b.classList.remove('active'));
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
