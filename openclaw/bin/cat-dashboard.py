#!/usr/bin/env python3
"""Cat Care dashboard for Whisker and Petlibro devices."""

from __future__ import annotations

import hmac
import json
import os
import secrets
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer
from socketserver import ThreadingMixIn
from urllib.parse import parse_qs, urlparse


PORT = 8554
BIND_HOST = "0.0.0.0"
CACHE_TTL_SECONDS = 60
COMMAND_TIMEOUT_SECONDS = 35
MAX_COMMAND_BODY_BYTES = 16 * 1024
MUTATION_TOKEN = secrets.token_urlsafe(32)
MUTATION_TOKEN_PLACEHOLDER = "__CAT_DASHBOARD_MUTATION_TOKEN__"
SECRETS_CACHE_PATH = os.path.expanduser("~/.openclaw/.secrets-cache")
LITTER_ROBOT_CLI = os.path.expanduser("~/.openclaw/bin/litter-robot")
PETLIBRO_CLI = os.path.expanduser("~/.openclaw/bin/petlibro")
HOME_EVENT_ACTION_CLI = os.path.expanduser("~/.openclaw/bin/home-event-action")
HOME_EVENTCTL_CLI = os.path.expanduser("~/.openclaw/bin/home-eventctl")
LITTER_ROBOT_SELECTORS = {
    "crosstown-litter-robot": "crosstown",
    "cabin-litter-robot": "cabin",
}
PETLIBRO_FEEDER_SELECTORS = {
    "crosstown-feeder": "crosstown",
    "cabin-feeder": "cabin",
}

STATUS_CACHE: dict[str, object] = {}
STATUS_CACHE_LOCK = threading.Lock()


def _load_secrets() -> None:
    """Provide the protected runtime environment to child CLIs without logging it."""
    try:
        with open(SECRETS_CACHE_PATH, encoding="utf-8") as handle:
            for raw_line in handle:
                line = raw_line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, _, value = line.partition("=")
                os.environ.setdefault(key.strip(), value.strip())
    except FileNotFoundError:
        pass


_load_secrets()


def _iso_timestamp(timestamp: float | None = None) -> str:
    return datetime.fromtimestamp(timestamp or time.time(), timezone.utc).isoformat()


def _parse_json_output(*values: str) -> object | None:
    for value in values:
        if not value:
            continue
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            continue
    return None


def _run_json(args: list[str]) -> object:
    try:
        result = subprocess.run(
            args,
            capture_output=True,
            text=True,
            timeout=COMMAND_TIMEOUT_SECONDS,
            check=False,
        )
    except FileNotFoundError:
        return {"ok": False, "error": "integration command is not installed"}
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": "integration command timed out"}
    except OSError as exc:
        return {"ok": False, "error": f"integration command failed: {exc}"}

    stdout = (result.stdout or "").strip()
    stderr = (result.stderr or "").strip()
    payload = _parse_json_output(stdout, stderr)
    if payload is None:
        return {
            "ok": False,
            "error": "integration returned invalid JSON",
            "returncode": result.returncode,
        }
    if result.returncode != 0:
        if isinstance(payload, dict):
            payload.setdefault("ok", False)
            payload.setdefault("returncode", result.returncode)
            return payload
        return {"ok": False, "error": "integration command failed", "returncode": result.returncode}
    return payload


def collect_whisker() -> dict[str, object]:
    payload = _run_json([LITTER_ROBOT_CLI, "--json", "overview", "14"])
    if isinstance(payload, dict):
        return payload
    return {"ok": False, "error": "Whisker returned an unexpected response"}


def collect_petlibro() -> dict[str, object]:
    payload = _run_json([PETLIBRO_CLI, "--json", "status"])
    if isinstance(payload, list):
        return {"ok": True, "devices": payload}
    if isinstance(payload, dict) and isinstance(payload.get("data"), list):
        return {"ok": True, "devices": payload["data"]}
    if isinstance(payload, dict):
        payload.setdefault("ok", False)
        return payload
    return {"ok": False, "error": "Petlibro returned an unexpected response"}


def collect_feeder_automation() -> dict[str, object]:
    payload = _run_json([HOME_EVENT_ACTION_CLI, "status"])
    if not isinstance(payload, dict):
        return {"ok": False, "error": "Feeder automation returned an unexpected response"}
    output = dict(payload)
    owners: dict[str, str] = {}
    for site in PETLIBRO_FEEDER_SELECTORS.values():
        ownership = _run_json(
            [
                HOME_EVENT_ACTION_CLI,
                "ownership",
                "--site",
                site,
                "--target",
                "feeding_schedule",
            ]
        )
        owner = ownership.get("owner") if isinstance(ownership, dict) else None
        owners[site] = owner if owner in {"bus", "legacy"} else "unknown"
    output["feeding_schedule_owners"] = owners
    return output


def collect_transfer_coverage() -> dict[str, object]:
    """Return only the bounded event-bus fields the dashboard needs."""
    payload = _run_json([HOME_EVENTCTL_CLI, "status"])
    if not isinstance(payload, dict):
        return {"ok": False, "error": "Cat transfer coverage returned an unexpected response"}
    sources = payload.get("sources")
    actions = payload.get("actions")
    whisker = sources.get("whisker") if isinstance(sources, dict) else None
    observer = whisker.get("observer") if isinstance(whisker, dict) else None
    sites = observer.get("sites") if isinstance(observer, dict) else None
    counts = actions.get("counts") if isinstance(actions, dict) else None
    if not isinstance(sites, dict) or not isinstance(counts, dict):
        return {"ok": False, "error": "Cat transfer coverage is unavailable"}

    site_status: dict[str, dict[str, object]] = {}
    for site in sorted(set(PETLIBRO_FEEDER_SELECTORS.values())):
        record = sites.get(site)
        if not isinstance(record, dict):
            return {"ok": False, "error": "Cat transfer coverage is incomplete"}
        site_status[site] = {
            "enabled": record.get("enabled") is True,
            "baselined": record.get("baselined") is True,
            "health": str(record.get("health", "unknown")),
            "poll_age_seconds": record.get("poll_age_seconds"),
        }
    return {
        "ok": True,
        "bus_health": str(payload.get("health", "unknown")),
        "observer_health": str(observer.get("health", "unknown")),
        "sites": site_status,
        "accepted_events": whisker.get("accepted", 0),
        "pending_actions": counts.get("pending", 0),
        "unknown_actions": counts.get("outcome_unknown", 0),
    }


def collect_status(*, refresh: bool = False) -> dict[str, object]:
    now = time.time()
    with STATUS_CACHE_LOCK:
        cached_at = float(STATUS_CACHE.get("cached_at", 0))
        cached_bundle = STATUS_CACHE.get("bundle")
        if not refresh and isinstance(cached_bundle, dict) and now - cached_at < CACHE_TTL_SECONDS:
            return cached_bundle

    with ThreadPoolExecutor(max_workers=4) as pool:
        whisker_future = pool.submit(collect_whisker)
        petlibro_future = pool.submit(collect_petlibro)
        automation_future = pool.submit(collect_feeder_automation)
        transfer_future = pool.submit(collect_transfer_coverage)
        bundle: dict[str, object] = {
            "meta": {
                "timestamp": _iso_timestamp(),
                "cache_ttl_seconds": CACHE_TTL_SECONDS,
            },
            "whisker": whisker_future.result(),
            "petlibro": petlibro_future.result(),
            "automation": automation_future.result(),
            "transfer": transfer_future.result(),
        }

    with STATUS_CACHE_LOCK:
        STATUS_CACHE["cached_at"] = now
        STATUS_CACHE["bundle"] = bundle
    return bundle


def build_command(payload: object) -> list[str]:
    if not isinstance(payload, dict):
        raise ValueError("command payload must be an object")
    device = payload.get("device")
    action = payload.get("action")
    selector = payload.get("selector")

    if device == "whisker" and action == "clean":
        if selector not in LITTER_ROBOT_SELECTORS:
            raise ValueError("use an exact enrolled Litter-Robot selector")
        if set(payload) != {"device", "action", "selector"}:
            raise ValueError("unexpected Whisker command fields")
        return [LITTER_ROBOT_CLI, "--json", "clean", str(selector)]

    if device == "petlibro" and action == "feed":
        if selector not in PETLIBRO_FEEDER_SELECTORS:
            raise ValueError("use an exact Petlibro feeder selector")
        if set(payload) != {"device", "action", "selector", "portions"}:
            raise ValueError("unexpected Petlibro command fields")
        portions = payload.get("portions")
        if isinstance(portions, bool) or not isinstance(portions, int) or not 1 <= portions <= 3:
            raise ValueError("portions must be an integer from 1 to 3")
        return [PETLIBRO_CLI, "--json", "feed", str(selector), str(portions)]

    if device == "petlibro" and action == "schedule":
        if selector not in PETLIBRO_FEEDER_SELECTORS:
            raise ValueError("use an exact Petlibro feeder selector")
        if set(payload) != {"device", "action", "selector", "state"}:
            raise ValueError("unexpected Petlibro schedule fields")
        state = payload.get("state")
        if state not in {"on", "off"}:
            raise ValueError("scheduled feeding state must be on or off")
        return [PETLIBRO_CLI, "--json", "schedule-set", str(selector), str(state)]

    raise ValueError("unsupported cat-care command")


def execute_command(payload: object) -> tuple[int, dict[str, object]]:
    try:
        args = build_command(payload)
    except ValueError as exc:
        return 400, {"ok": False, "error": str(exc)}

    result = _run_json(args)
    ok = isinstance(result, dict) and bool(result.get("ok", result.get("success", False)))
    if ok:
        with STATUS_CACHE_LOCK:
            STATUS_CACHE.clear()
        return 200, {"ok": True, "result": result}
    if isinstance(result, dict):
        return 502, {
            "ok": False,
            "result": result,
            "error": result.get("message", result.get("error", "command failed")),
        }
    return 502, {"ok": False, "error": "command failed"}


class DashboardHandler(BaseHTTPRequestHandler):
    def log_message(self, _format: str, *args: object) -> None:
        sys.stderr.write(f"{self.address_string()} {args[0]}\n")

    def do_OPTIONS(self) -> None:
        self.send_response(204)
        self.send_header("Allow", "GET, POST, OPTIONS")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/") or "/"
        if path == "/":
            self._serve_html()
            return
        if path == "/api/status":
            query = parse_qs(parsed.query)
            refresh = query.get("refresh", ["false"])[0].lower() in {"1", "true", "yes"}
            self._respond(200, collect_status(refresh=refresh))
            return
        self._respond(404, {"ok": False, "error": "not found"})

    def do_POST(self) -> None:
        path = urlparse(self.path).path.rstrip("/") or "/"
        if path != "/api/command":
            self._respond(404, {"ok": False, "error": "not found"})
            return
        if not self._origin_is_same_host():
            self._respond(403, {"ok": False, "error": "cross-origin mutation denied"})
            return
        if not self._has_valid_mutation_token():
            self._respond(
                401,
                {"ok": False, "error": "mutation authorization required"},
                extra_headers=(("WWW-Authenticate", "Bearer"),),
            )
            return
        try:
            content_length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            self._respond(400, {"ok": False, "error": "invalid Content-Length"})
            return
        if not 0 <= content_length <= MAX_COMMAND_BODY_BYTES:
            self._respond(413, {"ok": False, "error": "command body too large"})
            return
        try:
            body = self.rfile.read(content_length) if content_length else b"{}"
            payload = json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            self._respond(400, {"ok": False, "error": "invalid JSON body"})
            return
        code, response = execute_command(payload)
        self._respond(code, response)

    def _origin_is_same_host(self) -> bool:
        origin = self.headers.get("Origin")
        if not origin:
            return True
        host = self.headers.get("Host")
        if not host:
            return False
        parsed = urlparse(origin)
        return (
            parsed.scheme in {"http", "https"}
            and parsed.netloc.casefold() == host.casefold()
            and parsed.username is None
            and parsed.password is None
            and parsed.path in {"", "/"}
            and not parsed.params
            and not parsed.query
            and not parsed.fragment
        )

    def _has_valid_mutation_token(self) -> bool:
        authorization = self.headers.get("Authorization", "")
        scheme, separator, token = authorization.partition(" ")
        return separator == " " and scheme == "Bearer" and bool(token) and hmac.compare_digest(token, MUTATION_TOKEN)

    def _respond(
        self,
        code: int,
        data: object,
        *,
        extra_headers: tuple[tuple[str, str], ...] = (),
    ) -> None:
        body = json.dumps(data).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        for name, value in extra_headers:
            self.send_header(name, value)
        self.end_headers()
        self.wfile.write(body)

    def _serve_html(self) -> None:
        token_literal = json.dumps(MUTATION_TOKEN)
        body = DASHBOARD_HTML.replace(MUTATION_TOKEN_PLACEHOLDER, token_literal).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Security-Policy", "frame-ancestors 'none'")
        self.send_header("X-Frame-Options", "DENY")
        self.end_headers()
        self.wfile.write(body)


class ThreadedHTTPServer(ThreadingMixIn, HTTPServer):
    daemon_threads = True


DASHBOARD_HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Cat Care</title>
  <style>
    :root {
      color-scheme: dark;
      --ink: #f8f4eb; --muted: #a9a69f; --line: rgba(255,255,255,.09);
      --panel: rgba(28,31,31,.88); --panel-2: #222826; --mint: #95d5b2;
      --peach: #f4a261; --gold: #e9c46a; --red: #ee8172; --bg: #101312;
    }
    * { box-sizing: border-box; }
    body { margin: 0; min-height: 100vh; background: radial-gradient(circle at 15% 0%, #263a33 0, transparent 32rem), var(--bg); color: var(--ink); font: 15px/1.45 -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; }
    button, select { font: inherit; }
    .shell { width: min(1500px, calc(100% - 40px)); margin: 0 auto; padding: 36px 0 60px; }
    header { display: flex; align-items: end; justify-content: space-between; gap: 20px; margin-bottom: 28px; }
    .eyebrow { color: var(--mint); text-transform: uppercase; letter-spacing: .14em; font-size: 12px; font-weight: 700; }
    h1 { font-family: Georgia, serif; font-size: clamp(36px, 5vw, 62px); font-weight: 500; line-height: 1; margin: 7px 0 9px; letter-spacing: -.035em; }
    .subtitle, .muted { color: var(--muted); }
    .toolbar { display: flex; gap: 9px; align-items: center; flex-wrap: wrap; justify-content: end; }
    .segmented { display: flex; padding: 4px; border: 1px solid var(--line); background: rgba(0,0,0,.22); border-radius: 13px; }
    .segmented button, .refresh { border: 0; color: var(--muted); background: transparent; border-radius: 9px; padding: 8px 13px; cursor: pointer; }
    .segmented button.active { color: #132018; background: var(--mint); font-weight: 700; }
    .refresh { border: 1px solid var(--line); color: var(--ink); }
    .notice { display: none; border: 1px solid rgba(238,129,114,.35); background: rgba(238,129,114,.09); color: #ffd7d0; border-radius: 14px; padding: 12px 15px; margin-bottom: 18px; }
    .notice.show { display: block; }
    .section-head { display: flex; justify-content: space-between; align-items: baseline; gap: 12px; margin: 30px 0 13px; }
    h2 { font: 500 23px/1.2 Georgia, serif; margin: 0; }
    .grid { display: grid; grid-template-columns: repeat(12, 1fr); gap: 14px; }
    .card { grid-column: span 4; min-width: 0; border: 1px solid var(--line); background: linear-gradient(145deg, rgba(39,44,42,.96), var(--panel)); border-radius: 19px; padding: 19px; box-shadow: 0 18px 45px rgba(0,0,0,.16); }
    .cat-card { min-height: 166px; position: relative; overflow: hidden; }
    .cat-card::after { content: ""; position: absolute; width: 115px; height: 115px; right: -35px; bottom: -45px; border: 26px solid rgba(149,213,178,.08); border-radius: 50%; }
    .card-top { display: flex; justify-content: space-between; gap: 12px; align-items: start; }
    .label { font-size: 12px; color: var(--muted); text-transform: uppercase; letter-spacing: .09em; }
    .value { font-size: 31px; letter-spacing: -.04em; margin-top: 14px; }
    .unit { font-size: 15px; color: var(--muted); margin-left: 3px; }
    .pill { border-radius: 99px; padding: 4px 9px; background: rgba(149,213,178,.12); color: var(--mint); font-size: 12px; white-space: nowrap; }
    .pill.warn { color: var(--gold); background: rgba(233,196,106,.12); }
    .pill.bad { color: var(--red); background: rgba(238,129,114,.12); }
    .metric-row { display: grid; grid-template-columns: repeat(3, 1fr); border-top: 1px solid var(--line); margin-top: 17px; padding-top: 14px; gap: 10px; }
    .metric b { display: block; font-size: 17px; margin-top: 3px; }
    .automation-card { grid-column: span 12; min-height: 0; }
    .automation-card .metric-row { grid-template-columns: repeat(4, 1fr); }
    .automation-copy { max-width: 850px; margin: 9px 0 0; color: var(--muted); }
    .direction-row { display: flex; flex-wrap: wrap; gap: 9px; margin-top: 15px; }
    .direction { display: flex; align-items: center; gap: 9px; border: 1px solid var(--line); border-radius: 11px; padding: 7px 9px 7px 12px; }
    .device-card { min-height: 245px; }
    .device-card h3 { margin: 4px 0 0; font-size: 19px; }
    .site { color: var(--peach); text-transform: capitalize; font-size: 13px; }
    .bar { height: 7px; margin-top: 7px; background: rgba(255,255,255,.07); border-radius: 99px; overflow: hidden; }
    .bar span { height: 100%; display: block; background: var(--mint); border-radius: inherit; }
    .bar.warn span { background: var(--gold); }
    .actions { display: flex; gap: 8px; align-items: center; margin-top: 17px; }
    .schedule-actions { justify-content: space-between; border-top: 1px solid var(--line); padding-top: 14px; }
    .schedule-label { display: flex; flex-direction: column; gap: 2px; }
    .schedule-owner { color: var(--muted); font-size: 11px; }
    .action { border: 1px solid rgba(149,213,178,.3); color: var(--mint); background: rgba(149,213,178,.06); border-radius: 10px; padding: 8px 12px; cursor: pointer; }
    .action:disabled { opacity: .38; cursor: not-allowed; }
    select { color: var(--ink); border: 1px solid var(--line); background: #1c211f; border-radius: 10px; padding: 8px; }
    .timeline { grid-column: span 12; padding: 5px 19px; }
    .event { display: grid; grid-template-columns: 90px minmax(120px, .7fr) 1fr; gap: 18px; padding: 13px 0; border-bottom: 1px solid var(--line); align-items: center; }
    .event:last-child { border-bottom: 0; }
    .event time { color: var(--muted); font-variant-numeric: tabular-nums; }
    .empty { grid-column: span 12; border: 1px dashed rgba(255,255,255,.14); border-radius: 18px; padding: 27px; color: var(--muted); text-align: center; }
    .footer { margin-top: 28px; color: #777d79; font-size: 12px; display: flex; justify-content: space-between; }
    .toast { position: fixed; right: 22px; bottom: 22px; max-width: 360px; border: 1px solid var(--line); background: #29312e; color: var(--ink); padding: 13px 16px; border-radius: 12px; box-shadow: 0 12px 40px #0008; opacity: 0; transform: translateY(12px); pointer-events: none; transition: .2s ease; }
    .toast.show { opacity: 1; transform: none; }
    @media (max-width: 900px) { .card { grid-column: span 6; } header { align-items: start; flex-direction: column; } .toolbar { justify-content: start; } }
    @media (max-width: 600px) { .shell { width: min(100% - 24px, 1500px); padding-top: 22px; } .card { grid-column: span 12; } .automation-card .metric-row { grid-template-columns: repeat(2, 1fr); } .event { grid-template-columns: 75px 1fr; } .event .event-action { grid-column: 2; } }
  </style>
</head>
<body>
  <main class="shell">
    <header>
      <div><div class="eyebrow">Two homes · one care loop</div><h1>Cat Care</h1><div class="subtitle">Weights, litter visits, meals, and water at a glance.</div></div>
      <div class="toolbar">
        <div class="segmented" aria-label="Location filter">
          <button class="active" data-site="all">Both</button><button data-site="crosstown">Crosstown</button><button data-site="cabin">Cabin</button>
        </div>
        <button class="refresh" id="refresh">Refresh</button>
      </div>
    </header>
    <div class="notice" id="notice"></div>

    <section><div class="section-head"><h2>Transfer automation</h2><span class="muted" id="automation-summary"></span></div><div class="grid" id="automation"></div></section>
    <section><div class="section-head"><h2>The cats</h2><span class="muted" id="cat-summary"></span></div><div class="grid" id="cats"></div></section>
    <section><div class="section-head"><h2>Care stations</h2><span class="muted">Whisker · Petlibro</span></div><div class="grid" id="devices"></div></section>
    <section><div class="section-head"><h2>Recent litter-box activity</h2><span class="muted">Latest 14 events per home</span></div><div class="grid" id="activity"></div></section>
    <div class="footer"><span>Local to the home network and tailnet</span><span id="updated">Loading…</span></div>
  </main>
  <div class="toast" id="toast" role="status" aria-live="polite"></div>
  <script>
    const MUTATION_TOKEN = __CAT_DASHBOARD_MUTATION_TOKEN__;
    let state = null;
    let selectedSite = 'all';
    const esc = value => String(value ?? '').replace(/[&<>'"]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;',"'":'&#39;','"':'&quot;'}[c]));
    const siteName = site => site === 'cabin' ? 'Cabin' : site === 'crosstown' ? 'Crosstown' : 'Unknown';
    const number = (value, fallback='—') => value === null || value === undefined || value === '?' || Number.isNaN(Number(value)) ? fallback : Number(value);
    const pct = value => Math.max(0, Math.min(100, Number(value) || 0));
    const visible = site => selectedSite === 'all' || selectedSite === site;
    const statusPill = (online, text='Online') => `<span class="pill ${online ? '' : 'bad'}">${online ? esc(text) : 'Offline'}</span>`;
    const metric = (label, value) => `<div class="metric"><span class="label">${esc(label)}</span><b>${esc(value)}</b></div>`;

    function weightTrend(pet) {
      const samples = [...(pet.recent_weights || [])].filter(x => Number.isFinite(Number(x.weight_lbs)) && x.timestamp).sort((a,b) => new Date(a.timestamp)-new Date(b.timestamp));
      if (samples.length < 2) return 'No trend yet';
      const delta = Number(samples.at(-1).weight_lbs) - Number(samples[0].weight_lbs);
      if (Math.abs(delta) < .05) return 'Steady';
      return `${delta > 0 ? '+' : ''}${delta.toFixed(1)} lb recent`;
    }

    function renderAutomation() {
      const root = document.getElementById('automation');
      const automation = state?.automation || {};
      const transfer = state?.transfer || {};
      const owners = automation.feeding_schedule_owners || {};
      const sites = ['cabin', 'crosstown'];
      const activeSites = sites.filter(site => owners[site] === 'bus');
      const pairedCoverage = transfer.ok === true && transfer.bus_health === 'ok' && transfer.observer_health === 'ok' && sites.every(site => {
        const record = transfer.sites?.[site];
        const age = Number(record?.poll_age_seconds);
        return record?.enabled === true && record?.baselined === true && record?.health === 'ok' && record?.poll_age_seconds !== null && Number.isFinite(age) && age <= 300;
      });
      const managedSites = Object.keys(automation.feeder_suspensions?.sites || {});
      const pending = number(transfer.pending_actions, 0);
      const unknown = number(transfer.unknown_actions, 0);
      const coverageRequired = activeSites.length > 0;
      const attention = automation.ok === false || transfer.ok === false || Number(unknown) > 0 || (coverageRequired && !pairedCoverage);
      const fullyActive = activeSites.length === sites.length;
      const label = attention ? 'Attention' : fullyActive ? 'Active' : 'Standby';
      const pillClass = attention ? 'bad' : fullyActive ? '' : 'warn';
      const description = fullyActive && pairedCoverage
        ? 'Both directions are armed. A qualifying litter-box visit can suspend scheduled meals at a confirmed-vacant home; only an OpenClaw-owned pause will auto-resume.'
        : fullyActive
          ? 'Both feeder directions are armed, but paired litter coverage is not currently ready. Transfer actions fail closed.'
          : 'One or more feeder directions are not owned by the event bus. Manual schedule controls remain available.';
      document.getElementById('automation-summary').textContent = fullyActive ? 'Dual-direction guard' : `${activeSites.length} of ${sites.length} directions active`;
      const directions = sites.map(site => `<span class="direction"><b>Vacant ${esc(siteName(site))}</b><span class="pill ${owners[site] === 'bus' ? '' : 'warn'}">${owners[site] === 'bus' ? 'Active' : 'Disabled'}</span></span>`).join('');
      root.innerHTML = `<article class="card automation-card">
        <div class="card-top"><div><div class="label">Home event bus</div><h3>Feeder transfer protection</h3></div><span class="pill ${pillClass}">${esc(label)}</span></div>
        <p class="automation-copy">${esc(description)}</p>
        <div class="metric-row">${metric('Directions', `${activeSites.length}/${sites.length}`)}${metric('Litter coverage', pairedCoverage ? 'Paired' : 'Unavailable')}${metric('Managed pauses', managedSites.length)}${metric('Pending actions', pending)}</div>
        <div class="direction-row">${directions}</div>
      </article>`;
    }

    function renderCats() {
      const root = document.getElementById('cats');
      const pets = state?.whisker?.pets || [];
      document.getElementById('cat-summary').textContent = pets.length ? `${pets.length} profile${pets.length === 1 ? '' : 's'} from Whisker` : '';
      root.innerHTML = pets.length ? pets.map(pet => `<article class="card cat-card">
        <div class="card-top"><div><div class="label">Cat profile</div><h3>${esc(pet.name || 'Cat')}</h3></div><span class="pill">Whisker</span></div>
        <div class="value">${esc(number(pet.weight_lbs))}<span class="unit">lb</span></div>
        <div class="muted">${esc(weightTrend(pet))}</div>
      </article>`).join('') : '<div class="empty">Cat profiles will appear when Whisker reports them.</div>';
    }

    function whiskerCard(robot) {
      const waste = number(robot.waste_level_pct);
      const litter = number(robot.litter_level_pct);
      const wasteClass = Number(waste) >= 80 ? 'warn' : '';
      return `<article class="card device-card" data-location="${esc(robot.site)}">
        <div class="card-top"><div><div class="site">${esc(siteName(robot.site))}</div><h3>Litter-Robot</h3></div>${statusPill(robot.is_online, robot.status_text || robot.status || 'Online')}</div>
        <div class="metric-row">${metric('Waste', waste === '—' ? waste : `${waste}%`)}${metric('Litter', litter === '—' ? litter : `${litter}%`)}${metric('Cycles', number(robot.cycle_count))}</div>
        <div class="label" style="margin-top:16px">Waste drawer</div><div class="bar ${wasteClass}"><span style="width:${pct(waste)}%"></span></div>
        <div class="actions"><button class="action" ${robot.is_online ? '' : 'disabled'} data-command="clean" data-selector="${esc(robot.alias)}">Clean now</button><span class="muted">${robot.waste_full ? 'Drawer needs attention' : `${number(robot.clean_wait_minutes)} min wait`}</span></div>
      </article>`;
    }

    function petlibroCard(device) {
      const feeder = device.type === 'feeder';
      const selector = device.selector || '';
      const site = selector.startsWith('cabin-') ? 'cabin' : selector.startsWith('crosstown-') ? 'crosstown' : 'unknown';
      const metrics = feeder
        ? metric('Food', device.foodLevel || '—') + metric('Next meal', device.nextFeedTime || '—') + metric('Portions', number(device.nextFeedPortions))
        : metric('Water', device.waterPercent === '?' ? '—' : `${number(device.waterPercent)}%`) + metric('Today', device.todayDrinkMl === '?' ? '—' : `${number(device.todayDrinkMl)} ml`) + metric('Filter', device.filterDaysRemaining === '?' ? '—' : `${number(device.filterDaysRemaining)} d`);
      const enrolledFeeder = feeder && ['crosstown-feeder','cabin-feeder'].includes(selector);
      const action = enrolledFeeder
        ? `<div class="actions"><select aria-label="Portions" data-portions-for="${esc(selector)}"><option value="1">1 portion</option><option value="2">2 portions</option><option value="3">3 portions</option></select><button class="action" ${device.online ? '' : 'disabled'} data-command="feed" data-selector="${esc(selector)}">Feed now</button></div>` : '';
      const scheduleKnown = typeof device.scheduleEnabled === 'boolean';
      const scheduleEnabled = device.scheduleEnabled === true;
      const managed = state?.automation?.feeder_suspensions?.sites?.[site];
      const managedHere = managed?.selector === selector;
      const managedAttention = managedHere && managed.attention === true;
      const automationOwned = state?.automation?.feeding_schedule_owners?.[site] === 'bus';
      const scheduleLabel = managedAttention ? 'Automation attention' : managedHere && managed.phase === 'restoring' ? 'Auto-resume verifying' : managedHere ? 'Paused · Auto-resume armed' : scheduleKnown ? (scheduleEnabled ? 'Enabled' : 'Paused') : 'Unavailable';
      const schedule = enrolledFeeder ? `<div class="actions schedule-actions"><div class="schedule-label"><span class="label">Scheduled meals</span><span class="pill ${scheduleKnown && scheduleEnabled && !managedAttention ? '' : 'warn'}">${esc(scheduleLabel)}</span><span class="schedule-owner">${automationOwned ? 'Vacancy automation active' : 'Manual schedule only'}</span></div><button class="action" ${device.online && scheduleKnown && !managedHere ? '' : 'disabled'} data-command="schedule" data-state="${scheduleEnabled ? 'off' : 'on'}" data-selector="${esc(selector)}">${managedHere ? 'Vacancy-managed' : scheduleEnabled ? 'Pause schedule' : 'Resume schedule'}</button></div>` : '';
      return `<article class="card device-card" data-location="${esc(site)}"><div class="card-top"><div><div class="site">${esc(siteName(site))}</div><h3>${feeder ? 'Feeder' : 'Fountain'}</h3><div class="muted">${esc(device.name || device.model || 'Petlibro')}</div></div>${statusPill(device.online)}</div><div class="metric-row">${metrics}</div>${schedule}${action}</article>`;
    }

    function renderDevices() {
      const robots = (state?.whisker?.robots || []).filter(r => visible(r.site));
      const petlibro = (state?.petlibro?.devices || []).filter(d => {
        const site = String(d.selector || '').split('-')[0]; return visible(site);
      });
      const cards = [...robots.map(whiskerCard), ...petlibro.map(petlibroCard)];
      if (!petlibro.length && selectedSite === 'all') cards.push('<div class="empty">Petlibro is connected, but no feeder or fountain is currently reporting. Cards will appear automatically when devices return.</div>');
      document.getElementById('devices').innerHTML = cards.join('') || '<div class="empty">No care stations are reporting for this location.</div>';
    }

    function renderActivity() {
      const events = (state?.whisker?.robots || []).filter(r => visible(r.site)).flatMap(r => (r.recent_activity || []).map(e => ({...e, site:r.site}))).sort((a,b) => new Date(b.timestamp)-new Date(a.timestamp)).slice(0, 28);
      document.getElementById('activity').innerHTML = events.length ? `<div class="card timeline">${events.map(event => {
        const date = new Date(event.timestamp); const when = Number.isNaN(date.getTime()) ? 'Unknown' : date.toLocaleTimeString([], {hour:'numeric',minute:'2-digit'});
        const day = Number.isNaN(date.getTime()) ? '' : date.toLocaleDateString([], {month:'short',day:'numeric'});
        return `<div class="event"><time>${esc(when)}<br><span>${esc(day)}</span></time><strong>${esc(siteName(event.site))}</strong><span class="event-action">${esc(event.action || 'Litter-box activity')}</span></div>`;
      }).join('')}</div>` : '<div class="empty">No recent litter-box activity is available for this location.</div>';
    }

    function renderNotice() {
      const messages = [];
      if (state?.whisker?.error) messages.push(`Whisker: ${state.whisker.error}`);
      if (state?.petlibro?.error) messages.push(`Petlibro: ${state.petlibro.error}`);
      if (state?.automation?.ok === false) messages.push(`Feeder automation: ${state.automation.error || 'status unavailable'}`);
      if (state?.transfer?.ok === false) messages.push(`Cat transfer coverage: ${state.transfer.error || 'status unavailable'}`);
      if (Number(state?.transfer?.unknown_actions) > 0) messages.push('Feeder automation has an unknown action outcome.');
      for (const site of ['cabin', 'crosstown']) {
        const owner = state?.automation?.feeding_schedule_owners?.[site];
        const coverage = state?.transfer?.sites?.[site];
        const age = Number(coverage?.poll_age_seconds);
        if (owner && owner !== 'bus') messages.push(`${siteName(site)} feeder transfer automation is not active.`);
        if (coverage && (coverage.enabled !== true || coverage.baselined !== true || coverage.health !== 'ok' || coverage.poll_age_seconds === null || !Number.isFinite(age) || age > 300)) messages.push(`${siteName(site)} litter evidence is not ready.`);
      }
      for (const [site, managed] of Object.entries(state?.automation?.feeder_suspensions?.sites || {})) { if (managed.attention) messages.push(`${siteName(site)} feeder automation needs review (${managed.last_error || 'unknown state'}).`); }
      for (const robot of state?.whisker?.robots || []) { if (!robot.is_online) messages.push(`${siteName(robot.site)} Litter-Robot is offline.`); if (robot.waste_full || Number(robot.waste_level_pct) >= 80) messages.push(`${siteName(robot.site)} waste drawer needs attention.`); }
      const notice = document.getElementById('notice'); notice.textContent = messages.join(' '); notice.classList.toggle('show', Boolean(messages.length));
    }

    function render() { renderNotice(); renderAutomation(); renderCats(); renderDevices(); renderActivity(); document.getElementById('updated').textContent = state?.meta?.timestamp ? `Updated ${new Date(state.meta.timestamp).toLocaleTimeString([], {hour:'numeric',minute:'2-digit'})}` : 'Update unavailable'; }
    function toast(message) { const node = document.getElementById('toast'); node.textContent = message; node.classList.add('show'); clearTimeout(toast.timer); toast.timer = setTimeout(() => node.classList.remove('show'), 3500); }

    async function load(refresh=false) {
      document.getElementById('refresh').disabled = true;
      try { const response = await fetch(`/api/status${refresh ? '?refresh=true' : ''}`, {cache:'no-store'}); if (!response.ok) throw new Error(`HTTP ${response.status}`); state = await response.json(); render(); }
      catch (error) { document.getElementById('notice').textContent = `Dashboard refresh failed: ${error.message}`; document.getElementById('notice').classList.add('show'); }
      finally { document.getElementById('refresh').disabled = false; }
    }

    async function mutate(payload, button) {
      button.disabled = true;
      try {
        const response = await fetch('/api/command', {method:'POST', headers:{'Content-Type':'application/json','Authorization':`Bearer ${MUTATION_TOKEN}`}, body:JSON.stringify(payload)});
        const result = await response.json(); if (!response.ok || !result.ok) throw new Error(result.result?.message || result.error || 'Command failed');
        const message = payload.action === 'feed' ? 'Feed request confirmed.' : payload.action === 'schedule' ? `Scheduled feeding ${payload.state === 'on' ? 'resumed' : 'paused'} and verified.` : 'Clean cycle confirmed.';
        toast(message); await load(true);
      } catch (error) { toast(error.message); } finally { button.disabled = false; }
    }

    document.querySelector('.segmented').addEventListener('click', event => { const button = event.target.closest('button[data-site]'); if (!button) return; selectedSite = button.dataset.site; document.querySelectorAll('.segmented button').forEach(x => x.classList.toggle('active', x === button)); renderDevices(); renderActivity(); });
    document.getElementById('refresh').addEventListener('click', () => load(true));
    document.getElementById('devices').addEventListener('click', event => { const button = event.target.closest('button[data-command]'); if (!button) return; const payload = {device: button.dataset.command === 'clean' ? 'whisker' : 'petlibro', action:button.dataset.command, selector:button.dataset.selector}; if (payload.action === 'feed') { const select = document.querySelector(`[data-portions-for="${CSS.escape(payload.selector)}"]`); payload.portions = Number(select.value); } if (payload.action === 'schedule') { payload.state = button.dataset.state; const location = siteName(String(payload.selector).split('-')[0]); const verb = payload.state === 'on' ? 'Resume' : 'Pause'; if (!window.confirm(`${verb} all scheduled meals at ${location}? Manual feeding remains available.`)) return; } mutate(payload, button); });
    load(); setInterval(() => load(), 60000);
  </script>
</body>
</html>"""


def main() -> None:
    server = ThreadedHTTPServer((BIND_HOST, PORT), DashboardHandler)
    print(f"Cat Care dashboard listening on http://{BIND_HOST}:{PORT}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
