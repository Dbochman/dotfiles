#!/usr/bin/env python3
"""Deterministic weekly financial scrape orchestration.

Child scraper output is captured in memory and never relayed to scheduled logs.
Only source names, phase states, allowlisted execution paths, and the safe
whole-run identifier are emitted.
"""

from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import fcntl
import json
import os
import re
import selectors
import signal
import stat
import subprocess
import sys
import tempfile
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit


REPO = Path.home() / "repos" / "financial-dashboard"
PYTHON = REPO / "venv" / "bin" / "python3"
LOCK_PATH = Path.home() / ".openclaw" / "financial-dashboard" / ".weekly-scrape.lock"
FINANCE_CREDENTIAL_CACHE = (
    Path.home()
    / ".openclaw"
    / "financial-dashboard"
    / "scraper-credentials.json"
)
CREDENTIAL_CACHE_MAX_BYTES = 64 * 1024
CREDENTIAL_VALUE_MAX_BYTES = 16 * 1024
FINAL_STATUS_PATH = (
    Path.home()
    / ".openclaw"
    / "financial-dashboard"
    / "weekly-scrape-status.json"
)
PROVIDER_MODE_PATH = (
    Path.home()
    / ".openclaw"
    / "financial-dashboard"
    / "scraper-modes.json"
)
PINCHTAB_INSTANCE_HELPER = Path.home() / ".openclaw" / "bin" / "pinchtab-headless-instance"
COMMAND_TIMEOUT_SECONDS = 420
CONTRACT_PREFLIGHT_TIMEOUT_SECONDS = 30
PROFILE_PREFLIGHT_TIMEOUT_SECONDS = 45
BOA_TAB_OPERATION_TIMEOUT_SECONDS = 30
PROCESS_GROUP_GRACE_SECONDS = 5
PROCESS_GROUP_POLL_SECONDS = 0.05
CHILD_OUTPUT_MAX_BYTES = 64 * 1024
CHILD_OUTPUT_READ_BYTES = 16 * 1024
CHILD_OUTPUT_REJECTED_RETURN_CODE = 125
TERMINATION_SIGNALS = (signal.SIGINT, signal.SIGTERM)
RUNTIME_ENV_ALLOWLIST = frozenset({
    "HOME",
    "LANG",
    "LC_ALL",
    "LC_CTYPE",
    "PATH",
    "TEMP",
    "TMP",
    "TMPDIR",
    "TZ",
})
RUNTIME_ENV_VALUE_MAX_BYTES = 16 * 1024
SCRAPER_CREDENTIAL_KEYS = ("SCRAPER_USER", "SCRAPER_PW")
PYTHON_DOTENV_DISABLED_KEY = "PYTHON_DOTENV_DISABLED"
TESLA_EMAIL_KEY = "TESLA_EMAIL"
TESLA_EMAIL_MAX_BYTES = 320
DOTENV_MAX_BYTES = 64 * 1024
DOTENV_LINE_MAX_BYTES = 16 * 1024
DOTENV_SAFE_PARENT_MODES = frozenset({0o700, 0o750, 0o755})
BOA_TAB_BOOTSTRAP_URL = (
    "https://secure.bankofamerica.com/login/sign-in/"
    "signOnV2Screen.go?request_locale=en-us"
)
BOA_TAB_REUSE_HOSTS = frozenset({"secure.bankofamerica.com"})
SCRAPER_CONTRACT_VERSION = 2
SCRAPER_CONTRACT_LINE = f"FINANCE_SCRAPER_CONTRACT {SCRAPER_CONTRACT_VERSION}"
SCRAPER_WRAPPER_CONTRACT_ARGS = (
    "--wrapper-contract",
    str(SCRAPER_CONTRACT_VERSION),
)
SCRAPER_CONTRACT_COMMAND = ("financial_scraper_contract.py", "--version")
SCRAPER_MANIFEST_COMMAND = ("financial_scraper_contract.py", "--manifest")
SCRAPER_STATUS_PREFIX = "FINANCE_SCRAPER_STATUS "
SCRAPER_STATUS_TOKEN = SCRAPER_STATUS_PREFIX.rstrip()
ALERT_CONTRACT_VERSION = 2
ALERT_OUTBOX_NAME = "weekly-scrape-alerts"
ALERT_MAX_BYTES = 16 * 1024
ALERT_TEMP_PREFIX = ".alert."
ALERT_TEMP_SUFFIX = ".tmp"
ALERT_STATES = (
    "profile_preflight",
    "scrape",
    "verify_auth",
    "tab_bootstrap",
    "reauth",
    "import",
    "path",
)
ALERT_STATUSES = frozenset({
    "preflight_failed",
    "failed",
    "degraded",
    "interrupted",
    "internal_error",
    "lock_unavailable",
    "status_write_failed",
})
ALERT_RUN_STATUSES = ALERT_STATUSES | {"ok"}
ALERT_REASONS = frozenset({
    "repository_unavailable",
    "scraper_contract_mismatch",
    "provider_mode_config_invalid",
    "credential_cache_unavailable",
    "tesla_configuration_unavailable",
    "unspecified",
})
ALERT_SIGNALS = frozenset({"sigint", "sigterm", "termination"})
ALERT_DELIVERY_STATES = frozenset({"pending", "inflight", "sent"})
ALERT_DELIVERY_ERRORS = frozenset({
    "target_unavailable",
    "imsg_unavailable",
    "send_timeout",
    "send_failed",
    "receipt_invalid",
    "state_update_failed",
})


class WrapperInterrupted(Exception):
    """Internal signal used to unwind cleanly after SIGINT or SIGTERM."""

    def __init__(self, signum):
        super().__init__(signum)
        self.signum = signum


class RunLockError(Exception):
    """The protected singleton lock could not be opened or acquired."""


class CredentialCacheError(Exception):
    """The dedicated finance credential cache is absent or malformed."""

    def __init__(self, missing_profiles=()):
        super().__init__("finance credential cache unavailable")
        self.missing_profiles = tuple(sorted(missing_profiles))


class FinalStatusError(Exception):
    """The protected final-status artifact could not be written safely."""


class AlertOutboxError(Exception):
    """The protected failure-alert outbox could not be updated safely."""


class ProviderModeError(Exception):
    """The optional protected provider rollback configuration is unsafe."""


class ChildOutputError(Exception):
    """A child's private output could not be captured within the contract."""


class TeslaConfigurationError(Exception):
    """The canonical Tesla identifier source is absent or unsafe."""


class ReauthEnvironment(dict):
    """Marker for one environment populated from the validated credential store."""


class TeslaEnvironment(dict):
    """Marker for the one Tesla scrape environment carrying its account email."""


_ACTIVE_PROCESS = None
_SPAWNING_PROCESS = False
_DEFERRED_TERMINATION_SIGNAL = None


@dataclass(frozen=True)
class CommandResult:
    returncode: int
    stdout: str = ""
    stderr: str = ""
    timed_out: bool = False
    output_rejected: bool = False

    @property
    def output(self) -> str:
        return f"{self.stdout}\n{self.stderr}"


@dataclass(frozen=True)
class BoaProfileResult:
    status: str
    instance_id: str | None = None


@dataclass(frozen=True)
class Source:
    name: str
    scrape_args: tuple[str, ...]
    import_args: tuple[str, ...]
    credential_profile: str | None = None
    reauth_args: tuple[str, ...] | None = None
    mortgage_source: str | None = None


FINANCE_CREDENTIAL_KEYS = {
    "eversource": (
        "FINANCE_EVERSOURCE_USERNAME",
        "FINANCE_EVERSOURCE_PASSWORD",
    ),
    "national_grid": (
        "FINANCE_NATIONAL_GRID_USERNAME",
        "FINANCE_NATIONAL_GRID_PASSWORD",
    ),
    "bwsc": (
        "FINANCE_BWSC_USERNAME",
        "FINANCE_BWSC_PASSWORD",
    ),
    "pennymac": (
        "FINANCE_PENNYMAC_USERNAME",
        "FINANCE_PENNYMAC_PASSWORD",
    ),
    "boa": (
        "FINANCE_BOA_USERNAME",
        "FINANCE_BOA_PASSWORD",
    ),
}
FINANCE_CREDENTIAL_ENV_KEYS = frozenset(
    key
    for pair in FINANCE_CREDENTIAL_KEYS.values()
    for key in pair
)


SOURCES = (
    Source(
        "tesla_solar",
        ("scrape_tesla_solar.py", "--merge", *SCRAPER_WRAPPER_CONTRACT_ARGS),
        ("update_data.py", "import-json-solar-cabin"),
    ),
    Source(
        "eversource",
        (
            "scrape_eversource.py", "--headless", "--merge",
            *SCRAPER_WRAPPER_CONTRACT_ARGS,
        ),
        ("update_data.py", "import-json-utilities"),
        "eversource",
        ("scrape_eversource.py", "--re-auth", "--headless"),
    ),
    Source(
        "national_grid_electric",
        (
            "scrape_national_grid_electric.py", "--headless", "--merge",
            *SCRAPER_WRAPPER_CONTRACT_ARGS,
        ),
        ("update_data.py", "import-json-electric-cabin"),
        "national_grid",
        ("scrape_national_grid_electric.py", "--re-auth", "--headless"),
    ),
    Source(
        "national_grid_gas",
        (
            "scrape_national_grid.py", "--headless", "--merge",
            *SCRAPER_WRAPPER_CONTRACT_ARGS,
        ),
        ("update_data.py", "import-json-gas"),
        "national_grid",
        ("scrape_national_grid.py", "--re-auth", "--headless"),
    ),
    Source(
        "bwsc",
        (
            "scrape_bwsc.py", "--headless", "--merge",
            *SCRAPER_WRAPPER_CONTRACT_ARGS,
        ),
        ("update_data.py", "import-json-water"),
        "bwsc",
        ("scrape_bwsc.py", "--re-auth", "--headless"),
    ),
    Source(
        "pennymac",
        (
            "scrape_mortgage.py", "--lender", "pennymac", "--headless",
            "--merge", *SCRAPER_WRAPPER_CONTRACT_ARGS,
        ),
        ("update_data.py", "import-json-pennymac-mortgage"),
        "pennymac",
        ("scrape_mortgage.py", "--lender", "pennymac", "--re-auth", "--headless"),
        "pennymac",
    ),
)


AUTH_FAILURE_LINES = {
    "eversource": "ERROR: Eversource authentication required",
    "national_grid_electric": "ERROR: National Grid authentication required",
    "national_grid_gas": "ERROR: National Grid authentication required",
    "bwsc": "ERROR: BWSC authentication required",
    "pennymac": "ERROR: PennyMac authentication required",
}
SOURCE_PATH_HEALTH = {
    "tesla_solar": {
        "direct_api": "healthy",
    },
    "eversource": {
        "direct_http": "healthy",
        "browser_recovery": "degraded",
        "browser_only": "degraded",
        "browser_explicit": "degraded",
    },
    "national_grid_electric": {
        "direct_http": "healthy",
        "browser_recovery": "degraded",
        "browser_only": "degraded",
        "browser_explicit": "degraded",
    },
    "national_grid_gas": {
        "direct_http": "healthy",
        "browser_recovery": "degraded",
        "browser_only": "degraded",
        "browser_explicit": "degraded",
    },
    "bwsc": {
        "direct_http": "healthy",
        "browser_recovery": "degraded",
        "browser_only": "degraded",
        "browser_explicit": "degraded",
    },
    "pennymac": {
        "direct_http": "healthy",
        "browser_recovery": "degraded",
        "browser_only": "degraded",
        "browser_explicit": "degraded",
    },
    "boa": {
        "direct_http": "healthy",
        "browser_recovery": "degraded",
        "browser_only": "degraded",
    },
}
PROVIDER_MODE_OPTIONS = {
    "tesla_solar": frozenset({"auto"}),
    "eversource": frozenset({"auto", "direct_only", "browser_only"}),
    "national_grid_electric": frozenset(
        {"auto", "direct_only", "browser_only"}
    ),
    "national_grid_gas": frozenset({"auto", "direct_only", "browser_only"}),
    "bwsc": frozenset({"auto", "direct_only", "browser_only"}),
    "pennymac": frozenset({"auto", "direct_only", "browser_only"}),
    "boa": frozenset({"auto", "direct_only", "browser_only"}),
}

# This is intentionally duplicated on the orchestration side of the
# repository boundary.  A matching integer version alone is not enough: the
# deployed financial checkout must advertise the exact seven entrypoints,
# guarded imports, execution paths, and rollback capabilities that this
# wrapper is about to invoke.
SCRAPER_CAPABILITY_MANIFEST = {
    "contract": SCRAPER_CONTRACT_VERSION,
    "sources": {
        "tesla_solar": {
            "entrypoint": "scrape_tesla_solar.py",
            "import_command": "import-json-solar-cabin",
            "paths": ["direct_api"],
            "capabilities": [
                "guarded_import", "merge_requires_run_id", "run_id", "status_v2",
                "wrapper_contract_v2",
            ],
        },
        "eversource": {
            "entrypoint": "scrape_eversource.py",
            "import_command": "import-json-utilities",
            "paths": [
                "browser_explicit", "browser_only", "browser_recovery", "direct_http",
            ],
            "capabilities": [
                "browser_only", "direct_only", "guarded_import",
                "merge_requires_run_id", "run_id", "status_v2",
                "wrapper_contract_v2",
            ],
        },
        "national_grid_electric": {
            "entrypoint": "scrape_national_grid_electric.py",
            "import_command": "import-json-electric-cabin",
            "paths": [
                "browser_explicit", "browser_only", "browser_recovery", "direct_http",
            ],
            "capabilities": [
                "browser_only", "direct_only", "guarded_import",
                "merge_requires_run_id", "run_id", "status_v2",
                "wrapper_contract_v2",
            ],
        },
        "national_grid_gas": {
            "entrypoint": "scrape_national_grid.py",
            "import_command": "import-json-gas",
            "paths": [
                "browser_explicit", "browser_only", "browser_recovery", "direct_http",
            ],
            "capabilities": [
                "browser_only", "direct_only", "guarded_import",
                "merge_requires_run_id", "run_id", "status_v2",
                "wrapper_contract_v2",
            ],
        },
        "bwsc": {
            "entrypoint": "scrape_bwsc.py",
            "import_command": "import-json-water",
            "paths": [
                "browser_explicit", "browser_only", "browser_recovery", "direct_http",
            ],
            "capabilities": [
                "browser_only", "direct_only", "guarded_import",
                "merge_requires_run_id", "run_id", "status_v2",
                "wrapper_contract_v2",
            ],
        },
        "pennymac": {
            "entrypoint": "scrape_mortgage.py",
            "import_command": "import-json-pennymac-mortgage",
            "paths": [
                "browser_explicit", "browser_only", "browser_recovery", "direct_http",
            ],
            "capabilities": [
                "browser_only", "direct_only", "guarded_import",
                "merge_requires_run_id", "run_id", "status_v2",
                "wrapper_contract_v2",
            ],
        },
        "boa": {
            "entrypoint": "scrape_mortgage.py",
            "import_command": "import-json-boa-mortgage",
            "paths": ["browser_only", "browser_recovery", "direct_http"],
            "capabilities": [
                "browser_only", "direct_only", "guarded_import",
                "merge_requires_run_id", "run_id", "status_v2",
                "wrapper_contract_v2",
            ],
        },
    },
}
SCRAPER_MANIFEST_LINE = json.dumps(
    SCRAPER_CAPABILITY_MANIFEST,
    sort_keys=True,
    separators=(",", ":"),
)

BOA_REAUTH_SAFE_STATUSES = frozenset({
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
})
BOA_REAUTH_SUCCESS_STATUSES = frozenset({"authenticated", "already_authenticated"})
BOA_VERIFY_SAFE_STATUSES = frozenset({
    "authenticated",
    "auth_unknown",
    "boa_tab_unavailable",
    "cdp_attach_failed",
    "cdp_unavailable",
    "not_authenticated",
    "signed_out_landing",
})
ALERT_STATE_VALUES = {
    "profile_preflight": frozenset({"ok", "not_needed", "timeout", "failed"}),
    "scrape": frozenset({"ok", "failed", "timeout"}),
    "verify_auth": BOA_VERIFY_SAFE_STATUSES | {"not_needed", "verify_failed"},
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
    "reauth": BOA_REAUTH_SAFE_STATUSES | {
        "not_needed",
        "credentials_unavailable",
        "ok",
        "failed",
        "timeout",
        "reauth_failed",
    },
    "import": frozenset({"ok", "failed", "timeout", "skipped"}),
    "path": frozenset(
        path
        for paths in SOURCE_PATH_HEALTH.values()
        for path in paths
    ) | {"mode_mismatch", "contract_invalid", "not_observed"},
}


def _signal_process_group(process, signum):
    """Signal one child session without ever inspecting its captured output."""
    if process is None:
        return
    try:
        os.killpg(process.pid, signum)
    except (ProcessLookupError, PermissionError):
        pass


def _close_process_pipes(process):
    """Close both capture pipes without reading or persisting more output."""
    if process is None:
        return
    for stream in (process.stdout, process.stderr):
        if stream is None:
            continue
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


def _stop_process_group(process):
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


def _read_bounded_child_output(process, timeout):
    """Drain stdout/stderr under one strict byte and time ceiling."""
    buffers = {"stdout": bytearray(), "stderr": bytearray()}
    streams = {
        "stdout": process.stdout,
        "stderr": process.stderr,
    }
    if any(stream is None for stream in streams.values()):
        raise ChildOutputError

    deadline = time.monotonic() + timeout
    total_bytes = 0
    selector = selectors.DefaultSelector()
    try:
        for name, stream in streams.items():
            os.set_blocking(stream.fileno(), False)
            selector.register(stream, selectors.EVENT_READ, name)

        while selector.get_map():
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise subprocess.TimeoutExpired("child", timeout)
            ready = selector.select(remaining)
            if not ready:
                if time.monotonic() >= deadline:
                    raise subprocess.TimeoutExpired("child", timeout)
                continue

            for key, _events in ready:
                room = CHILD_OUTPUT_MAX_BYTES - total_bytes
                try:
                    chunk = os.read(
                        key.fd,
                        min(CHILD_OUTPUT_READ_BYTES, room + 1),
                    )
                except BlockingIOError:
                    continue
                if not chunk:
                    selector.unregister(key.fileobj)
                    key.fileobj.close()
                    continue
                if len(chunk) > room:
                    raise ChildOutputError
                buffers[key.data].extend(chunk)
                total_bytes += len(chunk)

        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise subprocess.TimeoutExpired("child", timeout)
        process.wait(timeout=remaining)
    except (OSError, ValueError) as error:
        raise ChildOutputError from error
    finally:
        selector.close()
        _close_process_pipes(process)

    return bytes(buffers["stdout"]), bytes(buffers["stderr"])


def _run_captured(command, env, timeout, cwd=None):
    """Run a command with bounded private output and contain its lifecycle."""
    global _ACTIVE_PROCESS, _SPAWNING_PROCESS

    process = None
    try:
        try:
            _SPAWNING_PROCESS = True
            try:
                process = subprocess.Popen(
                    command,
                    cwd=cwd,
                    env=env,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    bufsize=0,
                    start_new_session=True,
                )
                _ACTIVE_PROCESS = process
            finally:
                _SPAWNING_PROCESS = False
                _raise_deferred_termination()
        except (OSError, ValueError):
            return CommandResult(127)

        stdout_bytes, stderr_bytes = _read_bounded_child_output(process, timeout)
    except subprocess.TimeoutExpired:
        return CommandResult(124, timed_out=True)
    except ChildOutputError:
        return CommandResult(
            CHILD_OUTPUT_REJECTED_RETURN_CODE,
            output_rejected=True,
        )
    except BaseException:
        raise
    finally:
        # A leader can close both pipes and exit while a same-session
        # descendant remains alive. Always drain the entire private group,
        # including on normal completion and before decoding captured bytes.
        _stop_process_group(process)
        if _ACTIVE_PROCESS is process:
            _ACTIVE_PROCESS = None
    try:
        stdout = stdout_bytes.decode("utf-8", errors="strict")
        stderr = stderr_bytes.decode("utf-8", errors="strict")
    except UnicodeError:
        return CommandResult(
            CHILD_OUTPUT_REJECTED_RETURN_CODE,
            output_rejected=True,
        )
    return CommandResult(process.returncode, stdout, stderr)


def _termination_handler(signum, _frame):
    """Stop a tracked child or defer unwinding until spawn registration finishes."""
    global _DEFERRED_TERMINATION_SIGNAL

    _signal_process_group(_ACTIVE_PROCESS, signal.SIGTERM)
    if _SPAWNING_PROCESS:
        if _DEFERRED_TERMINATION_SIGNAL is None:
            _DEFERRED_TERMINATION_SIGNAL = signum
        return
    raise WrapperInterrupted(signum)


def _raise_deferred_termination():
    """Raise a signal deferred while a newly spawned child was untrackable."""
    global _DEFERRED_TERMINATION_SIGNAL

    if _DEFERRED_TERMINATION_SIGNAL is None:
        return
    signum = _DEFERRED_TERMINATION_SIGNAL
    _DEFERRED_TERMINATION_SIGNAL = None
    raise WrapperInterrupted(signum)


@contextmanager
def termination_signal_handlers():
    previous = {}
    try:
        for signum in TERMINATION_SIGNALS:
            previous[signum] = signal.getsignal(signum)
            signal.signal(signum, _termination_handler)
        yield
    finally:
        for signum, handler in previous.items():
            signal.signal(signum, handler)


@contextmanager
def singleton_lock():
    """Yield whether this process owns the nonblocking whole-run lock."""
    lock_file = None
    descriptor = None
    try:
        LOCK_PATH.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        parent_metadata = LOCK_PATH.parent.lstat()
        if (
            stat.S_ISLNK(parent_metadata.st_mode)
            or not stat.S_ISDIR(parent_metadata.st_mode)
            or parent_metadata.st_uid != os.getuid()
            or stat.S_IMODE(parent_metadata.st_mode) != 0o700
        ):
            raise RunLockError
        flags = (
            os.O_RDWR
            | os.O_CREAT
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NOFOLLOW", 0)
            | getattr(os, "O_NONBLOCK", 0)
        )
        descriptor = os.open(LOCK_PATH, flags, 0o600)
        lock_file = os.fdopen(descriptor, "r+", encoding="utf-8")
        descriptor = None
        descriptor_metadata = os.fstat(lock_file.fileno())
        path_metadata = LOCK_PATH.lstat()
        if (
            not stat.S_ISREG(descriptor_metadata.st_mode)
            or descriptor_metadata.st_uid != os.getuid()
            or stat.S_IMODE(descriptor_metadata.st_mode) != 0o600
            or descriptor_metadata.st_nlink != 1
            or stat.S_ISLNK(path_metadata.st_mode)
            or not stat.S_ISREG(path_metadata.st_mode)
            or (descriptor_metadata.st_dev, descriptor_metadata.st_ino)
            != (path_metadata.st_dev, path_metadata.st_ino)
        ):
            raise RunLockError
    except OSError as error:
        if lock_file is not None:
            lock_file.close()
        elif descriptor is not None:
            os.close(descriptor)
        raise RunLockError from error
    except RunLockError:
        if lock_file is not None:
            lock_file.close()
        elif descriptor is not None:
            os.close(descriptor)
        raise

    acquired = False
    try:
        try:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            yield False
            return
        except OSError as error:
            raise RunLockError from error
        acquired = True
        try:
            current_path_metadata = LOCK_PATH.lstat()
            descriptor_metadata = os.fstat(lock_file.fileno())
        except OSError as error:
            raise RunLockError from error
        if (
            stat.S_ISLNK(current_path_metadata.st_mode)
            or not stat.S_ISREG(current_path_metadata.st_mode)
            or (descriptor_metadata.st_dev, descriptor_metadata.st_ino)
            != (current_path_metadata.st_dev, current_path_metadata.st_ino)
        ):
            raise RunLockError
        yield True
    finally:
        if acquired:
            try:
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)
            except OSError:
                pass
        lock_file.close()


def run_command(arguments, env, timeout=COMMAND_TIMEOUT_SECONDS):
    return _run_captured(
        [str(PYTHON), *arguments],
        python_child_environment(env),
        timeout,
        cwd=REPO,
    )


def scrub_child_environment(parent_env):
    """Build the minimal nonsecret environment shared by ordinary children."""
    child_env = {}
    for key in RUNTIME_ENV_ALLOWLIST:
        value = parent_env.get(key)
        if not isinstance(value, str) or "\x00" in value:
            continue
        try:
            if len(value.encode("utf-8")) > RUNTIME_ENV_VALUE_MAX_BYTES:
                continue
        except UnicodeError:
            continue
        child_env[key] = value
    return child_env


def python_child_environment(environment):
    """Retain guarded scraper credentials while dropping every other key."""
    child_env = scrub_child_environment(environment)
    child_env[PYTHON_DOTENV_DISABLED_KEY] = "1"
    if isinstance(environment, ReauthEnvironment):
        credentials = [environment.get(key) for key in SCRAPER_CREDENTIAL_KEYS]
        if all(isinstance(value, str) and value for value in credentials):
            for key, value in zip(SCRAPER_CREDENTIAL_KEYS, credentials):
                if "\x00" in value:
                    return child_env
                try:
                    if len(value.encode("utf-8")) > CREDENTIAL_VALUE_MAX_BYTES:
                        return child_env
                except UnicodeError:
                    return child_env
            child_env.update(zip(SCRAPER_CREDENTIAL_KEYS, credentials))
    elif isinstance(environment, TeslaEnvironment):
        email = environment.get(TESLA_EMAIL_KEY)
        if _valid_tesla_email(email):
            child_env[TESLA_EMAIL_KEY] = email
    return child_env


def _valid_tesla_email(value):
    if not isinstance(value, str) or not value:
        return False
    if any(
        character.isspace()
        or ord(character) < 32
        or ord(character) == 127
        for character in value
    ):
        return False
    try:
        return len(value.encode("utf-8")) <= TESLA_EMAIL_MAX_BYTES
    except UnicodeError:
        return False


def tesla_source_environment(runtime_env, email):
    """Scope the one required Tesla identifier to the Tesla scrape process."""
    child_env = TeslaEnvironment(scrub_child_environment(runtime_env))
    if not _valid_tesla_email(email):
        return child_env
    child_env[TESLA_EMAIL_KEY] = email
    return child_env


def load_tesla_email(path=None):
    """Read only TESLA_EMAIL from the canonical private dotenv file."""
    dotenv_path = Path(path or (REPO / ".env"))
    try:
        parent_metadata = dotenv_path.parent.lstat()
        path_metadata = dotenv_path.lstat()
    except OSError as error:
        raise TeslaConfigurationError from error
    if (
        stat.S_ISLNK(parent_metadata.st_mode)
        or not stat.S_ISDIR(parent_metadata.st_mode)
        or parent_metadata.st_uid != os.getuid()
        or stat.S_IMODE(parent_metadata.st_mode) not in DOTENV_SAFE_PARENT_MODES
        or stat.S_ISLNK(path_metadata.st_mode)
        or not stat.S_ISREG(path_metadata.st_mode)
        or path_metadata.st_uid != os.getuid()
        or stat.S_IMODE(path_metadata.st_mode) != 0o600
        or path_metadata.st_nlink != 1
        or not 0 < path_metadata.st_size <= DOTENV_MAX_BYTES
    ):
        raise TeslaConfigurationError

    flags = (
        os.O_RDONLY
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOFOLLOW", 0)
        | getattr(os, "O_NONBLOCK", 0)
    )
    descriptor = None
    try:
        descriptor = os.open(dotenv_path, flags)
        before = os.fstat(descriptor)
        encoded = os.read(descriptor, DOTENV_MAX_BYTES + 1)
        after = os.fstat(descriptor)
        verified_parent = dotenv_path.parent.lstat()
        verified_path = dotenv_path.lstat()
    except OSError as error:
        raise TeslaConfigurationError from error
    finally:
        if descriptor is not None:
            os.close(descriptor)
    if (
        not stat.S_ISREG(before.st_mode)
        or before.st_uid != os.getuid()
        or stat.S_IMODE(before.st_mode) != 0o600
        or before.st_nlink != 1
        or (before.st_dev, before.st_ino)
        != (path_metadata.st_dev, path_metadata.st_ino)
        or (before.st_dev, before.st_ino) != (after.st_dev, after.st_ino)
        or (before.st_dev, before.st_ino)
        != (verified_path.st_dev, verified_path.st_ino)
        or stat.S_IMODE(after.st_mode) != 0o600
        or after.st_uid != os.getuid()
        or after.st_nlink != 1
        or before.st_size != after.st_size
        or before.st_size != path_metadata.st_size
        or before.st_size != verified_path.st_size
        or before.st_size != len(encoded)
        or not encoded
        or len(encoded) > DOTENV_MAX_BYTES
        or (parent_metadata.st_dev, parent_metadata.st_ino)
        != (verified_parent.st_dev, verified_parent.st_ino)
        or stat.S_ISLNK(verified_parent.st_mode)
        or not stat.S_ISDIR(verified_parent.st_mode)
        or verified_parent.st_uid != os.getuid()
        or stat.S_IMODE(verified_parent.st_mode) not in DOTENV_SAFE_PARENT_MODES
        or stat.S_ISLNK(verified_path.st_mode)
        or not stat.S_ISREG(verified_path.st_mode)
        or verified_path.st_uid != os.getuid()
        or stat.S_IMODE(verified_path.st_mode) != 0o600
        or verified_path.st_nlink != 1
    ):
        raise TeslaConfigurationError
    try:
        text = encoded.decode("utf-8")
    except UnicodeError as error:
        raise TeslaConfigurationError from error
    if "\r" in text or "\x00" in text:
        raise TeslaConfigurationError

    seen_keys = set()
    email = None
    for line in text.split("\n"):
        if line == "" or line.startswith("#"):
            continue
        try:
            if len(line.encode("utf-8")) > DOTENV_LINE_MAX_BYTES:
                raise TeslaConfigurationError
        except UnicodeError as error:
            raise TeslaConfigurationError from error
        match = re.fullmatch(r"([A-Za-z_][A-Za-z0-9_]{0,127})=(.*)", line)
        if match is None:
            raise TeslaConfigurationError
        key, value = match.groups()
        if key in seen_keys or any(
            ord(character) < 32 or ord(character) == 127
            for character in value
        ):
            raise TeslaConfigurationError
        seen_keys.add(key)
        if key == TESLA_EMAIL_KEY:
            email = value
    if not _valid_tesla_email(email):
        raise TeslaConfigurationError
    return email


def load_provider_modes(path=None):
    """Load an optional exact owner-only rollback map; otherwise use auto."""
    mode_path = Path(path or PROVIDER_MODE_PATH)
    defaults = {source: "auto" for source in PROVIDER_MODE_OPTIONS}
    try:
        parent_metadata = mode_path.parent.lstat()
        metadata = mode_path.lstat()
    except FileNotFoundError:
        return defaults
    except OSError as error:
        raise ProviderModeError from error
    if (
        stat.S_ISLNK(parent_metadata.st_mode)
        or not stat.S_ISDIR(parent_metadata.st_mode)
        or parent_metadata.st_uid != os.getuid()
        or stat.S_IMODE(parent_metadata.st_mode) != 0o700
        or stat.S_ISLNK(metadata.st_mode)
        or not stat.S_ISREG(metadata.st_mode)
        or metadata.st_uid != os.getuid()
        or stat.S_IMODE(metadata.st_mode) != 0o600
        or metadata.st_nlink != 1
        or not 0 < metadata.st_size <= 64 * 1024
    ):
        raise ProviderModeError

    flags = (
        os.O_RDONLY
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOFOLLOW", 0)
        | getattr(os, "O_NONBLOCK", 0)
    )
    descriptor = None
    try:
        descriptor = os.open(mode_path, flags)
        before = os.fstat(descriptor)
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_uid != os.getuid()
            or stat.S_IMODE(before.st_mode) != 0o600
            or before.st_nlink != 1
            or (before.st_dev, before.st_ino)
            != (metadata.st_dev, metadata.st_ino)
        ):
            raise ProviderModeError
        encoded = os.read(descriptor, 64 * 1024 + 1)
        if not encoded or len(encoded) > 64 * 1024:
            raise ProviderModeError
        after = os.fstat(descriptor)
        if (
            (before.st_dev, before.st_ino) != (after.st_dev, after.st_ino)
            or after.st_size != len(encoded)
        ):
            raise ProviderModeError
    except (OSError, TypeError, ValueError) as error:
        if isinstance(error, ProviderModeError):
            raise
        raise ProviderModeError from error
    finally:
        if descriptor is not None:
            os.close(descriptor)

    def unique_object(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("duplicate")
            result[key] = value
        return result

    try:
        payload = json.loads(
            encoded.decode("utf-8"),
            object_pairs_hook=unique_object,
            parse_constant=lambda _value: (_ for _ in ()).throw(
                ValueError("nonfinite")
            ),
        )
    except (UnicodeError, ValueError, TypeError, RecursionError) as error:
        raise ProviderModeError from error
    if (
        not isinstance(payload, dict)
        or set(payload) != {"contract", "modes"}
        or type(payload.get("contract")) is not int
        or payload["contract"] != SCRAPER_CONTRACT_VERSION
        or not isinstance(payload.get("modes"), dict)
        or set(payload["modes"]) != set(PROVIDER_MODE_OPTIONS)
    ):
        raise ProviderModeError
    modes = payload["modes"]
    if any(
        not isinstance(mode, str)
        or mode not in PROVIDER_MODE_OPTIONS[source]
        for source, mode in modes.items()
    ):
        raise ProviderModeError
    return dict(modes)


def apply_provider_mode(arguments, source_name, provider_modes):
    """Append only the exact diagnostic flag selected for one source."""
    mode = provider_modes[source_name]
    if mode == "auto":
        return tuple(arguments)
    return (*arguments, f"--{mode.replace('_', '-')}")


def load_credential_store(path=None):
    """Read the dedicated owner-only JSON cache without invoking a shell."""
    cache_path = Path(path or FINANCE_CREDENTIAL_CACHE)
    try:
        parent_metadata = cache_path.parent.lstat()
        metadata = cache_path.lstat()
    except OSError as error:
        raise CredentialCacheError(FINANCE_CREDENTIAL_KEYS) from error
    if (
        stat.S_ISLNK(parent_metadata.st_mode)
        or not stat.S_ISDIR(parent_metadata.st_mode)
        or parent_metadata.st_uid != os.getuid()
        or stat.S_IMODE(parent_metadata.st_mode) != 0o700
    ):
        raise CredentialCacheError()
    if (
        stat.S_ISLNK(metadata.st_mode)
        or not stat.S_ISREG(metadata.st_mode)
        or metadata.st_uid != os.getuid()
        or stat.S_IMODE(metadata.st_mode) != 0o600
        or metadata.st_nlink != 1
        or not 0 < metadata.st_size <= CREDENTIAL_CACHE_MAX_BYTES
    ):
        raise CredentialCacheError()

    flags = (
        os.O_RDONLY
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOFOLLOW", 0)
        | getattr(os, "O_NONBLOCK", 0)
    )
    descriptor = None
    try:
        descriptor = os.open(cache_path, flags)
        before = os.fstat(descriptor)
        encoded = os.read(descriptor, CREDENTIAL_CACHE_MAX_BYTES + 1)
        after = os.fstat(descriptor)
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_uid != os.getuid()
            or stat.S_IMODE(before.st_mode) != 0o600
            or before.st_nlink != 1
            or (before.st_dev, before.st_ino) != (metadata.st_dev, metadata.st_ino)
            or (before.st_dev, before.st_ino) != (after.st_dev, after.st_ino)
            or before.st_size != len(encoded)
            or not encoded
            or len(encoded) > CREDENTIAL_CACHE_MAX_BYTES
        ):
            raise CredentialCacheError()
        payload = _strict_json_loads(encoded)
    except (OSError, UnicodeError, ValueError, TypeError, RecursionError) as error:
        if isinstance(error, CredentialCacheError):
            raise
        raise CredentialCacheError() from error
    finally:
        if descriptor is not None:
            os.close(descriptor)
    if not isinstance(payload, dict) or set(payload) != set(FINANCE_CREDENTIAL_KEYS):
        missing = set(FINANCE_CREDENTIAL_KEYS) - set(payload) if isinstance(payload, dict) else ()
        raise CredentialCacheError(missing)

    credential_store = {}
    malformed = []
    for profile in FINANCE_CREDENTIAL_KEYS:
        values = payload.get(profile)
        if not isinstance(values, dict) or set(values) != {"username", "password"}:
            malformed.append(profile)
            continue
        username = values.get("username")
        password = values.get("password")
        if (
            not isinstance(username, str)
            or not username
            or not isinstance(password, str)
            or not password
            or "\x00" in username
            or "\x00" in password
        ):
            malformed.append(profile)
            continue
        try:
            username_size = len(username.encode("utf-8"))
            password_size = len(password.encode("utf-8"))
        except UnicodeError:
            malformed.append(profile)
            continue
        if (
            username_size > CREDENTIAL_VALUE_MAX_BYTES
            or password_size > CREDENTIAL_VALUE_MAX_BYTES
        ):
            malformed.append(profile)
            continue
        credential_store[profile] = (username, password)
    if malformed:
        raise CredentialCacheError(malformed)
    return credential_store


def credentials_for(profile, env, credential_store):
    credentials = credential_store.get(profile)
    if credentials is None:
        return None
    username, password = credentials
    child_env = ReauthEnvironment(scrub_child_environment(env))
    child_env["SCRAPER_USER"] = username
    child_env["SCRAPER_PW"] = password
    return child_env


def ensure_boa_profile(env):
    """Start or reuse the dedicated headless PinchTab finance profile.

    The helper output is treated as untrusted and never relayed. This step
    does not navigate a tab, read credentials, or weaken the exact
    ``not_authenticated`` gate that protects BoA re-authentication. The
    instance identifier remains process-local so later tab operations cannot
    accidentally fall back to another PinchTab profile.
    """
    browser_env = scrub_child_environment(env)
    completed = _run_captured(
        [str(PINCHTAB_INSTANCE_HELPER), "acquire", "finance"],
        browser_env,
        PROFILE_PREFLIGHT_TIMEOUT_SECONDS,
    )
    if completed.timed_out:
        return BoaProfileResult("timeout")
    if completed.returncode != 0:
        return BoaProfileResult("failed")
    lines = completed.stdout.splitlines()
    if len(lines) != 1 or not re.fullmatch(
        r"inst_[A-Za-z0-9]+\t[01]",
        lines[0].strip(),
    ):
        return BoaProfileResult("failed")
    instance_id, _started = lines[0].strip().split("\t", 1)
    return BoaProfileResult("ok", instance_id)


def _boa_tab_url_reusable(url):
    """Reuse only exact secure-host HTTPS tabs without userinfo or ports."""
    if not isinstance(url, str):
        return False
    try:
        parsed = urlsplit(url)
        port = parsed.port
    except (TypeError, ValueError):
        return False
    return (
        parsed.scheme == "https"
        and parsed.hostname in BOA_TAB_REUSE_HOSTS
        and parsed.username is None
        and parsed.password is None
        and port in (None, 443)
    )


def _boa_instance_id_allowed(instance_id):
    """Accept only one parsed PinchTab managed-instance identifier."""
    return isinstance(instance_id, str) and bool(
        re.fullmatch(r"inst_[A-Za-z0-9]+", instance_id)
    )


def parse_pinchtab_tabs(output):
    """Return tab URLs from one schema-safe PinchTab list response."""
    try:
        payload = json.loads(output)
    except (json.JSONDecodeError, TypeError):
        return None
    tabs = payload.get("tabs") if isinstance(payload, dict) else payload
    if not isinstance(tabs, list):
        return None
    urls = []
    for tab in tabs:
        if not isinstance(tab, dict) or not isinstance(tab.get("url"), str):
            return None
        urls.append(tab["url"])
    return urls


def ensure_boa_tab(instance_id, env):
    """Reuse an allowlisted BoA tab or seed exactly one fixed sign-in URL.

    Tab inventory and open output stay private. Both helper calls receive a
    defensively scrubbed environment, and this function never reads or
    selects a credential profile.
    """
    if not _boa_instance_id_allowed(instance_id):
        return "profile_unavailable"

    browser_env = scrub_child_environment(env)
    listed = _run_captured(
        [str(PINCHTAB_INSTANCE_HELPER), "tabs", instance_id],
        browser_env,
        BOA_TAB_OPERATION_TIMEOUT_SECONDS,
    )
    if listed.timed_out:
        return "tab_list_timeout"
    if listed.returncode != 0:
        return "tab_list_failed"
    tab_urls = parse_pinchtab_tabs(listed.stdout)
    if tab_urls is None:
        return "tab_list_failed"
    if any(_boa_tab_url_reusable(url) for url in tab_urls):
        return "reused"

    opened = _run_captured(
        [
            str(PINCHTAB_INSTANCE_HELPER),
            "open",
            instance_id,
            BOA_TAB_BOOTSTRAP_URL,
        ],
        browser_env,
        BOA_TAB_OPERATION_TIMEOUT_SECONDS,
    )
    if opened.timed_out:
        return "open_timeout"
    if opened.returncode != 0:
        return "open_failed"
    lines = opened.stdout.splitlines()
    if len(lines) != 1 or not re.fullmatch(
        r"[A-Za-z0-9_.:-]{4,160}",
        lines[0].strip(),
    ):
        return "open_failed"
    return "opened"


def command_status(result):
    if result.timed_out:
        return "timeout"
    if result.output_rejected:
        return "failed"
    return "ok" if result.returncode == 0 else "failed"


def is_auth_failure(result, source_name=None):
    """Accept only one provider-owned, exact full-line authentication marker."""
    if result.output_rejected:
        return False
    marker = AUTH_FAILURE_LINES.get(source_name)
    if marker is None:
        return False
    return any(line.strip() == marker for line in result.output.splitlines())


def parse_scraper_status(output, expected_source):
    """Return one allowlisted contract-v2 path or ``None`` on any ambiguity."""
    candidates = []
    malformed = False
    for raw_line in output.splitlines():
        line = raw_line.strip()
        if not line.startswith(SCRAPER_STATUS_TOKEN):
            continue
        if not line.startswith(SCRAPER_STATUS_PREFIX):
            malformed = True
            continue
        candidates.append(line[len(SCRAPER_STATUS_PREFIX):])

    if malformed or len(candidates) != 1:
        return None
    try:
        payload = json.loads(candidates[0])
    except (TypeError, json.JSONDecodeError):
        return None
    if json.dumps(payload, separators=(",", ":")) != candidates[0]:
        return None
    if not isinstance(payload, dict) or set(payload) != {"contract", "source", "path"}:
        return None
    if type(payload.get("contract")) is not int or payload["contract"] != SCRAPER_CONTRACT_VERSION:
        return None
    if payload.get("source") != expected_source:
        return None
    path = payload.get("path")
    if not isinstance(path, str) or path not in SOURCE_PATH_HEALTH.get(expected_source, {}):
        return None
    return path


def path_is_degraded(source_name, path):
    return SOURCE_PATH_HEALTH.get(source_name, {}).get(path) == "degraded"


def path_matches_provider_mode(source_name, path, mode):
    if mode == "auto":
        return True
    if mode == "direct_only":
        return path in {"direct_api", "direct_http"}
    if mode == "browser_only":
        return path == "browser_only"
    return False


def with_run_id(arguments, run_id, _mortgage_source=None):
    return (*arguments, "--run-id", run_id)


def guarded_import_args(source, run_id):
    return (*source.import_args, "--require-run-id", run_id)


def run_standard_source(
    source,
    run_id,
    env,
    credential_store=None,
    provider_modes=None,
):
    runtime_env = scrub_child_environment(env)
    scrape_env = (
        env
        if source.name == "tesla_solar" and isinstance(env, TeslaEnvironment)
        else runtime_env
    )
    modes = provider_modes or {
        source_name: "auto" for source_name in PROVIDER_MODE_OPTIONS
    }
    mode = modes[source.name]
    scrape_args = apply_provider_mode(
        with_run_id(source.scrape_args, run_id, source.mortgage_source),
        source.name,
        modes,
    )
    scrape = run_command(scrape_args, scrape_env)
    reauth_status = "not_needed"

    if (
        scrape.returncode != 0
        and mode != "direct_only"
        and source.reauth_args
        and is_auth_failure(scrape, source.name)
    ):
        credential_env = credentials_for(
            source.credential_profile,
            runtime_env,
            credential_store or {},
        )
        if credential_env is None:
            reauth_status = "credentials_unavailable"
        else:
            try:
                reauth = run_command(source.reauth_args, credential_env)
            finally:
                credential_env.pop("SCRAPER_USER", None)
                credential_env.pop("SCRAPER_PW", None)
            reauth_status = command_status(reauth)
            if reauth.returncode == 0 and not reauth.output_rejected:
                scrape = run_command(scrape_args, scrape_env)

    path = (
        parse_scraper_status(scrape.output, source.name)
        if scrape.returncode == 0 and not scrape.output_rejected
        else None
    )
    mode_mismatch = path is not None and not path_matches_provider_mode(
        source.name, path, mode
    )
    if mode_mismatch:
        path = None
    scrape_status = (
        command_status(scrape)
        if scrape.returncode != 0 or path is not None
        else "failed"
    )
    result = {
        "source": source.name,
        "scrape": scrape_status,
        "reauth": reauth_status,
        "import": "skipped",
        "path": path or (
            "mode_mismatch" if mode_mismatch else (
                "contract_invalid" if scrape.returncode == 0 else "not_observed"
            )
        ),
    }
    if (
        scrape.returncode == 0
        and not scrape.output_rejected
        and path is not None
    ):
        imported = run_command(guarded_import_args(source, run_id), runtime_env)
        result["import"] = command_status(imported)
    return result


def parse_boa_verify_status(output):
    statuses = []
    pattern = re.compile(
        r"^(?:\[[^\]]+\]\s+)?boa-tab-verify:\s*([a-z_]+)(?:\s+.*)?$"
    )
    for line in output.splitlines():
        match = pattern.fullmatch(line.strip())
        if match:
            statuses.append(match.group(1))
    if len(statuses) == 1 and statuses[0] in BOA_VERIFY_SAFE_STATUSES:
        return statuses[0]
    return "verify_failed"


def parse_boa_reauth_status(output):
    """Return one recognized raw-CDP result without relaying child output."""
    statuses = []
    pattern = re.compile(
        r"^(?:\[[^\]]+\]\s+)?boa-raw-cdp-reauth:\s*([a-z_]+)(?:\s+.*)?$"
    )
    for line in output.splitlines():
        match = pattern.fullmatch(line.strip())
        if match:
            statuses.append(match.group(1))
    if len(statuses) == 1 and statuses[0] in BOA_REAUTH_SAFE_STATUSES:
        return statuses[0]
    return "reauth_failed"


def run_boa(
    run_id,
    env,
    credential_store=None,
    instance_id=None,
    provider_modes=None,
):
    runtime_env = scrub_child_environment(env)
    modes = provider_modes or {
        source_name: "auto" for source_name in PROVIDER_MODE_OPTIONS
    }
    mode = modes["boa"]
    if mode != "direct_only" and not _boa_instance_id_allowed(instance_id):
        return {
            "source": "boa",
            "scrape": "failed",
            "verify_auth": "not_needed",
            "tab_bootstrap": "profile_unavailable",
            "reauth": "not_needed",
            "import": "skipped",
            "path": "not_observed",
        }

    scrape_base = [
        "scrape_mortgage.py", "--lender", "boa", "--headless", "--merge",
        *SCRAPER_WRAPPER_CONTRACT_ARGS, "--run-id", run_id,
    ]
    if mode != "direct_only":
        scrape_base.extend(("--boa-pinchtab-instance", instance_id))
    scrape_args = apply_provider_mode(tuple(scrape_base), "boa", modes)
    verify_args = (
        "scrape_mortgage.py",
        "--lender",
        "boa",
        "--verify-auth",
        "--boa-pinchtab-instance",
        instance_id,
    )
    reauth_args = (
        "scrape_mortgage.py",
        "--lender",
        "boa",
        "--boa-re-auth",
        "--boa-pinchtab-instance",
        instance_id,
    )
    scrape = run_command(scrape_args, runtime_env)
    verify_status = "not_needed"
    reauth_status = "not_needed"
    tab_bootstrap_status = "not_needed"
    tab_seeded = False

    if (
        scrape.returncode != 0
        and not scrape.output_rejected
        and mode != "direct_only"
    ):
        verified = run_command(verify_args, runtime_env)
        verify_status = (
            "verify_failed"
            if verified.output_rejected
            else parse_boa_verify_status(verified.output)
        )
        if verify_status in {"boa_tab_unavailable", "signed_out_landing"}:
            tab_bootstrap_status = ensure_boa_tab(instance_id, runtime_env)
            if tab_bootstrap_status in {"opened", "reused"}:
                tab_seeded = True
                verified = run_command(verify_args, runtime_env)
                verify_status = (
                    "verify_failed"
                    if verified.output_rejected
                    else parse_boa_verify_status(verified.output)
                )
        if verify_status == "not_authenticated":
            credential_env = credentials_for(
                "boa",
                runtime_env,
                credential_store or {},
            )
            if credential_env is None:
                reauth_status = "credentials_unavailable"
            else:
                try:
                    reauth = run_command(reauth_args, credential_env)
                finally:
                    credential_env.pop("SCRAPER_USER", None)
                    credential_env.pop("SCRAPER_PW", None)
                reauth_status = (
                    "timeout" if reauth.timed_out
                    else (
                        "reauth_failed" if reauth.output_rejected
                        else parse_boa_reauth_status(reauth.output)
                    )
                )
                if (
                    reauth.returncode == 0
                    and not reauth.output_rejected
                    and reauth_status in BOA_REAUTH_SUCCESS_STATUSES
                ):
                    scrape = run_command(scrape_args, runtime_env)
        elif tab_seeded and verify_status == "authenticated":
            scrape = run_command(scrape_args, runtime_env)

    path = (
        parse_scraper_status(scrape.output, "boa")
        if scrape.returncode == 0 and not scrape.output_rejected
        else None
    )
    mode_mismatch = path is not None and not path_matches_provider_mode(
        "boa", path, mode
    )
    if mode_mismatch:
        path = None
    result = {
        "source": "boa",
        "scrape": (
            command_status(scrape)
            if scrape.returncode != 0 or path is not None
            else "failed"
        ),
        "verify_auth": verify_status,
        "tab_bootstrap": tab_bootstrap_status,
        "reauth": reauth_status,
        "import": "skipped",
        "path": path or (
            "mode_mismatch" if mode_mismatch else (
                "contract_invalid" if scrape.returncode == 0 else "not_observed"
            )
        ),
    }
    if (
        scrape.returncode == 0
        and not scrape.output_rejected
        and path is not None
    ):
        imported = run_command(
            (
                "update_data.py", "import-json-boa-mortgage",
                "--require-run-id", run_id,
            ),
            runtime_env,
        )
        result["import"] = command_status(imported)
    return result


def result_ok(result):
    return (
        result.get("scrape") == "ok"
        and result.get("import") == "ok"
        and result.get("profile_preflight", "ok") in {"ok", "not_needed"}
        and result.get("path") in SOURCE_PATH_HEALTH.get(result.get("source"), {})
    )


def dry_run_plan():
    plan = [source.name for source in SOURCES] + ["boa"]
    print(json.dumps({"status": "dry_run", "sources": plan}, sort_keys=True))


def credential_preflight(parent_env):
    env = scrub_child_environment(parent_env)
    try:
        credential_store = load_credential_store()
    except CredentialCacheError as error:
        result = {
            "status": "preflight_failed",
            "reason": "credential_cache_unavailable",
        }
        if error.missing_profiles:
            result["missing_profiles"] = list(error.missing_profiles)
        return env, {}, result
    return env, credential_store, {
        "status": "preflight_ok",
        "credential_profiles": sorted(credential_store),
    }


def scraper_contract_preflight(env):
    """Verify exact repository version and fleet capabilities before side effects."""
    runtime_env = scrub_child_environment(env)
    version_result = run_command(
        SCRAPER_CONTRACT_COMMAND,
        runtime_env,
        timeout=CONTRACT_PREFLIGHT_TIMEOUT_SECONDS,
    )
    version_ok = (
        not version_result.timed_out
        and version_result.returncode == 0
        and version_result.stdout
        in {SCRAPER_CONTRACT_LINE, SCRAPER_CONTRACT_LINE + "\n"}
        and version_result.stderr == ""
    )
    if not version_ok:
        return {
            "status": "preflight_failed",
            "reason": "scraper_contract_mismatch",
        }

    manifest_result = run_command(
        SCRAPER_MANIFEST_COMMAND,
        runtime_env,
        timeout=CONTRACT_PREFLIGHT_TIMEOUT_SECONDS,
    )
    manifest_ok = (
        not manifest_result.timed_out
        and manifest_result.returncode == 0
        and manifest_result.stdout
        in {SCRAPER_MANIFEST_LINE, SCRAPER_MANIFEST_LINE + "\n"}
        and manifest_result.stderr == ""
    )
    if not manifest_ok:
        return {
            "status": "preflight_failed",
            "reason": "scraper_contract_mismatch",
        }
    return {
        "status": "contract_ok",
        "contract": SCRAPER_CONTRACT_VERSION,
    }


def write_final_status(payload, path=None):
    """Atomically persist one owner-only, operational-metadata-only result."""
    status_path = Path(path or FINAL_STATUS_PATH)
    parent = status_path.parent
    try:
        parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        parent_metadata = parent.lstat()
    except OSError as error:
        raise FinalStatusError from error
    if (
        stat.S_ISLNK(parent_metadata.st_mode)
        or not stat.S_ISDIR(parent_metadata.st_mode)
        or parent_metadata.st_uid != os.getuid()
        or stat.S_IMODE(parent_metadata.st_mode) != 0o700
    ):
        raise FinalStatusError

    try:
        metadata = status_path.lstat()
    except FileNotFoundError:
        metadata = None
    except OSError as error:
        raise FinalStatusError from error
    if metadata is not None and (
        stat.S_ISLNK(metadata.st_mode)
        or not stat.S_ISREG(metadata.st_mode)
        or metadata.st_uid != os.getuid()
        or stat.S_IMODE(metadata.st_mode) != 0o600
    ):
        raise FinalStatusError

    temp_path = None
    try:
        descriptor, temp_path = tempfile.mkstemp(
            prefix=f".{status_path.name}.",
            suffix=".tmp",
            dir=parent,
        )
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            os.fchmod(handle.fileno(), 0o600)
            json.dump(payload, handle, sort_keys=True, separators=(",", ":"))
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_path, status_path)
        temp_path = None
        directory_fd = os.open(parent, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    except OSError as error:
        if temp_path is not None:
            try:
                os.unlink(temp_path)
            except OSError:
                pass
        raise FinalStatusError from error


def _canonical_timestamp(value):
    if not isinstance(value, str) or len(value) != 25 or not value.endswith("+00:00"):
        return False
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return False
    return (
        parsed.tzinfo is not None
        and parsed.utcoffset() is not None
        and parsed.utcoffset().total_seconds() == 0
        and parsed.isoformat(timespec="seconds") == value
    )


def _canonical_run_id(value):
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


def _safe_alert_affected(results):
    """Retain only fixed operational states for failed/degraded sources."""
    affected = []
    for result in results or ():
        if not isinstance(result, dict):
            continue
        source = result.get("source")
        if source not in SOURCE_PATH_HEALTH:
            continue
        if result_ok(result) and not path_is_degraded(source, result.get("path")):
            continue
        states = {}
        for field in ALERT_STATES:
            value = result.get(field)
            if isinstance(value, str) and value in ALERT_STATE_VALUES[field]:
                states[field] = value
        affected.append({"source": source, "states": states})
    return affected[:len(SOURCE_PATH_HEALTH)]


def build_alert_payload(
    status,
    run_id,
    created_at,
    *,
    results=None,
    reason=None,
    missing_profiles=None,
    signal_name=None,
    run_status=None,
):
    """Build one bounded, data-free delivery record for a nonhealthy run."""
    safe_reason = reason if reason in ALERT_REASONS else None
    safe_profiles = sorted({
        profile
        for profile in (missing_profiles or ())
        if profile in FINANCE_CREDENTIAL_KEYS
    })
    safe_signal = signal_name if signal_name in ALERT_SIGNALS else None
    payload = {
        "contract": ALERT_CONTRACT_VERSION,
        "run_id": run_id,
        "status": status,
        "run_status": run_status,
        "created_at": created_at,
        "reason": safe_reason,
        "missing_profiles": safe_profiles,
        "signal": safe_signal,
        "affected": _safe_alert_affected(results),
        "attempts": 0,
        "next_attempt_at": created_at,
        "last_attempt_at": None,
        "last_error": None,
        "delivery_state": "pending",
        "sent_at": None,
    }
    if not validate_alert_payload(payload, expected_run_id=run_id):
        raise AlertOutboxError
    return payload


def validate_alert_payload(payload, expected_run_id=None):
    """Validate the exact durable alert schema, including retry metadata."""
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
        or payload["contract"] != ALERT_CONTRACT_VERSION
        or not _canonical_run_id(run_id)
        or (expected_run_id is not None and run_id != expected_run_id)
        or payload.get("status") not in ALERT_STATUSES
        or payload.get("run_status") not in (ALERT_RUN_STATUSES | {None})
        or not _canonical_timestamp(payload.get("created_at"))
        or payload.get("reason") not in (ALERT_REASONS | {None})
        or payload.get("signal") not in (ALERT_SIGNALS | {None})
        or delivery_state not in ALERT_DELIVERY_STATES
    ):
        return False
    if delivery_state == "sent":
        if not _canonical_timestamp(sent_at):
            return False
    elif sent_at is not None:
        return False
    if payload["status"] == "status_write_failed":
        if payload["run_status"] is None:
            return False
    elif payload["run_status"] is not None:
        return False
    profiles = payload.get("missing_profiles")
    if (
        not isinstance(profiles, list)
        or len(profiles) > len(FINANCE_CREDENTIAL_KEYS)
        or profiles != sorted(set(profiles))
        or any(profile not in FINANCE_CREDENTIAL_KEYS for profile in profiles)
    ):
        return False
    affected = payload.get("affected")
    if not isinstance(affected, list) or len(affected) > len(SOURCE_PATH_HEALTH):
        return False
    seen_sources = set()
    for entry in affected:
        if not isinstance(entry, dict) or set(entry) != {"source", "states"}:
            return False
        source = entry.get("source")
        states = entry.get("states")
        if (
            source not in SOURCE_PATH_HEALTH
            or source in seen_sources
            or not isinstance(states, dict)
            or not set(states).issubset(ALERT_STATES)
            or any(
                not isinstance(value, str)
                or value not in ALERT_STATE_VALUES[field]
                for field, value in states.items()
            )
        ):
            return False
        seen_sources.add(source)
    attempts = payload.get("attempts")
    if type(attempts) is not int or not 0 <= attempts <= 100_000:
        return False
    if not _canonical_timestamp(payload.get("next_attempt_at")):
        return False
    last_attempt = payload.get("last_attempt_at")
    if last_attempt is not None and not _canonical_timestamp(last_attempt):
        return False
    if delivery_state == "inflight" and last_attempt is None:
        return False
    last_error = payload.get("last_error")
    if last_error is not None and last_error not in ALERT_DELIVERY_ERRORS:
        return False
    return True


def _read_existing_alert(path, expected_run_id):
    try:
        metadata = path.lstat()
    except OSError as error:
        raise AlertOutboxError from error
    if (
        stat.S_ISLNK(metadata.st_mode)
        or not stat.S_ISREG(metadata.st_mode)
        or metadata.st_uid != os.getuid()
        or stat.S_IMODE(metadata.st_mode) != 0o600
        or metadata.st_nlink != 1
        or not 0 < metadata.st_size <= ALERT_MAX_BYTES
    ):
        raise AlertOutboxError
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
        encoded = os.read(descriptor, ALERT_MAX_BYTES + 1)
        after = os.fstat(descriptor)
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_uid != os.getuid()
            or stat.S_IMODE(before.st_mode) != 0o600
            or before.st_nlink != 1
            or (before.st_dev, before.st_ino) != (metadata.st_dev, metadata.st_ino)
            or (before.st_dev, before.st_ino) != (after.st_dev, after.st_ino)
            or before.st_size != len(encoded)
            or len(encoded) > ALERT_MAX_BYTES
        ):
            raise AlertOutboxError
        payload = _strict_json_loads(encoded.decode("utf-8"))
    except (OSError, UnicodeError, ValueError, TypeError) as error:
        if isinstance(error, AlertOutboxError):
            raise
        raise AlertOutboxError from error
    finally:
        if descriptor is not None:
            os.close(descriptor)
    if not validate_alert_payload(payload, expected_run_id=expected_run_id):
        raise AlertOutboxError
    return payload


def enqueue_alert(payload, directory=None):
    """Atomically create at most one alert file for a whole-run UUID."""
    run_id = payload.get("run_id") if isinstance(payload, dict) else None
    if not validate_alert_payload(payload, expected_run_id=run_id):
        raise AlertOutboxError
    outbox = Path(directory or (Path(FINAL_STATUS_PATH).parent / ALERT_OUTBOX_NAME))
    try:
        parent_metadata = outbox.parent.lstat()
        outbox.mkdir(parents=True, exist_ok=True, mode=0o700)
        metadata = outbox.lstat()
    except OSError as error:
        raise AlertOutboxError from error
    if (
        stat.S_ISLNK(parent_metadata.st_mode)
        or not stat.S_ISDIR(parent_metadata.st_mode)
        or parent_metadata.st_uid != os.getuid()
        or stat.S_IMODE(parent_metadata.st_mode) != 0o700
        or stat.S_ISLNK(metadata.st_mode)
        or not stat.S_ISDIR(metadata.st_mode)
        or metadata.st_uid != os.getuid()
        or stat.S_IMODE(metadata.st_mode) != 0o700
    ):
        raise AlertOutboxError
    destination = outbox / f"{run_id}.json"
    if destination.exists() or destination.is_symlink():
        _read_existing_alert(destination, run_id)
        return False

    encoded = (
        json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n"
    ).encode("utf-8")
    if len(encoded) > ALERT_MAX_BYTES:
        raise AlertOutboxError
    temporary = None
    descriptor = None
    try:
        candidate = outbox / f"{ALERT_TEMP_PREFIX}{run_id}{ALERT_TEMP_SUFFIX}"
        descriptor = os.open(
            candidate,
            os.O_WRONLY
            | os.O_CREAT
            | os.O_EXCL
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NOFOLLOW", 0)
            | getattr(os, "O_NONBLOCK", 0),
            0o600,
        )
        temporary = candidate
        handle = os.fdopen(descriptor, "wb")
        descriptor = None
        with handle:
            os.fchmod(handle.fileno(), 0o600)
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
        try:
            os.link(temporary, destination, follow_symlinks=False)
        except FileExistsError:
            _read_existing_alert(destination, run_id)
            return False
        finally:
            os.unlink(temporary)
            temporary = None
        directory_fd = os.open(outbox, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    except OSError as error:
        if descriptor is not None:
            try:
                os.close(descriptor)
            except OSError:
                pass
        if temporary is not None:
            try:
                os.unlink(temporary)
            except OSError:
                pass
        raise AlertOutboxError from error
    return True


def finish_run(
    status,
    run_id,
    *,
    results=None,
    reason=None,
    missing_profiles=None,
    signal_name=None,
):
    """Persist final status and a recoverable nonhealthy alert handoff."""
    completed_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    alert_required = status != "ok"
    payload = {
        "contract": SCRAPER_CONTRACT_VERSION,
        "status": status,
        "run_id": run_id,
        "completed_at": completed_at,
        "alert_handoff": "pending" if alert_required else "not_required",
    }
    if results is not None:
        payload["results"] = results
    if reason is not None:
        payload["reason"] = reason
    if missing_profiles:
        payload["missing_profiles"] = list(missing_profiles)
    if signal_name is not None:
        payload["signal"] = signal_name
    status_written = True
    try:
        write_final_status(payload)
    except FinalStatusError:
        status_written = False

    alert_status = status if status != "ok" else None
    alert_run_status = None
    if not status_written:
        alert_status = "status_write_failed"
        alert_run_status = status
    alert_enqueued = True
    if alert_status is not None:
        try:
            alert = build_alert_payload(
                alert_status,
                run_id,
                completed_at,
                results=results,
                reason=reason,
                missing_profiles=missing_profiles,
                signal_name=signal_name,
                run_status=alert_run_status,
            )
            enqueue_alert(alert)
        except AlertOutboxError:
            alert_enqueued = False

    handoff_status_written = status_written
    if status_written and alert_status is not None:
        payload["alert_handoff"] = "persisted" if alert_enqueued else "failed"
        try:
            write_final_status(payload)
        except FinalStatusError:
            handoff_status_written = False

    if not alert_enqueued:
        print(json.dumps({
            "contract": SCRAPER_CONTRACT_VERSION,
            "status": "alert_enqueue_failed",
            "run_status": status,
            "run_id": run_id,
            "status_persisted": status_written,
            "alert_handoff_persisted": handoff_status_written,
        }, sort_keys=True))
        return False
    if not status_written:
        print(json.dumps({
            "contract": SCRAPER_CONTRACT_VERSION,
            "status": "status_write_failed",
            "run_status": status,
            "run_id": run_id,
            "alert_persisted": True,
        }, sort_keys=True))
        return False
    if not handoff_status_written:
        print(json.dumps({
            "contract": SCRAPER_CONTRACT_VERSION,
            "status": "status_write_failed",
            "run_status": status,
            "run_id": run_id,
            "alert_persisted": True,
        }, sort_keys=True))
        return False
    print(json.dumps(payload, sort_keys=True))
    return True


def _execute_run(run_id):
    if not REPO.is_dir() or not PYTHON.is_file():
        finish_run(
            "preflight_failed",
            run_id,
            reason="repository_unavailable",
        )
        return 1

    try:
        tesla_email = load_tesla_email()
    except TeslaConfigurationError:
        finish_run(
            "preflight_failed",
            run_id,
            reason="tesla_configuration_unavailable",
        )
        return 1
    env = scrub_child_environment(os.environ)
    tesla_env = tesla_source_environment(env, tesla_email)
    contract_preflight = scraper_contract_preflight(env)
    if contract_preflight["status"] != "contract_ok":
        finish_run(
            "preflight_failed",
            run_id,
            reason=contract_preflight["reason"],
        )
        return 1

    try:
        provider_modes = load_provider_modes()
    except ProviderModeError:
        finish_run(
            "preflight_failed",
            run_id,
            reason="provider_mode_config_invalid",
        )
        return 1

    env, credential_store, preflight = credential_preflight(env)
    if preflight["status"] != "preflight_ok":
        finish_run(
            "preflight_failed",
            run_id,
            reason=preflight["reason"],
            missing_profiles=preflight.get("missing_profiles"),
        )
        return 1
    if provider_modes["boa"] == "direct_only":
        boa_profile_preflight = BoaProfileResult("not_needed")
    else:
        boa_profile_preflight = ensure_boa_profile(env)
    results = []
    for source in SOURCES:
        source_env = tesla_env if source.name == "tesla_solar" else env
        result = run_standard_source(
            source,
            run_id,
            source_env,
            credential_store,
            provider_modes,
        )
        results.append(result)
        print(json.dumps({"event": "source_complete", **result}, sort_keys=True), flush=True)
    boa_result = run_boa(
        run_id,
        env,
        credential_store,
        boa_profile_preflight.instance_id,
        provider_modes,
    )
    boa_result["profile_preflight"] = boa_profile_preflight.status
    results.append(boa_result)
    print(json.dumps({"event": "source_complete", **boa_result}, sort_keys=True), flush=True)
    if not all(result_ok(result) for result in results):
        status = "failed"
    elif any(path_is_degraded(result["source"], result.get("path")) for result in results):
        status = "degraded"
    else:
        status = "ok"
    persisted = finish_run(status, run_id, results=results)
    return 0 if status == "ok" and persisted else 1


def _safe_signal_name(signum):
    try:
        return signal.Signals(signum).name.lower()
    except (ValueError, AttributeError):
        return "termination"


def _run_locked():
    """Execute and durably finalize one lock-protected weekly run."""
    run_id = str(uuid.uuid4())
    try:
        return _execute_run(run_id)
    except WrapperInterrupted as error:
        finish_run(
            "interrupted",
            run_id,
            signal_name=_safe_signal_name(error.signum),
        )
        return 128 + int(error.signum)
    except Exception:
        finish_run("internal_error", run_id)
        return 1


def report_lock_contention():
    """Report an unhealthy overlap without touching singleton-owned status."""
    run_id = str(uuid.uuid4())
    created_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    alert_handoff = "failed"
    try:
        alert = build_alert_payload(
            "lock_unavailable",
            run_id,
            created_at,
        )
        enqueue_alert(alert)
        alert_handoff = "persisted"
    except Exception:
        # Scheduler failureAlert remains the independent delivery path. Avoid
        # finish_run here: the process holding the lock owns final-status state.
        pass
    print(json.dumps({
        "alert_handoff": alert_handoff,
        "contract": SCRAPER_CONTRACT_VERSION,
        "reason": "already_running",
        "run_id": run_id,
        "status": "lock_unavailable",
    }, sort_keys=True))
    return 1


def main():
    if sys.argv[1:] == ["--dry-run"]:
        dry_run_plan()
        return 0
    if sys.argv[1:] == ["--preflight"]:
        if not REPO.is_dir() or not PYTHON.is_file():
            print(json.dumps({"status": "preflight_failed"}))
            return 1
        try:
            load_tesla_email()
        except TeslaConfigurationError:
            print(json.dumps({
                "status": "preflight_failed",
                "reason": "tesla_configuration_unavailable",
            }, sort_keys=True))
            return 1
        env = scrub_child_environment(os.environ)
        contract_preflight = scraper_contract_preflight(env)
        if contract_preflight["status"] != "contract_ok":
            print(json.dumps(contract_preflight, sort_keys=True))
            return 1
        try:
            load_provider_modes()
        except ProviderModeError:
            print(json.dumps({
                "status": "preflight_failed",
                "reason": "provider_mode_config_invalid",
            }, sort_keys=True))
            return 1
        _, _, preflight = credential_preflight(env)
        if preflight["status"] == "preflight_ok":
            preflight["contract"] = SCRAPER_CONTRACT_VERSION
        print(json.dumps(preflight, sort_keys=True))
        return 0 if preflight["status"] == "preflight_ok" else 1
    if sys.argv[1:]:
        print(json.dumps({"status": "invalid_arguments"}))
        return 2

    try:
        with termination_signal_handlers():
            with singleton_lock() as acquired:
                if not acquired:
                    return report_lock_contention()
                return _run_locked()
    except WrapperInterrupted as error:
        finish_run(
            "interrupted",
            str(uuid.uuid4()),
            signal_name=_safe_signal_name(error.signum),
        )
        return 128 + int(error.signum)
    except RunLockError:
        finish_run("lock_unavailable", str(uuid.uuid4()))
        return 1
    except Exception:
        finish_run("internal_error", str(uuid.uuid4()))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
