#!/usr/bin/env python3
"""Act on authenticated Resy Notify email within an explicitly approved scope.

Gmail is checked periodically; Resy is contacted only for a fresh matching email.
No email instructions or URLs are executed. An unresolved mutation stops the job.
"""
from __future__ import annotations

import argparse
import base64
import contextlib
import copy
import fcntl
import hashlib
import html
import importlib.machinery
import importlib.util
import json
import os
from pathlib import Path
import re
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from email.utils import parseaddr

ROOT = Path.home() / "dotfiles"
NODE = Path.home() / ".local/share/openclaw/runtimes/node-v24.22.1-darwin-arm64/bin/node"


class Stop(RuntimeError):
    """Only fixed, non-sensitive reason codes may be used here."""


def load(name, path):
    loader = importlib.machinery.SourceFileLoader(name, str(path))
    spec = importlib.util.spec_from_loader(name, loader)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    loader.exec_module(module)
    return module


book = load("notify_booking_facts", ROOT / "openclaw/bin/restaurant-book.py")
snipe = load("notify_state_helpers", ROOT / "openclaw/skills/restaurant-snipe/scripts/restaurant_snipe.py")


def now():
    return datetime.now(timezone.utc)


def timestamp(value):
    return snipe.parse_timestamp(value, "deadline")


def digest(value):
    return hashlib.sha256(str(value).encode()).hexdigest()


def scope_digest(scope):
    return digest(json.dumps({k: v for k, v in scope.items() if k != "digest"}, sort_keys=True))


def validate(scope):
    if scope.get("version") != 1 or scope.get("digest") != scope_digest(scope):
        raise Stop("invalid_scope")
    # This deployment is intentionally limited to the explicitly approved dinner.
    if (scope.get("date"), scope.get("party_size"), scope.get("earliest"), scope.get("latest")) != (
        "2026-12-12", 6, "18:00", "20:00"
    ) or scope.get("venues") != {"4795": "Sarma", "4126": "Oleana"}:
        raise Stop("scope_mismatch")
    if scope.get("max_fee_per_person") != 25 or scope.get("due_now") != 0:
        raise Stop("fee_scope_mismatch")
    if not scope.get("allow_cancel_after_confirmed") or not scope.get("approved_by"):
        raise Stop("replacement_not_authorized")
    if not timestamp(scope["authorized_at"]) < timestamp(scope["expires_at"]) <= timestamp("2026-12-12T01:15:00Z"):
        raise Stop("invalid_expiry")
    if not re.fullmatch(r"[1-9][0-9]{0,17}", scope.get("notify_chat", "")):
        raise Stop("invalid_notification_target")
    if not scope.get("gmail_account") or not re.fullmatch(r"[a-f0-9]{64}", scope["baseline"]["identity"]):
        raise Stop("invalid_account_binding")


def quiet(function, *args):
    with open(os.devnull, "w") as sink, contextlib.redirect_stdout(sink), contextlib.redirect_stderr(sink):
        return function(*args)


def api_client():
    os.environ["RESY_CACHE_ONLY"] = "1"
    module = load("notify_resy_provider", ROOT / "bin/resy")
    module.CACHE_ONLY = True
    module.ResyCredentials._op_read = lambda self, field: None
    return module.ResyAPI()


def gmail(scope, method, params):
    env = dict(os.environ, GOOGLE_WORKSPACE_CLI_ACCOUNT=scope["gmail_account"])
    env.pop("OP_SERVICE_ACCOUNT_TOKEN", None)
    result = subprocess.run(
        [str(NODE), "/opt/homebrew/bin/gws", "gmail", "users", "messages", method, "--params", json.dumps({"userId": "me", **params})],
        env=env, capture_output=True, text=True, timeout=45,
    )
    if result.returncode:
        raise Stop("gmail_unavailable")
    data = json.loads(result.stdout)
    if not isinstance(data, dict) or "error" in data:
        raise Stop("gmail_unavailable")
    return data


def body_text(part):
    parts = [body_text(p) for p in part.get("parts", [])]
    if part.get("mimeType") in {"text/plain", "text/html"}:
        data = part.get("body", {}).get("data", "")
        if len(data) <= 300000:
            text = base64.urlsafe_b64decode(data + "=" * (-len(data) % 4)).decode("utf-8", "replace")
            parts.append(html.unescape(re.sub(r"<[^>]*>", " ", text)))
    return " ".join(parts)


def alert_venues(message, scope, at):
    received = datetime.fromtimestamp(int(message["internalDate"]) / 1000, timezone.utc)
    if not max(timestamp(scope["authorized_at"]), at - timedelta(minutes=15)) <= received <= at + timedelta(minutes=1):
        return []
    headers = message.get("payload", {}).get("headers", [])
    def values(name):
        return [h["value"] for h in headers if h.get("name", "").lower() == name]
    senders = values("from")
    if len(senders) != 1 or parseaddr(senders[0])[1].lower().split("@")[-1] != "resy.com":
        return []
    # Trust Gmail's own ingress authentication result, not the visible From alone.
    auth = next((v for v in values("authentication-results") if v.lstrip().startswith("mx.google.com;")), "")
    if not re.search(r"\bdmarc=pass\b[^;]*\bheader\.from=resy\.com(?:\s|;|$)", auth, re.I):
        return []
    subjects = values("subject")
    text = " ".join(subjects) + " " + body_text(message.get("payload", {}))
    if not re.search(r"\b(?:available|availability|opened up|opening|notify|hot table|table alert)\b|\btable\b.{0,100}\b(?:open|ready)\b", text, re.I | re.S):
        return []
    return [vid for vid, name in scope["venues"].items() if re.search(r"\b" + re.escape(name) + r"\b", text, re.I)]


def events(scope, state):
    at = now()
    since = max(timestamp(scope["authorized_at"]), at - timedelta(minutes=15))
    query = f"from:resy.com after:{int(since.timestamp())} {{Sarma Oleana}}"
    listing = gmail(scope, "list", {"q": query, "maxResults": 25})
    if listing.get("nextPageToken"):
        raise Stop("unexpected_email_volume")
    found = []
    for row in listing.get("messages", []):
        event = digest(row["id"])
        if event in state.get("seen", []):
            continue
        message = gmail(scope, "get", {"id": row["id"], "format": "full"})
        venues = alert_venues(message, scope, at)
        found.append((event, venues))
    return found


def inventory(api, scope):
    raw = quiet(api.get_reservations)
    rows = book.complete_resy_reservation_rows(raw)
    result = []
    for row in rows:
        normal = book.normalize_reservation(row, "resy", resy_shape=True)
        if normal["date"] != scope["date"] or normal["status"] in book.INACTIVE_STATUSES:
            continue
        if not row.get("reservation_id") or not row.get("resy_token"):
            raise Stop("reservation_identity_missing")
        result.append((normal, row, digest(row["reservation_id"])))
    return result


def free_to_cancel(row, scope):
    policy = row.get("cancellation", {})
    fee = policy.get("fee", {})
    if policy.get("allowed") is not True or not isinstance(fee, dict) or fee.get("applies") is not False:
        raise Stop("cancellation_not_free")
    cutoff = fee.get("date_cut_off") or policy.get("date_refund_cut_off")
    if not cutoff or now() + timedelta(minutes=1) >= min(timestamp(cutoff), timestamp(scope["expires_at"])):
        raise Stop("cancellation_deadline_reached")


def baseline(api, scope, state):
    rows = inventory(api, scope)
    expected = state.get("current", scope["baseline"])
    if len(rows) != 1:
        raise Stop("reservation_inventory_changed")
    normal, raw, identity = rows[0]
    if (identity, normal["venue_id"], normal["time"], normal["party_size"]) != (
        expected["identity"], expected["venue_id"], expected["time"], scope["party_size"]
    ) or normal["status"] not in book.CONFIRMED_STATUSES:
        raise Stop("baseline_changed")
    free_to_cancel(raw, scope)
    return normal, raw, identity


def acceptable_terms(slot, details, scope):
    normalized = copy.deepcopy(details)
    # Resy's current response represents a zero service charge as an amount
    # plus percentage display. Accept only this exact zero shape; preserve all
    # other fields for the existing strict financial parser to evaluate.
    provider_payment = normalized.get("payment", {})
    amounts = [provider_payment.get("amounts", {})]
    amounts.extend(option.get("amounts", {}) for option in provider_payment.get("options", []) if isinstance(option, dict))
    for item in amounts:
        if isinstance(item, dict) and item.get("service_charge") == {"amount": 0, "value": "0%"}:
            item["service_charge"] = 0
    payment = book.resy_payment(slot, normalized, scope["party_size"])
    if not payment["terms_known"] or payment["nonrefundable"]:
        return False
    if any(payment[k] != 0 for k in ["due_now_minor", "deposit_minor", "prepayment_minor"]):
        return False
    fee = details.get("cancellation", {}).get("fee", {})
    if not isinstance(fee, dict) or fee.get("currency") not in (None, "USD"):
        return False
    try:
        amount = book.numeric_minor(fee.get("amount"))
        tax = book.numeric_minor(fee.get("tax"))
        cutoff = timestamp(fee["date_cut_off"])
    except (ValueError, KeyError, TypeError, book.ProviderUnavailable):
        return False
    return (0 <= amount <= 2500 and payment["cancellation_fee_minor"] <= 2500 + tax
            and payment["no_show_fee_minor"] <= 2500
            and now() + timedelta(minutes=1) < cutoff)


def candidates(api, scope, current, venue_ids):
    result = []
    for venue_id in sorted(set(venue_ids), key=lambda v: (v != "4795", v)):
        if venue_id not in scope["venues"]:
            raise Stop("venue_outside_scope")
        data = quiet(api.find_availability, venue_id, scope["date"], scope["party_size"])
        if not isinstance(data, dict) or snipe.has_structured_failure(data):
            raise Stop("availability_unavailable")
        venues = data.get("results", {}).get("venues")
        if not isinstance(venues, list):
            raise Stop("availability_unavailable")
        for venue in venues:
            info = venue.get("venue", {})
            provider_id = info.get("id", {})
            provider_id = provider_id.get("resy") if isinstance(provider_id, dict) else provider_id
            if str(provider_id) != venue_id or info.get("name") != scope["venues"][venue_id]:
                raise Stop("venue_identity_mismatch")
            for slot in venue.get("slots", []):
                start = snipe.normalize_slot_start(slot.get("date", {}).get("start"))
                if not start or start[0] != scope["date"]:
                    continue
                time = start[1]
                if scope["earliest"] <= time <= scope["latest"] and time < current["time"]:
                    result.append((time, venue_id, slot))
    return sorted(result, key=lambda x: (x[0], x[1] != "4795"))


def cancel_once(api, token):
    # The general CLI's cancel method retries; a replacement must never do so.
    api.limiter.wait()
    headers = api._headers()
    guard = getattr(api, "_notify_pre_cancel", None)
    if callable(guard):
        guard()
    response = api.session.request(
        "POST", "https://api.resy.com/3/cancel", headers=headers,
        data={"resy_token": token}, timeout=30, allow_redirects=False,
    )
    if response.status_code != 200:
        raise Stop("cancellation_outcome_unknown")


def notify(scope, message):
    result = subprocess.run(
        ["/opt/homebrew/bin/imsg", "send", "--chat-id", scope["notify_chat"], "--text", message, "--json"],
        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=20,
    )
    return result.returncode == 0


def improve(api, scope, state, venue_ids, save, cancel=cancel_once):
    current, old, old_identity = baseline(api, scope, state)
    if current["time"] == scope["earliest"]:
        return "goal_reached"
    selected = None
    for time, venue_id, slot in candidates(api, scope, current, venue_ids)[:6]:
        token = slot.get("config", {}).get("token")
        if not token:
            continue
        details = quiet(api.get_details, token, scope["date"], scope["party_size"])
        if acceptable_terms(slot, details, scope) and details.get("book_token", {}).get("value"):
            selected = (time, venue_id, details)
            break
    if selected is None:
        return "no_eligible_improvement"
    time, venue_id, details = selected
    payment_id = api.creds.get("payment_id")
    if not payment_id:
        raise Stop("payment_method_unavailable")

    def final_guard():
        api._restaurant_snipe_final_guard = True
        try:
            baseline(api, scope, state)
        finally:
            api._restaurant_snipe_final_guard = False

    def deadline_guard():
        free_to_cancel(old, scope)
        if now() >= timestamp(details["cancellation"]["fee"]["date_cut_off"]) - timedelta(minutes=1):
            raise Stop("new_cancellation_deadline_reached")

    api._pre_booking_guard = final_guard
    api._pre_mutation_check = deadline_guard
    state["phase"] = "booking_attempted"
    state["candidate"] = {"venue_id": venue_id, "time": time}
    save()
    # No automatic retries after this durable boundary, including across crashes.
    quiet(api.book, details["book_token"]["value"], payment_id)
    rows = inventory(api, scope)
    old_rows = [r for r in rows if r[2] == old_identity]
    new_rows = [r for r in rows if r[2] != old_identity and r[0]["venue_id"] == venue_id
                and r[0]["time"] == time and r[0]["party_size"] == scope["party_size"]
                and r[0]["status"] in book.CONFIRMED_STATUSES]
    if len(rows) != 2 or len(old_rows) != 1 or len(new_rows) != 1:
        raise Stop("replacement_not_verified")
    free_to_cancel(old_rows[0][1], scope)
    free_to_cancel(new_rows[0][1], scope)
    state["phase"] = "cancellation_attempted"
    save()
    api._notify_pre_cancel = lambda: free_to_cancel(old_rows[0][1], scope)
    cancel(api, old_rows[0][1]["resy_token"])
    after = inventory(api, scope)
    if len(after) != 1 or after[0][2] != new_rows[0][2]:
        raise Stop("cancellation_not_verified")
    state["current"] = {
        "identity": after[0][2], "venue_id": venue_id, "time": time,
        "cancel_before": after[0][1]["cancellation"]["fee"]["date_cut_off"],
    }
    state["phase"] = "idle"
    state.pop("candidate", None)
    state["pending_notice"] = (
        f"Dinner improved: {scope['venues'][venue_id]}, December 12 at {time}, for six. "
        "Replacement confirmed; previous reservation canceled without a fee."
    )
    save()
    return "improved"


def run(scope_path, state_path, execute=False):
    scope = json.loads(scope_path.read_text())
    validate(scope)
    state_path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    with (state_path.parent / "worker.lock").open("a+") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return {"status": "busy"}
        state = json.loads(state_path.read_text()) if state_path.exists() else {"phase": "idle", "seen": [], "scope_digest": scope["digest"]}
        if state.get("scope_digest") != scope["digest"]:
            raise Stop("state_scope_mismatch")
        def save():
            snipe.atomic_json(state_path, state)
        if execute and state.get("pending_notice"):
            if notify(scope, state["pending_notice"]):
                state.pop("pending_notice")
                save()
        if state.get("phase") != "idle":
            return {"status": "manual_review_required"}
        current = state.get("current", scope["baseline"])
        deadline = min(timestamp(scope["expires_at"]), timestamp(current.get("cancel_before", scope["expires_at"])))
        if now() + timedelta(minutes=1) >= deadline:
            return {"status": "expired"}
        if state.get("current", scope["baseline"])["time"] == scope["earliest"]:
            return {"status": "goal_reached"}
        incoming = events(scope, state)
        venue_ids = {v for _, venues in incoming for v in venues}
        if not execute:
            return {"status": "preview", "matching_alerts": sum(bool(v) for _, v in incoming), "resy_requests": 0}
        state["seen"] = (state.get("seen", []) + [key for key, _ in incoming])[-2000:]
        save()
        if not venue_ids:
            return {"status": "no_alert", "resy_requests": 0}
        shared = Path.home() / ".openclaw/restaurant-snipes/state/booking-mutation.lock"
        shared.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        with shared.open("a+") as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                # Leave these messages available for the next run.
                fresh_keys = {key for key, _ in incoming}
                state["seen"] = [key for key in state["seen"] if key not in fresh_keys]
                save()
                return {"status": "booking_worker_busy"}
            try:
                status = improve(api_client(), scope, state, venue_ids, save)
            except BaseException:
                if state["phase"] != "idle":
                    state["phase"] = "manual_review_required"
                    state["pending_notice"] = "Dinner replacement needs manual review. An operation could not be verified; automatic changes have stopped. Check Resy before changing either reservation."
                else:
                    state["pending_notice"] = "A Resy alert arrived, but OpenClaw could not safely process an earlier dinner reservation. No booking or cancellation was attempted. Your existing reservation is preserved."
                save()
                if notify(scope, state["pending_notice"]):
                    state.pop("pending_notice")
                    save()
                raise
        if state.get("pending_notice") and notify(scope, state["pending_notice"]):
            state.pop("pending_notice")
            save()
        return {"status": status}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scope", type=Path, required=True)
    parser.add_argument("--state", type=Path, required=True)
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args()
    try:
        result = run(args.scope, args.state, args.execute)
    except Stop as exc:
        result = {"status": "blocked", "reason": str(exc)}
    except BaseException:
        # Provider errors, email bodies, tokens and reservation IDs stay private.
        result = {"status": "unavailable"}
    result["checked_at"] = now().isoformat()
    print(json.dumps(result, sort_keys=True))
    return 0 if result["status"] not in {"blocked", "unavailable"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
