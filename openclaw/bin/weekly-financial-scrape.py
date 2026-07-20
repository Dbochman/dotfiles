#!/usr/bin/env python3
"""Deterministic weekly financial scrape orchestration.

Child scraper output is captured in memory and never relayed to scheduled logs.
Only source names, phase states, BWSC's fixed path mode, and the safe mortgage
run ID are emitted.
"""

from __future__ import annotations

from contextlib import contextmanager
import fcntl
import json
import os
import re
import signal
import stat
import subprocess
import sys
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
PINCHTAB_INSTANCE_HELPER = Path.home() / ".openclaw" / "bin" / "pinchtab-headless-instance"
COMMAND_TIMEOUT_SECONDS = 420
PROFILE_PREFLIGHT_TIMEOUT_SECONDS = 45
BOA_TAB_OPERATION_TIMEOUT_SECONDS = 30
PROCESS_GROUP_GRACE_SECONDS = 5
TERMINATION_SIGNALS = (signal.SIGINT, signal.SIGTERM)
BOA_TAB_BOOTSTRAP_URL = (
    "https://secure.bankofamerica.com/login/sign-in/"
    "signOnV2Screen.go?request_locale=en-us"
)
BOA_TAB_REUSE_HOSTS = frozenset({"secure.bankofamerica.com"})


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
        ("scrape_tesla_solar.py", "--merge"),
        ("update_data.py", "import-json-solar-cabin"),
    ),
    Source(
        "eversource",
        ("scrape_eversource.py", "--headless", "--merge"),
        ("update_data.py", "import-json-utilities"),
        "eversource",
        ("scrape_eversource.py", "--re-auth", "--headless"),
    ),
    Source(
        "national_grid_electric",
        ("scrape_national_grid_electric.py", "--headless", "--merge"),
        ("update_data.py", "import-json-electric-cabin"),
        "national_grid",
        ("scrape_national_grid_electric.py", "--re-auth", "--headless"),
    ),
    Source(
        "national_grid_gas",
        ("scrape_national_grid.py", "--headless", "--merge"),
        ("update_data.py", "import-json-gas"),
        "national_grid",
        ("scrape_national_grid.py", "--re-auth", "--headless"),
    ),
    Source(
        "bwsc",
        ("scrape_bwsc.py", "--headless", "--merge"),
        ("update_data.py", "import-json-water"),
        "bwsc",
        ("scrape_bwsc.py", "--re-auth", "--headless"),
    ),
    Source(
        "pennymac",
        ("scrape_mortgage.py", "--lender", "pennymac", "--headless", "--merge"),
        ("update_data.py", "import-json-pennymac-mortgage"),
        "pennymac",
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
BWSC_AUTH_MARKER = "ERROR: BWSC authentication required"
BWSC_PATH_MODES = frozenset({
    "direct_http",
    "browser_recovery",
    "browser_only",
    "browser_explicit",
})

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


def scrub_child_environment(parent_env):
    """Remove credential material from every ordinary child environment."""
    child_env = parent_env.copy()
    child_env.pop("OP_SERVICE_ACCOUNT_TOKEN", None)
    child_env.pop("SCRAPER_USER", None)
    child_env.pop("SCRAPER_PW", None)
    for key in FINANCE_CREDENTIAL_ENV_KEYS:
        child_env.pop(key, None)
    return child_env


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
    ):
        raise CredentialCacheError()
    try:
        payload = json.loads(cache_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise CredentialCacheError() from error
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
        if not isinstance(username, str) or not username or not isinstance(password, str) or not password:
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
    child_env = env.copy()
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
    return "ok" if result.returncode == 0 else "failed"


def is_auth_failure(result, source_name=None):
    if source_name == "bwsc":
        return any(
            line.strip() == BWSC_AUTH_MARKER
            for line in result.output.splitlines()
        )
    output = result.output.lower()
    return any(marker in output for marker in AUTH_FAILURE_MARKERS)


def parse_bwsc_path(output):
    """Retain one exact, data-free scraper path marker and nothing else."""
    modes = []
    pattern = re.compile(r"^BWSC_PATH:\s*([a-z_]+)$")
    for line in output.splitlines():
        match = pattern.fullmatch(line.strip())
        if match:
            modes.append(match.group(1))
    if len(modes) == 1 and modes[0] in BWSC_PATH_MODES:
        return modes[0]
    return "unknown"


def with_run_id(arguments, run_id, mortgage_source):
    if not mortgage_source:
        return arguments
    return (*arguments, "--run-id", run_id)


def guarded_import_args(source, run_id):
    if not source.mortgage_source:
        return source.import_args
    return (*source.import_args, "--require-run-id", run_id)


def run_standard_source(source, run_id, env, credential_store=None):
    scrape_args = with_run_id(source.scrape_args, run_id, source.mortgage_source)
    scrape = run_command(scrape_args, env)
    reauth_status = "not_needed"

    if (
        scrape.returncode != 0
        and source.reauth_args
        and is_auth_failure(scrape, source.name)
    ):
        credential_env = credentials_for(
            source.credential_profile,
            env,
            credential_store or {},
        )
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
    if source.name == "bwsc":
        result["path"] = (
            parse_bwsc_path(scrape.output)
            if scrape.returncode == 0
            else "not_observed"
        )
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


def run_boa(
    run_id,
    env,
    credential_store=None,
    instance_id=None,
):
    if not _boa_instance_id_allowed(instance_id):
        return {
            "source": "boa",
            "scrape": "failed",
            "verify_auth": "not_needed",
            "tab_bootstrap": "profile_unavailable",
            "reauth": "not_needed",
            "import": "skipped",
        }

    scrape_args = (
        "scrape_mortgage.py", "--lender", "boa", "--headless", "--merge",
        "--run-id", run_id,
        "--boa-pinchtab-instance", instance_id,
    )
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
    scrape = run_command(scrape_args, env)
    verify_status = "not_needed"
    reauth_status = "not_needed"
    tab_bootstrap_status = "not_needed"
    tab_seeded = False

    if scrape.returncode != 0:
        verified = run_command(verify_args, env)
        verify_status = parse_boa_verify_status(verified.output)
        if verify_status in {"boa_tab_unavailable", "signed_out_landing"}:
            tab_bootstrap_status = ensure_boa_tab(instance_id, env)
            if tab_bootstrap_status in {"opened", "reused"}:
                tab_seeded = True
                verified = run_command(verify_args, env)
                verify_status = parse_boa_verify_status(verified.output)
        if verify_status == "not_authenticated":
            credential_env = credentials_for("boa", env, credential_store or {})
            if credential_env is None:
                reauth_status = "credentials_unavailable"
            else:
                reauth = run_command(reauth_args, credential_env)
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
        elif tab_seeded and verify_status == "authenticated":
            scrape = run_command(scrape_args, env)

    result = {
        "source": "boa",
        "scrape": command_status(scrape),
        "verify_auth": verify_status,
        "tab_bootstrap": tab_bootstrap_status,
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
    return (
        result.get("scrape") == "ok"
        and result.get("import") == "ok"
        and result.get("profile_preflight", "ok") == "ok"
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


def _run_locked():
    if not REPO.is_dir() or not PYTHON.is_file():
        print(json.dumps({"status": "preflight_failed"}))
        return 1

    env, credential_store, preflight = credential_preflight(os.environ)
    if preflight["status"] != "preflight_ok":
        print(json.dumps(preflight, sort_keys=True))
        return 1
    boa_profile_preflight = ensure_boa_profile(env)
    run_id = str(uuid.uuid4())
    results = []
    for source in SOURCES:
        result = run_standard_source(source, run_id, env, credential_store)
        results.append(result)
        print(json.dumps({"event": "source_complete", **result}, sort_keys=True), flush=True)
    boa_result = run_boa(
        run_id,
        env,
        credential_store,
        boa_profile_preflight.instance_id,
    )
    boa_result["profile_preflight"] = boa_profile_preflight.status
    results.append(boa_result)
    print(json.dumps({"event": "source_complete", **boa_result}, sort_keys=True), flush=True)
    status = "ok" if all(result_ok(result) for result in results) else "failed"
    print(json.dumps({"status": status, "run_id": run_id, "results": results}, sort_keys=True))
    return 0 if status == "ok" else 1


def main():
    if sys.argv[1:] == ["--dry-run"]:
        dry_run_plan()
        return 0
    if sys.argv[1:] == ["--preflight"]:
        if not REPO.is_dir() or not PYTHON.is_file():
            print(json.dumps({"status": "preflight_failed"}))
            return 1
        _, _, preflight = credential_preflight(os.environ)
        print(json.dumps(preflight, sort_keys=True))
        return 0 if preflight["status"] == "preflight_ok" else 1
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
