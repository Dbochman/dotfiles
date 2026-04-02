#!/usr/bin/env python3
"""Ring Doorbell real-time event listener.

Listens for doorbell dings and person-detected motion via FCM push notifications.
Sends iMessage alerts with doorbell camera frames to Dylan via BlueBubbles API.

Runs as a persistent LaunchAgent (ai.openclaw.ring-listener).
"""

import asyncio
import json
import os
import subprocess
import sys
import threading
import time
import urllib.request
import urllib.error
import uuid
from datetime import datetime, timezone
from pathlib import Path

# Use venv packages
VENV_SITE = Path.home() / ".openclaw/ring/venv/lib"
for p in VENV_SITE.glob("python*/site-packages"):
    sys.path.insert(0, str(p))

import aiohttp
import aiofiles

from ring_doorbell import Auth, Ring, RingEvent, RingEventListener
from ring_doorbell.listen.listenerconfig import RingEventListenerConfig

# Config
CONFIG_DIR = Path.home() / ".config" / "ring"
TOKEN_FILE = CONFIG_DIR / "token-cache.json"
FCM_CREDS_FILE = Path.home() / ".openclaw/ring-listener/fcm-credentials.json"
FRAME_DIR = Path.home() / ".openclaw/ring-listener/frames"

BB_URL = "http://localhost:1234"
DYLAN_CHAT = "any;-;dylanbochman@gmail.com"
USER_AGENT = "OpenClaw/1.0"
FFMPEG = "/opt/homebrew/bin/ffmpeg"
OAUTH_CACHE = Path.home() / ".openclaw/.anthropic-oauth-cache"
VISION_MODEL = "claude-haiku-4-5-20251001"

VISION_PROMPT = (
    "Analyze this front door camera footage. Respond with ONLY valid JSON (no markdown, no ```), "
    "using this exact schema:\n"
    '{"description":"<1 sentence>","people":["<name or unknown>"],"dogs":["<breed or name>"]}\n\n'
    "Count every person and every dog visible across all frames, even if only briefly. "
    "Known dogs: Potato (large brown/gold dog with a dark black face); "
    "Coconut (medium white and pink pitbull). "
    "If you see a person, add an entry. If you see a dog, add an entry. "
    "Empty lists if none visible."
)

# Doorbell ID → location mapping
DOORBELL_LOCATIONS = {
    684794187: "crosstown",
    697442349: "cabin",
}

# Roomba commands per location
ROOMBA_COMMANDS = {
    "crosstown": {
        "start": ["crosstown-roomba", "start", "all"],
        "dock": ["crosstown-roomba", "dock", "all"],
    },
    "cabin": {
        "start_1": ["roomba", "start", "floomba"],
        "start_2": ["roomba", "start", "philly"],
        "dock_1": ["roomba", "dock", "floomba"],
        "dock_2": ["roomba", "dock", "philly"],
    },
}

OPENCLAW_BIN = str(Path.home() / ".openclaw/bin")

# Fi GPS geofence locations — coordinates loaded from env vars at startup
_FI_LOCATIONS = {}
if os.environ.get("CROSSTOWN_LAT") and os.environ.get("CROSSTOWN_LON"):
    _FI_LOCATIONS["crosstown"] = {
        "lat": float(os.environ["CROSSTOWN_LAT"]),
        "lon": float(os.environ["CROSSTOWN_LON"]),
        "radius_m": 150,
    }
if os.environ.get("CABIN_LAT") and os.environ.get("CABIN_LON"):
    _FI_LOCATIONS["cabin"] = {
        "lat": float(os.environ["CABIN_LAT"]),
        "lon": float(os.environ["CABIN_LON"]),
        "radius_m": 300,
    }
STATE_FILE = Path.home() / ".openclaw/ring-listener/state.json"
HISTORY_DIR = Path.home() / ".openclaw/ring-listener/history"

# Home address per location — used for FindMy return detection
HOME_ADDRESSES = {
    "crosstown": {
        "street": "Crosstown Ave",
        "area": "West Roxbury, Boston",
        "radius": "1 block",
        "landmarks": "Crosstown Ave runs parallel to and one block south of Stimson St. Look for Stimson St on the map — home is one block below it. Also near: Mishkan Tefila Memorial Park, What The Trucks, Best Name Tape",
    },
    "cabin": {
        "street": "95 School House Rd",
        "area": "Phillipston, MA",
        "radius": "0.2 miles",
        "landmarks": "95 School House Rd is on the north side of the intersection where School House Rd meets Willis Rd. Look for where these two roads cross — home is just north of that junction. Also near: Cobb Hill Rd",
    },
}


# State file serialization lock — protects read-modify-write transactions
_state_lock = threading.Lock()
# Transient keys only valid on departure_skip events — stripped on all other writes
_SKIP_KEYS = {"skip_reason", "skip_location", "skip_details"}

# Dedup: track recent event IDs to avoid double-notify
_recent_events: dict[int, float] = {}
_DEDUP_WINDOW = 300  # 5 minutes

# Roomba cooldown: prevent re-triggering within 2 hours per location
_roomba_last_action: dict[str, float] = {}
_ROOMBA_COOLDOWN = 7200  # 2 hours

# Return monitoring state
_return_poll_task: asyncio.Task | None = None
_return_monitor_active: bool = False  # True while monitoring for return
_ring_motion_during_walk: bool = False  # Set when Ring detects motion while monitoring

# Departure accumulator: track people/dogs across recent events within a window
_DEPARTURE_WINDOW = 180  # 3 minutes
_departure_sightings: list[dict] = []  # [{"time": float, "people": int, "dogs": int, "location": str}]

# Cabin confirmation prompt cooldown: once prompted in a walk window, suppress
# until the next window. Keyed by (location, window_start_hour).
_cabin_prompt_sent: dict[tuple[str, int], bool] = {}

# Pending confirmation: after sending a PARTIAL DEPARTURE prompt, poll BB for
# Dylan's reply ("start roombas") so the listener can start Roombas + return
# monitoring directly instead of relying on OpenClaw's agent to call dog-walk-start.
_pending_confirmation: dict | None = None  # {"location": str, "sent_at_ms": int}
_CONFIRMATION_POLL_INTERVAL = 5  # seconds
_CONFIRMATION_TIMEOUT = 1800  # 30 minutes — stop polling if no reply

# Inbox directory for external processes to request return monitoring.
# Write a JSON file with {"location": "cabin", "requested_at": "<ISO>"}.
_INBOX_DIR = Path.home() / ".openclaw/ring-listener/inbox"
_INBOX_POLL_INTERVAL = 5  # seconds
_INBOX_MAX_AGE = 7200  # 2 hours — ignore stale requests


def log(msg: str) -> None:
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{ts}] {msg}\n"
    sys.stdout.write(line)
    sys.stdout.flush()


def _read_state() -> dict:
    """Read current state file, or return empty state."""
    try:
        if STATE_FILE.exists():
            return json.loads(STATE_FILE.read_text())
    except Exception as e:
        log(f"WARNING: Failed to read state file: {e}")
    return {}


def _write_state(state: dict, event_type: str = "state_update") -> None:
    """Write state atomically (temp + rename) and append to daily history JSONL.

    Called inside _state_lock by _update_state_* functions — do NOT acquire lock here.
    """
    # Strip stale skip metadata from previous writes — preserve on departure_skip
    if event_type != "departure_skip":
        for key in _SKIP_KEYS:
            state.pop(key, None)
    state["event_type"] = event_type
    state["timestamp"] = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)

    # Atomic write: write to temp file, fsync, then rename
    tmp_path = STATE_FILE.with_suffix(".tmp")
    data = json.dumps(state, indent=2)
    with open(tmp_path, "w") as f:
        f.write(data)
        f.flush()
        os.fsync(f.fileno())
    os.replace(str(tmp_path), str(STATE_FILE))

    # Append to daily history with flush
    HISTORY_DIR.mkdir(parents=True, exist_ok=True)
    history_file = HISTORY_DIR / f"{datetime.utcnow().strftime('%Y-%m-%d')}.jsonl"
    with open(history_file, "a") as f:
        f.write(json.dumps(state) + "\n")
        f.flush()


def _update_state_dog_walk(
    location: str,
    event: str,
    people: int = 0,
    dogs: int = 0,
    return_signal: str | None = None,
    roomba_result: dict | None = None,
    walkers: list[str] | None = None,
    skip_reason: str | None = None,
    skip_details: dict | None = None,
) -> None:
    """Update state file with dog walk event."""
    with _state_lock:
        state = _read_state()
        now = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")

        walk = state.get("dog_walk") or {}
        roombas = state.get("roombas") or {}
        loc_roombas = roombas.get(location) or {}

        if event == "departure":
            walk = {
                "active": True,
                "location": location,
                "departed_at": now,
                "returned_at": None,
                "people": people,
                "dogs": dogs,
                "walkers": None,
                "return_signal": None,
                "walk_duration_minutes": None,
            }
            loc_roombas = {
                "status": "running",
                "started_at": now,
                "docked_at": None,
                "trigger": "dog_walk_departure",
            }
        elif event in ("dock", "dock_timeout"):
            walk["active"] = False
            walk["returned_at"] = now
            walk["return_signal"] = return_signal
            # Compute walk duration
            departed_at = walk.get("departed_at")
            if departed_at:
                try:
                    departed = datetime.strptime(departed_at, "%Y-%m-%dT%H:%M:%SZ")
                    returned = datetime.strptime(now, "%Y-%m-%dT%H:%M:%SZ")
                    walk["walk_duration_minutes"] = round(
                        (returned - departed).total_seconds() / 60, 1
                    )
                except ValueError:
                    pass
            loc_roombas["status"] = "docked"
            loc_roombas["docked_at"] = now
            if event == "dock_timeout":
                loc_roombas["trigger"] = "timeout_fallback"
        elif event == "walkers_detected":
            walk["walkers"] = walkers
        elif event == "departure_skip":
            state["skip_reason"] = skip_reason
            state["skip_location"] = location
            if skip_details:
                state["skip_details"] = skip_details

        if roomba_result is not None:
            loc_roombas["last_command_result"] = roomba_result

        roombas[location] = loc_roombas
        state["dog_walk"] = walk
        state["roombas"] = roombas
        _write_state(state, event_type=event)
    log(f"STATE: dog_walk event={event} location={location}")


def _update_state_vision(vision_data: dict, event_id: int = 0) -> None:
    """Record the latest vision analysis result in state."""
    with _state_lock:
        state = _read_state()
        state["last_vision"] = {
            "event_id": event_id,
            "description": vision_data.get("description", ""),
            "people": len(vision_data.get("people", [])),
            "dogs": len(vision_data.get("dogs", [])),
            "people_list": vision_data.get("people", []),
            "dogs_list": vision_data.get("dogs", []),
            "analyzed_at": datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
        }
        _write_state(state, event_type="vision")


def _update_state_return_monitor(
    location: str, event: str, fi_result: dict | None = None, network_detail: dict | None = None
) -> None:
    """Update return monitoring state."""
    with _state_lock:
        state = _read_state()
        now = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
        # Read from new key, fall back to old for backward compat
        monitoring = state.get("return_monitoring") or state.get("findmy_polling") or {}

        if event == "start":
            monitoring = {
                "active": True,
                "location": location,
                "started_at": now,
                "polls": 0,
                "last_poll_at": None,
                "last_fi_gps": None,
                "last_network_check": None,
            }
        elif event == "poll":
            monitoring["polls"] = monitoring.get("polls", 0) + 1
            monitoring["last_poll_at"] = now
            if fi_result:
                monitoring["last_fi_gps"] = {
                    "distance_m": fi_result.get("distance_to_monitored"),
                    "at_location": fi_result.get("at_monitored_location", False),
                    "battery": fi_result.get("battery"),
                    "activity": fi_result.get("activity"),
                    "age_s": fi_result.get("age_s"),
                }
            if network_detail:
                monitoring["last_network_check"] = network_detail
        elif event == "stop":
            monitoring["active"] = False

        state["return_monitoring"] = monitoring
        # Remove old key if present
        state.pop("findmy_polling", None)
        _write_state(state, event_type=f"return_{event}")


def _emit_skip_event(location: str, reason: str, details: dict | None = None) -> None:
    """Convenience wrapper to emit a departure_skip via _update_state_dog_walk."""
    _update_state_dog_walk(location, "departure_skip", skip_reason=reason, skip_details=details)


def load_ring_token() -> dict | None:
    if TOKEN_FILE.exists():
        try:
            return json.loads(TOKEN_FILE.read_text())
        except (json.JSONDecodeError, KeyError):
            pass
    return None


def save_ring_token(token_data: dict) -> None:
    TOKEN_FILE.write_text(json.dumps(token_data))
    TOKEN_FILE.chmod(0o600)


def load_fcm_credentials() -> dict | None:
    if FCM_CREDS_FILE.exists():
        try:
            return json.loads(FCM_CREDS_FILE.read_text())
        except (json.JSONDecodeError, KeyError):
            pass
    return None


def save_fcm_credentials(creds: dict) -> None:
    FCM_CREDS_FILE.parent.mkdir(parents=True, exist_ok=True)
    FCM_CREDS_FILE.write_text(json.dumps(creds))
    FCM_CREDS_FILE.chmod(0o600)
    log("FCM credentials updated")


def bb_password() -> str:
    return os.environ.get("BLUEBUBBLES_PASSWORD", "")


def send_imessage(text: str) -> bool:
    pw = bb_password()
    if not pw:
        log("ERROR: BLUEBUBBLES_PASSWORD not set")
        return False
    try:
        data = json.dumps({
            "chatGuid": DYLAN_CHAT,
            "tempGuid": str(uuid.uuid4()).upper(),
            "message": text,
            "method": "private-api",
        }).encode()
        req = urllib.request.Request(
            f"{BB_URL}/api/v1/message/text?password={pw}",
            data=data,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        resp = urllib.request.urlopen(req, timeout=10)
        result = json.loads(resp.read().decode())
        return result.get("status") == 200
    except Exception as e:
        log(f"ERROR sending iMessage: {e}")
        return False


def extract_multi_frames(video_path: str, count: int = 5) -> list[str]:
    """Extract evenly-spaced frames from a video for multi-image analysis."""
    frames = []
    frame_dir = Path(video_path).parent
    stem = Path(video_path).stem

    # Get video duration with ffprobe
    try:
        probe = subprocess.run(
            [FFMPEG.replace("ffmpeg", "ffprobe"), "-v", "error", "-show_entries",
             "format=duration", "-of", "csv=p=0", video_path],
            capture_output=True, timeout=10,
        )
        duration = float(probe.stdout.decode().strip())
    except Exception:
        duration = 18.0  # fallback estimate

    # Center frames in the middle of the clip where the action is.
    # For an 18s clip: start ~3s, end ~15s → frames at 3, 6, 9, 12, 15.
    # Scale proportionally for other durations, with 2s minimum margin.
    margin = max(2.0, duration / 6.0)
    start = min(margin, duration * 0.4)  # don't overshoot on very short clips
    end = max(duration - margin, duration * 0.6)
    interval = (end - start) / max(count - 1, 1)

    for i in range(count):
        ts = start + i * interval
        frame_path = str(frame_dir / f"{stem}-f{i}.jpg")
        result = subprocess.run(
            [FFMPEG, "-ss", f"{ts:.1f}", "-i", video_path, "-vframes", "1",
             "-q:v", "2", "-update", "1", frame_path, "-y"],
            capture_output=True, timeout=10,
        )
        if result.returncode == 0 and Path(frame_path).exists() and Path(frame_path).stat().st_size > 0:
            frames.append(frame_path)

    log(f"Extracted {len(frames)} frames from {duration:.0f}s video")
    return frames


def analyze_video(video_path: str, frame_count: int = 5) -> str | None:
    """Analyze a doorbell video by sending multiple frames to Claude vision."""
    try:
        if not OAUTH_CACHE.exists():
            log("No OAuth cache — skipping vision analysis")
            return None
        oauth = json.loads(OAUTH_CACHE.read_text()).get("claudeAiOauth", {})
        token = oauth.get("accessToken")
        if not token:
            log("No OAuth access token — skipping vision analysis")
            return None

        frames = extract_multi_frames(video_path, count=frame_count)
        if not frames:
            log("No frames extracted for vision analysis")
            return None

        import base64
        content = []
        for i, fp in enumerate(frames):
            with open(fp, "rb") as f:
                img_b64 = base64.standard_b64encode(f.read()).decode()
            content.append({"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": img_b64}})
            # Clean up frame after encoding
            Path(fp).unlink(missing_ok=True)

        content.append({"type": "text", "text": (
            f"These are {len(frames)} frames sampled evenly from an {len(frames)}-frame doorbell video clip. "
            + VISION_PROMPT
        )})

        payload = json.dumps({
            "model": VISION_MODEL,
            "max_tokens": 1024,
            "messages": [{"role": "user", "content": content}],
        }).encode()

        req = urllib.request.Request(
            "https://api.anthropic.com/v1/messages",
            data=payload,
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
                "anthropic-version": "2023-06-01",
                "anthropic-beta": "oauth-2025-04-20",
            },
            method="POST",
        )
        resp = urllib.request.urlopen(req, timeout=60)
        result = json.loads(resp.read().decode())
        text = result["content"][0]["text"].strip()
        # Strip markdown headers if Haiku adds them
        if text.startswith("#"):
            text = text.split("\n", 1)[-1].strip()
        return text
    except Exception as e:
        log(f"Vision analysis failed: {e}")
        return None


def parse_vision_result(text: str) -> dict | None:
    """Parse structured JSON from vision analysis."""
    try:
        # Strip markdown code fences if present
        cleaned = text.strip()
        if cleaned.startswith("```"):
            cleaned = cleaned.split("\n", 1)[-1]
            if cleaned.endswith("```"):
                cleaned = cleaned[:-3]
            cleaned = cleaned.strip()
        return json.loads(cleaned)
    except (json.JSONDecodeError, ValueError):
        log(f"Could not parse vision JSON: {text[:200]}")
        return None


def run_roomba_command(location: str, action: str) -> dict:
    """Start or dock Roombas for a location. Returns result dict."""
    now = time.time()
    cooldown_key = f"{location}_{action}"
    last = _roomba_last_action.get(cooldown_key, 0)
    if now - last < _ROOMBA_COOLDOWN:
        remaining = int((_ROOMBA_COOLDOWN - (now - last)) / 60)
        log(f"Roomba {action} for {location} on cooldown ({remaining}min remaining)")
        return {"success": False, "results": [], "skipped": "cooldown", "remaining_min": remaining}

    cmds = ROOMBA_COMMANDS.get(location, {})
    env = os.environ.copy()
    env["PATH"] = f"{OPENCLAW_BIN}:{env.get('PATH', '')}"
    results = []

    if location == "crosstown":
        cmd = cmds.get(action)
        if cmd:
            log(f"ROOMBA: {' '.join(cmd)}")
            try:
                r = subprocess.run(cmd, capture_output=True, timeout=30, env=env)
                output = r.stdout.decode()[:200]
                error = r.stderr.decode()[:200] if r.returncode != 0 else None
                log(f"ROOMBA result: {output}")
                if error:
                    log(f"ROOMBA error: {error}")
                results.append({"name": "crosstown-roomba", "command": f"{action} all",
                                "returncode": r.returncode, "output": output, "error": error})
            except Exception as e:
                log(f"ROOMBA error: {e}")
                results.append({"name": "crosstown-roomba", "command": f"{action} all",
                                "returncode": -1, "output": "", "error": str(e)})
        else:
            return {"success": False, "results": [], "skipped": "no_command"}
    elif location == "cabin":
        roomba_names = {"start_1": "floomba", "start_2": "philly",
                        "dock_1": "floomba", "dock_2": "philly"}
        for key in (f"{action}_1", f"{action}_2"):
            cmd = cmds.get(key)
            name = roomba_names.get(key, key)
            if cmd:
                log(f"ROOMBA: {' '.join(cmd)}")
                try:
                    r = subprocess.run(cmd, capture_output=True, timeout=30, env=env)
                    output = r.stdout.decode()[:200]
                    error = r.stderr.decode()[:200] if r.returncode != 0 else None
                    log(f"ROOMBA result: {output}")
                    if error:
                        log(f"ROOMBA error: {error}")
                    results.append({"name": name, "command": action,
                                    "returncode": r.returncode, "output": output, "error": error})
                except Exception as e:
                    log(f"ROOMBA error: {e}")
                    results.append({"name": name, "command": action,
                                    "returncode": -1, "output": "", "error": str(e)})
    else:
        return {"success": False, "results": [], "skipped": "no_command"}

    _roomba_last_action[cooldown_key] = now
    success = all(r["returncode"] == 0 for r in results) if results else False
    return {"success": success, "results": results}


def _check_network_presence_detailed(location: str) -> dict:
    """Check network presence and return per-person details.

    Returns: {"any_present": bool, "people": {"dylan": {"present": bool}, ...}}
    """
    try:
        if location == "crosstown":
            result = subprocess.run(
                ["ssh", "-o", "ConnectTimeout=5", "dylans-macbook-pro",
                 "~/.openclaw/workspace/scripts/presence-detect.sh", "crosstown"],
                capture_output=True, timeout=90, text=True,
            )
        elif location == "cabin":
            script = str(Path.home() / ".openclaw/workspace/scripts/presence-detect.sh")
            result = subprocess.run(
                [script, "cabin"],
                capture_output=True, timeout=60, text=True,
            )
        else:
            return {"any_present": False, "people": {}}

        if result.returncode != 0:
            log(f"NETWORK CHECK: {location} scan failed: {result.stderr[:200]}")
            return {"any_present": False, "people": {}}
        scan = json.loads(result.stdout)
        presence = scan.get("presence", {})
        people_detail = {}
        any_present = False
        for person, info in presence.items():
            present = info.get("present", False)
            people_detail[person] = {"present": present}
            if present:
                any_present = True
                log(f"NETWORK CHECK: {person} detected on {location} network")
        if not any_present:
            log(f"NETWORK CHECK: no one on {location} network")
        return {"any_present": any_present, "people": people_detail}
    except Exception as e:
        log(f"NETWORK CHECK: error: {e}")
        return {"any_present": False, "people": {}}


def _check_network_presence(location: str) -> bool:
    """Boolean wrapper for backward compatibility."""
    return _check_network_presence_detailed(location)["any_present"]


def _detect_who_left(location: str) -> list[str]:
    """Determine who left by running a network scan and checking who's absent.

    Returns list of people not detected on the network (likely on the walk).
    """
    try:
        if location == "crosstown":
            result = subprocess.run(
                ["ssh", "-o", "ConnectTimeout=5", "dylans-macbook-pro",
                 "~/.openclaw/workspace/scripts/presence-detect.sh", "crosstown"],
                capture_output=True, timeout=90, text=True,
            )
        elif location == "cabin":
            script = str(Path.home() / ".openclaw/workspace/scripts/presence-detect.sh")
            result = subprocess.run([script, "cabin"], capture_output=True, timeout=60, text=True)
        else:
            return ["dylan", "julia"]  # default: monitor both

        if result.returncode != 0:
            return ["dylan", "julia"]

        scan = json.loads(result.stdout)
        presence = scan.get("presence", {})
        absent = []
        for person_key, info in presence.items():
            if not info.get("present"):
                absent.append(person_key.lower())
        return absent if absent else ["dylan", "julia"]
    except Exception as e:
        log(f"WHO LEFT: error detecting: {e}")
        return ["dylan", "julia"]


def _haversine(lat1, lon1, lat2, lon2):
    """Distance in meters between two coordinates."""
    import math
    R = 6371000
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def _check_fi_gps(location: str) -> dict | None:
    """Check Potato's Fi GPS location via fi-collar status subprocess.

    Returns dict with at_monitored_location, distance, battery, etc. or None on error.
    """
    try:
        env = os.environ.copy()
        env["PATH"] = f"{OPENCLAW_BIN}:{env.get('PATH', '')}"
        r = subprocess.run(
            [f"{OPENCLAW_BIN}/fi-collar", "status"],
            capture_output=True, timeout=15, env=env, text=True,
        )
        lines = r.stdout.strip().split("\n")
        result = None
        for line in lines:
            try:
                parsed = json.loads(line)
                if parsed.get("name") == "Potato" or "latitude" in parsed:
                    result = parsed
                    break
            except json.JSONDecodeError:
                continue
        if not result or "latitude" not in result:
            log("FI GPS: no valid location in output")
            return None
        if result.get("error"):
            log(f"FI GPS: API error: {result.get('message', result['error'])}")
            return None
        # Check staleness
        last_report = result.get("connectionDate") or result.get("lastReport")
        if last_report:
            report_time = datetime.fromisoformat(last_report.replace("Z", "+00:00"))
            age_s = (datetime.now(timezone.utc) - report_time).total_seconds()
            if age_s > 600:  # > 10 minutes
                log(f"FI GPS: stale data ({int(age_s)}s old), ignoring")
                return None
            result["age_s"] = int(age_s)
        # Check against monitored location specifically
        loc = _FI_LOCATIONS.get(location)
        if loc:
            dist = _haversine(result["latitude"], result["longitude"], loc["lat"], loc["lon"])
            result["distance_to_monitored"] = round(dist)
            result["at_monitored_location"] = dist <= loc["radius_m"]
        else:
            log(f"FI GPS: no geofence configured for {location}")
            return None
        battery = result.get("battery")
        if battery is not None and battery < 10:
            log(f"FI GPS: low battery warning ({battery}%)")
        return result
    except Exception as e:
        log(f"FI GPS: error: {e}")
        return None


async def _return_poll_loop(location: str) -> None:
    """Poll network presence + Fi GPS to detect return home.

    Checks every 60 seconds:
    1. Ring motion (event-driven flag) → immediate dock
    2. Network WiFi presence → dock
    3. Fi GPS geofence (Potato's collar) → dock

    Safety timeout: 2 hours.

    IMPORTANT: This function MUST reset _return_monitor_active on ALL exit
    paths. If the flag stays True after the loop ends, all subsequent motion
    events are misclassified as "return signals" instead of new departures.
    """
    global _return_monitor_active, _ring_motion_during_walk
    POLL_INTERVAL = 60       # 1 minute between checks
    MAX_DURATION = 7200      # 2 hours total timeout
    start_time = time.time()

    try:
        addr = HOME_ADDRESSES.get(location, HOME_ADDRESSES["crosstown"])
        log(f"RETURN MONITOR: Starting for {location} ({addr['street']})")
        send_imessage(f"\U0001f4cd Tracking your walk — will dock Roombas when you're back near {addr['street']}")

        # Wait 2 minutes then detect who left the network
        await asyncio.sleep(120)
        walkers = await asyncio.to_thread(_detect_who_left, location)
        log(f"RETURN MONITOR: Walkers detected: {walkers}")
        _update_state_dog_walk(location, "walkers_detected", walkers=walkers)

        _ring_motion_during_walk = False

        while time.time() - start_time < MAX_DURATION:
            elapsed = time.time() - start_time
            try:
                # 1. Ring motion event check (set by _handle_motion when monitor is active)
                if _ring_motion_during_walk:
                    _ring_motion_during_walk = False
                    elapsed_min = int(elapsed / 60)
                    log(f"RETURN MONITOR: Ring motion after {elapsed_min}min — docking at {location}")
                    roomba_result = run_roomba_command(location, "dock")
                    _update_state_dog_walk(location, "dock", return_signal="ring_motion", roomba_result=roomba_result)
                    _update_state_return_monitor(location, "stop")
                    return

                # 2. Network presence check
                wifi_detail = await asyncio.to_thread(_check_network_presence_detailed, location)
                _update_state_return_monitor(location, "poll", network_detail=wifi_detail)
                if wifi_detail["any_present"]:
                    elapsed_min = int(elapsed / 60)
                    log(f"RETURN MONITOR: Network return after {elapsed_min}min — docking at {location}")
                    roomba_result = run_roomba_command(location, "dock")
                    _update_state_dog_walk(location, "dock", return_signal="network_wifi", roomba_result=roomba_result)
                    _update_state_return_monitor(location, "stop")
                    return

                # 3. Fi GPS check — Potato's collar geofence
                fi_result = await asyncio.to_thread(_check_fi_gps, location)
                if fi_result:
                    _update_state_return_monitor(location, "poll", fi_result=fi_result)
                    if fi_result.get("at_monitored_location"):
                        elapsed_min = int(elapsed / 60)
                        dist = fi_result.get("distance_to_monitored", "?")
                        log(f"RETURN MONITOR: Fi GPS shows Potato {dist}m from {location} after {elapsed_min}min — docking")
                        roomba_result = run_roomba_command(location, "dock")
                        _update_state_dog_walk(location, "dock", return_signal="fi_gps", roomba_result=roomba_result)
                        _update_state_return_monitor(location, "stop")
                        return
                    else:
                        dist = fi_result.get("distance_to_monitored", "?")
                        log(f"RETURN MONITOR: Fi GPS — Potato {dist}m from {location} (outside geofence)")

            except asyncio.CancelledError:
                log("RETURN MONITOR: Cancelled")
                return
            except Exception as e:
                log(f"RETURN MONITOR: Error: {e}")

            await asyncio.sleep(POLL_INTERVAL)

        log(f"RETURN MONITOR: Timeout after {MAX_DURATION // 60}min — docking as safety fallback")
        send_imessage(f"\u23f0 Walk tracking timed out after 2 hours — docking Roombas at {location}.")
        roomba_result = run_roomba_command(location, "dock")
        _update_state_dog_walk(location, "dock_timeout", return_signal="timeout", roomba_result=roomba_result)
        _update_state_return_monitor(location, "stop")
    finally:
        _return_monitor_active = False
        _ring_motion_during_walk = False
        log("RETURN MONITOR: Ended — cleared _return_monitor_active flag")


async def _inbox_poll_loop() -> None:
    """Watch inbox directory for return-monitor requests from external processes."""
    _INBOX_DIR.mkdir(parents=True, exist_ok=True)
    while True:
        try:
            for fpath in _INBOX_DIR.iterdir():
                if not fpath.name.endswith(".json"):
                    continue
                try:
                    data = json.loads(fpath.read_text())
                    fpath.unlink()
                except (json.JSONDecodeError, OSError) as e:
                    log(f"INBOX: bad file {fpath.name}: {e}")
                    fpath.unlink(missing_ok=True)
                    continue

                location = data.get("location")
                requested_at = data.get("requested_at", "")
                if not location:
                    log(f"INBOX: missing location in {fpath.name}")
                    continue

                # Ignore stale requests (>2 hours old)
                try:
                    req_dt = datetime.fromisoformat(requested_at.replace("Z", "+00:00"))
                    age = (datetime.now(req_dt.tzinfo) - req_dt).total_seconds()
                    if age > _INBOX_MAX_AGE:
                        log(f"INBOX: ignoring stale request for {location} (age={int(age)}s)")
                        continue
                except (ValueError, AttributeError):
                    pass  # no timestamp or bad format — process anyway

                if _return_monitor_active:
                    log(f"INBOX: return monitor already active, ignoring request for {location}")
                    continue

                log(f"INBOX: starting return monitor for {location}")
                # Synthesize departure event so walk lifecycle is trackable in JSONL
                _update_state_dog_walk(
                    location, "departure",
                    people=0, dogs=0,  # unknown — manual trigger via dog-walk-start
                    roomba_result={"success": True, "results": [], "source": "dog-walk-start"},
                )
                _clear_pending_confirmation("inbox IPC")
                start_return_monitor(location)
        except Exception as e:
            log(f"INBOX: error: {e}")

        await asyncio.sleep(_INBOX_POLL_INTERVAL)


def _clear_pending_confirmation(reason: str) -> None:
    """Clear any pending confirmation state (called from all start paths)."""
    global _pending_confirmation
    if _pending_confirmation:
        log(f"CONFIRM: cleared pending ({reason})")
        _pending_confirmation = None


async def _confirmation_poll_loop() -> None:
    """Poll BB for Dylan's 'start roombas' reply after a PARTIAL DEPARTURE prompt.

    Uses POST /api/v1/message/query with an 'after' timestamp to find replies
    sent after the confirmation prompt. On match, starts Roombas + return monitor
    directly, bypassing the OpenClaw agent.
    """
    while True:
        try:
            if _pending_confirmation is None:
                await asyncio.sleep(_CONFIRMATION_POLL_INTERVAL)
                continue

            location = _pending_confirmation["location"]
            sent_at_ms = _pending_confirmation["sent_at_ms"]

            # Timeout check
            age_s = (time.time() * 1000 - sent_at_ms) / 1000
            if age_s > _CONFIRMATION_TIMEOUT:
                log(f"CONFIRM: timed out after {int(age_s)}s for {location}")
                _clear_pending_confirmation("timeout")
                await asyncio.sleep(_CONFIRMATION_POLL_INTERVAL)
                continue

            # Already started via another path (inbox IPC, 2-dog auto, etc.)
            if _return_monitor_active:
                _clear_pending_confirmation("monitor already active")
                await asyncio.sleep(_CONFIRMATION_POLL_INTERVAL)
                continue

            pw = bb_password()
            if not pw:
                await asyncio.sleep(_CONFIRMATION_POLL_INTERVAL)
                continue

            # Query BB for recent messages after our prompt
            url = f"{BB_URL}/api/v1/message/query?password={pw}"
            body = json.dumps({
                "limit": 10,
                "sort": "DESC",
                "after": sent_at_ms,
            }).encode()
            try:
                async with aiohttp.ClientSession() as session:
                    async with session.post(
                        url,
                        data=body,
                        headers={"Content-Type": "application/json"},
                        timeout=aiohttp.ClientTimeout(total=10),
                    ) as resp:
                        result = await resp.json()
            except (aiohttp.ClientError, asyncio.TimeoutError) as e:
                log(f"CONFIRM: BB query failed: {e}")
                await asyncio.sleep(_CONFIRMATION_POLL_INTERVAL)
                continue

            for msg in result.get("data", []):
                # Skip our own outbound messages
                if msg.get("isFromMe"):
                    continue
                # Only messages in Dylan's chat
                chat_guids = [c.get("guid", "") for c in msg.get("chats", [])]
                if DYLAN_CHAT not in chat_guids:
                    continue
                text = (msg.get("text") or "").strip().casefold()
                if text == "start roombas":
                    log(f"CONFIRM: Dylan replied 'start roombas' for {location} — starting")
                    _clear_pending_confirmation("reply received")
                    roomba_result = run_roomba_command(location, "start")
                    _update_state_dog_walk(location, "departure", people=1, dogs=1, roomba_result=roomba_result)
                    start_return_monitor(location)
                    send_imessage(
                        f"\U0001f9f9 Both running at {location} — I'll dock them when you're back"
                    )
                    break

        except Exception as e:
            log(f"CONFIRM: error: {e}")

        await asyncio.sleep(_CONFIRMATION_POLL_INTERVAL)


def start_return_monitor(location: str) -> None:
    """Start return-home monitoring (network + Fi GPS + Ring motion)."""
    global _return_poll_task, _return_monitor_active
    if _return_poll_task and not _return_poll_task.done():
        _return_poll_task.cancel()
    _return_monitor_active = True
    _update_state_return_monitor(location, "start")
    _return_poll_task = asyncio.get_event_loop().create_task(_return_poll_loop(location))


def stop_return_monitor() -> None:
    """Stop return-home monitoring."""
    global _return_poll_task, _return_monitor_active, _ring_motion_during_walk
    _return_monitor_active = False
    _ring_motion_during_walk = False
    if _return_poll_task and not _return_poll_task.done():
        _return_poll_task.cancel()
        _return_poll_task = None
    _update_state_return_monitor("", "stop")


# Dog walk hours (local time) — automation only active during these windows
_WALK_HOURS = [(8, 10), (11, 13), (17, 20)]  # 8-10 AM, 11 AM-1 PM, 5-8 PM

# Presence state file
_PRESENCE_STATE = Path.home() / ".openclaw/presence/state.json"


def _is_walk_hour() -> bool:
    """Check if current local time is within typical dog walk hours."""
    hour = datetime.now().hour
    return any(start <= hour < end for start, end in _WALK_HOURS)


def _current_walk_window() -> int | None:
    """Return the start hour of the current walk window, or None if outside walk hours."""
    hour = datetime.now().hour
    for start, end in _WALK_HOURS:
        if start <= hour < end:
            return start
    return None


def _is_location_occupied(location: str) -> bool:
    """Check presence state — returns True if location is occupied or state unknown."""
    try:
        if not _PRESENCE_STATE.exists():
            return True  # assume occupied if no state file
        state = json.loads(_PRESENCE_STATE.read_text())
        loc_state = state.get(location, {})
        occupancy = loc_state.get("occupancy", "occupied")
        return occupancy != "confirmed_vacant"
    except Exception:
        return True  # assume occupied on error


def _get_current_location() -> str | None:
    """Determine which location (cabin/crosstown) we're at based on presence state."""
    try:
        if not _PRESENCE_STATE.exists():
            return None
        state = json.loads(_PRESENCE_STATE.read_text())
        for loc in ("cabin", "crosstown"):
            loc_state = state.get(loc, {})
            if loc_state.get("occupancy") in ("occupied", "possibly_occupied"):
                return loc
        return None
    except Exception:
        return None


async def _fi_departure_poll_loop() -> None:
    """Poll Fi GPS every 3 minutes to detect dog walk departures independently of Ring.

    Triggers a departure when Potato leaves the geofence at the current location:
    - 2 consecutive readings outside geofence, ≥3 min apart
    - Both readings must be < 10 min old (not stale)
    - Only during walk hours and when location is occupied
    - Only when no walk is already active
    """
    FI_POLL_INTERVAL = 180  # 3 minutes
    last_outside_reading = None  # (timestamp, location, distance)

    log("FI DEPARTURE: Polling loop started (every 3 min during walk hours)")

    while True:
        try:
            await asyncio.sleep(FI_POLL_INTERVAL)

            # Skip if not walk hours
            if not _is_walk_hour():
                last_outside_reading = None
                continue

            # Skip if a walk is already active
            if _return_monitor_active:
                last_outside_reading = None
                continue

            # Determine current location
            location = _get_current_location()
            if not location:
                last_outside_reading = None
                continue

            # Skip if location is vacant
            if not _is_location_occupied(location):
                last_outside_reading = None
                continue

            # Check Fi GPS
            fi_result = await asyncio.to_thread(_check_fi_gps, location)
            if not fi_result:
                continue  # API error or stale data — don't reset, just skip

            if fi_result.get("at_monitored_location"):
                # Potato is home — reset departure tracking
                last_outside_reading = None
                continue

            # Potato is outside geofence
            dist = fi_result.get("distance_to_monitored", 0)
            now = time.time()

            if last_outside_reading is None or last_outside_reading[1] != location:
                # First outside reading at this location
                last_outside_reading = (now, location, dist)
                log(f"FI DEPARTURE: Potato {dist}m from {location} (first reading, need confirmation)")
                continue

            # Check if enough time has passed since first reading (≥3 min)
            time_since_first = now - last_outside_reading[0]
            if time_since_first < 180:
                log(f"FI DEPARTURE: Potato {dist}m from {location} (confirming, {int(time_since_first)}s since first)")
                continue

            # Confirmed departure — 2 readings outside geofence, ≥3 min apart
            log(f"FI DEPARTURE: Confirmed! Potato {dist}m from {location} "
                f"(first reading {int(time_since_first)}s ago at {last_outside_reading[2]}m)")
            last_outside_reading = None

            # Start Roombas and return monitor
            send_imessage(
                f"\U0001f9f9 Potato left {location} (GPS: {dist}m away) — starting Roombas!"
            )
            roomba_result = run_roomba_command(location, "start")
            _update_state_dog_walk(location, "departure", people=0, dogs=1, roomba_result=roomba_result)
            start_return_monitor(location)

        except asyncio.CancelledError:
            log("FI DEPARTURE: Polling loop cancelled")
            return
        except Exception as e:
            log(f"FI DEPARTURE: Error: {e}")
            await asyncio.sleep(30)


async def check_departure(vision_data: dict, doorbot_id: int) -> None:
    """Accumulate departing people/dogs across recent events and trigger Roombas.

    People and dogs often pass the doorbell in separate motion events during a
    single departure. This function accumulates sightings within a 10-minute
    sliding window. Return detection is handled by Fi GPS + network presence, not Ring.

    Pre-checks:
    - Time-of-day filter: only active 6-9 AM and 3-9 PM
    - Presence cross-check: skip if location already confirmed_vacant

    Trigger conditions:
    - 1+ people AND 2+ dogs departing → auto-start Roombas + begin FindMy polling
    - 1+ people AND 1 dog departing → ask Dylan via iMessage for confirmation
    """
    location = DOORBELL_LOCATIONS.get(doorbot_id)
    if not location:
        return

    # Time-of-day filter
    if not _is_walk_hour():
        hour = datetime.now().hour
        log(f"DEPARTURE SKIP: outside walk hours (hour={hour})")
        _emit_skip_event(location, "outside_walk_hours", {"hour": hour})
        return

    # Presence cross-check — if already vacant, no one is home to leave
    if not _is_location_occupied(location):
        log(f"DEPARTURE SKIP: {location} already confirmed_vacant")
        _emit_skip_event(location, "confirmed_vacant")
        return

    people = vision_data.get("people", [])
    dogs = vision_data.get("dogs", [])

    # Direction filter removed — Haiku struggles with fisheye distortion
    # (arrivals frequently misclassified as departures). Time-of-day filter,
    # presence cross-check, WiFi check, and cooldown prevent false positives.

    now = time.time()

    # Record this sighting
    _departure_sightings.append({
        "time": now,
        "people": len(people),
        "dogs": len(dogs),
        "location": location,
    })

    # Prune old sightings outside the window
    cutoff = now - _DEPARTURE_WINDOW
    _departure_sightings[:] = [s for s in _departure_sightings if s["time"] >= cutoff]

    # Accumulate counts at this location within the window.
    # Use max() for both people and dogs — the same dog seen across
    # multiple motion events should not be double-counted.
    max_people = 0
    max_dogs = 0
    for s in _departure_sightings:
        if s["location"] == location:
            max_people = max(max_people, s["people"])
            max_dogs = max(max_dogs, s["dogs"])

    log(f"ACCUMULATOR: location={location} "
        f"people_max={max_people} dogs_max={max_dogs} "
        f"window_events={sum(1 for s in _departure_sightings if s['location'] == location)}")

    if max_people < 1 or max_dogs < 1:
        return

    # WiFi check at decision time — by now the accumulation window has elapsed,
    # giving phones time to drop off WiFi during a real departure (~30-60s).
    # If ANY phone is still on WiFi, someone is home (or returning) — suppress.
    # This runs after accumulation so a real departure has time to clear WiFi,
    # and prevents false prompts when returning from a walk triggers motion.
    wifi_detail = await asyncio.to_thread(_check_network_presence_detailed, location)
    if wifi_detail["any_present"]:
        log(f"DEPARTURE SKIP: phone still on {location} WiFi at decision time — likely home or returning")
        _emit_skip_event(location, "wifi_present", {"wifi": wifi_detail["people"]})
        return

    # Clear sightings to prevent re-triggering
    _departure_sightings[:] = [
        s for s in _departure_sightings
        if not (s["location"] == location)
    ]

    total_people = max_people
    total_dogs = max_dogs

    if total_dogs >= 2:
        # Full household departure — auto-trigger
        log(f"DEPARTURE DETECTED at {location}: {total_people} people + {total_dogs} dogs leaving!")
        _clear_pending_confirmation("2-dog auto-start")
        send_imessage(f"\U0001f9f9 Starting Roombas at {location} — everyone left for a walk!")
        roomba_result = run_roomba_command(location, "start")
        _update_state_dog_walk(location, "departure", people=total_people, dogs=total_dogs, roomba_result=roomba_result)
        start_return_monitor(location)
    else:
        # Only 1 dog seen — ask for confirmation
        log(f"PARTIAL DEPARTURE at {location}: {total_people} people + 1 dog — asking for confirmation")

        # Cabin-specific: suppress repeat prompts within the same walk window.
        # Once prompted in e.g. 8-10 AM, don't ask again until the 11-1 PM window.
        if location == "cabin":
            window = _current_walk_window()
            prompt_key = (location, window)
            if _cabin_prompt_sent.get(prompt_key):
                log(f"CABIN PROMPT SUPPRESSED: already prompted in window {window}-{window+2 if window else '?'}")
                _emit_skip_event(location, "cabin_prompt_suppressed", {"window": window})
                return
            _cabin_prompt_sent[prompt_key] = True

        if location == "crosstown":
            sent = send_imessage(
                f"\U0001f436 Ring saw {total_people} {'person' if total_people == 1 else 'people'} "
                f"and 1 dog leaving. Want me to start the Roombas? "
                f"Reply \"start roombas\" and I'll start them + auto-dock when you're back"
            )
        elif location == "cabin":
            sent = send_imessage(
                f"\U0001f436 Ring saw {total_people} {'person' if total_people == 1 else 'people'} "
                f"and 1 dog leaving at cabin. Want me to start the Roombas? "
                f"Reply \"start roombas\" and I'll start them + auto-dock when you're back"
            )
        else:
            sent = False

        if sent:
            global _pending_confirmation
            _pending_confirmation = {
                "location": location,
                "sent_at_ms": int(time.time() * 1000),
            }
            log(f"CONFIRM: armed pending confirmation for {location}")


def send_imessage_image(image_path: str, caption: str = "") -> bool:
    """Send an image attachment via BlueBubbles API."""
    pw = bb_password()
    if not pw:
        log("ERROR: BLUEBUBBLES_PASSWORD not set")
        return False
    if not Path(image_path).exists():
        log(f"ERROR: Image not found: {image_path}")
        return False
    try:
        # BB attachment API requires multipart with 'name' field
        import requests
        url = f"{BB_URL}/api/v1/message/attachment?password={pw}"
        files = {"attachment": (Path(image_path).name, open(image_path, "rb"), "image/jpeg")}
        data = {
            "chatGuid": DYLAN_CHAT,
            "tempGuid": str(uuid.uuid4()).upper(),
            "method": "private-api",
            "name": Path(image_path).name,
        }
        r = requests.post(url, files=files, data=data, timeout=15)
        result = r.json()
        if result.get("status") == 200:
            # Send caption as follow-up text
            if caption:
                send_imessage(caption)
            return True
        else:
            log(f"ERROR sending image: {result.get('message')}")
            return False
    except Exception as e:
        log(f"ERROR sending image: {e}")
        return False


async def download_recording(db, event_id: int) -> tuple[str | None, str | None]:
    """Download the recording for an event and extract a preview frame.

    Returns (mp4_path, frame_path). Either may be None.
    Retries up to 3 times with increasing delays since Ring needs time
    to process and upload the recording after an event.
    """
    FRAME_DIR.mkdir(parents=True, exist_ok=True)

    mp4_path = str(FRAME_DIR / f"event-{event_id}.mp4")
    frame_path = str(FRAME_DIR / f"event-{event_id}.jpg")

    delays = [0, 10, 15]  # seconds between retries
    for attempt, delay in enumerate(delays):
        if delay > 0:
            log(f"Recording not ready, retrying in {delay}s (attempt {attempt + 1}/{len(delays)})")
            await asyncio.sleep(delay)
        try:
            url = await db.async_recording_url(event_id)
            if not url:
                log(f"No video URL for event {event_id} (attempt {attempt + 1})")
                continue

            async with aiohttp.ClientSession() as session:
                async with session.get(url, timeout=aiohttp.ClientTimeout(total=30)) as resp:
                    if resp.status == 404:
                        # Recording not yet available — retry
                        continue
                    if resp.status != 200:
                        log(f"Video download failed: HTTP {resp.status}")
                        return None, None
                    async with aiofiles.open(mp4_path, "wb") as f:
                        async for chunk in resp.content.iter_chunked(8192):
                            await f.write(chunk)

            # Extract a preview frame ~3 seconds in (skip pre-roll buffer)
            result = subprocess.run(
                [FFMPEG, "-ss", "3", "-i", mp4_path, "-vframes", "1", "-q:v", "2",
                 "-update", "1", frame_path, "-y"],
                capture_output=True, timeout=10,
            )
            if result.returncode != 0:
                log(f"ffmpeg frame extraction failed: {result.stderr.decode()[:200]}")
                frame_path = None

            if frame_path and (not Path(frame_path).exists() or Path(frame_path).stat().st_size == 0):
                frame_path = None

            return mp4_path, frame_path

        except Exception as e:
            if attempt < len(delays) - 1:
                log(f"Download attempt {attempt + 1} failed: {e}")
                continue
            log(f"ERROR downloading recording: {e}")
            return None, None

    log(f"Recording not available after {len(delays)} attempts for event {event_id}")
    return None, None


def on_event(event: RingEvent) -> None:
    """Handle incoming Ring event from FCM push."""
    now = time.time()

    # Clean old dedup entries
    expired = [eid for eid, ts in _recent_events.items() if now - ts > _DEDUP_WINDOW]
    for eid in expired:
        del _recent_events[eid]

    # Skip duplicate/update events
    if event.is_update:
        return
    if event.id in _recent_events:
        return
    _recent_events[event.id] = now

    kind = event.kind  # "ding", "motion", "on_demand"
    device = event.device_name
    doorbot_id = event.doorbot_id

    log(f"Event: kind={kind} device={device} doorbot_id={doorbot_id} state={event.state}")

    if kind == "ding":
        loop = asyncio.get_event_loop()
        loop.create_task(_handle_ding(device, doorbot_id, event.id))

    elif kind == "motion":
        loop = asyncio.get_event_loop()
        loop.create_task(_handle_motion(device, doorbot_id, event.id, state=event.state or ""))

    # Skip on_demand and other event types


async def _handle_ding(device: str, doorbot_id: int, event_id: int) -> None:
    """Handle doorbell ring — always notify with image if available."""
    # Send immediate text notification
    msg = f"\U0001f514 {device}: Doorbell rang!"
    log(f"NOTIFY: {msg}")
    send_imessage(msg)

    # Try to grab a frame (recording may take a few seconds)
    await _send_event_recording(device, doorbot_id, event_id)


async def _handle_motion(device: str, doorbot_id: int, event_id: int, state: str = "") -> None:
    """Handle motion — check for person detection, then notify with image."""
    try:
        # FCM event state "human" already means person detected — trust it
        person_detected = state.lower() == "human"

        # If return monitor is active and we see a person, signal return
        if person_detected and _return_monitor_active:
            global _ring_motion_during_walk
            _ring_motion_during_walk = True
            log(f"RING MOTION during walk monitoring — signaling return")

        global _ring
        if _ring is None:
            return

        devices = _ring.devices()
        doorbells = list(devices.doorbots) + list(devices.authorized_doorbots)
        db = None
        for d in doorbells:
            if d.id == doorbot_id:
                db = d
                break

        if db is None:
            log(f"WARNING: doorbot_id={doorbot_id} not found")
            return

        # No-subscription doorbells can't distinguish person vs generic motion —
        # treat all motion as potential person for departure detection
        if not db.has_subscription and not person_detected:
            log(f"{device} has no Ring Protect — treating motion as person for departure check")
            person_detected = True

        # If FCM didn't flag person, fall back to history API check
        if not person_detected:
            await asyncio.sleep(5)
            history = await db.async_history(limit=5)
            for h in history:
                if h.get("id") == event_id:
                    cv = h.get("cv_properties") or {}
                    person_detected = cv.get("person_detected", False)
                    break

        if person_detected:
            log(f"Person detected on {device} — processing recording")

            # Process recording for departure automation (no iMessage notification)
            await _send_event_recording(device, doorbot_id, event_id, db=db, notify=False)
        else:
            log(f"Motion on {device} — no person detected, skipping")

    except Exception as e:
        log(f"ERROR handling motion: {e}")


async def _send_event_recording(device: str, doorbot_id: int, event_id: int, db=None, notify: bool = True) -> None:
    """Download recording, analyze video with Claude vision, optionally send frame + description."""
    try:
        # Wait for recording to be ready
        await asyncio.sleep(8)

        global _ring
        if db is None and _ring is not None:
            devices = _ring.devices()
            doorbells = list(devices.doorbots) + list(devices.authorized_doorbots)
            for d in doorbells:
                if d.id == doorbot_id:
                    db = d
                    break

        if db is None:
            log(f"Cannot find doorbell {doorbot_id} for recording")
            return

        if not db.has_subscription:
            log(f"{device} has no Ring Protect — skipping recording, running departure check with assumed dog")
            # No video analysis possible — we know a person was detected (FCM told us).
            # Assume at least 1 dog to trigger the confirmation prompt at cabin.
            await check_departure({"people": ["unknown"], "dogs": ["unknown"]}, doorbot_id)
            return

        mp4_path, frame_path = await download_recording(db, event_id)
        if not mp4_path:
            log(f"Could not download recording for event {event_id}")
            return

        # Analyze the full video clip with Claude vision (run in thread to avoid blocking event loop)
        description = ""
        raw_analysis = await asyncio.to_thread(analyze_video, mp4_path)
        if raw_analysis:
            log(f"Vision raw: {raw_analysis}")
            vision_data = parse_vision_result(raw_analysis)
            if vision_data:
                description = vision_data.get("description", "")

                # If we see people but only 1 dog, retry with more frames
                # to catch the second dog that may appear briefly
                people = vision_data.get("people", [])
                dogs = vision_data.get("dogs", [])
                if len(people) >= 1 and len(dogs) == 1:
                    log(f"Only 1 dog in 5 frames — retrying with 10 frames for better coverage")
                    retry_analysis = await asyncio.to_thread(analyze_video, mp4_path, 10)
                    if retry_analysis:
                        log(f"Vision retry raw: {retry_analysis}")
                        retry_data = parse_vision_result(retry_analysis)
                        if retry_data and len(retry_data.get("dogs", [])) > len(dogs):
                            log(f"Retry found more dogs: {len(retry_data.get('dogs', []))} vs {len(dogs)}")
                            vision_data = retry_data
                            description = retry_data.get("description", description)

                # Record vision result and check for departure automation
                _update_state_vision(vision_data, event_id=event_id)
                await check_departure(vision_data, doorbot_id)
            else:
                # Fallback: use raw text as description
                description = raw_analysis
        else:
            # Vision failed (401, timeout, etc.) — still run departure check
            # with degraded data. FCM told us a person was detected; assume 1 dog
            # so the confirmation prompt fires instead of silently doing nothing.
            log("Vision unavailable — running departure check with FCM-only data (person + assumed dog)")
            await check_departure({"people": ["unknown"], "dogs": ["unknown"]}, doorbot_id)

        # Send preview frame as iMessage image, then description as caption
        if notify:
            if frame_path:
                log(f"Sending frame: {frame_path}")
                await asyncio.to_thread(send_imessage_image, frame_path, description)
            elif description:
                # No frame but have description — send as text
                send_imessage(description)

        if frame_path:
            Path(frame_path).unlink(missing_ok=True)

        # Clean up MP4
        Path(mp4_path).unlink(missing_ok=True)

    except Exception as e:
        log(f"ERROR processing recording: {e}")


# Global ring instance for async lookups
_ring: Ring | None = None


async def main() -> None:
    global _ring

    log("Ring event listener starting...")

    # Auth
    token = load_ring_token()
    if not token:
        log("ERROR: No Ring token cache. Run 'ring status' first to authenticate.")
        sys.exit(1)

    auth = Auth(USER_AGENT, token=token, token_updater=save_ring_token)
    ring = Ring(auth)

    try:
        await ring.async_create_session()
    except Exception as e:
        log(f"ERROR: Ring session creation failed: {e}")
        sys.exit(1)

    await ring.async_update_data()
    _ring = ring

    # List devices
    devices = ring.devices()
    doorbells = list(devices.doorbots) + list(devices.authorized_doorbots)
    log(f"Monitoring {len(doorbells)} doorbell(s): {', '.join(db.name + ' (id=' + str(db.id) + ')' for db in doorbells)}")

    # FCM credentials
    fcm_creds = load_fcm_credentials()

    # Event listener — disable permanent abort on sequential errors so transient
    # network outages don't kill the push receiver permanently
    listener_config = RingEventListenerConfig.default_config()
    listener_config.abort_on_sequential_error_count = None

    listener = RingEventListener(ring, credentials=fcm_creds,
                                 credentials_updated_callback=save_fcm_credentials,
                                 config=listener_config)
    listener.add_notification_callback(on_event)

    started = await listener.start(timeout=30)
    if not started:
        log("ERROR: Failed to start event listener (FCM registration failed)")
        sys.exit(1)

    log("Event listener started — waiting for Ring events...")

    # Start inbox polling for external return-monitor requests
    asyncio.get_event_loop().create_task(_inbox_poll_loop())

    # Start BB reply polling for dog walk confirmation
    asyncio.get_event_loop().create_task(_confirmation_poll_loop())

    # Start Fi GPS departure detection (works independently of Ring)
    asyncio.get_event_loop().create_task(_fi_departure_poll_loop())

    # Keep running with watchdog — restart listener if FCM push receiver dies
    try:
        while True:
            await asyncio.sleep(300)  # check every 5 min
            if not listener.started:
                log("WARNING: Event listener died — attempting restart...")
                try:
                    fcm_creds = load_fcm_credentials()
                    listener = RingEventListener(ring, credentials=fcm_creds,
                                                 credentials_updated_callback=save_fcm_credentials,
                                                 config=listener_config)
                    listener.add_notification_callback(on_event)
                    restarted = await listener.start(timeout=30)
                    if restarted:
                        log("Event listener restarted successfully")
                    else:
                        log("ERROR: Event listener restart failed — will retry in 5 min")
                except Exception as e:
                    log(f"ERROR: Event listener restart exception: {e} — will retry in 5 min")
            elif int(time.time()) % 3600 < 300:
                log("Heartbeat — listener still running")
    except asyncio.CancelledError:
        pass
    finally:
        await listener.stop()
        log("Event listener stopped")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        log("Interrupted — shutting down")
    except Exception as e:
        log(f"FATAL: {e}")
        sys.exit(1)
