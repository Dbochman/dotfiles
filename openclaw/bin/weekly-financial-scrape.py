#!/usr/bin/env python3
"""Deterministic weekly financial scrape orchestration.

Child scraper output is captured in memory and never relayed to scheduled logs.
Only source names, phase states, and the safe mortgage run ID are emitted.
"""

from __future__ import annotations

from contextlib import contextmanager
import fcntl
import json
import os
import re
import signal
import subprocess
import sys
import uuid
from dataclasses import dataclass
from pathlib import Path


REPO = Path.home() / "repos" / "financial-dashboard"
PYTHON = REPO / "venv" / "bin" / "python3"
OP_TOKEN_FILE = Path.home() / ".openclaw" / ".env-token"
LOCK_PATH = Path.home() / ".openclaw" / "financial-dashboard" / ".weekly-scrape.lock"
COMMAND_TIMEOUT_SECONDS = 420
PROCESS_GROUP_GRACE_SECONDS = 5
TERMINATION_SIGNALS = (signal.SIGINT, signal.SIGTERM)


class WrapperInterrupted(Exception):
    """Internal signal used to unwind cleanly after SIGINT or SIGTERM."""

    def __init__(self, signum):
        super().__init__(signum)
        self.signum = signum


class RunLockError(Exception):
    """The protected singleton lock could not be opened or acquired."""


_ACTIVE_PROCESS = None
_SPAWNING_PROCESS = False
_DEFERRED_TERMINATION_SIGNAL = None


@dataclass(frozen=True)
class CommandResult:
    returncode: int
    stdout: str = ""
    stderr: str = ""
    timed_out: bool = False

    @property
    def output(self) -> str:
        return f"{self.stdout}\n{self.stderr}"


@dataclass(frozen=True)
class Source:
    name: str
    scrape_args: tuple[str, ...]
    import_args: tuple[str, ...]
    op_item: str | None = None
    reauth_args: tuple[str, ...] | None = None
    mortgage_source: str | None = None


SOURCES = (
    Source(
        "tesla_solar",
        ("scrape_tesla_solar.py", "--merge"),
        ("update_data.py", "import-json-solar-cabin"),
    ),
    Source(
        "eversource",
        ("scrape_eversource.py", "--headless", "--merge"),
        ("update_data.py", "import-json-utilities"),
        "www.eversource.com",
        ("scrape_eversource.py", "--re-auth", "--headless"),
    ),
    Source(
        "national_grid_electric",
        ("scrape_national_grid_electric.py", "--headless", "--merge"),
        ("update_data.py", "import-json-electric-cabin"),
        "login.nationalgridus.com",
        ("scrape_national_grid_electric.py", "--re-auth", "--headless"),
    ),
    Source(
        "national_grid_gas",
        ("scrape_national_grid.py", "--headless", "--merge"),
        ("update_data.py", "import-json-gas"),
        "login.nationalgridus.com",
        ("scrape_national_grid.py", "--re-auth", "--headless"),
    ),
    Source(
        "bwsc",
        ("scrape_bwsc.py", "--headless", "--merge"),
        ("update_data.py", "import-json-water"),
        "umaxcustomerportalprod.b2clogin.com",
        ("scrape_bwsc.py", "--re-auth", "--headless"),
    ),
    Source(
        "pennymac",
        ("scrape_mortgage.py", "--lender", "pennymac", "--headless", "--merge"),
        ("update_data.py", "import-json-pennymac-mortgage"),
        "PennyMac",
        ("scrape_mortgage.py", "--lender", "pennymac", "--re-auth", "--headless"),
        "pennymac",
    ),
)


AUTH_FAILURE_MARKERS = (
    "session expired",
    "not logged in",
    "login timeout",
    "authentication failed",
    "authentication required",
    "please log in",
    "requires interactive login",
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
})


def _signal_process_group(process, signum):
    """Signal one child session without ever inspecting its captured output."""
    if process is None:
        return
    try:
        os.killpg(process.pid, signum)
    except (ProcessLookupError, PermissionError):
        pass


def _stop_process_group(process):
    """Terminate and reap a child session, escalating when it does not exit."""
    if process is None:
        return
    _signal_process_group(process, signal.SIGTERM)
    try:
        process.communicate(timeout=PROCESS_GROUP_GRACE_SECONDS)
    except subprocess.TimeoutExpired:
        _signal_process_group(process, signal.SIGKILL)
        try:
            process.communicate()
        except (OSError, ValueError):
            pass
    except (OSError, ValueError):
        pass


def _run_captured(command, env, timeout, cwd=None):
    """Run a command privately in a new session and contain its full lifecycle."""
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
                    text=True,
                    start_new_session=True,
                )
                _ACTIVE_PROCESS = process
            finally:
                _SPAWNING_PROCESS = False
                _raise_deferred_termination()
        except (OSError, ValueError):
            return CommandResult(127)

        stdout, stderr = process.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        _stop_process_group(process)
        return CommandResult(124, timed_out=True)
    except BaseException:
        _stop_process_group(process)
        raise
    finally:
        if _ACTIVE_PROCESS is process:
            _ACTIVE_PROCESS = None
    return CommandResult(process.returncode, stdout or "", stderr or "")


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
    try:
        LOCK_PATH.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(LOCK_PATH.parent, 0o700)
        lock_file = LOCK_PATH.open("a+", encoding="utf-8")
        os.fchmod(lock_file.fileno(), 0o600)
    except OSError as error:
        if lock_file is not None:
            lock_file.close()
        raise RunLockError from error

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
        env,
        timeout,
        cwd=REPO,
    )


def run_op_read(item, field, env):
    completed = _run_captured(
        ["op", "read", f"op://OpenClaw/{item}/{field}"],
        env,
        30,
    )
    value = completed.stdout.strip()
    return value if completed.returncode == 0 and value else None


def credentials_for(item, env, op_env=None):
    credential_env = op_env or env
    username = run_op_read(item, "username", credential_env)
    password = run_op_read(item, "password", credential_env)
    if not username or not password:
        return None
    child_env = env.copy()
    child_env["SCRAPER_USER"] = username
    child_env["SCRAPER_PW"] = password
    return child_env


def command_status(result):
    if result.timed_out:
        return "timeout"
    return "ok" if result.returncode == 0 else "failed"


def is_auth_failure(result):
    output = result.output.lower()
    return any(marker in output for marker in AUTH_FAILURE_MARKERS)


def with_run_id(arguments, run_id, mortgage_source):
    if not mortgage_source:
        return arguments
    return (*arguments, "--run-id", run_id)


def guarded_import_args(source, run_id):
    if not source.mortgage_source:
        return source.import_args
    return (*source.import_args, "--require-run-id", run_id)


def run_standard_source(source, run_id, env, op_env=None):
    scrape_args = with_run_id(source.scrape_args, run_id, source.mortgage_source)
    scrape = run_command(scrape_args, env)
    reauth_status = "not_needed"

    if scrape.returncode != 0 and source.reauth_args and is_auth_failure(scrape):
        credential_env = credentials_for(source.op_item, env, op_env)
        if credential_env is None:
            reauth_status = "credentials_unavailable"
        else:
            reauth = run_command(source.reauth_args, credential_env)
            reauth_status = command_status(reauth)
            credential_env.pop("SCRAPER_USER", None)
            credential_env.pop("SCRAPER_PW", None)
            if reauth.returncode == 0:
                scrape = run_command(scrape_args, env)

    result = {
        "source": source.name,
        "scrape": command_status(scrape),
        "reauth": reauth_status,
        "import": "skipped",
    }
    if scrape.returncode == 0:
        imported = run_command(guarded_import_args(source, run_id), env)
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


def run_boa(run_id, env, op_env=None):
    scrape_args = (
        "scrape_mortgage.py", "--lender", "boa", "--headless", "--merge",
        "--run-id", run_id,
    )
    scrape = run_command(scrape_args, env)
    verify_status = "not_needed"
    reauth_status = "not_needed"

    if scrape.returncode != 0:
        verified = run_command(
            ("scrape_mortgage.py", "--lender", "boa", "--verify-auth"), env
        )
        verify_status = parse_boa_verify_status(verified.output)
        if verify_status == "not_authenticated":
            credential_env = credentials_for("Bank of America", env, op_env)
            if credential_env is None:
                reauth_status = "credentials_unavailable"
            else:
                reauth = run_command(
                    ("scrape_mortgage.py", "--lender", "boa", "--boa-re-auth"),
                    credential_env,
                )
                reauth_status = (
                    "timeout" if reauth.timed_out
                    else parse_boa_reauth_status(reauth.output)
                )
                credential_env.pop("SCRAPER_USER", None)
                credential_env.pop("SCRAPER_PW", None)
                if (
                    reauth.returncode == 0
                    and reauth_status in BOA_REAUTH_SUCCESS_STATUSES
                ):
                    scrape = run_command(scrape_args, env)

    result = {
        "source": "boa",
        "scrape": command_status(scrape),
        "verify_auth": verify_status,
        "reauth": reauth_status,
        "import": "skipped",
    }
    if scrape.returncode == 0:
        imported = run_command(
            (
                "update_data.py", "import-json-boa-mortgage",
                "--require-run-id", run_id,
            ),
            env,
        )
        result["import"] = command_status(imported)
    return result


def result_ok(result):
    return result.get("scrape") == "ok" and result.get("import") == "ok"


def dry_run_plan():
    plan = [source.name for source in SOURCES] + ["boa"]
    print(json.dumps({"status": "dry_run", "sources": plan}, sort_keys=True))


def _run_locked():
    if not REPO.is_dir() or not PYTHON.is_file() or not OP_TOKEN_FILE.is_file():
        print(json.dumps({"status": "preflight_failed"}))
        return 1

    try:
        token = OP_TOKEN_FILE.read_text().strip()
    except OSError:
        print(json.dumps({"status": "preflight_failed"}))
        return 1
    if not token:
        print(json.dumps({"status": "preflight_failed"}))
        return 1
    env = os.environ.copy()
    env.pop("OP_SERVICE_ACCOUNT_TOKEN", None)
    env.pop("SCRAPER_USER", None)
    env.pop("SCRAPER_PW", None)
    op_env = env.copy()
    op_env["OP_SERVICE_ACCOUNT_TOKEN"] = token
    run_id = str(uuid.uuid4())
    results = []
    for source in SOURCES:
        result = run_standard_source(source, run_id, env, op_env)
        results.append(result)
        print(json.dumps({"event": "source_complete", **result}, sort_keys=True), flush=True)
    boa_result = run_boa(run_id, env, op_env)
    results.append(boa_result)
    print(json.dumps({"event": "source_complete", **boa_result}, sort_keys=True), flush=True)
    op_env.pop("OP_SERVICE_ACCOUNT_TOKEN", None)

    status = "ok" if all(result_ok(result) for result in results) else "failed"
    print(json.dumps({"status": status, "run_id": run_id, "results": results}, sort_keys=True))
    return 0 if status == "ok" else 1


def main():
    if sys.argv[1:] == ["--dry-run"]:
        dry_run_plan()
        return 0
    if sys.argv[1:]:
        print(json.dumps({"status": "invalid_arguments"}))
        return 2

    try:
        with termination_signal_handlers():
            with singleton_lock() as acquired:
                if not acquired:
                    print(json.dumps({"status": "already_running"}))
                    return 0
                return _run_locked()
    except WrapperInterrupted as error:
        try:
            signal_name = signal.Signals(error.signum).name.lower()
        except (ValueError, AttributeError):
            signal_name = "termination"
        print(json.dumps({"status": "interrupted", "signal": signal_name}))
        return 128 + int(error.signum)
    except RunLockError:
        print(json.dumps({"status": "lock_unavailable"}))
        return 1
    except Exception:
        print(json.dumps({"status": "internal_error"}))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
