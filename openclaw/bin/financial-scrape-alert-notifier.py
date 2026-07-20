#!/usr/bin/env python3
"""Delivery-only notifier for durable weekly financial scrape alerts.

This program has no financial-repository, scraper, browser, or importer entry
point. It reads only bounded operational alert records and invokes the fixed
native iMessage binary after resolving Dylan's validated chat ID.
"""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import fcntl
import json
import os
from pathlib import Path
import re
import selectors
import signal
import stat
import subprocess
import sys
import tempfile
import time
import uuid


CONTRACT_VERSION = 2
HEALTH_CONTRACT_VERSION = 1
HOME = Path.home()
STATE_DIR = HOME / ".openclaw" / "financial-dashboard"
OUTBOX_DIR = STATE_DIR / "weekly-scrape-alerts"
QUARANTINE_DIR = STATE_DIR / "weekly-scrape-alerts-quarantine"
HEALTH_PATH = STATE_DIR / "weekly-scrape-alert-notifier-status.json"
LOCK_PATH = STATE_DIR / ".weekly-scrape-alert-notifier.lock"
SECRETS_CACHE = HOME / ".openclaw" / ".secrets-cache"
IMSG_BIN = Path("/opt/homebrew/bin/imsg")
CHAT_ID_ENV = "OPENCLAW_FINANCE_ALERT_CHAT_ID"
MAX_ALERT_BYTES = 16 * 1024
MAX_SECRET_BYTES = 256 * 1024
MAX_COMMAND_BYTES = 16 * 1024
MAX_HEALTH_BYTES = 4 * 1024
MAX_SCAN_CURSOR_BYTES = 2 * 1024
MAX_SCAN_ENTRIES = 512
MAX_SENDS_PER_RUN = 16
MESSAGE_MAX_BYTES = 1_500
COMMAND_READ_BYTES = 4 * 1024
SEND_TIMEOUT_SECONDS = 20
IMSG_RPC_REQUEST_ID = "financial-scrape-alert-send"
PROCESS_GROUP_GRACE_SECONDS = 2
PROCESS_GROUP_POLL_SECONDS = 0.05
SCAN_CURSOR_CONTRACT_VERSION = 1
SCAN_CURSOR_NAME = ".weekly-scrape-alert-notifier-cursor.json"
INITIAL_BACKOFF_SECONDS = 15 * 60
MAX_BACKOFF_SECONDS = 6 * 60 * 60
CANARY_TEXT = (
    "Financial scrape alert delivery canary succeeded. "
    "No financial scrape or import was run."
)
CHAT_ID_RE = re.compile(r"^[1-9][0-9]{0,17}$")
CHAT_ID_ASSIGNMENT_RE = re.compile(rb"^DYLAN_CHAT_ID=([1-9][0-9]{0,17})$")
RUN_ID_PATTERN = (
    r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-"
    r"[0-9a-f]{4}-[0-9a-f]{12}"
)
FILE_RE = re.compile(rf"^{RUN_ID_PATTERN}\.json$")
PRODUCER_TEMP_RE = re.compile(rf"^\.alert\.({RUN_ID_PATTERN})\.tmp$")
SOURCES = frozenset({
    "tesla_solar",
    "eversource",
    "national_grid_electric",
    "national_grid_gas",
    "bwsc",
    "pennymac",
    "boa",
})
STATE_FIELD_ORDER = (
    "profile_preflight",
    "scrape",
    "verify_auth",
    "tab_bootstrap",
    "reauth",
    "import",
    "path",
)
STATE_FIELDS = frozenset(STATE_FIELD_ORDER)
ALERT_STATUSES = frozenset({
    "preflight_failed",
    "failed",
    "degraded",
    "interrupted",
    "internal_error",
    "lock_unavailable",
    "status_write_failed",
})
RUN_STATUSES = ALERT_STATUSES | {"ok"}
REASONS = frozenset({
    "repository_unavailable",
    "scraper_contract_mismatch",
    "provider_mode_config_invalid",
    "credential_cache_unavailable",
    "tesla_configuration_unavailable",
    "unspecified",
})
SIGNALS = frozenset({"sigint", "sigterm", "termination"})
DELIVERY_STATES = frozenset({"pending", "inflight", "sent"})
DELIVERY_ERRORS = frozenset({
    "target_unavailable",
    "imsg_unavailable",
    "send_timeout",
    "send_failed",
    "receipt_invalid",
    "state_update_failed",
})
STATE_VALUES = {
    "profile_preflight": frozenset({"ok", "not_needed", "timeout", "failed"}),
    "scrape": frozenset({"ok", "failed", "timeout"}),
    "verify_auth": frozenset({
        "not_needed",
        "verify_failed",
        "authenticated",
        "auth_unknown",
        "boa_tab_unavailable",
        "cdp_attach_failed",
        "cdp_unavailable",
        "not_authenticated",
        "signed_out_landing",
    }),
    "tab_bootstrap": frozenset({
        "not_needed",
        "profile_unavailable",
        "tab_list_timeout",
        "tab_list_failed",
        "reused",
        "open_timeout",
        "open_failed",
        "opened",
    }),
    "reauth": frozenset({
        "not_needed",
        "credentials_unavailable",
        "ok",
        "failed",
        "timeout",
        "reauth_failed",
        "already_authenticated",
        "authenticated",
        "auth_unknown",
        "boa_tab_unavailable",
        "cdp_attach_failed",
        "cdp_unavailable",
        "credentials_missing",
        "error",
        "host_not_allowed",
        "login_form_unavailable",
        "login_rejected",
        "login_timeout",
        "mfa_or_challenge",
        "password_not_ready",
        "submit_not_ready",
        "user_id_not_ready",
    }),
    "import": frozenset({"ok", "failed", "timeout", "skipped"}),
    "path": frozenset({
        "direct_api",
        "direct_http",
        "browser_recovery",
        "browser_only",
        "browser_explicit",
        "mode_mismatch",
        "contract_invalid",
        "not_observed",
    }),
}


class NotifierError(Exception):
    """Base class for safe notifier failures."""


class QueueError(NotifierError):
    """Protected queue state is absent or unsafe."""


class TargetError(NotifierError):
    """Dylan's exact iMessage chat ID could not be validated."""


class DeliveryError(NotifierError):
    """A delivery attempt did not return a strict success receipt."""

    def __init__(self, code):
        super().__init__(code)
        self.code = code if code in DELIVERY_ERRORS else "send_failed"


@dataclass(frozen=True)
class FileIdentity:
    device: int
    inode: int
    size: int
    modified_ns: int


@dataclass(frozen=True)
class AlertRecord:
    path: Path
    payload: dict
    identity: FileIdentity


@dataclass(frozen=True)
class CommandCapture:
    returncode: int
    stdout: bytes
    stderr: bytes
    timed_out: bool = False
    output_rejected: bool = False


def _timestamp(value):
    if not isinstance(value, str) or len(value) != 25 or not value.endswith("+00:00"):
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    if (
        parsed.tzinfo is None
        or parsed.utcoffset() is None
        or parsed.utcoffset().total_seconds() != 0
        or parsed.isoformat(timespec="seconds") != value
    ):
        return None
    return parsed


def _timestamp_text(value):
    return value.astimezone(timezone.utc).isoformat(timespec="seconds")


def _run_id(value):
    if not isinstance(value, str) or len(value) != 36:
        return False
    try:
        return str(uuid.UUID(value)) == value
    except (ValueError, AttributeError):
        return False


def _strict_json_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate key")
        result[key] = value
    return result


def _strict_json_loads(encoded):
    return json.loads(
        encoded,
        object_pairs_hook=_strict_json_object,
        parse_constant=lambda _value: (_ for _ in ()).throw(
            ValueError("nonfinite")
        ),
    )


def validate_alert(payload, expected_run_id=None):
    expected_keys = {
        "contract",
        "run_id",
        "status",
        "run_status",
        "created_at",
        "reason",
        "missing_profiles",
        "signal",
        "affected",
        "attempts",
        "next_attempt_at",
        "last_attempt_at",
        "last_error",
        "delivery_state",
        "sent_at",
    }
    if not isinstance(payload, dict) or set(payload) != expected_keys:
        return False
    run_id = payload.get("run_id")
    delivery_state = payload.get("delivery_state")
    sent_at = payload.get("sent_at")
    if (
        type(payload.get("contract")) is not int
        or payload["contract"] != CONTRACT_VERSION
        or not _run_id(run_id)
        or (expected_run_id is not None and run_id != expected_run_id)
        or payload.get("status") not in ALERT_STATUSES
        or payload.get("run_status") not in (RUN_STATUSES | {None})
        or _timestamp(payload.get("created_at")) is None
        or payload.get("reason") not in (REASONS | {None})
        or payload.get("signal") not in (SIGNALS | {None})
        or delivery_state not in DELIVERY_STATES
    ):
        return False
    if delivery_state == "sent":
        if _timestamp(sent_at) is None:
            return False
    elif sent_at is not None:
        return False
    if payload["status"] == "status_write_failed":
        if payload["run_status"] is None:
            return False
    elif payload["run_status"] is not None:
        return False
    profiles = payload.get("missing_profiles")
    allowed_profiles = {"eversource", "national_grid", "bwsc", "pennymac", "boa"}
    if (
        not isinstance(profiles, list)
        or len(profiles) > len(allowed_profiles)
        or profiles != sorted(set(profiles))
        or any(profile not in allowed_profiles for profile in profiles)
    ):
        return False
    affected = payload.get("affected")
    if not isinstance(affected, list) or len(affected) > len(SOURCES):
        return False
    seen = set()
    for entry in affected:
        if not isinstance(entry, dict) or set(entry) != {"source", "states"}:
            return False
        source = entry.get("source")
        states = entry.get("states")
        if (
            source not in SOURCES
            or source in seen
            or not isinstance(states, dict)
            or not set(states).issubset(STATE_FIELDS)
            or any(
                not isinstance(value, str)
                or value not in STATE_VALUES[field]
                for field, value in states.items()
            )
        ):
            return False
        seen.add(source)
    attempts = payload.get("attempts")
    if type(attempts) is not int or not 0 <= attempts <= 100_000:
        return False
    if _timestamp(payload.get("next_attempt_at")) is None:
        return False
    last_attempt = payload.get("last_attempt_at")
    if last_attempt is not None and _timestamp(last_attempt) is None:
        return False
    if delivery_state == "inflight" and last_attempt is None:
        return False
    last_error = payload.get("last_error")
    if last_error is not None and last_error not in DELIVERY_ERRORS:
        return False
    return True


def _private_directory(path, *, create=False):
    try:
        if create:
            path.mkdir(parents=True, exist_ok=True, mode=0o700)
        metadata = path.lstat()
    except OSError as error:
        raise QueueError from error
    if (
        stat.S_ISLNK(metadata.st_mode)
        or not stat.S_ISDIR(metadata.st_mode)
        or metadata.st_uid != os.getuid()
        or stat.S_IMODE(metadata.st_mode) != 0o700
    ):
        raise QueueError


def prepare_outbox(path=None):
    outbox = Path(path or OUTBOX_DIR)
    _private_directory(outbox.parent, create=True)
    _private_directory(outbox, create=True)
    return outbox


def _private_regular_file(path, max_bytes):
    try:
        metadata = path.lstat()
    except OSError as error:
        raise QueueError from error
    if (
        stat.S_ISLNK(metadata.st_mode)
        or not stat.S_ISREG(metadata.st_mode)
        or metadata.st_uid != os.getuid()
        or stat.S_IMODE(metadata.st_mode) != 0o600
        or metadata.st_nlink != 1
        or not 0 < metadata.st_size <= max_bytes
    ):
        raise QueueError
    return metadata


def _identity(metadata):
    return FileIdentity(
        metadata.st_dev,
        metadata.st_ino,
        metadata.st_size,
        metadata.st_mtime_ns,
    )


def _safe_producer_temp(path, *, missing_is_transient=False):
    """Recognize only the producer's bounded private commit temporary."""
    match = PRODUCER_TEMP_RE.fullmatch(path.name)
    if match is None or not _run_id(match.group(1)):
        return False
    try:
        metadata = path.lstat()
    except FileNotFoundError:
        return missing_is_transient
    except OSError:
        return False
    return (
        stat.S_ISREG(metadata.st_mode)
        and metadata.st_uid == os.getuid()
        and stat.S_IMODE(metadata.st_mode) == 0o600
        and metadata.st_nlink in {1, 2}
        and 0 <= metadata.st_size <= MAX_ALERT_BYTES
    )


def _producer_commit_in_progress(path):
    """Defer an exact producer temporary or its briefly hard-linked final."""
    if PRODUCER_TEMP_RE.fullmatch(path.name):
        return _safe_producer_temp(path, missing_is_transient=True)

    if FILE_RE.fullmatch(path.name) is None:
        return False
    run_id = path.name[:-5]
    try:
        final_metadata = path.lstat()
    except OSError:
        return False
    if (
        not stat.S_ISREG(final_metadata.st_mode)
        or final_metadata.st_uid != os.getuid()
        or stat.S_IMODE(final_metadata.st_mode) != 0o600
        or final_metadata.st_nlink != 2
        or not 0 < final_metadata.st_size <= MAX_ALERT_BYTES
    ):
        return False

    temporary = path.parent / f".alert.{run_id}.tmp"
    if not _safe_producer_temp(temporary):
        return False
    try:
        temporary_metadata = temporary.lstat()
    except OSError:
        return False
    return (
        temporary_metadata.st_nlink == 2
        and (temporary_metadata.st_dev, temporary_metadata.st_ino)
        == (final_metadata.st_dev, final_metadata.st_ino)
    )


def _require_identity(path, expected, max_bytes=MAX_ALERT_BYTES):
    metadata = _private_regular_file(path, max_bytes)
    if _identity(metadata) != expected:
        raise QueueError
    return metadata


def _read_verified_file(path, max_bytes):
    metadata = _private_regular_file(path, max_bytes)
    flags = (
        os.O_RDONLY
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOFOLLOW", 0)
        | getattr(os, "O_NONBLOCK", 0)
    )
    descriptor = None
    try:
        descriptor = os.open(path, flags)
        before = os.fstat(descriptor)
        chunks = []
        total = 0
        while True:
            chunk = os.read(descriptor, min(16 * 1024, max_bytes + 1 - total))
            if not chunk:
                break
            chunks.append(chunk)
            total += len(chunk)
            if total > max_bytes:
                raise QueueError
        encoded = b"".join(chunks)
        after = os.fstat(descriptor)
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_uid != os.getuid()
            or stat.S_IMODE(before.st_mode) != 0o600
            or before.st_nlink != 1
            or _identity(before) != _identity(metadata)
            or _identity(before) != _identity(after)
            or before.st_size != len(encoded)
        ):
            raise QueueError
    except OSError as error:
        raise QueueError from error
    finally:
        if descriptor is not None:
            os.close(descriptor)
    return encoded, _identity(before)


def read_alert(path):
    expected_name = path.name[:-5] if FILE_RE.fullmatch(path.name) else None
    if expected_name is None or not _run_id(expected_name):
        raise QueueError
    encoded, identity = _read_verified_file(path, MAX_ALERT_BYTES)
    try:
        payload = _strict_json_loads(encoded.decode("utf-8"))
    except (UnicodeError, ValueError, TypeError) as error:
        raise QueueError from error
    if not validate_alert(payload, expected_run_id=expected_name):
        raise QueueError
    return AlertRecord(path, payload, identity)


def _fsync_directory(path):
    try:
        descriptor = os.open(path, os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
    except OSError as error:
        raise QueueError from error


def write_alert(record, payload):
    path = record.path
    if not validate_alert(payload, expected_run_id=path.stem):
        raise QueueError
    encoded = (
        json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n"
    ).encode("utf-8")
    if len(encoded) > MAX_ALERT_BYTES:
        raise QueueError
    temporary = None
    temporary_identity = None
    try:
        descriptor, temporary = tempfile.mkstemp(
            prefix=".delivery.", suffix=".tmp", dir=path.parent
        )
        with os.fdopen(descriptor, "wb") as handle:
            os.fchmod(handle.fileno(), 0o600)
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
            temporary_identity = _identity(os.fstat(handle.fileno()))
        _require_identity(path, record.identity)
        os.replace(temporary, path)
        temporary = None
        metadata = _private_regular_file(path, MAX_ALERT_BYTES)
        if _identity(metadata) != temporary_identity:
            raise QueueError
        _fsync_directory(path.parent)
    except QueueError:
        raise
    except OSError as error:
        raise QueueError from error
    finally:
        if temporary is not None:
            try:
                os.unlink(temporary)
            except OSError:
                pass
    return AlertRecord(path, payload, _identity(metadata))


def _validate_secret_cache_parent(path):
    try:
        parent = path.parent.lstat()
    except OSError as error:
        raise TargetError from error
    if (
        stat.S_ISLNK(parent.st_mode)
        or not stat.S_ISDIR(parent.st_mode)
        or parent.st_uid != os.getuid()
        or stat.S_IMODE(parent.st_mode) != 0o700
    ):
        raise TargetError


def resolve_chat_id(environment=None, secrets_cache=None):
    environment = os.environ if environment is None else environment
    scoped = environment.get(CHAT_ID_ENV, "")
    if scoped:
        if not isinstance(scoped, str) or CHAT_ID_RE.fullmatch(scoped) is None:
            raise TargetError
        return scoped

    cache = Path(secrets_cache or SECRETS_CACHE)
    _validate_secret_cache_parent(cache)
    try:
        encoded, _ = _read_verified_file(cache, MAX_SECRET_BYTES)
    except QueueError as error:
        raise TargetError from error
    matches = []
    for line in encoded.splitlines():
        match = CHAT_ID_ASSIGNMENT_RE.fullmatch(line)
        if match is not None:
            matches.append(match.group(1))
    if len(matches) != 1:
        raise TargetError
    try:
        chat_id = matches[0].decode("ascii")
    except UnicodeDecodeError as error:
        raise TargetError from error
    if CHAT_ID_RE.fullmatch(chat_id) is None:
        raise TargetError
    return chat_id


def format_message(alert):
    """Render every validated alert with a deterministic bounded fallback."""
    if not validate_alert(alert):
        raise QueueError
    status = alert["status"].replace("_", " ")
    prefix = f"Weekly financial scrape {status}."
    if alert["status"] == "status_write_failed" and alert["run_status"]:
        run_status = alert["run_status"].replace("_", " ")
        prefix += f" The run outcome was {run_status}."
    if alert["reason"]:
        prefix += f" Reason: {alert['reason'].replace('_', ' ')}."
    if alert["missing_profiles"]:
        names = ", ".join(name.replace("_", " ") for name in alert["missing_profiles"])
        prefix += f" Missing credential profiles: {names}."
    suffix = " Prior data was preserved; review the protected weekly status."
    if not alert["affected"]:
        return prefix + suffix

    detailed = []
    for entry in alert["affected"]:
        states = ", ".join(
            f"{key}={entry['states'][key]}"
            for key in STATE_FIELD_ORDER
            if key in entry["states"]
        )
        source = entry["source"].replace("_", " ")
        detailed.append(f"{source} ({states})" if states else source)
    message = prefix + " Affected: " + "; ".join(detailed) + "." + suffix
    if len(message.encode("utf-8")) <= MESSAGE_MAX_BYTES:
        return message

    sources = ", ".join(
        entry["source"].replace("_", " ") for entry in alert["affected"]
    )
    message = prefix + f" Affected sources: {sources}." + suffix
    if len(message.encode("utf-8")) > MESSAGE_MAX_BYTES:
        raise QueueError
    return message


def _signal_process_group(process, signum):
    if process is None:
        return
    try:
        os.killpg(process.pid, signum)
    except (ProcessLookupError, PermissionError):
        pass


def _close_process_pipes(process):
    if process is None:
        return
    for stream in (process.stdin, process.stdout, process.stderr):
        if stream is not None:
            try:
                stream.close()
            except (OSError, ValueError):
                pass


def _process_group_exists(process):
    """Return whether any member of the child's private session remains."""
    try:
        os.killpg(process.pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _wait_for_process_group_exit(process, timeout):
    """Poll and reap the leader while waiting for the entire group to exit."""
    deadline = time.monotonic() + timeout
    while True:
        try:
            process.poll()
        except (OSError, ValueError):
            pass
        if not _process_group_exists(process):
            return True
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return False
        time.sleep(min(PROCESS_GROUP_POLL_SECONDS, remaining))


def _terminate_process_group(process):
    """Terminate and reap a child session without unbounded output reads."""
    if process is None:
        return
    _signal_process_group(process, signal.SIGTERM)
    _close_process_pipes(process)
    if not _wait_for_process_group_exit(process, PROCESS_GROUP_GRACE_SECONDS):
        _signal_process_group(process, signal.SIGKILL)
        _wait_for_process_group_exit(process, PROCESS_GROUP_GRACE_SECONDS)
    try:
        process.wait(timeout=PROCESS_GROUP_GRACE_SECONDS)
    except subprocess.TimeoutExpired:
        _signal_process_group(process, signal.SIGKILL)
        try:
            process.wait(timeout=PROCESS_GROUP_GRACE_SECONDS)
        except (OSError, ValueError, subprocess.TimeoutExpired):
            pass
    except (OSError, ValueError):
        pass


def run_bounded_command(
    arguments,
    environment,
    timeout_seconds,
    *,
    stdin_data=None,
):
    if stdin_data is not None and (
        not isinstance(stdin_data, bytes)
        or not stdin_data
        or len(stdin_data) > MAX_COMMAND_BYTES
    ):
        raise ValueError("bounded command input is invalid")
    process = None
    selector = selectors.DefaultSelector()
    stdout = bytearray()
    stderr = bytearray()
    stdin_offset = 0
    timed_out = False
    output_rejected = False
    try:
        process = subprocess.Popen(
            arguments,
            env=environment,
            stdin=subprocess.PIPE if stdin_data is not None else subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            start_new_session=True,
        )
        deadline = time.monotonic() + timeout_seconds
        selector.register(process.stdout, selectors.EVENT_READ, ("read", stdout))
        selector.register(process.stderr, selectors.EVENT_READ, ("read", stderr))
        if stdin_data is not None:
            os.set_blocking(process.stdin.fileno(), False)
            selector.register(process.stdin, selectors.EVENT_WRITE, ("write", None))
        while selector.get_map():
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                timed_out = True
                break
            events = selector.select(min(remaining, 0.25))
            if not events:
                if process.poll() is not None:
                    # A descendant retaining a pipe must not keep us alive.
                    _terminate_process_group(process)
                    break
                continue
            for key, _mask in events:
                operation, buffer = key.data
                if operation == "write":
                    try:
                        written = os.write(
                            key.fileobj.fileno(), stdin_data[stdin_offset:]
                        )
                    except BlockingIOError:
                        continue
                    except BrokenPipeError:
                        selector.unregister(key.fileobj)
                        key.fileobj.close()
                        continue
                    stdin_offset += written
                    if stdin_offset == len(stdin_data):
                        selector.unregister(key.fileobj)
                        key.fileobj.close()
                    continue
                try:
                    chunk = os.read(key.fileobj.fileno(), COMMAND_READ_BYTES)
                except OSError:
                    chunk = b""
                if not chunk:
                    selector.unregister(key.fileobj)
                    continue
                if len(buffer) + len(chunk) > MAX_COMMAND_BYTES:
                    output_rejected = True
                    break
                buffer.extend(chunk)
            if output_rejected:
                break
        if timed_out or output_rejected:
            _terminate_process_group(process)
        else:
            try:
                process.wait(timeout=PROCESS_GROUP_GRACE_SECONDS)
            except subprocess.TimeoutExpired:
                timed_out = True
                _terminate_process_group(process)
        return CommandCapture(
            process.returncode if process.returncode is not None else -signal.SIGKILL,
            bytes(stdout),
            bytes(stderr),
            timed_out=timed_out,
            output_rejected=output_rejected,
        )
    finally:
        selector.close()
        if process is not None:
            if _process_group_exists(process):
                _terminate_process_group(process)
            _close_process_pipes(process)


def send_imessage(chat_id, message):
    if (
        not IMSG_BIN.is_absolute()
        or not isinstance(chat_id, str)
        or CHAT_ID_RE.fullmatch(chat_id) is None
        or not isinstance(message, str)
        or not message
        or len(message.encode("utf-8")) > MESSAGE_MAX_BYTES
        or any(ord(character) < 0x20 for character in message)
    ):
        raise DeliveryError("imsg_unavailable")
    child_env = {
        "HOME": str(HOME),
        "PATH": "/opt/homebrew/bin:/usr/bin:/bin",
        "LANG": "en_US.UTF-8",
    }
    request = {
        "jsonrpc": "2.0",
        "id": IMSG_RPC_REQUEST_ID,
        "method": "send",
        "params": {
            "chat_id": int(chat_id),
            "text": message,
            "transport": "bridge",
        },
    }
    encoded_request = (
        json.dumps(request, sort_keys=True, separators=(",", ":")) + "\n"
    ).encode("utf-8")
    try:
        completed = run_bounded_command(
            [str(IMSG_BIN), "rpc"],
            child_env,
            SEND_TIMEOUT_SECONDS,
            stdin_data=encoded_request,
        )
    except OSError as error:
        raise DeliveryError("imsg_unavailable") from error
    if completed.timed_out:
        raise DeliveryError("send_timeout")
    if (
        completed.output_rejected
        or completed.returncode != 0
        or completed.stderr
        or not completed.stdout
    ):
        raise DeliveryError("send_failed")
    try:
        receipt = _strict_json_loads(completed.stdout.decode("utf-8"))
    except (UnicodeError, ValueError) as error:
        raise DeliveryError("receipt_invalid") from error
    if (
        not isinstance(receipt, dict)
        or set(receipt) != {"jsonrpc", "id", "result"}
        or receipt.get("jsonrpc") != "2.0"
        or receipt.get("id") != IMSG_RPC_REQUEST_ID
        or not isinstance(receipt.get("result"), dict)
        or receipt["result"].get("ok") is not True
        or receipt["result"].get("transport") != "bridge"
        or any(not isinstance(key, str) for key in receipt)
        or any(not isinstance(key, str) for key in receipt["result"])
    ):
        raise DeliveryError("receipt_invalid")


def _backoff_seconds(attempts):
    exponent = min(max(attempts - 1, 0), 20)
    return min(INITIAL_BACKOFF_SECONDS * (2 ** exponent), MAX_BACKOFF_SECONDS)


def claim_delivery(record, now):
    updated = dict(record.payload)
    updated["delivery_state"] = "inflight"
    updated["last_attempt_at"] = _timestamp_text(now)
    updated["sent_at"] = None
    return write_alert(record, updated)


def retain_failure(record, code, now):
    updated = dict(record.payload)
    attempts = min(updated["attempts"] + 1, 100_000)
    updated["delivery_state"] = "pending"
    updated["attempts"] = attempts
    updated["last_attempt_at"] = _timestamp_text(now)
    updated["last_error"] = code if code in DELIVERY_ERRORS else "send_failed"
    updated["next_attempt_at"] = _timestamp_text(
        now + timedelta(seconds=_backoff_seconds(attempts))
    )
    updated["sent_at"] = None
    return write_alert(record, updated)


def mark_sent(record, now):
    updated = dict(record.payload)
    updated["delivery_state"] = "sent"
    updated["sent_at"] = _timestamp_text(now)
    updated["last_error"] = None
    return write_alert(record, updated)


def delete_confirmed(record):
    try:
        _require_identity(record.path, record.identity)
        record.path.unlink()
        _fsync_directory(record.path.parent)
    except QueueError:
        raise
    except OSError as error:
        raise QueueError from error


def quarantine_entry(path, quarantine_dir):
    try:
        metadata = path.lstat()
    except OSError as error:
        raise QueueError from error
    expected = _identity(metadata)
    _private_directory(quarantine_dir.parent, create=True)
    _private_directory(quarantine_dir, create=True)
    destination = quarantine_dir / f"{uuid.uuid4()}.bad"
    try:
        current = path.lstat()
        if _identity(current) != expected:
            raise QueueError
        os.replace(path, destination)
        moved = destination.lstat()
        if _identity(moved) != expected:
            raise QueueError
        _fsync_directory(path.parent)
        _fsync_directory(quarantine_dir)
    except QueueError:
        raise
    except OSError as error:
        raise QueueError from error


def _write_private_json(path, payload, max_bytes):
    encoded = (
        json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n"
    ).encode("utf-8")
    if len(encoded) > max_bytes:
        raise QueueError
    _private_directory(path.parent, create=True)
    try:
        existing = path.lstat()
    except FileNotFoundError:
        existing = None
    except OSError as error:
        raise QueueError from error
    if existing is not None and (
        stat.S_ISLNK(existing.st_mode)
        or not stat.S_ISREG(existing.st_mode)
        or existing.st_uid != os.getuid()
        or stat.S_IMODE(existing.st_mode) != 0o600
        or existing.st_nlink != 1
    ):
        raise QueueError
    temporary = None
    try:
        descriptor, temporary = tempfile.mkstemp(
            prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
        )
        with os.fdopen(descriptor, "wb") as handle:
            os.fchmod(handle.fileno(), 0o600)
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        temporary = None
        _fsync_directory(path.parent)
    except QueueError:
        raise
    except OSError as error:
        raise QueueError from error
    finally:
        if temporary is not None:
            try:
                os.unlink(temporary)
            except OSError:
                pass


def write_health(status, started_at, result=None, path=None):
    now = datetime.now(timezone.utc).replace(microsecond=0)
    counts = {}
    for key in (
        "scanned",
        "due",
        "sent",
        "retained",
        "deferred",
        "invalid",
        "quarantined",
        "overflow",
        "sent_cleanup_failed",
    ):
        value = (result or {}).get(key)
        if type(value) is int and value >= 0:
            counts[key] = value
    payload = {
        "contract": HEALTH_CONTRACT_VERSION,
        "status": status,
        "started_at": _timestamp_text(started_at),
        "heartbeat_at": _timestamp_text(now),
        "completed_at": None if status == "running" else _timestamp_text(now),
        "counts": counts,
    }
    _write_private_json(Path(path or HEALTH_PATH), payload, MAX_HEALTH_BYTES)


@contextmanager
def notifier_lock(path=None):
    lock_path = Path(path or LOCK_PATH)
    _private_directory(lock_path.parent, create=True)
    handle = None
    acquired = False
    try:
        flags = (
            os.O_RDWR
            | os.O_CREAT
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NOFOLLOW", 0)
            | getattr(os, "O_NONBLOCK", 0)
        )
        descriptor = os.open(lock_path, flags, 0o600)
        handle = os.fdopen(descriptor, "r+", encoding="utf-8")
        metadata = os.fstat(handle.fileno())
        path_metadata = lock_path.lstat()
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_uid != os.getuid()
            or stat.S_IMODE(metadata.st_mode) != 0o600
            or metadata.st_nlink != 1
            or stat.S_ISLNK(path_metadata.st_mode)
            or not stat.S_ISREG(path_metadata.st_mode)
            or (metadata.st_dev, metadata.st_ino)
            != (path_metadata.st_dev, path_metadata.st_ino)
        ):
            raise QueueError
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            yield False
            return
        acquired = True
        current_metadata = os.fstat(handle.fileno())
        current_path_metadata = lock_path.lstat()
        if (
            not stat.S_ISREG(current_metadata.st_mode)
            or current_metadata.st_uid != os.getuid()
            or stat.S_IMODE(current_metadata.st_mode) != 0o600
            or current_metadata.st_nlink != 1
            or stat.S_ISLNK(current_path_metadata.st_mode)
            or not stat.S_ISREG(current_path_metadata.st_mode)
            or (current_metadata.st_dev, current_metadata.st_ino)
            != (current_path_metadata.st_dev, current_path_metadata.st_ino)
        ):
            raise QueueError
        yield True
    except OSError as error:
        raise QueueError from error
    finally:
        if handle is not None:
            if acquired:
                try:
                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
                except OSError:
                    pass
            handle.close()


def _recover_sent_state(record, now):
    """Best-effort reconciliation after a send succeeded but state write failed."""
    try:
        current = read_alert(record.path)
    except QueueError:
        return None
    if current.payload["delivery_state"] == "sent":
        return current
    try:
        return mark_sent(current, now)
    except QueueError:
        try:
            retain_failure(current, "state_update_failed", now)
        except QueueError:
            pass
        return None


def _valid_cursor_name(value):
    return (
        isinstance(value, str)
        and 0 < len(value.encode("utf-8")) <= 255
        and value not in {".", ".."}
        and Path(value).name == value
        and not any(ord(character) < 0x20 for character in value)
    )


def _read_scan_cursor(queue):
    path = queue.parent / SCAN_CURSOR_NAME
    try:
        path.lstat()
    except FileNotFoundError:
        return None
    except OSError as error:
        raise QueueError from error
    encoded, _identity_value = _read_verified_file(path, MAX_SCAN_CURSOR_BYTES)
    try:
        payload = _strict_json_loads(encoded.decode("utf-8"))
    except (UnicodeError, ValueError, TypeError) as error:
        raise QueueError from error
    if (
        not isinstance(payload, dict)
        or set(payload) != {"contract", "next_name"}
        or payload.get("contract") != SCAN_CURSOR_CONTRACT_VERSION
    ):
        raise QueueError
    next_name = payload.get("next_name")
    if next_name is not None and not _valid_cursor_name(next_name):
        raise QueueError
    return next_name


def _write_scan_cursor(queue, next_name):
    if next_name is not None and not _valid_cursor_name(next_name):
        raise QueueError
    _write_private_json(
        queue.parent / SCAN_CURSOR_NAME,
        {
            "contract": SCAN_CURSOR_CONTRACT_VERSION,
            "next_name": next_name,
        },
        MAX_SCAN_CURSOR_BYTES,
    )


def _scan_batch(queue, cursor_name):
    """Select a bounded batch and persistably rotate across stable dir order."""

    def collect(start_name):
        selected = []
        first_skipped_name = None
        next_name = None
        found_start = start_name is None
        try:
            with os.scandir(queue) as entries:
                for entry in entries:
                    name = entry.name
                    if not found_start:
                        if name == start_name:
                            found_start = True
                        else:
                            if first_skipped_name is None:
                                first_skipped_name = name
                            continue
                    if len(selected) < MAX_SCAN_ENTRIES:
                        selected.append(name)
                    else:
                        next_name = name
                        break
        except OSError as error:
            raise QueueError from error
        if not found_start:
            return None
        if next_name is None and first_skipped_name is not None:
            next_name = first_skipped_name
        return selected, next_name

    batch = collect(cursor_name)
    if batch is None:
        batch = collect(None)
    return batch


def deliver_pending(*, outbox=None, quarantine=None, environment=None, now=None):
    now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc).replace(microsecond=0)
    queue = prepare_outbox(outbox)
    quarantine_dir = Path(quarantine or (queue.parent / QUARANTINE_DIR.name))
    due = []
    scanned = 0
    deferred = 0
    invalid = 0
    quarantined = 0
    overflow = 0
    sent_cleanup_failed = 0
    cursor_name = _read_scan_cursor(queue)
    entry_names, next_cursor_name = _scan_batch(queue, cursor_name)
    overflow = int(next_cursor_name is not None)
    for name in entry_names:
        scanned += 1
        path = queue / name
        if _producer_commit_in_progress(path):
            deferred += 1
            continue
        try:
            record = read_alert(path)
        except QueueError:
            invalid += 1
            try:
                quarantine_entry(path, quarantine_dir)
                quarantined += 1
            except QueueError:
                pass
            continue
        if record.payload["delivery_state"] == "sent":
            try:
                delete_confirmed(record)
            except QueueError:
                sent_cleanup_failed += 1
            continue
        if _timestamp(record.payload["next_attempt_at"]) > now:
            deferred += 1
            continue
        if len(due) < MAX_SENDS_PER_RUN:
            due.append(record)
        else:
            deferred += 1
    _write_scan_cursor(queue, next_cursor_name)

    sent = 0
    retained = 0
    target = None
    target_error = None
    if due:
        try:
            target = resolve_chat_id(environment)
        except TargetError:
            target_error = "target_unavailable"
    for record in due:
        if target_error:
            try:
                retain_failure(record, target_error, now)
            except QueueError:
                invalid += 1
            retained += 1
            continue
        try:
            claimed = claim_delivery(record, now)
        except QueueError:
            invalid += 1
            retained += 1
            continue
        try:
            send_imessage(target, format_message(claimed.payload))
        except DeliveryError as error:
            try:
                retain_failure(claimed, error.code, now)
            except QueueError:
                invalid += 1
            retained += 1
            continue
        try:
            sent_record = mark_sent(claimed, now)
        except QueueError:
            sent_record = _recover_sent_state(claimed, now)
            if sent_record is None:
                invalid += 1
                retained += 1
                continue
        sent += 1
        try:
            delete_confirmed(sent_record)
        except QueueError:
            # The durable sent state remains and is cleanup-only on the next run.
            sent_cleanup_failed += 1

    status = "ok"
    if invalid or quarantined or overflow or sent_cleanup_failed:
        status = "failed"
    elif retained:
        status = "retry_pending"
    return {
        "contract": CONTRACT_VERSION,
        "status": status,
        "scanned": scanned,
        "due": len(due),
        "sent": sent,
        "retained": retained,
        "deferred": deferred,
        "invalid": invalid,
        "quarantined": quarantined,
        "overflow": overflow,
        "sent_cleanup_failed": sent_cleanup_failed,
    }


def run_canary(environment=None):
    chat_id = resolve_chat_id(environment)
    send_imessage(chat_id, CANARY_TEXT)
    return {
        "contract": CONTRACT_VERSION,
        "status": "canary_sent",
        "sent": 1,
    }


def main(argv=None):
    arguments = list(sys.argv[1:] if argv is None else argv)
    if arguments not in ([], ["--canary"]):
        print(json.dumps({"contract": CONTRACT_VERSION, "status": "invalid_arguments"}, sort_keys=True))
        return 2
    try:
        if arguments == ["--canary"]:
            result = run_canary()
        else:
            with notifier_lock() as acquired:
                if not acquired:
                    result = {"contract": CONTRACT_VERSION, "status": "already_running"}
                else:
                    started_at = datetime.now(timezone.utc).replace(microsecond=0)
                    try:
                        write_health("running", started_at)
                    except QueueError:
                        pass
                    try:
                        result = deliver_pending()
                    except TargetError:
                        result = {"contract": CONTRACT_VERSION, "status": "target_unavailable"}
                    except DeliveryError as error:
                        result = {"contract": CONTRACT_VERSION, "status": error.code}
                    except QueueError:
                        result = {"contract": CONTRACT_VERSION, "status": "queue_unavailable"}
                    except Exception:
                        result = {"contract": CONTRACT_VERSION, "status": "internal_error"}
                    try:
                        write_health(result["status"], started_at, result)
                    except QueueError:
                        if result["status"] == "ok":
                            result = {"contract": CONTRACT_VERSION, "status": "health_unavailable"}
    except TargetError:
        result = {"contract": CONTRACT_VERSION, "status": "target_unavailable"}
    except DeliveryError as error:
        result = {"contract": CONTRACT_VERSION, "status": error.code}
    except QueueError:
        result = {"contract": CONTRACT_VERSION, "status": "queue_unavailable"}
    except Exception:
        result = {"contract": CONTRACT_VERSION, "status": "internal_error"}
    print(json.dumps(result, sort_keys=True, separators=(",", ":")))
    return 0 if result["status"] in {"ok", "canary_sent"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
