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

# Dedup: track recent event IDs to avoid double-notify
_recent_events: dict[int, float] = {}
_DEDUP_WINDOW = 300  # 5 minutes

# Roomba cooldown: prevent re-triggering within 2 hours per location
_roomba_last_action: dict[str, float] = {}
_ROOMBA_COOLDOWN = 7200  # 2 hours


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


def analyze_frame(image_path: str) -> str | None:
    """Use Claude vision (via OAuth) to describe who/what is at the door."""
    try:
        if not OAUTH_CACHE.exists():
            log("No OAuth cache — skipping vision analysis")
            return None
        oauth = json.loads(OAUTH_CACHE.read_text()).get("claudeAiOauth", {})
        token = oauth.get("accessToken")
        if not token:
            log("No OAuth access token — skipping vision analysis")
            return None

        import base64
        with open(image_path, "rb") as f:
            img_b64 = base64.standard_b64encode(f.read()).decode()

        payload = json.dumps({
            "model": VISION_MODEL,
            "max_tokens": 150,
            "messages": [{
                "role": "user",
                "content": [
                    {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": img_b64}},
                    {"type": "text", "text": VISION_PROMPT},
                ],
            }],
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


def check_departure_arrival(vision_data: dict, doorbot_id: int) -> None:
    """Check vision analysis for full household departure or arrival and trigger Roombas."""
    location = DOORBELL_LOCATIONS.get(doorbot_id)
    if not location:
        return

    people = [p.lower() for p in vision_data.get("people", [])]
    dogs = [d.lower() for d in vision_data.get("dogs", [])]
    direction = vision_data.get("direction", "unclear").lower()

    has_dylan = any("dylan" in p for p in people)
    has_julia = any("julia" in p for p in people)
    both_people = has_dylan and has_julia
    both_dogs = len(dogs) >= 2

    if not both_people or not both_dogs:
        return

    if direction == "departing":
        log(f"DEPARTURE DETECTED at {location}: Dylan + Julia + both dogs leaving!")
        send_imessage(f"\U0001f9f9 Starting Roombas at {location} — everyone left for a walk!")
        run_roomba_command(location, "start")

    elif direction == "arriving":
        log(f"ARRIVAL DETECTED at {location}: Dylan + Julia + both dogs returning!")
        send_imessage(f"\U0001f3e0 Welcome home! Docking Roombas at {location}.")
        run_roomba_command(location, "dock")


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


async def download_and_extract_frame(db, event_id: int) -> str | None:
    """Download the recording for an event and extract the first frame."""
    FRAME_DIR.mkdir(parents=True, exist_ok=True)

    try:
        # Get the video URL
        url = await db.async_recording_url(event_id)
        if not url:
            log(f"No video URL for event {event_id}")
            return None

        # Download the MP4
        mp4_path = str(FRAME_DIR / f"event-{event_id}.mp4")
        frame_path = str(FRAME_DIR / f"event-{event_id}.jpg")

        async with aiohttp.ClientSession() as session:
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=30)) as resp:
                if resp.status != 200:
                    log(f"Video download failed: HTTP {resp.status}")
                    return None
                async with aiofiles.open(mp4_path, "wb") as f:
                    async for chunk in resp.content.iter_chunked(8192):
                        await f.write(chunk)

        # Extract first frame with ffmpeg
        result = subprocess.run(
            [FFMPEG, "-i", mp4_path, "-vframes", "1", "-q:v", "2",
             "-update", "1", frame_path, "-y"],
            capture_output=True, timeout=10,
        )
        if result.returncode != 0:
            log(f"ffmpeg failed: {result.stderr.decode()[:200]}")
            return None

        # Clean up MP4
        Path(mp4_path).unlink(missing_ok=True)

        if Path(frame_path).exists() and Path(frame_path).stat().st_size > 0:
            return frame_path
        return None

    except Exception as e:
        log(f"ERROR extracting frame: {e}")
        return None


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
        loop.create_task(_handle_motion(device, doorbot_id, event.id))

    # Skip on_demand and other event types


async def _handle_ding(device: str, doorbot_id: int, event_id: int) -> None:
    """Handle doorbell ring — always notify with image if available."""
    # Send immediate text notification
    msg = f"\U0001f514 {device}: Doorbell rang!"
    log(f"NOTIFY: {msg}")
    send_imessage(msg)

    # Try to grab a frame (recording may take a few seconds)
    await _send_event_frame(device, doorbot_id, event_id)


async def _handle_motion(device: str, doorbot_id: int, event_id: int) -> None:
    """Handle motion — check for person detection, then notify with image."""
    try:
        # Wait for Ring to process CV data
        await asyncio.sleep(5)

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
            msg = f"\U0001f514 {device}: Motion detected"
            log(f"NOTIFY (unverified): {msg}")
            send_imessage(msg)
            return

        # Check history for person detection
        history = await db.async_history(limit=3)
        person_detected = False
        recording_id = None
        for h in history:
            if h.get("id") == event_id:
                cv = h.get("cv_properties") or {}
                person_detected = cv.get("person_detected", False)
                recording_id = h.get("id")
                break

        if person_detected:
            msg = f"\U0001f514 {device}: Person detected at door"
            log(f"NOTIFY: {msg}")
            send_imessage(msg)

            # Grab and send frame
            if recording_id:
                await _send_event_frame(device, doorbot_id, recording_id, db=db)
        else:
            log(f"Motion on {device} — no person detected, skipping")

    except Exception as e:
        log(f"ERROR handling motion: {e}")


async def _send_event_frame(device: str, doorbot_id: int, event_id: int, db=None) -> None:
    """Download recording and send frame as iMessage attachment."""
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
            log(f"Cannot find doorbell {doorbot_id} for frame extraction")
            return

        if not db.has_subscription:
            log(f"{device} has no Ring Protect — skipping frame")
            return

        frame_path = await download_and_extract_frame(db, event_id)
        if frame_path:
            # Analyze the frame with Claude vision
            raw_analysis = analyze_frame(frame_path)
            description = ""
            if raw_analysis:
                log(f"Vision raw: {raw_analysis}")
                vision_data = parse_vision_result(raw_analysis)
                if vision_data:
                    description = vision_data.get("description", "")
                    # Check for departure/arrival automation
                    check_departure_arrival(vision_data, doorbot_id)
                else:
                    # Fallback: use raw text as description
                    description = raw_analysis

            # Send image then description
            log(f"Sending frame: {frame_path}")
            send_imessage_image(frame_path, caption=description)
            # Clean up frame after sending
            Path(frame_path).unlink(missing_ok=True)
        else:
            log(f"Could not extract frame for event {event_id}")

    except Exception as e:
        log(f"ERROR sending frame: {e}")


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
