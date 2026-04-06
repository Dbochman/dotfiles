#!/usr/bin/env python3
"""Home Control Plane Dashboard — single-file HTTP server with embedded UI."""

import json
import os
import signal
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer
from socketserver import ThreadingMixIn
from urllib.parse import parse_qs, urlparse


PORT = 8558
CACHE_TTL_SECONDS = 60
COMMAND_TIMEOUT_SECONDS = 30
PRESENCE_STATE_PATH = os.path.expanduser("~/.openclaw/presence/state.json")
NEST_HISTORY_DIR = os.path.expanduser("~/.openclaw/nest-history")
DOG_WALK_STATE_PATH = os.path.expanduser("~/.openclaw/dog-walk/state.json")
SECRETS_CACHE_PATH = os.path.expanduser("~/.openclaw/.secrets-cache")
CATT_BIN = os.path.expanduser("~/.local/bin/catt")
CAMERA_SNAP_DIR = os.path.expanduser("~/.openclaw/camera-snaps")
_SPEAKER_IPS = {"bedroom": "192.168.165.146", "living room": "192.168.165.113"}
_CABIN_SPEAKER_IPS = {"kitchen": "192.168.1.66", "bedroom": "192.168.1.163"}


def _load_secrets():
    """Source ~/.openclaw/.secrets-cache into os.environ so CLIs get their env vars."""
    try:
        with open(SECRETS_CACHE_PATH) as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, _, value = line.partition("=")
                os.environ.setdefault(key.strip(), value.strip())
    except FileNotFoundError:
        pass


_load_secrets()
STATUS_CACHE = {}
STATUS_CACHE_LOCK = threading.Lock()


def _iso_timestamp(timestamp=None):
    if timestamp is None:
        timestamp = time.time()
    return datetime.fromtimestamp(timestamp, timezone.utc).isoformat()


def _read_json_file(path):
    try:
        with open(path) as handle:
            data = json.load(handle)
    except FileNotFoundError:
        return {"error": f"file not found: {path}"}
    except OSError as exc:
        return {"error": f"unable to read {path}: {exc}"}
    except json.JSONDecodeError as exc:
        return {"error": f"invalid JSON in {path}: {exc}"}

    if isinstance(data, dict):
        return data
    return {"data": data}


def _read_latest_jsonl_record(path):
    try:
        latest_line = ""
        with open(path) as handle:
            for line in handle:
                stripped = line.strip()
                if stripped:
                    latest_line = stripped
    except FileNotFoundError:
        return {"error": f"file not found: {path}"}
    except OSError as exc:
        return {"error": f"unable to read {path}: {exc}"}

    if not latest_line:
        return {"error": f"no records in {path}"}

    try:
        data = json.loads(latest_line)
    except json.JSONDecodeError as exc:
        return {"error": f"invalid JSONL record in {path}: {exc}"}

    if isinstance(data, dict):
        return data
    return {"data": data}


def _run_cli(args, parse_json=False):
    try:
        result = subprocess.run(
            args,
            capture_output=True,
            timeout=COMMAND_TIMEOUT_SECONDS,
            text=True,
        )
    except FileNotFoundError as exc:
        return {"error": str(exc)}
    except subprocess.TimeoutExpired:
        return {"error": f"command timed out after {COMMAND_TIMEOUT_SECONDS}s"}
    except OSError as exc:
        return {"error": str(exc)}

    stdout = (result.stdout or "").strip()
    stderr = (result.stderr or "").strip()

    if result.returncode != 0:
        return {
            "error": stderr or stdout or f"command exited with {result.returncode}",
            "returncode": result.returncode,
        }

    if parse_json:
        if not stdout:
            return {"error": "empty JSON output"}
        try:
            data = json.loads(stdout)
        except json.JSONDecodeError as exc:
            return {"error": f"invalid JSON output: {exc}"}
        if isinstance(data, dict):
            return data
        return {"data": data}

    return {"raw": stdout or stderr or "(no output)"}


def collect_presence():
    return _read_json_file(PRESENCE_STATE_PATH)


def collect_hue_crosstown():
    return _run_cli(["hue", "--crosstown", "status"])


def collect_hue_cabin():
    return _run_cli(["hue", "--cabin", "status"])


def collect_nest():
    today = datetime.now().strftime("%Y-%m-%d")
    return _read_latest_jsonl_record(os.path.join(NEST_HISTORY_DIR, f"{today}.jsonl"))


def collect_cielo():
    return _run_cli(["cielo", "status", "--json"], parse_json=True)


def collect_mysa():
    return _run_cli(["mysa"], parse_json=True)


def collect_lock():
    return _run_cli(["august", "status"], parse_json=True)


def collect_roombas_crosstown():
    return _run_cli(["crosstown-roomba", "status"])


def collect_roombas_cabin():
    results = {}
    for name in ("floomba", "philly"):
        r = _run_cli(["roomba", "status", name])
        results[name] = r.get("raw", r.get("error", "unknown")) if isinstance(r, dict) else str(r)
    return {"robots": results}


def collect_tv():
    return _run_cli(["samsung-tv", "status"])


def collect_speakers():
    return _run_cli(["speaker", "status"])


def collect_cabin_speakers():
    results = {}
    for name, ip in _CABIN_SPEAKER_IPS.items():
        r = _run_cli([CATT_BIN, "-d", ip, "status"])
        results[name] = r.get("raw", r.get("error", "unknown")) if isinstance(r, dict) else str(r)
    return {"speakers": results}


def collect_litter_robot():
    return _run_cli(["litter-robot", "status"])


def collect_petlibro():
    return _run_cli(["petlibro", "status"])


def collect_8sleep():
    return _run_cli(["8sleep", "status"])


def collect_ring():
    return _run_cli(["ring", "status"])


def collect_dog_walk():
    return _read_json_file(DOG_WALK_STATE_PATH)


COLLECTORS = {
    "presence": collect_presence,
    "hue_crosstown": collect_hue_crosstown,
    "hue_cabin": collect_hue_cabin,
    "nest": collect_nest,
    "cielo": collect_cielo,
    "mysa": collect_mysa,
    "lock": collect_lock,
    "roombas_crosstown": collect_roombas_crosstown,
    "roombas_cabin": collect_roombas_cabin,
    "tv": collect_tv,
    "speakers": collect_speakers,
    "cabin_speakers": collect_cabin_speakers,
    "litter_robot": collect_litter_robot,
    "petlibro": collect_petlibro,
    "8sleep": collect_8sleep,
    "ring": collect_ring,
    "dog_walk": collect_dog_walk,
}


def _collect_with_cache(name, collector, refresh=False):
    now = time.time()
    if not refresh:
        with STATUS_CACHE_LOCK:
            cached = STATUS_CACHE.get(name)
        if cached and (now - cached["timestamp"]) < CACHE_TTL_SECONDS:
            return cached["data"], True, cached["timestamp"]

    data = collector()
    timestamp = time.time()
    with STATUS_CACHE_LOCK:
        STATUS_CACHE[name] = {"data": data, "timestamp": timestamp}
    return data, False, timestamp


def collect_status_bundle(refresh=False):
    results = {}
    cache = {}

    with ThreadPoolExecutor(max_workers=len(COLLECTORS)) as executor:
        futures = {
            executor.submit(_collect_with_cache, name, collector, refresh): name
            for name, collector in COLLECTORS.items()
        }
        for future in as_completed(futures):
            name = futures[future]
            try:
                data, cached, timestamp = future.result()
            except Exception as exc:
                data = {"error": str(exc)}
                cached = False
                timestamp = time.time()
            results[name] = data
            cache[name] = {"cached": cached, "timestamp": _iso_timestamp(timestamp)}

    payload = {
        "meta": {
            "timestamp": _iso_timestamp(),
            "ttl_seconds": CACHE_TTL_SECONDS,
            "refresh": refresh,
        }
    }
    for name in COLLECTORS:
        payload[name] = results.get(name, {"error": "collector missing"})
    payload["cache"] = cache
    return payload


def collect_status_cached_fast():
    """Return whatever is in cache immediately — no blocking on collectors.

    Returns partial data if some collectors haven't finished yet.
    The 'pending' list tells the frontend which devices to poll individually.
    """
    now = time.time()
    results = {}
    cache_info = {}
    pending = []

    with STATUS_CACHE_LOCK:
        for name in COLLECTORS:
            cached = STATUS_CACHE.get(name)
            if cached:
                results[name] = cached["data"]
                cache_info[name] = {"cached": True, "timestamp": _iso_timestamp(cached["timestamp"])}
            else:
                results[name] = {"_pending": True}
                pending.append(name)

    payload = {
        "meta": {
            "timestamp": _iso_timestamp(),
            "ttl_seconds": CACHE_TTL_SECONDS,
            "refresh": False,
            "pending": pending,
        }
    }
    for name in COLLECTORS:
        payload[name] = results.get(name, {"error": "collector missing"})
    payload["cache"] = cache_info
    return payload


COMMANDS = {
    "hue_crosstown": {
        "on": lambda a: ["hue", "--crosstown", "on", a["room"]] + ([str(a["brightness"])] if "brightness" in a else []),
        "off": lambda a: ["hue", "--crosstown", "off", a["room"]],
        "bri": lambda a: ["hue", "--crosstown", "bri", a["room"], str(a["brightness"])],
        "color": lambda a: ["hue", "--crosstown", "color", a["room"], a["color"]],
    },
    "hue_cabin": {
        "on": lambda a: ["hue", "--cabin", "on", a["room"]] + ([str(a["brightness"])] if "brightness" in a else []),
        "off": lambda a: ["hue", "--cabin", "off", a["room"]],
        "bri": lambda a: ["hue", "--cabin", "bri", a["room"], str(a["brightness"])],
        "color": lambda a: ["hue", "--cabin", "color", a["room"], a["color"]],
    },
    "nest": {
        "set": lambda a: ["nest", "set", a["room"], str(a["temp"])],
        "mode": lambda a: ["nest", "mode", a["room"], a["mode"]],
        "eco": lambda a: ["nest", "eco", a["room"], a.get("mode", "on")],
    },
    "cielo": {
        "on": lambda a: ["cielo", "on", "-d", a["device"]],
        "off": lambda a: ["cielo", "off", "-d", a["device"]],
        "temp": lambda a: ["cielo", "temp", str(a["temp"]), "-d", a["device"]],
        "mode": lambda a: ["cielo", "mode", a["mode"], "-d", a["device"]],
    },
    "august": {
        "lock": lambda a: ["august", "lock"],
        "unlock": lambda a: ["august", "unlock"],
    },
    "crosstown_roomba": {
        "start": lambda a: ["crosstown-roomba", "start", a.get("robot", "all")],
        "stop": lambda a: ["crosstown-roomba", "stop", a.get("robot", "all")],
        "dock": lambda a: ["crosstown-roomba", "dock", a.get("robot", "all")],
    },
    "cabin_roomba": {
        "start": lambda a: ["roomba", "start", a.get("robot", "all")],
        "stop": lambda a: ["roomba", "stop", a.get("robot", "all")],
        "dock": lambda a: ["roomba", "dock", a.get("robot", "all")],
    },
    "tv": {
        "power_on": lambda a: ["samsung-tv", "power", a.get("name", "frame"), "on"],
        "power_off": lambda a: ["samsung-tv", "power", a.get("name", "frame"), "off"],
    },
    "speaker": {
        "wake": lambda a: ["speaker", "volume", a["name"], "5"],
        "volume": lambda a: ["speaker", "volume", a["name"], str(a["level"])],
        "mute": lambda a: ["speaker", "mute", a["name"]],
        "unmute": lambda a: ["speaker", "unmute", a["name"]],
    },
    "cabin_speaker": {
        "volume": lambda a: [CATT_BIN, "-d", _CABIN_SPEAKER_IPS.get(a["name"], a["name"]), "volume", str(a["level"])],
        "stop": lambda a: [CATT_BIN, "-d", _CABIN_SPEAKER_IPS.get(a["name"], a["name"]), "stop"],
        "status": lambda a: [CATT_BIN, "-d", _CABIN_SPEAKER_IPS.get(a["name"], a["name"]), "status"],
    },
    "litter_robot": {
        "clean": lambda a: ["litter-robot", "clean"],
        "reset": lambda a: ["litter-robot", "reset"],
    },
    "petlibro": {
        "feed": lambda a: ["petlibro", "feed", "feeder"] + ([str(a["portions"])] if "portions" in a else []),
    },
    "eightsleep": {
        "temp": lambda a: ["8sleep", "temp", a["side"], str(a["level"])],
        "off": lambda a: ["8sleep", "off", a["side"]],
        "on": lambda a: ["8sleep", "on", a["side"]],
    },
    "nest_camera": {
        "snap": lambda a: ["nest", "camera", "snap", a.get("room", "kitchen"),
                           os.path.join(CAMERA_SNAP_DIR, a.get("room", "kitchen") + ".jpg")],
    },
    "ring_camera": {
        "snap": lambda a: ["ring", "snapshot",
                           os.path.join(CAMERA_SNAP_DIR, "ring-" + a.get("doorbell", "crosstown") + ".jpg"),
                           a.get("doorbell_id", "")],
    },
}


def execute_command(payload):
    if not isinstance(payload, dict):
        return 400, {"success": False, "error": "request body must be a JSON object"}

    device = payload.get("device")
    action = payload.get("action")
    args = payload.get("args") or {}

    if not isinstance(args, dict):
        return 400, {"success": False, "error": "args must be a JSON object"}

    device_commands = COMMANDS.get(device)
    if not device_commands:
        return 400, {"success": False, "error": f"unknown device: {device}"}

    builder = device_commands.get(action)
    if not builder:
        return 400, {"success": False, "error": f"unknown action for {device}: {action}"}

    try:
        command = builder(args)
    except KeyError as exc:
        return 400, {"success": False, "error": f"missing argument: {exc.args[0]}"}
    except Exception as exc:
        return 400, {"success": False, "error": str(exc)}

    try:
        result = subprocess.run(
            command,
            capture_output=True,
            timeout=COMMAND_TIMEOUT_SECONDS,
            text=True,
        )
    except FileNotFoundError as exc:
        return 502, {"success": False, "error": str(exc), "command": command}
    except subprocess.TimeoutExpired:
        return 504, {
            "success": False,
            "error": f"command timed out after {COMMAND_TIMEOUT_SECONDS}s",
            "command": command,
        }
    except OSError as exc:
        return 502, {"success": False, "error": str(exc), "command": command}

    stdout = (result.stdout or "").strip()
    stderr = (result.stderr or "").strip()

    response = {
        "success": result.returncode == 0,
        "command": command,
        "output": stdout,
        "error": stderr,
        "returncode": result.returncode,
    }

    if result.returncode == 0:
        with STATUS_CACHE_LOCK:
            STATUS_CACHE.clear()
        return 200, response

    return 502, response


class DashboardHandler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        sys.stderr.write(f"{self.address_string()} {args[0]}\n")

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.end_headers()

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/") or "/"
        qs = parse_qs(parsed.query)

        if path == "/":
            self._serve_html()
        elif path == "/api/status":
            refresh = qs.get("refresh", ["false"])[0].lower() in {"1", "true", "yes"}
            if refresh:
                self._respond(200, collect_status_bundle(refresh=True))
            else:
                self._respond(200, collect_status_cached_fast())
        elif path.startswith("/api/status/"):
            device_name = path.split("/api/status/", 1)[1]
            if device_name in COLLECTORS:
                data = COLLECTORS[device_name]()
                with STATUS_CACHE_LOCK:
                    STATUS_CACHE[device_name] = {"data": data, "timestamp": time.time()}
                self._respond(200, data)
            else:
                self._respond(404, {"error": f"unknown device: {device_name}"})
        elif path == "/api/presence":
            self._respond(200, collect_presence())
        elif path.startswith("/api/camera-snap/"):
            room = path.split("/api/camera-snap/", 1)[1]
            self._serve_camera_snap(room)
        else:
            self._respond(404, {"error": "not found"})

    def do_POST(self):
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/") or "/"

        if path != "/api/command":
            self._respond(404, {"error": "not found"})
            return

        try:
            content_length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            self._respond(400, {"success": False, "error": "invalid Content-Length"})
            return

        body = self.rfile.read(content_length) if content_length > 0 else b"{}"
        try:
            payload = json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            self._respond(400, {"success": False, "error": "invalid JSON body"})
            return

        code, response = execute_command(payload)
        self._respond(code, response)

    def _respond(self, code, data):
        body = json.dumps(data).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.end_headers()
        self.wfile.write(body)

    def _serve_html(self):
        body = DASHBOARD_HTML.encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _serve_camera_snap(self, room):
        import re
        if not re.match(r'^[a-z0-9 _-]+$', room):
            self._respond(400, {"error": "invalid room name"})
            return
        snap_path = os.path.join(CAMERA_SNAP_DIR, room + ".jpg")
        if not os.path.isfile(snap_path):
            self._respond(404, {"error": "no snapshot available"})
            return
        with open(snap_path, "rb") as f:
            body = f.read()
        mtime = os.path.getmtime(snap_path)
        self.send_response(200)
        self.send_header("Content-Type", "image/jpeg")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("X-Snapshot-Timestamp", str(int(mtime)))
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        self.wfile.write(body)


class ThreadedHTTPServer(ThreadingMixIn, HTTPServer):
    daemon_threads = True


DASHBOARD_HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Home Control Plane</title>
<style>
:root { --bg: #0f1117; --surface: #1a1d27; --border: #2a2d3a; --text: #e4e4e7; --text-muted: #9ca3af; }
@media (prefers-color-scheme: light) { :root { --bg: #f8fafc; --surface: #ffffff; --border: #e2e8f0; --text: #1e293b; --text-muted: #64748b; } }
* { box-sizing: border-box; }
body { margin: 0; background: var(--bg); color: var(--text); font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', system-ui, sans-serif; padding: 24px 16px 48px; }
.page { max-width: 1200px; margin: 0 auto; }
.header { display: flex; justify-content: space-between; align-items: flex-start; gap: 16px; margin-bottom: 20px; }
.title { margin: 0; font-size: 1.85rem; font-weight: 700; }
.updated { margin-top: 6px; color: var(--text-muted); font-size: 0.95rem; }
.toolbar { display: flex; gap: 12px; flex-wrap: wrap; align-items: center; }
.segmented { display: inline-flex; gap: 8px; padding: 6px; background: var(--surface); border: 1px solid var(--border); border-radius: 999px; }
.segmented button, .refresh-button, .command-row button { border: 1px solid var(--border); background: transparent; color: var(--text); border-radius: 999px; padding: 9px 14px; font: inherit; cursor: pointer; }
.segmented button.active { background: var(--text); color: var(--bg); border-color: var(--text); }
.refresh-button { background: var(--surface); }
.feedback { margin-bottom: 20px; padding: 12px 14px; border: 1px solid var(--border); border-radius: 12px; background: var(--surface); }
.feedback.error { border-color: #ef4444; color: #fca5a5; }
.feedback.success { border-color: #22c55e; color: #86efac; }
.card-feedback { margin: 0 0 8px; font-size: 0.85rem; }
.cards { display: grid; grid-template-columns: repeat(auto-fit, minmax(320px, 1fr)); gap: 16px; }
.card { background: var(--surface); border: 1px solid var(--border); border-radius: 16px; padding: 18px; min-height: 220px; display: flex; flex-direction: column; gap: 14px; }
.card-wide { grid-column: span 2; }
.card-header { display: flex; justify-content: space-between; gap: 12px; align-items: flex-start; }
.eyebrow { color: var(--text-muted); font-size: 0.75rem; font-weight: 600; letter-spacing: 0.08em; text-transform: uppercase; margin-bottom: 4px; }
.card h2 { margin: 0; font-size: 1.1rem; }
.location-pill { display: inline-flex; align-items: center; justify-content: center; padding: 4px 10px; border-radius: 999px; border: 1px solid var(--border); color: var(--text-muted); font-size: 0.8rem; white-space: nowrap; }
.content { display: flex; flex-direction: column; gap: 12px; }
.raw, .json { margin: 0; padding: 12px; border: 1px solid var(--border); border-radius: 12px; background: rgba(0, 0, 0, 0.12); color: var(--text); overflow-x: auto; white-space: pre-wrap; word-break: break-word; font-size: 0.88rem; line-height: 1.45; }
.muted { color: var(--text-muted); }
.error-text { color: #fca5a5; }
.kv { display: grid; grid-template-columns: minmax(110px, 1fr) 2fr; gap: 8px 12px; }
.kv dt { color: var(--text-muted); }
.kv dd { margin: 0; text-align: right; word-break: break-word; }
.mini-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(220px, 1fr)); gap: 12px; }
.subcard { border: 1px solid var(--border); border-radius: 12px; padding: 12px; }
.subcard-title { font-weight: 600; margin-bottom: 8px; }
.metric { display: flex; justify-content: space-between; gap: 10px; padding: 6px 0; border-bottom: 1px solid var(--border); }
.metric:last-child { border-bottom: 0; }
.room-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(140px, 1fr)); gap: 10px; }
.room-chip { border: 1px solid var(--border); border-radius: 12px; padding: 10px; }
.room-name { font-size: 0.86rem; color: var(--text-muted); margin-bottom: 6px; }
.room-temp { font-size: 1.25rem; font-weight: 700; }
.room-meta { margin-top: 6px; font-size: 0.82rem; color: var(--text-muted); }
.controls { margin-top: auto; display: flex; flex-direction: column; gap: 10px; }
.controls-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(140px, 1fr)); gap: 8px; }
.controls-grid input, .controls-grid select { width: 100%; border: 1px solid var(--border); border-radius: 10px; background: var(--surface); color: var(--text); padding: 10px 12px; font: inherit; }
.controls-grid select { appearance: none; -webkit-appearance: none; background-image: url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='12' height='12' viewBox='0 0 12 12'%3E%3Cpath fill='%239ca3af' d='M2 4l4 4 4-4'/%3E%3C/svg%3E"); background-repeat: no-repeat; background-position: right 12px center; background-size: 12px; padding-right: 32px; cursor: pointer; }
.controls-grid select option { background: var(--surface); color: var(--text); padding: 8px; }
.command-row { display: flex; flex-wrap: wrap; gap: 8px; }
.command-row button { background: transparent; }
.command-row button:hover, .refresh-button:hover, .segmented button:hover { border-color: var(--text-muted); }
.hidden { display: none !important; }
.camera-snap { width: 100%; border-radius: 10px; margin-top: 8px; }
.camera-snap-meta { font-size: 0.82rem; color: var(--text-muted); margin-top: 6px; }
.dashboard-section { margin-bottom: 8px; }
.dashboard-section > summary { list-style: none; cursor: pointer; display: flex; align-items: center; gap: 10px; padding: 12px 0 8px; user-select: none; }
.dashboard-section > summary::-webkit-details-marker { display: none; }
.dashboard-section > summary::before { content: '▾'; font-size: 0.75rem; color: var(--text-muted); transition: transform 0.2s; }
.dashboard-section:not([open]) > summary::before { transform: rotate(-90deg); }
.dashboard-section > summary h2 { font-size: 0.85rem; font-weight: 600; text-transform: uppercase; letter-spacing: 0.08em; color: var(--text-muted); margin: 0; }
.dashboard-section > .cards { padding-top: 4px; }
@media (max-width: 900px) {
  .header { flex-direction: column; }
  .card-wide { grid-column: span 1; }
}
</style>
</head>
<body>
<div class="page">
  <header class="header">
    <div>
      <h1 class="title">Home Control Plane</h1>
      <div class="updated">Last updated: <span id="lastUpdated">—</span></div>
    </div>
    <div class="toolbar">
      <div class="segmented" id="locationSelector">
        <button type="button" class="active" data-location-filter="both">Both</button>
        <button type="button" data-location-filter="crosstown">Crosstown</button>
        <button type="button" data-location-filter="cabin">Cabin</button>
      </div>
      <button type="button" class="refresh-button" id="refreshButton">Refresh</button>
    </div>
  </header>

  <div id="feedback" class="feedback hidden"></div>

  <!-- LIGHTING -->
  <details class="dashboard-section" open>
    <summary><h2>Lighting</h2></summary>
    <section class="cards">
      <article class="card" data-location="crosstown">
        <div class="card-header">
          <div>
            <div class="eyebrow">Lights</div>
            <h2>Hue Crosstown</h2>
          </div>
          <span class="location-pill">Crosstown</span>
        </div>
        <div id="hueCrosstownContent" class="content"></div>
        <div class="controls">
          <form id="hue-crosstown-form" class="controls-grid">
            <select name="room">
              <option value="entryway">Entryway</option>
              <option value="kitchen">Kitchen</option>
              <option value="bedroom" selected>Bedroom</option>
              <option value="movie">Movie</option>
              <option value="living">Living</option>
              <option value="office">Office</option>
              <option value="upstairs">Upstairs</option>
              <option value="downstairs">Downstairs</option>
              <option value="master">Master</option>
            </select>
            <input name="brightness" type="number" min="1" max="100" placeholder="Brightness">
            <select name="color">
              <option value="">Color...</option>
              <option value="warm">Warm</option>
              <option value="cool">Cool</option>
              <option value="daylight">Daylight</option>
              <option value="red">Red</option>
              <option value="blue">Blue</option>
              <option value="green">Green</option>
              <option value="purple">Purple</option>
              <option value="orange">Orange</option>
              <option value="pink">Pink</option>
            </select>
          </form>
          <div class="command-row">
            <button type="button" data-command data-device="hue_crosstown" data-action="on" data-form="hue-crosstown-form" data-fields="room,brightness">On</button>
            <button type="button" data-command data-device="hue_crosstown" data-action="off" data-form="hue-crosstown-form" data-fields="room">Off</button>
            <button type="button" data-command data-device="hue_crosstown" data-action="bri" data-form="hue-crosstown-form" data-fields="room,brightness">Set Brightness</button>
            <button type="button" data-command data-device="hue_crosstown" data-action="color" data-form="hue-crosstown-form" data-fields="room,color">Set Color</button>
          </div>
        </div>
      </article>

      <article class="card" data-location="cabin">
        <div class="card-header">
          <div>
            <div class="eyebrow">Lights</div>
            <h2>Hue Cabin</h2>
          </div>
          <span class="location-pill">Cabin</span>
        </div>
        <div id="hueCabinContent" class="content"></div>
        <div class="controls">
          <form id="hue-cabin-form" class="controls-grid">
            <select name="room">
              <option value="kitchen">Kitchen</option>
              <option value="living" selected>Living</option>
              <option value="bathroom">Bathroom</option>
              <option value="hallway">Hallway</option>
              <option value="bedroom">Bedroom</option>
              <option value="office">Office</option>
              <option value="solarium">Solarium</option>
              <option value="staircase">Staircase</option>
            </select>
            <input name="brightness" type="number" min="1" max="100" placeholder="Brightness">
            <select name="color">
              <option value="">Color...</option>
              <option value="warm">Warm</option>
              <option value="cool">Cool</option>
              <option value="daylight">Daylight</option>
              <option value="red">Red</option>
              <option value="blue">Blue</option>
              <option value="green">Green</option>
              <option value="purple">Purple</option>
              <option value="orange">Orange</option>
              <option value="pink">Pink</option>
            </select>
          </form>
          <div class="command-row">
            <button type="button" data-command data-device="hue_cabin" data-action="on" data-form="hue-cabin-form" data-fields="room,brightness">On</button>
            <button type="button" data-command data-device="hue_cabin" data-action="off" data-form="hue-cabin-form" data-fields="room">Off</button>
            <button type="button" data-command data-device="hue_cabin" data-action="bri" data-form="hue-cabin-form" data-fields="room,brightness">Set Brightness</button>
            <button type="button" data-command data-device="hue_cabin" data-action="color" data-form="hue-cabin-form" data-fields="room,color">Set Color</button>
          </div>
        </div>
      </article>
    </section>
  </details>

  <!-- TEMPERATURE -->
  <details class="dashboard-section" open>
    <summary><h2>Temperature</h2></summary>
    <section class="cards">
      <article class="card" data-location="cabin">
        <div class="card-header">
          <div>
            <div class="eyebrow">Temperature</div>
            <h2>Nest</h2>
          </div>
          <span class="location-pill">Cabin</span>
        </div>
        <div id="nestContent" class="content"></div>
        <div class="controls">
          <form id="nest-form" class="controls-grid">
            <select name="room">
              <option value="Solarium">Solarium</option>
              <option value="Living Room">Living Room</option>
              <option value="Bedroom" selected>Bedroom</option>
            </select>
            <input name="temp" type="number" step="1" placeholder="Temp °F">
            <select name="mode">
              <option value="">Mode...</option>
              <option value="HEAT">Heat</option>
              <option value="OFF">Off</option>
            </select>
          </form>
          <div class="command-row">
            <button type="button" data-command data-device="nest" data-action="set" data-form="nest-form" data-fields="room,temp">Set Temp</button>
            <button type="button" data-command data-device="nest" data-action="mode" data-form="nest-form" data-fields="room,mode">Set Mode</button>
            <button type="button" data-command data-device="nest" data-action="eco" data-form="nest-form" data-fields="room">Eco On</button>
            <button type="button" data-command data-device="nest" data-action="eco" data-form="nest-form" data-fields="room,mode" data-extra='{"mode":"off"}'>Eco Off</button>
          </div>
        </div>
      </article>

      <article class="card" data-location="crosstown">
        <div class="card-header">
          <div>
            <div class="eyebrow">Temperature</div>
            <h2>Cielo</h2>
          </div>
          <span class="location-pill">Crosstown</span>
        </div>
        <div id="cieloContent" class="content"></div>
        <div class="controls">
          <form id="cielo-form" class="controls-grid">
            <select name="device">
              <option value="basement">Basement</option>
              <option value="living room">Living Room</option>
              <option value="office">Dylan's Office</option>
              <option value="bedroom" selected>Bedroom</option>
            </select>
            <input name="temp" type="number" step="1" placeholder="Temp °F">
            <select name="mode">
              <option value="">Mode...</option>
              <option value="cool">Cool</option>
              <option value="heat">Heat</option>
              <option value="auto">Auto</option>
              <option value="dry">Dry</option>
              <option value="fan">Fan</option>
            </select>
          </form>
          <div class="command-row">
            <button type="button" data-command data-device="cielo" data-action="on" data-form="cielo-form" data-fields="device">On</button>
            <button type="button" data-command data-device="cielo" data-action="off" data-form="cielo-form" data-fields="device">Off</button>
            <button type="button" data-command data-device="cielo" data-action="temp" data-form="cielo-form" data-fields="device,temp">Set Temp</button>
            <button type="button" data-command data-device="cielo" data-action="mode" data-form="cielo-form" data-fields="device,mode">Set Mode</button>
          </div>
        </div>
      </article>

      <article class="card" data-location="crosstown">
        <div class="card-header">
          <div>
            <div class="eyebrow">Temperature</div>
            <h2>Mysa</h2>
          </div>
          <span class="location-pill">Crosstown</span>
        </div>
        <div id="mysaContent" class="content"></div>
      </article>

      <article class="card" data-location="crosstown">
        <div class="card-header">
          <div>
            <div class="eyebrow">Sleep</div>
            <h2>Eight Sleep</h2>
          </div>
          <span class="location-pill">Crosstown</span>
        </div>
        <div id="eightSleepContent" class="content"></div>
        <div class="controls">
          <form id="eightsleep-form" class="controls-grid">
            <select name="side">
              <option value="dylan">Dylan</option>
              <option value="julia">Julia</option>
            </select>
            <input name="level" type="number" min="-100" max="100" step="10" placeholder="Level (-100 to +100)">
          </form>
          <div class="command-row">
            <button type="button" data-command data-device="eightsleep" data-action="on" data-form="eightsleep-form" data-fields="side">On</button>
            <button type="button" data-command data-device="eightsleep" data-action="off" data-form="eightsleep-form" data-fields="side">Off</button>
            <button type="button" data-command data-device="eightsleep" data-action="temp" data-form="eightsleep-form" data-fields="side,level">Set Temp</button>
          </div>
        </div>
      </article>
    </section>
  </details>

  <!-- SECURITY -->
  <details class="dashboard-section" open>
    <summary><h2>Security</h2></summary>
    <section class="cards">
      <article class="card" data-location="crosstown">
        <div class="card-header">
          <div>
            <div class="eyebrow">Lock</div>
            <h2>August</h2>
          </div>
          <span class="location-pill">Crosstown</span>
        </div>
        <div id="lockContent" class="content"></div>
        <div class="controls">
          <div class="command-row">
            <button type="button" data-command data-device="august" data-action="lock">Lock</button>
            <button type="button" data-command data-device="august" data-action="unlock">Unlock</button>
          </div>
        </div>
      </article>

      <article class="card" data-location="both">
        <div class="card-header">
          <div>
            <div class="eyebrow">Doorbell</div>
            <h2>Ring</h2>
          </div>
          <span class="location-pill">Both</span>
        </div>
        <div id="ringContent" class="content"></div>
        <div id="ringSnapContent" class="content"></div>
        <div class="controls">
          <form id="ring-camera-form" class="controls-grid">
            <select name="doorbell" onchange="this.form.doorbell_id.value=this.selectedOptions[0].dataset.id">
              <option value="crosstown" data-id="684794187" selected>Crosstown</option>
              <option value="cabin" data-id="697442349">Cabin</option>
            </select>
            <input type="hidden" name="doorbell_id" value="684794187">
          </form>
          <div class="command-row">
            <button type="button" data-command data-device="ring_camera" data-action="snap" data-form="ring-camera-form" data-fields="doorbell,doorbell_id">Take Snapshot</button>
          </div>
        </div>
      </article>

      <article class="card" data-location="cabin">
        <div class="card-header">
          <div>
            <div class="eyebrow">Camera</div>
            <h2>Nest Camera</h2>
          </div>
          <span class="location-pill">Cabin</span>
        </div>
        <div id="nestCameraContent" class="content">
          <div class="muted">No snapshot yet</div>
        </div>
        <div class="controls">
          <form id="nest-camera-form" class="controls-grid">
            <select name="room">
              <option value="kitchen" selected>Kitchen</option>
            </select>
          </form>
          <div class="command-row">
            <button type="button" id="cameraSnapBtn" data-command data-device="nest_camera" data-action="snap" data-form="nest-camera-form" data-fields="room">Take Snapshot</button>
          </div>
        </div>
      </article>
    </section>
  </details>

  <!-- PETS -->
  <details class="dashboard-section" open>
    <summary><h2>Pets</h2></summary>
    <section class="cards">
      <article class="card" data-location="crosstown">
        <div class="card-header">
          <div>
            <div class="eyebrow">Pets</div>
            <h2>Litter-Robot</h2>
          </div>
          <span class="location-pill">Crosstown</span>
        </div>
        <div id="litterRobotContent" class="content"></div>
        <div class="controls">
          <div class="command-row">
            <button type="button" data-command data-device="litter_robot" data-action="clean">Clean</button>
            <button type="button" data-command data-device="litter_robot" data-action="reset">Reset</button>
          </div>
        </div>
      </article>

      <article class="card" data-location="crosstown">
        <div class="card-header">
          <div>
            <div class="eyebrow">Pets</div>
            <h2>Petlibro</h2>
          </div>
          <span class="location-pill">Crosstown</span>
        </div>
        <div id="petlibroContent" class="content"></div>
        <div class="controls">
          <form id="petlibro-form" class="controls-grid">
            <input name="portions" type="number" min="1" step="1" placeholder="Portions">
          </form>
          <div class="command-row">
            <button type="button" data-command data-device="petlibro" data-action="feed" data-form="petlibro-form" data-fields="portions">Feed</button>
          </div>
        </div>
      </article>

      <article class="card" data-location="both">
        <div class="card-header">
          <div>
            <div class="eyebrow">Dog Walk</div>
            <h2>Walk State</h2>
          </div>
          <span class="location-pill">Both</span>
        </div>
        <div id="dogWalkContent" class="content"></div>
      </article>
    </section>
  </details>

  <!-- MISC -->
  <details class="dashboard-section" open>
    <summary><h2>Misc</h2></summary>
    <section class="cards">
      <article class="card" data-location="crosstown">
        <div class="card-header">
          <div>
            <div class="eyebrow">Media</div>
            <h2>TV</h2>
          </div>
          <span class="location-pill">Crosstown</span>
        </div>
        <div id="tvContent" class="content"></div>
        <div class="controls">
          <form id="tv-form" class="controls-grid">
            <input name="name" placeholder="TV name" value="frame">
          </form>
          <div class="command-row">
            <button type="button" data-command data-device="tv" data-action="power_on" data-form="tv-form" data-fields="name">Power On</button>
            <button type="button" data-command data-device="tv" data-action="power_off" data-form="tv-form" data-fields="name">Power Off</button>
          </div>
        </div>
      </article>

      <article class="card" data-location="crosstown">
        <div class="card-header">
          <div>
            <div class="eyebrow">Media</div>
            <h2>Speakers</h2>
          </div>
          <span class="location-pill">Crosstown</span>
        </div>
        <div id="speakersContent" class="content"></div>
        <div class="controls">
          <form id="speaker-form" class="controls-grid">
            <select name="name">
              <option value="bedroom" selected>Bedroom</option>
              <option value="living room">Living Room</option>
            </select>
            <input name="level" type="number" min="0" max="100" placeholder="Volume">
          </form>
          <div class="command-row">
            <button type="button" data-command data-device="speaker" data-action="wake" data-form="speaker-form" data-fields="name">Wake</button>
            <button type="button" data-command data-device="speaker" data-action="volume" data-form="speaker-form" data-fields="name,level">Set Volume</button>
            <button type="button" data-command data-device="speaker" data-action="mute" data-form="speaker-form" data-fields="name">Mute</button>
            <button type="button" data-command data-device="speaker" data-action="unmute" data-form="speaker-form" data-fields="name">Unmute</button>
          </div>
        </div>
      </article>

      <article class="card" data-location="cabin">
        <div class="card-header">
          <div>
            <div class="eyebrow">Media</div>
            <h2>Cabin Speakers</h2>
          </div>
          <span class="location-pill">Cabin</span>
        </div>
        <div id="cabinSpeakersContent" class="content"></div>
        <div class="controls">
          <form id="cabin-speaker-form" class="controls-grid">
            <select name="name">
              <option value="kitchen" selected>Kitchen</option>
              <option value="bedroom">Bedroom</option>
            </select>
            <input name="level" type="number" min="0" max="100" placeholder="Volume">
          </form>
          <div class="command-row">
            <button type="button" data-command data-device="cabin_speaker" data-action="volume" data-form="cabin-speaker-form" data-fields="name,level">Set Volume</button>
            <button type="button" data-command data-device="cabin_speaker" data-action="stop" data-form="cabin-speaker-form" data-fields="name">Stop</button>
            <button type="button" data-command data-device="cabin_speaker" data-action="status" data-form="cabin-speaker-form" data-fields="name">Status</button>
          </div>
        </div>
      </article>

      <article class="card" data-location="crosstown">
        <div class="card-header">
          <div>
            <div class="eyebrow">Roombas</div>
            <h2>Crosstown</h2>
          </div>
          <span class="location-pill">Crosstown</span>
        </div>
        <div id="roombasCrosstownContent" class="content"></div>
        <div class="controls">
          <form id="roombas-crosstown-form" class="controls-grid">
            <input name="robot" placeholder="Robot" value="all">
          </form>
          <div class="command-row">
            <button type="button" data-command data-device="crosstown_roomba" data-action="start" data-form="roombas-crosstown-form" data-fields="robot">Start</button>
            <button type="button" data-command data-device="crosstown_roomba" data-action="stop" data-form="roombas-crosstown-form" data-fields="robot">Stop</button>
            <button type="button" data-command data-device="crosstown_roomba" data-action="dock" data-form="roombas-crosstown-form" data-fields="robot">Dock</button>
          </div>
        </div>
      </article>

      <article class="card" data-location="cabin">
        <div class="card-header">
          <div>
            <div class="eyebrow">Roombas</div>
            <h2>Cabin</h2>
          </div>
          <span class="location-pill">Cabin</span>
        </div>
        <div id="roombasCabinContent" class="content"></div>
        <div class="controls">
          <form id="roombas-cabin-form" class="controls-grid">
            <input name="robot" placeholder="Robot" value="all">
          </form>
          <div class="command-row">
            <button type="button" data-command data-device="cabin_roomba" data-action="start" data-form="roombas-cabin-form" data-fields="robot">Start</button>
            <button type="button" data-command data-device="cabin_roomba" data-action="stop" data-form="roombas-cabin-form" data-fields="robot">Stop</button>
            <button type="button" data-command data-device="cabin_roomba" data-action="dock" data-form="roombas-cabin-form" data-fields="robot">Dock</button>
          </div>
        </div>
      </article>
    </section>
  </details>
</div>

<script>
const state = {
  location: 'both',
  data: null,
  loading: false,
};

function escapeHtml(value) {
  return String(value ?? '').replace(/[&<>"']/g, (char) => ({
    '&': '&amp;',
    '<': '&lt;',
    '>': '&gt;',
    '"': '&quot;',
    "'": '&#39;'
  }[char]));
}

function formatTimestamp(value) {
  if (!value) return '—';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleString();
}

function showFeedback(message, kind, button) {
  // Clear any previous inline feedback
  document.querySelectorAll('.card-feedback').forEach(el => el.remove());
  // Also clear the global fallback
  const global = document.getElementById('feedback');
  global.className = 'feedback hidden';
  global.textContent = '';
  if (!message) return;
  // If we have a button context, show inline in that card's section header
  if (button) {
    const section = button.closest('.dashboard-section');
    if (section) {
      const fb = document.createElement('div');
      fb.className = `card-feedback feedback ${kind || ''}`.trim();
      fb.textContent = message;
      const summary = section.querySelector('summary');
      if (summary) {
        summary.after(fb);
        if (kind) setTimeout(() => fb.remove(), 4000);
        return;
      }
    }
  }
  // Fallback to global
  global.className = `feedback ${kind || ''}`.trim();
  global.textContent = message;
}

function normalizeObject(value) {
  if (value && typeof value === 'object' && !Array.isArray(value)) {
    return value;
  }
  return null;
}

function summarizeValue(value) {
  if (value === null || value === undefined || value === '') return '—';
  if (typeof value === 'boolean') return value ? 'Yes' : 'No';
  if (Array.isArray(value)) return value.join(', ') || '—';
  if (typeof value === 'object') return JSON.stringify(value);
  return String(value);
}

function isPending(result) {
  return result && result._pending;
}
function renderPending() {
  return '<div class="muted" style="opacity:0.5">Loading...</div>';
}

function renderError(result) {
  return `<div class="error-text">${escapeHtml(result.error || 'Unknown error')}</div>`;
}

function renderPre(value, className='raw') {
  const text = typeof value === 'string' ? value : JSON.stringify(value, null, 2);
  return `<pre class="${className}">${escapeHtml(text || '(no output)')}</pre>`;
}

function renderSimpleObject(result) {
  if (!result) return '<div class="muted">No data available</div>';
  if (result.error) return renderError(result);
  const objectValue = normalizeObject(result);
  if (!objectValue) return renderPre(result);
  const entries = Object.entries(objectValue);
  const simple = entries.length > 0 && entries.every(([, value]) => value === null || ['string', 'number', 'boolean'].includes(typeof value));
  if (!simple) return renderPre(objectValue, 'json');
  return `<dl class="kv">${entries.map(([key, value]) => `<dt>${escapeHtml(key)}</dt><dd>${escapeHtml(summarizeValue(value))}</dd>`).join('')}</dl>`;
}

function renderRawResult(result) {
  if (!result) return '<div class="muted">No data available</div>';
  if (isPending(result)) return renderPending();
  if (result.error) return renderError(result);
  if (result.raw !== undefined) return renderPre(result.raw);
  return renderSimpleObject(result);
}

function pickLocationObject(result, name) {
  if (!result || typeof result !== 'object') return null;
  const candidates = [
    result[name],
    result[name.charAt(0).toUpperCase() + name.slice(1)],
    result.locations && result.locations[name],
    result.structures && result.structures[name],
  ];
  return candidates.find((value) => value && typeof value === 'object') || null;
}

function renderPresence(result) {
  if (!result) return '<div class="muted">No presence data</div>';
  if (result.error) return renderError(result);
  const crosstown = pickLocationObject(result, 'crosstown');
  const cabin = pickLocationObject(result, 'cabin');
  if (!crosstown && !cabin) return renderSimpleObject(result);

  const parts = [];
  if (crosstown) {
    const entries = Object.entries(crosstown).slice(0, 8);
    parts.push(`<div class="subcard"><div class="subcard-title">Crosstown</div>${entries.map(([key, value]) => `<div class="metric"><span>${escapeHtml(key)}</span><strong>${escapeHtml(summarizeValue(value))}</strong></div>`).join('')}</div>`);
  }
  if (cabin) {
    const entries = Object.entries(cabin).slice(0, 8);
    parts.push(`<div class="subcard"><div class="subcard-title">Cabin</div>${entries.map(([key, value]) => `<div class="metric"><span>${escapeHtml(key)}</span><strong>${escapeHtml(summarizeValue(value))}</strong></div>`).join('')}</div>`);
  }
  return `<div class="mini-grid">${parts.join('')}</div>`;
}

function roomLabel(name) {
  if (!name) return 'Room';
  return String(name).replace(/^19Crosstown\s+/i, '').replace(/^Philly\s+/i, '');
}

function roomTemp(room) {
  const fields = ['temp_f', 'temperature_f', 'current_temp_f', 'ambient_temperature_f'];
  for (const field of fields) {
    if (room && room[field] !== undefined && room[field] !== null) return `${room[field]}°`;
  }
  return '—';
}

function roomMeta(room) {
  const parts = [];
  if (room.humidity !== undefined) parts.push(`Humidity ${room.humidity}%`);
  if (room.target_f !== undefined) parts.push(`Target ${room.target_f}°`);
  if (room.mode) parts.push(String(room.mode));
  return parts.join(' · ') || 'No extra metrics';
}

function renderCielo(result) {
  if (!result) return '<div class="muted">No Cielo data</div>';
  if (isPending(result)) return renderPending();
  if (result.error) return renderError(result);
  const devices = result.data || result.devices || (Array.isArray(result) ? result : null);
  if (!devices) return renderSimpleObject(result);
  return '<div class="room-grid">' + devices.map((d) => {
    const name = d.deviceName || d.name || '?';
    const action = d.latestAction || {};
    const env = d.latEnv || {};
    const power = action.power || 'off';
    const temp = env.temp !== undefined ? env.temp + '°' : '—';
    const setpoint = action.temp ? action.temp + '°' : '—';
    const mode = action.mode || '—';
    const fan = action.fanspeed || '—';
    const humidity = env.humidity !== undefined ? env.humidity + '%' : '—';
    const online = d.deviceStatus === 1;
    return `<div class="room-chip">
      <div class="room-name">${escapeHtml(name)} ${online ? '' : '<span class="error-text">(offline)</span>'}</div>
      <div class="room-temp">${escapeHtml(temp)}</div>
      <div class="room-meta">Set: ${escapeHtml(setpoint)} · ${escapeHtml(mode)} · Fan: ${escapeHtml(fan)} · ${escapeHtml(humidity)} · Power: ${escapeHtml(power)}</div>
    </div>`;
  }).join('') + '</div>';
}

function renderMysa(result) {
  if (!result) return '<div class="muted">No Mysa data</div>';
  if (isPending(result)) return renderPending();
  if (result.error) return renderError(result);
  if (result.raw) return renderPre(result.raw);
  const devices = result.devices || (Array.isArray(result.data) ? result.data : null);
  if (!devices) return renderSimpleObject(result);
  return '<div class="room-grid">' + devices.map((d) => {
    const name = d.name || '?';
    const temp = d.temp_f !== undefined ? d.temp_f + '°F' : (d.temp_c !== undefined ? d.temp_c + '°C' : '—');
    const setpoint = d.setpoint_f !== undefined ? d.setpoint_f + '°F' : '—';
    const humidity = d.humidity !== undefined ? d.humidity + '%' : '—';
    const duty = d.duty_pct !== undefined ? d.duty_pct + '%' : '—';
    return `<div class="room-chip">
      <div class="room-name">${escapeHtml(name)}</div>
      <div class="room-temp">${escapeHtml(temp)}</div>
      <div class="room-meta">Set: ${escapeHtml(setpoint)} · Humidity: ${escapeHtml(humidity)} · Duty: ${escapeHtml(duty)}</div>
    </div>`;
  }).join('') + '</div>';
}

function renderLock(result) {
  if (!result) return '<div class="muted">No lock data</div>';
  if (isPending(result)) return renderPending();
  if (result.error) return renderError(result);
  if (result.raw) return renderPre(result.raw);
  const locked = result.state ? result.state.locked : null;
  const lockStatus = result.status || '—';
  const doorState = result.doorState || '—';
  const lockIcon = locked === true ? '&#x1F512;' : locked === false ? '&#x1F513;' : '';
  const info = result.info || {};
  const battery = info.battery !== undefined ? info.battery + '%' : '';
  const wlan = info.wlanRSSI !== undefined ? info.wlanRSSI + ' dBm' : '';
  return `<div class="subcard">
    <div style="font-size:1.5rem;font-weight:700;margin-bottom:8px">${lockIcon} ${locked ? 'Locked' : locked === false ? 'Unlocked' : 'Unknown'}</div>
    <div class="metric"><span>Door</span><strong>${escapeHtml(doorState.replace('kAugDoorState_', ''))}</strong></div>
    <div class="metric"><span>Status</span><strong>${escapeHtml(lockStatus)}</strong></div>
    ${battery ? `<div class="metric"><span>Battery</span><strong>${escapeHtml(battery)}</strong></div>` : ''}
    ${wlan ? `<div class="metric"><span>WiFi</span><strong>${escapeHtml(wlan)}</strong></div>` : ''}
  </div>`;
}

function renderTV(result) {
  if (!result) return '<div class="muted">No TV data</div>';
  if (isPending(result)) return renderPending();
  if (result.error) {
    const msg = result.error || '';
    if (msg.includes('timed out') || msg.includes('unreachable') || msg.includes('Unreachable') || msg.includes('ConnectTimeout')) {
      return `<div class="subcard">
        <div style="color:var(--text-muted);margin-bottom:8px">TV is likely off or in standby</div>
        <div style="font-size:0.85rem;color:var(--text-muted)">Samsung TV is unreachable when powered off. Use Power On to wake via WoL.</div>
      </div>`;
    }
    return renderError(result);
  }
  if (result.raw) return renderPre(result.raw);
  return renderSimpleObject(result);
}

function renderSpeakers(result) {
  if (!result) return '<div class="muted">No speaker data</div>';
  if (isPending(result)) return renderPending();
  if (result.error) {
    const msg = result.error || '';
    if (msg.includes('timed out') || msg.includes('UNREACHABLE') || msg.includes('urlopen error')) {
      return `<div class="subcard">
        <div style="color:var(--text-muted);margin-bottom:8px">Speakers are likely asleep</div>
        <div style="font-size:0.85rem;color:var(--text-muted)">Google Home speakers go idle after inactivity. Use the Wake button below to send a cast ping, then retry status.</div>
      </div>`;
    }
    return renderError(result);
  }
  if (result.raw) {
    const raw = result.raw || '';
    if (raw.includes('UNREACHABLE') || raw.includes('timed out')) {
      return `<div class="subcard">
        <div style="color:var(--text-muted);margin-bottom:8px">Speakers are likely asleep</div>
        <div style="font-size:0.85rem;color:var(--text-muted)">Google Home speakers go idle after inactivity. Use the Wake button below to send a cast ping, then retry status.</div>
      </div>`;
    }
    return renderPre(raw);
  }
  return renderSimpleObject(result);
}

function renderCabinSpeakers(result) {
  if (!result) return '<div class="muted">No speaker data</div>';
  if (isPending(result)) return renderPending();
  if (result.error) return renderError(result);
  const speakers = result.speakers;
  if (!speakers || typeof speakers !== 'object') return renderRawResult(result);
  const cards = Object.entries(speakers).map(([name, raw]) => {
    const text = typeof raw === 'string' ? raw : JSON.stringify(raw);
    const asleep = text.includes('timed out') || text.includes('UNREACHABLE') || text.includes('urlopen error') || text.includes('Connection refused');
    const dot = asleep ? '<span style="color:var(--text-muted)">○</span>' : '<span style="color:#4ade80">●</span>';
    const status = asleep ? 'Asleep' : 'Online';
    return `<div class="room-chip">
      <div class="room-name">${escapeHtml(name.charAt(0).toUpperCase() + name.slice(1))}</div>
      <div class="room-temp">${dot} ${escapeHtml(status)}</div>
    </div>`;
  }).join('');
  return `<div class="room-grid">${cards}</div>`;
}

function renderPetlibro(result) {
  if (!result) return '<div class="muted">No Petlibro data</div>';
  if (isPending(result)) return renderPending();
  if (result.error) return renderError(result);
  const raw = result.raw || '';
  if (!raw) return '<div class="muted">No data</div>';
  const blocks = raw.split(/\n(?=\S)/).filter(b => b.trim());
  const cards = blocks.map(block => {
    const lines = block.split('\n');
    const headerMatch = lines[0].match(/^(.+?)\s+\((\w+)\)\s*—\s*(\w+)/);
    if (!headerMatch) return null;
    const [, name, , status] = headerMatch;
    const isOnline = status.toLowerCase() === 'online';
    const dot = isOnline ? '<span style="color:#4ade80">●</span>' : '<span style="color:var(--text-muted)">○</span>';
    const props = {};
    lines.slice(1).forEach(l => {
      const kv = l.match(/^\s+(.+?):\s+(.+)$/);
      if (kv) props[kv[1].trim().toLowerCase()] = kv[2].trim();
    });
    const meta = [];
    if (props['water level']) meta.push('💧 ' + props['water level']);
    if (props['battery']) meta.push('🔋 ' + props['battery']);
    if (props['food level']) meta.push('🍽️ ' + props['food level']);
    if (props['next feed']) meta.push('⏰ ' + props['next feed']);
    if (props['today drunk']) meta.push(props['today drunk'] + ' today');
    if (props['filter'] && props['filter'].includes('OVERDUE')) meta.push('⚠️ Filter overdue');
    return `<div class="room-chip">
      <div class="room-name">${escapeHtml(name.trim())}</div>
      <div class="room-temp">${dot} ${escapeHtml(status)}</div>
      ${meta.length ? '<div class="room-meta">' + meta.join(' · ') + '</div>' : ''}
    </div>`;
  }).filter(Boolean).join('');
  return `<div class="room-grid">${cards}</div>`;
}

function renderLitterRobot(result) {
  if (!result) return '<div class="muted">No Litter-Robot data</div>';
  if (isPending(result)) return renderPending();
  if (result.error) return renderError(result);
  const raw = result.raw || '';
  if (!raw) return '<div class="muted">No data</div>';
  // Split into main block and cats block
  const catSection = raw.match(/Cats:\n([\s\S]+)/);
  const mainLines = (catSection ? raw.slice(0, catSection.index) : raw).split('\n');
  const headerMatch = mainLines[0].match(/^(.+?)\s*—\s*(\w+)/);
  const status = headerMatch ? headerMatch[2] : 'Unknown';
  const isOnline = status.toLowerCase() === 'online';
  const dot = isOnline ? '<span style="color:#4ade80">●</span>' : '<span style="color:var(--text-muted)">○</span>';
  const props = {};
  mainLines.slice(1).forEach(l => {
    const kv = l.match(/^\s+(.+?):\s+(.+)$/);
    if (kv) props[kv[1].trim().toLowerCase()] = kv[2].trim();
  });
  const meta = [];
  if (props['status']) meta.push(props['status']);
  if (props['waste level']) meta.push('🗑️ ' + props['waste level']);
  if (props['cycles']) meta.push(props['cycles'] + ' cycles');
  // Parse cats
  const cats = [];
  if (catSection) {
    catSection[1].split('\n').forEach(l => {
      const cm = l.match(/^\s+(.+?)\s*—\s*(.+)/);
      if (cm) cats.push(cm[1].trim() + ' · ' + cm[2].trim());
    });
  }
  return `<div class="room-grid"><div class="room-chip">
    <div class="room-name">Litter-Robot 4</div>
    <div class="room-temp">${dot} ${escapeHtml(status)}</div>
    ${meta.length ? '<div class="room-meta">' + meta.join(' · ') + '</div>' : ''}
    ${cats.length ? '<div class="room-meta">🐱 ' + cats.map(c => escapeHtml(c)).join(' · ') + '</div>' : ''}
  </div></div>`;
}

function renderEightSleep(result) {
  if (!result) return '<div class="muted">No Eight Sleep data</div>';
  if (isPending(result)) return renderPending();
  if (result.error) return renderError(result);
  const raw = result.raw || '';
  if (!raw) return '<div class="muted">No data</div>';
  // Parse person blocks: "Name (side):\n  Key: Value"
  const blocks = raw.split(/\n(?=\S)/).filter(b => b.trim());
  const cards = [];
  let podStatus = '';
  blocks.forEach(block => {
    const lines = block.split('\n');
    // Pod header: "Eight Sleep Pod (king Pod3)"
    if (lines[0].match(/^Eight Sleep Pod/)) {
      const props = {};
      lines.slice(1).forEach(l => {
        const kv = l.match(/^\s+(.+?):\s+(.+)$/);
        if (kv) props[kv[1].trim().toLowerCase()] = kv[2].trim();
      });
      podStatus = props['water'] || '';
      return;
    }
    // Smart schedule line — skip
    if (lines[0].match(/Smart Schedule/i)) return;
    // Person block: "Dylan (left):"
    const personMatch = lines[0].match(/^(.+?)\s*\((\w+)\):/);
    if (!personMatch) return;
    const [, name, side] = personMatch;
    const props = {};
    lines.slice(1).forEach(l => {
      const kv = l.match(/^\s+(.+?):\s+(.+)$/);
      if (kv) props[kv[1].trim().toLowerCase()] = kv[2].trim();
    });
    const level = props['level'] || '?';
    const status = props['status'] || 'Unknown';
    const isActive = status.toLowerCase() !== 'idle' && status.toLowerCase() !== 'off';
    const dot = isActive ? '<span style="color:#4ade80">●</span>' : '<span style="color:var(--text-muted)">○</span>';
    // Extract temp from level like "-39 (~72F)"
    const tempMatch = level.match(/\(~?(\d+)F\)/);
    const temp = tempMatch ? tempMatch[1] + '°F' : level;
    cards.push(`<div class="room-chip">
      <div class="room-name">${escapeHtml(name)} (${escapeHtml(side)})</div>
      <div class="room-temp">${dot} ${escapeHtml(temp)}</div>
      <div class="room-meta">${escapeHtml(status)}</div>
    </div>`);
  });
  return `<div class="room-grid">${cards.join('')}</div>`;
}

function renderRing(result) {
  if (!result) return '<div class="muted">No Ring data</div>';
  if (isPending(result)) return renderPending();
  if (result.error) return renderError(result);
  const raw = result.raw || '';
  if (!raw) return '<div class="muted">No data</div>';
  const blocks = raw.split(/\n(?=\S)/).filter(b => b.trim());
  const cards = blocks.map(block => {
    const lines = block.split('\n');
    const headerMatch = lines[0].match(/^(.+?)\s*\(/);
    if (!headerMatch) return null;
    const name = headerMatch[1].trim();
    const isShared = lines[0].includes('[shared]');
    const props = {};
    lines.slice(1).forEach(l => {
      const kv = l.match(/^\s+(.+?):\s+(.+)$/);
      if (kv) props[kv[1].trim().toLowerCase()] = kv[2].trim();
    });
    const battery = props['battery'] || '?';
    const lastEvent = props['last event'] || '';
    // Parse last event: "motion [person] at 2026-04-06 13:07:42..."
    const eventMatch = lastEvent.match(/^(\w+)(?:\s+\[(\w+)\])?\s+at\s+(.+)/);
    let eventText = lastEvent;
    if (eventMatch) {
      const [, type, tag, ts] = eventMatch;
      const d = new Date(ts);
      const ago = Math.round((Date.now() - d.getTime()) / 60000);
      const agoText = ago < 60 ? ago + 'm ago' : Math.round(ago / 60) + 'h ago';
      eventText = type + (tag ? ' [' + tag + ']' : '') + ' ' + agoText;
    }
    const dot = '<span style="color:#4ade80">●</span>';
    const meta = ['🔋 ' + battery];
    if (eventText) meta.push(eventText);
    return `<div class="room-chip">
      <div class="room-name">${escapeHtml(name)}${isShared ? ' (shared)' : ''}</div>
      <div class="room-temp">${dot} Online</div>
      <div class="room-meta">${meta.join(' · ')}</div>
    </div>`;
  }).filter(Boolean).join('');
  return `<div class="room-grid">${cards}</div>`;
}

function renderDogWalk(result) {
  if (!result) return '<div class="muted">No dog walk data</div>';
  if (isPending(result)) return renderPending();
  if (result.error) return renderError(result);
  const walk = result.dog_walk;
  if (!walk) return renderSimpleObject(result);
  const active = walk.active;
  const location = walk.location || '—';
  const walkers = (walk.walkers || []).join(', ') || '—';
  if (active) {
    const departed = formatTimestamp(walk.departed_at);
    return `<div class="subcard">
      <div style="font-size:1.1rem;font-weight:700;color:#22c55e;margin-bottom:8px">Active Walk</div>
      <div class="metric"><span>Location</span><strong>${escapeHtml(location)}</strong></div>
      <div class="metric"><span>Walkers</span><strong>${escapeHtml(walkers)}</strong></div>
      <div class="metric"><span>Departed</span><strong>${escapeHtml(departed)}</strong></div>
      <div class="metric"><span>Distance</span><strong>${walk.distance_m ? (walk.distance_m / 1609.34).toFixed(2) + ' mi' : '—'}</strong></div>
    </div>`;
  }
  const duration = walk.walk_duration_minutes ? walk.walk_duration_minutes.toFixed(0) + ' min' : '—';
  const distance = walk.distance_m ? (walk.distance_m / 1609.34).toFixed(2) + ' mi' : '—';
  const returned = formatTimestamp(walk.returned_at);
  return `<div class="subcard">
    <div style="font-size:1.1rem;font-weight:700;color:var(--text-muted);margin-bottom:8px">No Active Walk</div>
    <div class="metric"><span>Last Walk</span><strong>${escapeHtml(returned)}</strong></div>
    <div class="metric"><span>Duration</span><strong>${escapeHtml(duration)}</strong></div>
    <div class="metric"><span>Distance</span><strong>${escapeHtml(distance)}</strong></div>
    <div class="metric"><span>Location</span><strong>${escapeHtml(location)}</strong></div>
    <div class="metric"><span>Walkers</span><strong>${escapeHtml(walkers)}</strong></div>
  </div>`;
}

function renderHue(result) {
  if (!result) return '<div class="muted">No Hue data</div>';
  if (isPending(result)) return renderPending();
  if (result.error) return renderError(result);
  const raw = result.raw || '';
  if (!raw) return '<div class="muted">No data</div>';
  const lines = raw.split('\n').filter(l => l.trim());
  if (!lines.length) return '<div class="muted">No rooms</div>';
  const rooms = lines.map(line => {
    // Parse: "Room Name            ON/OFF  brightness%  count lights  mired mired"
    const m = line.match(/^(.+?)\s{2,}(ON|OFF)\s+(\d+)%\s+(\d+)\s+lights?\s+(\d+)\s+mired$/i);
    if (!m) return null;
    const [, name, state, brightness, lightCount, mired] = m;
    const isOn = state.toUpperCase() === 'ON';
    const dot = isOn ? '<span style="color:#4ade80">●</span>' : '<span style="color:var(--text-muted)">○</span>';
    let meta = '';
    if (isOn) {
      const mi = parseInt(mired, 10);
      const warmth = mi >= 500 ? 'Candlelight' : mi >= 400 ? 'Warm White' : mi >= 300 ? 'Neutral' : mi >= 200 ? 'Cool White' : 'Daylight';
      meta = `<div class="room-meta">${escapeHtml(brightness)}% · ${warmth}</div>`;
    }
    return `<div class="room-chip">
      <div class="room-name">${escapeHtml(name.trim())}</div>
      <div class="room-temp">${dot} ${escapeHtml(state)}</div>
      ${meta}
    </div>`;
  }).filter(Boolean).join('');
  return `<div class="room-grid">${rooms}</div>`;
}

function renderRoombas(result) {
  if (!result) return '<div class="muted">No Roomba data</div>';
  if (isPending(result)) return renderPending();
  if (result.error) return renderError(result);
  const raw = result.raw || '';
  if (!raw) return '<div class="muted">No data</div>';
  // Split into robot blocks: "Name (Model):\n  Key: Value\n  Key: Value"
  const blocks = raw.split(/\n(?=\S)/).filter(b => b.trim());
  const cards = blocks.map(block => {
    const lines = block.split('\n');
    const nameMatch = lines[0].match(/^(.+?):/);
    if (!nameMatch) return null;
    const name = nameMatch[1].trim();
    const props = {};
    lines.slice(1).forEach(l => {
      const kv = l.match(/^\s+(.+?):\s+(.+)$/);
      if (kv) props[kv[1].trim().toLowerCase()] = kv[2].trim();
    });
    const status = props.status || 'Unknown';
    const battery = props.battery || '?';
    const bin = props.bin || '';
    const isActive = !status.toLowerCase().includes('charging') && !status.toLowerCase().includes('dock');
    const dot = isActive ? '<span style="color:#4ade80">●</span>' : '<span style="color:var(--text-muted)">○</span>';
    const meta = [`🔋 ${escapeHtml(battery)}`];
    if (bin) meta.push(`🗑️ ${escapeHtml(bin)}`);
    return `<div class="room-chip">
      <div class="room-name">${escapeHtml(name)}</div>
      <div class="room-temp">${dot} ${escapeHtml(status)}</div>
      <div class="room-meta">${meta.join(' · ')}</div>
    </div>`;
  }).filter(Boolean).join('');
  return `<div class="room-grid">${cards}</div>`;
}

function renderRoombasCabin(result) {
  if (!result) return '<div class="muted">No Roomba data</div>';
  if (isPending(result)) return renderPending();
  if (result.error) return renderError(result);
  const robots = result.robots;
  if (!robots || typeof robots !== 'object') return renderRawResult(result);
  const cards = Object.entries(robots).map(([name, raw]) => {
    const text = typeof raw === 'string' ? raw : JSON.stringify(raw);
    // Extract status from "Response: Floomba isn't running." style
    const respMatch = text.match(/Response:\s*(.+)/i);
    const status = respMatch ? respMatch[1].trim() : text.trim();
    const isActive = status.toLowerCase().includes('running') && !status.toLowerCase().includes("isn't");
    const dot = isActive ? '<span style="color:#4ade80">●</span>' : '<span style="color:var(--text-muted)">○</span>';
    return `<div class="room-chip">
      <div class="room-name">${escapeHtml(name.charAt(0).toUpperCase() + name.slice(1))}</div>
      <div class="room-temp">${dot} ${escapeHtml(status)}</div>
    </div>`;
  }).join('');
  return `<div class="room-grid">${cards}</div>`;
}

function renderNest(result) {
  if (!result) return '<div class="muted">No Nest data</div>';
  if (isPending(result)) return renderPending();
  if (result.error) return renderError(result);
  if (!Array.isArray(result.rooms)) return renderSimpleObject(result);
  const rooms = result.rooms.map((room) => `
    <div class="room-chip">
      <div class="room-name">${escapeHtml(roomLabel(room.room || room.name))}</div>
      <div class="room-temp">${escapeHtml(roomTemp(room))}</div>
      <div class="room-meta">${escapeHtml(roomMeta(room))}</div>
    </div>
  `).join('');
  const timestamp = result.timestamp ? `<div class="muted">Snapshot: ${escapeHtml(formatTimestamp(result.timestamp))}</div>` : '';
  return `${timestamp}<div class="room-grid">${rooms}</div>`;
}

function setContent(id, html) {
  const el = document.getElementById(id);
  if (el) el.innerHTML = html;
}

function applyLocationFilter() {
  document.querySelectorAll('[data-location]').forEach((el) => {
    const location = el.dataset.location;
    const visible = state.location === 'both' || location === 'both' || location === state.location;
    el.classList.toggle('hidden', !visible);
  });

  document.querySelectorAll('[data-location-filter]').forEach((button) => {
    button.classList.toggle('active', button.dataset.locationFilter === state.location);
  });
}

function renderDashboard() {
  const data = state.data || {};
  setContent('hueCrosstownContent', renderHue(data.hue_crosstown));
  setContent('hueCabinContent', renderHue(data.hue_cabin));
  setContent('nestContent', renderNest(data.nest));
  setContent('cieloContent', renderCielo(data.cielo));
  setContent('mysaContent', renderMysa(data.mysa));
  setContent('lockContent', renderLock(data.lock));
  setContent('roombasCrosstownContent', renderRoombas(data.roombas_crosstown));
  setContent('roombasCabinContent', renderRoombasCabin(data.roombas_cabin));
  setContent('tvContent', renderTV(data.tv));
  setContent('speakersContent', renderSpeakers(data.speakers));
  setContent('cabinSpeakersContent', renderCabinSpeakers(data.cabin_speakers));
  setContent('litterRobotContent', renderLitterRobot(data.litter_robot));
  setContent('petlibroContent', renderPetlibro(data.petlibro));
  setContent('eightSleepContent', renderEightSleep(data['8sleep']));
  setContent('ringContent', renderRing(data.ring));
  setContent('dogWalkContent', renderDogWalk(data.dog_walk));
  document.getElementById('lastUpdated').textContent = formatTimestamp(data.meta && data.meta.timestamp);
  applyLocationFilter();
}

async function fetchStatus(refresh=false) {
  if (state.loading) return;
  state.loading = true;
  try {
    const response = await fetch(`/api/status${refresh ? '?refresh=true' : ''}`);
    const data = await response.json();
    if (!response.ok) {
      throw new Error(data.error || 'Failed to load status');
    }
    state.data = data;
    renderDashboard();
    if (!refresh) {
      showFeedback('');
    }
    // If there are pending devices (cache miss), poll them in background
    const pending = (data.meta && data.meta.pending) || [];
    if (pending.length > 0) {
      pending.forEach(deviceKey => {
        refreshDevice(deviceKey);
      });
    }
  } catch (error) {
    console.error(error);
    showFeedback(error.message || 'Failed to load status', 'error');
  } finally {
    state.loading = false;
  }
}

const DEVICE_TO_COLLECTOR = {
  hue_crosstown: 'hue_crosstown',
  hue_cabin: 'hue_cabin',
  nest: 'nest',
  cielo: 'cielo',
  august: 'lock',
  crosstown_roomba: 'roombas_crosstown',
  cabin_roomba: 'roombas_cabin',
  tv: 'tv',
  speaker: 'speakers',
  cabin_speaker: 'cabin_speakers',
  litter_robot: 'litter_robot',
  petlibro: 'petlibro',
  eightsleep: '8sleep',
};

async function refreshDevice(deviceKey) {
  if (!deviceKey) return;
  try {
    const response = await fetch(`/api/status/${encodeURIComponent(deviceKey)}`);
    const data = await response.json();
    if (!response.ok) {
      throw new Error(data.error || 'Failed to refresh device');
    }
    if (state.data) {
      state.data[deviceKey] = data;
      renderDashboard();
    }
  } catch (error) {
    console.error('refresh failed', error);
  }
}

function collectArgs(button) {
  const args = {};
  const formId = button.dataset.form;
  const fieldNames = (button.dataset.fields || '').split(',').map((item) => item.trim()).filter(Boolean);
  if (formId) {
    const form = document.getElementById(formId);
    if (form) {
      fieldNames.forEach((field) => {
        const input = form.querySelector(`[name="${field}"]`);
        if (!input) return;
        if (input.value !== '') args[field] = input.value;
      });
    }
  }
  if (button.dataset.extra) {
    try {
      Object.assign(args, JSON.parse(button.dataset.extra));
    } catch (error) {
      console.error('Invalid data-extra payload', error);
    }
  }
  return args;
}

async function postCommand(button) {
  const device = button.dataset.device;
  const action = button.dataset.action;
  const args = collectArgs(button);
  const collectorKey = DEVICE_TO_COLLECTOR[device];

  showFeedback(`Running ${device} ${action}...`, '', button);

  try {
    const response = await fetch('/api/command', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ device, action, args }),
    });
    const data = await response.json();
    if (!response.ok || !data.success) {
      throw new Error(data.error || data.output || `Command failed (${response.status})`);
    }
    showFeedback(`${device} ${action} succeeded`, 'success', button);
    if (device === 'nest_camera' && action === 'snap') {
      loadCameraSnap(args.room || 'kitchen', 'nestCameraContent');
    } else if (device === 'ring_camera' && action === 'snap') {
      loadCameraSnap('ring-' + (args.doorbell || 'crosstown'), 'ringSnapContent');
    } else {
      await refreshDevice(collectorKey);
    }
  } catch (error) {
    console.error(error);
    showFeedback(error.message || 'Command failed', 'error', button);
  }
}

document.getElementById('locationSelector').addEventListener('click', (event) => {
  const button = event.target.closest('[data-location-filter]');
  if (!button) return;
  state.location = button.dataset.locationFilter;
  applyLocationFilter();
});

document.getElementById('refreshButton').addEventListener('click', () => fetchStatus(true));

document.addEventListener('click', (event) => {
  const button = event.target.closest('[data-command]');
  if (!button) return;
  postCommand(button);
});

function loadCameraSnap(name, containerId) {
  const container = document.getElementById(containerId);
  if (!container) return;
  const url = '/api/camera-snap/' + encodeURIComponent(name) + '?t=' + Date.now();
  fetch(url).then(resp => {
    if (!resp.ok) {
      container.innerHTML = '<div class="muted">No snapshot yet</div>';
      return;
    }
    const ts = resp.headers.get('X-Snapshot-Timestamp');
    resp.blob().then(blob => {
      const imgUrl = URL.createObjectURL(blob);
      let meta = '';
      if (ts) {
        const d = new Date(parseInt(ts, 10) * 1000);
        const ago = Math.round((Date.now() - d.getTime()) / 60000);
        const agoText = ago < 1 ? 'Just now' : ago < 60 ? ago + 'm ago' : Math.round(ago / 60) + 'h ago';
        meta = '<div class="camera-snap-meta">Captured ' + agoText + '</div>';
      }
      container.innerHTML = '<img src="' + imgUrl + '" class="camera-snap" alt="Camera snapshot">' + meta;
    });
  }).catch(() => {
    container.innerHTML = '<div class="muted">No snapshot yet</div>';
  });
}

// Try to load existing snapshots on page load
loadCameraSnap('kitchen', 'nestCameraContent');
loadCameraSnap('ring-crosstown', 'ringSnapContent');

fetchStatus();
setInterval(() => fetchStatus(), 5 * 60 * 1000);
</script>
</body>
</html>
"""


def run():
    server = ThreadedHTTPServer(("0.0.0.0", PORT), DashboardHandler)

    # Precache all collectors on startup
    threading.Thread(target=collect_status_bundle, daemon=True).start()

    # Periodic background refresh every 5 minutes
    def _periodic_refresh():
        while True:
            time.sleep(300)
            try:
                collect_status_bundle(refresh=True)
            except Exception:
                pass

    threading.Thread(target=_periodic_refresh, daemon=True).start()

    print(f"Home Control Plane running on http://0.0.0.0:{PORT}", flush=True)
    print("  Access via Tailscale IP or localhost", flush=True)

    def shutdown(signum, frame):
        print(f"\nShutting down (signal {signum})...", flush=True)
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


if __name__ == "__main__":
    run()
