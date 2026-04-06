#!/usr/bin/env python3
"""Ring Doorbell API wrapper for OpenClaw.

Usage:
    ring-api.py status           All doorbells: name, model, battery, wifi, last event
    ring-api.py events [N]       Last N ding/motion events (default: 10)
    ring-api.py health           WiFi signal strength and connectivity per device
    ring-api.py video [ID]       Get video URL for a recording (default: latest)
    ring-api.py videos [N]       List N recent recordings with URLs (default: 5)
    ring-api.py snapshot [FILE]  Capture a snapshot (saves to FILE or prints path)
    ring-api.py download ID FILE Download a recording MP4 to FILE
"""

import asyncio
import json
import sys
import time
from pathlib import Path

# Use venv packages
VENV_SITE = Path.home() / ".openclaw/ring/venv/lib"
for p in VENV_SITE.glob("python*/site-packages"):
    sys.path.insert(0, str(p))

from ring_doorbell import Auth, Ring, Requires2FAError, AuthenticationError

CONFIG_DIR = Path.home() / ".config" / "ring"
CONFIG_FILE = CONFIG_DIR / "config.yaml"
TOKEN_FILE = CONFIG_DIR / "token-cache.json"

USER_AGENT = "OpenClaw/1.0"


def load_config():
    if not CONFIG_FILE.exists():
        print(json.dumps({"error": "config_missing",
                          "message": f"Config not found at {CONFIG_FILE}"}))
        sys.exit(1)
    config = {}
    for line in CONFIG_FILE.read_text().splitlines():
        if ":" in line:
            k, _, v = line.partition(":")
            config[k.strip()] = v.strip()
    return config


def load_cached_token():
    if TOKEN_FILE.exists():
        try:
            return json.loads(TOKEN_FILE.read_text())
        except (json.JSONDecodeError, KeyError):
            pass
    return None


def save_token(token_data):
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    TOKEN_FILE.write_text(json.dumps(token_data))
    TOKEN_FILE.chmod(0o600)


async def get_ring():
    config = load_config()
    cached_token = load_cached_token()

    auth = Auth(USER_AGENT, token=cached_token, token_updater=save_token)

    if cached_token is None:
        try:
            await auth.async_fetch_token(config["email"], config["password"])
        except Requires2FAError:
            # If running interactively, prompt for 2FA code
            if sys.stdin.isatty():
                otp = input("Enter Ring 2FA code: ").strip()
                await auth.async_fetch_token(config["email"], config["password"], otp)
            else:
                print(json.dumps({
                    "error": "2fa_required",
                    "message": "Ring requires 2FA. Run 'ring status' interactively on the Mini to complete setup."
                }))
                sys.exit(1)
        except AuthenticationError as e:
            print(json.dumps({"error": "auth_failed", "message": str(e)}))
            sys.exit(1)

    ring = Ring(auth)
    try:
        await ring.async_create_session()
    except AuthenticationError:
        # Cached token invalid — re-auth
        try:
            await auth.async_fetch_token(config["email"], config["password"])
        except Requires2FAError:
            if sys.stdin.isatty():
                otp = input("Enter Ring 2FA code: ").strip()
                await auth.async_fetch_token(config["email"], config["password"], otp)
            else:
                print(json.dumps({
                    "error": "2fa_required",
                    "message": "Ring token expired and 2FA is required. Run 'ring status' interactively on the Mini."
                }))
                sys.exit(1)
        except AuthenticationError as e:
            print(json.dumps({"error": "auth_failed", "message": str(e)}))
            sys.exit(1)
        ring = Ring(auth)
        await ring.async_create_session()

    await ring.async_update_data()
    return ring


async def cmd_status():
    ring = await get_ring()
    devices = ring.devices()
    doorbells = list(devices.doorbots) + list(devices.authorized_doorbots)

    output = {"doorbells": []}
    for db in doorbells:
        d = {
            "name": db.name,
            "model": db.model,
            "id": db.id,
            "family": db.family,
            "firmware": db.firmware,
            "address": db.address,
            "timezone": db.timezone,
        }
        if db.battery_life is not None:
            d["battery"] = db.battery_life
        if hasattr(db, "existing_doorbell_type"):
            d["chimeType"] = db.existing_doorbell_type
        # WiFi
        d["wifiName"] = db.wifi_name
        d["wifiSignal"] = db.wifi_signal_strength
        d["wifiCategory"] = db.wifi_signal_category
        # Last event (history returns dicts, not objects)
        try:
            history = await db.async_history(limit=1)
            if history:
                last = history[0]
                d["lastEvent"] = {
                    "kind": last.get("kind"),
                    "timestamp": str(last.get("created_at", "")),
                    "answered": last.get("answered"),
                    "personDetected": (last.get("cv_properties") or {}).get("person_detected"),
                }
        except Exception:
            pass
        output["doorbells"].append(d)

    print(json.dumps(output, indent=2, default=str))


async def cmd_events(limit=10):
    ring = await get_ring()
    devices = ring.devices()
    doorbells = list(devices.doorbots) + list(devices.authorized_doorbots)

    output = {"events": []}
    for db in doorbells:
        try:
            history = await db.async_history(limit=int(limit))
            for h in history:
                cv = h.get("cv_properties") or {}
                output["events"].append({
                    "device": db.name,
                    "kind": h.get("kind"),
                    "timestamp": str(h.get("created_at", "")),
                    "answered": h.get("answered"),
                    "duration": h.get("duration"),
                    "personDetected": cv.get("person_detected"),
                    "detectionType": cv.get("detection_type"),
                })
        except Exception as e:
            output["events"].append({
                "device": db.name,
                "error": str(e),
            })

    # Sort by timestamp descending
    output["events"].sort(key=lambda e: e.get("timestamp", ""), reverse=True)
    print(json.dumps(output, indent=2, default=str))


def find_doorbell(doorbells, identifier=None):
    """Find a doorbell by ID or name. Returns first with subscription if no identifier."""
    if identifier:
        for db in doorbells:
            if str(db.id) == str(identifier) or db.name.lower() == identifier.lower():
                return db
        return None
    # Default: first doorbell with a subscription (for video commands)
    for db in doorbells:
        if db.has_subscription:
            return db
    return doorbells[0] if doorbells else None


async def cmd_video(recording_id=None):
    """Get video URL for a recording (default: latest)."""
    ring = await get_ring()
    devices = ring.devices()
    doorbells = list(devices.doorbots) + list(devices.authorized_doorbots)
    db = find_doorbell(doorbells)
    if not db:
        print(json.dumps({"error": "no_device", "message": "No doorbell found"}))
        sys.exit(1)
    if not db.has_subscription:
        print(json.dumps({"error": "no_subscription",
                          "message": f"{db.name} (id={db.id}) has no Ring Protect subscription"}))
        sys.exit(1)

    if recording_id is None:
        recording_id = await db.async_get_last_recording_id()
        if not recording_id:
            print(json.dumps({"error": "no_recording", "message": "No recordings found"}))
            sys.exit(1)

    url = await db.async_recording_url(recording_id)
    if not url:
        print(json.dumps({"error": "no_url", "message": f"No video URL for recording {recording_id}"}))
        sys.exit(1)

    print(json.dumps({
        "device": db.name,
        "recordingId": recording_id,
        "url": url,
    }, indent=2, default=str))


async def cmd_videos(limit=5):
    """List recent recordings with URLs."""
    ring = await get_ring()
    devices = ring.devices()
    doorbells = list(devices.doorbots) + list(devices.authorized_doorbots)

    output = {"recordings": []}
    for db in doorbells:
        if not db.has_subscription:
            output["recordings"].append({
                "device": db.name,
                "id": db.id,
                "error": "no_subscription",
            })
            continue
        try:
            history = await db.async_history(limit=int(limit))
            for h in history:
                rec_id = h.get("id")
                rec_status = (h.get("recording") or {}).get("status")
                url = None
                if rec_status == "ready":
                    try:
                        url = await db.async_recording_url(rec_id)
                    except Exception:
                        pass
                cv = h.get("cv_properties") or {}
                output["recordings"].append({
                    "device": db.name,
                    "recordingId": rec_id,
                    "kind": h.get("kind"),
                    "timestamp": str(h.get("created_at", "")),
                    "duration": h.get("duration"),
                    "personDetected": cv.get("person_detected"),
                    "status": rec_status,
                    "url": url,
                })
        except Exception as e:
            output["recordings"].append({"device": db.name, "error": str(e)})

    print(json.dumps(output, indent=2, default=str))


async def cmd_snapshot(filename=None, device_id=None):
    """Capture a snapshot from a doorbell (default: first subscribed)."""
    ring = await get_ring()
    devices = ring.devices()
    doorbells = list(devices.doorbots) + list(devices.authorized_doorbots)
    db = find_doorbell(doorbells, device_id)
    if not db:
        print(json.dumps({"error": "no_device", "message": "No doorbell found"}))
        sys.exit(1)

    if filename is None:
        filename = f"/tmp/ring-snapshot-{db.id}.jpg"

    try:
        data = await db.async_get_snapshot(retries=3, delay=2)
        if data and len(data) > 0:
            Path(filename).write_bytes(data)
            print(json.dumps({
                "device": db.name,
                "file": filename,
                "size": Path(filename).stat().st_size,
            }, indent=2))
        else:
            print(json.dumps({"error": "snapshot_empty",
                              "message": f"{db.name} returned empty snapshot (doorbell may be asleep)"}))
            sys.exit(1)
    except IndexError:
        print(json.dumps({"error": "snapshot_unavailable",
                          "message": f"{db.name} could not capture snapshot (battery doorbell may be asleep — try ringing it first)"}))
        sys.exit(1)
    except Exception as e:
        print(json.dumps({"error": "snapshot_failed", "message": str(e)}))
        sys.exit(1)


async def cmd_download(recording_id, filename):
    """Download a recording MP4 via its signed URL."""
    ring = await get_ring()
    devices = ring.devices()
    doorbells = list(devices.doorbots) + list(devices.authorized_doorbots)
    db = find_doorbell(doorbells)
    if not db:
        print(json.dumps({"error": "no_device", "message": "No doorbell found"}))
        sys.exit(1)
    if not db.has_subscription:
        print(json.dumps({"error": "no_subscription",
                          "message": f"{db.name} has no Ring Protect subscription"}))
        sys.exit(1)

    try:
        # Get the signed URL first, then download directly
        url = await db.async_recording_url(int(recording_id))
        if not url:
            print(json.dumps({"error": "no_url",
                              "message": f"No video URL for recording {recording_id}"}))
            sys.exit(1)
        import aiohttp, aiofiles
        async with aiohttp.ClientSession() as session:
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=120)) as resp:
                if resp.status != 200:
                    print(json.dumps({"error": "download_failed",
                                      "message": f"HTTP {resp.status} downloading video"}))
                    sys.exit(1)
                async with aiofiles.open(filename, "wb") as f:
                    async for chunk in resp.content.iter_chunked(8192):
                        await f.write(chunk)
        print(json.dumps({
            "device": db.name,
            "recordingId": recording_id,
            "file": filename,
            "size": Path(filename).stat().st_size if Path(filename).exists() else 0,
        }, indent=2))
    except Exception as e:
        print(json.dumps({"error": "download_failed", "message": str(e)}))
        sys.exit(1)


async def cmd_health():
    ring = await get_ring()
    devices = ring.devices()
    doorbells = list(devices.doorbots) + list(devices.authorized_doorbots)

    output = {"devices": []}
    for db in doorbells:
        output["devices"].append({
            "name": db.name,
            "model": db.model,
            "firmware": db.firmware,
            "battery": db.battery_life,
            "wifiName": db.wifi_name,
            "wifiSignal": db.wifi_signal_strength,
            "wifiCategory": db.wifi_signal_category,
            "connectionStatus": db.connection_status if hasattr(db, "connection_status") else None,
        })
    print(json.dumps(output, indent=2, default=str))


def main():
    if len(sys.argv) < 2:
        print(__doc__.strip())
        sys.exit(1)

    cmd = sys.argv[1]
    if cmd == "status":
        asyncio.run(cmd_status())
    elif cmd == "events":
        limit = int(sys.argv[2]) if len(sys.argv) > 2 else 10
        asyncio.run(cmd_events(limit))
    elif cmd == "health":
        asyncio.run(cmd_health())
    elif cmd == "video":
        rec_id = int(sys.argv[2]) if len(sys.argv) > 2 else None
        asyncio.run(cmd_video(rec_id))
    elif cmd == "videos":
        limit = int(sys.argv[2]) if len(sys.argv) > 2 else 5
        asyncio.run(cmd_videos(limit))
    elif cmd == "snapshot":
        filename = sys.argv[2] if len(sys.argv) > 2 else None
        device_id = sys.argv[3] if len(sys.argv) > 3 else None
        asyncio.run(cmd_snapshot(filename, device_id))
    elif cmd == "download":
        if len(sys.argv) < 4:
            print(json.dumps({"error": "missing_arg",
                              "message": "Usage: ring-api.py download <recording-id> <filename>"}))
            sys.exit(1)
        asyncio.run(cmd_download(sys.argv[2], sys.argv[3]))
    else:
        print(json.dumps({"error": "unknown_command", "message": f"Unknown: {cmd}"}))
        sys.exit(1)


if __name__ == "__main__":
    main()
