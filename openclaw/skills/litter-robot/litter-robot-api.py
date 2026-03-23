#!/usr/bin/env python3
"""Litter-Robot API wrapper for OpenClaw.

Usage:
    litter-robot-api.py status           Robot status, waste level, cats
    litter-robot-api.py clean            Start a cleaning cycle
    litter-robot-api.py history [N]      Activity history (default: 10 entries)
    litter-robot-api.py pets             Pet info and weights
    litter-robot-api.py nightlight <on|off>  Toggle night light
    litter-robot-api.py reset            Reset waste drawer gauge
"""

import asyncio
import json
import sys
import time
from pathlib import Path

# Use venv packages
VENV_SITE = Path.home() / ".openclaw/litter-robot/venv/lib"
for p in VENV_SITE.glob("python*/site-packages"):
    sys.path.insert(0, str(p))

from pylitterbot import Account

CONFIG_DIR = Path.home() / ".config" / "litter-robot"
CONFIG_FILE = CONFIG_DIR / "config.yaml"
TOKEN_FILE = CONFIG_DIR / "token-cache.json"


def load_config():
    if not CONFIG_FILE.exists():
        print(json.dumps({"error": "config_missing", "message": f"Config not found at {CONFIG_FILE}"}))
        sys.exit(1)
    config = {}
    for line in CONFIG_FILE.read_text().splitlines():
        if ":" in line:
            k, _, v = line.partition(":")
            config[k.strip()] = v.strip()
    return config


def load_cached_tokens():
    if TOKEN_FILE.exists():
        try:
            cached = json.loads(TOKEN_FILE.read_text())
            if time.time() - cached.get("cached_at", 0) < 86400:  # 24h
                return cached.get("tokens")
        except (json.JSONDecodeError, KeyError):
            pass
    return None


def save_tokens(tokens):
    TOKEN_FILE.write_text(json.dumps({"tokens": tokens, "cached_at": time.time()}))
    TOKEN_FILE.chmod(0o600)


async def get_account():
    config = load_config()
    cached_tokens = load_cached_tokens()

    account = Account(
        token=cached_tokens,
        token_update_callback=save_tokens,
    )

    try:
        await account.connect(
            username=config["email"],
            password=config["password"],
            load_robots=True,
            load_pets=True,
        )
    except Exception as e:
        print(json.dumps({"error": "auth_failed", "message": str(e)}))
        sys.exit(1)

    return account


async def cmd_status():
    account = await get_account()
    try:
        output = {"robots": [], "pets": []}
        for robot in account.robots:
            r = {
                "name": robot.name,
                "model": robot.model,
                "serial": robot.serial,
                "status": str(robot.status).replace("LitterBoxStatus.", ""),
                "wasteLevel": robot.waste_drawer_level,
                "isOnline": robot.is_online,
                "nightLight": robot.night_light_mode_enabled,
                "panelLock": robot.panel_lock_enabled,
                "cleanWaitMinutes": robot.clean_cycle_wait_time_minutes,
            }
            try:
                r["cycleCount"] = robot.cycle_count
                r["cycleCapacity"] = robot.cycle_capacity
            except Exception:
                pass
            try:
                r["isWasteFull"] = robot.is_waste_drawer_full
            except Exception:
                pass
            output["robots"].append(r)

        for pet in account.pets:
            output["pets"].append({
                "name": pet.name,
                "type": str(pet.pet_type).replace("PetType.", ""),
                "weight": pet.weight,
            })

        print(json.dumps(output, indent=2, default=str))
    finally:
        await account.disconnect()


async def cmd_clean():
    account = await get_account()
    try:
        if not account.robots:
            print(json.dumps({"error": "no_robot", "message": "No Litter-Robot found"}))
            sys.exit(1)
        robot = account.robots[0]
        await robot.start_cleaning()
        print(json.dumps({"success": True, "robot": robot.name, "action": "clean_cycle_started"}))
    finally:
        await account.disconnect()


async def cmd_history(limit=10):
    account = await get_account()
    try:
        if not account.robots:
            print(json.dumps({"error": "no_robot"}))
            sys.exit(1)
        robot = account.robots[0]
        history = await robot.get_activity_history(limit=int(limit))
        entries = []
        for h in history:
            entries.append({
                "timestamp": str(h.timestamp) if hasattr(h, "timestamp") else str(h),
                "action": str(h.action) if hasattr(h, "action") else None,
            })
        print(json.dumps(entries, indent=2, default=str))
    finally:
        await account.disconnect()


async def cmd_pets():
    account = await get_account()
    try:
        pets = []
        for pet in account.pets:
            p = {
                "name": pet.name,
                "type": str(pet.pet_type).replace("PetType.", ""),
                "weight": pet.weight,
                "gender": str(pet.gender) if hasattr(pet, "gender") else None,
            }
            try:
                weight_history = await pet.fetch_weight_history()
                if weight_history:
                    p["recentWeights"] = [
                        {"weight": w.weight if hasattr(w, "weight") else w, "date": str(w.timestamp) if hasattr(w, "timestamp") else None}
                        for w in weight_history[:5]
                    ]
            except Exception:
                pass
            pets.append(p)
        print(json.dumps(pets, indent=2, default=str))
    finally:
        await account.disconnect()


async def cmd_nightlight(state):
    account = await get_account()
    try:
        if not account.robots:
            print(json.dumps({"error": "no_robot"}))
            sys.exit(1)
        robot = account.robots[0]
        value = state.lower() in ("on", "true", "1", "yes")
        await robot.set_night_light(value)
        print(json.dumps({"success": True, "robot": robot.name, "nightLight": value}))
    finally:
        await account.disconnect()


async def cmd_reset():
    account = await get_account()
    try:
        if not account.robots:
            print(json.dumps({"error": "no_robot"}))
            sys.exit(1)
        robot = account.robots[0]
        await robot.reset_waste_drawer()
        print(json.dumps({"success": True, "robot": robot.name, "action": "waste_drawer_reset"}))
    finally:
        await account.disconnect()


def main():
    if len(sys.argv) < 2:
        print(__doc__.strip())
        sys.exit(1)

    cmd = sys.argv[1]
    if cmd == "status":
        asyncio.run(cmd_status())
    elif cmd == "clean":
        asyncio.run(cmd_clean())
    elif cmd == "history":
        limit = int(sys.argv[2]) if len(sys.argv) > 2 else 10
        asyncio.run(cmd_history(limit))
    elif cmd == "pets":
        asyncio.run(cmd_pets())
    elif cmd == "nightlight":
        if len(sys.argv) < 3:
            print(json.dumps({"error": "missing_arg", "message": "Usage: nightlight <on|off>"}))
            sys.exit(1)
        asyncio.run(cmd_nightlight(sys.argv[2]))
    elif cmd == "reset":
        asyncio.run(cmd_reset())
    else:
        print(json.dumps({"error": "unknown_command", "message": f"Unknown: {cmd}"}))
        sys.exit(1)


if __name__ == "__main__":
    main()
