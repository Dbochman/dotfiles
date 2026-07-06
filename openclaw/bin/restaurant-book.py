#!/usr/bin/env python3
"""Deterministic, fail-closed coordinator for canonical restaurant cron jobs."""

from __future__ import annotations

import argparse
import contextlib
import fcntl
import hashlib
import importlib.machinery
import importlib.util
import io
import json
import os
import re
import stat
import subprocess
import sys
import tempfile
import time
import urllib.parse
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Iterable


SCHEMA_VERSION = 1
PLATFORMS = ("resy", "opentable")
ACTIVE_STATUSES = {"active", "booked", "confirmed", "pending", "reserved"}
CONFIRMED_STATUSES = {"active", "booked", "confirmed", "reserved"}
INACTIVE_STATUSES = {"cancelled", "canceled", "completed", "expired", "finished", "past"}
TIME_RE = re.compile(r"^(?:[01]\d|2[0-3]):[0-5]\d$")
JOB_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
SELECTION_SORT = [
    "date_rank",
    "time_delta",
    "preferred_venue_rank",
    "platform_rank",
    "provider_rank",
    "restaurant_name",
]
OPENTABLE_EXTERNAL_GUARD_CONTRACT = 1
CONFIRMATION_KEYS = {
    "bookingid",
    "confirmationcode",
    "confirmationid",
    "confirmationnumber",
    "reservationid",
}
CONTEXTUAL_CONFIRMATION_KEYS = {"confirmationcode", "confirmationnumber"}
CONFIRMATION_FLAG_KEYS = {"confirmed", "ok", "success"}
CONFIRMATION_STATUS_KEYS = {"bookingstatus", "reservationstatus"}
CONFIRMED_STATUS_VALUES = {"booked", "confirmed", "reserved", "success", "succeeded"}
FAILURE_STATUS_VALUES = {
    "aborted",
    "cancelled",
    "canceled",
    "declined",
    "denied",
    "error",
    "expired",
    "failed",
    "failure",
    "invalid",
    "rejected",
    "timedout",
    "timeout",
    "unavailable",
}
CURRENT_RESULT_WRAPPERS = {"data", "response", "result"}


class RestaurantBookError(ValueError):
    """Safe validation failure."""


class ProviderUnavailable(RuntimeError):
    """A provider could not supply a strict, safe result."""


class ProviderBudgetExhausted(RuntimeError):
    """A bounded provider search exhausted its total call budget."""


class ExistingReservation(RuntimeError):
    """A final live guard found an in-scope reservation."""


class AuthorizationExpired(RuntimeError):
    """The standing authorization expired before mutation."""


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def timestamp(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def parse_timestamp(value: Any, field_name: str) -> datetime:
    if not isinstance(value, str):
        raise RestaurantBookError(f"{field_name} must be an ISO-8601 timestamp")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise RestaurantBookError(f"{field_name} must be an ISO-8601 timestamp") from exc
    if parsed.tzinfo is None:
        raise RestaurantBookError(f"{field_name} must include a timezone")
    return parsed.astimezone(timezone.utc)


def parse_date(value: Any, field_name: str = "date") -> str:
    if not isinstance(value, str):
        raise RestaurantBookError(f"{field_name} must use YYYY-MM-DD")
    try:
        return datetime.strptime(value, "%Y-%m-%d").date().isoformat()
    except ValueError as exc:
        raise RestaurantBookError(f"{field_name} must use YYYY-MM-DD") from exc


def time_minutes(value: Any, field_name: str = "time") -> int:
    if not isinstance(value, str) or not TIME_RE.fullmatch(value):
        raise RestaurantBookError(f"{field_name} must use HH:MM")
    hour, minute = (int(part) for part in value.split(":"))
    return hour * 60 + minute


def normalize_time(value: Any) -> str:
    if not isinstance(value, str):
        raise RestaurantBookError("reservation time is invalid")
    value = value.strip()
    if TIME_RE.fullmatch(value):
        return value
    if re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d:[0-5]\d", value):
        return value[:5]
    match = re.fullmatch(r"(\d{1,2}):(\d{2})\s*([AP]M)", value, re.IGNORECASE)
    if match:
        hour = int(match.group(1))
        minute = int(match.group(2))
        if not 1 <= hour <= 12 or minute > 59:
            raise RestaurantBookError("reservation time is invalid")
        hour %= 12
        if match.group(3).upper() == "PM":
            hour += 12
        return f"{hour:02d}:{minute:02d}"
    match = re.search(r"(?:T|\s)(\d{2}:\d{2})(?::\d{2})?", value)
    if match and TIME_RE.fullmatch(match.group(1)):
        return match.group(1)
    raise RestaurantBookError("reservation time is invalid")


def exact_keys(value: Any, expected: set[str], field_name: str) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != expected:
        raise RestaurantBookError(f"{field_name} has an unsupported schema")
    return value


def string_list(value: Any, field_name: str, *, allow_empty: bool = False) -> list[str]:
    if not isinstance(value, list) or (not allow_empty and not value):
        raise RestaurantBookError(f"{field_name} must be a non-empty string list")
    if any(not isinstance(item, str) or not item.strip() for item in value):
        raise RestaurantBookError(f"{field_name} must be a string list")
    return [item.strip() for item in value]


def canonical_digest(value: Any) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def normalized_search_text(value: Any) -> str:
    """Normalize provider metadata for punctuation-insensitive phrase matching."""
    if not isinstance(value, str):
        return ""
    return " ".join(re.findall(r"[a-z0-9]+", value.casefold()))


def normalized_identity_text(value: Any) -> str:
    return " ".join(value.casefold().split()) if isinstance(value, str) else ""


def metadata_matches(value: str, terms: Iterable[str]) -> bool:
    haystack = f" {normalized_search_text(value)} "
    return any(
        (needle := normalized_search_text(term)) and f" {needle} " in haystack
        for term in terms
    )


def joined_metadata(values: Iterable[Any]) -> str:
    result: list[str] = []
    seen: set[str] = set()
    for raw in values:
        if isinstance(raw, dict):
            raw = raw.get("name")
        if not isinstance(raw, str):
            continue
        value = " ".join(raw.split()).strip()[:300]
        key = value.casefold()
        if value and key not in seen:
            result.append(value)
            seen.add(key)
    return ", ".join(result)


def normalize_price_tier(value: Any) -> int | None:
    return value if type(value) is int and 1 <= value <= 4 else None


def fsync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def secure_directory(path: Path) -> None:
    missing: list[Path] = []
    cursor = path
    while not cursor.exists():
        missing.append(cursor)
        if cursor.parent == cursor:
            break
        cursor = cursor.parent
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    info = path.lstat()
    if not stat.S_ISDIR(info.st_mode) or stat.S_ISLNK(info.st_mode):
        raise RestaurantBookError("protected state directory is unsafe")
    if info.st_uid != os.getuid():
        raise RestaurantBookError("protected state directory has unsafe ownership")
    if info.st_mode & 0o077:
        os.chmod(path, 0o700)
        fsync_directory(path.parent)
    for created in reversed(missing):
        with contextlib.suppress(OSError):
            os.chmod(created, 0o700)
            fsync_directory(created.parent)


def secure_json_file(path: Path) -> Any:
    try:
        info = path.lstat()
    except FileNotFoundError as exc:
        raise RestaurantBookError("protected configuration is unavailable") from exc
    if not stat.S_ISREG(info.st_mode) or stat.S_ISLNK(info.st_mode):
        raise RestaurantBookError("protected configuration is not a regular file")
    if info.st_uid != os.getuid() or info.st_mode & 0o077:
        raise RestaurantBookError("protected configuration has unsafe ownership or permissions")
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RestaurantBookError("protected configuration is not valid JSON") from exc


def atomic_json(path: Path, payload: Any, mode: int = 0o600) -> None:
    secure_directory(path.parent)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, sort_keys=True, separators=(",", ":"))
            handle.write("\n")
            handle.flush()
            os.fchmod(handle.fileno(), mode)
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        fsync_directory(path.parent)
    finally:
        with contextlib.suppress(FileNotFoundError):
            os.unlink(temporary)


def durable_unlink(path: Path) -> None:
    path.unlink(missing_ok=True)
    fsync_directory(path.parent)


@contextlib.contextmanager
def nonblocking_lock(path: Path) -> Iterable[int]:
    secure_directory(path.parent)
    flags = os.O_CREAT | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags, 0o600)
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid():
            raise RestaurantBookError("booking lock is unsafe")
        os.fchmod(descriptor, 0o600)
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            yield -1
            return
        yield descriptor
    finally:
        os.close(descriptor)


def validate_scope(raw: Any, job_id: str) -> dict[str, Any]:
    scope = exact_keys(
        raw,
        {
            "authorization",
            "fees",
            "idempotency",
            "providers",
            "reservation",
            "search",
            "selection",
        },
        f"scope {job_id}",
    )
    if scope["providers"] != list(PLATFORMS):
        raise RestaurantBookError("restaurant scopes must search Resy and OpenTable")

    authorization = exact_keys(
        scope["authorization"],
        {"expires_at", "kind", "max_mutation_attempts", "not_before"},
        "authorization",
    )
    if authorization["kind"] != "canonical-cron-standing":
        raise RestaurantBookError("unsupported authorization kind")
    if authorization["max_mutation_attempts"] != 1:
        raise RestaurantBookError("authorization must permit exactly one mutation attempt")
    not_before = parse_timestamp(authorization["not_before"], "authorization.not_before")
    expires_at = parse_timestamp(authorization["expires_at"], "authorization.expires_at")
    if expires_at <= not_before:
        raise RestaurantBookError("authorization expiry must follow its start")

    if isinstance(scope["search"], dict) and "minimum_price_tier" not in scope["search"]:
        scope["search"] = dict(scope["search"])
        scope["search"]["minimum_price_tier"] = None
    search = exact_keys(
        scope["search"],
        {
            "allow_discovery",
            "eligible_cuisine_terms",
            "eligible_locality_terms",
            "excluded_venues",
            "max_candidates_per_provider",
            "max_search_attempts_per_provider",
            "minimum_price_tier",
            "preferred_venues",
            "queries",
        },
        "search",
    )
    for key in ("queries", "eligible_cuisine_terms", "eligible_locality_terms"):
        search[key] = string_list(search[key], f"search.{key}")
    for key in ("preferred_venues", "excluded_venues"):
        search[key] = string_list(search[key], f"search.{key}", allow_empty=True)
    if type(search["allow_discovery"]) is not bool:
        raise RestaurantBookError("search.allow_discovery must be boolean")
    minimum_price_tier = search["minimum_price_tier"]
    if minimum_price_tier is not None and (
        type(minimum_price_tier) is not int or not 1 <= minimum_price_tier <= 4
    ):
        raise RestaurantBookError("search.minimum_price_tier must be null or 1-4")
    for key in ("max_candidates_per_provider", "max_search_attempts_per_provider"):
        if type(search[key]) is not int or not 1 <= search[key] <= 20:
            raise RestaurantBookError(f"search.{key} must be 1-20")

    reservation = exact_keys(
        scope["reservation"],
        {"dates", "max_delta_minutes", "party_size", "target_time", "window_end", "window_start"},
        "reservation",
    )
    dates = [parse_date(value, "reservation date") for value in string_list(reservation["dates"], "reservation.dates")]
    if dates != sorted(set(dates)):
        raise RestaurantBookError("reservation dates must be unique and sorted")
    reservation["dates"] = dates
    if type(reservation["party_size"]) is not int or not 1 <= reservation["party_size"] <= 20:
        raise RestaurantBookError("reservation.party_size must be 1-20")
    target = time_minutes(reservation["target_time"], "reservation.target_time")
    window_start = time_minutes(reservation["window_start"], "reservation.window_start")
    window_end = time_minutes(reservation["window_end"], "reservation.window_end")
    if not window_start <= target <= window_end:
        raise RestaurantBookError("target time must fall inside the time window")
    if type(reservation["max_delta_minutes"]) is not int or not 0 <= reservation["max_delta_minutes"] <= 180:
        raise RestaurantBookError("reservation.max_delta_minutes must be 0-180")

    idempotency = exact_keys(
        scope["idempotency"],
        {"date_end", "date_start", "match", "party_size", "time_end", "time_start"},
        "idempotency",
    )
    if idempotency["match"] != "any-active":
        raise RestaurantBookError("unsupported idempotency match policy")
    start = parse_date(idempotency["date_start"], "idempotency.date_start")
    end = parse_date(idempotency["date_end"], "idempotency.date_end")
    if end < start or any(not start <= candidate_date <= end for candidate_date in dates):
        raise RestaurantBookError("idempotency dates do not contain every candidate date")
    idempotency["date_start"] = start
    idempotency["date_end"] = end
    party = idempotency["party_size"]
    if party is not None and (type(party) is not int or not 1 <= party <= 20):
        raise RestaurantBookError("idempotency.party_size must be null or 1-20")
    if (idempotency["time_start"] is None) != (idempotency["time_end"] is None):
        raise RestaurantBookError("idempotency time bounds must both be null or HH:MM")
    if idempotency["time_start"] is not None:
        if time_minutes(idempotency["time_end"]) < time_minutes(idempotency["time_start"]):
            raise RestaurantBookError("idempotency time window may not cross midnight")

    fees = exact_keys(
        scope["fees"],
        {
            "allow_card_guarantee",
            "allow_nonrefundable",
            "currency",
            "max_cancellation_fee_minor",
            "max_deposit_minor",
            "max_due_now_minor",
            "max_no_show_fee_minor",
            "max_prepayment_minor",
            "unknown_terms",
        },
        "fees",
    )
    if fees["currency"] != "USD" or fees["unknown_terms"] != "reject":
        raise RestaurantBookError("fee policy must use USD and reject unknown terms")
    for key in ("allow_card_guarantee", "allow_nonrefundable"):
        if type(fees[key]) is not bool:
            raise RestaurantBookError(f"fees.{key} must be boolean")
    for key in (
        "max_cancellation_fee_minor",
        "max_deposit_minor",
        "max_due_now_minor",
        "max_no_show_fee_minor",
        "max_prepayment_minor",
    ):
        if type(fees[key]) is not int or fees[key] < 0:
            raise RestaurantBookError(f"fees.{key} must be a non-negative integer")

    selection = exact_keys(scope["selection"], {"platform_tiebreak", "sort"}, "selection")
    if selection["platform_tiebreak"] != list(PLATFORMS) or selection["sort"] != SELECTION_SORT:
        raise RestaurantBookError("selection policy is unsupported")

    return scope


def load_scope_registry(path: Path, job_id: str) -> tuple[dict[str, Any], str]:
    if not JOB_RE.fullmatch(job_id):
        raise RestaurantBookError("invalid canonical job ID")
    registry = secure_json_file(path)
    registry = exact_keys(registry, {"jobs", "schema_version"}, "scope registry")
    if registry["schema_version"] != SCHEMA_VERSION or not isinstance(registry["jobs"], dict):
        raise RestaurantBookError("unsupported scope registry")
    if job_id not in registry["jobs"]:
        raise RestaurantBookError("job ID is not standing-authorized")
    scope = validate_scope(registry["jobs"][job_id], job_id)
    return scope, canonical_digest(scope)


@dataclass
class Candidate:
    platform: str
    venue_id: str
    restaurant: str
    cuisine: str
    location: str
    date: str
    time: str
    party_size: int
    seating: str
    payment: dict[str, Any]
    cancellation_policy: str
    no_show_policy: str
    provider_rank: int
    price_tier: int | None = None
    private: dict[str, Any] = field(default_factory=dict, repr=False, compare=False)

    def safe(self) -> dict[str, Any]:
        return {
            "candidate_digest": self.digest(),
            "platform": self.platform,
            "venue_id": self.venue_id,
            "restaurant": self.restaurant,
            "cuisine": self.cuisine,
            "location": self.location,
            "price_tier": self.price_tier,
            "date": self.date,
            "time": self.time,
            "party_size": self.party_size,
            "seating": self.seating,
            "payment": self.payment,
            "cancellation_policy": self.cancellation_policy,
            "no_show_policy": self.no_show_policy,
        }

    def digest(self) -> str:
        payload = {key: value for key, value in self.safe_without_digest().items()}
        return canonical_digest(payload)

    def safe_without_digest(self) -> dict[str, Any]:
        return {
            "platform": self.platform,
            "venue_id": self.venue_id,
            "restaurant": self.restaurant,
            "cuisine": self.cuisine,
            "location": self.location,
            "price_tier": self.price_tier,
            "date": self.date,
            "time": self.time,
            "party_size": self.party_size,
            "seating": self.seating,
            "payment": self.payment,
            "cancellation_policy": self.cancellation_policy,
            "no_show_policy": self.no_show_policy,
        }


def payment_facts(
    *,
    due_now_minor: int = 0,
    deposit_minor: int = 0,
    prepayment_minor: int = 0,
    cancellation_fee_minor: int = 0,
    no_show_fee_minor: int = 0,
    nonrefundable: bool = False,
    card_guarantee: bool = False,
    terms_known: bool = True,
) -> dict[str, Any]:
    values = {
        "currency": "USD",
        "due_now_minor": due_now_minor,
        "deposit_minor": deposit_minor,
        "prepayment_minor": prepayment_minor,
        "cancellation_fee_minor": cancellation_fee_minor,
        "no_show_fee_minor": no_show_fee_minor,
        "nonrefundable": nonrefundable,
        "card_guarantee": card_guarantee,
        "terms_known": terms_known,
    }
    for key, value in values.items():
        if key.endswith("_minor") and (type(value) is not int or value < 0):
            raise ProviderUnavailable("provider returned invalid payment terms")
    return values


def numeric_minor(value: Any) -> int:
    if value in (None, "", False):
        return 0
    if type(value) not in (int, float) or value < 0:
        raise ProviderUnavailable("provider returned invalid payment terms")
    return round(float(value) * 100)


BOOKING_TERM_FRAGMENTS = (
    "bookingterm",
    "cancellation",
    "cancelfee",
    "creditcardpolicy",
    "deposit",
    "noshow",
    "paymentterm",
    "penalty",
    "prepay",
    "servicecharge",
)
BOOKING_TERM_KEYS = {"charge", "charges", "fee", "fees", "hold", "policy", "terms"}
BOOKING_TERM_VALUE_KEYS = {
    "amount",
    "description",
    "display",
    "label",
    "message",
    "required",
    "status",
    "text",
    "title",
    "type",
    "value",
}
BOOKING_TERM_STRUCTURAL_KEYS = {
    "currency",
    "datecutoff",
    "secscancelcutoff",
    "secschangecutoff",
    "timecancelcutoff",
    "timechangecutoff",
}

BOOKING_TERM_RISK_PATTERN = re.compile(
    r"\b(?:cancellation|no[- ]?show|service\s+charge|charge(?:d|s)?|deposit|"
    r"fees?|holds?|penalt(?:y|ies)|prepay(?:ment|ments)?)\b"
)


def _clause_has_explicit_zero_booking_term(clause: str) -> bool:
    dollar_amounts = re.findall(r"\$\s*(\d+(?:\.\d{1,2})?)", clause)
    return bool(
        re.search(
            r"\b(?:won't|will not|would not|not be|never)\s+(?:be\s+)?charged\b",
            clause,
        )
        or re.search(
            r"\b(?:no|without)\s+(?:separate\s+)?"
            r"(?:cancellation\s+|no[- ]?show\s+|service\s+)?"
            r"(?:charge|deposit|fee|hold|penalty|prepayment)\b",
            clause,
        )
        or "free cancellation" in clause
        or (
            dollar_amounts
            and all(float(amount) == 0 for amount in dollar_amounts)
        )
        or re.search(
            r"\b(?:deposit|fee|hold|penalty|prepayment|service\s+charge)\s+"
            r"(?:is\s+)?not\s+required\b",
            clause,
        )
    )


def _has_affirmative_or_uncertain_booking_term(text: str) -> bool:
    for match in re.finditer(
        r"\b(?:(?:cancellation|no[- ]?show|service)\s+)?"
        r"(?:fees?|charges?|deposits?|holds?|penalt(?:y|ies)|prepayments?)\s+"
        r"(?:(?:may|might|can|could|does|will|would)\s+)?"
        r"(?:apply|applies|be\s+(?:assessed|charged|due|required))\b",
        text,
    ):
        prefix = text[max(0, match.start() - 24) : match.start()]
        if not re.search(r"\b(?:no|without)\s+(?:separate\s+)?$", prefix):
            return True
    return bool(
        re.search(
            r"\b(?:may|might|can|could|will|would)\s+be\s+"
            r"(?:assessed|charged|required)\b",
            text,
        )
    )


def _explicit_zero_booking_term(value: Any) -> bool:
    if value in (None, False, "", 0, 0.0, [], {}):
        return True
    if type(value) is bool or type(value) in (int, float):
        return False
    if isinstance(value, list):
        return all(_explicit_zero_booking_term(item) for item in value)
    if isinstance(value, dict):
        return all(_explicit_zero_booking_term(item) for item in value.values())
    if not isinstance(value, str):
        return False
    text = " ".join(value.casefold().replace("’", "'").split())
    normalized = normalized_search_text(text)
    if normalized in {"free", "n a", "na", "no", "none", "not applicable", "not required"}:
        return True
    dollar_amounts = re.findall(r"\$\s*(\d+(?:\.\d{1,2})?)", text)
    if any(float(amount) > 0 for amount in dollar_amounts):
        return False
    if re.search(r"\b\d+(?:\.\d+)?\s*(?:/|per)\s*(?:guest|person|party)\b", text):
        return False
    if _has_affirmative_or_uncertain_booking_term(text):
        return False
    clauses = tuple(
        clause.strip()
        for clause in re.split(
            r"(?:[.;!?]+|,?\s+\b(?:but|however|except)\b\s+)", text
        )
        if clause.strip()
    )
    for clause in clauses:
        if BOOKING_TERM_RISK_PATTERN.search(clause) and not (
            _clause_has_explicit_zero_booking_term(clause)
        ):
            return False
    explicit_zero = any(
        _clause_has_explicit_zero_booking_term(clause) for clause in clauses
    )
    if not explicit_zero:
        return False
    # A zero-fee sentence cannot authorize a separate positive/unknown term.
    for risky in ("deposit", "hold", "penalty", "prepay", "prepayment"):
        if risky in normalized and not re.search(
            rf"\b(?:no|without)\s+{risky}\b|\b{risky}\s+(?:is\s+)?not\s+required\b",
            normalized,
        ):
            return False
    return True


def structured_booking_terms_safe(value: Any, *, in_term: bool = False) -> bool:
    if isinstance(value, dict):
        for raw_key, child in value.items():
            key = re.sub(r"[^a-z0-9]", "", str(raw_key).casefold())
            if in_term and key in BOOKING_TERM_STRUCTURAL_KEYS:
                continue
            known_term_key = (
                key in BOOKING_TERM_KEYS
                or any(fragment in key for fragment in BOOKING_TERM_FRAGMENTS)
                or (in_term and key in BOOKING_TERM_VALUE_KEYS)
            )
            if in_term and not known_term_key:
                if child in (None, False, "", 0, 0.0, [], {}):
                    continue
                return False
            child_in_term = in_term or known_term_key
            if not structured_booking_terms_safe(child, in_term=child_in_term):
                return False
        return True
    if isinstance(value, list):
        return all(structured_booking_terms_safe(item, in_term=in_term) for item in value)
    return not in_term or _explicit_zero_booking_term(value)


def resy_unparsed_booking_terms_safe(slot: dict[str, Any], details: dict[str, Any]) -> bool:
    slot_terms = {key: value for key, value in slot.items() if key != "payment"}
    details_terms = {
        key: value for key, value in details.items() if key not in {"cancellation", "payment"}
    }
    cancellation = details.get("cancellation")
    cancellation_extras = (
        {
            key: value
            for key, value in cancellation.items()
            if key not in {"display", "fee"}
        }
        if isinstance(cancellation, dict)
        else {}
    )
    return all(
        structured_booking_terms_safe(payload)
        for payload in (slot_terms, details_terms, cancellation_extras)
    )


def _resy_cancellation(details: dict[str, Any]) -> tuple[int, str, bool]:
    cancellation = details.get("cancellation")
    if not isinstance(cancellation, dict):
        return 0, "Cancellation policy unavailable", False
    fee = cancellation.get("fee")
    if fee is None:
        fee_minor = 0
    elif isinstance(fee, dict) and set(fee) <= {
        "amount",
        "currency",
        "date_cut_off",
        "display",
        "tax",
    }:
        if fee.get("currency") not in (None, "USD"):
            return 0, "Cancellation policy unavailable", False
        try:
            fee_minor = numeric_minor(fee.get("amount")) + numeric_minor(fee.get("tax"))
        except ProviderUnavailable:
            return 0, "Cancellation policy unavailable", False
        fee_display = fee.get("display")
        if fee_display is not None:
            if isinstance(fee_display, str):
                pass
            elif isinstance(fee_display, dict) and all(
                isinstance(key, str)
                and value is not None
                and isinstance(value, (str, int, float))
                for key, value in fee_display.items()
            ):
                pass
            else:
                return 0, "Cancellation policy unavailable", False
        if fee.get("date_cut_off") is not None and not isinstance(
            fee.get("date_cut_off"), (str, int, float)
        ):
            return 0, "Cancellation policy unavailable", False
    else:
        return 0, "Cancellation policy unavailable", False

    display = cancellation.get("display")
    policy: Any = display.get("policy") if isinstance(display, dict) else None
    if isinstance(policy, str):
        lines = [policy]
    elif isinstance(policy, list) and all(isinstance(item, str) for item in policy):
        lines = policy
    else:
        lines = []
    text = " ".join(" ".join(item.split()) for item in lines if item.strip())[:1000]
    terms_known = bool(text) and (
        fee_minor > 0 or _explicit_zero_booking_term(text)
    )
    if fee_minor == 0 and fee is not None and isinstance(fee, dict):
        fee_display = fee.get("display")
        if fee_display not in (None, "", {}, []) and not _explicit_zero_booking_term(
            fee_display
        ):
            terms_known = False
    return fee_minor, text or "Cancellation policy unavailable", terms_known


def _empty_booking_value(value: Any) -> bool:
    return value in (None, False, "", 0, 0.0, [], {})


def _zero_money_value(value: Any) -> bool:
    return _empty_booking_value(value) or (
        isinstance(value, str)
        and re.fullmatch(r"\$?0+(?:\.0{1,2})?", value.strip()) is not None
    )


def _zero_resy_amounts(value: Any, expected_party_size: int | None) -> bool:
    if not isinstance(value, dict):
        return False
    financial_keys = {
        "addons",
        "priceperunit",
        "reservationcharge",
        "resyfee",
        "servicecharge",
        "servicefee",
        "subtotal",
        "surcharge",
        "tax",
        "total",
    }
    for raw_key, child in value.items():
        key = re.sub(r"[^a-z0-9]", "", str(raw_key).casefold())
        if key == "items":
            if child not in (None, []):
                return False
        elif key == "quantity":
            if type(child) is not int or child <= 0:
                return False
            if expected_party_size is not None and child != expected_party_size:
                return False
        elif key == "currency":
            if child not in (None, "", "USD"):
                return False
        elif key in financial_keys:
            if not _zero_money_value(child):
                return False
        elif not _empty_booking_value(child):
            return False
    return True


def _zero_resy_display(value: Any) -> bool:
    if value in (None, {}, []):
        return True
    if not isinstance(value, dict):
        return False
    allowed = {"balance", "buy", "description", "title", "total"}
    if any(
        re.sub(r"[^a-z0-9]", "", str(key).casefold()) not in allowed
        and not _empty_booking_value(child)
        for key, child in value.items()
    ):
        return False
    balance = value.get("balance")
    if balance not in (None, {}, []):
        if not isinstance(balance, dict) or any(
            key not in {"value", "modifier"} and not _empty_booking_value(child)
            for key, child in balance.items()
        ):
            return False
        if any(not _zero_money_value(child) for child in balance.values()):
            return False
    buy = value.get("buy")
    if not isinstance(buy, dict):
        return False
    if any(
        key not in {"action", "value", "init", "before_modifier", "after_modifier"}
        and not _empty_booking_value(child)
        for key, child in buy.items()
    ):
        return False
    if normalized_search_text(buy.get("action")) != "now":
        return False
    if normalized_search_text(buy.get("value")) != "reserve":
        return False
    if any(
        not _empty_booking_value(buy.get(key))
        for key in ("init", "before_modifier", "after_modifier")
    ):
        return False
    if value.get("description") not in (None, "", []):
        return False
    if normalized_search_text(value.get("title")) not in {"", "reserve"}:
        return False
    return _zero_money_value(value.get("total"))


def _zero_resy_payment_structure(
    value: Any, expected_party_size: int | None
) -> bool:
    if not isinstance(value, dict):
        return False
    allowed = {"amounts", "config", "display", "options"}
    if any(
        key not in allowed and not _empty_booking_value(child)
        for key, child in value.items()
    ):
        return False
    if not _zero_resy_amounts(value.get("amounts"), expected_party_size):
        return False
    config = value.get("config")
    if not isinstance(config, dict):
        return False
    if any(
        key != "type" and not _empty_booking_value(child)
        for key, child in config.items()
    ):
        return False
    if normalized_search_text(config.get("type")) != "free":
        return False
    if not _zero_resy_display(value.get("display")):
        return False
    options = value.get("options", [])
    if not isinstance(options, list):
        return False
    for option in options:
        if not isinstance(option, dict):
            return False
        if any(
            key not in {"action", "amounts", "type"}
            and not _empty_booking_value(child)
            for key, child in option.items()
        ):
            return False
        if option.get("action") is not None and normalized_search_text(
            option.get("action")
        ) != "reserve":
            return False
        if option.get("type") is not None and normalized_search_text(
            option.get("type")
        ) != "free":
            return False
        if not _zero_resy_amounts(option.get("amounts", {}), expected_party_size):
            return False
    return True


def _resy_details_payment(
    details: dict[str, Any], expected_party_size: int | None = None
) -> tuple[int, bool]:
    payment = details.get("payment")
    if not isinstance(payment, dict):
        return 0, False
    unknown = any(
        key not in {"amounts", "config", "display", "options"}
        and value not in (None, False, "", 0, [], {})
        for key, value in payment.items()
    )
    amounts = payment.get("amounts")
    config = payment.get("config")
    if not isinstance(amounts, dict) or not isinstance(config, dict):
        return 0, False
    payment_type = config.get("type")
    if payment_type != "free":
        return 0, False
    display = payment.get("display")
    if display not in (None, {}, []):
        if not isinstance(display, dict) or not isinstance(display.get("buy"), dict):
            return 0, False
        buy = display["buy"]
        if (
            normalized_search_text(buy.get("action")) != "now"
            or normalized_search_text(buy.get("value")) != "reserve"
        ):
            return 0, False
    options = payment.get("options", [])
    if not isinstance(options, list):
        return 0, False
    for option in options:
        if not isinstance(option, dict):
            return 0, False
        if option.get("action") is not None and normalized_search_text(
            option.get("action")
        ) != "reserve":
            return 0, False
        if option.get("type") is not None and normalized_search_text(
            option.get("type")
        ) != "free":
            return 0, False
    if not _zero_resy_payment_structure(payment, expected_party_size):
        unknown = True
    try:
        total_minor = numeric_minor(amounts.get("total"))
    except ProviderUnavailable:
        return 0, False
    return total_minor, not unknown


def resy_payment(
    slot: dict[str, Any],
    details: dict[str, Any],
    expected_party_size: int | None = None,
) -> dict[str, Any]:
    slot_payment = slot.get("payment")
    if not isinstance(slot_payment, dict):
        return payment_facts(terms_known=False)
    known_slot_keys = {
        "is_paid",
        "is_add_on_required",
        "cancellation_fee",
        "deposit_fee",
        "service_charge",
        "venue_share",
        "payment_structure",
        "options",
        "service_charge_options",
        "secs_cancel_cut_off",
        "time_cancel_cut_off",
        "secs_change_cut_off",
        "time_change_cut_off",
    }
    unknown = any(
        key not in known_slot_keys and value not in (None, False, "", 0, [], {})
        for key, value in slot_payment.items()
    )
    is_paid = slot_payment.get("is_paid")
    add_on_required = slot_payment.get("is_add_on_required")
    if type(is_paid) is not bool or type(add_on_required) is not bool:
        unknown = True
    if add_on_required is True:
        unknown = True
    if slot_payment.get("payment_structure") not in (None, "", "free"):
        unknown = True
    options = slot_payment.get("options", [])
    if not isinstance(options, list) or options:
        unknown = True
    service_charge_options = slot_payment.get("service_charge_options", [])
    if not isinstance(service_charge_options, list) or service_charge_options:
        unknown = True
    try:
        slot_cancellation = numeric_minor(slot_payment.get("cancellation_fee"))
        deposit = numeric_minor(slot_payment.get("deposit_fee"))
        service_charge = numeric_minor(slot_payment.get("service_charge"))
        venue_share = numeric_minor(slot_payment.get("venue_share"))
    except ProviderUnavailable:
        return payment_facts(terms_known=False)
    if venue_share:
        unknown = True

    details_total, details_known = _resy_details_payment(details, expected_party_size)
    cancellation_fee, cancellation_text, cancellation_known = _resy_cancellation(details)
    if slot_cancellation and cancellation_fee and slot_cancellation != cancellation_fee:
        unknown = True
    effective_cancellation = max(slot_cancellation, cancellation_fee)
    if is_paid is True and not any((effective_cancellation, deposit, service_charge, details_total)):
        unknown = True
    encoded = cancellation_text.casefold()
    return payment_facts(
        due_now_minor=details_total + service_charge,
        deposit_minor=deposit,
        cancellation_fee_minor=effective_cancellation,
        nonrefundable=bool(re.search(r"non[- ]?refundable", encoded)),
        card_guarantee=bool(is_paid or effective_cancellation or deposit),
        terms_known=(
            details_known
            and cancellation_known
            and not unknown
            and resy_unparsed_booking_terms_safe(slot, details)
        ),
    )


def resy_policy_text(details: dict[str, Any]) -> tuple[str, str]:
    _fee, structured_cancellation, _known = _resy_cancellation(details)
    cancellation = details.get("cancellation_policy")
    no_show = details.get("no_show_policy") or details.get("no_show")
    cancellation_text = (
        cancellation.strip()[:1000]
        if isinstance(cancellation, str) and cancellation.strip()
        else structured_cancellation
    )
    no_show_text = (
        no_show.strip()[:1000]
        if isinstance(no_show, str) and no_show.strip()
        else "No separate no-show policy in structured booking details"
    )
    return cancellation_text, no_show_text


def fee_allowed(scope: dict[str, Any], payment: dict[str, Any]) -> bool:
    policy = scope["fees"]
    if set(payment) != {
        "card_guarantee",
        "cancellation_fee_minor",
        "currency",
        "deposit_minor",
        "due_now_minor",
        "no_show_fee_minor",
        "nonrefundable",
        "prepayment_minor",
        "terms_known",
    }:
        return False
    if payment["currency"] != policy["currency"] or payment["terms_known"] is not True:
        return False
    if payment["nonrefundable"] and not policy["allow_nonrefundable"]:
        return False
    if payment["card_guarantee"] and not policy["allow_card_guarantee"]:
        return False
    return all(
        payment[field] <= policy[f"max_{field}"]
        for field in (
            "cancellation_fee_minor",
            "deposit_minor",
            "due_now_minor",
            "no_show_fee_minor",
            "prepayment_minor",
        )
    )


def normalize_reservation(
    item: Any,
    platform: str,
    *,
    resy_shape: bool = False,
) -> dict[str, Any]:
    if not isinstance(item, dict):
        raise ProviderUnavailable("provider reservation data is malformed")
    if resy_shape:
        venue = item.get("venue")
        venue_id = venue.get("id") if isinstance(venue, dict) else None
        restaurant = venue.get("name") if isinstance(venue, dict) else None
        day = item.get("day")
        time_value = item.get("time_slot")
        party_size = item.get("num_seats")
        status_value = item.get("status")
        if isinstance(status_value, dict):
            finished = status_value.get("finished")
            if type(finished) is bool:
                status_value = "finished" if finished else "confirmed"
            elif type(finished) is int and finished in {0, 1}:
                status_value = "finished" if finished == 1 else "confirmed"
            else:
                status_value = "unknown"
    else:
        if item.get("platform") != platform:
            raise ProviderUnavailable("provider reservation platform is malformed")
        venue_id = item.get("venue_id")
        restaurant = item.get("restaurant")
        day = item.get("date")
        time_value = item.get("time")
        party_size = item.get("party_size")
        status_value = item.get("status")
    if venue_id is not None and (not isinstance(venue_id, (str, int)) or not str(venue_id)):
        raise ProviderUnavailable("provider reservation venue identity is malformed")
    if not isinstance(restaurant, str) or not restaurant.strip():
        raise ProviderUnavailable("provider reservation restaurant is missing")
    try:
        normalized_date = parse_date(day)
        normalized_time = normalize_time(time_value)
    except RestaurantBookError as exc:
        raise ProviderUnavailable("provider reservation time is malformed") from exc
    if type(party_size) is not int or not 1 <= party_size <= 20:
        raise ProviderUnavailable("provider reservation party size is malformed")
    if not isinstance(status_value, str):
        raise ProviderUnavailable("provider reservation status is malformed")
    status_value = status_value.strip().lower()
    if status_value not in ACTIVE_STATUSES | INACTIVE_STATUSES:
        raise ProviderUnavailable("provider reservation status is unknown")
    return {
        "platform": platform,
        "venue_id": str(venue_id) if venue_id is not None else None,
        "restaurant": restaurant.strip()[:200],
        "date": normalized_date,
        "time": normalized_time,
        "party_size": party_size,
        "status": status_value,
    }


def canonical_resy_venue_id(value: Any) -> str:
    if type(value) is int:
        if value <= 0:
            raise ProviderUnavailable("Resy venue identity is malformed")
        return str(value)
    if isinstance(value, str) and re.fullmatch(r"[1-9][0-9]*", value):
        return value
    raise ProviderUnavailable("Resy venue identity is malformed")


def complete_resy_reservation_rows(raw: Any) -> list[dict[str, Any]]:
    if not isinstance(raw, dict) or set(raw) != {
        "metadata",
        "reservations",
        "venues",
    }:
        raise ProviderUnavailable("Resy reservation envelope is malformed")
    metadata = raw.get("metadata")
    if not isinstance(metadata, dict) or set(metadata) != {"limit", "offset", "total"}:
        raise ProviderUnavailable("Resy reservation pagination is malformed")
    limit = metadata.get("limit")
    offset = metadata.get("offset")
    total = metadata.get("total")
    if any(type(value) is not int for value in (limit, offset, total)):
        raise ProviderUnavailable("Resy reservation pagination is malformed")
    reservations = raw.get("reservations")
    venues = raw.get("venues")
    if not isinstance(reservations, list) or not isinstance(venues, dict):
        raise ProviderUnavailable("Resy reservation page is malformed")
    if (
        offset != 0
        or not 0 <= total <= limit <= 100
        or total != len(reservations)
    ):
        raise ProviderUnavailable("Resy reservation page is incomplete")

    venue_names: dict[str, str] = {}
    for map_key, venue in venues.items():
        if not isinstance(map_key, str):
            raise ProviderUnavailable("Resy venue map identity is malformed")
        if not isinstance(venue, dict):
            raise ProviderUnavailable("Resy venue map entry is malformed")
        map_venue_id = canonical_resy_venue_id(map_key)
        venue_id = canonical_resy_venue_id(venue.get("id"))
        venue_name = venue.get("name")
        if venue_id != map_venue_id:
            raise ProviderUnavailable("Resy venue map identity is malformed")
        if not isinstance(venue_name, str) or not venue_name.strip():
            raise ProviderUnavailable("Resy venue map name is malformed")
        if venue_id in venue_names:
            raise ProviderUnavailable("Resy venue map identity is duplicated")
        venue_names[venue_id] = venue_name

    enriched: list[dict[str, Any]] = []
    for item in reservations:
        if not isinstance(item, dict):
            raise ProviderUnavailable("Resy reservation data is malformed")
        partial_venue = item.get("venue")
        if (
            not isinstance(partial_venue, dict)
            or not {"currency", "id"} <= set(partial_venue)
            or not set(partial_venue) <= {"currency", "id", "name"}
        ):
            raise ProviderUnavailable("Resy reservation venue is malformed")
        currency = partial_venue.get("currency")
        venue_id = canonical_resy_venue_id(partial_venue.get("id"))
        if not isinstance(currency, str) or not currency.strip():
            raise ProviderUnavailable("Resy reservation venue currency is malformed")
        venue_name = venue_names.get(venue_id)
        if venue_name is None:
            raise ProviderUnavailable("Resy reservation venue is missing")
        partial_name = partial_venue.get("name")
        if partial_name is not None and partial_name != venue_name:
            raise ProviderUnavailable("Resy reservation venue name conflicts")
        enriched_item = dict(item)
        enriched_item["venue"] = {
            **partial_venue,
            "id": venue_id,
            "name": venue_name,
        }
        enriched.append(enriched_item)
    return enriched


def reservation_conflict(scope: dict[str, Any], reservations: Iterable[dict[str, Any]]) -> dict[str, Any] | None:
    policy = scope["idempotency"]
    for reservation in reservations:
        if reservation["status"] in INACTIVE_STATUSES:
            continue
        if not policy["date_start"] <= reservation["date"] <= policy["date_end"]:
            continue
        if policy["party_size"] is not None and reservation["party_size"] != policy["party_size"]:
            continue
        if policy["time_start"] is not None:
            value = time_minutes(reservation["time"])
            if not time_minutes(policy["time_start"]) <= value <= time_minutes(policy["time_end"]):
                continue
        return reservation
    return None


def venue_metadata_allowed(
    scope: dict[str, Any],
    *,
    restaurant: str,
    cuisine: str,
    location: str,
    price_tier: int | None = None,
) -> bool:
    search = scope["search"]
    normalized_name = normalized_search_text(restaurant)
    if normalized_name in {
        normalized_search_text(value) for value in search["excluded_venues"]
    }:
        return False
    preferred = {
        normalized_search_text(value) for value in search["preferred_venues"]
    }
    if not search["allow_discovery"] and normalized_name not in preferred:
        return False
    minimum_price_tier = search.get("minimum_price_tier")
    if minimum_price_tier is not None and (
        type(price_tier) is not int or not 1 <= price_tier <= 4 or price_tier < minimum_price_tier
    ):
        return False
    return metadata_matches(cuisine, search["eligible_cuisine_terms"]) and metadata_matches(
        location, search["eligible_locality_terms"]
    )


def candidate_allowed(scope: dict[str, Any], candidate: Candidate) -> bool:
    reservation = scope["reservation"]
    search = scope["search"]
    if candidate.platform not in scope["providers"]:
        return False
    if candidate.date not in reservation["dates"] or candidate.party_size != reservation["party_size"]:
        return False
    try:
        slot = time_minutes(candidate.time)
    except RestaurantBookError:
        return False
    target = time_minutes(reservation["target_time"])
    if not time_minutes(reservation["window_start"]) <= slot <= time_minutes(reservation["window_end"]):
        return False
    if abs(slot - target) > reservation["max_delta_minutes"]:
        return False
    if not venue_metadata_allowed(
        scope,
        restaurant=candidate.restaurant,
        cuisine=candidate.cuisine,
        location=candidate.location,
        price_tier=candidate.price_tier,
    ):
        return False
    return fee_allowed(scope, candidate.payment)


def choose_candidate(scope: dict[str, Any], candidates: Iterable[Candidate]) -> Candidate | None:
    dates = {value: index for index, value in enumerate(scope["reservation"]["dates"])}
    providers = {value: index for index, value in enumerate(scope["selection"]["platform_tiebreak"])}
    preferred = {
        normalized_search_text(value): index
        for index, value in enumerate(scope["search"]["preferred_venues"])
    }
    target = time_minutes(scope["reservation"]["target_time"])
    eligible = [candidate for candidate in candidates if candidate_allowed(scope, candidate)]
    if not eligible:
        return None
    return min(
        eligible,
        key=lambda candidate: (
            dates[candidate.date],
            abs(time_minutes(candidate.time) - target),
            preferred.get(normalized_search_text(candidate.restaurant), len(preferred)),
            providers[candidate.platform],
            candidate.provider_rank,
            candidate.restaurant.casefold(),
        ),
    )


def resy_provider_call_budget(scope: dict[str, Any]) -> int:
    search = scope["search"]
    return search["max_search_attempts_per_provider"] + 2 * search["max_candidates_per_provider"]


class ResyProvider:
    def __init__(self, module_path: Path):
        self.module_path = module_path
        self.module: Any | None = None
        self.api: Any | None = None

    def _load(self) -> None:
        if self.api is not None:
            return
        try:
            resolved_module = self.module_path.resolve(strict=True)
            module_info = resolved_module.stat()
        except OSError as exc:
            raise ProviderUnavailable("Resy adapter is unavailable") from exc
        if (
            not stat.S_ISREG(module_info.st_mode)
            or module_info.st_uid != os.getuid()
            or module_info.st_mode & 0o022
        ):
            raise ProviderUnavailable("Resy adapter is unavailable")
        os.environ["RESY_CACHE_ONLY"] = "1"
        os.environ.pop("RESY_ALLOW_1PASSWORD", None)
        name = f"restaurant_book_resy_{os.getpid()}_{id(self)}"
        loader = importlib.machinery.SourceFileLoader(name, str(resolved_module))
        spec = importlib.util.spec_from_loader(name, loader)
        if spec is None or spec.loader is None:
            raise ProviderUnavailable("Resy adapter is unavailable")
        module = importlib.util.module_from_spec(spec)
        try:
            with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                spec.loader.exec_module(module)
                api = module.ResyAPI()
        except (Exception, SystemExit) as exc:
            raise ProviderUnavailable("Resy adapter is unavailable") from exc
        if getattr(api, "PRE_MUTATION_CHECK_CONTRACT", None) != 2:
            raise ProviderUnavailable("Resy mutation guard contract is unavailable")
        self.module = module
        self.api = api

    @staticmethod
    def _quiet(function: Callable[..., Any], *args: Any) -> Any:
        try:
            with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                return function(*args)
        except (ExistingReservation, AuthorizationExpired):
            raise
        except (Exception, SystemExit) as exc:
            raise ProviderUnavailable("Resy provider call failed") from exc

    def reservations(self, *, final: bool = False) -> list[dict[str, Any]]:
        self._load()
        assert self.api is not None
        if final:
            self.api._restaurant_snipe_final_guard = True
        try:
            raw = self._quiet(self.api.get_reservations)
        finally:
            if final:
                self.api._restaurant_snipe_final_guard = False
        reservations = complete_resy_reservation_rows(raw)
        return [normalize_reservation(item, "resy", resy_shape=True) for item in reservations]

    def search(self, scope: dict[str, Any]) -> tuple[list[Candidate], str]:
        self._load()
        assert self.api is not None
        candidates: list[Candidate] = []
        rank = 0
        had_failure = False
        incomplete = False
        provider_calls = 0
        call_budget = resy_provider_call_budget(scope)
        max_attempts = scope["search"]["max_search_attempts_per_provider"]
        max_candidates = scope["search"]["max_candidates_per_provider"]

        def bounded_call(function: Callable[..., Any], *args: Any) -> Any:
            nonlocal provider_calls
            if provider_calls >= call_budget:
                raise ProviderBudgetExhausted
            provider_calls += 1
            return self._quiet(function, *args)

        stop = False
        for query in scope["search"]["queries"][:max_attempts]:
            if stop or len(candidates) >= max_candidates:
                break
            try:
                raw_search = bounded_call(self.api.search, query)
            except ProviderBudgetExhausted:
                incomplete = True
                break
            except ProviderUnavailable:
                had_failure = True
                continue
            search_result = raw_search.get("search") if isinstance(raw_search, dict) else None
            if (
                not isinstance(raw_search, dict)
                or has_structured_failure(raw_search)
                or not isinstance(search_result, dict)
                or "hits" not in search_result
                or not isinstance(search_result.get("hits"), list)
            ):
                had_failure = True
                continue
            hits = search_result["hits"]
            for hit in hits[:5]:
                if stop or len(candidates) >= max_candidates:
                    break
                if not isinstance(hit, dict):
                    had_failure = True
                    continue
                venue_id = hit.get("id", {}).get("resy") if isinstance(hit.get("id"), dict) else None
                restaurant = hit.get("name")
                cuisine_values = hit.get("cuisine", [])
                location_data = hit.get("location", {})
                if (
                    not venue_id
                    or not isinstance(restaurant, str)
                    or not restaurant.strip()
                    or not isinstance(cuisine_values, list)
                    or not cuisine_values
                ):
                    had_failure = True
                    continue
                cuisine = joined_metadata(cuisine_values)
                price_tier = normalize_price_tier(hit.get("price_range_id"))
                location_values: list[Any] = [
                    hit.get("neighborhood"),
                    hit.get("locality"),
                    hit.get("region"),
                ]
                if isinstance(location_data, dict):
                    location_values.extend(
                        location_data.get(key)
                        for key in ("name", "neighborhood", "locality", "region", "city", "state")
                    )
                locality = joined_metadata(location_values)
                if not cuisine or not locality:
                    had_failure = True
                    continue
                if scope["search"]["minimum_price_tier"] is not None and price_tier is None:
                    if venue_metadata_allowed(
                        scope,
                        restaurant=restaurant,
                        cuisine=cuisine,
                        location=locality,
                        price_tier=scope["search"]["minimum_price_tier"],
                    ):
                        had_failure = True
                    continue
                if not venue_metadata_allowed(
                    scope,
                    restaurant=restaurant,
                    cuisine=cuisine,
                    location=locality,
                    price_tier=price_tier,
                ):
                    continue
                for candidate_date in scope["reservation"]["dates"]:
                    if stop or len(candidates) >= max_candidates:
                        break
                    try:
                        raw_availability = bounded_call(
                            self.api.find_availability,
                            str(venue_id),
                            candidate_date,
                            scope["reservation"]["party_size"],
                        )
                    except ProviderBudgetExhausted:
                        incomplete = True
                        stop = True
                        break
                    except ProviderUnavailable:
                        had_failure = True
                        continue
                    results = (
                        raw_availability.get("results")
                        if isinstance(raw_availability, dict)
                        else None
                    )
                    if (
                        not isinstance(raw_availability, dict)
                        or has_structured_failure(raw_availability)
                        or not isinstance(results, dict)
                        or "venues" not in results
                        or not isinstance(results.get("venues"), list)
                    ):
                        had_failure = True
                        continue
                    venues = results["venues"]
                    if any(
                        not isinstance(venue, dict) or not isinstance(venue.get("slots"), list)
                        for venue in venues
                    ):
                        had_failure = True
                        continue
                    eligible_slots: list[tuple[int, int, dict[str, Any]]] = []
                    for venue in venues:
                        for slot_index, slot in enumerate(venue["slots"]):
                            if not isinstance(slot, dict):
                                had_failure = True
                                continue
                            config = slot.get("config")
                            config_token = config.get("token") if isinstance(config, dict) else None
                            if not isinstance(config_token, str) or not config_token:
                                had_failure = True
                                continue
                            start = (
                                slot.get("date", {}).get("start")
                                if isinstance(slot.get("date"), dict)
                                else None
                            )
                            if not isinstance(start, str):
                                had_failure = True
                                continue
                            try:
                                slot_time = normalize_time(start)
                            except RestaurantBookError:
                                had_failure = True
                                continue
                            if candidate_date not in start:
                                continue
                            slot_minutes = time_minutes(slot_time)
                            target_minutes = time_minutes(scope["reservation"]["target_time"])
                            if not (
                                time_minutes(scope["reservation"]["window_start"])
                                <= slot_minutes
                                <= time_minutes(scope["reservation"]["window_end"])
                            ):
                                continue
                            if abs(slot_minutes - target_minutes) > scope["reservation"]["max_delta_minutes"]:
                                continue
                            eligible_slots.append(
                                (abs(slot_minutes - target_minutes), slot_index, slot)
                            )
                    eligible_slots.sort(key=lambda item: item[:2])
                    for _delta, _slot_index, slot in eligible_slots:
                        if len(candidates) >= max_candidates:
                            break
                        config = slot["config"]
                        config_token = config["token"]
                        start = slot["date"]["start"]
                        slot_time = normalize_time(start)
                        try:
                            details = bounded_call(
                                self.api.get_details,
                                config_token,
                                candidate_date,
                                scope["reservation"]["party_size"],
                            )
                        except ProviderBudgetExhausted:
                            incomplete = True
                            stop = True
                            break
                        except ProviderUnavailable:
                            had_failure = True
                            continue
                        if not isinstance(details, dict) or has_structured_failure(details):
                            had_failure = True
                            continue
                        payment = resy_payment(
                            slot,
                            details,
                            scope["reservation"]["party_size"],
                        )
                        cancellation, no_show = resy_policy_text(details)
                        proposed = Candidate(
                            platform="resy",
                            venue_id=str(venue_id),
                            restaurant=restaurant.strip(),
                            cuisine=cuisine,
                            location=locality,
                            date=candidate_date,
                            time=slot_time,
                            party_size=scope["reservation"]["party_size"],
                            seating=str(config.get("type", "Standard")),
                            payment=payment,
                            cancellation_policy=cancellation,
                            no_show_policy=no_show,
                            provider_rank=rank + 1,
                            price_tier=price_tier,
                            private={"config_token": config_token, "slot": slot},
                        )
                        if candidate_allowed(scope, proposed):
                            rank += 1
                            candidates.append(proposed)
        incomplete = incomplete or len(scope["search"]["queries"]) > max_attempts
        status = (
            "partial"
            if (had_failure or incomplete) and candidates
            else "unavailable"
            if had_failure or incomplete
            else "ok"
        )
        return candidates, status

    def prepare(self, candidate: Candidate) -> tuple[str, str]:
        self._load()
        assert self.api is not None
        config_token = candidate.private.get("config_token")
        if not isinstance(config_token, str) or not config_token:
            raise ProviderUnavailable("Resy exact booking input is unavailable")
        details = self._quiet(
            self.api.get_details,
            config_token,
            candidate.date,
            candidate.party_size,
        )
        if not isinstance(details, dict):
            raise ProviderUnavailable("Resy exact booking details are malformed")
        slot = candidate.private.get("slot")
        if not isinstance(slot, dict):
            raise ProviderUnavailable("Resy exact slot facts are unavailable")
        if resy_payment(slot, details, candidate.party_size) != candidate.payment:
            raise ProviderUnavailable("Resy payment terms changed before booking")
        cancellation, no_show = resy_policy_text(details)
        if cancellation != candidate.cancellation_policy or no_show != candidate.no_show_policy:
            raise ProviderUnavailable("Resy policy terms changed before booking")
        config = details.get("config")
        if not isinstance(config, dict):
            raise ProviderUnavailable("Resy exact booking configuration is malformed")
        date_info = config.get("date")
        start = date_info.get("start") if isinstance(date_info, dict) else None
        try:
            exact_time = normalize_time(start)
        except RestaurantBookError as exc:
            raise ProviderUnavailable("Resy exact booking time is malformed") from exc
        if candidate.date not in str(start) or exact_time != candidate.time:
            raise ProviderUnavailable("Resy exact booking time changed")
        venue = details.get("venue")
        if not isinstance(venue, dict) or str(venue.get("id")) != candidate.venue_id:
            raise ProviderUnavailable("Resy exact venue changed")
        if str(venue.get("name", "")).strip() != candidate.restaurant:
            raise ProviderUnavailable("Resy exact restaurant changed")
        book_token = (
            details.get("book_token", {}).get("value")
            if isinstance(details.get("book_token"), dict)
            else None
        )
        payment_id = self._quiet(self.api.creds.get, "payment_id")
        if not isinstance(book_token, str) or not book_token or not payment_id:
            raise ProviderUnavailable("Resy cache-only booking credentials are unavailable")
        return book_token, str(payment_id)

    def book(
        self,
        prepared: tuple[str, str],
        live_guard: Callable[[], None],
        mutation_boundary: Callable[[], None],
    ) -> Any:
        self._load()
        assert self.api is not None
        self.api._pre_booking_guard = live_guard
        self.api._pre_mutation_check = mutation_boundary
        return self._quiet(self.api.book, *prepared)


def _meaningful_term(value: Any) -> bool:
    if value in (None, False, "", 0, [], {}):
        return False
    if isinstance(value, str):
        return normalized_search_text(value) not in {"", "free", "none", "no", "not required"}
    return True


def opentable_slot_payment(slot: dict[str, Any]) -> dict[str, Any]:
    """Classify only explicit card-guarantee/no-card structured slot terms."""
    card_keys = {
        "creditcardrequired",
        "iscreditcardrequired",
        "requirescreditcard",
        "isccrequired",
        "requirescc",
    }
    monetary_semantic_fragments = (
        "amount",
        "cancellation",
        "charge",
        "cost",
        "deposit",
        "fee",
        "hold",
        "monetary",
        "money",
        "noshow",
        "payment",
        "penalty",
        "prepay",
        "prepaid",
        "price",
        "surcharge",
    )
    ambiguous_keys = {
        "creditcardpolicytype",
        "creditcardpolicy",
        "experience",
        "offer",
        "premium",
        "paymentterms",
    }
    structured_term_keys = {
        "bookingterms",
        "cancellationpolicy",
        "noshowpolicy",
        "servicechargepolicy",
    }
    known_scalar_term_keys = {
        "cancellationfee",
        "costperguest",
        "deposit",
        "depositamount",
        "duenow",
        "isprepaid",
        "noshowfee",
        "paymentamount",
        "paymentrequired",
        "priceamount",
        "priceperguest",
        "servicecharge",
    }
    card_values: list[bool] = []
    unknown = False

    def payment_structure_known(value: Any) -> bool:
        if value in (None, {}, []):
            return True
        if not isinstance(value, dict):
            return False
        known_containers = {
            "amounts",
            "cancellation",
            "charges",
            "fees",
            "noshow",
            "paymentterms",
            "policy",
            "terms",
        }
        for raw_key, child in value.items():
            key = re.sub(r"[^a-z0-9]", "", str(raw_key).casefold())
            known_term = (
                key in card_keys
                or key in ambiguous_keys
                or key in BOOKING_TERM_KEYS
                or key in known_containers
                or any(fragment in key for fragment in monetary_semantic_fragments)
                or any(fragment in key for fragment in BOOKING_TERM_FRAGMENTS)
            )
            if key in BOOKING_TERM_STRUCTURAL_KEYS:
                continue
            if key == "type":
                if normalized_search_text(child) not in {
                    "",
                    "free",
                    "none",
                    "not required",
                }:
                    return False
                continue
            if not known_term:
                if not _empty_booking_value(child):
                    return False
                continue
            if isinstance(child, (dict, list)):
                items = child if isinstance(child, list) else [child]
                if not all(payment_structure_known(item) for item in items):
                    return False
        return True

    def inspect(value: Any, *, slot_root: bool = False) -> None:
        nonlocal unknown
        if isinstance(value, dict):
            for raw_key, child in value.items():
                key = re.sub(r"[^a-z0-9]", "", str(raw_key).casefold())
                if key in card_keys:
                    if type(child) is bool:
                        card_values.append(child)
                    else:
                        unknown = True
                elif key == "payment" and slot_root:
                    pass
                elif key in structured_term_keys:
                    pass
                elif any(
                    fragment in key for fragment in monetary_semantic_fragments
                ):
                    if (
                        _meaningful_term(child)
                        if key in known_scalar_term_keys
                        else not _empty_booking_value(child)
                    ):
                        unknown = True
                elif key in ambiguous_keys and _meaningful_term(child):
                    unknown = True
                inspect(child)
        elif isinstance(value, list):
            for child in value:
                inspect(child)

    inspect(slot, slot_root=True)
    if "payment" in slot and not payment_structure_known(slot.get("payment")):
        unknown = True
    if not structured_booking_terms_safe(slot):
        unknown = True
    slot_type = normalized_search_text(slot.get("type"))
    if any(term in slot_type.split() for term in ("experience", "prepaid", "premium")):
        unknown = True
    # The mobile availability schema omits the card-required field entirely
    # for ordinary no-card slots and emits it only for card/HOLD inventory.
    # A present malformed or contradictory flag remains unknown.
    if card_values and len(set(card_values)) != 1:
        unknown = True
    # A required card can secure an undisclosed per-person hold, cancellation,
    # or no-show amount. The current availability shape does not affirmatively
    # enumerate all such amounts, so zero-fee scopes must reject it.
    if card_values and card_values[0] is True:
        unknown = True
    return payment_facts(
        card_guarantee=bool(card_values and card_values[0]),
        terms_known=not unknown,
    )


class OpenTableProvider:
    def __init__(
        self,
        home: Path,
        *,
        module_path: Path | None = None,
        browser_helper: Path | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ):
        self.home = home
        self.module_path = module_path or Path("/opt/homebrew/bin/opentable")
        self.browser_helper = browser_helper or home / ".openclaw" / "bin" / "pinchtab-headless-instance"
        self.reservations_command = home / ".openclaw" / "bin" / "opentable-reservations"
        self.sleep = sleep
        self.module: Any | None = None
        self.api: Any | None = None

    def _load(self) -> None:
        if self.api is not None:
            return
        try:
            resolved_module = self.module_path.resolve(strict=True)
            module_info = resolved_module.stat()
        except OSError as exc:
            raise ProviderUnavailable("OpenTable adapter is unavailable") from exc
        if (
            not stat.S_ISREG(module_info.st_mode)
            or module_info.st_uid != os.getuid()
            or module_info.st_mode & 0o022
        ):
            raise ProviderUnavailable("OpenTable adapter is unavailable")
        os.environ["OPENTABLE_CACHE_ONLY"] = "1"
        name = f"restaurant_book_opentable_{os.getpid()}_{id(self)}"
        loader = importlib.machinery.SourceFileLoader(name, str(resolved_module))
        spec = importlib.util.spec_from_loader(name, loader)
        if spec is None or spec.loader is None:
            raise ProviderUnavailable("OpenTable adapter is unavailable")
        module = importlib.util.module_from_spec(spec)
        try:
            with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                spec.loader.exec_module(module)
                credential_class = module.OpenTableCredentials
                credential_class._op_read = lambda _self, _field: None
                api = module.OpenTableAPI()
        except (Exception, SystemExit) as exc:
            raise ProviderUnavailable("OpenTable adapter is unavailable") from exc
        if getattr(api, "PRE_MUTATION_CHECK_CONTRACT", None) != 2:
            raise ProviderUnavailable("OpenTable mutation guard contract is unavailable")
        if (
            getattr(api, "EXTERNAL_PRE_BOOKING_GUARD_CONTRACT", None)
            != OPENTABLE_EXTERNAL_GUARD_CONTRACT
        ):
            raise ProviderUnavailable("OpenTable external guard contract is unavailable")
        self.module = module
        self.api = api

    @staticmethod
    def _quiet(function: Callable[..., Any], *args: Any) -> Any:
        try:
            with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                return function(*args)
        except (ExistingReservation, AuthorizationExpired):
            raise
        except (Exception, SystemExit) as exc:
            raise ProviderUnavailable("OpenTable provider call failed") from exc

    @staticmethod
    def _json_command(
        command: list[str],
        *,
        timeout_seconds: int = 240,
        extra_env: dict[str, str] | None = None,
        pass_fds: tuple[int, ...] = (),
    ) -> tuple[dict[str, Any], int]:
        env = os.environ.copy()
        if extra_env:
            env.update(extra_env)
        try:
            completed = subprocess.run(
                command,
                check=False,
                capture_output=True,
                text=True,
                timeout=timeout_seconds,
                env=env,
                pass_fds=pass_fds,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            raise ProviderUnavailable("OpenTable provider command failed") from exc
        try:
            payload = json.loads(completed.stdout)
        except json.JSONDecodeError as exc:
            raise ProviderUnavailable("OpenTable provider response is malformed") from exc
        if not isinstance(payload, dict):
            raise ProviderUnavailable("OpenTable provider response is malformed")
        return payload, completed.returncode

    def reservations(self, *, final: bool = False) -> list[dict[str, Any]]:
        del final
        if not self.reservations_command.is_file() or self.reservations_command.is_symlink():
            raise ProviderUnavailable("OpenTable reservation reader is unavailable")
        payload, returncode = self._json_command([str(self.reservations_command), "--json"])
        if returncode != 0 or payload.get("success") is not True or payload.get("provider") != "opentable":
            raise ProviderUnavailable("OpenTable reservation read failed")
        checked_at = parse_timestamp(payload.get("checked_at"), "OpenTable checked_at")
        now = utc_now()
        if checked_at < now - timedelta(minutes=2) or checked_at > now + timedelta(seconds=10):
            raise ProviderUnavailable("OpenTable reservation read is stale")
        reservations = payload.get("reservations")
        if not isinstance(reservations, list):
            raise ProviderUnavailable("OpenTable reservation list is missing")
        return [normalize_reservation(item, "opentable") for item in reservations]

    def _validate_browser_helper(self) -> None:
        try:
            info = self.browser_helper.resolve(strict=True).stat()
        except OSError as exc:
            raise ProviderUnavailable("OpenTable browser discovery is unavailable") from exc
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_uid != os.getuid()
            or info.st_mode & 0o022
            or not os.access(self.browser_helper, os.X_OK)
        ):
            raise ProviderUnavailable("OpenTable browser discovery is unavailable")

    def _browser_command(self, *arguments: str, timeout_seconds: int = 60) -> str:
        self._validate_browser_helper()
        try:
            completed = subprocess.run(
                [str(self.browser_helper), *arguments],
                check=False,
                capture_output=True,
                text=True,
                timeout=timeout_seconds,
                env=os.environ.copy(),
            )
        except (OSError, subprocess.SubprocessError) as exc:
            raise ProviderUnavailable("OpenTable browser discovery failed") from exc
        if completed.returncode != 0:
            raise ProviderUnavailable("OpenTable browser discovery failed")
        return completed.stdout.strip()

    @staticmethod
    def _browser_eval_payload(raw: str) -> dict[str, Any]:
        try:
            outer = json.loads(raw)
            value = outer.get("result") if isinstance(outer, dict) else None
            if isinstance(value, str):
                value = json.loads(value)
        except (json.JSONDecodeError, TypeError, ValueError) as exc:
            raise ProviderUnavailable("OpenTable browser discovery response is malformed") from exc
        if not isinstance(value, dict):
            raise ProviderUnavailable("OpenTable browser discovery response is malformed")
        return value

    def _discover_query(
        self,
        instance_id: str,
        scope: dict[str, Any],
        query: str,
        requested_date: str,
    ) -> list[dict[str, Any]]:
        reservation = scope["reservation"]
        requested_datetime = f"{requested_date}T{reservation['target_time']}:00"
        parameters = urllib.parse.urlencode(
            {
                "covers": str(reservation["party_size"]),
                "dateTime": requested_datetime,
                "metroId": "7",
                "term": query,
            }
        )
        url = f"https://www.opentable.com/s?{parameters}"
        tab_id = self._browser_command("open", instance_id, url)
        if not re.fullmatch(r"[A-Za-z0-9_.:-]{4,160}", tab_id):
            raise ProviderUnavailable("OpenTable browser returned an invalid tab")
        javascript = r"""(function(){
          const clean=(value,limit=300)=>String(value ?? '').replace(/\s+/g,' ').trim().slice(0,limit);
          const params=new URL(window.location.href).searchParams;
          const context={
            origin:window.location.origin,
            path:window.location.pathname,
            covers:params.get('covers') || '',
            dateTime:params.get('dateTime') || '',
            metroId:params.get('metroId') || '',
            term:params.get('term') || ''
          };
          const source=window.__INITIAL_STATE__?.multiSearch?.restaurants;
          if(!Array.isArray(source)) return JSON.stringify({...context,status:'loading',restaurants:[]});
          const restaurants=source.slice(0,25).map(item=>({
            restaurant_id:clean(item?.restaurantId,80),
            name:clean(item?.name,200),
            cuisine:clean(item?.primaryCuisine?.name,160),
            neighborhood:clean(item?.neighborhood?.name,160),
            line1:clean(item?.address?.line1,200),
            city:clean(item?.address?.city,120),
            state:clean(item?.address?.state,80),
            dining_style:clean(item?.diningStyle?.name || item?.diningStyle,160),
            price_tier:Number.isInteger(item?.priceBand?.priceBandId) ? item.priceBand.priceBandId : 0
          }));
          return JSON.stringify({...context,status:'ready',restaurants});
        })()"""
        try:
            result: dict[str, Any] | None = None
            for _ in range(8):
                result = self._browser_eval_payload(
                    self._browser_command("eval", instance_id, tab_id, javascript)
                )
                if (
                    result.get("origin") != "https://www.opentable.com"
                    or result.get("path") != "/s"
                    or result.get("covers") != str(reservation["party_size"])
                    or result.get("dateTime") != requested_datetime
                    or result.get("metroId") != "7"
                    or result.get("term") != query
                ):
                    raise ProviderUnavailable("OpenTable browser search context changed")
                if result.get("status") == "ready":
                    break
                if result.get("status") != "loading":
                    raise ProviderUnavailable("OpenTable browser discovery response is malformed")
                self.sleep(1)
            if result is None or result.get("status") != "ready":
                raise ProviderUnavailable("OpenTable browser discovery timed out")
            restaurants = result.get("restaurants")
            if not isinstance(restaurants, list):
                raise ProviderUnavailable("OpenTable browser discovery response is malformed")
            normalized: list[dict[str, Any]] = []
            expected = {
                "restaurant_id",
                "name",
                "cuisine",
                "neighborhood",
                "line1",
                "city",
                "state",
                "dining_style",
                "price_tier",
            }
            for raw in restaurants:
                if not isinstance(raw, dict) or set(raw) != expected:
                    raise ProviderUnavailable("OpenTable restaurant metadata is malformed")
                if any(
                    not isinstance(value, str)
                    for key, value in raw.items()
                    if key != "price_tier"
                ) or type(raw["price_tier"]) is not int:
                    raise ProviderUnavailable("OpenTable restaurant metadata is malformed")
                if not raw["restaurant_id"] or not raw["name"] or not raw["cuisine"]:
                    raise ProviderUnavailable("OpenTable restaurant metadata omitted core facts")
                normalized.append(
                    {
                        "venue_id": raw["restaurant_id"],
                        "restaurant": raw["name"],
                        "cuisine": raw["cuisine"],
                        "location": joined_metadata(
                            (raw["neighborhood"], raw["city"], raw["state"])
                        ),
                        "dining_style": raw["dining_style"],
                        "price_tier": normalize_price_tier(raw["price_tier"]),
                    }
                )
            return normalized
        finally:
            with contextlib.suppress(ProviderUnavailable):
                self._browser_command("close", instance_id, tab_id, timeout_seconds=30)

    def _availability_candidates(
        self,
        scope: dict[str, Any],
        metadata: dict[str, Any],
        candidate_date: str,
        raw: Any,
        rank: int,
        *,
        closest_only: bool,
    ) -> list[Candidate]:
        if not isinstance(raw, dict) or has_structured_failure(raw):
            raise ProviderUnavailable("OpenTable availability response is malformed")
        days = raw.get("suggestedAvailability")
        if not isinstance(days, list):
            raise ProviderUnavailable("OpenTable availability response is malformed")
        candidates: list[Candidate] = []
        for day in days:
            if not isinstance(day, dict) or not isinstance(day.get("timeslots", []), list):
                raise ProviderUnavailable("OpenTable availability response is malformed")
            for slot in day.get("timeslots", []):
                if not isinstance(slot, dict) or slot.get("available") is not True:
                    continue
                token = slot.get("token")
                slot_hash = slot.get("slotHash")
                slot_type = slot.get("type", "Standard")
                if any(not isinstance(value, str) or not value for value in (token, slot_hash, slot_type)):
                    raise ProviderUnavailable("OpenTable available slot omitted booking identity")
                start = slot.get("dateTime")
                if not isinstance(start, str):
                    raise ProviderUnavailable("OpenTable available slot omitted its datetime")
                try:
                    slot_date = parse_date(start[:10])
                except RestaurantBookError as exc:
                    raise ProviderUnavailable("OpenTable available slot datetime is malformed") from exc
                if slot_date != candidate_date:
                    continue
                try:
                    slot_time = normalize_time(start)
                except RestaurantBookError as exc:
                    raise ProviderUnavailable("OpenTable available slot datetime is malformed") from exc
                areas = slot.get("diningAreas", [])
                if not isinstance(areas, list) or any(not isinstance(area, dict) for area in areas):
                    raise ProviderUnavailable("OpenTable dining-area data is malformed")
                dining_area_id: str | None = None
                area_label = ""
                if areas:
                    raw_id = areas[0].get("id")
                    raw_label = areas[0].get("environment") or areas[0].get("name")
                    if raw_id is not None and not isinstance(raw_id, (str, int)):
                        raise ProviderUnavailable("OpenTable dining-area data is malformed")
                    dining_area_id = str(raw_id) if raw_id not in (None, "") else None
                    area_label = raw_label.strip() if isinstance(raw_label, str) else ""
                payment = opentable_slot_payment(slot)
                terms = (
                    "No cancellation fee reported by structured availability"
                    if payment["terms_known"]
                    else "OpenTable payment or cancellation terms unavailable"
                )
                proposed = Candidate(
                    platform="opentable",
                    venue_id=metadata["venue_id"],
                    restaurant=metadata["restaurant"],
                    cuisine=metadata["cuisine"],
                    location=metadata["location"],
                    date=candidate_date,
                    time=slot_time,
                    party_size=scope["reservation"]["party_size"],
                    seating=", ".join(value for value in (slot_type, area_label) if value),
                    payment=payment,
                    cancellation_policy=terms,
                    no_show_policy=(
                        "No no-show fee reported by structured availability"
                        if payment["terms_known"]
                        else "OpenTable no-show terms unavailable"
                    ),
                    provider_rank=rank,
                    price_tier=metadata["price_tier"],
                    private={
                        "slot_token": token,
                        "slot_hash": slot_hash,
                        "slot_datetime": start,
                        "dining_area_id": dining_area_id,
                    },
                )
                if candidate_allowed(scope, proposed):
                    candidates.append(proposed)
        candidates.sort(
            key=lambda item: (
                abs(time_minutes(item.time) - time_minutes(scope["reservation"]["target_time"])),
                time_minutes(item.time),
                item.seating.casefold(),
            )
        )
        return candidates[:1] if closest_only else candidates

    def search(self, scope: dict[str, Any]) -> tuple[list[Candidate], str]:
        self._load()
        assert self.api is not None
        candidates: list[Candidate] = []
        had_failure = False
        metadata_by_id: dict[str, dict[str, Any]] = {}
        instance_id = ""
        started = "0"
        try:
            lease = self._browser_command("acquire", "opentable")
            parts = lease.split("\t")
            if len(parts) != 2 or not re.fullmatch(r"[A-Za-z0-9_.:-]{4,160}", parts[0]) or parts[1] not in {"0", "1"}:
                raise ProviderUnavailable("OpenTable browser returned an invalid lease")
            instance_id, started = parts
            requested_date = scope["reservation"]["dates"][0]
            for query in scope["search"]["queries"][: scope["search"]["max_search_attempts_per_provider"]]:
                try:
                    discovered = self._discover_query(instance_id, scope, query, requested_date)
                except ProviderUnavailable:
                    had_failure = True
                    continue
                for metadata in discovered:
                    if (
                        scope["search"]["minimum_price_tier"] is not None
                        and metadata["price_tier"] is None
                    ):
                        if venue_metadata_allowed(
                            scope,
                            restaurant=metadata["restaurant"],
                            cuisine=metadata["cuisine"],
                            location=metadata["location"],
                            price_tier=scope["search"]["minimum_price_tier"],
                        ):
                            had_failure = True
                        continue
                    if not venue_metadata_allowed(
                        scope,
                        restaurant=metadata["restaurant"],
                        cuisine=metadata["cuisine"],
                        location=metadata["location"],
                        price_tier=metadata["price_tier"],
                    ):
                        continue
                    existing = metadata_by_id.get(metadata["venue_id"])
                    if existing is not None and existing != metadata:
                        raise ProviderUnavailable("OpenTable restaurant metadata changed during discovery")
                    metadata_by_id.setdefault(metadata["venue_id"], metadata)
        except ProviderUnavailable:
            had_failure = True
        finally:
            if instance_id:
                with contextlib.suppress(ProviderUnavailable):
                    self._browser_command("release", instance_id, started, timeout_seconds=30)

        attempts = 0
        max_attempts = scope["search"]["max_search_attempts_per_provider"]
        max_candidates = scope["search"]["max_candidates_per_provider"]
        rank = 0
        for metadata in metadata_by_id.values():
            for candidate_date in scope["reservation"]["dates"]:
                if attempts >= max_attempts or len(candidates) >= max_candidates:
                    break
                attempts += 1
                try:
                    raw = self._quiet(
                        self.api.find_availability,
                        metadata["venue_id"],
                        candidate_date,
                        scope["reservation"]["target_time"],
                        scope["reservation"]["party_size"],
                    )
                    found = self._availability_candidates(
                        scope,
                        metadata,
                        candidate_date,
                        raw,
                        rank + 1,
                        closest_only=True,
                    )
                except ProviderUnavailable:
                    had_failure = True
                    continue
                if found:
                    rank += 1
                    candidates.extend(found)
            if attempts >= max_attempts or len(candidates) >= max_candidates:
                break
        status = "partial" if had_failure and candidates else "unavailable" if had_failure and not candidates else "ok"
        return candidates, status

    def refresh(self, scope: dict[str, Any], candidate: Candidate) -> Candidate:
        self._load()
        assert self.api is not None
        raw = self._quiet(
            self.api.find_availability,
            candidate.venue_id,
            candidate.date,
            candidate.time,
            candidate.party_size,
        )
        metadata = {
            "venue_id": candidate.venue_id,
            "restaurant": candidate.restaurant,
            "cuisine": candidate.cuisine,
            "location": candidate.location,
            "dining_style": "",
            "price_tier": candidate.price_tier,
        }
        refreshed = self._availability_candidates(
            scope,
            metadata,
            candidate.date,
            raw,
            candidate.provider_rank,
            closest_only=False,
        )
        exact = [
            item
            for item in refreshed
            if item.time == candidate.time and item.digest() == candidate.digest()
        ]
        if len(exact) != 1:
            raise ProviderUnavailable("OpenTable exact availability changed before booking")
        return exact[0]

    def book(
        self,
        candidate: Candidate,
        live_guard: Callable[[], None],
        mutation_boundary: Callable[[], None],
    ) -> Any:
        self._load()
        assert self.api is not None
        token = candidate.private.get("slot_token")
        slot_hash = candidate.private.get("slot_hash")
        slot_datetime = candidate.private.get("slot_datetime")
        dining_area_id = candidate.private.get("dining_area_id")
        if any(not isinstance(value, str) or not value for value in (token, slot_hash, slot_datetime)):
            raise ProviderUnavailable("OpenTable exact booking input is unavailable")
        if dining_area_id is not None and not isinstance(dining_area_id, str):
            raise ProviderUnavailable("OpenTable dining-area input is unavailable")
        self.api._pre_booking_guard = live_guard
        self.api._pre_mutation_check = mutation_boundary
        self.api._external_pre_booking_guard_contract = OPENTABLE_EXTERNAL_GUARD_CONTRACT
        try:
            return self._quiet(
                self.api.book,
                candidate.venue_id,
                token,
                slot_hash,
                slot_datetime,
                candidate.party_size,
                dining_area_id,
            )
        finally:
            for attribute in (
                "_pre_booking_guard",
                "_pre_mutation_check",
                "_external_pre_booking_guard_contract",
            ):
                with contextlib.suppress(AttributeError):
                    delattr(self.api, attribute)


def safe_reservation(reservation: dict[str, Any]) -> dict[str, Any]:
    return {
        "platform": reservation["platform"],
        "restaurant": reservation["restaurant"],
        "date": reservation["date"],
        "time": reservation["time"],
        "party_size": reservation["party_size"],
        "status": reservation["status"],
    }


def meaningful_failure_value(value: Any) -> bool:
    if value is None or value is False or value == 0:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, (list, dict)):
        return bool(value)
    return True


def has_structured_failure(value: Any) -> bool:
    if isinstance(value, dict):
        for raw_key, child in value.items():
            normalized = re.sub(r"[^a-z0-9]", "", str(raw_key).casefold())
            if normalized in {"issuccessful", "ok", "success", "successful"} and child is not True:
                return True
            if (
                normalized.startswith(("error", "exception", "failure"))
                or normalized
                in {
                    "cancelled",
                    "canceled",
                    "declined",
                    "failed",
                    "haserror",
                    "hasfailed",
                    "iscancelled",
                    "iscanceled",
                    "isdeclined",
                    "iserror",
                    "isfailed",
                    "isrejected",
                    "rejected",
                }
            ) and meaningful_failure_value(child):
                return True
            if normalized in {"status", "bookingstatus", "reservationstatus"}:
                if str(child).strip().casefold() in FAILURE_STATUS_VALUES:
                    return True
            if has_structured_failure(child):
                return True
    elif isinstance(value, list):
        return any(has_structured_failure(item) for item in value)
    return False


def nested_items(value: Any, parent: str = "") -> Iterable[tuple[str, Any]]:
    if isinstance(value, dict):
        for key, child in value.items():
            normalized = re.sub(r"[^a-z0-9]", "", str(key).casefold())
            path = f"{parent}.{normalized}" if parent else normalized
            yield path, child
            yield from nested_items(child, path)
    elif isinstance(value, list):
        for child in value:
            yield from nested_items(child, parent)


def confirmation_context(path_parts: list[str]) -> str | None:
    if len(path_parts) == 1:
        return "$"
    parents = path_parts[:-1]
    if (
        parents[-1] in {"booking", "reservation"}
        and all(part in CURRENT_RESULT_WRAPPERS for part in parents[:-1])
    ):
        return ".".join(parents)
    return None


def strict_confirmation_id(result: Any) -> str | None:
    if not isinstance(result, dict) or has_structured_failure(result):
        return None
    identifiers: dict[str, set[str]] = {}
    affirmations: set[str] = set()
    contradiction = False
    for path, value in nested_items(result):
        key = path.rsplit(".", 1)[-1]
        context = confirmation_context(path.split("."))
        valid_identifier = (
            not isinstance(value, bool)
            and isinstance(value, (str, int))
            and str(value).strip() != ""
            and not (isinstance(value, int) and value == 0)
        )
        contextual = key in CONTEXTUAL_CONFIRMATION_KEYS
        if context is not None and valid_identifier and (
            key in CONFIRMATION_KEYS or (key == "id" and context != "$")
        ):
            identifiers.setdefault(context, set()).add(str(value).strip())
        if key in CONFIRMATION_FLAG_KEYS:
            if value is True and context is not None:
                affirmations.add(context)
            else:
                contradiction = True
        if context is not None and (key in CONFIRMATION_STATUS_KEYS or key == "status"):
            if value not in (None, "") and str(value).strip().casefold() in CONFIRMED_STATUS_VALUES:
                affirmations.add(context)
            elif value not in (None, ""):
                contradiction = True
        if contextual and context is None and valid_identifier:
            contradiction = True
    if contradiction:
        return None
    correlated: set[str] = set()
    for context, values in identifiers.items():
        if context in affirmations or context == "$" and affirmations or "$" in affirmations:
            correlated.update(values)
    return next(iter(correlated)) if len(correlated) == 1 else None


def opentable_confirmation_correlates(payload: Any, candidate: Candidate) -> bool:
    if not isinstance(payload, dict):
        return False
    venue_keys = {"restaurantid", "rid", "venueid"}
    name_keys = {"restaurantname", "venuename"}
    date_keys = {"confirmeddate", "date", "reservationdate"}
    time_keys = {"confirmedtime", "reservationtime", "time"}
    datetime_keys = {"confirmeddatetime", "datetime", "reservationdatetime"}
    party_keys = {"covers", "guests", "numguests", "numseats", "party", "partysize"}

    for path, value in nested_items(payload):
        parts = path.split(".")
        key = parts[-1]
        context = confirmation_context(parts)
        if context is None:
            continue
        if key in venue_keys:
            if isinstance(value, bool) or not isinstance(value, (str, int)):
                return False
            if str(value).strip() != candidate.venue_id:
                return False
        elif key in {"restaurant", "venue"}:
            if isinstance(value, str):
                if normalized_identity_text(value) != normalized_identity_text(candidate.restaurant):
                    return False
            elif isinstance(value, dict):
                present_id = next(
                    (value.get(raw_key) for raw_key in ("restaurantId", "rid", "venueId") if raw_key in value),
                    None,
                )
                present_name = next(
                    (value.get(raw_key) for raw_key in ("name", "restaurantName", "venueName") if raw_key in value),
                    None,
                )
                if present_id is not None and str(present_id).strip() != candidate.venue_id:
                    return False
                if present_name is not None and (
                    not isinstance(present_name, str)
                    or normalized_identity_text(present_name)
                    != normalized_identity_text(candidate.restaurant)
                ):
                    return False
            else:
                return False
        elif key in name_keys:
            if not isinstance(value, str) or normalized_identity_text(value) != normalized_identity_text(
                candidate.restaurant
            ):
                return False
        elif key in date_keys:
            try:
                if parse_date(value) != candidate.date:
                    return False
            except RestaurantBookError:
                return False
        elif key in time_keys:
            try:
                if normalize_time(value) != candidate.time:
                    return False
            except RestaurantBookError:
                return False
        elif key in datetime_keys:
            try:
                if parse_date(str(value)[:10]) != candidate.date or normalize_time(value) != candidate.time:
                    return False
            except RestaurantBookError:
                return False
        elif key in party_keys:
            if isinstance(value, bool):
                return False
            try:
                party_size = int(value)
            except (TypeError, ValueError):
                return False
            if str(party_size) != str(value).strip() and type(value) is not int:
                return False
            if party_size != candidate.party_size:
                return False
    return True


class Coordinator:
    def __init__(
        self,
        *,
        home: Path | None = None,
        scopes_path: Path | None = None,
        resy: Any | None = None,
        opentable: Any | None = None,
        now: Callable[[], datetime] = utc_now,
    ):
        self.home = (home or Path.home()).resolve()
        self.scopes_path = scopes_path or self.home / ".openclaw" / "restaurant-bookings" / "scopes.json"
        self.shared_state_root = self.home / ".openclaw" / "restaurant-snipes" / "state"
        self.mutation_lock_path = self.shared_state_root / "booking-mutation.lock"
        self.resy = resy or ResyProvider(Path("/opt/homebrew/bin/resy"))
        self.opentable = opentable or OpenTableProvider(self.home)
        self.now = now

    def scope(self, job_id: str) -> tuple[dict[str, Any], str]:
        return load_scope_registry(self.scopes_path, job_id)

    def state_dir(self, job_id: str) -> Path:
        if not JOB_RE.fullmatch(job_id):
            raise RestaurantBookError("invalid canonical job ID")
        return self.shared_state_root / f"cron-{job_id}"

    def _base(
        self,
        command: str,
        job_id: str,
        scope_digest: str,
        status: str,
        **extra: Any,
    ) -> dict[str, Any]:
        result: dict[str, Any] = {
            "schema_version": SCHEMA_VERSION,
            "command": command,
            "job_id": job_id,
            "scope_digest": scope_digest,
            "status": status,
            "mutation_attempted": False,
            "reservation_may_exist": False,
        }
        result.update(extra)
        return result

    def _authorization_current(self, scope: dict[str, Any]) -> bool:
        now = self.now().astimezone(timezone.utc)
        return (
            parse_timestamp(scope["authorization"]["not_before"], "not_before")
            <= now
            < parse_timestamp(scope["authorization"]["expires_at"], "expires_at")
        )

    def _require_authorization_current(self, scope: dict[str, Any]) -> None:
        if not self._authorization_current(scope):
            raise AuthorizationExpired("standing authorization is not active")

    def _read_both(
        self,
        *,
        chosen_last: str | None = None,
        final: bool = False,
    ) -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]]]:
        providers = {"resy": self.resy, "opentable": self.opentable}
        order = list(PLATFORMS)
        if chosen_last in providers:
            order = [name for name in order if name != chosen_last] + [chosen_last]
        reservations: list[dict[str, Any]] = []
        statuses = {
            name: {"reservations": "not_checked", "search": "not_attempted", "candidates": 0}
            for name in PLATFORMS
        }
        errors: list[str] = []
        for name in order:
            try:
                current = providers[name].reservations(final=final)
                if not isinstance(current, list):
                    raise ProviderUnavailable("reservation reader did not return a list")
                reservations.extend(current)
                statuses[name]["reservations"] = "ok"
            except (ProviderUnavailable, RestaurantBookError, OSError, ValueError):
                statuses[name]["reservations"] = "unavailable"
                errors.append(name)
        if errors:
            raise ProviderUnavailable("one or more reservation guards are unavailable")
        return reservations, statuses

    def _search_both(
        self,
        scope: dict[str, Any],
        statuses: dict[str, dict[str, Any]],
    ) -> list[Candidate]:
        candidates: list[Candidate] = []
        for name, provider in (("resy", self.resy), ("opentable", self.opentable)):
            try:
                found, search_status = provider.search(scope)
                if not isinstance(found, list) or search_status not in {"ok", "partial", "unavailable"}:
                    raise ProviderUnavailable("provider search response is malformed")
            except (ProviderUnavailable, RestaurantBookError, OSError, ValueError):
                found, search_status = [], "unavailable"
            statuses[name]["search"] = search_status
            eligible = [candidate for candidate in found if candidate_allowed(scope, candidate)]
            statuses[name]["candidates"] = len(eligible)
            candidates.extend(eligible)
        return candidates

    def _local_record(self, path: Path, *, attempt: bool) -> dict[str, Any] | None:
        try:
            info = path.lstat()
            if not stat.S_ISREG(info.st_mode) or stat.S_ISLNK(info.st_mode) or info.st_uid != os.getuid():
                raise ValueError
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError, json.JSONDecodeError):
            return {"malformed": True}
        if not isinstance(raw, dict):
            return {"malformed": True}
        try:
            record_date = parse_date(raw.get("date"))
            record_time = normalize_time(raw.get("time"))
        except RestaurantBookError:
            return {"malformed": True}
        party_size = raw.get("party_size")
        if type(party_size) is not int or not 1 <= party_size <= 20:
            party_size = 0
        return {
            "platform": str(raw.get("platform", "unknown")),
            "venue_id": str(raw.get("venue_id", "")) or None,
            "restaurant": str(raw.get("restaurant", "local booking state")),
            "date": record_date,
            "time": record_time,
            "party_size": party_size,
            "status": "pending" if attempt else "confirmed",
        }

    def _local_conflict(
        self,
        scope: dict[str, Any],
        *,
        exclude_attempt: Path | None = None,
    ) -> tuple[str, dict[str, Any] | None] | None:
        if not self.shared_state_root.is_dir():
            return None
        policy = scope["idempotency"]
        for path in sorted(self.shared_state_root.glob("*/booking-attempt.json")):
            if exclude_attempt is not None and path.resolve() == exclude_attempt.resolve():
                continue
            record = self._local_record(path, attempt=True)
            if record is None:
                continue
            if record.get("malformed"):
                return "attempt", None
            if policy["date_start"] <= record["date"] <= policy["date_end"]:
                if policy["party_size"] is None or record["party_size"] in {0, policy["party_size"]}:
                    return "attempt", record
        for path in sorted(self.shared_state_root.glob("*/confirmed.json")):
            record = self._local_record(path, attempt=False)
            if record is None:
                continue
            if record.get("malformed"):
                return "receipt", None
            if policy["date_start"] <= record["date"] <= policy["date_end"]:
                if policy["party_size"] is None or record["party_size"] in {0, policy["party_size"]}:
                    return "receipt", record
        return None

    def _prepare_plan(
        self,
        command: str,
        job_id: str,
        scope: dict[str, Any],
        scope_digest: str,
    ) -> tuple[dict[str, Any], Candidate | None]:
        checked_at = timestamp(self.now())
        try:
            reservations, statuses = self._read_both()
        except ProviderUnavailable:
            statuses = {
                name: {"reservations": "unavailable", "search": "not_attempted", "candidates": 0}
                for name in PLATFORMS
            }
            return self._base(
                command,
                job_id,
                scope_digest,
                "blocked",
                checked_at=checked_at,
                reason="reservation_guard_unavailable",
                providers=statuses,
                run_authorized_now=self._authorization_current(scope),
            ), None
        existing = reservation_conflict(scope, reservations)
        if existing is not None:
            return self._base(
                command,
                job_id,
                scope_digest,
                "already_reserved",
                checked_at=checked_at,
                existing_reservation=safe_reservation(existing),
                providers=statuses,
                run_authorized_now=self._authorization_current(scope),
            ), None
        local = self._local_conflict(scope)
        if local is not None:
            kind, record = local
            if kind == "attempt":
                return self._base(
                    command,
                    job_id,
                    scope_digest,
                    "manual_review_required",
                    checked_at=checked_at,
                    reason="unresolved_booking_attempt",
                    providers=statuses,
                    run_authorized_now=self._authorization_current(scope),
                ), None
            return self._base(
                command,
                job_id,
                scope_digest,
                "already_reserved",
                checked_at=checked_at,
                existing_reservation=safe_reservation(record) if record else None,
                source="local_receipt",
                providers=statuses,
                run_authorized_now=self._authorization_current(scope),
            ), None
        candidates = self._search_both(scope, statuses)
        if any(statuses[name]["search"] != "ok" for name in PLATFORMS):
            return self._base(
                command,
                job_id,
                scope_digest,
                "blocked",
                checked_at=checked_at,
                reason="provider_search_incomplete",
                providers=statuses,
                run_authorized_now=self._authorization_current(scope),
            ), None
        candidate = choose_candidate(scope, candidates)
        if candidate is None:
            return self._base(
                command,
                job_id,
                scope_digest,
                "no_availability",
                checked_at=checked_at,
                providers=statuses,
                run_authorized_now=self._authorization_current(scope),
            ), None
        return self._base(
            command,
            job_id,
            scope_digest,
            "ready",
            checked_at=checked_at,
            providers=statuses,
            candidate=candidate.safe(),
            run_authorized_now=self._authorization_current(scope),
        ), candidate

    def plan(self, job_id: str) -> dict[str, Any]:
        scope, scope_digest = self.scope(job_id)
        result, _ = self._prepare_plan("plan", job_id, scope, scope_digest)
        return result

    def _attempt_payload(
        self,
        job_id: str,
        scope_digest: str,
        candidate: Candidate,
        phase: str,
    ) -> dict[str, Any]:
        return {
            "schema_version": SCHEMA_VERSION,
            "job_id": job_id,
            "scope_digest": scope_digest,
            "candidate_digest": candidate.digest(),
            "platform": candidate.platform,
            "venue_id": candidate.venue_id,
            "restaurant": candidate.restaurant,
            "date": candidate.date,
            "time": candidate.time,
            "party_size": candidate.party_size,
            "phase": phase,
            "started_at": timestamp(self.now()),
        }

    def _write_receipt(
        self,
        path: Path,
        job_id: str,
        scope_digest: str,
        candidate: Candidate,
        confirmation_id: str,
    ) -> None:
        atomic_json(
            path,
            {
                "schema_version": SCHEMA_VERSION,
                "job_id": job_id,
                "scope_digest": scope_digest,
                "source": "booking",
                "confirmed_at": timestamp(self.now()),
                "platform": candidate.platform,
                "venue_id": candidate.venue_id,
                "restaurant": candidate.restaurant,
                "date": candidate.date,
                "time": candidate.time,
                "party_size": candidate.party_size,
                "confirmation_id": confirmation_id,
            },
        )

    def _fresh_guard(self, scope: dict[str, Any], *, chosen_last: str) -> None:
        reservations, _ = self._read_both(chosen_last=chosen_last, final=True)
        if reservation_conflict(scope, reservations) is not None:
            raise ExistingReservation("a reservation now exists inside the authorized scope")

    def _resy_confirmation(self, candidate: Candidate) -> bool:
        try:
            reservations = self.resy.reservations(final=True)
        except BaseException:
            return False
        for reservation in reservations:
            if reservation["status"] not in CONFIRMED_STATUSES:
                continue
            if reservation["platform"] != "resy":
                continue
            if reservation["venue_id"] != candidate.venue_id:
                continue
            if (
                reservation["date"] == candidate.date
                and reservation["time"] == candidate.time
                and reservation["party_size"] == candidate.party_size
            ):
                return True
        return False

    def _run_resy(
        self,
        *,
        job_id: str,
        scope: dict[str, Any],
        scope_digest: str,
        candidate: Candidate,
        attempt_path: Path,
        receipt_path: Path,
    ) -> dict[str, Any]:
        try:
            prepared = self.resy.prepare(candidate)
        except (ProviderUnavailable, RestaurantBookError, OSError, ValueError):
            return self._base("run", job_id, scope_digest, "guard_unavailable", reason="provider_preparation_failed")
        except BaseException as exc:
            if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                raise
            return self._base(
                "run", job_id, scope_digest, "guard_unavailable", reason="provider_preparation_failed"
            )
        atomic_json(attempt_path, self._attempt_payload(job_id, scope_digest, candidate, "guard_pending"))
        mutation_started = False

        def live_guard() -> None:
            self._require_authorization_current(scope)
            self._fresh_guard(scope, chosen_last="resy")

        def mutation_boundary() -> None:
            nonlocal mutation_started
            self._require_authorization_current(scope)
            atomic_json(
                attempt_path,
                self._attempt_payload(job_id, scope_digest, candidate, "mutation_started"),
            )
            mutation_started = True

        try:
            self.resy.book(prepared, live_guard, mutation_boundary)
        except ExistingReservation:
            if not mutation_started:
                durable_unlink(attempt_path)
                return self._base("run", job_id, scope_digest, "already_reserved", source="final_live_guard")
            return self._base(
                "run",
                job_id,
                scope_digest,
                "unknown",
                reason="reservation_changed_after_mutation_boundary",
                mutation_attempted=True,
                reservation_may_exist=True,
            )
        except AuthorizationExpired:
            if not mutation_started:
                durable_unlink(attempt_path)
                return self._base("run", job_id, scope_digest, "blocked", reason="authorization_expired")
            return self._base(
                "run",
                job_id,
                scope_digest,
                "unknown",
                reason="authorization_expired_after_mutation_boundary",
                mutation_attempted=True,
                reservation_may_exist=True,
            )
        except (ProviderUnavailable, RestaurantBookError, OSError, ValueError, SystemExit):
            if not mutation_started:
                durable_unlink(attempt_path)
                return self._base("run", job_id, scope_digest, "guard_unavailable", reason="final_guard_failed")
            return self._base(
                "run",
                job_id,
                scope_digest,
                "unknown",
                reason="booking_outcome_unknown",
                mutation_attempted=True,
                reservation_may_exist=True,
            )
        except BaseException as exc:
            if not mutation_started:
                durable_unlink(attempt_path)
                if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                    raise
                return self._base(
                    "run", job_id, scope_digest, "guard_unavailable", reason="final_guard_failed"
                )
            return self._base(
                "run",
                job_id,
                scope_digest,
                "unknown",
                reason="booking_outcome_unknown",
                mutation_attempted=True,
                reservation_may_exist=True,
            )
        if not mutation_started or not self._resy_confirmation(candidate):
            return self._base(
                "run",
                job_id,
                scope_digest,
                "manual_review_required",
                reason="exact_reservation_readback_failed",
                mutation_attempted=mutation_started,
                reservation_may_exist=mutation_started,
            )
        try:
            self._write_receipt(receipt_path, job_id, scope_digest, candidate, "resy-readback")
            durable_unlink(attempt_path)
        except BaseException:
            return self._base(
                "run",
                job_id,
                scope_digest,
                "manual_review_required",
                reason="receipt_not_durable",
                mutation_attempted=True,
                reservation_may_exist=True,
            )
        return self._base(
            "run",
            job_id,
            scope_digest,
            "confirmed",
            mutation_attempted=True,
            reservation_may_exist=False,
            booking=candidate.safe(),
        )

    @staticmethod
    def _opentable_confirmation_matches(payload: dict[str, Any], candidate: Candidate) -> tuple[bool, str]:
        confirmation_id = strict_confirmation_id(payload)
        matches = confirmation_id is not None and opentable_confirmation_correlates(
            payload, candidate
        )
        return matches, confirmation_id or ""

    def _opentable_readback_matches(self, candidate: Candidate) -> bool:
        try:
            reservations = self.opentable.reservations(final=True)
        except BaseException:
            return False
        for reservation in reservations:
            if reservation.get("platform") != "opentable":
                continue
            if reservation.get("status") not in CONFIRMED_STATUSES:
                continue
            if normalized_identity_text(reservation.get("restaurant")) != normalized_identity_text(
                candidate.restaurant
            ):
                continue
            if (
                reservation.get("date") == candidate.date
                and reservation.get("time") == candidate.time
                and reservation.get("party_size") == candidate.party_size
            ):
                return True
        return False

    def _run_opentable(
        self,
        *,
        job_id: str,
        scope: dict[str, Any],
        scope_digest: str,
        candidate: Candidate,
        attempt_path: Path,
        receipt_path: Path,
    ) -> dict[str, Any]:
        try:
            candidate = self.opentable.refresh(scope, candidate)
        except (ProviderUnavailable, RestaurantBookError, OSError, ValueError):
            return self._base(
                "run",
                job_id,
                scope_digest,
                "guard_unavailable",
                reason="opentable_exact_availability_changed",
            )
        except BaseException as exc:
            if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                raise
            return self._base(
                "run",
                job_id,
                scope_digest,
                "guard_unavailable",
                reason="opentable_exact_availability_changed",
            )
        atomic_json(attempt_path, self._attempt_payload(job_id, scope_digest, candidate, "guard_pending"))
        mutation_started = False

        def live_guard() -> None:
            self._require_authorization_current(scope)
            self._fresh_guard(scope, chosen_last="opentable")

        def mutation_boundary() -> None:
            nonlocal mutation_started
            self._require_authorization_current(scope)
            atomic_json(
                attempt_path,
                self._attempt_payload(job_id, scope_digest, candidate, "mutation_started"),
            )
            mutation_started = True

        try:
            payload = self.opentable.book(candidate, live_guard, mutation_boundary)
        except ExistingReservation:
            if not mutation_started:
                durable_unlink(attempt_path)
                return self._base("run", job_id, scope_digest, "already_reserved", source="final_live_guard")
            return self._base(
                "run",
                job_id,
                scope_digest,
                "unknown",
                reason="reservation_changed_after_mutation_boundary",
                mutation_attempted=True,
                reservation_may_exist=True,
            )
        except AuthorizationExpired:
            if not mutation_started:
                durable_unlink(attempt_path)
                return self._base("run", job_id, scope_digest, "blocked", reason="authorization_expired")
            return self._base(
                "run",
                job_id,
                scope_digest,
                "unknown",
                reason="authorization_expired_after_mutation_boundary",
                mutation_attempted=True,
                reservation_may_exist=True,
            )
        except (ProviderUnavailable, RestaurantBookError, OSError, ValueError, SystemExit):
            if not mutation_started:
                durable_unlink(attempt_path)
                return self._base("run", job_id, scope_digest, "guard_unavailable", reason="final_guard_failed")
            return self._base(
                "run",
                job_id,
                scope_digest,
                "unknown",
                reason="opentable_booking_outcome_unknown",
                mutation_attempted=True,
                reservation_may_exist=True,
            )
        except BaseException as exc:
            if not mutation_started:
                durable_unlink(attempt_path)
                if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                    raise
                return self._base(
                    "run", job_id, scope_digest, "guard_unavailable", reason="final_guard_failed"
                )
            return self._base(
                "run",
                job_id,
                scope_digest,
                "unknown",
                reason="opentable_booking_outcome_unknown",
                mutation_attempted=True,
                reservation_may_exist=True,
            )
        matches, confirmation_id = self._opentable_confirmation_matches(payload, candidate)
        if not mutation_started or not matches:
            return self._base(
                "run",
                job_id,
                scope_digest,
                "unknown",
                reason="opentable_confirmation_unknown",
                mutation_attempted=True,
                reservation_may_exist=True,
            )
        if not self._opentable_readback_matches(candidate):
            return self._base(
                "run",
                job_id,
                scope_digest,
                "manual_review_required",
                reason="opentable_exact_reservation_readback_failed",
                mutation_attempted=True,
                reservation_may_exist=True,
            )
        try:
            self._write_receipt(receipt_path, job_id, scope_digest, candidate, confirmation_id)
            durable_unlink(attempt_path)
        except BaseException:
            return self._base(
                "run",
                job_id,
                scope_digest,
                "manual_review_required",
                reason="receipt_not_durable",
                mutation_attempted=True,
                reservation_may_exist=True,
            )
        return self._base(
            "run",
            job_id,
            scope_digest,
            "confirmed",
            mutation_attempted=True,
            reservation_may_exist=False,
            booking=candidate.safe(),
        )

    def run(self, job_id: str) -> dict[str, Any]:
        scope, scope_digest = self.scope(job_id)
        if not self._authorization_current(scope):
            return self._base("run", job_id, scope_digest, "blocked", reason="authorization_not_active")
        state_dir = self.state_dir(job_id)
        secure_directory(self.shared_state_root)
        secure_directory(state_dir)
        attempt_path = state_dir / "booking-attempt.json"
        receipt_path = state_dir / "confirmed.json"
        context_path = state_dir / "run.json"
        with nonblocking_lock(state_dir / "runner.lock") as runner_lock:
            if runner_lock < 0:
                return self._base("run", job_id, scope_digest, "busy", reason="job_in_progress")
            if receipt_path.exists():
                return self._base("run", job_id, scope_digest, "already_reserved", source="local_receipt")
            if attempt_path.exists():
                return self._base(
                    "run",
                    job_id,
                    scope_digest,
                    "manual_review_required",
                    reason="prior_booking_attempt_unresolved",
                )
            result, candidate = self._prepare_plan("run", job_id, scope, scope_digest)
            if candidate is None:
                return result
            try:
                atomic_json(
                    context_path,
                    {
                        "schema_version": SCHEMA_VERSION,
                        "job_id": job_id,
                        "scope_digest": scope_digest,
                        "candidate": candidate.safe(),
                        "phase": "selected",
                        "created_at": timestamp(self.now()),
                    },
                )
            except (OSError, RestaurantBookError):
                return self._base(
                    "run",
                    job_id,
                    scope_digest,
                    "manual_review_required",
                    reason="exact_run_state_not_durable",
                )
            with nonblocking_lock(self.mutation_lock_path) as mutation_lock:
                if mutation_lock < 0:
                    return self._base("run", job_id, scope_digest, "busy", reason="booking_mutation_in_progress")
                local = self._local_conflict(scope, exclude_attempt=attempt_path)
                if local is not None:
                    kind, record = local
                    if kind == "attempt":
                        return self._base(
                            "run",
                            job_id,
                            scope_digest,
                            "manual_review_required",
                            reason="other_booking_attempt_unresolved",
                        )
                    return self._base(
                        "run",
                        job_id,
                        scope_digest,
                        "already_reserved",
                        existing_reservation=safe_reservation(record) if record else None,
                        source="local_receipt",
                    )
                try:
                    reservations, _ = self._read_both(chosen_last=candidate.platform, final=True)
                except ProviderUnavailable:
                    return self._base(
                        "run",
                        job_id,
                        scope_digest,
                        "guard_unavailable",
                        reason="final_reservation_guard_unavailable",
                    )
                existing = reservation_conflict(scope, reservations)
                if existing is not None:
                    return self._base(
                        "run",
                        job_id,
                        scope_digest,
                        "already_reserved",
                        existing_reservation=safe_reservation(existing),
                        source="final_live_guard",
                    )
                self._require_authorization_current(scope)
                if candidate.platform == "resy":
                    return self._run_resy(
                        job_id=job_id,
                        scope=scope,
                        scope_digest=scope_digest,
                        candidate=candidate,
                        attempt_path=attempt_path,
                        receipt_path=receipt_path,
                    )
                return self._run_opentable(
                    job_id=job_id,
                    scope=scope,
                    scope_digest=scope_digest,
                    candidate=candidate,
                    attempt_path=attempt_path,
                    receipt_path=receipt_path,
                )

def make_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    for command in ("plan", "run"):
        subparser = subparsers.add_parser(command)
        subparser.add_argument("--job-id", required=True)
    return parser


def emit(payload: dict[str, Any]) -> None:
    print(json.dumps(payload, sort_keys=True, separators=(",", ":")))


def main(argv: list[str] | None = None) -> int:
    raw_arguments = list(sys.argv[1:] if argv is None else argv)
    args = make_parser().parse_args(raw_arguments)
    coordinator = Coordinator()
    try:
        if args.command == "plan":
            result = coordinator.plan(args.job_id)
        elif args.command == "run":
            result = coordinator.run(args.job_id)
        else:
            raise RestaurantBookError("unsupported coordinator command")
    except ExistingReservation:
        emit({"schema_version": SCHEMA_VERSION, "status": "blocked_existing_reservation"})
        return 3
    except (RestaurantBookError, ProviderUnavailable, AuthorizationExpired, OSError, ValueError):
        emit({"schema_version": SCHEMA_VERSION, "status": "error", "error_code": "contract_invalid"})
        return 2
    emit(result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
