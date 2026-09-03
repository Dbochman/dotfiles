#!/usr/bin/env python3
"""Roomba Dashboard — single-file HTTP server with embedded UI.

Serves a JSON API and dashboard for two-home Roomba status, automation
decisions, pause controls, and a calendar of cleaning activity.

Reads dog-walk JSONL plus protected vacancy-decision records for history,
guarded local status for Crosstown, and read-only Google Assistant status for
Cabin.

Same architecture as nest-dashboard.py. Intended for home-LAN and
Tailscale-tailnet access; not public internet.
"""

import calendar
import hashlib
import json
import os
import signal
import stat
import subprocess
import sys
import threading
import time
from datetime import datetime, timedelta, timezone
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path
from socketserver import ThreadingMixIn
from urllib.parse import urlparse, parse_qs
from zoneinfo import ZoneInfo

HISTORY_DIR = os.path.expanduser("~/.openclaw/dog-walk/history")
SNOOZE_FILE = os.path.expanduser("~/.openclaw/dog-walk/snooze.json")
CROSSTOWN_ROOMBA_CLI = os.path.expanduser("~/.openclaw/bin/crosstown-roomba")
CABIN_ROOMBA_CLI = os.path.expanduser("~/.openclaw/bin/roomba")
PRESENCE_STATE_FILE = Path(os.path.expanduser("~/.openclaw/presence/state.json"))
PRESENCE_PRODUCER_FILE = Path(
    os.path.expanduser("~/.openclaw/presence/home-events-outbox/producer-state.json")
)
VACANT_ROOMBA_DIR = Path(os.path.expanduser("~/.openclaw/vacant-roomba/crosstown"))
VACANT_ROOMBA_RUNS_DIR = VACANT_ROOMBA_DIR / "runs"
VACANT_ROOMBA_LATEST_FILE = VACANT_ROOMBA_DIR / "latest-status.json"
PORT = 8553

ROOMBA_CACHE_TTL = 300  # 5 minutes
CROSSTOWN_ROOMBA_TIMEOUT = 30
CABIN_ROOMBA_TIMEOUT = 30
CABIN_ROOMBA_CACHE_TTL = 900  # 15 minutes; preserves daily Assistant quota
CROSSTOWN_ROBOTS = {
    "10max": {"selector": "roomba", "label": "Roomba Combo 10 Max"},
    "j5": {"selector": "scoomba", "label": "Roomba J5 (Scoomba)"},
}
CABIN_ROBOTS = {
    "floomba": "Floomba",
    "philly": "Philly",
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

LOCAL_TIMEZONE = ZoneInfo("America/New_York")
MAX_PROTECTED_FILE_BYTES = 256 * 1024
MAX_PRESENCE_AGE_SECONDS = 30 * 60
ALLOWED_SNOOZE_MINUTES = frozenset({0, 60, 180, 480, 525600})
ALLOWED_SNOOZE_LOCATIONS = frozenset({"all", "crosstown", "cabin"})
ROBOT_ALIASES = frozenset({"roomba", "scoomba"})
DECISION_OUTCOMES = frozenset(
    {
        "started",
        "already_cleaning",
        "snoozed",
        "recent_cat_activity",
        "robot_not_ready",
        "failed",
        "already_handled",
        "not_vacant",
        "in_progress",
    }
)
DECISION_REASONS = frozenset(
    {
        "already_satisfied",
        "automation_directory_unsafe",
        "automation_lock_unsafe",
        "automation_record_oversize",
        "daily_already_handled",
        "daily_record_invalid",
        "litter_history_invalid",
        "litter_history_unavailable",
        "presence_state_invalid",
        "presence_state_mismatch",
        "presence_state_stale",
        "producer_state_invalid",
        "protected_file_unavailable",
        "protected_file_unsafe",
        "protected_json_invalid",
        "recent_cat_activity",
        "robot_not_ready",
        "robot_start_failed",
        "robot_start_unverified",
        "robot_status_invalid",
        "robot_status_unavailable",
        "runtime_io_error",
        "site_not_confirmed_vacant",
        "snooze_policy_invalid",
        "snoozed",
    }
)
OCCUPANCY_VALUES = frozenset(
    {"occupied", "partial", "possibly_vacant", "confirmed_vacant", "unknown"}
)


def _utc_iso_now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace(
        "+00:00", "Z"
    )


def fetch_roomba_status():
    """Fetch Crosstown live status through the guarded CLI, with caching."""
    with _roomba_cache["lock"]:
        if time.time() - _roomba_cache["ts"] < ROOMBA_CACHE_TTL and _roomba_cache["data"] is not None:
            return _roomba_cache["data"]

    robots = {}
    for name, cfg in CROSSTOWN_ROBOTS.items():
        try:
            result = subprocess.run(
                [CROSSTOWN_ROOMBA_CLI, "state", cfg["selector"]],
                capture_output=True,
                text=True,
                timeout=CROSSTOWN_ROOMBA_TIMEOUT,
            )
            if result.returncode != 0:
                robots[name] = {
                    "label": cfg["label"],
                    "error": "status_unavailable",
                }
                continue

            raw = json.loads(result.stdout.strip())
            if not isinstance(raw, dict) or raw.get("connected") is not True:
                robots[name] = {
                    "label": cfg["label"],
                    "error": "status_invalid",
                }
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
        except (subprocess.TimeoutExpired, OSError, json.JSONDecodeError):
            robots[name] = {
                "label": cfg["label"],
                "error": "status_unavailable",
            }

    data = {
        "location": "crosstown",
        "telemetry": "live_local",
        "fetchedAt": _utc_iso_now(),
        "integration": {
            "ok": all(not robot.get("error") for robot in robots.values()),
            "label": "Live local",
        },
        "robots": robots,
    }
    with _roomba_cache["lock"]:
        _roomba_cache["data"] = data
        _roomba_cache["ts"] = time.time()
    return data


def parse_cabin_assistant_status(output):
    """Project a Google Assistant response into a bounded robot status."""
    if not isinstance(output, str):
        return {
            "phase": "unknown",
            "status": "Status unverified",
            "error": "assistant_status_unverified",
        }
    response = ""
    for line in output.splitlines():
        if line.startswith("Response:"):
            response = line.removeprefix("Response:").strip().casefold()
    if not response or response.startswith("ok (command sent"):
        return {
            "phase": "unknown",
            "status": "Status unverified",
            "error": "assistant_status_unverified",
        }
    if any(
        phrase in response
        for phrase in ("isn't running", "is not running", "not currently running")
    ):
        return {"phase": "stop", "status": "Stopped", "error": None}
    if any(
        phrase in response
        for phrase in ("is charging", "on the charger", "at the dock")
    ):
        return {"phase": "charge", "status": "Charging", "error": None}
    if any(phrase in response for phrase in ("returning to", "going back to")):
        return {"phase": "hmUsrDock", "status": "Returning", "error": None}
    if any(
        phrase in response
        for phrase in ("is running", "currently running", "is cleaning")
    ):
        return {"phase": "run", "status": "Cleaning", "error": None}
    if any(
        phrase in response
        for phrase in ("offline", "unavailable", "can't connect", "cannot connect")
    ):
        return {
            "phase": "unknown",
            "status": "Status unavailable",
            "error": "assistant_status_unavailable",
        }
    return {
        "phase": "unknown",
        "status": "Status unverified",
        "error": "assistant_status_unverified",
    }


def project_cabin_assistant_failure(stdout, stderr):
    """Classify an Assistant failure without returning provider diagnostics."""
    message = "\n".join(part for part in (stdout, stderr) if isinstance(part, str))
    if "RESOURCE_EXHAUSTED" in message and "converse_requests" in message:
        error = "assistant_quota_exhausted"
    else:
        error = "assistant_status_unavailable"
    return {
        "phase": "unknown",
        "status": "Status unavailable",
        "error": error,
    }


def fetch_cabin_roomba_status():
    """Fetch Cabin running/stopped state via read-only Assistant queries."""
    with _cabin_roomba_cache["lock"]:
        if (time.time() - _cabin_roomba_cache["ts"] < CABIN_ROOMBA_CACHE_TTL
                and _cabin_roomba_cache["data"] is not None):
            return _cabin_roomba_cache["data"]

    robots = {}
    for selector, label in CABIN_ROBOTS.items():
        try:
            result = subprocess.run(
                [CABIN_ROOMBA_CLI, "status", selector],
                capture_output=True,
                text=True,
                timeout=CABIN_ROOMBA_TIMEOUT,
            )
            if result.returncode != 0:
                projected = project_cabin_assistant_failure(
                    result.stdout, result.stderr
                )
            else:
                projected = parse_cabin_assistant_status(result.stdout)
        except (subprocess.TimeoutExpired, OSError):
            projected = {
                "phase": "unknown",
                "status": "Status unavailable",
                "error": "assistant_status_unavailable",
            }
        robots[selector] = {"name": label, **projected}

    integration_ok = all(not robot["error"] for robot in robots.values())
    errors = {robot["error"] for robot in robots.values() if robot["error"]}
    integration_error = (
        "assistant_quota_exhausted"
        if "assistant_quota_exhausted" in errors
        else "assistant_status_degraded"
    )
    data = {
        "location": "cabin",
        "telemetry": "assistant_status",
        "fetchedAt": _utc_iso_now(),
        "integration": {
            "ok": integration_ok,
            "label": "Assistant status",
            **({} if integration_ok else {"error": integration_error}),
        },
        "robots": robots,
    }

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


def _safe_iso8601(ts_str):
    parsed = _parse_iso8601(ts_str)
    if parsed is None or parsed.tzinfo is None:
        return None
    return parsed.astimezone(timezone.utc).isoformat(timespec="seconds").replace(
        "+00:00", "Z"
    )


def _canonical_json(value):
    return json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
        allow_nan=False,
    ).encode("utf-8")


def _state_hash(value):
    return hashlib.sha256(_canonical_json(value)).hexdigest()


def _read_private_json(path):
    """Read an owner-only regular JSON file without following symlinks."""
    try:
        metadata = path.lstat()
        if (
            not stat.S_ISREG(metadata.st_mode)
            or stat.S_ISLNK(metadata.st_mode)
            or metadata.st_uid != os.getuid()
            or stat.S_IMODE(metadata.st_mode) != 0o600
            or metadata.st_size <= 0
            or metadata.st_size > MAX_PROTECTED_FILE_BYTES
        ):
            return None
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else None
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return None


def _safe_robot_checks(value):
    if not isinstance(value, dict):
        return {}
    result = {}
    for alias in ROBOT_ALIASES:
        state = value.get(alias)
        if not isinstance(state, dict):
            continue
        result[alias] = {
            "phase": state.get("phase") if isinstance(state.get("phase"), str) else None,
            "battery": state.get("battery") if isinstance(state.get("battery"), int) else None,
            "bin_full": state.get("bin_full") if isinstance(state.get("bin_full"), bool) else None,
            "error": state.get("error") if isinstance(state.get("error"), int) else None,
        }
    return result


def normalize_decision(value):
    """Return the non-sensitive, dashboard-safe daily decision view."""
    if not isinstance(value, dict):
        return None
    outcome = value.get("outcome")
    if outcome not in DECISION_OUTCOMES:
        return None
    source = value.get("source")
    if source not in ("scheduled", "vacancy_transition"):
        return None
    started = value.get("started_robots")
    if not isinstance(started, list) or any(item not in ROBOT_ALIASES for item in started):
        return None
    evaluated_at = _safe_iso8601(
        value.get("evaluated_at") or value.get("completed_at") or value.get("started_at")
    )
    if evaluated_at is None:
        return None
    local_date = value.get("local_date")
    try:
        datetime.strptime(local_date, "%Y-%m-%d")
    except (TypeError, ValueError):
        return None
    decision_outcome = value.get("decision_outcome")
    if decision_outcome not in DECISION_OUTCOMES:
        decision_outcome = None
    reason = value.get("reason")
    if reason not in DECISION_REASONS:
        reason = None
    checks = value.get("checks") if isinstance(value.get("checks"), dict) else {}
    presence_check = checks.get("presence")
    if presence_check not in {"confirmed_vacant", "not_confirmed_vacant"}:
        presence_check = None
    snooze_check = checks.get("snooze")
    if snooze_check not in {"active", "clear"}:
        snooze_check = None
    return {
        "evaluated_at": evaluated_at,
        "local_date": local_date,
        "source": source,
        "outcome": outcome,
        "decision_outcome": decision_outcome,
        "reason": reason,
        "started_robots": started,
        "checks": {
            "presence": presence_check,
            "snooze": snooze_check,
            "recent_cat_activity": checks.get("recent_cat_activity") if isinstance(checks.get("recent_cat_activity"), bool) else None,
            "robots": _safe_robot_checks(checks.get("robots")),
        },
    }


def load_presence_summary(now=None):
    now = now or datetime.now(timezone.utc)
    state = _read_private_json(PRESENCE_STATE_FILE)
    producer = _read_private_json(PRESENCE_PRODUCER_FILE)
    result = {
        "verified": False,
        "ageSeconds": None,
        "crosstown": {"occupancy": "unknown", "fresh": False},
        "cabin": {"occupancy": "unknown", "fresh": False},
    }
    if state is None or producer is None:
        return result
    evaluated = _parse_iso8601(producer.get("evaluated_at"))
    if evaluated is None or evaluated.tzinfo is None:
        return result
    age = (now.astimezone(timezone.utc) - evaluated.astimezone(timezone.utc)).total_seconds()
    result["ageSeconds"] = max(0, round(age))
    result["verified"] = (
        state.get("timestamp") == producer.get("evaluated_at")
        and isinstance(producer.get("state_hash"), str)
        and _state_hash(state) == producer["state_hash"]
        and -60 <= age <= MAX_PRESENCE_AGE_SECONDS
    )
    for site in ("crosstown", "cabin"):
        site_state = state.get(site)
        if isinstance(site_state, dict):
            occupancy = site_state.get("occupancy")
            if occupancy not in OCCUPANCY_VALUES:
                occupancy = "unknown"
            result[site] = {
                "occupancy": occupancy,
                "fresh": site_state.get("fresh") is True and result["verified"],
                "stateChangedAt": _safe_iso8601(site_state.get("stateChangedAt")),
            }
    return result


def _next_six_am(now):
    local = now.astimezone(LOCAL_TIMEZONE)
    scheduled = local.replace(hour=6, minute=0, second=0, microsecond=0)
    if scheduled <= local:
        scheduled += timedelta(days=1)
    return scheduled.isoformat()


def collect_automation_state(now=None):
    now = now or datetime.now(timezone.utc)
    local_date = now.astimezone(LOCAL_TIMEZONE).date().isoformat()
    presence = load_presence_summary(now)
    snooze = load_snooze()
    today = normalize_decision(
        _read_private_json(VACANT_ROOMBA_RUNS_DIR / f"{local_date}.json")
    )
    latest = normalize_decision(_read_private_json(VACANT_ROOMBA_LATEST_FILE))
    return {
        "generatedAt": now.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
        "homes": {
            "crosstown": {
                "presence": presence["crosstown"],
                "presenceVerified": presence["verified"],
                "presenceAgeSeconds": presence["ageSeconds"],
                "mode": "daily_and_vacancy",
                "scheduleLabel": "Daily at 6:00 AM + vacancy transition",
                "nextScheduledAt": _next_six_am(now),
                "snoozedUntil": snooze.get("crosstown"),
                "todayDecision": today,
                "latestEvaluation": latest,
            },
            "cabin": {
                "presence": presence["cabin"],
                "presenceVerified": presence["verified"],
                "presenceAgeSeconds": presence["ageSeconds"],
                "mode": "vacancy_transition",
                "scheduleLabel": "Vacancy transition only",
                "nextScheduledAt": None,
                "snoozedUntil": snooze.get("cabin"),
                "todayDecision": None,
                "latestEvaluation": None,
            },
        },
    }


# ---------------------------------------------------------------------------
# Snooze
# ---------------------------------------------------------------------------

def load_snooze():
    """Load snooze state. Returns dict like {"crosstown": "...", "cabin": null}."""
    try:
        if os.path.exists(SNOOZE_FILE):
            with open(SNOOZE_FILE) as f:
                data = json.load(f)
            if not isinstance(data, dict):
                raise ValueError("snooze policy must be an object")
            data = {site: data.get(site) for site in ("crosstown", "cabin")}
            now = datetime.now(timezone.utc)
            changed = False
            for loc in list(data):
                if data[loc] and _parse_iso8601(data[loc]) and _parse_iso8601(data[loc]) < now:
                    data[loc] = None
                    changed = True
            if changed:
                save_snooze(data)
            return data
    except (OSError, ValueError, json.JSONDecodeError):
        pass
    return {"crosstown": None, "cabin": None}


def save_snooze(data):
    """Persist snooze state to disk."""
    os.makedirs(os.path.dirname(SNOOZE_FILE), exist_ok=True)
    tmp = SNOOZE_FILE + ".tmp"
    with open(tmp, "w") as f:
        json.dump(data, f, indent=2)
    os.chmod(tmp, 0o600)
    os.replace(tmp, SNOOZE_FILE)


# ---------------------------------------------------------------------------
# Calendar heatmap data
# ---------------------------------------------------------------------------

def load_calendar_data(year, month):
    """Load cleaning and automation decisions for a calendar month.

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

    # Add protected Crosstown daily vacancy decisions. These are actual
    # controller outcomes, not inferences from current robot state.
    for day in range(1, num_days + 1):
        date_str = f"{year}-{month:02d}-{day:02d}"
        decision = normalize_decision(
            _read_private_json(VACANT_ROOMBA_RUNS_DIR / f"{date_str}.json")
        )
        if decision is None:
            continue
        crosstown.setdefault(day, []).append(
            {
                "time": decision.get("evaluated_at"),
                "trigger": (
                    "daily_vacancy"
                    if decision["source"] == "scheduled"
                    else "vacancy_transition"
                ),
                "outcome": decision["outcome"],
                "success": decision["outcome"] in {"started", "already_cleaning"},
                "skipped": decision.get("reason")
                if decision["outcome"] not in {"started", "already_cleaning"}
                else None,
                "roombas": decision.get("started_robots", []),
            }
        )

    for loc_data in (crosstown, cabin):
        for entries in loc_data.values():
            entries.sort(key=lambda item: item.get("time") or "")

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
        elif path == "/api/automation":
            self._serve_automation()
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
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(body)

    def _serve_roombas(self):
        self._respond(200, fetch_roomba_status())

    def _serve_cabin_roombas(self):
        self._respond(200, fetch_cabin_roomba_status())

    def _serve_snooze_status(self):
        self._respond(200, load_snooze())

    def _serve_automation(self):
        self._respond(200, collect_automation_state())

    def _set_snooze(self, location, minutes):
        if (
            location not in ALLOWED_SNOOZE_LOCATIONS
            or not isinstance(minutes, int)
            or isinstance(minutes, bool)
            or minutes not in ALLOWED_SNOOZE_MINUTES
        ):
            self._respond(400, {"error": "invalid snooze request"})
            return
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
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("X-Content-Type-Options", "nosniff")
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
body{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',system-ui,sans-serif;background:var(--bg);color:var(--text);padding:1rem;max-width:1320px;margin:0 auto}

/* Header */
.header{display:flex;align-items:center;justify-content:space-between;margin-bottom:1rem;flex-wrap:wrap;gap:0.5rem}
.header h1{font-size:1.15rem;font-weight:600}
.header-copy p{font-size:0.74rem;color:var(--muted);margin-top:0.2rem}
.header-right{display:flex;align-items:center;gap:1rem}
.last-update{font-size:0.7rem;color:var(--muted)}
.view-switch{display:flex;background:rgba(255,255,255,0.04);border:1px solid var(--border);border-radius:8px;padding:3px;gap:2px}
.view-btn{border:0;background:transparent;color:var(--muted);padding:0.35rem 0.75rem;border-radius:5px;font-size:0.72rem;cursor:pointer}
.view-btn.active{background:#3b82f6;color:white}

/* Home comparison */
.homes-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:1rem;margin-bottom:1rem}
.home-panel{background:var(--surface);border:1px solid var(--border);border-radius:12px;padding:0.9rem;min-width:0}
.home-panel.hidden{display:none}
.homes-grid.single{grid-template-columns:1fr}
.home-head{display:flex;justify-content:space-between;align-items:flex-start;gap:0.75rem;margin-bottom:0.75rem}
.home-title{font-size:1rem;font-weight:650}
.home-subtitle{font-size:0.7rem;color:var(--muted);margin-top:0.15rem}
.telemetry{font-size:0.65rem;color:var(--muted);border:1px solid var(--border);border-radius:999px;padding:0.2rem 0.5rem;white-space:nowrap}
.automation-summary{background:rgba(255,255,255,0.025);border:1px solid rgba(255,255,255,0.06);border-radius:9px;padding:0.7rem;margin-bottom:0.75rem}
.automation-top{display:flex;justify-content:space-between;gap:0.5rem;align-items:center;margin-bottom:0.35rem}
.automation-label{font-size:0.65rem;color:var(--muted);text-transform:uppercase;letter-spacing:0.05em}
.automation-state{font-size:0.9rem;font-weight:650}
.automation-detail{font-size:0.72rem;color:var(--muted);line-height:1.45}
.automation-reason{font-size:0.72rem;color:#f59e0b;margin-top:0.25rem}
@media(max-width:860px){.homes-grid{grid-template-columns:1fr}.header-right{width:100%;justify-content:space-between}}

/* Stat cards */
.stats{display:grid;grid-template-columns:repeat(auto-fit,minmax(160px,1fr));gap:0.75rem;margin-bottom:1rem}
.stat{background:var(--surface);border:1px solid var(--border);border-radius:10px;padding:0.85rem}
.home-panel .stat{background:rgba(15,17,23,0.55)}
.stat-label{font-size:0.65rem;text-transform:uppercase;letter-spacing:0.05em;color:var(--muted);margin-bottom:0.2rem}
.stat-value{font-size:1.5rem;font-weight:700}
.stat-sub{font-size:0.75rem;color:var(--muted);margin-top:0.15rem}
.readiness{font-size:0.69rem;margin-top:0.5rem;padding-top:0.45rem;border-top:1px solid rgba(255,255,255,0.06)}

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
  <div class="header-copy"><h1>Roomba Dashboard</h1><p>Two homes, live status, and cleaning decisions.</p></div>
  <div class="header-right">
    <div class="view-switch" role="group" aria-label="Location view">
      <button class="view-btn active" data-view="both">Both</button>
      <button class="view-btn" data-view="crosstown">Crosstown</button>
      <button class="view-btn" data-view="cabin">Cabin</button>
    </div>
    <span class="last-update" id="lastUpdate"></span>
  </div>
</div>

<div class="error-banner" id="errorBanner"></div>

<div class="homes-grid" id="homesGrid">
  <section class="home-panel" data-site="crosstown">
    <div class="home-head">
      <div><div class="home-title">Crosstown</div><div class="home-subtitle">West Roxbury</div></div>
      <span class="telemetry" id="telemetry-crosstown">Live local</span>
    </div>
    <div class="automation-summary" id="automation-crosstown"><div class="loading">Loading automation…</div></div>
    <div class="stats" id="crosstownCards"><div class="loading">Loading robots…</div></div>
  </section>
  <section class="home-panel" data-site="cabin">
    <div class="home-head">
      <div><div class="home-title">Cabin</div><div class="home-subtitle">Phillipston</div></div>
      <span class="telemetry" id="telemetry-cabin">Assistant status</span>
    </div>
    <div class="automation-summary" id="automation-cabin"><div class="loading">Loading automation…</div></div>
    <div class="stats" id="cabinCards"><div class="loading">Loading robots…</div></div>
  </section>
</div>

<div class="snooze-bar" id="snoozeBar">
  <h3>Automation Pause</h3>
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
    <h2>Cleaning &amp; Decision History</h2>
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
const TRIGGER_LABELS = { manual:'Manual', dog_walk:'Dog walk', daily_vacancy:'6 AM vacancy', vacancy_transition:'Vacancy transition' };
const OUTCOME_LABELS = {
  started:'Started', already_cleaning:'Already cleaning', snoozed:'Paused',
  recent_cat_activity:'Cat activity hold', robot_not_ready:'Not ready',
  failed:'Failed', already_handled:'Already handled', not_vacant:'Home occupied',
  in_progress:'Evaluating'
};
const REASON_LABELS = {
  recent_cat_activity:'Recent litter-box activity safety hold',
  snoozed:'Automation is paused', robot_not_ready:'One or more robots are not safely ready',
  already_satisfied:'Both robots are already cleaning', daily_already_handled:'Today\'s vacancy run was already handled',
  site_not_confirmed_vacant:'Home is not confirmed vacant', not_confirmed_vacant:'Home is not confirmed vacant',
  presence_state_stale:'Presence evidence is stale', presence_state_mismatch:'Presence evidence could not be verified',
  robot_status_unavailable:'Robot readiness is unavailable', litter_history_unavailable:'Cat activity history is unavailable',
  robot_start_failed:'A robot start command failed', robot_start_unverified:'A robot start could not be verified'
};

let calYear, calMonth, calData = null, currentView = 'both';
const now = new Date();
calYear = now.getFullYear();
calMonth = now.getMonth() + 1;

// ── Helpers ──

function esc(value) {
  return String(value == null ? '' : value)
    .replaceAll('&', '&amp;').replaceAll('<', '&lt;').replaceAll('>', '&gt;')
    .replaceAll('"', '&quot;').replaceAll("'", '&#39;');
}

function fmtTime(iso) {
  if (!iso) return '-';
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return '-';
  return d.toLocaleTimeString('en-US', { hour:'numeric', minute:'2-digit' });
}

function fmtDateTime(iso) {
  if (!iso) return '—';
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return '—';
  return d.toLocaleString('en-US', { month:'short', day:'numeric', hour:'numeric', minute:'2-digit' });
}

function fmtAge(seconds) {
  if (!Number.isFinite(seconds)) return 'unknown age';
  if (seconds < 60) return 'less than a minute ago';
  if (seconds < 3600) return Math.round(seconds / 60) + 'm ago';
  return Math.round(seconds / 3600) + 'h ago';
}

function outcomeLabel(decision) {
  if (!decision) return null;
  const effective = decision.outcome === 'already_handled' && decision.decision_outcome
    ? decision.decision_outcome : decision.outcome;
  return OUTCOME_LABELS[effective] || 'Decision recorded';
}

function reasonLabel(reason) {
  return REASON_LABELS[reason] || (reason ? 'Automation stopped safely' : '');
}

function applyView() {
  document.getElementById('homesGrid').classList.toggle('single', currentView !== 'both');
  for (const panel of document.querySelectorAll('.home-panel')) {
    panel.classList.toggle('hidden', currentView !== 'both' && panel.dataset.site !== currentView);
  }
  for (const group of document.querySelectorAll('.snooze-group')) {
    group.style.display = currentView === 'both' || group.dataset.loc === currentView ? 'flex' : 'none';
  }
  for (const button of document.querySelectorAll('.view-btn')) {
    button.classList.toggle('active', button.dataset.view === currentView);
    button.setAttribute('aria-pressed', button.dataset.view === currentView ? 'true' : 'false');
  }
  if (calData) renderCalendar(calData);
}

document.querySelector('.view-switch').addEventListener('click', event => {
  const button = event.target.closest('.view-btn');
  if (!button) return;
  currentView = button.dataset.view;
  applyView();
});

// ── Roomba Cards ──

function renderCrosstownCards(roombas) {
  const el = document.getElementById('crosstownCards');
  const telemetry = document.getElementById('telemetry-crosstown');
  const integrationOK = Boolean(roombas && roombas.integration && roombas.integration.ok);
  telemetry.textContent = integrationOK ? 'Live local' : 'Live status degraded';
  telemetry.style.color = integrationOK ? C.green : C.amber;
  if (!roombas || !roombas.robots) {
    el.innerHTML = '<div class="stat"><div class="stat-label">Integration</div><div class="stat-value" style="color:' + C.amber + '">Unavailable</div><div class="readiness" style="color:' + C.muted + '">Readiness could not be checked</div></div>';
    return;
  }

  let html = '';
  for (const [id, r] of Object.entries(roombas.robots)) {
    if (typeof r.error === 'string') {
      html += '<div class="stat"><div class="stat-label">' + esc(r.label || id) + '</div>';
      html += '<div class="stat-value" style="color:' + C.amber + '">Status unavailable</div>';
      html += '<div class="readiness" style="color:' + C.muted + '">Readiness could not be checked</div></div>';
      continue;
    }

    const status = r.status || 'Unknown';
    const phase = r.phase || '';
    const isActive = ['run', 'hmMidMsn', 'hmUsrDock', 'hmPostMsn', 'evac', 'new'].includes(phase);
    const isError = phase === 'stuck' || r.error > 0;
    const statusColor = isError ? C.red : isActive ? C.green : C.muted;

    const bat = r.battery;
    const batColor = bat == null ? C.muted : bat > 50 ? C.green : bat > 20 ? C.amber : C.red;

    html += '<div class="stat"><div class="stat-label">' + esc(r.label || id) + '</div>';
    html += '<div class="stat-value" style="color:' + statusColor + '">' + esc(status) + '</div>';

    let sub = '';
    if (Number.isFinite(bat)) sub += '<span style="color:' + batColor + '">' + esc(bat) + '% battery</span>';
    if (r.binFull) sub += ' <span class="badge badge-amber">Bin Full</span>';
    if (!r.binPresent) sub += ' <span class="badge badge-red">No Bin</span>';
    if (sub) html += '<div class="stat-sub">' + sub + '</div>';

    if (Number.isFinite(r.tank)) html += '<div class="stat-sub">Tank: ' + esc(r.tank) + '%</div>';
    if (Number.isFinite(r.error) && r.error > 0) html += '<div class="stat-sub" style="color:' + C.red + '">Robot error present</div>';
    if (Number.isFinite(r.missions)) html += '<div class="stat-sub">' + esc(r.missions) + ' total missions</div>';
    let readiness = 'Eligible for vacancy automation';
    let readinessColor = C.green;
    if (phase === 'run') readiness = 'Already active · no duplicate start';
    else if (r.binFull) { readiness = 'Blocked · bin full'; readinessColor = C.amber; }
    else if (!r.binPresent) { readiness = 'Blocked · bin missing'; readinessColor = C.red; }
    else if (Number.isFinite(r.error) && r.error > 0) { readiness = 'Blocked · robot error'; readinessColor = C.red; }
    else if (Number.isFinite(bat) && bat < 30) { readiness = 'Blocked · battery below 30%'; readinessColor = C.amber; }
    else if (!['charge', 'stop'].includes(phase)) { readiness = 'Not in a safe start state'; readinessColor = C.amber; }
    html += '<div class="readiness" style="color:' + readinessColor + '">' + readiness + '</div>';
    html += '</div>';
  }
  el.innerHTML = html || '<div class="stat"><div class="stat-label">Integration</div><div class="stat-value" style="color:' + C.amber + '">No robot data</div></div>';
}

function renderCabinCards(data) {
  const el = document.getElementById('cabinCards');
  const telemetry = document.getElementById('telemetry-cabin');
  const integrationOK = Boolean(data && data.integration && data.integration.ok);
  telemetry.textContent = integrationOK ? 'Assistant status' : 'Assistant status degraded';
  telemetry.style.color = integrationOK ? C.green : C.amber;
  if (!data || !data.robots || !Object.keys(data.robots).length) {
    el.innerHTML = '<div class="stat"><div class="stat-label">Integration</div><div class="stat-value" style="color:' + C.amber + '">Unavailable</div><div class="stat-sub">Assistant status could not be refreshed</div><div class="readiness" style="color:' + C.muted + '">No physical command was attempted</div></div>';
    return;
  }

  let html = '';
  const quotaExhausted = data.integration && data.integration.error === 'assistant_quota_exhausted';
  for (const [id, r] of Object.entries(data.robots)) {
    const phase = r.phase || 'unknown';
    if (r.error) {
      html += '<div class="stat"><div class="stat-label">' + esc(r.name || id) + '</div>';
      html += '<div class="stat-value" style="color:' + C.amber + '">' + esc(r.status || 'Status unavailable') + '</div>';
      const detail = quotaExhausted
        ? 'Daily Assistant request limit reached · controls may also be unavailable'
        : 'Assistant response was not sufficient to verify state';
      html += '<div class="readiness" style="color:' + C.muted + '">' + detail + '</div></div>';
      continue;
    }

    const label = r.status || 'Unknown';
    const color = phase === 'run' ? C.green : phase === 'charge' ? C.blue : C.muted;

    html += '<div class="stat"><div class="stat-label">' + esc(r.name || id) + '</div>';
    html += '<div class="stat-value" style="color:' + color + '">' + esc(label) + '</div>';
    const detail = phase === 'run'
      ? 'Assistant confirms active cleaning'
      : 'Assistant confirms not cleaning; battery and bin readiness remain app-only';
    html += '<div class="readiness" style="color:' + (phase === 'run' ? C.green : C.muted) + '">' + detail + '</div>';
    html += '</div>';
  }
  el.innerHTML = html;
}

// ── Automation ──

function renderAutomation(data) {
  for (const site of ['crosstown', 'cabin']) {
    const el = document.getElementById('automation-' + site);
    const home = data && data.homes ? data.homes[site] : null;
    if (!home) {
      el.innerHTML = '<div class="automation-state" style="color:' + C.amber + '">Automation status unavailable</div>';
      continue;
    }
    const presence = home.presence || {};
    const occupancyLabels = { confirmed_vacant:'Confirmed vacant', occupied:'Occupied', partial:'Partially occupied', unknown:'Unknown' };
    const occupancy = occupancyLabels[presence.occupancy] || 'Unknown';
    const verified = home.presenceVerified && presence.fresh;
    const badge = verified ? '<span class="badge badge-green">Verified</span>' : '<span class="badge badge-amber">Unverified</span>';
    const decision = home.todayDecision || home.latestEvaluation;
    let html = '<div class="automation-top"><div><div class="automation-label">Home automation</div><div class="automation-state">' + esc(occupancy) + '</div></div>' + badge + '</div>';
    html += '<div class="automation-detail">' + esc(home.scheduleLabel || 'No schedule') + '</div>';
    html += '<div class="automation-detail">Presence ' + esc(fmtAge(home.presenceAgeSeconds)) + '</div>';
    if (home.nextScheduledAt) html += '<div class="automation-detail">Next daily check: ' + esc(fmtDateTime(home.nextScheduledAt)) + '</div>';
    if (decision) {
      html += '<div class="automation-detail">Latest decision: ' + esc(outcomeLabel(decision)) + ' · ' + esc(fmtDateTime(decision.evaluated_at)) + '</div>';
      const reason = reasonLabel(decision.reason);
      if (reason) html += '<div class="automation-reason">' + esc(reason) + '</div>';
    } else if (site === 'cabin') {
      html += '<div class="automation-detail">Runs only when a protected vacancy transition requests it.</div>';
    } else {
      html += '<div class="automation-detail">No daily decision recorded yet.</div>';
    }
    if (home.snoozedUntil) html += '<div class="automation-reason">Paused until ' + esc(fmtDateTime(home.snoozedUntil)) + '</div>';
    el.innerHTML = html;
  }
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
      loadAutomation();
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
  const locations = currentView === 'both' ? ['crosstown', 'cabin'] : [currentView];
  grid.style.gridTemplateColumns = locations.length === 1 ? '1fr' : '';
  for (const loc of locations) {
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
  html += '<div style="color:var(--muted);font-size:0.68rem;margin-bottom:0.3rem">' + runs.length + ' activit' + (runs.length > 1 ? 'ies' : 'y') + ' or decision' + (runs.length > 1 ? 's' : '') + '</div>';

  for (const run of runs) {
    html += '<div class="tt-run">';
    html += '<div>' + esc(fmtTime(run.time));
    const triggerLabel = TRIGGER_LABELS[run.trigger] || 'Automation';
    const triggerClass = run.trigger === 'manual' ? 'badge-blue' : run.trigger === 'daily_vacancy' ? 'badge-blue' : 'badge-muted';
    html += ' <span class="badge ' + triggerClass + '">' + esc(triggerLabel) + '</span>';
    if (run.outcome) {
      const good = ['started', 'already_cleaning', 'already_handled', 'not_vacant'].includes(run.outcome);
      const caution = ['snoozed', 'recent_cat_activity', 'robot_not_ready', 'in_progress'].includes(run.outcome);
      html += ' <span class="badge ' + (good ? 'badge-green' : caution ? 'badge-amber' : 'badge-red') + '">' + esc(OUTCOME_LABELS[run.outcome] || 'Recorded') + '</span>';
    } else if (run.success) html += ' <span class="badge badge-green">Verified</span>';
    else if (run.skipped) html += ' <span class="badge badge-amber">' + esc(reasonLabel(run.skipped) || 'Skipped safely') + '</span>';
    else html += ' <span class="badge badge-red">Failed</span>';
    html += '</div>';

    let detail = '';
    if (run.roombas && run.roombas.length) detail += run.roombas.map(name => name === 'roomba' ? 'Roomba Combo 10 Max' : name === 'scoomba' ? 'Roomba J5 (Scoomba)' : String(name)).join(', ');
    if (run.duration_min != null) detail += (detail ? ' · ' : '') + Math.round(run.duration_min) + 'min walk';
    if (run.return_signal) detail += (detail ? ' · ' : '') + (SIGNAL_LABELS[run.return_signal] || run.return_signal);
    if (detail) html += '<div class="tt-detail">' + esc(detail) + '</div>';
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

async function loadAutomation() {
  const response = await fetch('/api/automation');
  if (!response.ok) throw new Error('automation unavailable');
  renderAutomation(await response.json());
}

// ── Refresh ──

async function refresh() {
  try {
    const [snoozeResp, automationResp] = await Promise.all([
      fetch('/api/snooze'),
      fetch('/api/automation'),
    ]);
    if (snoozeResp.ok) renderSnooze(await snoozeResp.json());
    if (automationResp.ok) renderAutomation(await automationResp.json());
    document.getElementById('lastUpdate').textContent = 'Updated ' + new Date().toLocaleTimeString();
    document.getElementById('errorBanner').style.display = 'none';
  } catch (err) {
    console.error('Refresh failed:', err);
    document.getElementById('errorBanner').textContent = 'Some dashboard data could not be refreshed.';
    document.getElementById('errorBanner').style.display = 'block';
  }
  // Roombas fetched separately — local and Assistant calls can be slow
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
applyView();
setInterval(refresh, 5 * 60 * 1000);
setInterval(loadCalendar, 5 * 60 * 1000);
</script>
</body>
</html>
"""

if __name__ == "__main__":
    run()
