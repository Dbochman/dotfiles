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
import time
import urllib.request
import urllib.error
import uuid
from datetime import datetime
from pathlib import Path

# Use venv packages
VENV_SITE = Path.home() / ".openclaw/ring/venv/lib"
for p in VENV_SITE.glob("python*/site-packages"):
    sys.path.insert(0, str(p))

import aiohttp
import aiofiles

from ring_doorbell import Auth, Ring, RingEvent, RingEventListener

# Config
CONFIG_DIR = Path.home() / ".config" / "ring"
TOKEN_FILE = CONFIG_DIR / "token-cache.json"
FCM_CREDS_FILE = Path.home() / ".openclaw/ring-listener/fcm-credentials.json"
FRAME_DIR = Path.home() / ".openclaw/ring-listener/frames"
LOG_FILE = "/tmp/ring-listener.log"

BB_URL = "http://localhost:1234"
DYLAN_CHAT = "any;-;dylanbochman@gmail.com"
USER_AGENT = "OpenClaw/1.0"
FFMPEG = "/opt/homebrew/bin/ffmpeg"
OAUTH_CACHE = Path.home() / ".openclaw/.anthropic-oauth-cache"
VISION_MODEL = "claude-haiku-4-5-20251001"

VISION_PROMPT = (
    "Analyze this front door camera image. Respond with ONLY valid JSON (no markdown, no ```), "
    "using this exact schema:\n"
    '{"description":"<1 sentence describing the scene>",'
    '"people":["<name or unknown>"],'
    '"dogs":["<name or unknown>"],'
    '"direction":"<arriving|departing|unclear>"}\n\n'
    "Known residents: Dylan (man), Julia (woman with long brown hair). "
    "Known dogs: large brown/gold dog with dark black face; Coconut (white and pink pitbull). "
    "Use names when you recognize them. 'direction' means whether people are walking "
    "TOWARD the door (arriving) or AWAY from the door toward the street (departing). "
    "If no people visible, use empty lists."
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
PEEKABOO = "/opt/homebrew/bin/peekaboo"
FINDMY_CAPTURE_DIR = Path.home() / ".openclaw/ring-listener/findmy"

# Home coordinates for reference (Crosstown Ave, West Roxbury)
HOME_STREET = "Crosstown"

FINDMY_PROMPT = (
    "Look at this FindMy app screenshot. There is a pin on the map showing a person's location. "
    "Respond with ONLY valid JSON (no markdown, no ```), using this exact schema:\n"
    '{"street":"<street name the pin is on or nearest to, or unknown>",'
    '"near_home":true/false,'
    '"description":"<1 sentence describing where the pin is on the map>"}\n\n'
    "The person's home is on Crosstown Ave in West Roxbury/Boston. "
    "'near_home' should be true if the pin appears to be ON Crosstown Ave or within ~1 block of it. "
    "Look at the street labels on the map to determine the pin's location."
)

# Dedup: track recent event IDs to avoid double-notify
_recent_events: dict[int, float] = {}
_DEDUP_WINDOW = 300  # 5 minutes

# Roomba cooldown: prevent re-triggering within 2 hours per location
_roomba_last_action: dict[str, float] = {}
_ROOMBA_COOLDOWN = 7200  # 2 hours

# FindMy polling state
_findmy_poll_task: asyncio.Task | None = None

# Departure accumulator: track people/dogs across recent events within a window
_DEPARTURE_WINDOW = 600  # 10 minutes
_departure_sightings: list[dict] = []  # [{"time": float, "people": int, "dogs": int, "direction": str, "location": str}]


def log(msg: str) -> None:
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{ts}] {msg}\n"
    sys.stdout.write(line)
    sys.stdout.flush()


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
            "max_tokens": 200,
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


def run_roomba_command(location: str, action: str) -> None:
    """Start or dock Roombas for a location."""
    now = time.time()
    cooldown_key = f"{location}_{action}"
    last = _roomba_last_action.get(cooldown_key, 0)
    if now - last < _ROOMBA_COOLDOWN:
        remaining = int((_ROOMBA_COOLDOWN - (now - last)) / 60)
        log(f"Roomba {action} for {location} on cooldown ({remaining}min remaining)")
        return

    cmds = ROOMBA_COMMANDS.get(location, {})
    env = os.environ.copy()
    env["PATH"] = f"{OPENCLAW_BIN}:{env.get('PATH', '')}"

    if location == "crosstown":
        cmd = cmds.get(action)
        if cmd:
            log(f"ROOMBA: {' '.join(cmd)}")
            try:
                result = subprocess.run(cmd, capture_output=True, timeout=30, env=env)
                log(f"ROOMBA result: {result.stdout.decode()[:200]}")
                if result.returncode != 0:
                    log(f"ROOMBA error: {result.stderr.decode()[:200]}")
            except Exception as e:
                log(f"ROOMBA error: {e}")
    elif location == "cabin":
        # Cabin has two separate Roombas
        for key in (f"{action}_1", f"{action}_2"):
            cmd = cmds.get(key)
            if cmd:
                log(f"ROOMBA: {' '.join(cmd)}")
                try:
                    result = subprocess.run(cmd, capture_output=True, timeout=30, env=env)
                    log(f"ROOMBA result: {result.stdout.decode()[:200]}")
                except Exception as e:
                    log(f"ROOMBA error: {e}")

    _roomba_last_action[cooldown_key] = now


def capture_findmy() -> str | None:
    """Capture FindMy app window via Peekaboo. Returns path to PNG or None."""
    FINDMY_CAPTURE_DIR.mkdir(parents=True, exist_ok=True)
    capture_path = str(FINDMY_CAPTURE_DIR / "findmy-poll.png")
    try:
        result = subprocess.run(
            [PEEKABOO, "image", "--app", "Find My", "--path", capture_path],
            capture_output=True, timeout=15,
        )
        if result.returncode == 0 and Path(capture_path).exists() and Path(capture_path).stat().st_size > 1000:
            return capture_path
        log(f"FindMy capture failed: exit={result.returncode} stderr={result.stderr.decode()[:200]}")
        return None
    except Exception as e:
        log(f"FindMy capture error: {e}")
        return None


def analyze_findmy(image_path: str) -> dict | None:
    """Analyze FindMy screenshot with Claude vision to determine location."""
    try:
        if not OAUTH_CACHE.exists():
            return None
        oauth = json.loads(OAUTH_CACHE.read_text()).get("claudeAiOauth", {})
        token = oauth.get("accessToken")
        if not token:
            return None

        import base64
        with open(image_path, "rb") as f:
            img_b64 = base64.standard_b64encode(f.read()).decode()

        payload = json.dumps({
            "model": VISION_MODEL,
            "max_tokens": 150,
            "messages": [{"role": "user", "content": [
                {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": img_b64}},
                {"type": "text", "text": FINDMY_PROMPT},
            ]}],
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
        resp = urllib.request.urlopen(req, timeout=30)
        result = json.loads(resp.read().decode())
        text = result["content"][0]["text"].strip()
        return parse_vision_result(text)
    except Exception as e:
        log(f"FindMy analysis error: {e}")
        return None


async def _findmy_poll_loop(location: str) -> None:
    """Poll FindMy every 5 minutes to detect return home. Dock Roombas when near home."""
    POLL_INTERVAL = 300  # 5 minutes
    MAX_DURATION = 7200  # 2 hours
    start_time = time.time()

    log(f"FINDMY POLL: Starting return-home monitoring for {location}")
    send_imessage(f"\U0001f4cd Tracking your walk — will dock Roombas when you're back on Crosstown Ave")

    # Wait 5 minutes before first poll (you just left)
    await asyncio.sleep(POLL_INTERVAL)

    while time.time() - start_time < MAX_DURATION:
        try:
            capture_path = capture_findmy()
            if not capture_path:
                log("FINDMY POLL: Capture failed, retrying next interval")
                await asyncio.sleep(POLL_INTERVAL)
                continue

            result = analyze_findmy(capture_path)
            Path(capture_path).unlink(missing_ok=True)

            if result:
                street = result.get("street", "unknown")
                near_home = result.get("near_home", False)
                desc = result.get("description", "")
                log(f"FINDMY POLL: street={street} near_home={near_home} desc={desc}")

                if near_home:
                    elapsed = int((time.time() - start_time) / 60)
                    log(f"FINDMY POLL: Return detected after {elapsed}min — docking Roombas at {location}")
                    run_roomba_command(location, "dock")
                    return
            else:
                log("FINDMY POLL: Could not parse location result")

        except asyncio.CancelledError:
            log("FINDMY POLL: Cancelled")
            return
        except Exception as e:
            log(f"FINDMY POLL: Error: {e}")

        await asyncio.sleep(POLL_INTERVAL)

    log(f"FINDMY POLL: Timeout after {MAX_DURATION // 60}min — docking Roombas as safety fallback")
    send_imessage(f"\u23f0 Walk tracking timed out after 2 hours — docking Roombas at {location}.")
    run_roomba_command(location, "dock")


def start_findmy_polling(location: str) -> None:
    """Start FindMy polling in the background to detect return home."""
    global _findmy_poll_task
    # Cancel any existing poll
    if _findmy_poll_task and not _findmy_poll_task.done():
        _findmy_poll_task.cancel()
    _findmy_poll_task = asyncio.get_event_loop().create_task(_findmy_poll_loop(location))


def stop_findmy_polling() -> None:
    """Stop FindMy polling if active."""
    global _findmy_poll_task
    if _findmy_poll_task and not _findmy_poll_task.done():
        _findmy_poll_task.cancel()
        _findmy_poll_task = None


def check_departure(vision_data: dict, doorbot_id: int) -> None:
    """Accumulate departing people/dogs across recent events and trigger Roombas.

    People and dogs often pass the doorbell in separate motion events during a
    single departure. This function accumulates sightings within a 10-minute
    sliding window. Arrival detection is handled by FindMy polling, not Ring.

    Trigger conditions:
    - 2+ people AND 2+ dogs departing → auto-start Roombas + begin FindMy polling
    - 2+ people AND 1 dog departing → ask Dylan via iMessage for confirmation
    """
    location = DOORBELL_LOCATIONS.get(doorbot_id)
    if not location:
        return

    people = vision_data.get("people", [])
    dogs = vision_data.get("dogs", [])
    direction = vision_data.get("direction", "unclear").lower()

    # Only track departures — arrivals handled by FindMy polling
    if direction != "departing":
        return

    now = time.time()

    # Record this sighting
    _departure_sightings.append({
        "time": now,
        "people": len(people),
        "dogs": len(dogs),
        "direction": direction,
        "location": location,
    })

    # Prune old sightings outside the window
    cutoff = now - _DEPARTURE_WINDOW
    _departure_sightings[:] = [s for s in _departure_sightings if s["time"] >= cutoff]

    # Accumulate counts for departures at this location within the window
    total_people = 0
    total_dogs = 0
    for s in _departure_sightings:
        if s["direction"] == "departing" and s["location"] == location:
            total_people = max(total_people, s["people"])  # use max per event
            total_dogs += s["dogs"]  # dogs may appear in different events

    log(f"ACCUMULATOR: direction={direction} location={location} "
        f"people_max={total_people} dogs_total={total_dogs} "
        f"window_events={sum(1 for s in _departure_sightings if s['direction'] == direction and s['location'] == location)}")

    if total_people < 2:
        return

    if total_dogs >= 2:
        # Full household departure — auto-trigger
        _departure_sightings[:] = [
            s for s in _departure_sightings
            if not (s["direction"] == "departing" and s["location"] == location)
        ]
        log(f"DEPARTURE DETECTED at {location}: 2+ people + 2+ dogs leaving (accumulated)!")
        send_imessage(f"\U0001f9f9 Starting Roombas at {location} — everyone left for a walk!")
        run_roomba_command(location, "start")
        start_findmy_polling(location)

    elif total_dogs == 1:
        # Only 1 dog seen — ask for confirmation
        _departure_sightings[:] = [
            s for s in _departure_sightings
            if not (s["direction"] == "departing" and s["location"] == location)
        ]
        log(f"PARTIAL DEPARTURE at {location}: 2+ people + 1 dog — asking for confirmation")
        send_imessage(
            f"\U0001f436 Spotted 2 people and 1 dog leaving at {location}. "
            f"Should I start the Roombas? (Tell OpenClaw yes/no)"
        )


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
            if person_detected:
                msg = f"\U0001f514 {device}: Person detected at door"
                log(f"NOTIFY (unverified): {msg}")
                send_imessage(msg)
            return

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
            msg = f"\U0001f514 {device}: Person detected at door"
            log(f"NOTIFY: {msg}")
            send_imessage(msg)

            # Grab and send frame
            await _send_event_recording(device, doorbot_id, event_id, db=db)
        else:
            log(f"Motion on {device} — no person detected, skipping")

    except Exception as e:
        log(f"ERROR handling motion: {e}")


async def _send_event_recording(device: str, doorbot_id: int, event_id: int, db=None) -> None:
    """Download recording, analyze video with Claude vision, send frame + description."""
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
            log(f"{device} has no Ring Protect — skipping recording")
            return

        mp4_path, frame_path = await download_recording(db, event_id)
        if not mp4_path:
            log(f"Could not download recording for event {event_id}")
            return

        # Analyze the full video clip with Claude vision
        description = ""
        raw_analysis = analyze_video(mp4_path)
        if raw_analysis:
            log(f"Vision raw: {raw_analysis}")
            vision_data = parse_vision_result(raw_analysis)
            if vision_data:
                description = vision_data.get("description", "")

                # If we see people but only 1 dog, retry with more frames
                # to catch the second dog that may appear briefly
                people = vision_data.get("people", [])
                dogs = vision_data.get("dogs", [])
                direction = vision_data.get("direction", "unclear").lower()
                if len(people) >= 1 and len(dogs) == 1 and direction in ("departing", "arriving"):
                    log(f"Only 1 dog in 5 frames — retrying with 10 frames for better coverage")
                    retry_analysis = analyze_video(mp4_path, frame_count=10)
                    if retry_analysis:
                        log(f"Vision retry raw: {retry_analysis}")
                        retry_data = parse_vision_result(retry_analysis)
                        if retry_data and len(retry_data.get("dogs", [])) > len(dogs):
                            log(f"Retry found more dogs: {len(retry_data.get('dogs', []))} vs {len(dogs)}")
                            vision_data = retry_data
                            description = retry_data.get("description", description)

                # Check for departure/arrival automation
                check_departure(vision_data, doorbot_id)
            else:
                # Fallback: use raw text as description
                description = raw_analysis

        # Send preview frame as iMessage image, then description as caption
        if frame_path:
            log(f"Sending frame: {frame_path}")
            send_imessage_image(frame_path, caption=description)
            Path(frame_path).unlink(missing_ok=True)
        elif description:
            # No frame but have description — send as text
            send_imessage(description)

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

    # Event listener
    listener = RingEventListener(ring, credentials=fcm_creds, credentials_updated_callback=save_fcm_credentials)
    listener.add_notification_callback(on_event)

    started = await listener.start(timeout=30)
    if not started:
        log("ERROR: Failed to start event listener (FCM registration failed)")
        sys.exit(1)

    log("Event listener started — waiting for Ring events...")

    # Keep running forever
    try:
        while True:
            await asyncio.sleep(3600)
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
