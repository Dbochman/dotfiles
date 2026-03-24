#!/usr/bin/env python3
"""Home state snapshot — captures cat weights, sleep data, and doorbell battery.

Appends a JSON snapshot to ~/.openclaw/home-state/YYYY-MM-DD.jsonl
and writes the latest state to ~/.openclaw/home-state/current.json.

Designed to run once daily via LaunchAgent (e.g., 9 AM).
"""

import json
import os
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path

HISTORY_DIR = Path.home() / ".openclaw/home-state"
CURRENT_FILE = HISTORY_DIR / "current.json"

# CLI paths
LITTER_ROBOT_API = Path.home() / ".openclaw/skills/litter-robot/litter-robot-api.py"
LITTER_ROBOT_VENV_PYTHON = Path.home() / ".openclaw/litter-robot/venv/bin/python3"
EIGHTSLEEP_API = Path.home() / ".openclaw/skills/8sleep/8sleep-api.py"
RING_API = Path.home() / ".openclaw/skills/ring-doorbell/ring-api.py"
RING_VENV_PYTHON = Path.home() / ".openclaw/ring/venv/bin/python3"


def run_cmd(args, timeout=30):
    """Run a command and return stdout as string, or None on failure."""
    try:
        result = subprocess.run(args, capture_output=True, timeout=timeout, text=True)
        if result.returncode == 0:
            return result.stdout.strip()
        print(f"  WARN: {args[0]} exited {result.returncode}: {result.stderr[:200]}", file=sys.stderr)
    except Exception as e:
        print(f"  WARN: {args[0]} failed: {e}", file=sys.stderr)
    return None


def run_json(args, timeout=30):
    """Run a command and parse stdout as JSON, or return None."""
    out = run_cmd(args, timeout)
    if out:
        try:
            return json.loads(out)
        except json.JSONDecodeError:
            print(f"  WARN: could not parse JSON from {args[0]}", file=sys.stderr)
    return None


def collect_cat_weights():
    """Get current cat weights from Litter-Robot."""
    data = run_json([str(LITTER_ROBOT_VENV_PYTHON), str(LITTER_ROBOT_API), "pets"], timeout=30)
    if not data:
        return None

    cats = []
    for pet in data:
        entry = {
            "name": pet.get("name"),
            "weight_lbs": pet.get("weight"),
            "gender": pet.get("gender"),
        }
        # Include latest weight timestamp if available
        recent = pet.get("recentWeights", [])
        if recent:
            entry["last_weighed"] = recent[-1].get("date")
        cats.append(entry)
    return cats


def collect_sleep_data():
    """Get last night's sleep data for both sides from Eight Sleep."""
    yesterday = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
    sides = {}

    for side in ("dylan", "julia"):
        data = run_json(["/usr/bin/python3", str(EIGHTSLEEP_API), "sleep", side], timeout=30)
        if not data:
            continue

        # Extract from trends response
        days = data.get("days", [])
        if not days:
            continue
        day = days[0]
        score = day.get("score")
        sessions = day.get("sleepDuration", day.get("sessions", []))

        entry = {
            "date": yesterday,
            "score": score,
        }

        # Duration from API is in seconds
        sleep_dur = day.get("sleepDuration")
        if sleep_dur:
            entry["duration_sec"] = sleep_dur
            entry["duration_min"] = round(sleep_dur / 60)

        stages = day.get("stages", {})
        if stages:
            entry["rem_pct"] = stages.get("rem")
            entry["deep_pct"] = stages.get("deep")
            entry["light_pct"] = stages.get("light")
            entry["awake_pct"] = stages.get("awake")

        entry["snoring_min"] = day.get("snoringDuration")
        entry["tosses_turns"] = day.get("tnt")
        entry["time_in_bed"] = day.get("presenceDuration")
        entry["bed_temp_avg"] = day.get("tempRoomC")
        entry["hrv_avg"] = day.get("hrv")
        entry["hr_avg"] = day.get("heartRate")
        entry["rr_avg"] = day.get("respiratoryRate")

        sides[side] = entry

    return sides if sides else None


def collect_doorbell_battery():
    """Get battery levels for all Ring doorbells."""
    # ring-api.py status outputs JSON directly (the bash wrapper formats it)
    data = run_json([str(RING_VENV_PYTHON), str(RING_API), "status"], timeout=30)
    if not data:
        return None

    doorbells_raw = data.get("doorbells", [])
    return [
        {
            "name": d.get("name"),
            "id": d.get("id"),
            "battery_pct": d.get("battery"),
            "location": "crosstown" if d.get("id") == 684794187 else "cabin",
        }
        for d in doorbells_raw
        if d.get("battery") is not None
    ] or None


def main():
    now = datetime.utcnow()
    snapshot = {
        "timestamp": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "type": "daily_snapshot",
    }

    print("Collecting cat weights...")
    cats = collect_cat_weights()
    if cats:
        snapshot["cats"] = cats
        for c in cats:
            print(f"  {c['name']}: {c['weight_lbs']} lbs")

    print("Collecting sleep data...")
    sleep = collect_sleep_data()
    if sleep:
        snapshot["sleep"] = sleep
        for side, data in sleep.items():
            print(f"  {side}: score={data.get('score')} duration={data.get('duration_min')}min")

    print("Collecting doorbell battery...")
    batteries = collect_doorbell_battery()
    if batteries:
        snapshot["doorbell_battery"] = batteries
        for d in batteries:
            print(f"  {d['name']} ({d['location']}): {d.get('battery_pct')}%")

    # Write current state
    HISTORY_DIR.mkdir(parents=True, exist_ok=True)
    CURRENT_FILE.write_text(json.dumps(snapshot, indent=2))

    # Append to daily history
    history_file = HISTORY_DIR / f"{now.strftime('%Y-%m-%d')}.jsonl"
    with open(history_file, "a") as f:
        f.write(json.dumps(snapshot) + "\n")

    print(f"\nSnapshot written to {CURRENT_FILE}")
    print(f"History appended to {history_file}")


if __name__ == "__main__":
    main()
