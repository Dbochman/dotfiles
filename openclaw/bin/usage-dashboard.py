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
import subprocess
import sys
import threading
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
    """Keep one snapshot per hour, merging activity/cron deltas so counts aren't lost."""
    buckets = {}
    extras = {}  # key -> list of activity dicts + cron_jobs from dropped snapshots
    for rec in records:
        ts_str = rec.get("timestamp", "")
        try:
            ts = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
        except (ValueError, AttributeError):
            continue
        key = ts.strftime("%Y-%m-%d-%H")
        if key not in buckets:
            buckets[key] = rec
            extras[key] = []
        elif ts.minute < _ts_minute(buckets[key]):
            # New winner — demote old winner to extras
            extras[key].append(buckets[key])
            buckets[key] = rec
        else:
            extras[key].append(rec)

    # Merge activity deltas and cron_jobs from dropped snapshots into the kept one
    for key, kept in buckets.items():
        for dropped in extras.get(key, []):
            da = dropped.get("activity", {})
            if da:
                ka = kept.setdefault("activity", {})
                for field in ("agent_runs", "messages_sent", "messages_received",
                              "cron_runs", "errors", "gateway_restarts"):
                    ka[field] = ka.get(field, 0) + da.get(field, 0)
            dc = dropped.get("cron_jobs", [])
            if dc:
                kept.setdefault("cron_jobs", []).extend(dc)

    return [buckets[k] for k in sorted(buckets.keys())]


def _ts_minute(rec):
    ts_str = rec.get("timestamp", "")
    try:
        return datetime.fromisoformat(ts_str.replace("Z", "+00:00")).minute
    except (ValueError, AttributeError):
        return 60


def load_ccusage():
    """Load and merge Claude Code usage from all ccusage-*.json files (one per machine)."""
    import glob
    merged = {}  # date -> {totalTokens, inputTokens, outputTokens, ...}
    for path in glob.glob(os.path.join(HISTORY_DIR, "ccusage-*.json")):
        try:
            with open(path) as f:
                data = json.load(f)
            for day in data.get("daily", []):
                date = day.get("date", "")
                if not date:
                    continue
                if date not in merged:
                    merged[date] = {"date": date, "totalTokens": 0, "inputTokens": 0,
                                    "outputTokens": 0, "cacheCreationTokens": 0,
                                    "cacheReadTokens": 0, "totalCost": 0, "machines": []}
                m = merged[date]
                m["totalTokens"] += day.get("totalTokens", 0)
                m["inputTokens"] += day.get("inputTokens", 0)
                m["outputTokens"] += day.get("outputTokens", 0)
                m["cacheCreationTokens"] += day.get("cacheCreationTokens", 0)
                m["cacheReadTokens"] += day.get("cacheReadTokens", 0)
                m["totalCost"] += day.get("totalCost", 0)
                machine = os.path.basename(path).replace("ccusage-", "").replace(".json", "")
                if machine not in m["machines"]:
                    m["machines"].append(machine)
        except (json.JSONDecodeError, OSError):
            continue
    return sorted(merged.values(), key=lambda d: d["date"])


LAUNCHAGENT_DIR = os.path.expanduser("~/Library/LaunchAgents")


def _plist_log_path(label):
    """Read the StandardOutPath or StandardErrorPath from a plist."""
    plist_path = os.path.join(LAUNCHAGENT_DIR, label + ".plist")
    try:
        result = subprocess.run(
            ["plutil", "-convert", "json", "-o", "-", plist_path],
            capture_output=True, text=True, timeout=3
        )
        if result.returncode == 0:
            d = json.loads(result.stdout)
            return d.get("StandardOutPath") or d.get("StandardErrorPath") or ""
    except (subprocess.TimeoutExpired, OSError, json.JSONDecodeError):
        pass
    return ""


def get_launchagent_status():
    """Query launchctl for OpenClaw service status."""
    services = []
    try:
        result = subprocess.run(
            ["launchctl", "list"],
            capture_output=True, text=True, timeout=5
        )
        for line in result.stdout.splitlines():
            parts = line.split("\t")
            if len(parts) != 3:
                continue
            label = parts[2]
            if "openclaw" not in label.lower():
                continue
            pid_str, exit_str = parts[0], parts[1]
            if pid_str != "-":
                status = "running"
            else:
                status = "idle"
            try:
                last_exit = int(exit_str)
            except ValueError:
                last_exit = None

            # Get last activity time from log file mtime
            last_run_iso = None
            log_path = _plist_log_path(label)
            if log_path:
                try:
                    mtime = os.path.getmtime(log_path)
                    last_run_iso = datetime.fromtimestamp(mtime, tz=timezone.utc).isoformat()
                except OSError:
                    pass

            services.append({
                "label": label,
                "status": status,
                "last_exit": last_exit,
                "last_run": last_run_iso,
            })
    except (subprocess.TimeoutExpired, OSError):
        pass
    services.sort(key=lambda s: s["label"])
    return services


SECRETS_CACHE = os.path.expanduser("~/.openclaw/.secrets-cache")
OPENCLAW_BIN = "/opt/homebrew/opt/node@22/bin/openclaw"

# Gateway usage RPC cache (5-minute TTL)
_gw_usage_cache = {"data": None, "ts": 0}
_gw_usage_lock = threading.Lock()
GW_CACHE_TTL = 300  # seconds


def fetch_gateway_usage():
    """Call `openclaw gateway call sessions.usage --json` with 5-min caching."""
    now = datetime.now(timezone.utc).timestamp()
    with _gw_usage_lock:
        if _gw_usage_cache["data"] and (now - _gw_usage_cache["ts"]) < GW_CACHE_TTL:
            return _gw_usage_cache["data"]

    # Build env with secrets
    env = os.environ.copy()
    env["PATH"] = "/opt/homebrew/bin:/opt/homebrew/opt/node@22/bin:" + env.get("PATH", "")
    if os.path.exists(SECRETS_CACHE):
        try:
            for line in open(SECRETS_CACHE):
                line = line.strip()
                if "=" in line and not line.startswith("#"):
                    k, v = line.split("=", 1)
                    env[k] = v
        except OSError:
            pass

    try:
        result = subprocess.run(
            ["openclaw", "gateway", "call", "sessions.usage", "--json", "--timeout", "30000"],
            capture_output=True, text=True, timeout=35, env=env,
        )
        if result.returncode != 0:
            print(f"gateway usage RPC failed: {result.stderr[:200]}", file=sys.stderr, flush=True)
            return None
        data = json.loads(result.stdout)
    except (subprocess.TimeoutExpired, OSError, json.JSONDecodeError) as e:
        print(f"gateway usage RPC error: {e}", file=sys.stderr, flush=True)
        return None

    with _gw_usage_lock:
        _gw_usage_cache["data"] = data
        _gw_usage_cache["ts"] = now
    return data


CRON_JOBS_PATH = os.path.expanduser("~/.openclaw/cron/jobs.json")


def get_upcoming_cron_jobs():
    """Read cron jobs.json and return upcoming scheduled runs."""
    try:
        with open(CRON_JOBS_PATH) as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError):
        return []

    now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    jobs = []
    for j in data.get("jobs", []):
        if not j.get("enabled", True):
            continue
        state = j.get("state", {})
        next_ms = state.get("nextRunAtMs")
        sched = j.get("schedule", {})
        jobs.append({
            "id": j.get("id", ""),
            "name": j.get("name", ""),
            "schedule_kind": sched.get("kind", ""),
            "schedule_expr": sched.get("expr", sched.get("at", "")),
            "next_run_ms": next_ms,
            "last_status": state.get("lastStatus"),
            "consecutive_errors": state.get("consecutiveErrors", 0),
            "delete_after_run": j.get("deleteAfterRun", False),
        })

    # Sort by next run time (soonest first), nulls last
    jobs.sort(key=lambda j: j["next_run_ms"] if j["next_run_ms"] else float("inf"))
    return jobs


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
        elif path == "/api/services":
            self._respond(200, {"services": get_launchagent_status()})
        elif path == "/api/cron":
            self._respond(200, {"jobs": get_upcoming_cron_jobs()})
        elif path == "/api/gateway-usage":
            data = fetch_gateway_usage()
            if data:
                self._respond(200, data)
            else:
                self._respond(503, {"error": "gateway usage unavailable"})
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
        ccusage = load_ccusage()
        self._respond(200, {
            "meta": {
                "hours": clamped_hours,
                "count": len(records),
                "downsampled": clamped_hours > DOWNSAMPLE_THRESHOLD_HOURS,
            },
            "snapshots": records,
            "ccusage": ccusage,
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
<title>OpenClaw Usage Dashboard</title>
<link rel="icon" href="data:image/svg+xml,<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 100 100'><text y='.9em' font-size='90'>📊</text></svg>">
<script src="https://cdn.jsdelivr.net/npm/chart.js@4"></script>
<script src="https://cdn.jsdelivr.net/npm/luxon@3"></script>
<script src="https://cdn.jsdelivr.net/npm/chartjs-adapter-luxon@1"></script>
<noscript><p style="color:#f87171;text-align:center;margin:2rem">JavaScript required.</p></noscript>
<style>
:root{--bg:#0f1117;--surface:#1a1d27;--border:#2a2d3a;--text:#e4e4e7;--muted:#9ca3af;--green:#22c55e;--amber:#f59e0b;--red:#ef4444;--blue:#3b82f6;--purple:#8b5cf6;--cyan:#06b6d4}
*{margin:0;padding:0;box-sizing:border-box}
body{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',system-ui,sans-serif;background:var(--bg);color:var(--text);padding:1rem;max-width:1400px;margin:0 auto}

/* Header */
.header{display:flex;align-items:center;justify-content:space-between;margin-bottom:1rem;flex-wrap:wrap;gap:0.5rem}
.header h1{font-size:1.15rem;font-weight:600}
.header-right{display:flex;align-items:center;gap:1rem}
.stale-warn{font-size:0.75rem;color:var(--red);font-weight:500;display:none}
.last-update{font-size:0.7rem;color:var(--muted)}

/* Controls */
.controls{display:flex;gap:0.5rem;margin-bottom:1rem;flex-wrap:wrap}
.controls button{background:var(--surface);border:1px solid var(--border);color:var(--text);padding:0.35rem 0.9rem;border-radius:6px;cursor:pointer;font-size:0.78rem;transition:all 0.15s}
.controls button:hover{border-color:var(--blue)}
.controls button.active{background:var(--blue);border-color:var(--blue);color:#fff}

/* Gauge row */
.gauges{display:grid;grid-template-columns:repeat(auto-fit,minmax(160px,1fr));gap:0.75rem;margin-bottom:1rem}
.gauge{background:var(--surface);border:1px solid var(--border);border-radius:10px;padding:1rem;text-align:center}
.gauge-ring{position:relative;width:100px;height:100px;margin:0 auto 0.5rem}
.gauge-ring svg{transform:rotate(-90deg)}
.gauge-ring .track{fill:none;stroke:var(--border);stroke-width:8}
.gauge-ring .fill{fill:none;stroke-width:8;stroke-linecap:round;transition:stroke-dashoffset 0.6s ease}
.gauge-pct{position:absolute;top:50%;left:50%;transform:translate(-50%,-50%);font-size:1.4rem;font-weight:700}
.gauge-label{font-size:0.7rem;text-transform:uppercase;letter-spacing:0.05em;color:var(--muted);margin-bottom:0.15rem}
.gauge-sub{font-size:0.75rem;color:var(--muted)}
.gauge-pace{font-size:0.7rem;margin-top:0.35rem;font-weight:500}

/* Stat cards */
.stats{display:grid;grid-template-columns:repeat(auto-fit,minmax(140px,1fr));gap:0.75rem;margin-bottom:1rem}
.stat{background:var(--surface);border:1px solid var(--border);border-radius:8px;padding:0.85rem}
.stat-label{font-size:0.65rem;text-transform:uppercase;letter-spacing:0.05em;color:var(--muted);margin-bottom:0.2rem}
.stat-value{font-size:1.5rem;font-weight:700}
.stat-sub{font-size:0.75rem;color:var(--muted);margin-top:0.15rem}

/* Charts */
.charts-grid{display:grid;grid-template-columns:1fr 1fr;gap:0.75rem;margin-bottom:1rem}
@media(max-width:800px){.charts-grid{grid-template-columns:1fr}}
.chart-box{background:var(--surface);border:1px solid var(--border);border-radius:8px;padding:0.85rem}
.chart-box.full{grid-column:1/-1}
.chart-box h2{font-size:0.8rem;font-weight:600;margin-bottom:0.5rem;color:var(--muted)}
.chart-wrap{position:relative;width:100%;height:220px}

/* Cron table */
.cron-section{background:var(--surface);border:1px solid var(--border);border-radius:8px;padding:0.85rem;margin-bottom:1rem}
.cron-section h2{font-size:0.8rem;font-weight:600;margin-bottom:0.5rem;color:var(--muted)}
.cron-table{width:100%;border-collapse:collapse;font-size:0.78rem}
.cron-table th{text-align:left;padding:0.4rem 0.6rem;border-bottom:1px solid var(--border);color:var(--muted);font-weight:500;font-size:0.7rem;text-transform:uppercase;letter-spacing:0.04em}
.cron-table td{padding:0.4rem 0.6rem;border-bottom:1px solid rgba(255,255,255,0.03)}
.cron-table tr:last-child td{border-bottom:none}
.badge{display:inline-block;padding:0.1rem 0.45rem;border-radius:4px;font-size:0.7rem;font-weight:500}
.badge-ok{background:rgba(34,197,94,0.15);color:var(--green)}
.badge-err{background:rgba(239,68,68,0.15);color:var(--red)}

.loading{text-align:center;color:var(--muted);padding:2rem}
.error-banner{background:rgba(239,68,68,0.1);border:1px solid rgba(239,68,68,0.3);border-radius:8px;padding:0.75rem;color:var(--red);font-size:0.8rem;margin-bottom:1rem;display:none}
</style>
</head>
<body>

<div class="header">
  <h1>OpenClaw Usage</h1>
  <div class="header-right">
    <span class="stale-warn" id="staleWarn">Data stale</span>
    <span class="last-update" id="lastUpdate"></span>
  </div>
</div>

<div class="error-banner" id="errorBanner"></div>

<div class="controls" id="timeControls">
  <button data-hours="6">6h</button>
  <button data-hours="24">24h</button>
  <button data-hours="168">7d</button>
  <button data-hours="720" class="active">30d</button>
</div>

<!-- Utilization gauges -->
<div class="gauges" id="gauges"><div class="loading">Loading...</div></div>

<!-- Stat cards -->
<div class="stats" id="stats"></div>

<!-- Charts -->
<div class="charts-grid">
  <div class="chart-box full" id="utilBox"><h2>Utilization Over Time</h2><div class="chart-wrap"><canvas id="utilChart"></canvas></div></div>
  <div class="chart-box" id="tokenJobBox"><h2>Tokens by Job</h2><div class="chart-wrap"><canvas id="tokenJobChart"></canvas></div></div>
  <div class="chart-box" id="tokenTimeBox"><h2>Token Usage Over Time</h2><div class="chart-wrap"><canvas id="tokenTimeChart"></canvas></div></div>
  <div class="chart-box full" id="activityBox"><h2>Activity</h2><div class="chart-wrap"><canvas id="activityChart"></canvas></div></div>
  <div class="chart-box" id="costBox" style="display:none"><h2>Cost Over Time</h2><div class="chart-wrap"><canvas id="costChart"></canvas></div></div>
  <div class="chart-box" id="modelBox" style="display:none"><h2>Model Split</h2><div class="chart-wrap"><canvas id="modelChart"></canvas></div></div>
  <div class="chart-box" id="toolBox" style="display:none"><h2>Tool Usage</h2><div class="chart-wrap"><canvas id="toolChart"></canvas></div></div>
</div>

<!-- LaunchAgent services -->
<div class="cron-section">
  <h2>LaunchAgent Services</h2>
  <table class="cron-table" id="servicesTable">
    <thead><tr><th>Service</th><th>Status</th><th>Last Run</th><th>Exit</th></tr></thead>
    <tbody id="servicesBody"><tr><td colspan="4" class="loading">Loading...</td></tr></tbody>
  </table>
</div>

<!-- Cron job table -->
<div class="cron-section">
  <h2>Recent Cron Runs</h2>
  <table class="cron-table" id="cronTable">
    <thead><tr><th>Job</th><th>Status</th><th>Delivered</th><th>Model</th><th>Duration</th><th>Trend</th><th>Tokens</th><th>Time</th></tr></thead>
    <tbody id="cronBody"><tr><td colspan="8" class="loading">Loading...</td></tr></tbody>
  </table>
</div>

<!-- Upcoming cron jobs -->
<div class="cron-section">
  <h2>Upcoming Cron Jobs</h2>
  <table class="cron-table" id="upcomingTable">
    <thead><tr><th>Job</th><th>Next Run</th><th>Last</th><th>Type</th></tr></thead>
    <tbody id="upcomingBody"><tr><td colspan="4" class="loading">Loading...</td></tr></tbody>
  </table>
</div>

<script>
const C = { green:'#22c55e', amber:'#f59e0b', red:'#ef4444', blue:'#3b82f6', purple:'#8b5cf6', cyan:'#06b6d4', orange:'#f97316', pink:'#ec4899', muted:'#9ca3af', grid:'rgba(255,255,255,0.05)' };
let charts = {};
let currentHours = 720;

// ── Helpers ──

function utilColor(pct) {
  if (pct == null) return C.muted;
  return pct < 50 ? C.green : pct < 80 ? C.amber : C.red;
}

function paceLabel(pct, resetsAt) {
  if (pct == null || !resetsAt) return '';
  const hoursLeft = Math.max(0, (new Date(resetsAt) - Date.now()) / 3600000);
  if (hoursLeft <= 0) return 'resetting...';
  // Simple pace: if you'd hit 100% before reset at current rate
  const rate = pct / Math.max(1, (5 - hoursLeft)); // rough estimate for 5h window
  if (pct < 30) return '<span style="color:' + C.green + '">chill</span>';
  if (pct < 70) return '<span style="color:' + C.amber + '">on track</span>';
  return '<span style="color:' + C.red + '">hot</span>';
}

function countdown(resetsAt) {
  if (!resetsAt) return '';
  const diff = new Date(resetsAt) - Date.now();
  if (diff <= 0) return 'resetting...';
  const h = Math.floor(diff / 3600000);
  const m = Math.floor((diff % 3600000) / 60000);
  return h > 0 ? h + 'h ' + m + 'm' : m + 'm';
}

function fmtTokens(n) {
  if (n == null || n === 0) return '0';
  if (n >= 1e6) return (n/1e6).toFixed(1) + 'M';
  if (n >= 1e3) return (n/1e3).toFixed(1) + 'K';
  return String(n);
}

function fmtDuration(ms) {
  if (!ms) return '-';
  if (ms < 1000) return ms + 'ms';
  const s = ms / 1000;
  if (s < 60) return s.toFixed(1) + 's';
  return Math.floor(s/60) + 'm ' + Math.round(s%60) + 's';
}

function fmtTime(ts) {
  if (!ts) return '-';
  const d = typeof ts === 'number' ? new Date(ts) : new Date(ts);
  return d.toLocaleTimeString([], { hour:'2-digit', minute:'2-digit' });
}

function shortJobId(id) {
  if (!id) return '?';
  // Trim UUID-style prefixes, keep readable part
  return id.replace(/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/, id.slice(0,8))
            .replace(/-0001$/, '');
}

async function fetchData(hours) {
  try {
    const r = await fetch('/api/data?hours=' + hours);
    if (!r.ok) throw new Error('HTTP ' + r.status);
    const d = await r.json();
    document.getElementById('errorBanner').style.display = 'none';
    return d;
  } catch (e) {
    document.getElementById('errorBanner').textContent = 'Failed to load data: ' + e.message;
    document.getElementById('errorBanner').style.display = 'block';
    return { snapshots: [], meta: {} };
  }
}

// ── SVG Gauge ──

function gaugeHTML(label, pct, resetsAt, sub) {
  const color = utilColor(pct);
  const circ = 2 * Math.PI * 42; // r=42
  const offset = circ - (circ * Math.min(pct || 0, 100) / 100);
  const pctText = pct != null ? pct.toFixed(1) + '%' : '?';
  const pace = paceLabel(pct, resetsAt);
  return `<div class="gauge">
    <div class="gauge-label">${label}</div>
    <div class="gauge-ring">
      <svg viewBox="0 0 100 100" width="100" height="100">
        <circle class="track" cx="50" cy="50" r="42"/>
        <circle class="fill" cx="50" cy="50" r="42"
          stroke="${color}" stroke-dasharray="${circ}" stroke-dashoffset="${offset}"/>
      </svg>
      <div class="gauge-pct" style="color:${color}">${pctText}</div>
    </div>
    <div class="gauge-sub">Resets in ${countdown(resetsAt)}</div>
    ${sub ? '<div class="gauge-sub">' + sub + '</div>' : ''}
    ${pace ? '<div class="gauge-pace">' + pace + '</div>' : ''}
  </div>`;
}

function usageGaugeHTML(label, pct, tokenCount, color) {
  const circ = 2 * Math.PI * 42;
  const offset = circ - (circ * Math.min(pct, 100) / 100);
  return `<div class="gauge">
    <div class="gauge-label">${label}</div>
    <div class="gauge-ring">
      <svg viewBox="0 0 100 100" width="100" height="100">
        <circle class="track" cx="50" cy="50" r="42"/>
        <circle class="fill" cx="50" cy="50" r="42"
          stroke="${color}" stroke-dasharray="${circ}" stroke-dashoffset="${offset}"/>
      </svg>
      <div class="gauge-pct" style="color:${color}">${pct.toFixed(0)}%</div>
    </div>
    <div class="gauge-sub">${tokenCount} tokens</div>
  </div>`;
}

// ── Render ──

function aggregate(snaps) {
  const r = { tokens:{input:0,output:0,total:0}, activity:{agent_runs:0,messages_sent:0,messages_received:0,cron_runs:0,errors:0,gateway_restarts:0}, cronJobs:[] };
  for (const s of snaps) {
    const t = s.tokens || {};
    const out = t.output || 0;
    const inp = (t.input && t.input >= out) ? t.input : Math.max(0, (t.total||0) - out);
    r.tokens.input += inp;
    r.tokens.output += out;
    r.tokens.total += t.total || 0;
    const a = s.activity || {};
    r.activity.agent_runs += a.agent_runs || 0;
    r.activity.messages_sent += a.messages_sent || 0;
    r.activity.messages_received += a.messages_received || 0;
    r.activity.cron_runs += a.cron_runs || 0;
    r.activity.errors += a.errors || 0;
    r.activity.gateway_restarts += a.gateway_restarts || 0;
    if (s.cron_jobs) r.cronJobs.push(...s.cron_jobs.map(j => ({...j, _snap_ts: s.timestamp})));
  }
  if (snaps.length > 0) {
    r.utilization = snaps[snaps.length - 1].utilization;
    r.timestamp = snaps[snaps.length - 1].timestamp;
  }
  return r;
}

function renderGauges(util, agg, ccusage) {
  const el = document.getElementById('gauges');
  if (!util) { el.innerHTML = ''; el.style.display = 'none'; return; }
  el.style.display = '';
  let h = '';
  const fh = util.five_hour;
  if (fh) h += gaugeHTML('5-Hour', fh.utilization, fh.resets_at);
  const sd = util.seven_day;
  const op = util.seven_day_opus;
  const sn = util.seven_day_sonnet;
  // Show 7-day gauge: prefer seven_day_opus if available, otherwise use seven_day
  if (op) { h += gaugeHTML('7-Day', op.utilization, op.resets_at); }
  else if (sd) { h += gaugeHTML('7-Day', sd.utilization, sd.resets_at); }
  // Only show Sonnet gauge if there's meaningful Sonnet-specific usage
  if (sn && sn.utilization > 0 && op) h += gaugeHTML('Sonnet 7d', sn.utilization, sn.resets_at);
  // OpenClaw + Claude Code usage gauges (share of combined total)
  if (agg && ccusage && ccusage.length > 0) {
    const cutoff = new Date(Date.now() - currentHours * 3600000).toISOString().slice(0, 10);
    const ccTotal = ccusage.filter(d => d.date >= cutoff).reduce((s, d) => s + (d.totalTokens || 0), 0);
    const ocTotal = agg.tokens.total || 0;
    const combined = ocTotal + ccTotal;
    if (combined > 0) {
      const ocPct = ocTotal / combined * 100;
      const ccPct = ccTotal / combined * 100;
      h += usageGaugeHTML('OpenClaw', ocPct, fmtTokens(ocTotal), '#F97316');
      h += usageGaugeHTML('Claude Code', ccPct, fmtTokens(ccTotal), '#3B82F6');
    }
  }
  if (!h) { el.innerHTML = ''; el.style.display = 'none'; return; }
  el.innerHTML = h;
}

function renderStats(agg, gwData) {
  const el = document.getElementById('stats');
  const a = agg.activity;
  // Filter gateway data by current time range
  const cutoff = new Date(Date.now() - currentHours * 3600000).toISOString().slice(0, 10);
  const gwDaily = gwData ? (gwData.aggregates || {}).daily || [] : [];
  const filteredDaily = gwDaily.filter(d => d.date >= cutoff);
  // Sum filtered daily for totals
  const gw = filteredDaily.length > 0 ? filteredDaily.reduce((acc, d) => {
    acc.input += d.input || 0; acc.output += d.output || 0;
    acc.cacheRead += d.cacheRead || 0; acc.cacheWrite += d.cacheWrite || 0;
    acc.totalTokens += d.totalTokens || 0; acc.totalCost += d.totalCost || 0;
    return acc;
  }, { input:0, output:0, cacheRead:0, cacheWrite:0, totalTokens:0, totalCost:0 }) : null;
  const totalTok = gw ? gw.totalTokens : agg.tokens.total;
  const inTok = gw ? (gw.input + gw.cacheRead) : agg.tokens.input;
  const outTok = gw ? (gw.output + gw.cacheWrite) : agg.tokens.output;
  // Filter sessions and compute latency/tools for the time range
  const filteredSessions = gwData ? (gwData.sessions || []).filter(s => {
    const dates = s.usage && s.usage.activityDates;
    if (dates && dates.length > 0) return dates.some(d => d >= cutoff);
    return false;
  }) : [];
  const filteredDailyLat = gwData ? ((gwData.aggregates || {}).dailyLatency || []).filter(d => d.date >= cutoff) : [];
  let errCount = a.errors;
  let totalToolCalls = 0;
  for (const s of filteredSessions) {
    if (s.usage && s.usage.messageCounts) errCount = Math.max(errCount, 0); // keep snapshot errors as floor
    totalToolCalls += ((s.usage || {}).toolUsage || {}).totalCalls || 0;
  }
  const sessions = filteredSessions.length;
  const cost = gw ? gw.totalCost : null;
  // Compute weighted average latency from filtered daily entries
  let lat = null;
  if (filteredDailyLat.length > 0) {
    const totalCount = filteredDailyLat.reduce((s, d) => s + d.count, 0);
    const weightedAvg = totalCount > 0 ? filteredDailyLat.reduce((s, d) => s + d.avgMs * d.count, 0) / totalCount : 0;
    const maxP95 = Math.max(...filteredDailyLat.map(d => d.p95Ms));
    lat = { avgMs: weightedAvg, p95Ms: maxP95 };
  }

  let h = '';
  if (cost != null && cost > 0) h += `<div class="stat"><div class="stat-label">Total Cost</div><div class="stat-value">$${cost.toFixed(2)}</div><div class="stat-sub">${gw ? fmtTokens(gw.cacheRead) + ' cache hits' : ''}</div></div>`;
  if (totalTok > 0) h += `<div class="stat"><div class="stat-label">Total Tokens</div><div class="stat-value">${fmtTokens(totalTok)}</div><div class="stat-sub">In: ${fmtTokens(inTok)} / Out: ${fmtTokens(outTok)}</div></div>`;
  if (a.cron_runs > 0) h += `<div class="stat"><div class="stat-label">Cron Runs</div><div class="stat-value">${a.cron_runs}</div><div class="stat-sub">${agg.cronJobs.filter(j=>j.status==='error').length} failed</div></div>`;
  if (a.messages_sent + a.messages_received > 0) h += `<div class="stat"><div class="stat-label">Messages</div><div class="stat-value">${a.messages_sent + a.messages_received}</div><div class="stat-sub">Sent: ${a.messages_sent} / Recv: ${a.messages_received}</div></div>`;
  if (sessions > 0) h += `<div class="stat"><div class="stat-label">Sessions</div><div class="stat-value">${sessions}</div><div class="stat-sub">${totalToolCalls} tool calls</div></div>`;
  if (errCount > 0) h += `<div class="stat"><div class="stat-label">Errors</div><div class="stat-value" style="color:${C.red}">${errCount}</div></div>`;
  if (a.gateway_restarts > 0) h += `<div class="stat"><div class="stat-label">Gateway Restarts</div><div class="stat-value">${a.gateway_restarts}</div></div>`;
  if (!h) h = '<div class="stat"><div class="stat-label">No Activity</div><div class="stat-value" style="color:' + C.muted + '">-</div></div>';
  el.innerHTML = h;
}

function durationTrend(job, allJobs) {
  // Compare this run's duration to the average of previous runs of the same job
  const id = job.job_id;
  const dur = job.duration_ms;
  if (!dur || dur < 2000) return { text: '-', color: C.muted };
  const prev = allJobs.filter(j => j.job_id === id && j.duration_ms > 2000 && j !== job);
  if (prev.length < 1) return { text: '-', color: C.muted };
  const avg = prev.reduce((s, j) => s + j.duration_ms, 0) / prev.length;
  if (avg === 0) return { text: '-', color: C.muted };
  const pct = Math.round(((dur - avg) / avg) * 100);
  if (Math.abs(pct) < 5) return { text: '~', color: C.muted };
  if (pct > 0) return { text: '+' + pct + '%', color: pct > 30 ? C.red : C.amber };
  return { text: pct + '%', color: C.green };
}

function renderCronTable(jobs) {
  const el = document.getElementById('cronBody');
  if (!jobs.length) { el.innerHTML = '<tr><td colspan="8" style="color:' + C.muted + ';text-align:center;padding:1rem">No cron runs in period</td></tr>'; return; }
  // Most recent first
  const sorted = [...jobs].reverse().slice(0, 10);
  el.innerHTML = sorted.map(j => {
    const delIcon = j.delivered === true ? '✓' : j.delivered === false ? '✗' : '-';
    const delColor = j.delivered === true ? C.green : j.delivered === false ? C.red : C.muted;
    const trend = durationTrend(j, jobs);
    return `<tr>
    <td style="font-weight:500">${shortJobId(j.job_id)}</td>
    <td><span class="badge ${j.status === 'ok' ? 'badge-ok' : 'badge-err'}">${j.status}</span></td>
    <td style="color:${delColor};text-align:center">${delIcon}</td>
    <td style="color:${C.muted}">${j.model || '-'}</td>
    <td>${fmtDuration(j.duration_ms)}</td>
    <td style="color:${trend.color};text-align:center;font-weight:500">${trend.text}</td>
    <td>${fmtTokens(j.total_tokens)}</td>
    <td style="color:${C.muted}">${j.run_at ? fmtTime(j.run_at) : '-'}</td>
  </tr>`;
  }).join('');
}

function renderStaleness(ts) {
  const el = document.getElementById('lastUpdate');
  const warn = document.getElementById('staleWarn');
  if (!ts) { el.textContent = ''; warn.style.display = 'none'; return; }
  const d = new Date(ts);
  const ago = (Date.now() - d.getTime()) / 60000;
  el.textContent = 'Updated ' + d.toLocaleTimeString();
  if (ago > 30) {
    warn.textContent = Math.round(ago) + 'm stale';
    warn.style.display = 'inline';
  } else {
    warn.style.display = 'none';
  }
}

// ── Charts (all use resolved hex colors, not CSS vars) ──

const baseOpts = {
  responsive: true, maintainAspectRatio: false,
  animation: { duration: 300 },
  plugins: { legend: { labels: { color: C.muted, boxWidth: 10, padding: 8, font: { size: 10 } } } },
  scales: {
    x: { type:'time', grid:{ color: C.grid }, ticks:{ color: C.muted, font:{ size: 9 }, maxRotation: 0, autoSkipPadding: 20 } },
    y: { grid:{ color: C.grid }, ticks:{ color: C.muted, font:{ size: 9 } } },
  },
};

const thresholdPlugin = {
  id: 'threshold100',
  afterDraw(chart) {
    if (chart.canvas.id !== 'utilChart') return;
    const yScale = chart.scales.y;
    if (!yScale) return;
    const y = yScale.getPixelForValue(100);
    if (y < chart.chartArea.top || y > chart.chartArea.bottom) return;
    const ctx = chart.ctx;
    ctx.save(); ctx.beginPath(); ctx.setLineDash([6,4]);
    ctx.strokeStyle = 'rgba(239,68,68,0.4)'; ctx.lineWidth = 1;
    ctx.moveTo(chart.chartArea.left, y); ctx.lineTo(chart.chartArea.right, y);
    ctx.stroke(); ctx.restore();
  }
};

function updateOrCreate(id, type, datasets, yTitle, extra) {
  const ctx = document.getElementById(id);
  if (charts[id]) {
    charts[id].data.datasets = datasets;
    charts[id].update('none');
    return;
  }
  const opts = JSON.parse(JSON.stringify(baseOpts));
  if (yTitle) opts.scales.y.title = { display:true, text:yTitle, color:C.muted, font:{size:10} };
  if (extra) Object.assign(opts, extra);
  if (type === 'bar') { opts.scales.x.stacked = true; opts.scales.y.stacked = true; }
  const plugins = id === 'utilChart' ? [thresholdPlugin] : [];
  charts[id] = new Chart(ctx, { type, data:{ datasets }, options: opts, plugins });
}

function buildCharts(snaps, agg, ccusage) {
  // Filter out backfill entries for short time ranges (they compress a full day into one point)
  const chartSnaps = currentHours <= 24 ? snaps.filter(s => !s._backfill) : snaps;

  // Utilization over time
  const fiveH = [], sevenD = [];
  for (const s of chartSnaps) {
    const u = s.utilization; if (!u) continue;
    if (u.five_hour) fiveH.push({ x: s.timestamp, y: u.five_hour.utilization });
    if (u.seven_day) sevenD.push({ x: s.timestamp, y: u.seven_day.utilization });
  }
  const utilBox = document.getElementById('utilBox');
  if (fiveH.length > 0 || sevenD.length > 0) {
    utilBox.style.display = '';
    updateOrCreate('utilChart', 'line', [
      { label:'5-Hour', data:fiveH, borderColor:C.blue, backgroundColor:'rgba(59,130,246,0.08)', fill:true, tension:0.3, borderWidth:2, pointRadius:0, pointHitRadius:6 },
      { label:'7-Day', data:sevenD, borderColor:C.purple, backgroundColor:'rgba(139,92,246,0.08)', fill:true, tension:0.3, borderWidth:2, pointRadius:0, pointHitRadius:6 },
    ], 'Utilization %');
  } else {
    utilBox.style.display = 'none';
    if (charts['utilChart']) { charts['utilChart'].destroy(); delete charts['utilChart']; }
  }

  // Tokens by job (doughnut) — hide entirely when no data
  const jobTokens = {};
  for (const j of agg.cronJobs) {
    const name = shortJobId(j.job_id);
    jobTokens[name] = (jobTokens[name] || 0) + (j.total_tokens || 0);
  }
  const jobNames = Object.keys(jobTokens).sort((a,b) => jobTokens[b] - jobTokens[a]);
  const jobColors = [C.blue, C.purple, C.cyan, C.amber, C.green, C.red, '#ec4899', '#f97316'];
  if (charts['tokenJobChart']) { charts['tokenJobChart'].destroy(); delete charts['tokenJobChart']; }
  const tokenJobBox = document.getElementById('tokenJobBox');
  const tokenTimeBox = document.getElementById('tokenTimeBox');
  if (jobNames.length > 0 && Object.values(jobTokens).some(v => v > 0)) {
    tokenJobBox.style.display = '';
    tokenTimeBox.classList.remove('full');
    charts['tokenJobChart'] = new Chart(document.getElementById('tokenJobChart'), {
      type: 'doughnut',
      data: {
        labels: jobNames,
        datasets: [{ data: jobNames.map(n => jobTokens[n]), backgroundColor: jobNames.map((_,i) => jobColors[i % jobColors.length]), borderWidth: 0 }],
      },
      options: {
        responsive: true, maintainAspectRatio: false,
        plugins: { legend: { position:'right', labels:{ color:C.muted, boxWidth:10, padding:6, font:{size:10} } } },
        cutout: '60%',
      },
    });
  } else {
    tokenJobBox.style.display = 'none';
    tokenTimeBox.classList.add('full');
  }

  // Token usage over time: OpenClaw vs Claude Code (stacked bar, adaptive buckets)
  function tokenBucketKey(ts) {
    const d = new Date(ts);
    if (currentHours <= 24) {
      return d.toISOString().slice(0,13) + ':00:00Z'; // hourly
    } else if (currentHours <= 168) {
      const h = d.getUTCHours() < 12 ? '00' : '12';
      return d.toISOString().slice(0,10) + 'T' + h + ':00:00Z'; // 12h
    } else {
      return d.toISOString().slice(0,10) + 'T12:00:00Z'; // daily
    }
  }
  const ocBk = {}, ccBk = {};
  for (const s of chartSnaps) {
    const key = tokenBucketKey(s.timestamp);
    const t = s.tokens || {};
    ocBk[key] = (ocBk[key]||0) + (t.total || 0);
  }
  // Overlay Claude Code daily totals from ccusage (only at ≥7d where daily buckets make sense)
  const cutoffDate = new Date(Date.now() - currentHours * 3600000).toISOString().slice(0, 10);
  if (ccusage && currentHours > 24) {
    for (const d of ccusage) {
      if (d.date < cutoffDate) continue;
      if (currentHours <= 168) {
        const half = Math.round((d.totalTokens || 0) / 2);
        ccBk[d.date + 'T00:00:00Z'] = (ccBk[d.date + 'T00:00:00Z']||0) + half;
        ccBk[d.date + 'T12:00:00Z'] = (ccBk[d.date + 'T12:00:00Z']||0) + half;
      } else {
        ccBk[d.date + 'T12:00:00Z'] = (ccBk[d.date + 'T12:00:00Z']||0) + (d.totalTokens || 0);
      }
    }
  }
  const allKeys = [...new Set([...Object.keys(ocBk), ...Object.keys(ccBk)])].sort();
  const hasTokenData = allKeys.length > 0 && (Object.values(ocBk).some(v => v > 0) || Object.values(ccBk).some(v => v > 0));
  if (hasTokenData) {
    tokenTimeBox.style.display = '';
    const tokenDatasets = [
      { label:'OpenClaw', data:allKeys.map(k=>({x:k,y:ocBk[k]||0})), backgroundColor:'rgba(249,115,22,0.7)', borderColor:'#F97316', borderWidth:1 },
    ];
    if (currentHours > 24) {
      tokenDatasets.push({ label:'Claude Code', data:allKeys.map(k=>({x:k,y:ccBk[k]||0})), backgroundColor:'rgba(59,130,246,0.7)', borderColor:C.blue, borderWidth:1 });
    }
    updateOrCreate('tokenTimeChart', 'bar', tokenDatasets, 'Tokens');
  } else {
    tokenTimeBox.style.display = 'none';
    if (charts['tokenTimeChart']) { charts['tokenTimeChart'].destroy(); delete charts['tokenTimeChart']; }
  }

  // Activity (adaptive buckets: hourly for ≤24h, 12h for 7d, daily for 30d+)
  function activityBucketKey(ts) {
    const d = new Date(ts);
    if (currentHours <= 24) {
      return d.toISOString().slice(0,13) + ':00:00Z'; // hourly
    } else if (currentHours <= 168) {
      const h = d.getUTCHours() < 12 ? '00' : '12';
      return d.toISOString().slice(0,10) + 'T' + h + ':00:00Z'; // 12h
    } else {
      return d.toISOString().slice(0,10) + 'T12:00:00Z'; // daily (noon for centering)
    }
  }
  const sentBk = {}, recvBk = {}, cronBk = {};
  for (const s of chartSnaps) {
    const key = activityBucketKey(s.timestamp);
    const a = s.activity || {};
    sentBk[key] = (sentBk[key]||0) + (a.messages_sent||0);
    recvBk[key] = (recvBk[key]||0) + (a.messages_received||0);
    cronBk[key] = (cronBk[key]||0) + (a.cron_runs||0);
  }
  const akeys = [...new Set([...Object.keys(sentBk), ...Object.keys(recvBk), ...Object.keys(cronBk)])].sort();
  const activityBox = document.getElementById('activityBox');
  const hasActivity = akeys.length > 0 && [...Object.values(sentBk), ...Object.values(recvBk), ...Object.values(cronBk)].some(v => v > 0);
  if (hasActivity) {
    activityBox.style.display = '';
    updateOrCreate('activityChart', 'bar', [
      { label:'Sent', data:akeys.map(k=>({x:k,y:sentBk[k]||0})), backgroundColor:'rgba(59,130,246,0.7)', borderColor:C.blue, borderWidth:1 },
      { label:'Received', data:akeys.map(k=>({x:k,y:recvBk[k]||0})), backgroundColor:'rgba(34,197,94,0.7)', borderColor:C.green, borderWidth:1 },
      { label:'Cron', data:akeys.map(k=>({x:k,y:cronBk[k]||0})), backgroundColor:'rgba(245,158,11,0.7)', borderColor:C.amber, borderWidth:1 },
    ], 'Count');
  } else {
    activityBox.style.display = 'none';
    if (charts['activityChart']) { charts['activityChart'].destroy(); delete charts['activityChart']; }
  }
}

// ── Services panel ──

function fmtAgo(isoTs) {
  if (!isoTs) return '-';
  const ms = Date.now() - new Date(isoTs).getTime();
  if (ms < 0) return 'just now';
  const mins = Math.floor(ms / 60000);
  if (mins < 1) return 'just now';
  if (mins < 60) return mins + 'm ago';
  const hrs = Math.floor(mins / 60);
  if (hrs < 24) return hrs + 'h ' + (mins % 60) + 'm ago';
  const days = Math.floor(hrs / 24);
  return days + 'd ' + (hrs % 24) + 'h ago';
}

async function refreshServices() {
  try {
    const r = await fetch('/api/services');
    if (!r.ok) return;
    const d = await r.json();
    const svcs = d.services || [];
    const el = document.getElementById('servicesBody');
    if (!svcs.length) {
      el.innerHTML = '<tr><td colspan="4" style="color:' + C.muted + ';text-align:center;padding:1rem">No services found</td></tr>';
      return;
    }
    el.innerHTML = svcs.map(s => {
      const isRunning = s.status === 'running';
      const dot = isRunning ? '●' : '○';
      const dotColor = isRunning ? C.green : C.muted;
      const label = s.label.replace(/^(ai|com)\.openclaw\./, '');
      const exitCode = s.last_exit;
      const exitBadge = exitCode == null ? '<span style="color:' + C.muted + '">-</span>' :
        exitCode === 0 ? '<span class="badge badge-ok">ok</span>' :
        '<span class="badge badge-err">error (' + exitCode + ')</span>';
      return `<tr>
        <td><span style="color:${dotColor};margin-right:0.4rem">${dot}</span>${label}</td>
        <td><span class="badge ${isRunning ? 'badge-ok' : ''}" style="${isRunning ? '' : 'color:' + C.muted}">${isRunning ? 'running' : 'idle'}</span></td>
        <td style="color:${C.muted}">${fmtAgo(s.last_run)}</td>
        <td>${exitBadge}</td>
      </tr>`;
    }).join('');
  } catch(e) {}
}

// ── Upcoming cron jobs ──

function fmtRelativeTime(ms) {
  if (!ms) return '-';
  const diff = ms - Date.now();
  if (diff < 0) return 'overdue';
  const mins = Math.floor(diff / 60000);
  if (mins < 60) return mins + 'm';
  const hrs = Math.floor(mins / 60);
  if (hrs < 24) return hrs + 'h ' + (mins % 60) + 'm';
  const days = Math.floor(hrs / 24);
  return days + 'd ' + (hrs % 24) + 'h';
}

function fmtDate(ms) {
  if (!ms) return '-';
  const d = new Date(ms);
  const now = new Date();
  const tomorrow = new Date(now); tomorrow.setDate(tomorrow.getDate() + 1);
  const dayStr = d.toDateString() === now.toDateString() ? 'Today' :
                 d.toDateString() === tomorrow.toDateString() ? 'Tomorrow' :
                 d.toLocaleDateString([], { month:'short', day:'numeric' });
  return dayStr + ' ' + d.toLocaleTimeString([], { hour:'2-digit', minute:'2-digit' });
}

async function refreshUpcoming() {
  try {
    const r = await fetch('/api/cron');
    if (!r.ok) return;
    const d = await r.json();
    const jobs = d.jobs || [];
    const el = document.getElementById('upcomingBody');
    if (!jobs.length) {
      el.innerHTML = '<tr><td colspan="4" style="color:' + C.muted + ';text-align:center;padding:1rem">No cron jobs</td></tr>';
      return;
    }
    el.innerHTML = jobs.map(j => {
      const lastBadge = !j.last_status ? '<span style="color:' + C.muted + '">-</span>' :
        j.last_status === 'ok' ? '<span class="badge badge-ok">ok</span>' :
        '<span class="badge badge-err">' + j.last_status + (j.consecutive_errors > 1 ? ' (' + j.consecutive_errors + 'x)' : '') + '</span>';
      const typeLabel = j.delete_after_run ? '<span style="color:' + C.amber + '">one-shot</span>' : '<span style="color:' + C.muted + '">recurring</span>';
      const countdown = j.next_run_ms ? 'in ' + fmtRelativeTime(j.next_run_ms) : '-';
      const isOverdue = j.next_run_ms && j.next_run_ms < Date.now();
      return `<tr>
        <td style="font-weight:500">${shortJobId(j.id)}</td>
        <td style="color:${isOverdue ? C.red : C.muted}">${countdown}<br><span style="font-size:0.7rem">${fmtDate(j.next_run_ms)}</span></td>
        <td>${lastBadge}</td>
        <td>${typeLabel}</td>
      </tr>`;
    }).join('');
  } catch(e) {}
}

// ── Gateway usage ──

let gwUsageData = null;

async function fetchGatewayUsage() {
  try {
    const r = await fetch('/api/gateway-usage');
    if (!r.ok) return null;
    return await r.json();
  } catch(e) { return null; }
}

function fmtCost(n) {
  if (n == null) return '-';
  return '$' + n.toFixed(2);
}

function gwCutoffDate() {
  return new Date(Date.now() - currentHours * 3600000).toISOString().slice(0, 10);
}

function filterGwSessions(sessions) {
  const cutoff = gwCutoffDate();
  return sessions.filter(s => {
    // Include if any activity date is >= cutoff
    const dates = s.usage && s.usage.activityDates;
    if (dates && dates.length > 0) return dates.some(d => d >= cutoff);
    // Fallback: check lastActivity timestamp
    if (s.usage && s.usage.lastActivity) return new Date(s.usage.lastActivity).toISOString().slice(0,10) >= cutoff;
    return false;
  });
}

function buildGatewayCharts(gwData) {
  if (!gwData) return;
  const agg = gwData.aggregates || {};
  const cutoff = gwCutoffDate();

  // Filter daily arrays by cutoff
  const daily = (agg.daily || []).filter(d => d.date >= cutoff);

  // Filter sessions for model split and tool usage
  const filteredSessions = filterGwSessions(gwData.sessions || []);

  // Cost Over Time
  const costBox = document.getElementById('costBox');
  if (daily.length > 0 && daily.some(d => d.totalCost > 0)) {
    costBox.style.display = '';
    const cacheWriteData = daily.map(d => ({ x: d.date + 'T12:00:00Z', y: d.cacheWriteCost || 0 }));
    const cacheReadData = daily.map(d => ({ x: d.date + 'T12:00:00Z', y: d.cacheReadCost || 0 }));
    const outputData = daily.map(d => ({ x: d.date + 'T12:00:00Z', y: d.outputCost || 0 }));
    const inputData = daily.map(d => ({ x: d.date + 'T12:00:00Z', y: d.inputCost || 0 }));
    updateOrCreate('costChart', 'bar', [
      { label:'Cache Write', data:cacheWriteData, backgroundColor:'rgba(139,92,246,0.7)', borderColor:C.purple, borderWidth:1 },
      { label:'Cache Read', data:cacheReadData, backgroundColor:'rgba(6,182,212,0.7)', borderColor:C.cyan, borderWidth:1 },
      { label:'Output', data:outputData, backgroundColor:'rgba(249,115,22,0.7)', borderColor:C.orange, borderWidth:1 },
      { label:'Input', data:inputData, backgroundColor:'rgba(34,197,94,0.7)', borderColor:C.green, borderWidth:1 },
    ], 'USD');
  } else {
    costBox.style.display = 'none';
    if (charts['costChart']) { charts['costChart'].destroy(); delete charts['costChart']; }
  }

  // Model Split (doughnut) — aggregate from filtered sessions' modelUsage
  const modelTotals = {};
  for (const s of filteredSessions) {
    for (const m of (s.usage && s.usage.modelUsage) || []) {
      const key = m.model;
      if (!modelTotals[key]) modelTotals[key] = 0;
      modelTotals[key] += m.totals.totalTokens || 0;
    }
  }
  const modelBox = document.getElementById('modelBox');
  if (charts['modelChart']) { charts['modelChart'].destroy(); delete charts['modelChart']; }
  const modelNames = Object.keys(modelTotals).filter(k => modelTotals[k] > 0).sort((a,b) => modelTotals[b] - modelTotals[a]);
  if (modelNames.length > 0) {
    modelBox.style.display = '';
    const modelColors = [C.purple, C.blue, C.cyan, C.orange, C.green, C.pink, C.amber, C.red];
    charts['modelChart'] = new Chart(document.getElementById('modelChart'), {
      type: 'doughnut',
      data: {
        labels: modelNames,
        datasets: [{ data: modelNames.map(n => modelTotals[n]), backgroundColor: modelNames.map((_,i) => modelColors[i % modelColors.length]), borderWidth: 0 }],
      },
      options: {
        responsive: true, maintainAspectRatio: false,
        plugins: { legend: { position:'right', labels:{ color:C.muted, boxWidth:10, padding:6, font:{size:10} } } },
        cutout: '60%',
      },
    });
  } else {
    modelBox.style.display = 'none';
  }

  // Tool Usage (horizontal bar) — aggregate from filtered sessions' toolUsage
  const toolCounts = {};
  for (const s of filteredSessions) {
    for (const t of ((s.usage && s.usage.toolUsage) || {}).tools || []) {
      toolCounts[t.name] = (toolCounts[t.name] || 0) + t.count;
    }
  }
  const toolBox = document.getElementById('toolBox');
  if (charts['toolChart']) { charts['toolChart'].destroy(); delete charts['toolChart']; }
  const toolNames = Object.keys(toolCounts).filter(k => toolCounts[k] > 0).sort((a,b) => toolCounts[b] - toolCounts[a]);
  if (toolNames.length > 0) {
    toolBox.style.display = '';
    const toolColors = [C.blue, C.purple, C.cyan, C.orange, C.green, C.pink, C.amber, C.red];
    charts['toolChart'] = new Chart(document.getElementById('toolChart'), {
      type: 'bar',
      data: {
        labels: toolNames,
        datasets: [{ data: toolNames.map(n => toolCounts[n]), backgroundColor: toolNames.map((_,i) => toolColors[i % toolColors.length] + 'b3'), borderWidth: 0 }],
      },
      options: {
        responsive: true, maintainAspectRatio: false,
        indexAxis: 'y',
        plugins: { legend: { display: false } },
        scales: {
          x: { grid:{ color: C.grid }, ticks:{ color: C.muted, font:{ size: 9 } } },
          y: { grid:{ display: false }, ticks:{ color: C.muted, font:{ size: 10 } } },
        },
      },
    });
  } else {
    toolBox.style.display = 'none';
  }
}

// ── Refresh ──

async function refresh() {
  const [data, gwData] = await Promise.all([fetchData(currentHours), fetchGatewayUsage()]);
  gwUsageData = gwData;
  const snaps = data.snapshots || [];
  const ccusage = data.ccusage || [];

  if (snaps.length === 0 && !gwData) {
    document.getElementById('gauges').innerHTML = '<div class="loading">No data available</div>';
    document.getElementById('stats').innerHTML = '';
    document.getElementById('cronBody').innerHTML = '<tr><td colspan="7" class="loading">No data</td></tr>';
    return;
  }

  const agg = aggregate(snaps);
  renderGauges(agg.utilization, agg, ccusage);
  renderStats(agg, gwData);
  renderCronTable(agg.cronJobs);
  renderStaleness(agg.timestamp);

  if (typeof Chart !== 'undefined') {
    buildCharts(snaps, agg, ccusage);
    buildGatewayCharts(gwData);
  }
}

// ── Events ──

document.getElementById('timeControls').addEventListener('click', e => {
  if (e.target.tagName !== 'BUTTON') return;
  document.querySelectorAll('#timeControls button').forEach(b => b.classList.remove('active'));
  e.target.classList.add('active');
  currentHours = parseInt(e.target.dataset.hours);
  // Destroy all charts on time range change to rebuild cleanly
  Object.values(charts).forEach(c => { try { c.destroy(); } catch(e) {} });
  charts = {};
  refresh();
});

refresh();
refreshServices();
refreshUpcoming();
setInterval(refresh, 5 * 60 * 1000);
setInterval(refreshServices, 60 * 1000);
setInterval(refreshUpcoming, 5 * 60 * 1000);
</script>
</body>
</html>
"""

if __name__ == "__main__":
    run()
