#!/usr/bin/env python3
"""Collect a bounded, read-only preview of Julia's inbox review queues."""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import sys
import time
from datetime import datetime
from pathlib import Path


SPEC = importlib.util.spec_from_file_location(
    "julia_review_briefing", Path(__file__).with_name("julia-morning-briefing-data.py")
)
assert SPEC and SPEC.loader
briefing = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = briefing
SPEC.loader.exec_module(briefing)

REVIEW_LIMIT = 20
SCAN_LIMIT = 1000
PRIMARY_LABELS = ("Urgent", "Action", "FYI", "Financial", "Shopping", "Newsletters", "Social")
BACKLOG_QUERY = "in:inbox is:read " + " ".join(
    f'-label:"OpenClaw/{label}"' for label in PRIMARY_LABELS
)
ACTION_QUERY = 'in:inbox {label:"OpenClaw/Action" label:"OpenClaw/Urgent"}'


def collect_review(*, scope="actions", now=None, runner=None, clock=time.monotonic):
    if scope not in ("actions", "backlog"):
        raise ValueError("invalid_scope")
    local_now = now.astimezone(briefing.TIME_ZONE) if now else datetime.now(briefing.TIME_ZONE)
    deadline = clock() + briefing.OVERALL_TIMEOUT_SECONDS
    result = {
        "schemaVersion": 1,
        "date": local_now.date().isoformat(),
        "mode": "preview",
        "scope": scope,
        "status": "ok",
        "metrics": {"inboxTotal": None, "inboxUnread": None},
        "queue": {"count": None, "complete": False, "candidates": [], "hasMoreCandidates": True},
        "errors": [],
    }
    if not briefing.ACCOUNT.strip():
        result.update(status="unavailable", errors=["missing_account"])
        return result

    def read(resource, method, params):
        if (resource, method) not in {
            ("labels", "list"), ("labels", "get"),
            ("messages", "list"), ("threads", "get"),
        }:
            raise ValueError("read-only boundary")
        remaining = deadline - clock()
        if remaining <= 0:
            raise ValueError("timeout")
        response = (runner or briefing.run_command)(
            [briefing.GWS_BIN, "gmail", "users", resource, method, "--params",
             json.dumps({"userId": "me", **params})],
            briefing.gws_environment(), min(briefing.COMMAND_TIMEOUT_SECONDS, remaining),
        )
        payload, error = briefing.parse_object(response)
        if error:
            raise ValueError(error)
        return payload

    def inventory(query):
        messages = []
        seen_ids = set()
        seen_tokens = set()
        params = {"q": query, "maxResults": 100}
        for _ in range(SCAN_LIMIT // 100):
            page = read("messages", "list", params)
            entries = page.get("messages", [])
            if not isinstance(entries, list) or len(entries) > 100:
                raise ValueError("invalid_response")
            for entry in entries:
                if not isinstance(entry, dict) or not all(
                    isinstance(entry.get(key), str) and entry[key]
                    for key in ("id", "threadId")
                ):
                    raise ValueError("invalid_response")
                if entry["id"] not in seen_ids:
                    seen_ids.add(entry["id"])
                    messages.append(entry)
            token = page.get("nextPageToken")
            if not token:
                return messages, True
            if not isinstance(token, str) or token in seen_tokens:
                raise ValueError("invalid_pagination")
            seen_tokens.add(token)
            params["pageToken"] = token
        return messages, False

    try:
        labels = read("labels", "list", {}).get("labels", [])
        if not isinstance(labels, list) or not all(
            isinstance(label, dict)
            and isinstance(label.get("id"), str)
            and isinstance(label.get("name"), str)
            for label in labels
        ):
            raise ValueError("invalid_response")
        names = {label["id"]: label["name"] for label in labels}
        inbox = read("labels", "get", {"id": "INBOX"})
        for source, target in (("messagesTotal", "inboxTotal"), ("messagesUnread", "inboxUnread")):
            value = inbox.get(source)
            if type(value) is not int or value < 0:
                raise ValueError("invalid_response")
            result["metrics"][target] = value
        entries, complete = inventory(ACTION_QUERY if scope == "actions" else BACKLOG_QUERY)
        result["queue"]["count"] = len(entries) if complete else None
        result["queue"]["complete"] = complete
        seen_threads = set()
        for entry in entries:
            if entry["threadId"] in seen_threads:
                continue
            if len(seen_threads) >= REVIEW_LIMIT:
                break
            thread = read("threads", "get", {"id": entry["threadId"], "format": "metadata"})
            messages = thread.get("messages", [])
            if not isinstance(messages, list) or not messages:
                raise ValueError("invalid_response")
            candidate = summarize_thread(entry, messages, names, local_now)
            result["queue"]["candidates"].append(candidate)
            seen_threads.add(entry["threadId"])
        result["queue"]["hasMoreCandidates"] = len({entry["threadId"] for entry in entries}) > len(seen_threads) or not complete
        if not complete:
            result["status"] = "partial"
            result["errors"].append("inventory_truncated")
    except (ValueError, KeyError, TypeError, AttributeError, OverflowError, OSError) as error:
        allowed = {"auth_error", "token_error", "timeout", "command_unavailable", "command_error", "api_error", "invalid_response", "invalid_pagination"}
        result["status"] = "partial"
        result["errors"].append(str(error) if str(error) in allowed else "invalid_response")
    return result


def summarize_thread(entry, messages, names, now):
    ordered = sorted(messages, key=lambda message: int(message.get("internalDate", 0)))
    original = next((message for message in ordered if message.get("id") == entry["id"]), None)
    if original is None:
        raise ValueError("invalid_response")
    non_drafts = [message for message in ordered if "DRAFT" not in message.get("labelIds", [])]
    if not non_drafts:
        raise ValueError("invalid_response")
    latest = non_drafts[-1]
    summaries = []
    for message in non_drafts[-3:]:
        headers = {
            header["name"].lower(): header["value"]
            for header in message.get("payload", {}).get("headers", [])
        }
        summaries.append({
            "from": briefing.clean_text(headers.get("from", ""), 120),
            "subject": briefing.clean_text(headers.get("subject", ""), 180),
            "snippet": briefing.clean_text(message.get("snippet", ""), 240),
            "sentByJulia": "SENT" in message.get("labelIds", []),
        })
    labels = {names.get(label, label) for message in ordered for label in message.get("labelIds", [])}
    activity = [
        [message.get("id"), "DRAFT" in message.get("labelIds", [])]
        for message in ordered
    ]
    return {
        "messageId": entry["id"], "threadId": entry["threadId"],
        "activityKey": hashlib.sha256(json.dumps(activity).encode()).hexdigest(),
        "ageDays": max(0, int((now.timestamp() * 1000 - int(original["internalDate"])) / 86400000)),
        "lastActivityDate": datetime.fromtimestamp(int(latest["internalDate"]) / 1000, briefing.TIME_ZONE).date().isoformat(),
        "starred": "STARRED" in labels,
        "protected": bool(labels & {"STARRED", "OpenClaw/Urgent", "OpenClaw/Action", "DRAFT"}),
        "urgent": "OpenClaw/Urgent" in labels,
        "draftCount": sum("DRAFT" in message.get("labelIds", []) for message in ordered),
        "latestNonDraftSentByJulia": "SENT" in latest.get("labelIds", []),
        "recentMessages": summaries,
        "contextLimited": len(non_drafts) > len(summaries),
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scope", choices=("actions", "backlog"), default="actions")
    args = parser.parse_args(argv)
    try:
        with briefing.termination_signal_handlers():
            result = collect_review(scope=args.scope)
    except Exception:
        result = {"schemaVersion": 1, "mode": "preview", "status": "unavailable", "errors": ["internal_error"]}
    print(json.dumps(result, separators=(",", ":"), ensure_ascii=False))
    return 0 if result["status"] == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())
