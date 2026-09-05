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
CAT_ACTIVITY_LIMIT = 40
MAX_CAT_WEIGHT_LBS = 30
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
SITE_ORDER = ("cabin", "crosstown")
SITE_NAMES = {"cabin": "Cabin", "crosstown": "Crosstown"}

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
        devices = payload
    elif isinstance(payload, dict) and isinstance(payload.get("data"), list):
        devices = payload["data"]
    else:
        if isinstance(payload, dict):
            payload.setdefault("ok", False)
            return payload
        return {"ok": False, "error": "Petlibro returned an unexpected response"}

    verified_devices: list[object] = []
    for item in devices:
        if not isinstance(item, dict):
            verified_devices.append(item)
            continue
        device = dict(item)
        selector = device.get("selector")
        if selector not in PETLIBRO_FEEDER_SELECTORS:
            verified_devices.append(device)
            continue
        schedule = _run_json([PETLIBRO_CLI, "--json", "schedule-state", selector])
        feeding_history = _run_json(
            [PETLIBRO_CLI, "--json", "feeding-history", selector, "14"]
        )
        valid_schedule = (
            isinstance(schedule, dict)
            and schedule.get("success") is True
            and schedule.get("selector") == selector
            and isinstance(schedule.get("scheduleEnabled"), bool)
            and isinstance(schedule.get("enabledMealCount"), int)
            and not isinstance(schedule.get("enabledMealCount"), bool)
            and 0 <= schedule["enabledMealCount"] <= 64
            and isinstance(schedule.get("observedAt"), str)
        )
        if valid_schedule:
            device.update(
                {
                    "scheduleEnabled": schedule["scheduleEnabled"],
                    "enabledMealCount": schedule["enabledMealCount"],
                    "scheduleObservedAt": schedule["observedAt"],
                    "scheduleReadback": "verified",
                }
            )
        elif (
            isinstance(device.get("scheduleEnabled"), bool)
            and device.get("scheduleState")
            == ("enabled" if device["scheduleEnabled"] else "disabled")
        ):
            device.update(
                {
                    "enabledMealCount": None,
                    "scheduleObservedAt": _iso_timestamp(),
                    "scheduleReadback": "master_verified",
                }
            )
        else:
            device.update(
                {
                    "scheduleEnabled": None,
                    "enabledMealCount": None,
                    "scheduleObservedAt": None,
                    "scheduleReadback": "unavailable",
                }
            )
        valid_feedings = (
            isinstance(feeding_history, dict)
            and feeding_history.get("success") is True
            and feeding_history.get("selector") == selector
            and feeding_history.get("site") == PETLIBRO_FEEDER_SELECTORS[selector]
            and isinstance(feeding_history.get("feedings"), list)
            and len(feeding_history["feedings"]) <= 14
            and all(
                isinstance(feeding, dict)
                and set(feeding) == {"occurredAt", "portions"}
                and isinstance(feeding.get("occurredAt"), str)
                and isinstance(feeding.get("portions"), int)
                and not isinstance(feeding.get("portions"), bool)
                and 1 <= feeding["portions"] <= 48
                for feeding in feeding_history["feedings"]
            )
        )
        device["recentScheduledFeedings"] = (
            feeding_history["feedings"] if valid_feedings else []
        )
        device["feedingHistoryReadback"] = (
            "verified" if valid_feedings else "unavailable"
        )
        verified_devices.append(device)
    return {"ok": True, "devices": verified_devices}


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
    coverage_ready = observer.get("health") == "ok" and all(
        record["enabled"] is True
        and record["baselined"] is True
        and record["health"] == "ok"
        and isinstance(record["poll_age_seconds"], (int, float))
        and not isinstance(record["poll_age_seconds"], bool)
        and 0 <= record["poll_age_seconds"] <= 300
        for record in site_status.values()
    )
    return {
        "ok": True,
        "bus_health": str(payload.get("health", "unknown")),
        "observer_health": str(observer.get("health", "unknown")),
        "coverage_ready": coverage_ready,
        "sites": site_status,
        "accepted_events": whisker.get("accepted", 0),
        "pending_actions": counts.get("pending", 0),
        "unknown_actions": counts.get("outcome_unknown", 0),
    }


def summarize_transfer_state(
    automation: object,
    transfer: object,
    petlibro: object,
) -> dict[str, object]:
    """Translate provider and policy state into one household-facing outcome."""
    automation_data = automation if isinstance(automation, dict) else {}
    transfer_data = transfer if isinstance(transfer, dict) else {}
    petlibro_data = petlibro if isinstance(petlibro, dict) else {}
    owners = automation_data.get("feeding_schedule_owners")
    owner_map = owners if isinstance(owners, dict) else {}
    feeder_suspensions = automation_data.get("feeder_suspensions")
    suspension_data = (
        feeder_suspensions if isinstance(feeder_suspensions, dict) else {}
    )
    suspension_sites = suspension_data.get("sites")
    managed = suspension_sites if isinstance(suspension_sites, dict) else {}

    schedule_states = {site: "unavailable" for site in SITE_ORDER}
    devices = petlibro_data.get("devices")
    if isinstance(devices, list):
        for device in devices:
            if not isinstance(device, dict):
                continue
            selector = device.get("selector")
            site = PETLIBRO_FEEDER_SELECTORS.get(str(selector))
            if site is None or device.get("scheduleReadback") not in {
                "verified",
                "master_verified",
            }:
                continue
            enabled = device.get("scheduleEnabled")
            if isinstance(enabled, bool):
                schedule_states[site] = "on" if enabled else "paused"

    site_output = {
        site: {
            "schedule": schedule_states[site],
            "label": {
                "on": "Meals on",
                "paused": "Meals paused",
                "unavailable": "Checking",
            }[schedule_states[site]],
        }
        for site in SITE_ORDER
    }
    pending = transfer_data.get("pending_actions", 0)
    pending_count = (
        int(pending)
        if isinstance(pending, (int, float)) and not isinstance(pending, bool)
        else 0
    )
    unknown = transfer_data.get("unknown_actions", 0)
    unknown_count = (
        int(unknown)
        if isinstance(unknown, (int, float)) and not isinstance(unknown, bool)
        else 0
    )
    coverage_ready = (
        transfer_data.get("ok") is True
        and transfer_data.get("coverage_ready") is True
    )
    fully_automatic = all(owner_map.get(site) == "bus" for site in SITE_ORDER)

    current_errors: list[str] = []
    transitional = False
    for site, value in managed.items():
        if site not in SITE_ORDER or not isinstance(value, dict):
            current_errors.append("invalid_suspension")
            continue
        phase = value.get("phase")
        transitional = transitional or phase in {"suspending", "restoring"}
        if value.get("attention") is not True:
            continue
        recovered_readback = value.get("last_error") == "feeder_readback_unavailable" and (
            (phase == "suspended" and schedule_states[site] == "paused")
            or (phase == "restoring" and schedule_states[site] == "on")
        )
        if not recovered_readback:
            current_errors.append(str(value.get("last_error") or "unknown"))

    base = {
        "sites": site_output,
        "litter_boxes": "Both reporting" if coverage_ready else "Waiting for data",
        "pending_changes": pending_count,
        "attention": False,
        "notice": None,
    }
    if (
        automation_data.get("ok") is not True
        or transfer_data.get("ok") is not True
        or petlibro_data.get("ok") is not True
    ):
        return {
            **base,
            "tone": "bad",
            "label": "Unavailable",
            "title": "Cat feeding status is unavailable",
            "description": "OpenClaw could not confirm the feeders and litter boxes. No automatic feeder change will be made.",
            "summary": "No automatic changes",
            "attention": True,
            "notice": "Cat feeding status could not be confirmed. No automatic feeder change will be made.",
        }
    if unknown_count > 0:
        return {
            **base,
            "tone": "bad",
            "label": "Needs review",
            "title": "A feeder change could not be confirmed",
            "description": "OpenClaw stopped without retrying. Check the two feeder schedules before making another automatic change.",
            "summary": "One feeder change needs review",
            "attention": True,
            "notice": "A feeder change could not be confirmed. OpenClaw will not retry it automatically.",
        }
    if len(managed) == 1:
        waiting_site = next(iter(managed))
        waiting_state = managed[waiting_site]
        other_site = "crosstown" if waiting_site == "cabin" else "cabin"
        if (
            isinstance(waiting_state, dict)
            and waiting_state.get("waiting_reason") == "cat_transfer_not_settled"
            and schedule_states[waiting_site] == "on"
            and schedule_states[other_site] == "paused"
        ):
            return {
                **base,
                "tone": "warn",
                "label": "Waiting",
                "title": f"{SITE_NAMES[waiting_site]} meals are on",
                "description": (
                    f"{SITE_NAMES[waiting_site]} scheduled meals are on and "
                    f"{SITE_NAMES[other_site]} meals are paused. OpenClaw is waiting "
                    f"for a {SITE_NAMES[waiting_site]} litter-box visit before it "
                    "treats the cats’ return as confirmed."
                ),
                "summary": "Waiting for litter-box confirmation",
            }
    if current_errors or len(managed) > 1:
        return {
            **base,
            "tone": "bad",
            "label": "Needs review",
            "title": "Automatic feeding needs attention",
            "description": "The current feeder schedules could not be matched safely to one occupied home. OpenClaw will not make another change until this clears.",
            "summary": "Feeder schedules need review",
            "attention": True,
            "notice": "Automatic feeder switching needs review before it can make another change.",
        }
    if not fully_automatic:
        return {
            **base,
            "tone": "warn",
            "label": "Manual",
            "title": "Automatic feeder switching is off",
            "description": "At least one home is using manual feeder control, so OpenClaw will not move scheduled feeding between homes.",
            "summary": "Manual feeder control",
        }
    if pending_count > 0 or transitional:
        return {
            **base,
            "tone": "warn",
            "label": "Updating",
            "title": "Feeder schedules are being updated",
            "description": "OpenClaw is checking both homes and will show the final schedule state when the change is confirmed.",
            "summary": "A feeder change is in progress",
        }
    if len(managed) == 1:
        paused_site = next(iter(managed))
        cat_site = "crosstown" if paused_site == "cabin" else "cabin"
        suspension_context = managed[paused_site].get("occupancy_context")
        if (
            schedule_states[paused_site] == "paused"
            and schedule_states[cat_site] == "on"
        ):
            coverage_note = (
                " Both litter boxes are reporting."
                if coverage_ready
                else " The next automatic change will wait for fresh data from both litter boxes."
            )
            if suspension_context == "split_household":
                description = (
                    f"{SITE_NAMES[cat_site]} scheduled meals are on. "
                    f"{SITE_NAMES[paused_site]} scheduled meals remain paused because the cats are still at {SITE_NAMES[cat_site]}, even though someone is home at each house. "
                    "They will turn back on automatically after the cats return and the matching litter-box evidence settles."
                    f"{coverage_note}"
                )
                summary = f"Cats remain at {SITE_NAMES[cat_site]}"
            else:
                description = (
                    f"{SITE_NAMES[cat_site]} scheduled meals are on. "
                    f"{SITE_NAMES[paused_site]} scheduled meals are paused while that home is vacant and will turn back on automatically when the cats return."
                    f"{coverage_note}"
                )
                summary = f"{SITE_NAMES[paused_site]} meals paused automatically"
            return {
                **base,
                "tone": "ok" if coverage_ready else "warn",
                "label": "Working" if coverage_ready else "Protected",
                "title": f"Cats are at {SITE_NAMES[cat_site]}",
                "description": description,
                "summary": summary,
            }
        if "unavailable" in {
            schedule_states[paused_site],
            schedule_states[cat_site],
        }:
            return {
                **base,
                "tone": "warn",
                "label": "Checking",
                "title": "Checking both feeder schedules",
                "description": "OpenClaw knows which home should be paused, but one feeder has not returned a fresh schedule state yet. No new change will be made meanwhile.",
                "summary": "Waiting for feeder confirmation",
            }
        return {
            **base,
            "tone": "bad",
            "label": "Needs review",
            "title": "Feeder schedules do not match the cats’ home",
            "description": "The feeder at the empty home should be paused and the feeder with the cats should be on. OpenClaw will not make another change until the mismatch clears.",
            "summary": "Feeder schedules need review",
            "attention": True,
            "notice": "The feeder schedules do not match the expected home state.",
        }
    if all(schedule_states[site] == "on" for site in SITE_ORDER):
        return {
            **base,
            "tone": "ok" if coverage_ready else "warn",
            "label": "Ready" if coverage_ready else "Waiting",
            "title": "Both homes are ready",
            "description": (
                "Scheduled meals are on at both homes. OpenClaw will pause the empty home after the cats settle at the other home."
                if coverage_ready
                else "Scheduled meals are on at both homes. Automatic switching will wait for fresh data from both litter boxes."
            ),
            "summary": "Watching for the cats’ next move",
        }
    return {
        **base,
        "tone": "warn",
        "label": "Manual pause",
        "title": "A feeder is paused manually",
        "description": "OpenClaw did not create this pause and will not turn that feeder back on automatically.",
        "summary": "One feeder needs manual control",
    }


def _parsed_timestamp(value: object) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return None
    return parsed.astimezone(timezone.utc)


def _cat_weight_index(whisker: object) -> dict[tuple[int, float], set[str]]:
    data = whisker if isinstance(whisker, dict) else {}
    pets = data.get("pets")
    index: dict[tuple[int, float], set[str]] = {}
    if not isinstance(pets, list):
        return index
    for pet in pets:
        if not isinstance(pet, dict) or not isinstance(pet.get("name"), str):
            continue
        name = pet["name"].strip()
        samples = pet.get("recent_weights")
        if not name or not isinstance(samples, list):
            continue
        for sample in samples:
            if not isinstance(sample, dict):
                continue
            timestamp = _parsed_timestamp(sample.get("timestamp"))
            weight = sample.get("weight_lbs")
            if (
                timestamp is None
                or not isinstance(weight, (int, float))
                or isinstance(weight, bool)
                or not 0 < float(weight) <= MAX_CAT_WEIGHT_LBS
            ):
                continue
            key = (int(timestamp.timestamp()), round(float(weight), 2))
            index.setdefault(key, set()).add(name)
    return index


def build_cat_activity(
    whisker: object,
    petlibro: object,
    automation: object,
) -> list[dict[str, object]]:
    """Build one privacy-bounded, household-readable activity timeline."""
    events: list[dict[str, object]] = []
    weight_index = _cat_weight_index(whisker)
    whisker_data = whisker if isinstance(whisker, dict) else {}
    robots = whisker_data.get("robots")
    if isinstance(robots, list):
        for robot in robots:
            if not isinstance(robot, dict) or robot.get("site") not in SITE_NAMES:
                continue
            site = str(robot["site"])
            recent = robot.get("recent_activity")
            if not isinstance(recent, list):
                continue
            for record in recent:
                if not isinstance(record, dict):
                    continue
                occurred_at = _parsed_timestamp(record.get("timestamp"))
                weight = record.get("weight_lbs")
                if (
                    occurred_at is None
                    or not isinstance(weight, (int, float))
                    or isinstance(weight, bool)
                    or not 0 < float(weight) <= MAX_CAT_WEIGHT_LBS
                ):
                    continue
                key = (int(occurred_at.timestamp()), round(float(weight), 2))
                names = weight_index.get(key, set())
                name = next(iter(names)) if len(names) == 1 else None
                events.append(
                    {
                        "kind": "litter_visit",
                        "occurredAt": occurred_at.isoformat(timespec="seconds").replace(
                            "+00:00", "Z"
                        ),
                        "site": site,
                        "sites": [site],
                        "location": SITE_NAMES[site],
                        "title": (
                            f"{name} used the Litter-Robot"
                            if name
                            else "A cat used the Litter-Robot"
                        ),
                        "detail": f"{float(weight):g} lb",
                    }
                )

    petlibro_data = petlibro if isinstance(petlibro, dict) else {}
    devices = petlibro_data.get("devices")
    if isinstance(devices, list):
        for device in devices:
            if not isinstance(device, dict):
                continue
            site = PETLIBRO_FEEDER_SELECTORS.get(str(device.get("selector")))
            feedings = device.get("recentScheduledFeedings")
            if site is None or not isinstance(feedings, list):
                continue
            for feeding in feedings:
                if not isinstance(feeding, dict):
                    continue
                occurred_at = _parsed_timestamp(feeding.get("occurredAt"))
                portions = feeding.get("portions")
                if (
                    occurred_at is None
                    or not isinstance(portions, int)
                    or isinstance(portions, bool)
                    or not 1 <= portions <= 48
                ):
                    continue
                events.append(
                    {
                        "kind": "scheduled_feeding",
                        "occurredAt": occurred_at.isoformat(timespec="seconds").replace(
                            "+00:00", "Z"
                        ),
                        "site": site,
                        "sites": [site],
                        "location": SITE_NAMES[site],
                        "title": "Scheduled feeding",
                        "detail": f"{portions} portion{'s' if portions != 1 else ''} dispensed",
                    }
                )

    automation_data = automation if isinstance(automation, dict) else {}
    transfers = automation_data.get("cat_transfers")
    recent_transfers = transfers.get("recent") if isinstance(transfers, dict) else None
    if isinstance(recent_transfers, list):
        for transfer in recent_transfers:
            if not isinstance(transfer, dict):
                continue
            origin = transfer.get("origin_site")
            destination = transfer.get("destination_site")
            occurred_at = _parsed_timestamp(transfer.get("occurred_at"))
            if (
                origin not in SITE_NAMES
                or destination not in SITE_NAMES
                or origin == destination
                or occurred_at is None
            ):
                continue
            events.append(
                {
                    "kind": "cat_move",
                    "occurredAt": occurred_at.isoformat(timespec="seconds").replace(
                        "+00:00", "Z"
                    ),
                    "site": destination,
                    "sites": [origin, destination],
                    "location": f"{SITE_NAMES[origin]} → {SITE_NAMES[destination]}",
                    "title": f"Cats moved to {SITE_NAMES[destination]}",
                    "detail": f"{SITE_NAMES[origin]} scheduled meals paused",
                }
            )

    events.sort(
        key=lambda event: _parsed_timestamp(event.get("occurredAt"))
        or datetime.min.replace(tzinfo=timezone.utc),
        reverse=True,
    )
    output: list[dict[str, object]] = []
    seen: set[tuple[object, ...]] = set()
    for event in events:
        key = (
            event["kind"],
            event["occurredAt"],
            event["site"],
            event["title"],
            event["detail"],
        )
        if key in seen:
            continue
        seen.add(key)
        output.append(event)
        if len(output) >= CAT_ACTIVITY_LIMIT:
            break
    return output


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
        bundle["transfer_summary"] = summarize_transfer_state(
            bundle["automation"], bundle["transfer"], bundle["petlibro"]
        )
        bundle["activity"] = build_cat_activity(
            bundle["whisker"], bundle["petlibro"], bundle["automation"]
        )

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
  <link rel="icon" sizes="any" href="data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 100 100'%3E%3Ctext y='.9em' font-size='90'%3E%F0%9F%90%B1%3C/text%3E%3C/svg%3E">
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
    .cat-card { min-height: 218px; position: relative; overflow: hidden; }
    .cat-card::after { content: ""; position: absolute; z-index: 0; width: 115px; height: 115px; right: -35px; bottom: -45px; border: 26px solid rgba(149,213,178,.08); border-radius: 50%; }
    .cat-card > * { position: relative; z-index: 1; }
    .weight-chart { margin-top: 13px; }
    .weight-chart svg { display: block; width: 100%; height: 48px; overflow: visible; }
    .weight-grid { stroke: rgba(255,255,255,.08); stroke-width: 1; }
    .weight-line { fill: none; stroke: var(--mint); stroke-width: 2.25; stroke-linecap: round; stroke-linejoin: round; vector-effect: non-scaling-stroke; }
    .weight-dot { fill: var(--mint); stroke: var(--panel-2); stroke-width: 1.5; vector-effect: non-scaling-stroke; }
    .weight-chart-meta { display: flex; justify-content: space-between; gap: 8px; color: var(--muted); font-size: 11px; font-variant-numeric: tabular-nums; }
    .weight-chart-empty { height: 67px; display: flex; align-items: center; color: var(--muted); font-size: 12px; }
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
    .automation-card h3 { margin: 4px 0 0; font-size: clamp(22px, 3vw, 31px); letter-spacing: -.02em; }
    .automation-card .metric-row { grid-template-columns: repeat(4, 1fr); }
    .automation-copy { max-width: 850px; margin: 9px 0 0; color: var(--muted); }
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
    .event { display: grid; grid-template-columns: 90px minmax(140px, .7fr) 1fr; gap: 18px; padding: 13px 0; border-bottom: 1px solid var(--line); align-items: center; }
    .event:last-child { border-bottom: 0; }
    .event time { color: var(--muted); font-variant-numeric: tabular-nums; }
    .event-action { display: flex; flex-wrap: wrap; gap: 6px 10px; align-items: baseline; }
    .event-action span { color: var(--muted); }
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

    <section><div class="section-head"><h2>Feeding between homes</h2><span class="muted" id="automation-summary"></span></div><div class="grid" id="automation"></div></section>
    <section><div class="section-head"><h2>The cats</h2><span class="muted" id="cat-summary"></span></div><div class="grid" id="cats"></div></section>
    <section><div class="section-head"><h2>Care stations</h2><span class="muted">Whisker · Petlibro</span></div><div class="grid" id="devices"></div></section>
    <section><div class="section-head"><h2>Cat activity</h2><span class="muted">Litter visits · scheduled feedings · home moves</span></div><div class="grid" id="activity"></div></section>
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
      const samples = weightSamples(pet);
      if (samples.length < 2) return 'No trend yet';
      const delta = Number(samples.at(-1).weight_lbs) - Number(samples[0].weight_lbs);
      if (Math.abs(delta) < .05) return 'Steady';
      return `${delta > 0 ? '+' : ''}${delta.toFixed(1)} lb recent`;
    }

    function weightSamples(pet) {
      return [...(pet.recent_weights || [])]
        .filter(sample => Number.isFinite(Number(sample.weight_lbs)) && Number(sample.weight_lbs) > 0 && Number(sample.weight_lbs) <= 40 && Number.isFinite(new Date(sample.timestamp).getTime()))
        .sort((a, b) => new Date(a.timestamp) - new Date(b.timestamp));
    }

    function weightSparkline(pet) {
      const samples = weightSamples(pet);
      if (samples.length < 2) return '<div class="weight-chart-empty">More readings needed for a weight trend.</div>';
      const width = 240;
      const height = 48;
      const inset = 4;
      const weights = samples.map(sample => Number(sample.weight_lbs));
      const times = samples.map(sample => new Date(sample.timestamp).getTime());
      const observedMin = Math.min(...weights);
      const observedMax = Math.max(...weights);
      const midpoint = (observedMin + observedMax) / 2;
      const span = Math.max(observedMax - observedMin, .5);
      const low = midpoint - span / 2;
      const high = midpoint + span / 2;
      const firstTime = times[0];
      const elapsed = Math.max(times.at(-1) - firstTime, 1);
      const points = samples.map((sample, index) => {
        const x = inset + ((times[index] - firstTime) / elapsed) * (width - inset * 2);
        const y = inset + ((high - weights[index]) / (high - low)) * (height - inset * 2);
        return `${x.toFixed(1)},${y.toFixed(1)}`;
      }).join(' ');
      const lastPoint = points.split(' ').at(-1).split(',');
      const dateLabel = timestamp => new Intl.DateTimeFormat(undefined, {month: 'short', day: 'numeric'}).format(new Date(timestamp));
      const range = `${observedMin.toFixed(1)}–${observedMax.toFixed(1)} lb`;
      const description = `${pet.name || 'Cat'} weight from ${weights[0].toFixed(2)} to ${weights.at(-1).toFixed(2)} pounds across ${samples.length} readings`;
      return `<div class="weight-chart"><svg viewBox="0 0 ${width} ${height}" role="img" aria-label="${esc(description)}" preserveAspectRatio="none"><line class="weight-grid" x1="${inset}" y1="${height / 2}" x2="${width - inset}" y2="${height / 2}"></line><polyline class="weight-line" points="${points}"></polyline><circle class="weight-dot" cx="${lastPoint[0]}" cy="${lastPoint[1]}" r="3"></circle></svg><div class="weight-chart-meta"><span>${esc(dateLabel(samples[0].timestamp))}</span><span>${esc(`${samples.length} readings · ${range}`)}</span><span>${esc(dateLabel(samples.at(-1).timestamp))}</span></div></div>`;
    }

    function renderAutomation() {
      const root = document.getElementById('automation');
      const summary = state?.transfer_summary || {};
      const sites = summary.sites || {};
      const pillClass = summary.tone === 'bad' ? 'bad' : summary.tone === 'warn' ? 'warn' : '';
      document.getElementById('automation-summary').textContent = summary.summary || 'Status unavailable';
      root.innerHTML = `<article class="card automation-card">
        <div class="card-top"><div><div class="label">Automatic feeder switching</div><h3>${esc(summary.title || 'Cat feeding status is unavailable')}</h3></div><span class="pill ${pillClass}">${esc(summary.label || 'Unavailable')}</span></div>
        <p class="automation-copy">${esc(summary.description || 'OpenClaw could not confirm the current feeder state. No automatic change will be made.')}</p>
        <div class="metric-row">${metric('Cabin meals', sites.cabin?.label || 'Checking')}${metric('Crosstown meals', sites.crosstown?.label || 'Checking')}${metric('Litter boxes', summary.litter_boxes || 'Waiting for data')}${metric('Changes waiting', Number(summary.pending_changes) > 0 ? number(summary.pending_changes) : 'None')}</div>
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
        ${weightSparkline(pet)}
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
      const scheduleKnown = typeof device.scheduleEnabled === 'boolean';
      const scheduleEnabled = device.scheduleEnabled === true;
      const metrics = feeder
        ? metric('Food', device.foodLevel || '—') + metric('Next meal', scheduleKnown ? (scheduleEnabled ? device.nextFeedTime || '—' : 'Paused') : '—') + metric('Portions', scheduleKnown && scheduleEnabled ? number(device.nextFeedPortions) : '—')
        : metric('Water', device.waterPercent === '?' ? '—' : `${number(device.waterPercent)}%`) + metric('Today', device.todayDrinkMl === '?' ? '—' : `${number(device.todayDrinkMl)} ml`) + metric('Filter', device.filterDaysRemaining === '?' ? '—' : `${number(device.filterDaysRemaining)} d`);
      const enrolledFeeder = feeder && ['crosstown-feeder','cabin-feeder'].includes(selector);
      const action = enrolledFeeder
        ? `<div class="actions"><select aria-label="Portions" data-portions-for="${esc(selector)}"><option value="1">1 portion</option><option value="2">2 portions</option><option value="3">3 portions</option></select><button class="action" ${device.online ? '' : 'disabled'} data-command="feed" data-selector="${esc(selector)}">Feed now</button></div>` : '';
      const managed = state?.automation?.feeder_suspensions?.sites?.[site];
      const managedHere = managed?.selector === selector;
      const recoveredManagedReadback = managedHere && managed.last_error === 'feeder_readback_unavailable' && ((managed.phase === 'suspended' && scheduleKnown && !scheduleEnabled) || (managed.phase === 'restoring' && scheduleKnown && scheduleEnabled));
      const managedAttention = managedHere && managed.attention === true && !recoveredManagedReadback;
      const managedWaiting = managedHere && managed.waiting_reason === 'cat_transfer_not_settled';
      const automationOwned = state?.automation?.feeding_schedule_owners?.[site] === 'bus';
      const enabledMeals = Number.isInteger(device.enabledMealCount) ? device.enabledMealCount : null;
      const mealText = enabledMeals === null ? '' : `${enabledMeals} active meal${enabledMeals === 1 ? '' : 's'}`;
      const verifiedAt = ['verified','master_verified'].includes(device.scheduleReadback) && device.scheduleObservedAt ? new Date(device.scheduleObservedAt) : null;
      const verificationLabel = device.scheduleReadback === 'master_verified' ? 'On/off switch checked' : 'Schedule checked';
      const verifiedText = verifiedAt && !Number.isNaN(verifiedAt.getTime()) ? `${verificationLabel} ${verifiedAt.toLocaleTimeString([], {hour:'numeric',minute:'2-digit'})}` : 'Schedule check unavailable';
      const scheduleLabel = scheduleKnown ? (scheduleEnabled ? `On${mealText ? ` · ${mealText}` : ''}` : `Paused${mealText ? ` · ${mealText}` : ''}`) : 'Unavailable';
      const ownershipText = managedAttention ? 'Automatic pause needs review' : managedWaiting && scheduleEnabled ? 'Meals restored · Waiting for litter-box confirmation' : managedHere && managed.phase === 'restoring' ? 'Turning scheduled meals back on' : managedHere ? 'Paused while home is vacant · Turns back on automatically' : automationOwned ? 'Will pause automatically when the cats move homes' : 'Controlled manually';
      const scheduleClass = managedAttention ? 'bad' : scheduleKnown && scheduleEnabled ? '' : 'warn';
      const schedule = enrolledFeeder ? `<div class="actions schedule-actions"><div class="schedule-label"><span class="label">Scheduled meals</span><span class="pill ${scheduleClass}">${esc(scheduleLabel)}</span><span class="schedule-owner">${esc(verifiedText)} · ${esc(ownershipText)}</span></div><button class="action" ${device.online && scheduleKnown && !managedHere ? '' : 'disabled'} data-command="schedule" data-state="${scheduleEnabled ? 'off' : 'on'}" data-selector="${esc(selector)}">${managedHere ? 'Managed automatically' : scheduleEnabled ? 'Pause schedule' : 'Resume schedule'}</button></div>` : '';
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
      const events = (state?.activity || []).filter(event => selectedSite === 'all' || (event.sites || [event.site]).includes(selectedSite));
      document.getElementById('activity').innerHTML = events.length ? `<div class="card timeline">${events.map(event => {
        const date = new Date(event.occurredAt); const when = Number.isNaN(date.getTime()) ? 'Unknown' : date.toLocaleTimeString([], {hour:'numeric',minute:'2-digit'});
        const day = Number.isNaN(date.getTime()) ? '' : date.toLocaleDateString([], {month:'short',day:'numeric'});
        return `<div class="event"><time>${esc(when)}<br><span>${esc(day)}</span></time><strong>${esc(event.location || siteName(event.site))}</strong><span class="event-action"><b>${esc(event.title || 'Cat activity')}</b>${event.detail ? `<span>${esc(event.detail)}</span>` : ''}</span></div>`;
      }).join('')}</div>` : '<div class="empty">No recent cat activity is available for this location.</div>';
    }

    function renderNotice() {
      const messages = [];
      if (state?.whisker?.error) messages.push(`Whisker: ${state.whisker.error}`);
      if (state?.petlibro?.error) messages.push(`Petlibro: ${state.petlibro.error}`);
      if (state?.automation?.ok === false) messages.push(`Feeder automation: ${state.automation.error || 'status unavailable'}`);
      if (state?.transfer?.ok === false) messages.push(`Cat transfer coverage: ${state.transfer.error || 'status unavailable'}`);
      if (state?.transfer?.ok === true && state.transfer.bus_health !== 'ok') messages.push('Home event bus has degraded health outside feeder transfer coverage.');
      for (const site of ['cabin', 'crosstown']) {
        const owner = state?.automation?.feeding_schedule_owners?.[site];
        const coverage = state?.transfer?.sites?.[site];
        const age = Number(coverage?.poll_age_seconds);
        if (owner && owner !== 'bus') messages.push(`${siteName(site)} automatic feeder switching is off.`);
        if (coverage && (coverage.enabled !== true || coverage.baselined !== true || coverage.health !== 'ok' || coverage.poll_age_seconds === null || !Number.isFinite(age) || age > 300)) messages.push(`${siteName(site)} Litter-Robot has not reported fresh data.`);
      }
      if (state?.transfer_summary?.attention && state.transfer_summary.notice) messages.push(state.transfer_summary.notice);
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
    load(true); setInterval(() => load(), 60000);
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
