#!/usr/bin/env python3
"""Validate owner-scoped morning triage handoffs and present safe reminders."""

from __future__ import annotations

import json
import os
import re
import sqlite3
import stat
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo


TIME_ZONE = ZoneInfo("America/New_York")
HANDOFF_LIMIT_BYTES = 128 * 1024
REVIEW_LIMIT = 20
HANDOFF_COUNTERS = (
    "processed", "markedRead", "leftUnread", "draftsCreated", "draftsExisting",
    "archived", "trashed",
)


def clean_text(value: object, limit: int) -> str:
    normalized = " ".join(str(value).split())
    if len(normalized) <= limit:
        return normalized
    return f"{normalized[: limit - 1]}…"


def strip_json_fence(value: str) -> str:
    text = value.strip()
    match = re.fullmatch(r"```(?:json)?\s*(.*?)\s*```", text, flags=re.DOTALL)
    return match.group(1).strip() if match else text


def safe_nonnegative_int(value: object) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else 0


def validate_handoff(payload: object, today: datetime) -> dict[str, object]:
    if not isinstance(payload, dict) or type(payload.get("schemaVersion")) is not int:
        raise ValueError("invalid_handoff")
    version = payload["schemaVersion"]
    if version not in (1, 2) or payload.get("date") != today.date().isoformat():
        raise ValueError("invalid_handoff")
    if not isinstance(payload.get("unreadAfter"), list) or not all(
        isinstance(item, str) and item for item in payload["unreadAfter"]
    ) or len(payload["unreadAfter"]) > 5000:
        raise ValueError("invalid_handoff")
    if not isinstance(payload.get("attention"), list) or not all(
        isinstance(item, dict) for item in payload["attention"]
    ):
        raise ValueError("invalid_handoff")
    if version == 1:
        return payload
    required = {
        "schemaVersion", "date", "status", "unreadAfter", "attention", "errors",
        "review", "actionReview", "cleanupVerified", "unreadSnapshotVerified", *HANDOFF_COUNTERS,
    }
    if set(payload) != required or payload.get("status") not in ("ok", "partial", "auth_error"):
        raise ValueError("invalid_handoff")
    if any(type(payload.get(key)) is not int or payload[key] < 0 for key in HANDOFF_COUNTERS):
        raise ValueError("invalid_handoff")
    if payload["leftUnread"] != len(set(payload["unreadAfter"])) or len(set(payload["unreadAfter"])) != len(payload["unreadAfter"]):
        raise ValueError("invalid_handoff")
    if payload["markedRead"] > payload["processed"]:
        raise ValueError("invalid_handoff")
    attention_keys = {"messageId", "threadId", "from", "subject", "reason", "deadline", "draftStatus"}
    if len(payload["attention"]) > 100:
        raise ValueError("invalid_handoff")
    for item in payload["attention"]:
        if set(item) != attention_keys or not all(isinstance(value, str) and len(value) <= 700 for value in item.values()):
            raise ValueError("invalid_handoff")
        if not item["messageId"] or not item["threadId"] or item["draftStatus"] not in ("none", "existing", "created"):
            raise ValueError("invalid_handoff")
    errors = payload.get("errors")
    if not isinstance(errors, list) or len(errors) > 100 or not all(isinstance(error, str) and len(error) <= 700 for error in errors):
        raise ValueError("invalid_handoff")
    if payload["status"] == "ok" and errors:
        raise ValueError("invalid_handoff")
    if payload["status"] != "ok" and not errors:
        raise ValueError("invalid_handoff")
    if payload["status"] == "auth_error" and (any(payload[key] for key in HANDOFF_COUNTERS) or payload["attention"]):
        raise ValueError("invalid_handoff")
    review = payload.get("review")
    review_keys = {"mode", "status", "inboxTotal", "inboxUnread", "actionMessageCount", "inventoryComplete", "errors"}
    if not isinstance(review, dict) or set(review) != review_keys or review.get("mode") != "preview":
        raise ValueError("invalid_handoff")
    if review.get("status") not in ("ok", "partial", "unavailable"):
        raise ValueError("invalid_handoff")
    for key in ("inboxTotal", "inboxUnread", "actionMessageCount"):
        if review[key] is not None and (type(review[key]) is not int or review[key] < 0):
            raise ValueError("invalid_handoff")
    if type(review["inventoryComplete"]) is not bool or type(payload["unreadSnapshotVerified"]) is not bool:
        raise ValueError("invalid_handoff")
    if payload["cleanupVerified"] is not None and type(payload["cleanupVerified"]) is not bool:
        raise ValueError("invalid_handoff")
    if review["status"] == "ok" and (not review["inventoryComplete"] or any(review[key] is None for key in ("inboxTotal", "inboxUnread", "actionMessageCount"))):
        raise ValueError("invalid_handoff")
    review_errors = review["errors"]
    if not isinstance(review_errors, list) or len(review_errors) > 20 or not all(
        isinstance(error, str) and 0 < len(error) <= 700 for error in review_errors
    ):
        raise ValueError("invalid_handoff")
    if (review["status"] == "ok") != (not review_errors):
        raise ValueError("invalid_handoff")
    if payload["status"] == "ok" and (payload["cleanupVerified"] is not True or not payload["unreadSnapshotVerified"]):
        raise ValueError("invalid_handoff")
    if payload["status"] == "auth_error" and (
        review["status"] != "unavailable" or review["inventoryComplete"]
        or payload["cleanupVerified"] is not None or payload["unreadSnapshotVerified"]
        or payload["actionReview"] or any(review[key] is not None for key in (
            "inboxTotal", "inboxUnread", "actionMessageCount",
        ))
    ):
        raise ValueError("invalid_handoff")
    entries = payload["actionReview"]
    if not isinstance(entries, list) or len(entries) > REVIEW_LIMIT:
        raise ValueError("invalid_handoff")
    if review["status"] == "unavailable" and (
        entries or review["inventoryComplete"]
        or any(review[key] is not None for key in ("inboxTotal", "inboxUnread", "actionMessageCount"))
    ):
        raise ValueError("invalid_handoff")
    if not review["inventoryComplete"] and review["actionMessageCount"] is not None:
        raise ValueError("invalid_handoff")
    if review["actionMessageCount"] is not None and len(entries) > review["actionMessageCount"]:
        raise ValueError("invalid_handoff")
    seen = set()
    for item in entries:
        if not isinstance(item, dict) or item.get("state") not in {"needs_you", "waiting", "resolved_candidate", "needs_context"}:
            raise ValueError("invalid_handoff")
        if set(item) != {"messageId", "threadId", "subject", "reason", "nextStep", "deadline", "activityKey", "state", "ageDays", "urgent", "protected", "draftStatus"}:
            raise ValueError("invalid_handoff")
        for key in ("messageId", "threadId", "subject", "reason", "nextStep", "deadline", "activityKey"):
            if not isinstance(item.get(key), str) or len(item[key]) > 700:
                raise ValueError("invalid_handoff")
        if not item["messageId"] or not item["threadId"] or not item["reason"] or not item["nextStep"]:
            raise ValueError("invalid_handoff")
        if item["threadId"] in seen or not re.fullmatch(r"[a-f0-9]{64}", item["activityKey"]):
            raise ValueError("invalid_handoff")
        seen.add(item["threadId"])
        if type(item.get("ageDays")) is not int or item["ageDays"] < 0:
            raise ValueError("invalid_handoff")
        if any(type(item.get(key)) is not bool for key in ("urgent", "protected")):
            raise ValueError("invalid_handoff")
        if item.get("draftStatus") not in ("none", "existing", "created"):
            raise ValueError("invalid_handoff")
        if item["deadline"]:
            try:
                parsed_date = datetime.strptime(item["deadline"], "%Y-%m-%d")
            except ValueError:
                raise ValueError("invalid_handoff") from None
            if parsed_date.strftime("%Y-%m-%d") != item["deadline"]:
                raise ValueError("invalid_handoff")
    return payload


def review_presentation(items, previous, today, *, attention=(), previous_attention=()):
    old_items = {item["threadId"]: item for item in previous}
    presented = []
    for item in items:
        old = old_items.get(item["threadId"])
        fields = ("activityKey", "state", "deadline", "draftStatus", "urgent")
        unchanged = old is not None and all(old.get(key) == item.get(key) for key in fields)
        confirmed = [entry for entry in attention if entry.get("threadId") == item["threadId"]]
        old_confirmed = [entry for entry in previous_attention if entry.get("threadId") == item["threadId"]]
        unchanged = unchanged and confirmed == old_confirmed
        due = bool(item["deadline"]) and item["deadline"] <= (today.date() + timedelta(days=1)).isoformat()
        for entry in confirmed:
            deadline = entry.get("deadline", "")
            if deadline:
                try:
                    confirmed_date = datetime.strptime(deadline, "%Y-%m-%d").date()
                    due = due or confirmed_date <= today.date() + timedelta(days=1)
                except ValueError:
                    due = True
        needs_disposition = unchanged and item["ageDays"] >= 7 and today.weekday() == 0
        group = "urgent_or_due" if item["urgent"] or due else "decision_needed" if needs_disposition else "unchanged" if unchanged else "new_or_changed"
        presented.append({
            **{key: item[key] for key in ("subject", "reason", "nextStep", "deadline", "draftStatus", "state", "ageDays")},
            "group": group,
        })
    return presented


def load_triage_handoff(
    today: datetime,
    *,
    db_path: Path,
    job_id: str,
    store_key: str,
) -> tuple[dict[str, object], set[str] | None]:
    try:
        connection = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        try:
            rows = connection.execute(
                """SELECT run_at_ms, summary, status FROM cron_run_logs
                   WHERE store_key = ? AND job_id = ?
                   ORDER BY run_at_ms DESC LIMIT 50""",
                (store_key, job_id),
            ).fetchall()
        finally:
            connection.close()
    except (OSError, sqlite3.Error):
        return {"status": "unavailable", "reason": "database_unavailable"}, None

    today_date = today.date()
    for row_index, (run_at_ms, summary, run_status) in enumerate(rows):
        if not isinstance(run_at_ms, int):
            continue
        run_date = datetime.fromtimestamp(run_at_ms / 1000, TIME_ZONE).date()
        if run_date != today_date:
            if run_date < today_date:
                break
            continue
        if run_status != "ok":
            return {"status": "unavailable", "reason": "triage_run_failed"}, None
        if not isinstance(summary, str) or len(summary.encode("utf-8")) > HANDOFF_LIMIT_BYTES:
            return {"status": "unavailable", "reason": "invalid_handoff"}, None
        try:
            payload = validate_handoff(json.loads(strip_json_fence(summary)), today)
        except (ValueError, TypeError):
            return {"status": "unavailable", "reason": "invalid_handoff"}, None

        unread_raw = payload.get("unreadAfter")
        if not isinstance(unread_raw, list) or not all(
            isinstance(item, str) for item in unread_raw
        ):
            continue
        attention_raw = payload.get("attention")
        if not isinstance(attention_raw, list):
            continue

        attention: list[dict[str, str]] = []
        for item in attention_raw:
            if not isinstance(item, dict):
                continue
            attention.append(
                {
                    "from": clean_text(item.get("from", ""), 320),
                    "subject": clean_text(item.get("subject", ""), 500),
                    "reason": clean_text(item.get("reason", ""), 700),
                    "deadline": clean_text(item.get("deadline", ""), 160),
                    "draftStatus": clean_text(item.get("draftStatus", "none"), 16),
                }
            )

        errors = payload.get("errors")
        error_count = len(errors) if isinstance(errors, list) else 0
        handoff_status = clean_text(payload.get("status", "unknown"), 24)
        baseline_verified = handoff_status == "ok"
        review_fields = {}
        if payload["schemaVersion"] == 2:
            baseline_verified = payload["unreadSnapshotVerified"]
            previous = []
            previous_attention = []
            for previous_ms, previous_summary, previous_status in rows[row_index + 1:]:
                if not isinstance(previous_ms, int):
                    break
                previous_date = datetime.fromtimestamp(previous_ms / 1000, TIME_ZONE)
                if previous_date.date() >= today_date:
                    continue
                if previous_date.date() != today_date - timedelta(days=1):
                    break
                if previous_status != "ok" or not isinstance(previous_summary, str) or len(previous_summary.encode("utf-8")) > HANDOFF_LIMIT_BYTES:
                    break
                try:
                    older = validate_handoff(json.loads(strip_json_fence(previous_summary)), previous_date)
                except (ValueError, TypeError):
                    break
                if older.get("schemaVersion") == 2 and older.get("status") == "ok":
                    previous = older["actionReview"]
                    previous_attention = older["attention"]
                break
            presentations = review_presentation(
                payload["actionReview"], previous, today,
                attention=attention_raw, previous_attention=previous_attention,
            )
            groups = {
                item["threadId"]: presented["group"]
                for item, presented in zip(payload["actionReview"], presentations)
            }
            confirmed_threads = {item["threadId"] for item in attention_raw}
            attention = [
                {**cleaned, "group": groups.get(item["threadId"], "new_or_changed")}
                for item, cleaned in zip(attention_raw, attention)
            ]
            review_fields = {
                "cleanupVerified": payload["cleanupVerified"],
                "unreadSnapshotVerified": baseline_verified,
                "outcomes": {
                    **{key: value for key, value in payload["review"].items() if key != "errors"},
                    "errorCount": len(payload["review"]["errors"]),
                },
                "actionReview": [
                    presented for item, presented in zip(payload["actionReview"], presentations)
                    if item["threadId"] not in confirmed_threads
                ],
            }
        return (
            {
                "status": "ok",
                "handoffStatus": handoff_status,
                "processed": safe_nonnegative_int(payload.get("processed")),
                "markedRead": safe_nonnegative_int(payload.get("markedRead")),
                "leftUnread": safe_nonnegative_int(payload.get("leftUnread")),
                "draftsCreated": safe_nonnegative_int(payload.get("draftsCreated")),
                "draftsExisting": safe_nonnegative_int(payload.get("draftsExisting")),
                "archived": safe_nonnegative_int(payload.get("archived")),
                "trashed": safe_nonnegative_int(payload.get("trashed")),
                "errorCount": error_count,
                "attention": attention,
                **review_fields,
            },
            set(unread_raw) if baseline_verified else None,
        )
    return {"status": "unavailable", "reason": "same_day_handoff_missing"}, None


def validate_handoff_file(path: Path) -> dict[str, object]:
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(descriptor, "rb") as source:
        metadata = os.fstat(source.fileno())
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_uid != os.getuid() or stat.S_IMODE(metadata.st_mode) != 0o600 or metadata.st_size > HANDOFF_LIMIT_BYTES:
            raise ValueError("invalid_handoff_file")
        content = source.read(HANDOFF_LIMIT_BYTES + 1)
    if len(content) > HANDOFF_LIMIT_BYTES:
        raise ValueError("invalid_handoff_file")
    payload = validate_handoff(json.loads(content), datetime.now(TIME_ZONE))
    if payload["schemaVersion"] != 2:
        raise ValueError("invalid_handoff")
    return payload
